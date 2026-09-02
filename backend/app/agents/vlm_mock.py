"""离线视觉理解模型：不调用任何外部服务，按抽帧内容摘要确定性合成分项评分。

只用于无 Key 演示与回归测试。分数分布刻意保留约三成 RETAKE，
否则自动重拍闭环在离线演示里永远不会触发，评审看不到这一段。
"""

import hashlib

from .video_understanding import (
    MODEL_CHECKLIST,
    ChecklistAnswer,
    CompareVerdict,
    TimelineNote,
    VideoFrame,
    VideoInsight,
    VideoUnderstandingRequest,
)

BASE_SCORE = {
    "prompt_alignment": 66,
    "character_consistency": 70,
    "temporal_consistency": 74,
    "composition": 70,
    "motion_quality": 68,
    "narrative_intent": 66,
    "continuity": 78,
}

FLAW_NOTES = {
    "prompt_alignment": "画面主体与提示词描述不符，关键元素缺失或位置错乱",
    "character_consistency": "角色外观与锁定的不可变锚点不一致，服饰/发型出现漂移",
    "temporal_consistency": "帧间主体发生跳变，出现多余肢体或物体扭曲",
    "composition": "景别与镜头设定不符，主体被裁切或画面重心偏移",
    "motion_quality": "运动不自然，主体漂浮、动作卡顿或与运镜设定相反",
    "narrative_intent": "画面情绪与该镜头的剧情意图脱节",
    "continuity": "光线与色调和整体视觉风格不连贯",
}

FLAW_FIXES = {
    "prompt_alignment": "在提示词中前置主体与动作，删除与画面无关的修饰",
    "character_consistency": "把角色不可变锚点逐字重复一次，并挂上三视图参考图",
    "temporal_consistency": "缩短单镜时长或降低运镜幅度，减少帧间跳变",
    "composition": "在提示词中显式写明景别与主体位置",
    "motion_quality": "明确运镜方式与速度，避免与主体动作冲突",
    "narrative_intent": "补充该镜头在剧情中的情绪目标",
    "continuity": "把导演圣经的色调与光线规则原样带入提示词",
}

# severity 分档：0~2 严重缺陷（低于分项下限，必判 RETAKE）、3~6 轻微缺陷、7~9 干净
HARSH_MAX = 3
MILD_MAX = 7


def _seed_of(*parts: str) -> int:
    return int(hashlib.md5("|".join(parts).encode()).hexdigest()[:8], 16)


class MockVideoUnderstandingModel:
    name = "mock-vlm"

    async def describe(self, request: VideoUnderstandingRequest) -> VideoInsight:
        seed = _seed_of(
            request.take_id, *(f.digest or f.path for f in request.frames)
        )
        severity = seed % 10
        keys = [spec.key for spec in MODEL_CHECKLIST]
        flaw_key = keys[(seed >> 4) % len(keys)] if severity < MILD_MAX else None
        if flaw_key is None:
            flaw_score = 0
        elif severity < HARSH_MAX:
            flaw_score = 12 + (seed >> 7) % 24  # 12~35，必低于 review_item_floor
        else:
            flaw_score = 45 + (seed >> 7) % 18  # 45~62，扣分但不否决

        checklist: list[ChecklistAnswer] = []
        issues: list[str] = []
        fixes: list[str] = []
        for position, spec in enumerate(MODEL_CHECKLIST):
            if spec.key == flaw_key:
                score = flaw_score
                note = FLAW_NOTES[spec.key]
                issues.append(f"{spec.label}：{note}")
                fixes.append(FLAW_FIXES[spec.key])
            else:
                score = min(
                    100, BASE_SCORE[spec.key] + (seed >> (3 * position + 1)) % 26
                )
                note = "符合镜头设定，未见明显问题"
            checklist.append(ChecklistAnswer(key=spec.key, score=score, note=note))

        return VideoInsight(
            model=self.name,
            frame_count=len(request.frames),
            observation=(
                f"送审 {len(request.frames)} 帧，{request.cv_summary}。"
                + (issues[0] if issues else "画面整体连贯，未见结构性缺陷。")
            ),
            narrative_intent=(
                "画面情绪与镜头意图一致" if not issues else "存在偏离镜头意图的缺陷，见 issues"
            ),
            continuity="光线与色调延续整体视觉风格",
            timeline=_timeline(request.frames),
            checklist=checklist,
            issues=issues,
            suggested_prompt_changes=fixes,
            raw=f"mock seed={seed} severity={severity}",
        )

    async def compare(
        self,
        shot_title: str,
        prompt: str,
        a: list[VideoFrame],
        b: list[VideoFrame],
    ) -> CompareVerdict:
        variety_a = len({f.digest for f in a if f.digest})
        variety_b = len({f.digest for f in b if f.digest})
        if variety_a != variety_b:
            winner = "A" if variety_a > variety_b else "B"
            reason = f"抽帧内容差异更多的一组运动更丰富（A {variety_a} / B {variety_b}）"
            return CompareVerdict(winner=winner, reason=reason, confidence=62)
        seed = _seed_of(shot_title, prompt, *(f.digest for f in a + b))
        return CompareVerdict(
            winner="A" if seed % 2 else "B",
            reason="两组抽帧差异相当，按帧序稳定度择优（mock 判定）",
            confidence=52,
        )


def _timeline(frames: list[VideoFrame]) -> list[TimelineNote]:
    if not frames:
        return []
    static = len({f.digest for f in frames if f.digest}) <= 1
    picked = [frames[0], frames[len(frames) // 2], frames[-1]]
    seen: set[int] = set()
    notes: list[TimelineNote] = []
    for frame in picked:
        if frame.index in seen:
            continue
        seen.add(frame.index)
        notes.append(
            TimelineNote(
                t=round(frame.time, 2),
                text=(
                    "构图与前帧无可见差异（送审分辨率下素材近乎静止）"
                    if static
                    else "画面延续同一构图，主体位置随时间推进"
                ),
            )
        )
    return notes


__all__ = ["MockVideoUnderstandingModel"]
