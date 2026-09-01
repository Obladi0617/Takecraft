from datetime import datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from .base import utcnow


class Take(SQLModel, table=True):
    id: str = Field(primary_key=True)  # take_001
    project_id: str = Field(index=True)
    shot_id: str = Field(index=True)
    index: int = Field(default=0)
    generation_job_id: str | None = None
    prompt: str = ""
    negative_prompt: str | None = None
    model: str = Field(default="imported")
    seed: int | None = None
    parameters: dict = Field(default_factory=dict, sa_column=Column(JSON))
    reference_images: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    reference_videos: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    reference_audio: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    original_path: str
    proxy_path: str | None = None
    duration: float | None = None
    created_at: datetime = Field(default_factory=utcnow)
