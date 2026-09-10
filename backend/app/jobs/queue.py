import asyncio
import shutil
from collections import deque
from pathlib import Path

from PIL import Image, ImageFilter
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from ..config import settings
from ..db import abs_path, get_engine, project_path
from ..domain import Character, GenerationJob, Location, Shot, Storyboard, Take
from ..generators import get_image_generator, get_video_generator
from ..generators.base import ImageGenerationRequest, VideoGenerationRequest
from ..media.ffmpeg import make_proxy, probe_duration, trim_to_duration
from ..repositories import next_seq_and_id

PRIORITY_RANK = {"CRITICAL": 0, "HIGH": 1, "NORMAL": 2, "LOW": 3}
POLL_INTERVAL = 2.0
ADAPTER_POLL_INTERVAL = 0.5
MAX_RETRY = 1
CHARACTER_VIEW_ATTEMPTS = 3
# max(index)+1 在并发下会撞主键，撞了就重算
ID_ATTEMPTS = 3


def _large_foreground_components(path: str) -> int:
    """估算纯色角色设定图中的大型独立主体数，用于拦截双人拼版。"""
    with Image.open(path) as source:
        image = source.convert("RGB")
        image.thumbnail((192, 256))
        width, height = image.size
        pixels = image.load()
        corners = [pixels[0, 0], pixels[width - 1, 0], pixels[0, height - 1], pixels[width - 1, height - 1]]
        background = tuple(sum(color[channel] for color in corners) // len(corners) for channel in range(3))
        mask = Image.new("L", image.size)
        mask.putdata([
            255 if sum(abs(pixel[channel] - background[channel]) for channel in range(3)) > 72 else 0
            for pixel in image.getdata()
        ])
        # 连起人物内部被浅色衣物切开的细小缝隙，但不跨越两个角色之间的大间距。
        mask = mask.filter(ImageFilter.MaxFilter(3))
        data = mask.load()
        visited = bytearray(width * height)
        minimum_area = max(int(width * height * 0.025), 80)
        minimum_height = max(int(height * 0.28), 24)
        large = 0
        for y in range(height):
            for x in range(width):
                index = y * width + x
                if visited[index] or not data[x, y]:
                    continue
                queue = deque([(x, y)])
                visited[index] = 1
                area = 0
                min_y = max_y = y
                while queue:
                    px, py = queue.popleft()
                    area += 1
                    min_y = min(min_y, py)
                    max_y = max(max_y, py)
                    for nx, ny in ((px - 1, py), (px + 1, py), (px, py - 1), (px, py + 1)):
                        if 0 <= nx < width and 0 <= ny < height:
                            next_index = ny * width + nx
                            if not visited[next_index] and data[nx, ny]:
                                visited[next_index] = 1
                                queue.append((nx, ny))
                if area >= minimum_area and max_y - min_y + 1 >= minimum_height:
                    large += 1
        return large


class GenerationQueue:
    """规格书第 23 节：所有生成任务进入统一队列（进程内异步队列 + DB 持久化）。"""

    def __init__(self) -> None:
        self._wakeup = asyncio.Event()
        self._running = False
        self._image_sem: asyncio.Semaphore | None = None
        self._video_sem: asyncio.Semaphore | None = None
        self._review_sem: asyncio.Semaphore | None = None
        self._poll_task: asyncio.Task | None = None
        self._inflight: set[str] = set()

    async def start(self) -> None:
        self._running = True
        self._image_sem = asyncio.Semaphore(settings.image_generation_concurrency)
        self._video_sem = asyncio.Semaphore(settings.video_generation_concurrency)
        self._review_sem = asyncio.Semaphore(settings.review_concurrency)
        self._recover_interrupted_asset_jobs()
        self._poll_task = asyncio.create_task(self._poll_loop())

    def _recover_interrupted_asset_jobs(self) -> None:
        """接管上次进程被中断的资产任务，并从尚未完成的视角继续。

        三视图是逐张生成和落库的。进程若在第二、第三张之间退出，数据库会
        留下 RUNNING；新进程必须将它恢复为 PENDING，否则界面会永久显示生成中。
        completed_views 用作逐张检查点，保证恢复后不会重算已经完成的视角。
        """
        for project_id in self._active_projects():
            with Session(get_engine(project_id)) as session:
                jobs = list(
                    session.exec(
                        select(GenerationJob).where(
                            GenerationJob.project_id == project_id,
                            GenerationJob.job_type.in_(["CHARACTER", "LOCATION"]),  # type: ignore[arg-type]
                            GenerationJob.status == "RUNNING",
                        )
                    )
                )
                for job in jobs:
                    desired_views = list((job.payload or {}).get("views") or [])
                    result = dict(job.result or {})
                    completed = list(result.get("completed_views") or [])
                    # 兼容旧任务：旧版本尚未写检查点时，以已落库的视角作为恢复起点。
                    if not completed:
                        owner_id = str((job.payload or {}).get("owner_id") or "")
                        completed = [
                            view for view in desired_views
                            if session.get(Asset, f"{owner_id}_{view}") is not None
                        ]
                    result["completed_views"] = completed
                    result["recovered_after_restart"] = True
                    job.result = result
                    job.status = "DONE" if all(view in completed for view in desired_views) else "PENDING"
                    job.error = None
                    session.add(job)
                session.commit()

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
        # 只有会打外部重资源的任务类型才限并发：VIDEO 打生成端点，REVIEW 打视觉模型
        semaphores = {
            "STORYBOARD": self._image_sem,
            "CHARACTER": self._image_sem,
            "LOCATION": self._image_sem,
            "VIDEO": self._video_sem,
            "REVIEW": self._review_sem,
        }
        semaphore = semaphores.get(job_type)
        try:
            if semaphore is not None:
                async with semaphore:
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
                elif job.job_type == "REVIEW":
                    job.result = await self._do_review(session, job)
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
        validate_single_human = bool(
            is_character
            and isinstance(owner, Character)
            and owner.gender not in {"其他", "非人", "无"}
        )

        asset_ids: list[str] = []
        checkpoint = dict(job.result or {})
        generated_views: list[str] = list(checkpoint.get("completed_views") or [])
        for view in views:
            if view in generated_views:
                continue
            image = None
            attempts = CHARACTER_VIEW_ATTEMPTS if is_character else 1
            for attempt in range(attempts):
                request = ImageGenerationRequest(
                    prompt=view_prompt(base_prompt, view),
                    negative_prompt=(
                        "two people, multiple people, duplicate person, cloned person, mirrored person, "
                        "character sheet, turnaround sheet, multiple views, split screen, collage, grid, panels"
                        if is_character else None
                    ),
                    count=1,
                    width=int(payload.get("width", 1024)),
                    height=int(payload.get("height", 576)),
                    seed=(owner.seed + attempt * 7919) if owner.seed is not None else None,
                    references=[str(path) for path in payload.get("references") or []],
                )
                images = await generator.generate(request)
                if not images:
                    continue
                candidate = images[0]
                if validate_single_human and _large_foreground_components(candidate.path) >= 2:
                    Path(candidate.path).unlink(missing_ok=True)
                    continue
                image = candidate
                break
            if image is None:
                raise RuntimeError(f"{view} 连续 {attempts} 次检测到多人物或未产出图像，已拒绝写入")
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
            # 每完成一张就持久化检查点。即使进程此刻重启，下一次也只补剩余视角。
            checkpoint["completed_views"] = list(generated_views)
            checkpoint["asset_ids"] = list(dict.fromkeys([*(checkpoint.get("asset_ids") or []), *asset_ids]))
            job.result = dict(checkpoint)
            session.add(job)
            session.commit()

        # 重新生成参考图即视为设定有变：LOCKED 也要退回待确认，
        # 否则 api 层的 _assert_editable 会永久 409，编辑入口不可达
        if owner.status != "PENDING_CONFIRM":
            owner.status = "PENDING_CONFIRM"
            session.add(owner)
        session.commit()
        return {
            "asset_ids": list(dict.fromkeys([*(checkpoint.get("asset_ids") or []), *asset_ids])),
            "views": generated_views,
            "completed_views": generated_views,
        }

    async def _do_video(self, session: Session, job: GenerationJob) -> dict:
        from ..services.assets import shot_asset_blocks
        import logging
        logger = logging.getLogger(__name__)

        payload = job.payload
        if payload.get("mode") == "scene_r2v":
            shot_ids = list(payload.get("shot_ids") or [])
            shots = [session.get(Shot, shot_id) for shot_id in shot_ids]
            if not shots or any(shot is None for shot in shots):
                raise RuntimeError("场景 R2V 任务包含不存在的镜头")
            prompt = str(payload.get("prompt") or "")
            references = [str(path) for path in payload.get("references") or [] if Path(path).is_file()]
            target_duration = sum(max(float(shot.duration_target), 1.0) for shot in shots if shot)
            logger.info("[VIDEO_R2V] scene=%s shots=%s references=%s duration=%.2f",
                        payload.get("scene_id"), shot_ids, len(references), target_duration)
            request = VideoGenerationRequest(
                shot_id=shot_ids[0], prompt=prompt, duration=target_duration,
                first_frame=None, last_frame=None, reference_images=references,
            )
            generator = get_video_generator(job.project_id)
            adapter_job_id = await generator.submit(request)
            job.result = {"adapter_job_id": adapter_job_id, "shot_ids": shot_ids}
            session.add(job)
            session.commit()
            while True:
                status = await generator.status(adapter_job_id)
                if status == "DONE":
                    break
                if status in ("FAILED", "CANCELLED", "UNKNOWN"):
                    raise RuntimeError(f"场景 R2V 视频生成失败: {status}")
                await asyncio.sleep(ADAPTER_POLL_INTERVAL)
            video = await generator.result(adapter_job_id)
            staged = abs_path(job.project_id, f"takes/_staging/{job.id}_scene.mp4")
            staged.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(video.path, staged)
            from ..media.ffmpeg import extract_segment
            actual_duration = probe_duration(staged)
            if actual_duration is None or actual_duration < target_duration - 0.20:
                raise RuntimeError(
                    "场景视频时长不足，拒绝切分空白Take："
                    f"实际 {actual_duration or 0:.2f}s / 需要 {target_duration:.2f}s"
                )
            # Keep the complete scene render as the user-facing review/version
            # asset. Shot segments remain internal metadata for editing only.
            scene_rel = f"takes/scenes/{payload.get('scene_id')}/{job.id}.mp4"
            scene_dest = abs_path(job.project_id, scene_rel)
            scene_dest.parent.mkdir(parents=True, exist_ok=True)
            if not await asyncio.to_thread(trim_to_duration, staged, scene_dest, target_duration):
                shutil.copy2(staged, scene_dest)
            scene_proxy_rel = f"proxies/scenes/{payload.get('scene_id')}/{job.id}.mp4"
            scene_proxy = make_proxy(scene_dest, abs_path(job.project_id, scene_proxy_rel))
            take_ids: list[str] = []
            offset = 0.0
            try:
                for shot in shots:
                    assert shot is not None
                    segment_duration = max(float(shot.duration_target), 1.0)
                    segment = abs_path(job.project_id, f"takes/_staging/{job.id}_{shot.id}.mp4")
                    if not await asyncio.to_thread(extract_segment, staged, segment, offset, segment_duration):
                        raise RuntimeError(f"场景视频切分失败: {shot.title}")
                    take: Take | None = None
                    for _ in range(ID_ATTEMPTS):
                        index, take_id = next_seq_and_id(session, Take, job.project_id, "take")
                        rel = f"takes/{shot.id}/{take_id}.mp4"
                        proxy_rel = f"proxies/{take_id}.mp4"
                        proxy = make_proxy(segment, abs_path(job.project_id, proxy_rel))
                        take = Take(id=take_id, project_id=job.project_id, shot_id=shot.id,
                            index=index, generation_job_id=job.id, prompt=prompt,
                            model=video.model, seed=video.seed, original_path=rel,
                            proxy_path=proxy_rel if proxy else None, duration=segment_duration,
                            parameters={"mode": "scene_r2v", "scene_id": payload.get("scene_id"),
                                        "segment_start": offset, "segment_duration": segment_duration},
                            reference_images=references)
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
                    shutil.move(segment, dest)
                    take_ids.append(take.id)
                    offset += segment_duration
            finally:
                staged.unlink(missing_ok=True)
            return {"take_ids": take_ids, "shot_ids": shot_ids,
                    "adapter_job_id": adapter_job_id, "mode": "scene_r2v",
                    "scene_id": payload.get("scene_id"),
                    "scene_media_path": scene_proxy_rel if scene_proxy else scene_rel,
                    "duration": target_duration}

        shot = session.get(Shot, payload["shot_id"])
        if shot is None:
            raise RuntimeError(f"Shot 不存在: {payload['shot_id']}")
        prompt = payload.get("prompt") or shot.description or shot.title
        # R2V only: storyboard images are references, never implicit first frames.
        first_frame = None
        asset_refs = shot_asset_blocks(session, job.project_id, shot)["reference_images"]
        logger.info(f"[VIDEO] shot={shot.id} first_frame={first_frame} asset_refs={len(asset_refs)} asset_refs_paths={asset_refs[:3]}")
        reference_images = [str(path) for path in (payload.get("references") or asset_refs)]
        logger.info(f"[VIDEO] reference_images total={len(reference_images)}")
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
        target_duration = max(float(shot.duration_target), 1.0)
        if duration > target_duration + 0.02:
            from ..media.ffmpeg import trim_to_duration

            trimmed = staged.with_name(f"{staged.stem}_trimmed.mp4")
            if await asyncio.to_thread(
                trim_to_duration, staged, trimmed, target_duration
            ):
                staged.unlink(missing_ok=True)
                trimmed.replace(staged)
                duration = probe_duration(staged) or target_duration

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
        # Persist a reusable continuity frame for the next shot and for later
        # human-triggered retakes. Failure to extract must not discard a valid
        # generated Take.
        tail_rel = f"frames/{take.id}_tail.png"
        try:
            from ..media.ffmpeg import extract_tail_frame

            tail = await asyncio.to_thread(
                extract_tail_frame,
                dest,
                abs_path(job.project_id, tail_rel),
            )
        except Exception:  # noqa: BLE001 - continuity frame is best-effort
            tail = None
        return {
            "take_id": take.id,
            "adapter_job_id": adapter_job_id,
            "tail_frame": tail_rel if tail else None,
        }

    async def _do_review(self, session: Session, job: GenerationJob) -> dict:
        """AI Dailies：真实看片审核一条 Take。

        顺序是刻意的——先 ffmpeg 测量与硬闸门，命中就直接判定，绝不为一条
        时长不对或全黑的素材付一次视觉模型的钱。
        """
        from ..agents import get_video_understanding_model
        from ..services.generation import record_artifact
        from ..services.review import (
            asset_anchors,
            build_review,
            make_request,
            measure_and_sample,
        )

        payload = job.payload
        if payload.get("mode") == "compare":
            return await self._do_compare(session, job)

        take = session.get(Take, payload["take_id"])
        if take is None:
            raise RuntimeError(f"Take 不存在: {payload['take_id']}")
        shot = session.get(Shot, take.shot_id)
        if shot is None:
            raise RuntimeError(f"Shot 不存在: {take.shot_id}")

        # ffprobe/ffmpeg 是子进程调用，必须丢到线程里，否则整条队列会被堵住
        expected_duration = max(shot.duration_target, 1.0)
        stats, failures, frames = await asyncio.to_thread(
            measure_and_sample, job.project_id, take, expected_duration
        )

        insight = None
        if not failures and frames:
            characters, locations = asset_anchors(session, job.project_id, shot)
            request = make_request(
                shot, take, stats, frames, characters, locations
            )
            insight = await get_video_understanding_model().describe(request)

        review = build_review(job.project_id, take.id, insight, stats, failures, frames)
        record_artifact(
            session, job.project_id, "take_review", "reviewer", review, take.id
        )
        return {
            "take_id": take.id,
            "decision": review["decision"],
            "score": review["score"],
            "gates": [g["code"] for g in review["gates"]],
            "frame_count": len(frames),
            "vlm_called": insight is not None,
        }

    async def _do_compare(self, session: Session, job: GenerationJob) -> dict:
        """同镜头内已通过初筛的 Take 打擂台：绝对分判可用性，比较判谁进成片。"""
        from ..agents import get_video_understanding_model
        from ..services.generation import record_artifact
        from ..services.review import compare_payload, frames_for_compare

        payload = job.payload
        shot = session.get(Shot, payload["shot_id"])
        if shot is None:
            raise RuntimeError(f"Shot 不存在: {payload['shot_id']}")
        takes = [
            take
            for take in (
                session.get(Take, tid) for tid in (payload.get("candidates") or [])
            )
            if take is not None
        ]
        if len(takes) < 2:
            raise RuntimeError(f"比较候选不足: {[t.id for t in takes]}")

        model = get_video_understanding_model()
        groups = [
            await asyncio.to_thread(
                frames_for_compare, job.project_id, take, max(shot.duration_target, 1.0)
            )
            for take in takes
        ]
        champion, champion_frames = takes[0], groups[0]
        rounds: list[dict] = []
        for challenger, challenger_frames in zip(takes[1:], groups[1:], strict=False):
            verdict = await model.compare(
                shot.title, champion.prompt or shot.description, champion_frames, challenger_frames
            )
            rounds.append(
                {
                    "a": champion.id,
                    "b": challenger.id,
                    "winner": verdict.winner,
                    "reason": verdict.reason,
                    "confidence": verdict.confidence,
                }
            )
            if verdict.winner == "B":
                champion, champion_frames = challenger, challenger_frames

        data = compare_payload(
            shot, champion.id, [t.id for t in takes], rounds, model.name
        )
        record_artifact(
            session, job.project_id, "take_compare", "reviewer", data, shot.id
        )
        return {"shot_id": shot.id, "winner": champion.id, "rounds": len(rounds)}


generation_queue = GenerationQueue()
