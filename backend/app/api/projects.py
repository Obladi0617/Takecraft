import json
import shutil
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from ..db import (
    PROJECT_SUBDIRS,
    drop_engine,
    init_project_db,
    project_path,
    project_session,
    projects_root,
    write_project_json,
)
from ..domain import Project

router = APIRouter(prefix="/api/v1", tags=["projects"])


class ProjectCreate(BaseModel):
    name: str
    idea: str | None = None
    mode: str = "AUTO"
    default_take_count: int = 1


def _load_registry() -> list[dict]:
    items = []
    if projects_root().exists():
        for pjson in projects_root().glob("*/project.json"):
            try:
                items.append(json.loads(pjson.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
    items.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return items


@router.post("/projects", response_model=Project, status_code=201)
def create_project(body: ProjectCreate):
    project_id = uuid.uuid4().hex[:12]
    root = project_path(project_id)
    for sub in PROJECT_SUBDIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)
    engine = init_project_db(project_id)
    project = Project(
        id=project_id,
        name=body.name,
        idea=body.idea,
        mode=body.mode if body.mode in ("AUTO", "DIRECTOR") else "AUTO",
        default_take_count=max(1, body.default_take_count),
    )
    with Session(engine) as session:
        session.add(project)
        session.commit()
        session.refresh(project)
    write_project_json(project)
    return project


@router.get("/projects")
def list_projects():
    return _load_registry()


@router.get("/projects/{project_id}", response_model=Project)
def get_project(project_id: str, session: Session = Depends(project_session)):
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str):
    path = project_path(project_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="project not found")
    deleting_marker = path / ".deleting"
    deleting_marker.write_text("deleting", encoding="utf-8")
    drop_engine(project_id)
    last_error: OSError | None = None
    for attempt in range(5):
        try:
            shutil.rmtree(path)
            return
        except OSError as exc:
            last_error = exc
            if attempt < 4:
                time.sleep(0.15 * (attempt + 1))
    if deleting_marker.exists():
        deleting_marker.unlink()
    raise HTTPException(status_code=409, detail=f"项目文件仍被占用，请稍后重试: {last_error}")
