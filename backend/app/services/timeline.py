"""自动剪辑服务：API 路由与 LangGraph Editor 节点共用。"""

from sqlalchemy import delete
from sqlmodel import Session, select

from ..domain import Shot, Take, TimelineClip


def auto_edit_timeline(project_id: str, session: Session) -> dict:
    """按 Shot 顺序，用各自 selected_take 重建时间线，返回时间线响应。"""
    stmt = select(Shot).where(Shot.project_id == project_id).order_by(Shot.index)
    shots = [s for s in session.exec(stmt) if s.selected_take_id]

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
