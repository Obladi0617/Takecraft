"""OpenAI 兼容文本模型适配器（ModelScope API-Inference / MiniMax / Ollama 等）。"""

import httpx

from ..config import settings


class OpenAICompatibleTextModel:
    name = "openai-compat"

    def __init__(self) -> None:
        if not settings.llm_api_key:
            raise RuntimeError("API Key 缺失：请设置 FILMAGENT_LLM_API_KEY")

    async def complete(self, system: str, user: str) -> str:
        base = settings.llm_base_url.rstrip("/")
        payload = {
            "model": settings.llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.7,
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{base}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.llm_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        return data["choices"][0]["message"]["content"]
