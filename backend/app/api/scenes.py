from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import project_session
from ..domain import Scene
from ..repositories import next_seq_and_id

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["scenes"])


class SceneCreate(BaseModel):
    title: str
    description: str = ""


@router.get("/scenes", response_model=list[Scene])
def list_scenes(project_id: str, session: Session = Depends(project_session)):
    stmt = (
        select(Scene)
        .where(Scene.project_id == project_id)
        .order_by(Scene.index)
    )
    return list(session.exec(stmt))


@router.post("/scenes", response_model=Scene, status_code=201)
def create_scene(
    project_id: str, body: SceneCreate, session: Session = Depends(project_session)
):
    index, scene_id = next_seq_and_id(session, Scene, project_id, "scene")
    scene = Scene(
        id=scene_id,
        project_id=project_id,
        index=index,
        title=body.title,
        description=body.description,
    )
    session.add(scene)
    session.commit()
    session.refresh(scene)
    return scene
