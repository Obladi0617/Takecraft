import asyncio
import shutil
from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..config import settings
from ..db import abs_path, get_engine, project_path
from ..domain import Character, GenerationJob, Location, Shot, Storyboard, Take
from ..generators import get_image_generator, get_video_generator
from ..generators.base import ImageGenerationRequest, VideoGenerationRequest
from ..media.ffmpeg import make_proxy, probe_duration
from ..repositories import next_seq_and_id

PRIORITY_RANK = {"CRITICAL": 0, "HIGH": 1, "NORMAL": 2, "LOW": 3}
POLL_INTERVAL = 2.0
ADAPTER_POLL_INTERVAL = 0.5
MAX_RETRY = 1
# max(index)+1 在并发下会撞主键，撞了就重算
ID_ATTEMPTS = 3


class GenerationQueue:
    """规格书第 23 节：所有生成任务进入统一队列（进程内异步队列 + DB 持久化）。"""

    def __init__(self) -> None:
        self._wakeup = asyncio.Event()
        self._running = False
        self._video_sem: asyncio.Semaphore | None = None
        self._poll_task: asyncio.Task | None = None
        self._inflight: set[str] = set()

    async def start(self) -> None:
        self._running = True
        self._video_sem = asyncio.Semaphore(settings.video_generation_concurrency)
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        self._running = False
        self._wakeup.set()
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

    def notify(self) -> None:
        self._wakeup.set()

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                self._dispatch_pending()
            except Exception:  # noqa: BLE001
                pass
            try:
                await asyncio.wait_for(self._wakeup.wait(), timeout=POLL_INTERVAL)
            except asyncio.TimeoutError:
                pass
            self._wakeup.clear()

    def _dispatch_pending(self) -> None:
        for project_id in self._active_projects():
            with Session(get_engine(project_id)) as session:
                jobs = list(
                    session.exec(
                        select(GenerationJob).where(
                            GenerationJob.project_id == project_id,
                            GenerationJob.status == "PENDING",
                        )
                    )
                )
            jobs = [j for j in jobs if j.id not in self._inflight]
            jobs.sort(
                key=lambda j: (PRIORITY_RANK.get(j.priority, 2), j.created_at)
            )
            for job in jobs:
                self._inflight.add(job.id)
                asyncio.create_task(
                    self._run_job(project_id, job.id, job.job_type)
                )

    def _active_projects(self) -> list[str]:
        from ..db import projects_root

        if not projects_root().exists():
            return []
        return [
            p.name
            for p in projects_root().iterdir()
            if (p / "project.db").exists() and p.is_dir()
        ]

    async def _run_job(
        self, project_id: str, job_id: str, job_type: str
    ) -> None:
        try:
            if job_type == "VIDEO" and self._video_sem is not None:
                async with self._video_sem:
                    await self._execute(project_id, job_id)
            else:
                await self._execute(project_id, job_id)
        finally:
            self._inflight.discard(job_id)

    async def _execute(self, project_id: str, job_id: str) -> None:
        with Session(get_engine(project_id)) as session:
            job = session.get(GenerationJob, job_id)
            if job is None or job.status != "PENDING":
                return
            job.status = "RUNNING"
            session.add(job)
            session.commit()
            try:
                if job.job_type == "STORYBOARD":
                    job.result = await self._do_storyboard(session, job)
                elif job.job_type == "VIDEO":
                    job.result = {
                        **job.result,
                        **await self._do_video(session, job),
                    }
                elif job.job_type in ("CHARACTER", "LOCATION"):
                    job.result = await self._do_asset_images(session, job)
                else:
                    raise RuntimeError(f"不支持的 job_type: {job.job_type}")
                job.status = "DONE"
            except asyncio.CancelledError:
                session.rollback()
                job = session.get(GenerationJob, job_id)
                if job is None:
                    raise
                job.status = "CANCELLED"
                session.add(job)
                session.commit()
                raise
            except Exception as e:  # noqa: BLE001
                # 必须先回滚：失败的 flush 会让同一 Session 的后续写入全部报 PendingRollbackError
                session.rollback()
                job = session.get(GenerationJob, job_id)
                if job is None:
                    return
                if job.retry_count < MAX_RETRY:
                    job.retry_count += 1
                    job.status = "PENDING"
                else:
                    job.status = "FAILED"
                    job.error = str(e)[:500]
            session.add(job)
            session.commit()

    async def _do_storyboard(self, session: Session, job: GenerationJob) -> dict:
        payload = job.payload
        shot = session.get(Shot, payload["shot_id"])
        if shot is None:
            raise RuntimeError(f"Shot 不存在: {payload['shot_id']}")
        prompt = payload.get("prompt") or shot.description or shot.title
        request = ImageGenerationRequest(
            prompt=prompt,
            count=max(int(payload.get("count", 4)), 1),
            width=int(payload.get("width", 1024)),
            height=int(payload.get("height", 576)),
            references=[r for r in payload.get("references") or []],
        )
        images = await get_image_generator(job.project_id).generate(request)
        root = project_path(job.project_id)
        ids: list[str] = []
        for _ in range(ID_ATTEMPTS):
            ids = []
            for image in images:
                index, sb_id = next_seq_and_id(session, Storyboard, job.project_id, "sb")
                rel = str(Path(image.path).resolve().relative_to(root))
                session.add(
                    Storyboard(
                        id=sb_id,
                        project_id=job.project_id,
                        shot_id=shot.id,
                        index=index,
                        prompt=prompt,
                        width=image.width,
                        height=image.height,
                        image_path=rel,
                        model=image.model,
                        seed=image.seed,
                    )
                )
                ids.append(sb_id)
            try:
                session.commit()
                break
            except IntegrityError:
                session.rollback()
        else:
            raise RuntimeError("分镜 id 分配连续冲突")
        return {"storyboard_ids": ids}

    async def _do_asset_images(self, session: Session, job: GenerationJob) -> dict:
        """角色三视图 / 场景参考图（规格书第 13.2、14 节）。"""
        from ..services.assets import (
            CHARACTER_ASSET_TYPE,
            CHARACTER_SUBDIR,
            LOCATION_ASSET_TYPE,
            LOCATION_SUBDIR,
            store_generated_asset,
            view_prompt,
        )

        payload = job.payload
        owner_id = payload["owner_id"]
        views = list(payload.get("views") or [])
        is_character = job.job_type == "CHARACTER"
        owner = session.get(Character if is_character else Location, owner_id)
        if owner is None:
            raise RuntimeError(f"资产不存在: {owner_id}")
        base_prompt = payload.get("prompt") or owner.description or owner.name
        subdir = CHARACTER_SUBDIR if is_character else LOCATION_SUBDIR
        asset_type = CHARACTER_ASSET_TYPE if is_character else LOCATION_ASSET_TYPE
        generator = get_image_generator(job.project_id, subdir)

        asset_ids: list[str] = []
        generated_views: list[str] = []
        for view in views:
            request = ImageGenerationRequest(
                prompt=view_prompt(base_prompt, view),
                count=1,
                width=int(payload.get("width", 1024)),
                height=int(payload.get("height", 576)),
                seed=owner.seed,
            )
            images = await generator.generate(request)
            if not images:
                raise RuntimeError(f"{view} 未产出图像")
            image = images[0]
            asset = store_generated_asset(
                session,
                job.project_id,
                owner_id,
                asset_type,
                view,
                image.path,
                image.model,
                image.seed,
            )
            asset_ids.append(asset.id)
            generated_views.append(view)

        if owner.status == "DRAFT":
            owner.status = "PENDING_CONFIRM"
            session.add(owner)
        session.commit()
        return {"asset_ids": asset_ids, "views": generated_views}

    async def _do_video(self, session: Session, job: GenerationJob) -> dict:
        from ..services.assets import shot_asset_blocks

        payload = job.payload
        shot = session.get(Shot, payload["shot_id"])
        if shot is None:
            raise RuntimeError(f"Shot 不存在: {payload['shot_id']}")
        prompt = payload.get("prompt") or shot.description or shot.title
        first_frame = None
        if shot.storyboard_id:
            sb = session.get(Storyboard, shot.storyboard_id)
            if sb is not None:
                first_frame = str(abs_path(job.project_id, sb.image_path))
        asset_refs = shot_asset_blocks(session, job.project_id, shot)["reference_images"]
        reference_images = ([first_frame] if first_frame else []) + [
            ref for ref in asset_refs if ref != first_frame
        ]
        request = VideoGenerationRequest(
            shot_id=shot.id,
            prompt=prompt,
            duration=max(shot.duration_target, 1.0),
            first_frame=first_frame,
            reference_images=reference_images,
        )
        generator = get_video_generator(job.project_id)
        adapter_job_id = await generator.submit(request)
        job.result = {"adapter_job_id": adapter_job_id}
        session.add(job)
        session.commit()

        while True:
            status = await generator.status(adapter_job_id)
            if status == "DONE":
                break
            if status in ("FAILED", "CANCELLED", "UNKNOWN"):
                raise RuntimeError(f"视频生成失败: {status}")
            await asyncio.sleep(ADAPTER_POLL_INTERVAL)

        video = await generator.result(adapter_job_id)
        # 先落到 job 专属暂存名：id 冲突重试时才不会覆盖别的 Take 已成片的文件
        staged = abs_path(job.project_id, f"takes/_staging/{job.id}.mp4")
        staged.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(video.path, staged)
        duration = probe_duration(staged) or video.duration

        rel = proxy_rel = ""
        take: Take | None = None
        for _ in range(ID_ATTEMPTS):
            index, take_id = next_seq_and_id(session, Take, job.project_id, "take")
            rel = f"takes/{shot.id}/{take_id}.mp4"
            proxy_rel = f"proxies/{take_id}.mp4"
            proxy = make_proxy(staged, abs_path(job.project_id, proxy_rel))
            take = Take(
                id=take_id,
                project_id=job.project_id,
                shot_id=shot.id,
                index=index,
                generation_job_id=job.id,
                prompt=prompt,
                model=video.model,
                seed=video.seed,
                original_path=rel,
                proxy_path=proxy_rel if proxy is not None else None,
                duration=duration,
            )
            session.add(take)
            try:
                session.commit()
                break
            except IntegrityError:
                session.rollback()
                abs_path(job.project_id, proxy_rel).unlink(missing_ok=True)
                take = None
        if take is None:
            raise RuntimeError("take id 分配连续冲突")

        dest = abs_path(job.project_id, rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(staged, dest)
        return {"take_id": take.id, "adapter_job_id": adapter_job_id}


generation_queue = GenerationQueue()
