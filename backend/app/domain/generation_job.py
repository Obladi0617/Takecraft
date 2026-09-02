from datetime import datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from .base import utcnow

# 规格书第 23 节；PIPELINE 为一句话全自动流程的编排任务
JOB_TYPES = ["CHARACTER", "LOCATION", "STORYBOARD", "VIDEO", "PIPELINE"]
JOB_STATUSES = [
    "PENDING",
    "RUNNING",
    "REVIEWING",
    "RETAKE",
    "DONE",
    "FAILED",
    "CANCELLED",
]


class GenerationJob(SQLModel, table=True):
    id: str = Field(primary_key=True)  # job_001
    project_id: str = Field(index=True)
    shot_id: str | None = Field(default=None, index=True)
    index: int = Field(default=0)
    job_type: str = Field(default="VIDEO")  # JOB_TYPES
    priority: str = Field(default="NORMAL")  # CRITICAL | HIGH | NORMAL | LOW
    status: str = Field(default="PENDING")  # JOB_STATUSES
    retry_count: int = Field(default=0)
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    result: dict = Field(default_factory=dict, sa_column=Column(JSON))
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
