from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import project_session
from ..domain import Project, Shot, Take
from ..domain.base import utcnow
from ..repositories import next_seq_and_id

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["shots"])


class ShotCreate(BaseModel):
    scene_id: str | None = None
    title: str = ""
    description: str = ""
    duration_target: float = 4.0
    framing: str = "MEDIUM"
    camera_motion: str = "STATIC"
    take_count: int | None = None


class ShotUpdate(BaseModel):
    scene_id: str | None = None
    title: str | None = None
    description: str | None = None
    duration_target: float | None = None
    framing: str | None = None
    camera_motion: str | None = None
    take_count: int | None = None
    status: str | None = None


@router.get("/shots", response_model=list[Shot])
def list_shots(
    project_id: str,
    scene_id: str | None = None,
    session: Session = Depends(project_session),
):
    stmt = select(Shot).where(Shot.project_id == project_id).order_by(Shot.index)
    if scene_id is not None:
        stmt = stmt.where(Shot.scene_id == scene_id)
    return list(session.exec(stmt))


@router.post("/shots", response_model=Shot, status_code=201)
def create_shot(
    project_id: str, body: ShotCreate, session: Session = Depends(project_session)
):
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    index, shot_id = next_seq_and_id(session, Shot, project_id, "shot")
    shot = Shot(
        id=shot_id,
        project_id=project_id,
        scene_id=body.scene_id,
        index=index,
        title=body.title,
        description=body.description,
        duration_target=body.duration_target,
        framing=body.framing,
        camera_motion=body.camera_motion,
        take_count=body.take_count or project.default_take_count,
    )
    session.add(shot)
    session.commit()
    session.refresh(shot)
    return shot


@router.put("/shots/{shot_id}", response_model=Shot)
def update_shot(
    project_id: str,
    shot_id: str,
    body: ShotUpdate,
    session: Session = Depends(project_session),
):
    shot = session.get(Shot, shot_id)
    if shot is None or shot.project_id != project_id:
        raise HTTPException(status_code=404, detail="shot not found")
    data = body.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(shot, key, value)
    shot.updated_at = utcnow()
    session.add(shot)
    session.commit()
    session.refresh(shot)
    return shot


@router.post("/shots/{shot_id}/takes/{take_id}/select", response_model=Shot)
def select_take(
    project_id: str,
    shot_id: str,
    take_id: str,
    session: Session = Depends(project_session),
):
    shot = session.get(Shot, shot_id)
    if shot is None or shot.project_id != project_id:
        raise HTTPException(status_code=404, detail="shot not found")
    take = session.get(Take, take_id)
    if take is None or take.shot_id != shot_id:
        raise HTTPException(status_code=404, detail="take not found")
    shot.selected_take_id = take_id
    shot.updated_at = utcnow()
    session.add(shot)
    session.commit()
    session.refresh(shot)
    return shot


@router.delete("/shots/{shot_id}", status_code=204)
def delete_shot(
    project_id: str, shot_id: str, session: Session = Depends(project_session)
):
    shot = session.get(Shot, shot_id)
    if shot is None or shot.project_id != project_id:
        raise HTTPException(status_code=404, detail="shot not found")
    if shot.selected_take_id is not None:
        raise HTTPException(
            status_code=409, detail="不能删除已选 Take 的 Shot，先取消选择"
        )
    session.delete(shot)
    session.commit()
