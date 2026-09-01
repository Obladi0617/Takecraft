from datetime import datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from .base import utcnow


class Shot(SQLModel, table=True):
    id: str = Field(primary_key=True)  # shot_001
    project_id: str = Field(index=True)
    scene_id: str | None = Field(default=None, index=True)
    index: int
    title: str = ""
    description: str = ""
    duration_target: float = Field(default=4.0)
    framing: str = Field(default="MEDIUM")
    camera_motion: str = Field(default="STATIC")
    character_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    location_id: str | None = None
    storyboard_id: str | None = None
    selected_take_id: str | None = None
    take_count: int = Field(default=4)
    status: str = Field(default="PLANNED")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
