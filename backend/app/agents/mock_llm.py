"""无 API Key 的本地文本模型：模板式生成结构化中文内容。

仅用于离线演示与测试；通过 llm_backend=openai 切换真实模型。
"""

import hashlib
import json

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
    idea = user.strip()[:60] or "一个关于时间与记忆的短片"
    scenes = []
    for i, (act, _, goal) in enumerate(SCENE_ARCS):
        shots = []
        for j in range(2):
            idx = i * 2 + j
            shots.append(
                {
                    "title": f"{act}{j + 1}",
                    "description": f"{idea}——{act}阶段的第{j + 1}个镜头，延续整体视觉基调",
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


def _prompt(user: str) -> str:
    return json.dumps(
        {"prompt": user.strip()[:200] or "cinematic still, soft light"},
        ensure_ascii=False,
    )
