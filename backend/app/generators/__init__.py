from ..config import settings
from ..db import project_path
from .base import ImageGenerator, VideoGenerator
from .cloud import ModelScopeImageGenerator, ModelScopeVideoGenerator, MiniMaxVideoGenerator
from .mock import MockImageGenerator, MockVideoGenerator


def get_image_generator(project_id: str) -> ImageGenerator:
    backend = settings.image_backend
    if backend == "mock":
        return MockImageGenerator(project_path(project_id) / "storyboards")
    if backend == "modelscope":
        return ModelScopeImageGenerator(project_path(project_id) / "storyboards")
    raise ValueError(f"未知图像后端: {backend}")


def get_video_generator(project_id: str) -> VideoGenerator:
    backend = settings.video_backend
    if backend == "mock":
        return MockVideoGenerator(project_path(project_id) / "takes" / "_generated")
    if backend == "minimax":
        return MiniMaxVideoGenerator(project_path(project_id) / "takes" / "_generated")
    if backend == "modelscope":
        return ModelScopeVideoGenerator(project_path(project_id) / "takes" / "_generated")
    raise ValueError(f"未知视频后端: {backend}")


__all__ = ["get_image_generator", "get_video_generator"]
