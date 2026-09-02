"""OpenAI 兼容视觉模型适配器（DashScope compatible-mode / vLLM 本地 Qwen-VL 等）。

抽帧一律以 base64 `image_url` 部件上传：DashScope 的 OpenAI 兼容端点不接受
`video_url`，本地 vLLM 也不接，图片序列是两边都通的唯一路子。
temperature=0：审核要可复现，同一份素材两次跑出的分必须一致。
"""

import base64
import json
from pathlib import Path

import httpx

from ..config import settings
from .base import extract_json
from .video_understanding import (
    COMPARE_SYSTEM,
    DESCRIBE_SYSTEM,
    ChecklistAnswer,
    CompareVerdict,
    TimelineNote,
    VideoFrame,
    VideoInsight,
    VideoUnderstandingRequest,
    build_compare_user,
    build_describe_user,
)

PARSE_ATTEMPTS = 2


def _image_part(frame: VideoFrame) -> dict:
    data = base64.b64encode(Path(frame.path).read_bytes()).decode()
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/jpeg;base64,{data}"},
    }


def _text_part(text: str) -> dict:
    return {"type": "text", "text": text}


class OpenAICompatibleVideoModel:
    name = "openai-compat-vlm"

    def __init__(self) -> None:
        if not settings.vlm_api_key:
            raise RuntimeError("API Key 缺失：请设置 FILMAGENT_VLM_API_KEY")

    async def describe(self, request: VideoUnderstandingRequest) -> VideoInsight:
        content = [_text_part(build_describe_user(request))]
        content += [_image_part(frame) for frame in request.frames]
        data = await self._chat(DESCRIBE_SYSTEM, content)

        checklist: list[ChecklistAnswer] = []
        for item in data.get("checklist") or []:
            if not isinstance(item, dict):
                continue
            try:
                score = int(item.get("score", 0))
            except (TypeError, ValueError):
                score = 0
            checklist.append(
                ChecklistAnswer(
                    key=str(item.get("key") or ""),
                    score=score,
                    note=str(item.get("note") or ""),
                )
            )

        timeline: list[TimelineNote] = []
        for item in data.get("timeline") or []:
            if not isinstance(item, dict):
                continue
            try:
                moment = float(item.get("t", 0.0))
            except (TypeError, ValueError):
                moment = 0.0
            timeline.append(TimelineNote(t=moment, text=str(item.get("text") or "")))

        return VideoInsight(
            model=str(data.get("model") or settings.vlm_model),
            frame_count=len(request.frames),
            observation=str(data.get("observation") or ""),
            narrative_intent=str(data.get("narrative_intent") or ""),
            continuity=str(data.get("continuity") or ""),
            timeline=timeline,
            checklist=[c for c in checklist if c.key],
            issues=[str(i) for i in (data.get("issues") or [])][:12],
            suggested_prompt_changes=[
                str(i) for i in (data.get("suggested_prompt_changes") or [])
            ][:6],
            raw=json.dumps(data, ensure_ascii=False)[:4000],
        )

    async def compare(
        self,
        shot_title: str,
        prompt: str,
        a: list[VideoFrame],
        b: list[VideoFrame],
    ) -> CompareVerdict:
        content = [_text_part(build_compare_user(shot_title, prompt, a, b))]
        content.append(_text_part("以下为 A 组抽帧。"))
        for frame in a:
            content.append(_image_part(frame))
        content.append(_text_part("以上为 A 组，以下为 B 组。"))
        for frame in b:
            content.append(_image_part(frame))
        data = await self._chat(COMPARE_SYSTEM, content)
        winner = str(data.get("winner") or "TIE").strip().upper()
        try:
            confidence = int(data.get("confidence", 50))
        except (TypeError, ValueError):
            confidence = 50
        return CompareVerdict(
            winner=winner if winner in {"A", "B", "TIE"} else "TIE",
            reason=str(data.get("reason") or ""),
            confidence=max(0, min(100, confidence)),
        )

    async def _chat(self, system: str, content: list[dict]) -> dict:
        payload: dict = {
            "model": settings.vlm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            "temperature": 0,
            "max_tokens": settings.vlm_max_tokens,
        }
        # repetition_penalty 是 vLLM 扩展参数，云端兼容端点会直接 400，只在本地量化模型上开
        if settings.vlm_repetition_penalty:
            payload["repetition_penalty"] = settings.vlm_repetition_penalty

        base = settings.vlm_base_url.rstrip("/")
        last_error = "模型输出解析失败"
        for attempt in range(PARSE_ATTEMPTS):
            async with httpx.AsyncClient(timeout=settings.vlm_timeout) as client:
                resp = await client.post(
                    f"{base}/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.vlm_api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                resp.raise_for_status()
                body = resp.json()
            text = body["choices"][0]["message"]["content"]
            try:
                data = extract_json(text)
            except ValueError as e:
                last_error = f"{e}；原始回复：{text[:200]}"
                continue
            data.setdefault("model", body.get("model") or settings.vlm_model)
            return data
        raise RuntimeError(f"视觉模型输出解析失败: {last_error}")


__all__ = ["OpenAICompatibleVideoModel"]
