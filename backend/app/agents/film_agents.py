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

CHARACTER_SYSTEM = """你是角色设计师。从剧本中提取主要角色并输出角色卡 JSON，结构：
{"characters": [{"name": "...", "gender": "男|女|其他", "age_range": "...", "role": "PRIMARY|SUPPORTING",
"description": "...", "appearance": "...", "costume": "...", "personality": "...",
"visual_anchors": ["...", "..."], "immutable_traits": ["...", "..."]}]}
要求：最多 3 个主要角色，其中至少 1 个 role=PRIMARY；appearance 与 costume 必须是可直接用于图像生成的
具体视觉描述（发型发色、体型、服装颜色与材质）；visual_anchors 是 3~5 条原子化视觉锚点
（例：黑色短发、左眉尾疤痕、深灰色连帽外套），短词组优于长句；immutable_traits 为跨镜头不可变的
识别锚点 2~4 条；严格区分不同角色的特征，不得把 A 角色的服装或外貌分配给 B 角色；中文。只输出 JSON。"""

LOCATION_SYSTEM = """你是美术指导。从剧本中提取主要场景并输出场景资产 JSON，结构：
{"locations": [{"name": "...", "scene_title": "<剧本中对应的场景标题>", "description": "...",
"visual_style": "...", "time_of_day_default": "清晨|白天|黄昏|夜晚",
"materials": ["..."], "colors": ["..."], "visual_cues": ["..."],
"immutable_elements": ["..."], "lighting_rules": ["..."]}]}
要求：最多 3 个主要场景；scene_title 必须与剧本里的场景标题一致；colors 用具体色名；
visual_cues 是 2~4 条具体可视锚点（例：红色招牌、绿色长椅）；immutable_elements 为跨镜头必须
保持一致的固定元素 2~4 条；中文。只输出 JSON。"""

PROMPT_SYSTEM = """你是提示词工程师。把镜头描述、已锁定的角色/场景资产与导演圣经融合成图像生成提示词，输出：
{"prompt": "..."}（中文，400 字以内，包含画面主体、构图、光线、色调）。
硬性要求：
1. 角色「不可变特征」与场景「固定元素」必须原样出现在提示词中，不得改写、翻译或省略；
2. 字数超限时只能压缩【剧情场景】与【镜头】里的动作描述，锚点段落永不删减；
3. 保留输入的分节顺序（风格→角色→场景→镜头→导演圣经）与各分节标题。只输出 JSON。"""

SELECT_SB_SYSTEM = """你是导演。从分镜候选中选出最符合导演意图的一张，输出：
{"storyboard_id": "<候选id>"}。只输出 JSON。"""

REVIEW_SYSTEM = """你是 AI Dailies 审核员，对生成的 Take 质量初筛（不做最终艺术裁决）。输出：
{"decision": "KEEP|RETAKE|REJECT", "scores": {"prompt_alignment": 0-100, "character_consistency": 0-100,
"temporal_consistency": 0-100, "motion_quality": 0-100, "composition": 0-100,
"narrative_intent": 0-100, "continuity": 0-100}, "issues": [], "suggested_prompt_changes": ["..."], "explanation": "..."}
中文。只输出 JSON。"""


async def _ask_json(
    model: TextModel, system: str, user: str, required: tuple[str, ...] = ()
) -> dict:
    last_error: Exception = RuntimeError("未知 JSON 错误")
    last_text = ""
    for attempt in range(3):
        retry_system = system
        retry_user = user
        if attempt:
            retry_system += (
                "\n上次输出不是完整合法 JSON。本次只输出紧凑的单行 JSON，"
                "不要 Markdown、注释或尾随文本。"
            )
            retry_user += f"\n上次解析错误：{last_error}"
        try:
            last_text = await model.complete(retry_system, retry_user)
            result = extract_json(last_text)
            missing = [
                key for key in required if not result.get(key)
            ]
            if missing:
                raise ValueError(f"JSON 缺少必填字段或字段为空: {', '.join(missing)}")
            return result
        except (ValueError, SyntaxError) as exc:
            last_error = exc
            if attempt == 2:
                preview = last_text[:300].replace("\n", " ")
                raise RuntimeError(
                    f"模型输出解析失败: {exc}；回复开头: {preview}"
                ) from exc
            await asyncio.sleep(0.2 * (attempt + 1))
    raise RuntimeError("unreachable")


async def writer_draft(model: TextModel, idea: str) -> dict:
    return await _ask_json(
        model, SCREENPLAY_SYSTEM, f"一句话创意：{idea}", ("logline", "scenes")
    )


async def director_bible(model: TextModel, idea: str, screenplay: dict) -> str:
    data = await _ask_json(
        model,
        BIBLE_SYSTEM,
        f"创意：{idea}\n剧本：{json.dumps(screenplay, ensure_ascii=False)}",
        ("visual_style", "color_rules"),
    )
    return data


async def producer_plan(model: TextModel, idea: str, screenplay: dict) -> dict:
    return await _ask_json(
        model,
        PLAN_SYSTEM,
        f"创意：{idea}\n剧本：{json.dumps(screenplay, ensure_ascii=False)}",
        ("default_take_count",),
    )


async def character_cards(model: TextModel, screenplay: dict) -> list[dict]:
    data = await _ask_json(
        model,
        CHARACTER_SYSTEM,
        f"剧本：{json.dumps(screenplay, ensure_ascii=False)}",
        ("characters",),
    )
    cards = data.get("characters")
    return [c for c in cards if isinstance(c, dict)] if isinstance(cards, list) else []


async def location_cards(model: TextModel, screenplay: dict) -> list[dict]:
    data = await _ask_json(
        model,
        LOCATION_SYSTEM,
        f"剧本：{json.dumps(screenplay, ensure_ascii=False)}",
        ("locations",),
    )
    cards = data.get("locations")
    return [c for c in cards if isinstance(c, dict)] if isinstance(cards, list) else []


async def prompt_storyboard(
    model: TextModel,
    shot_spec: dict,
    scene_desc: str,
    bible: dict,
    characters: list[str] | None = None,
    location: str = "",
) -> str:
    """组装顺序（风格→角色→场景→镜头→导演圣经）来自真实项目模板，不可随意调整。"""
    shot_desc = str(shot_spec.get("description") or shot_spec.get("title") or "")
    blocks = [f"【风格】{bible.get('visual_style', '')}"]
    if characters:
        blocks.append(
            "【角色】以下锚点逐字保留，不得改写：\n" + "\n".join(f"- {c}" for c in characters)
        )
    if location:
        blocks.append(f"【场景】{location}")
    if scene_desc:
        blocks.append(f"【剧情场景】{scene_desc}")
    blocks.append(
        "【镜头】"
        + f"{shot_desc}；景别 {shot_spec.get('framing', 'MEDIUM')}"
        + f"；运镜 {shot_spec.get('camera_motion', 'STATIC')}"
        + f"；时长 {shot_spec.get('duration_target', 3.0)}s"
    )
    blocks.append(
        "【导演圣经】色调 "
        + "、".join(bible.get("color_rules", []) or [])
        + "；光线 "
        + "、".join(bible.get("lighting_rules", []) or [])
    )
    data = await _ask_json(
        model, PROMPT_SYSTEM, "\n".join(blocks), ("prompt",)
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
            ("storyboard_id",),
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
        ("decision", "scores"),
    )
    data.setdefault("decision", "KEEP")
    data.setdefault("scores", {})
    data.setdefault("issues", [])
    data.setdefault("explanation", "")
    return data
