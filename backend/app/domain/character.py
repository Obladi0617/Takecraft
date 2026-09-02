from datetime import datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from .base import utcnow

# 规格书第 13.3 节
CHARACTER_SOURCES = ["AUTO", "IMPORT"]
# DRAFT：Agent 产出待确认；PENDING_CONFIRM：三视图已生成待用户确认；LOCKED：锁定后所有 Shot 引用
CHARACTER_STATUSES = ["DRAFT", "PENDING_CONFIRM", "LOCKED"]
TURNAROUND_VIEWS = ["FRONT", "SIDE", "BACK"]
CHARACTER_ROLES = ["PRIMARY", "SUPPORTING"]


class Character(SQLModel, table=True):
    id: str = Field(primary_key=True)  # char_001
    project_id: str = Field(index=True)
    index: int = Field(default=0)
    name: str = ""
    age_range: str | None = None
    gender: str | None = None
    role: str = Field(default="PRIMARY")
    description: str = ""
    appearance: str = ""
    costume: str = ""
    personality: str = ""
    immutable_traits: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    # 原子化视觉锚点（发型/瞳色/体型/标记物…）：比长句更能扛住提示词截断
    visual_anchors: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    # 锁定瞬间编译并固化，之后逐字注入每个镜头提示词，杜绝跨镜头漂移
    prompt_block: str = ""
    prompt_block_hash: str = ""
    # 无参考图能力的后端靠固定 seed 兜底一致性
    seed: int | None = None
    # 规格书第 13.4 节：第一版不训练 LoRA，仅预留身份模型挂载点
    identity_model_id: str | None = None
    identity_model_type: str | None = None
    source: str = Field(default="AUTO")
    status: str = Field(default="DRAFT")
    version: int = Field(default=1)
    locked_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
