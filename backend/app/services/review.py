"""AI Dailies 审核打分：ffmpeg 确定性分项 + 视觉模型分项 → 应用侧加权与判定。

总分与 KEEP/RETAKE 全在这里算，模型只提供分项——权重可调、可解释、可回归，
也避免「模型自己给自己打总分」导致的不可比较（不同 Take 的分不在同一把尺子上）。
"""

import json
from pathlib import Path

from pydantic import ValidationError
from sqlmodel import Session

from ..agents.video_understanding import (
    CHECKLIST_BY_KEY,
    MODEL_CHECKLIST,
    VideoFrame,
    VideoInsight,
    VideoUnderstandingRequest,
)
from ..config import settings
from ..db import abs_path, project_path
from ..domain import Character, Location, Shot, Take
from ..media.frames import (
    GateFailure,
    VideoStats,
    frame_stats,
    gate_failures,
    sample_frames,
)

REVIEWS_SUBDIR = "reviews"
# 模型没答出来的分项按此分计入，并记一条 issue：宁可拉低总分也不要静默满分
MISSING_SCORE = 50


def _band(value: float, bands: list[tuple[float, int]]) -> int:
    """按阈值表打分：bands 为 [(上限, 分数), ...]，从小到大。"""
    for limit, score in bands:
        if value <= limit:
            return score
    return bands[-1][1]


def cv_scores(stats: VideoStats) -> dict[str, int]:
    """确定性技术分项。这些分数不经过任何模型，同一份素材永远得到同一个分。

    分档阈值按闸门上限等比例推导：调配置时档位始终单调，且与硬闸门同一把尺子。
    """
    tolerance = settings.review_duration_tolerance
    black_max = settings.review_black_ratio_max
    freeze_max = settings.review_freeze_ratio_max
    exposure = _band(
        stats.black_ratio,
        [(0.0, 100), (0.1 * black_max, 85), (0.3 * black_max, 60), (black_max, 35), (99, 0)],
    )
    if stats.yavg_mean > 235:  # 过曝同样不可用
        exposure = min(exposure, 55)
    motion = _band(
        stats.freeze_ratio,
        [
            (0.11 * freeze_max, 100),
            (0.39 * freeze_max, 80),
            (0.66 * freeze_max, 55),
            (freeze_max, 30),
            (99, 0),
        ],
    )
    if stats.motion_mean < 0.3:  # 画面几乎不动（区别于「间歇性静止」）
        motion = min(motion, 70)
    return {
        "cv_duration": _band(
            stats.duration_error,
            [(0.25 * tolerance, 100), (0.5 * tolerance, 85), (tolerance, 65), (99, 30)],
        ),
        "cv_exposure": exposure,
        "cv_motion": motion,
        "cv_stability": _band(
            stats.flicker, [(1.0, 100), (3.0, 85), (6.0, 60), (12.0, 35), (99, 10)]
        ),
    }


def cv_notes(stats: VideoStats) -> dict[str, str]:
    return {
        "cv_duration": f"实际 {stats.probe.duration:.2f}s / 设定 {stats.expected_duration:.2f}s",
        "cv_exposure": f"平均亮度 {stats.yavg_mean:.1f}，黑帧 {stats.black_ratio:.0%}",
        "cv_motion": f"运动量 {stats.motion_mean:.2f}，静止帧 {stats.freeze_ratio:.0%}",
        "cv_stability": f"逐帧亮度抖动 {stats.flicker:.2f}",
    }


def weighted_total(scores: dict[str, int]) -> float:
    total = 0.0
    weight = 0.0
    for spec in CHECKLIST_BY_KEY.values():
        if spec.key in scores:
            total += spec.weight * max(0, min(100, int(scores[spec.key])))
            weight += spec.weight
    return round(total / weight, 1) if weight else 0.0


def decide(
    total: float, scores: dict[str, int], failures: list[GateFailure]
) -> tuple[str, list[str]]:
    """绝对阈值判定（是否可用）；同镜头内谁进成片由 compare_takes 两两比较决定。"""
    if failures:
        issues = [f.detail for f in failures]
        decision = "REJECT" if any(f.decision == "REJECT" for f in failures) else "RETAKE"
        return decision, issues

    issues: list[str] = []
    weak = [
        (CHECKLIST_BY_KEY[key].label, score)
        for key, score in scores.items()
        if key in CHECKLIST_BY_KEY and score < settings.review_item_floor
    ]
    if weak:
        label, score = min(weak, key=lambda item: item[1])
        issues.append(
            f"{label} 仅 {score} 分，低于入选下限 {settings.review_item_floor}"
        )
        return "RETAKE", issues
    if total >= settings.review_keep_threshold:
        return "KEEP", issues
    if total >= settings.review_reject_floor:
        issues.append(
            f"加权总分 {total} 未达 KEEP 线 {settings.review_keep_threshold}"
        )
        return "RETAKE", issues
    issues.append(f"加权总分 {total} 低于 REJECT 线 {settings.review_reject_floor}")
    return "REJECT", issues


def review_video_path(project_id: str, take: Take):
    """优先审原片；原片缺失（例如只上传了代理）时退回代理。"""
    for rel in (take.original_path, take.proxy_path):
        if not rel:
            continue
        path = abs_path(project_id, rel)
        if path.exists():
            return path
    return None


def asset_anchors(session: Session, project_id: str, shot: Shot) -> tuple[list[str], list[str]]:
    """给审核模型的一致性参照：已锁定资产的不可变锚点（与生成时用的是同一份）。"""
    characters: list[str] = []
    for cid in shot.character_ids or []:
        character = session.get(Character, cid)
        if character is not None and character.status == "LOCKED":
            characters.extend(character.immutable_traits or [])
    locations: list[str] = []
    if shot.location_id:
        location = session.get(Location, shot.location_id)
        if location is not None and location.status == "LOCKED":
            locations.extend(location.immutable_elements or [])
    return characters[:6], locations[:6]


def merge_scores(insight: VideoInsight, stats: VideoStats) -> tuple[dict[str, int], dict[str, str], list[str]]:
    """模型分项 + CV 分项合成完整分数表，并补齐模型漏答的项。"""
    scores = cv_scores(stats)
    notes = cv_notes(stats)
    issues: list[str] = []
    answered = insight.scores()
    for spec in MODEL_CHECKLIST:
        if spec.key in answered:
            scores[spec.key] = answered[spec.key]
            note = next(
                (item.note for item in insight.checklist if item.key == spec.key), ""
            )
            notes[spec.key] = note
        else:
            scores[spec.key] = MISSING_SCORE
            notes[spec.key] = "模型未给出该分项，按中间分计入"
            issues.append(f"视觉模型漏答分项 {spec.label}")
    return scores, notes, issues


def build_review(
    project_id: str,
    take_id: str,
    insight: VideoInsight | None,
    stats: VideoStats,
    failures: list[GateFailure],
    frames: list[VideoFrame],
) -> dict:
    """组装 take_review 产物。字段是旧文本审核结果的超集，前端/选片逻辑无需改动。"""
    if insight is not None:
        scores, notes, missing = merge_scores(insight, stats)
        issues = list(insight.issues) + missing
        changes = list(insight.suggested_prompt_changes)[:6]
        explanation = (
            f"加权总分 {weighted_total(scores)}（模型分项 0.70 / 技术分项 0.30），"
            f"视觉模型 {insight.model}，送审 {len(frames)} 帧"
        )
    else:
        scores, notes = cv_scores(stats), cv_notes(stats)
        issues, changes = [], []
        explanation = "命中确定性闸门，未调用视觉模型"

    total = weighted_total(scores)
    decision, gate_issues = decide(total, scores, failures)
    issues = gate_issues + [i for i in issues if i not in gate_issues]

    return {
        "decision": decision,
        "score": total,
        "scores": scores,
        "notes": notes,
        "issues": issues[:12],
        "suggested_prompt_changes": changes,
        "explanation": f"{explanation}；判定 {decision}",
        "gates": [f.model_dump() for f in failures],
        "observation": insight.observation if insight else "",
        "narrative_intent": insight.narrative_intent if insight else "",
        "continuity": insight.continuity if insight else "",
        "timeline": [n.model_dump() for n in (insight.timeline if insight else [])][:12],
        "reviewer": {
            "model": insight.model if insight else "cv-gates",
            "backend": settings.vlm_backend,
            "frame_count": len(frames),
            "frames": [
                {
                    "t": round(f.time, 3),
                    "digest": f.digest,
                    "url": f"/media/{project_id}/{REVIEWS_SUBDIR}/{take_id}/{Path(f.path).name}",
                }
                for f in frames
            ],
        },
        "stats": stats.model_dump(),
    }


def review_dir(project_id: str, take_id: str):
    return project_path(project_id) / REVIEWS_SUBDIR / take_id


def compare_payload(
    shot: Shot,
    winner_take_id: str,
    candidates: list[str],
    rounds: list[dict],
    model: str,
) -> dict:
    return {
        "shot_id": shot.id,
        "winner": winner_take_id,
        "candidates": candidates,
        "rounds": rounds,
        "model": model,
    }


def frames_for_compare(
    project_id: str, take: Take, expected_duration: float
) -> list[VideoFrame]:
    """复用审核阶段抽好的帧；manifest 缺失或帧文件被清理时重抽一次。"""
    manifest = review_dir(project_id, take.id) / "manifest.json"
    if manifest.exists():
        try:
            stored = json.loads(manifest.read_text(encoding="utf-8")).get("frames") or []
            frames = [VideoFrame.model_validate(item) for item in stored]
        except (OSError, ValueError, ValidationError):
            frames = []
        if frames and all(Path(f.path).exists() for f in frames):
            return frames
    _, _, frames = measure_and_sample(project_id, take, expected_duration)
    return frames


def rank_takes(reviews: dict[str, dict]) -> list[tuple[str, float]]:
    """按加权总分降序，供两两比较挑种子选手。"""
    ranked = [
        (take_id, float(review.get("score") or 0.0))
        for take_id, review in reviews.items()
        if review.get("decision") == "KEEP"
    ]
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked


def make_request(
    shot: Shot,
    take: Take,
    stats: VideoStats,
    frames: list[VideoFrame],
    characters: list[str],
    locations: list[str],
) -> VideoUnderstandingRequest:
    return VideoUnderstandingRequest(
        take_id=take.id,
        shot_title=shot.title,
        prompt=take.prompt,
        expected_duration=shot.duration_target,
        framing=shot.framing,
        camera_motion=shot.camera_motion,
        frames=frames,
        cv_summary=stats.summary(),
        character_anchors=characters,
        location_anchors=locations,
    )


def measure_and_sample(
    project_id: str, take: Take, expected_duration: float
) -> tuple[VideoStats, list[GateFailure], list[VideoFrame]]:
    """测量 + 闸门 + 抽帧。闸门命中时不抽帧（省下解码与后续上传开销）。"""
    path = review_video_path(project_id, take)
    if path is None:
        stats = VideoStats(expected_duration=expected_duration)
        stats.probe.error = "Take 视频文件不存在"
        return stats, [
            GateFailure(code="missing", decision="REJECT", detail="Take 视频文件不存在")
        ], []
    stats = frame_stats(path, expected_duration)
    failures = gate_failures(stats)
    if failures:
        return stats, failures, []
    directory = review_dir(project_id, take.id)
    samples = sample_frames(
        path,
        directory,
        stats.probe,
        count=settings.review_frame_count,
        max_width=settings.review_max_width,
        quality=settings.review_jpeg_quality,
    )
    frames = [
        VideoFrame(index=s.index, time=s.time, path=s.path, digest=s.digest)
        for s in samples
    ]
    stats.sampled_frames = len(frames)
    if not frames:
        return stats, [
            GateFailure(code="sampling", decision="RETAKE", detail="抽帧失败，无法送审")
        ], []
    # manifest 让后续的两两比较直接复用这批帧，不必再解码一次视频
    (directory / "manifest.json").write_text(
        json.dumps(
            {"take_id": take.id, "frames": [f.model_dump() for f in frames]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return stats, [], frames


__all__ = [
    "MISSING_SCORE",
    "REVIEWS_SUBDIR",
    "asset_anchors",
    "build_review",
    "compare_payload",
    "cv_notes",
    "cv_scores",
    "decide",
    "frames_for_compare",
    "make_request",
    "measure_and_sample",
    "merge_scores",
    "rank_takes",
    "review_dir",
    "review_video_path",
    "weighted_total",
]
