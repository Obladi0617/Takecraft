"""OpenAI 兼容文本模型适配器（ModelScope API-Inference / MiniMax / Ollama 等）。"""

import asyncio
import httpx

from ..config import settings


class OpenAICompatibleTextModel:
    name = "openai-compat"

    def __init__(self) -> None:
        if not settings.llm_api_key:
            raise RuntimeError("API Key 缺失：请设置 FILMAGENT_LLM_API_KEY")

    @property
    def _base(self) -> str:
        return settings.llm_base_url.rstrip("/")

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {settings.llm_api_key}",
            "Content-Type": "application/json",
        }

    async def health(self) -> dict:
        """探测模型列表，不消耗生成 token。"""
        try:
            async with httpx.AsyncClient(
                timeout=min(settings.llm_timeout, 15.0)
            ) as client:
                response = await client.get(
                    f"{self._base}/v1/models", headers=self._headers
                )
                response.raise_for_status()
                data = response.json().get("data", [])
            model_ids = [
                str(item.get("id")) for item in data if isinstance(item, dict)
            ]
            return {
                "ok": True,
                "backend": self.name,
                "model": settings.llm_model,
                "base_url": self._base,
                "model_available": not model_ids or settings.llm_model in model_ids,
                "available_models": model_ids[:20],
            }
        except (httpx.HTTPError, ValueError) as exc:
            return {
                "ok": False,
                "backend": self.name,
                "model": settings.llm_model,
                "base_url": self._base,
                "error": f"{type(exc).__name__}: {exc}",
            }

    async def complete(self, system: str, user: str) -> str:
        payload = {
            "model": settings.llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": settings.llm_temperature,
        }
        if settings.llm_max_tokens > 0:
            payload["max_tokens"] = settings.llm_max_tokens
        if settings.llm_reasoning_effort:
            payload["reasoning_effort"] = settings.llm_reasoning_effort
        if settings.llm_json_mode:
            payload["response_format"] = {"type": "json_object"}
        attempts = max(1, settings.llm_max_retries)
        last_error: Exception = RuntimeError("未知错误")
        async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
            for attempt in range(attempts):
                try:
                    response = await client.post(
                        f"{self._base}/v1/chat/completions",
                        headers=self._headers,
                        json=payload,
                    )
                    response.raise_for_status()
                    data = response.json()
                    content = data["choices"][0]["message"]["content"]
                    if not isinstance(content, str) or not content.strip():
                        raise ValueError("模型返回了空内容")
                    return content
                except httpx.TransportError as exc:
                    retryable = True
                    last_error = exc
                except httpx.HTTPStatusError as exc:
                    retryable = (
                        exc.response.status_code == 429
                        or exc.response.status_code >= 500
                    )
                    last_error = exc
                except (KeyError, IndexError, TypeError, ValueError) as exc:
                    retryable = True
                    last_error = RuntimeError(f"文本模型返回格式错误: {exc}")
                if not retryable or attempt == attempts - 1:
                    break
                await asyncio.sleep(settings.llm_retry_base_delay * (2**attempt))
        raise RuntimeError(
            f"文本模型调用失败（已尝试 {attempts} 次）: "
            f"{type(last_error).__name__}: {last_error}"
        ) from last_error
