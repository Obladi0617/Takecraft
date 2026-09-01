import asyncio
import shutil
from pathlib import Path

from sqlmodel import Session, select

from ..config import settings
from ..db import abs_path, get_engine, project_path
from ..domain import GenerationJob, Shot, Storyboard, Take
from ..generators import get_image_generator, get_video_generator
from ..generators.base import ImageGenerationRequest, VideoGenerationRequest
from ..media.ffmpeg import make_proxy, probe_duration
from ..repositories import next_seq_and_id

PRIORITY_RANK = {"CRITICAL": 0, "HIGH": 1, "NORMAL": 2, "LOW": 3}
POLL_INTERVAL = 2.0
ADAPTER_POLL_INTERVAL = 0.5
MAX_RETRY = 1


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
                else:
                    raise RuntimeError(f"不支持的 job_type: {job.job_type}")
                job.status = "DONE"
            except asyncio.CancelledError:
                job.status = "CANCELLED"
                session.add(job)
                session.commit()
                raise
            except Exception as e:  # noqa: BLE001
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
        )
        images = await get_image_generator(job.project_id).generate(request)
        ids = []
        root = project_path(job.project_id)
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
        session.commit()
        return {"storyboard_ids": ids}

    async def _do_video(self, session: Session, job: GenerationJob) -> dict:
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
        request = VideoGenerationRequest(
            shot_id=shot.id,
            prompt=prompt,
            duration=max(shot.duration_target, 1.0),
            first_frame=first_frame,
            reference_images=[first_frame] if first_frame else [],
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
        index, take_id = next_seq_and_id(session, Take, job.project_id, "take")
        rel = f"takes/{shot.id}/{take_id}.mp4"
        dest = abs_path(job.project_id, rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(video.path, dest)
        proxy_rel = f"proxies/{take_id}.mp4"
        proxy = make_proxy(dest, abs_path(job.project_id, proxy_rel))
        session.add(
            Take(
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
                duration=probe_duration(dest) or video.duration,
            )
        )
        session.commit()
        return {"take_id": take_id, "adapter_job_id": adapter_job_id}


generation_queue = GenerationQueue()
