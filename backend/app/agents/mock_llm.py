"""无 API Key 的本地文本模型：模板式生成结构化中文内容。

仅用于离线演示与测试；通过 llm_backend=openai 切换真实模型。
"""

import hashlib
import json
import re

FRAMINGS = ["WIDE", "MEDIUM", "CLOSE_UP", "EXTREME_WIDE"]
CAMERAS = ["STATIC", "SLOW_PUSH_IN", "PAN_LEFT", "TILT_UP", "HANDHELD"]

SCENE_ARCS = [
    ("开端", "establishing", "把观众带入{idea}的世界，建立时间与空间感"),
    ("发展", "rising", "冲突逐渐展开，{idea}的核心张力浮现"),
    ("高潮", "climax", "情绪与视觉同时到达顶点"),
]


class MockTextModel:
    name = "mock-text"

    async def complete(self, system: str, user: str) -> str:
        # 按特异关键词优先匹配：多个系统提示词都含「剧本」「导演」这类泛词
        if "审核" in system or "review" in system:
            return _take_review(user)
        if "提示词工程师" in system:
            return _prompt(user)
        if "分镜候选" in system:
            return json.dumps({"storyboard_id": ""}, ensure_ascii=False)
        if "角色设计师" in system:
            return _character_cards(user)
        if "美术指导" in system:
            return _location_cards(user)
        if "导演圣经" in system:
            return _director_bible(user)
        if "制片" in system or "生产计划" in system:
            return _production_plan(user)
        if "剧本" in system or "screenplay" in system:
            return _screenplay(user)
        return '{"text": "mock"}'


def _seed_of(text: str) -> int:
    return int(hashlib.md5(text.encode()).hexdigest()[:8], 16)


def _screenplay(user: str) -> str:
    idea = user.replace("一句话创意：", "").strip()[:60] or "一个关于时间与记忆的短片"
    cast = _cast_names(idea)
    scenes = []
    for i, (act, _, goal) in enumerate(SCENE_ARCS):
        shots = []
        for j in range(2):
            idx = i * 2 + j
            who = "、".join(cast) if i >= 1 else cast[0]
            shots.append(
                {
                    "title": f"{act}{j + 1}",
                    "description": f"{who}在{act}阶段的第{j + 1}个镜头：{idea}，延续整体视觉基调",
                    "framing": FRAMINGS[idx % len(FRAMINGS)],
                    "camera_motion": CAMERAS[idx % len(CAMERAS)],
                    "duration": 3.0,
                }
            )
        scenes.append(
            {
                "title": act,
                "description": goal.format(idea=idea),
                "dramatic_goal": goal.format(idea=idea),
                "shots": shots,
            }
        )
    return json.dumps(
        {
            "logline": f"{idea}：一段从静到动、由暗转明的三幕旅程。",
            "scenes": scenes,
        },
        ensure_ascii=False,
    )


def _director_bible(user: str) -> str:
    return json.dumps(
        {
            "visual_style": "低饱和电影感，柔和自然光，浅景深",
            "color_rules": ["暖色高光、冷色阴影", "每幕主色随情绪推进逐渐转向"],
            "camera_rules": ["开场用大景别建立空间", "高潮用特写与手持增强张力"],
            "performance_rules": ["表演克制，情绪靠动作与光线传达"],
            "lighting_rules": ["黄金时刻为主", "高潮允许高对比硬光"],
            "editing_rules": ["节奏前缓后急", "转场以叠化与匹配剪辑为主"],
        },
        ensure_ascii=False,
    )


def _production_plan(user: str) -> str:
    return json.dumps(
        {
            "batch_strategy": "按场景分批，每批 3~5 个 Shot（规格 §24）",
            "default_take_count": 2,
            "shot_priorities": {"climax": "CRITICAL", "establishing": "HIGH"},
        },
        ensure_ascii=False,
    )


CHARACTER_PRESETS = [
    {
        "name": "林澈",
        "gender": "男",
        "age_range": "25-30",
        "role": "PRIMARY",
        "appearance": "瘦削身形，黑色短发微乱，眉眼沉静，左眉尾有一道细小疤痕",
        "costume": "深灰色连帽外套、黑色长裤、磨旧的帆布鞋",
        "personality": "克制、专注，情绪藏在动作里",
        "visual_anchors": ["黑色短发", "左眉尾细小疤痕", "深灰色连帽外套", "瘦削身形"],
        "immutable_traits": ["左眉尾的细小疤痕", "黑色短发", "深灰色连帽外套"],
    },
    {
        "name": "苏晚",
        "gender": "女",
        "age_range": "25-30",
        "role": "SUPPORTING",
        "appearance": "及肩黑发，身形挺拔，眼神锐利，右手腕戴一枚银色旧手表",
        "costume": "米白色长风衣、深蓝羊毛围巾、黑色皮靴",
        "personality": "冷静、直接，习惯先观察再开口",
        "visual_anchors": ["及肩黑发", "米白色长风衣", "右手腕银色旧手表", "深蓝围巾"],
        "immutable_traits": ["右手腕的银色旧手表", "及肩黑发", "米白色长风衣"],
    },
]

LOCATION_NAMES = ["旧天文台观测室", "雨夜便利店门口", "海边废弃灯塔"]

LOCATION_PRESETS = [
    {
        "visual_style": "锈蚀金属与斑驳混凝土，冷调低饱和，高窗光束切开尘埃",
        "time_of_day_default": "夜晚",
        "materials": ["混凝土", "生锈金属", "厚玻璃"],
        "colors": ["青灰", "暗橙", "冷白"],
        "visual_cues": ["高窗光束", "墙面手绘星图", "铁制旋转楼梯"],
        "immutable_elements": ["中央悬挂的巨型望远镜", "墙面手绘星图", "铁制旋转楼梯"],
        "lighting_rules": ["单侧高窗自然光为主", "夜间以暖色实用灯具点缀"],
    },
    {
        "visual_style": "湿润霓虹反射的都市夜色，浅景深，雨丝在逆光下可见",
        "time_of_day_default": "夜晚",
        "materials": ["玻璃", "湿沥青", "塑料灯箱"],
        "colors": ["霓虹粉", "电光蓝", "湿黑"],
        "visual_cues": ["便利店灯箱招牌", "门口自动贩卖机", "路边红色消防栓"],
        "immutable_elements": ["便利店灯箱招牌", "门口自动贩卖机", "路边红色消防栓"],
        "lighting_rules": ["霓虹与路灯混合光源", "人物面部保留一处冷色补光"],
    },
    {
        "visual_style": "海风侵蚀的白石与铁栏，黄昏逆光，长曝光般的平静海面",
        "time_of_day_default": "黄昏",
        "materials": ["白石", "生锈铁栏", "原木楼梯"],
        "colors": ["灰白", "铁锈红", "暮蓝"],
        "visual_cues": ["旋转灯室", "断裂石阶扶手", "墙面航海图残片"],
        "immutable_elements": ["灯塔顶部的旋转灯室", "断裂的石阶扶手", "墙面航海图残片"],
        "lighting_rules": ["黄昏逆光为主", "室内只点一盏暖黄灯"],
    },
]


def _payload_json(user: str) -> dict:
    """从「剧本：{...}」形式的消息里稳健取出内嵌 JSON。"""
    start = user.find("{")
    if start == -1:
        return {}
    try:
        data, _ = json.JSONDecoder().raw_decode(user[start:])
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _cast_names(text: str) -> list[str]:
    """创意里点名的角色优先，否则退回第一个预设（保证镜头描述里有可匹配的人名）。"""
    named = [p["name"] for p in CHARACTER_PRESETS if p["name"] in text]
    return named or [CHARACTER_PRESETS[0]["name"]]


def _character_cards(user: str) -> str:
    seed = _seed_of(user)
    topic = str(_payload_json(user).get("logline", ""))[:24] or "这个故事"
    named = [p for p in CHARACTER_PRESETS if p["name"] in user]
    if named:
        presets = named[:3]
    else:
        presets = [
            dict(CHARACTER_PRESETS[i % len(CHARACTER_PRESETS)])
            for i in range(1 + seed % 2)
        ]
    if not any(p.get("role") == "PRIMARY" for p in presets):
        presets[0] = {**presets[0], "role": "PRIMARY"}
    characters = []
    for preset in presets:
        card = dict(preset)
        card["description"] = f"{topic}的核心人物，承担主要情绪线"
        characters.append(card)
    return json.dumps({"characters": characters}, ensure_ascii=False)


def _location_cards(user: str) -> str:
    scenes = _payload_json(user).get("scenes") or []
    locations = []
    for i, scene in enumerate(scenes[:3]):
        if not isinstance(scene, dict):
            continue
        title = str(scene.get("title") or f"场景{i + 1}")
        locations.append(
            {
                "name": LOCATION_NAMES[i % len(LOCATION_NAMES)],
                "scene_title": title,
                "description": str(scene.get("description") or "")[:80],
                **LOCATION_PRESETS[i % len(LOCATION_PRESETS)],
            }
        )
    return json.dumps({"locations": locations}, ensure_ascii=False)


def _take_review(user: str) -> str:
    seed = _seed_of(user)
    score = 70 + seed % 26
    decision = "KEEP" if score >= 78 else ("RETAKE" if seed % 3 else "REJECT")
    return json.dumps(
        {
            "decision": decision,
            "scores": {
                "prompt_alignment": score,
                "character_consistency": max(score - 3, 0),
                "temporal_consistency": max(score - 6, 0),
                "motion_quality": max(score - 2, 0),
                "composition": score,
                "narrative_intent": max(score - 4, 0),
                "continuity": max(score - 5, 0),
            },
            "issues": [],
            "suggested_prompt_changes": []
            if decision == "KEEP"
            else ["加强主体与提示词的一致性描述"],
            "explanation": f"整体质量评分 {score}，{ '符合导演意图' if decision == 'KEEP' else '未达到入选标准' }",
        },
        ensure_ascii=False,
    )


PROMPT_SECTION_ORDER = ["风格", "角色", "场景", "镜头", "导演圣经"]
PROMPT_MAX = 400


def _split_sections(text: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current: str | None = None
    for line in text.splitlines():
        head = re.match(r"^【(.+?)】(.*)$", line.strip())
        if head:
            current = head.group(1)
            sections[current] = head.group(2).strip()
        elif current is not None and line.strip():
            sections[current] = f"{sections[current]}\n{line.strip()}"
    return sections


def _prompt(user: str) -> str:
    """按 PROMPT_SYSTEM 的压缩优先级出提示词：先丢剧情场景，再压镜头描述，锚点永不删。"""
    sections = _split_sections(user)
    if not sections:
        return json.dumps(
            {"prompt": user.strip()[:PROMPT_MAX] or "cinematic still, soft light"},
            ensure_ascii=False,
        )

    def render(shot_body: str) -> str:
        parts = []
        for key in PROMPT_SECTION_ORDER:
            body = sections.get(key)
            if body is None:
                continue
            if key == "镜头":
                body = shot_body
            parts.append(f"【{key}】{body}")
        return "\n".join(parts)

    shot = sections.get("镜头", "")
    prompt = render(shot)
    while len(prompt) > PROMPT_MAX and len(shot) > 20:
        shot = shot[: len(shot) - 16].rstrip("；，。 ") + "…"
        prompt = render(shot)
    return json.dumps({"prompt": prompt}, ensure_ascii=False)
