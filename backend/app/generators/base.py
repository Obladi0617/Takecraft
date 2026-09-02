from typing import Protocol

from pydantic import BaseModel, Field


# 规格书第 16 节
class ImageGenerationRequest(BaseModel):
    prompt: str
    negative_prompt: str | None = None
    references: list[str] = Field(default_factory=list)
    width: int = 1024
    height: int = 576
    count: int = 4
    seed: int | None = None


class GeneratedImage(BaseModel):
    path: str  # 生成文件的绝对路径
    model: str
    seed: int | None = None
    width: int = 1024
    height: int = 576


class ImageGenerator(Protocol):
    async def generate(
        self, request: ImageGenerationRequest
    ) -> list[GeneratedImage]: ...


# 规格书第 21 节
class VideoGenerationRequest(BaseModel):
    shot_id: str
    prompt: str
    negative_prompt: str | None = None
    duration: float = 4.0
    reference_images: list[str] = Field(default_factory=list)
    reference_videos: list[str] = Field(default_factory=list)
    reference_audio: list[str] = Field(default_factory=list)
    first_frame: str | None = None
    last_frame: str | None = None
    parameters: dict = Field(default_factory=dict)


class GeneratedVideo(BaseModel):
    path: str  # 生成文件的绝对路径
    model: str
    seed: int | None = None
    duration: float


class VideoGenerator(Protocol):
    async def submit(self, request: VideoGenerationRequest) -> str: ...

    async def status(self, job_id: str) -> str: ...

    async def cancel(self, job_id: str) -> None: ...

    async def result(self, job_id: str) -> GeneratedVideo: ...
