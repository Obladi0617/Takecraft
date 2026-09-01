from datetime import datetime

from sqlmodel import Field, SQLModel

from .base import utcnow


class Storyboard(SQLModel, table=True):
    id: str = Field(primary_key=True)  # sb_001
    project_id: str = Field(index=True)
    shot_id: str = Field(index=True)
    index: int
    prompt: str = ""
    negative_prompt: str | None = None
    image_path: str  # 项目内相对路径
    width: int = Field(default=1024)
    height: int = Field(default=576)
    model: str = ""
    seed: int | None = None
    status: str = Field(default="CANDIDATE")  # CANDIDATE | LOCKED
    created_at: datetime = Field(default_factory=utcnow)
