from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import abs_path, project_session
from ..compliance import compliance_gate
from ..domain import GenerationJob, Shot, Storyboard, Take
from ..jobs import generation_queue
from ..media.ffmpeg import make_proxy, probe_duration
from ..repositories import next_seq_and_id

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["takes"])


class TakeGenerateIn(BaseModel):
    count: int | None = None
    prompt: str | None = None
    force: bool = False


@router.post("/shots/{shot_id}/takes/generate", status_code=202)
async def generate_takes(
    project_id: str,
    shot_id: str,
    body: TakeGenerateIn | None = None,
    session: Session = Depends(project_session),
):
    body = body or TakeGenerateIn()
    shot = session.get(Shot, shot_id)
    if shot is None or shot.project_id != project_id:
        raise HTTPException(status_code=404, detail="shot not found")

    # 规格书：Storyboard 未锁定属于用户可解决的阻塞
    locked = False
    if shot.storyboard_id:
        sb = session.get(Storyboard, shot.storyboard_id)
        locked = sb is not None and sb.status == "LOCKED"
    if not locked and not body.force:
        raise HTTPException(
            status_code=409,
            detail="storyboard_not_locked: 请先锁定分镜，或传 force=true 跳过",
        )

    count = max(body.count or shot.take_count, 1)
    prompt = body.prompt or shot.description or shot.title
    compliance_gate.check_or_raise(prompt, "take.generate", project_id)
    jobs = []
    for _ in range(count):
        index, job_id = next_seq_and_id(session, GenerationJob, project_id, "job")
        job = GenerationJob(
            id=job_id,
            project_id=project_id,
            shot_id=shot_id,
            index=index,
            job_type="VIDEO",
            payload={"shot_id": shot_id, "prompt": prompt},
        )
        session.add(job)
        jobs.append(job)
    session.commit()
    for job in jobs:
        session.refresh(job)
    generation_queue.notify()
    return {
        "count": count,
        "jobs": [j.model_dump() for j in jobs],
    }


@router.post(
    "/shots/{shot_id}/takes", response_model=Take, status_code=201
)
async def upload_take(
    project_id: str,
    shot_id: str,
    file: UploadFile,
    prompt: str = Form(""),
    model_name: str = Form("imported"),
    seed: int | None = Form(None),
    session: Session = Depends(project_session),
):
    shot = session.get(Shot, shot_id)
    if shot is None or shot.project_id != project_id:
        raise HTTPException(status_code=404, detail="shot not found")

    suffix = Path(file.filename or "take.mp4").suffix or ".mp4"
    index, take_id = next_seq_and_id(session, Take, project_id, "take")
    rel_path = f"takes/{shot_id}/{take_id}{suffix}"
    dest = abs_path(project_id, rel_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            out.write(chunk)

    duration = probe_duration(dest)
    proxy_rel = f"proxies/{take_id}.mp4"
    proxy = make_proxy(dest, abs_path(project_id, proxy_rel))

    take = Take(
        id=take_id,
        project_id=project_id,
        shot_id=shot_id,
        index=index,
        prompt=prompt,
        model=model_name,
        seed=seed,
        original_path=rel_path,
        proxy_path=proxy_rel if proxy is not None else None,
        duration=duration,
    )
    session.add(take)
    session.commit()
    session.refresh(take)
    return take


@router.get("/shots/{shot_id}/takes", response_model=list[Take])
def list_takes(
    project_id: str,
    shot_id: str,
    session: Session = Depends(project_session),
):
    stmt = (
        select(Take)
        .where(Take.project_id == project_id, Take.shot_id == shot_id)
        .order_by(Take.index)
    )
    return list(session.exec(stmt))
