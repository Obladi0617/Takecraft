from datetime import datetime

from sqlmodel import Field, SQLModel

from .base import utcnow


class Project(SQLModel, table=True):
    id: str = Field(primary_key=True)
    name: str
    idea: str | None = None
    mode: str = Field(default="AUTO")  # AUTO | DIRECTOR
    status: str = Field(default="PROJECT_CREATED")
    default_take_count: int = Field(default=1)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
