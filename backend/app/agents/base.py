import json
import re
from typing import Protocol


class TextModel(Protocol):
    """统一文本模型接口（规格 Model Agnostic）。"""

    name: str

    async def complete(self, system: str, user: str) -> str: ...


def extract_json(text: str) -> dict:
    """从 LLM 回复中提取第一个 JSON 对象。

    用 raw_decode 而非括号计数：字符串值中出现 {} 时计数法必错。
    """
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    if start == -1:
        raise ValueError("回复中未找到 JSON")
    try:
        result, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON 不完整: {e}") from e
    if not isinstance(result, dict):
        raise ValueError("JSON 不是对象")
    return result
