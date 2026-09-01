from sqlmodel import Field, SQLModel


class Scene(SQLModel, table=True):
    id: str = Field(primary_key=True)  # scene_001
    project_id: str = Field(index=True)
    index: int
    title: str
    description: str = ""
