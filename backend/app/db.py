import json
from pathlib import Path
from typing import Iterator

from fastapi import HTTPException
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from . import domain  # noqa: F401  导入以注册全部表
from .config import settings
from .domain import Project

# 规格书第 43 节：项目目录布局
PROJECT_SUBDIRS = [
    "assets/characters",
    "assets/locations",
    "assets/references",
    "assets/audio",
    "assets/imported_video",
    "storyboards",
    "takes",
    "proxies",
    "previews",
    "renders",
    "snapshots",
    "logs",
]

_engines: dict[str, object] = {}


def projects_root() -> Path:
    return settings.data_dir / "projects"


def project_path(project_id: str) -> Path:
    return projects_root() / project_id


def project_db(project_id: str) -> Path:
    return project_path(project_id) / "project.db"


def abs_path(project_id: str, rel_path: str) -> Path:
    return project_path(project_id) / rel_path


def require_project(project_id: str) -> None:
    if not project_db(project_id).exists():
        raise HTTPException(status_code=404, detail="project not found")


def _make_engine(project_id: str):
    engine = create_engine(
        f"sqlite:///{project_db(project_id)}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def init_project_db(project_id: str):
    # 新项目：先建库再缓存 engine（get_engine 会先做 require_project 存在性检查）
    engine = _make_engine(project_id)
    SQLModel.metadata.create_all(engine)
    _engines[project_id] = engine
    return engine


def get_engine(project_id: str):
    require_project(project_id)
    engine = _engines.get(project_id)
    if engine is None:
        engine = _make_engine(project_id)
        # 幂等：旧项目库在此补建新增的表
        SQLModel.metadata.create_all(engine)
        _engines[project_id] = engine
    return engine


def drop_engine(project_id: str) -> None:
    _engines.pop(project_id, None)


def project_session(project_id: str) -> Iterator[Session]:
    with Session(get_engine(project_id)) as session:
        yield session


def write_project_json(project: Project) -> None:
    payload = {
        "id": project.id,
        "name": project.name,
        "idea": project.idea,
        "mode": project.mode,
        "status": project.status,
        "default_take_count": project.default_take_count,
        "created_at": project.created_at.isoformat(),
        "updated_at": project.updated_at.isoformat(),
    }
    path = project_path(project.id) / "project.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
