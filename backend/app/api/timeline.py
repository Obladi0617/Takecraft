from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import delete
from sqlmodel import Session, select

from ..db import project_session
from ..domain import Shot, Take, TimelineClip
from ..services.timeline import auto_edit_timeline

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["timeline"])


class ClipIn(BaseModel):
    shot_id: str | None = None
    take_id: str | None = None
    asset_id: str | None = None
    source_in: float = 0.0
    source_out: float | None = None
    volume: float = 1.0
    transition_in: str | None = None
    transition_out: str | None = None


class TimelineIn(BaseModel):
    clips: list[ClipIn]


def _clip_duration(clip: ClipIn, shot: Shot | None, take: Take | None) -> float:
    if clip.source_out is not None:
        return max(clip.source_out - clip.source_in, 0.0)
    if take is not None and take.duration is not None:
        return max(take.duration - clip.source_in, 0.0)
    if shot is not None:
        return max(shot.duration_target - clip.source_in, 0.0)
    return 2.0


def _timeline_response(project_id: str, session: Session) -> dict:
    stmt = (
        select(TimelineClip)
        .where(TimelineClip.project_id == project_id)
        .order_by(TimelineClip.index)
    )
    clips = list(session.exec(stmt))
    items = []
    total = 0.0
    for clip in clips:
        take = session.get(Take, clip.take_id) if clip.take_id else None
        shot = session.get(Shot, clip.shot_id) if clip.shot_id else None
        media_rel = (take.proxy_path or take.original_path) if take else None
        source_out = clip.source_out
        if source_out is None:
            proxy_clip = ClipIn(
                shot_id=clip.shot_id,
                take_id=clip.take_id,
                source_in=clip.source_in,
            )
            source_out = clip.source_in + _clip_duration(proxy_clip, shot, take)
        total += max(source_out - clip.source_in, 0.0)
        items.append(
            {
                **clip.model_dump(),
                "source_out": source_out,
                "shot_title": shot.title if shot else None,
                "media_url": f"/media/{project_id}/{media_rel}" if media_rel else None,
            }
        )
    return {"clips": items, "duration": total}


@router.get("/timeline")
def get_timeline(project_id: str, session: Session = Depends(project_session)):
    return _timeline_response(project_id, session)


@router.post("/timeline/auto-edit")
def auto_edit(project_id: str, session: Session = Depends(project_session)):
    auto_edit_timeline(project_id, session)
    return _timeline_response(project_id, session)


@router.put("/timeline")
def put_timeline(
    project_id: str, body: TimelineIn, session: Session = Depends(project_session)
):
    session.exec(delete(TimelineClip).where(TimelineClip.project_id == project_id))
    start = 0.0
    for index, clip_in in enumerate(body.clips):
        take = session.get(Take, clip_in.take_id) if clip_in.take_id else None
        shot = session.get(Shot, clip_in.shot_id) if clip_in.shot_id else None
        source_out = clip_in.source_out
        if source_out is None:
            source_out = clip_in.source_in + _clip_duration(clip_in, shot, take)
        clip = TimelineClip(
            id=f"clip_{index + 1:03d}",
            project_id=project_id,
            index=index,
            shot_id=clip_in.shot_id,
            take_id=clip_in.take_id,
            asset_id=clip_in.asset_id,
            timeline_start=start,
            source_in=clip_in.source_in,
            source_out=source_out,
            volume=clip_in.volume,
            transition_in=clip_in.transition_in,
            transition_out=clip_in.transition_out,
        )
        session.add(clip)
        start += max(source_out - clip_in.source_in, 0.0)
    session.commit()
    return _timeline_response(project_id, session)
