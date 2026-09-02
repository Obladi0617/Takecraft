"""六个 Agent：Producer / Writer / Director / Prompt / Reviewer / Editor。

全部只依赖 TextModel 接口（规格 Model Agnostic），输出结构化 JSON；
DB 落库由调用方（Graph 节点 / API）负责。
"""

import asyncio
import json

from .base import TextModel, extract_json

SCREENPLAY_SYSTEM = """你是资深短片编剧。根据一句话创意输出剧本 JSON，结构：
{"logline": "...", "scenes": [{"title": "...", "description": "...", "dramatic_goal": "...",
"shots": [{"title": "...", "description": "...", "framing": "WIDE|MEDIUM|CLOSE_UP|EXTREME_WIDE",
"camera_motion": "STATIC|SLOW_PUSH_IN|PAN_LEFT|TILT_UP|HANDHELD", "duration": 3.0}]}]}
要求：2~3 个场景，每场 2~3 个镜头，duration 2~5 秒，中文。只输出 JSON。"""

BIBLE_SYSTEM = """你是导演。基于剧本输出导演圣经 JSON，结构：
{"visual_style": "...", "color_rules": ["..."], "camera_rules": ["..."],
"performance_rules": ["..."], "lighting_rules": ["..."], "editing_rules": ["..."]}
规则各 2~3 条，中文。只输出 JSON。"""

PLAN_SYSTEM = """你是制片人。基于剧本输出生产计划 JSON，结构：
{"batch_strategy": "...", "default_take_count": 2, "shot_priorities": {"<镜头标题>": "CRITICAL|HIGH|NORMAL|LOW"}}
default_take_count 取 1~4。中文。只输出 JSON。"""

PROMPT_SYSTEM = """你是提示词工程师。把镜头描述与导演圣经融合成图像生成提示词，输出：
{"prompt": "..."}（中文，120 字以内，包含画面主体、构图、光线、色调）。只输出 JSON。"""

SELECT_SB_SYSTEM = """你是导演。从分镜候选中选出最符合导演意图的一张，输出：
{"storyboard_id": "<候选id>"}。只输出 JSON。"""

REVIEW_SYSTEM = """你是 AI Dailies 审核员，对生成的 Take 质量初筛（不做最终艺术裁决）。输出：
{"decision": "KEEP|RETAKE|REJECT", "scores": {"prompt_alignment": 0-100, "character_consistency": 0-100,
"temporal_consistency": 0-100, "motion_quality": 0-100, "composition": 0-100,
"narrative_intent": 0-100, "continuity": 0-100}, "issues": [], "suggested_prompt_changes": ["..."], "explanation": "..."}
中文。只输出 JSON。"""


async def _ask_json(model: TextModel, system: str, user: str) -> dict:
    for attempt in range(2):
        try:
            return extract_json(await model.complete(system, user))
        except (ValueError, SyntaxError) as e:
            if attempt == 1:
                raise RuntimeError(f"模型输出解析失败: {e}") from e
            await asyncio.sleep(0.2)
    raise RuntimeError("unreachable")


async def writer_draft(model: TextModel, idea: str) -> dict:
    return await _ask_json(model, SCREENPLAY_SYSTEM, f"一句话创意：{idea}")


async def director_bible(model: TextModel, idea: str, screenplay: dict) -> str:
    data = await _ask_json(
        model,
        BIBLE_SYSTEM,
        f"创意：{idea}\n剧本：{json.dumps(screenplay, ensure_ascii=False)}",
    )
    return data


async def producer_plan(model: TextModel, idea: str, screenplay: dict) -> dict:
    return await _ask_json(
        model,
        PLAN_SYSTEM,
        f"创意：{idea}\n剧本：{json.dumps(screenplay, ensure_ascii=False)}",
    )


async def prompt_storyboard(
    model: TextModel, shot_desc: str, scene_desc: str, bible: dict
) -> str:
    data = await _ask_json(
        model,
        PROMPT_SYSTEM,
        f"镜头：{shot_desc}\n场景：{scene_desc}\n导演圣经：{json.dumps(bible, ensure_ascii=False)}",
    )
    return str(data.get("prompt", shot_desc))


async def select_storyboard(
    model: TextModel, shot_title: str, bible: dict, candidates: list[dict]
) -> str:
    listing = "\n".join(
        f"- {c['id']}：{c.get('prompt', '')[:80]}" for c in candidates
    )
    try:
        data = await _ask_json(
            model,
            SELECT_SB_SYSTEM,
            f"镜头：{shot_title}\n导演圣经：{json.dumps(bible, ensure_ascii=False)}\n候选：\n{listing}",
        )
        chosen = data.get("storyboard_id")
        if chosen in {c["id"] for c in candidates}:
            return str(chosen)
    except RuntimeError:
        pass
    return str(candidates[0]["id"])


async def review_take(
    model: TextModel, shot_title: str, prompt: str, take_id: str
) -> dict:
    data = await _ask_json(
        model,
        REVIEW_SYSTEM,
        f"镜头：{shot_title}\n提示词：{prompt}\nTake：{take_id}",
    )
    data.setdefault("decision", "KEEP")
    data.setdefault("scores", {})
    data.setdefault("issues", [])
    data.setdefault("explanation", "")
    return data
