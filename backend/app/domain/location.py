from datetime import datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from .base import utcnow

LOCATION_SOURCES = ["AUTO", "IMPORT"]
LOCATION_STATUSES = ["DRAFT", "PENDING_CONFIRM", "LOCKED"]
# 规格书第 14 节：参考素材四类
LOCATION_REF_KINDS = ["ESTABLISHING", "KEY_ANGLE_A", "KEY_ANGLE_B", "DETAIL"]


class Location(SQLModel, table=True):
    id: str = Field(primary_key=True)  # loc_001
    project_id: str = Field(index=True)
    index: int = Field(default=0)
    scene_id: str | None = Field(default=None, index=True)
    name: str = ""
    description: str = ""
    visual_style: str = ""
    materials: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    colors: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    immutable_elements: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    lighting_rules: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    # 具体可视锚点（例：红色招牌、绿色长椅），比风格长句更能约束跨镜头一致性
    visual_cues: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    time_of_day_default: str = ""
    # 锁定瞬间编译并固化，逐字注入提示词
    prompt_block: str = ""
    prompt_block_hash: str = ""
    seed: int | None = None
    source: str = Field(default="AUTO")
    status: str = Field(default="DRAFT")
    version: int = Field(default=1)
    locked_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
