from ..config import settings
from .base import TextModel, extract_json
from .mock_llm import MockTextModel
from .openai_compat import OpenAICompatibleTextModel


def get_text_model() -> TextModel:
    backend = settings.llm_backend
    if backend == "mock":
        return MockTextModel()
    if backend == "openai":
        return OpenAICompatibleTextModel()
    raise ValueError(f"未知文本后端: {backend}")


__all__ = ["get_text_model", "TextModel", "extract_json"]
