from ..config import settings
from .base import TextModel, extract_json
from .mock_llm import MockTextModel
from .openai_compat import OpenAICompatibleTextModel
from .video_understanding import VideoUnderstandingModel
from .vlm_mock import MockVideoUnderstandingModel
from .vlm_openai import OpenAICompatibleVideoModel


def get_text_model() -> TextModel:
    backend = settings.llm_backend
    if backend == "mock":
        return MockTextModel()
    if backend == "openai":
        return OpenAICompatibleTextModel()
    raise ValueError(f"未知文本后端: {backend}")


def get_video_understanding_model() -> VideoUnderstandingModel:
    """Reviewer 的眼睛：mock（离线合成）或 openai 兼容 VLM（云端 / 本地 vLLM）。"""
    backend = settings.vlm_backend
    if backend == "mock":
        return MockVideoUnderstandingModel()
    if backend == "openai":
        return OpenAICompatibleVideoModel()
    raise ValueError(f"未知视觉后端: {backend}")


__all__ = [
    "TextModel",
    "VideoUnderstandingModel",
    "extract_json",
    "get_text_model",
    "get_video_understanding_model",
]
