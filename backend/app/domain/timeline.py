from sqlmodel import Field, SQLModel


class TimelineClip(SQLModel, table=True):
    id: str = Field(primary_key=True)  # clip_001
    project_id: str = Field(index=True)
    index: int
    shot_id: str | None = None
    take_id: str | None = None
    asset_id: str | None = None
    timeline_start: float = Field(default=0.0)
    source_in: float = Field(default=0.0)
    source_out: float | None = None
    volume: float = Field(default=1.0)
    transition_in: str | None = None  # cut | fade | dissolve
    transition_out: str | None = None
