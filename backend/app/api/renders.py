from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..compliance import compliance_gate
from ..db import project_session
from ..domain import Take, TimelineClip
from ..media.render import render_timeline
from ..services.timeline import resolve_clip_take

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["renders"])


@router.post("/render")
def render(project_id: str, session: Session = Depends(project_session)):
    stmt = (
        select(TimelineClip)
        .where(TimelineClip.project_id == project_id)
        .order_by(TimelineClip.index)
    )
    clips = list(session.exec(stmt))
    if not clips:
        raise HTTPException(status_code=400, detail="时间线为空，无法渲染")

    pairs = []
    for clip in clips:
        take = resolve_clip_take(session, clip)
        if take is None:
            raise HTTPException(status_code=400, detail=f"Take 不存在: {clip.take_id}")
        pairs.append((clip, take))

    # 成片出口前的最终合规闸：审核时间线引用的全部提示词
    for _, take in pairs:
        compliance_gate.check_or_raise(
            take.prompt, "render.final", project_id
        )

    try:
        result = render_timeline(project_id, pairs)
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    return {
        **result,
        "url": f"/media/{project_id}/{result['path']}",
    }
