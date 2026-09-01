from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import project_session
from ..domain import GenerationJob, Shot, Storyboard
from ..compliance import compliance_gate
from ..jobs import generation_queue
from ..repositories import next_seq_and_id

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["storyboards"])


class StoryboardGenerateIn(BaseModel):
    prompt: str | None = None
    count: int = 4
    width: int = 1024
    height: int = 576


@router.post("/shots/{shot_id}/storyboards/generate", status_code=202)
async def generate_storyboards(
    project_id: str,
    shot_id: str,
    body: StoryboardGenerateIn | None = None,
    session: Session = Depends(project_session),
):
    body = body or StoryboardGenerateIn()
    shot = session.get(Shot, shot_id)
    if shot is None or shot.project_id != project_id:
        raise HTTPException(status_code=404, detail="shot not found")
    prompt = body.prompt or shot.description or shot.title
    compliance_gate.check_or_raise(prompt, "storyboard.generate", project_id)
    index, job_id = next_seq_and_id(session, GenerationJob, project_id, "job")
    job = GenerationJob(
        id=job_id,
        project_id=project_id,
        shot_id=shot_id,
        index=index,
        job_type="STORYBOARD",
        payload={
            "shot_id": shot_id,
            "prompt": prompt,
            "count": max(body.count, 1),
            "width": body.width,
            "height": body.height,
        },
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    generation_queue.notify()
    return {"job": job.model_dump()}


@router.get("/shots/{shot_id}/storyboards")
def list_storyboards(
    project_id: str,
    shot_id: str,
    session: Session = Depends(project_session),
):
    shot = session.get(Shot, shot_id)
    if shot is None or shot.project_id != project_id:
        raise HTTPException(status_code=404, detail="shot not found")
    stmt = (
        select(Storyboard)
        .where(
            Storyboard.project_id == project_id,
            Storyboard.shot_id == shot_id,
        )
        .order_by(Storyboard.index)
    )
    items = []
    for sb in session.exec(stmt):
        items.append(
            {
                **sb.model_dump(),
                "media_url": f"/media/{project_id}/{sb.image_path}",
                "is_selected": shot.storyboard_id == sb.id,
                "is_locked": sb.status == "LOCKED",
            }
        )
    return items


@router.post("/storyboards/{sb_id}/select")
def select_storyboard(
    project_id: str, sb_id: str, session: Session = Depends(project_session)
):
    sb = session.get(Storyboard, sb_id)
    if sb is None or sb.project_id != project_id:
        raise HTTPException(status_code=404, detail="storyboard not found")
    if sb.status == "LOCKED":
        raise HTTPException(status_code=409, detail="已锁定的分镜不可取消选择")
    shot = session.get(Shot, sb.shot_id)
    if shot is None:
        raise HTTPException(status_code=404, detail="shot not found")
    shot.storyboard_id = sb_id
    session.add(shot)
    session.commit()
    return {"ok": True, "shot_id": shot.id, "storyboard_id": sb_id}


@router.post("/storyboards/{sb_id}/lock")
def lock_storyboard(
    project_id: str, sb_id: str, session: Session = Depends(project_session)
):
    sb = session.get(Storyboard, sb_id)
    if sb is None or sb.project_id != project_id:
        raise HTTPException(status_code=404, detail="storyboard not found")
    shot = session.get(Shot, sb.shot_id)
    if shot is None:
        raise HTTPException(status_code=404, detail="shot not found")
    if shot.storyboard_id != sb_id:
        raise HTTPException(
            status_code=409, detail="请先选择该候选再锁定"
        )
    stmt = select(Storyboard).where(
        Storyboard.project_id == project_id,
        Storyboard.shot_id == sb.shot_id,
        Storyboard.status == "LOCKED",
    )
    for other in session.exec(stmt):
        other.status = "CANDIDATE"
        session.add(other)
    sb.status = "LOCKED"
    session.add(sb)
    session.commit()
    return {"ok": True, "shot_id": shot.id, "storyboard_id": sb_id}
