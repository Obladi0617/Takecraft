from datetime import datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from .base import utcnow


class AgentArtifact(SQLModel, table=True):
    """Agent 产物：剧本 / 导演圣经 / 生产计划 / 评审结果 / 阶段进度。"""

    id: str = Field(primary_key=True)  # art_001
    project_id: str = Field(index=True)
    index: int = Field(default=0)
    kind: str = Field(index=True)  # production_plan | screenplay | director_bible | take_review | editing | stage | run_summary
    subject_id: str | None = Field(default=None, index=True)  # 关联的 shot/take 等
    agent: str = ""  # producer | writer | director | prompt | reviewer | editor | pipeline
    data: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utcnow)
