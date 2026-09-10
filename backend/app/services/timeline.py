"""自动剪辑服务：API 路由与 LangGraph Editor 节点共用。"""

from sqlalchemy import delete
from sqlmodel import Session, select

from ..domain import Asset, Scene, GenerationJob, Shot, Take, TimelineClip
from ..db import abs_path


def resolve_clip_take(session: Session, clip: TimelineClip) -> Take | None:
    if clip.asset_id:
        asset = session.get(Asset, clip.asset_id)
        if asset and asset.project_id == clip.project_id:
            return Take(id=asset.id, project_id=clip.project_id, shot_id="",
                        original_path=asset.path, proxy_path=asset.proxy_path,
                        duration=asset.meta.get("duration"), prompt=asset.meta.get("prompt", ""))
        return None
    return session.get(Take, clip.take_id) if clip.take_id else None


def auto_edit_timeline(project_id: str, session: Session) -> dict:
    """按 Shot 顺序，用各自 selected_take 重建时间线，返回时间线响应。"""
    stmt = select(Shot).where(Shot.project_id == project_id).order_by(Shot.index)
    shots = [s for s in session.exec(stmt) if s.selected_take_id]

    # Resolve the approved selections to their complete scene source, keeping
    # internal shot segments out of the user-facing timeline and render.
    jobs = list(session.exec(select(GenerationJob).where(GenerationJob.project_id == project_id)))
    if any((j.payload or {}).get("mode") == "scene_r2v" for j in jobs):
        scenes = list(session.exec(select(Scene).where(Scene.project_id == project_id).order_by(Scene.index)))
        sources = []
        for scene in scenes:
            selected = [session.get(Take, s.selected_take_id) for s in shots if s.scene_id == scene.id]
            scene_shots = list(session.exec(select(Shot).where(Shot.project_id == project_id, Shot.scene_id == scene.id)))
            if not selected or len(selected) != len(scene_shots) or any(t is None for t in selected):
                raise ValueError(f"场景 {scene.title} 尚未全部选用")
            job_ids = {t.generation_job_id for t in selected}
            job = next((j for j in jobs if j.id in job_ids and j.status == "DONE"), None)
            if len(job_ids) != 1 or not job or not (job.result or {}).get("scene_media_path"):
                raise ValueError(f"场景 {scene.title} 缺少一致的完整视频版本")
            proxy = job.result["scene_media_path"]
            original = f"takes/scenes/{scene.id}/{job.id}.mp4"
            if not abs_path(project_id, original).is_file():
                original = proxy
            if not abs_path(project_id, original).is_file():
                raise ValueError(f"场景 {scene.title} 视频文件缺失")
            duration = float(job.result["duration"])
            asset = Asset(id=f"scene_video_{job.id}", project_id=project_id, type="VIDEO",
                          path=original, proxy_path=proxy, source="GENERATED",
                          meta={"scene_id": scene.id, "title": scene.title, "duration": duration,
                                "prompt": job.payload.get("prompt", "")})
            sources.append((asset, duration))
        session.exec(delete(TimelineClip).where(TimelineClip.project_id == project_id))
        start = 0.0
        clips = []
        for index, (asset, duration) in enumerate(sources):
            session.merge(asset)
            clip = TimelineClip(id=f"clip_{index + 1:03d}", project_id=project_id,
                                index=index, asset_id=asset.id, timeline_start=start,
                                source_in=0.0, source_out=duration)
            session.add(clip)
            clips.append(clip)
            start += duration
        session.commit()
        return {"clip_count": len(clips), "duration": start, "clips": clips}

    session.exec(delete(TimelineClip).where(TimelineClip.project_id == project_id))
    start = 0.0
    index = 0
    clips = []
    for shot in shots:
        take = session.get(Take, shot.selected_take_id)  # type: ignore[arg-type]
        # 生成模型可能因帧对齐产出更长母片，成片严格服从剧本目标时长。
        media_duration = take.duration if take and take.duration else shot.duration_target
        duration = min(media_duration, shot.duration_target)
        clip = TimelineClip(
            id=f"clip_{index + 1:03d}",
            project_id=project_id,
            index=index,
            shot_id=shot.id,
            take_id=shot.selected_take_id,
            timeline_start=start,
            source_in=0.0,
            source_out=duration,
        )
        session.add(clip)
        clips.append(clip)
        start += duration
        index += 1
    session.commit()
    return {"clip_count": index, "duration": start, "clips": clips}
