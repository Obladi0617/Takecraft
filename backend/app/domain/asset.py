from datetime import datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from .base import utcnow


class Asset(SQLModel, table=True):
    id: str = Field(primary_key=True)
    project_id: str = Field(index=True)
    type: str = Field(default="OTHER")
    path: str
    proxy_path: str | None = None
    source: str = Field(default="USER")  # USER | GENERATED
    meta: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)
