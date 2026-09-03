"""LangGraph 主生产流程：一句话创意 → 自动成片（规格书第 46/47 节）。

状态机：IDEATION → SCRIPTING → ASSET_DESIGN → STORYBOARDING → STORYBOARD_LOCKED
→ VIDEO_GENERATION → REVIEWING →（RETAKE 回 VIDEO_GENERATION ｜ TAKE_SELECTION）
→ EDITING → PREVIEW → RENDERING → COMPLETE
"""

import asyncio
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from sqlmodel import Session, select

from ..agents import get_text_model
from ..agents.film_agents import (
    character_cards,
    director_bible,
    location_cards,
    producer_plan,
    prompt_storyboard,
    review_take,
    select_storyboard,
    writer_draft,
)
from ..compliance import compliance_gate
from ..config import settings
from ..db import get_engine, write_project_json
from ..domain import (
    AgentArtifact,
    Character,
    Location,
    Project,
    Scene,
    Shot,
    Storyboard,
    Take,
    TimelineClip,
)
from ..media.render import render_timeline
from ..repositories import next_seq_and_id
from ..services.assets import (
    character_design_prompt,
    create_assets_from_cards,
    enqueue_character_turnaround,
    enqueue_location_refs,
    link_shots_to_assets,
    location_design_prompt,
    lock_character,
    lock_location,
    shot_asset_blocks,
)
from ..services.generation import (
    enqueue_compare_jobs,
    enqueue_review_jobs,
    enqueue_storyboard_job,
    enqueue_video_jobs,
    record_artifact,
    select_and_lock_storyboard,
    takes_of_shot,
    wait_for_jobs,
)
from ..services.review import rank_takes
from ..services.timeline import auto_edit_timeline

STORYBOARD_CANDIDATES = 2


class ProductionState(TypedDict, total=False):
    """规格书第 46 节：State 只存 ID 与轻量调度元数据，数据本体走 DB。"""

    project_id: str
    idea: str
    stage: str
    scene_ids: list[str]
    shot_ids: list[str]
    character_ids: list[str]
    location_ids: list[str]
    retake_rounds: dict[str, int]
    needs_retake_shot_ids: list[str]
    blocked_reason: str
    render_url: str
    error: str


def _session(project_id: str) -> Session:
    return Session(get_engine(project_id))


def _latest_artifact(session: Session, project_id: str, kind: str) -> dict:
    stmt = (
        select(AgentArtifact)
        .where(AgentArtifact.project_id == project_id, AgentArtifact.kind == kind)
        .order_by(AgentArtifact.index.desc())
    )
    artifact = session.exec(stmt).first()
    return artifact.data if artifact else {}


def _stage_update(project_id: str, stage: str, note: str = "") -> dict:
    with _session(project_id) as session:
        project = session.get(Project, project_id)
        if project is not None:
            project.status = stage
            session.add(project)
            session.commit()
            session.refresh(project)
            write_project_json(project)
        record_artifact(
            session, project_id, "stage", "pipeline", {"stage": stage, "note": note}
        )
    return {"stage": stage}


def _shots_of_project(session: Session, project_id: str) -> list[Shot]:
    return list(
        session.exec(
            select(Shot).where(Shot.project_id == project_id).order_by(Shot.index)
        )
    )

def _take_reviews(session: Session, project_id: str) -> dict[str, dict]:
    """Return the latest persisted review for every take in a project."""
    reviews: dict[str, dict] = {}
    artifacts = session.exec(
        select(AgentArtifact)
        .where(
            AgentArtifact.project_id == project_id,
            AgentArtifact.kind == "take_review",
        )
        .order_by(AgentArtifact.index)
    )
    for artifact in artifacts:
        if artifact.subject_id:
            reviews[artifact.subject_id] = artifact.data
    return reviews


def _compliance_guard(project_id: str, prompt: str, stage: str) -> None:
    result = compliance_gate.check(prompt)
    compliance_gate.audit(project_id, stage, prompt, result)
    if not result.allowed:
        raise RuntimeError(f"合规拦截（{stage}）: {result.detail()}")


# ---------------------------------------------------------------- 节点定义


async def ideation(state: ProductionState) -> dict:
    """Writer：一句话创意 → 剧本草稿（AgentArtifact）。"""
    project_id, idea = state["project_id"], state["idea"]
    model = get_text_model()
    screenplay = await writer_draft(model, idea)
    with _session(project_id) as session:
        record_artifact(session, project_id, "screenplay", "writer", screenplay)
    return _stage_update(project_id, "SCRIPTING", f"剧本完成：{screenplay.get('logline', '')[:50]}")


async def scripting(state: ProductionState) -> dict:
    """剧本落库：Scene + Shot。"""
    project_id = state["project_id"]
    with _session(project_id) as session:
        screenplay = _latest_artifact(session, project_id, "screenplay")
        scene_ids: list[str] = []
        shot_ids: list[str] = []
        for sc in screenplay.get("scenes", []):
            if settings.video_backend == "comfyui" and len(shot_ids) >= 3:
                break
            index, scene_id = next_seq_and_id(session, Scene, project_id, "scene")
            session.add(
                Scene(
                    id=scene_id,
                    project_id=project_id,
                    index=index,
                    title=str(sc.get("title", f"场景{index}")),
                    description=str(sc.get("description", "")),
                )
            )
            scene_ids.append(scene_id)
            scene_shots = sc.get("shots", [])
            if settings.video_backend == "comfyui":
                scene_shots = scene_shots[:1]
            for sh in scene_shots:
                s_index, shot_id = next_seq_and_id(session, Shot, project_id, "shot")
                session.add(
                    Shot(
                        id=shot_id,
                        project_id=project_id,
                        scene_id=scene_id,
                        index=s_index,
                        title=str(sh.get("title", f"镜头{s_index}")),
                        description=str(sh.get("description", "")),
                        duration_target=float(sh.get("duration") or 3.0),
                        framing=str(sh.get("framing", "MEDIUM")),
                        camera_motion=str(sh.get("camera_motion", "STATIC")),
                        status="PLANNED",
                    )
                )
                shot_ids.append(shot_id)
        session.commit()
    return {
        **_stage_update(
            project_id, "ASSET_DESIGN", f"{len(scene_ids)} 场 / {len(shot_ids)} 镜"
        ),
        "scene_ids": scene_ids,
        "shot_ids": shot_ids,
    }


async def asset_design(state: ProductionState) -> dict:
    """Director 出导演圣经、Producer 出生产计划；角色设计师与美术指导建立并锁定资产。"""
    project_id, idea = state["project_id"], state["idea"]
    model = get_text_model()
    with _session(project_id) as session:
        screenplay = _latest_artifact(session, project_id, "screenplay")
        bible = await director_bible(model, idea, screenplay)
        record_artifact(session, project_id, "director_bible", "director", bible)
        plan = await producer_plan(model, idea, screenplay)
        record_artifact(session, project_id, "production_plan", "producer", plan)
        take_count = max(1, min(4, int(plan.get("default_take_count") or 2)))
        if settings.video_backend == "comfyui":
            take_count = 1
        for shot in _shots_of_project(session, project_id):
            shot.take_count = take_count
            session.add(shot)
        session.commit()

        # 规格书第 13/14 节：正式生产前建立角色与场景资产
        char_cards = await character_cards(model, screenplay)
        loc_cards = await location_cards(model, screenplay)
        record_artifact(
            session,
            project_id,
            "character_cards",
            "character_designer",
            {"characters": char_cards},
        )
        record_artifact(
            session,
            project_id,
            "location_cards",
            "art_director",
            {"locations": loc_cards},
        )
        characters, locations = create_assets_from_cards(
            session, project_id, char_cards, loc_cards
        )
        job_ids = [
            enqueue_character_turnaround(
                session, project_id, character, character_design_prompt(character)
            ).id
            for character in characters
        ]
        job_ids += [
            enqueue_location_refs(
                session, project_id, location, location_design_prompt(location)
            ).id
            for location in locations
        ]
        project = session.get(Project, project_id)
        auto_confirm = (project.mode if project is not None else "AUTO") == "AUTO"

    statuses = await wait_for_jobs(project_id, job_ids) if job_ids else {}
    asset_failed = [jid for jid, s in statuses.items() if s != "DONE"]

    with _session(project_id) as session:
        locked_characters: list[str] = []
        locked_locations: list[str] = []
        all_characters = list(
            session.exec(
                select(Character)
                .where(Character.project_id == project_id)
                .order_by(Character.index)
            )
        )
        all_locations = list(
            session.exec(
                select(Location)
                .where(Location.project_id == project_id)
                .order_by(Location.index)
            )
        )
        if auto_confirm:
            for character in all_characters:
                if character.status != "LOCKED":
                    lock_character(session, character)
                locked_characters.append(character.name)
            for location in all_locations:
                if location.status != "LOCKED":
                    lock_location(session, location)
                locked_locations.append(location.name)
        linked = link_shots_to_assets(session, project_id)
        record_artifact(
            session,
            project_id,
            "asset_lock",
            "producer",
            {
                "auto_confirm": auto_confirm,
                "characters": locked_characters,
                "locations": locked_locations,
                "shot_character_links": linked,
                "failed_asset_jobs": asset_failed,
            },
        )
        character_ids = [c.id for c in all_characters]
        location_ids = [loc.id for loc in all_locations]

    note = (
        f"导演圣经与生产计划就绪（每镜 {take_count} Take）；"
        f"角色 {len(locked_characters)} 个 / 场景 {len(locked_locations)} 个"
    )
    if asset_failed:
        note += f"，{len(asset_failed)} 个资产出图失败（仍以文本锚点约束一致性）"
    return {
        **_stage_update(project_id, "STORYBOARDING", note),
        "character_ids": character_ids,
        "location_ids": location_ids,
    }


async def storyboarding(state: ProductionState) -> dict:
    """Prompt 出提示词 → 合规闸 → 生成候选 → Director 选定并锁定。"""
    project_id = state["project_id"]
    model = get_text_model()
    with _session(project_id) as session:
        bible = _latest_artifact(session, project_id, "director_bible")
        shots = _shots_of_project(session, project_id)
        job_ids = []
        for shot in shots:
            scene = session.get(Scene, shot.scene_id) if shot.scene_id else None
            assets = shot_asset_blocks(session, project_id, shot)
            prompt = await prompt_storyboard(
                model,
                {
                    "title": shot.title,
                    "description": shot.description,
                    "framing": shot.framing,
                    "camera_motion": shot.camera_motion,
                    "duration_target": shot.duration_target,
                },
                scene.description if scene else "",
                bible,
                characters=assets["characters"],
                location=assets["location"],
            )
            _compliance_guard(project_id, prompt, "pipeline.storyboard")
            job = enqueue_storyboard_job(
                session,
                project_id,
                shot.id,
                prompt,
                count=STORYBOARD_CANDIDATES,
                references=assets["reference_images"],
            )
            job_ids.append(job.id)

    statuses = await wait_for_jobs(project_id, job_ids)
    failed = [j for j, s in statuses.items() if s != "DONE"]
    if len(failed) == len(job_ids):
        raise RuntimeError(f"分镜生成全部失败: {failed}")

    with _session(project_id) as session:
        for shot in _shots_of_project(session, project_id):
            candidates = list(
                session.exec(
                    select(Storyboard)
                    .where(
                        Storyboard.project_id == project_id,
                        Storyboard.shot_id == shot.id,
                    )
                    .order_by(Storyboard.index)
                )
            )
            if not candidates:
                continue
            if settings.image_backend == "mock" or len(candidates) == 1:
                sb_id = candidates[0].id
            else:
                sb_id = await select_storyboard(
                    model,
                    shot.title,
                    bible,
                    [{"id": c.id, "prompt": c.prompt} for c in candidates],
                )
            select_and_lock_storyboard(session, project_id, shot, sb_id)
    return _stage_update(project_id, "VIDEO_GENERATION", "分镜已锁定")


async def video_generation(state: ProductionState) -> dict:
    """按生产计划入队 VIDEO 任务并等待完成（RETAKE 轮次只补拍未过审镜头）。"""
    project_id = state["project_id"]
    targets = state.get("needs_retake_shot_ids") or state.get("shot_ids") or []
    job_ids: list[str] = []
    with _session(project_id) as session:
        for shot_id in targets:
            shot = session.get(Shot, shot_id)
            if shot is None:
                continue
            sb = session.get(Storyboard, shot.storyboard_id) if shot.storyboard_id else None
            prompt = (sb.prompt if sb else "") or shot.description
            jobs = enqueue_video_jobs(
                session, project_id, shot.id, shot.take_count, prompt
            )
            shot.status = "GENERATING"
            session.add(shot)
            session.commit()
            job_ids.extend(job.id for job in jobs)

    statuses = await wait_for_jobs(project_id, job_ids)
    done = sum(1 for s in statuses.values() if s == "DONE")
    if done == 0 and job_ids:
        raise RuntimeError(f"视频生成全部失败: {statuses}")

    with _session(project_id) as session:
        for shot_id in targets:
            shot = session.get(Shot, shot_id)
            if shot is not None:
                shot.status = "REVIEWING"
                session.add(shot)
        session.commit()
    return {
        **_stage_update(
            project_id, "REVIEWING", f"本轮 {done}/{len(job_ids)} 个 Take 生成完成"
        ),
        "needs_retake_shot_ids": [],
    }


async def reviewing(state: ProductionState) -> dict:
    """Reviewer 真实看片：审核入队 → 等待 → 同镜头 KEEP 打擂台 → 决定重拍。

    审核不在图节点里直接调模型：视觉模型调用需要限并发、可重试、在前端可见，
    这三件事统一队列已经有了（规格书第 23 节）。
    """
    project_id = state["project_id"]
    rounds = dict(state.get("retake_rounds", {}))
    needs_retake: list[str] = []
    blocked_notes: list[str] = []

    with _session(project_id) as session:
        reviewed = set(_take_reviews(session, project_id))
        targets = [
            (shot.id, take.id)
            for shot in _shots_of_project(session, project_id)
            for take in takes_of_shot(session, project_id, shot.id)
            if take.id not in reviewed
        ]
        job_ids = [j.id for j in enqueue_review_jobs(session, project_id, targets)]

    statuses = await wait_for_jobs(project_id, job_ids) if job_ids else {}
    review_failed = [job_id for job_id, s in statuses.items() if s != "DONE"]
    if review_failed:
        blocked_notes.append(
            f"{len(review_failed)} 条审核任务失败（视觉模型或抽帧不可用），相应 Take 按未过审处理"
        )

    compare_ids: list[str] = []
    with _session(project_id) as session:
        if settings.review_compare:
            reviews = _take_reviews(session, project_id)
            compare_targets: list[tuple[str, list[str]]] = []
            for shot in _shots_of_project(session, project_id):
                ranked = rank_takes(
                    {
                        take.id: reviews[take.id]
                        for take in takes_of_shot(session, project_id, shot.id)
                        if take.id in reviews
                    }
                )
                if len(ranked) >= 2:
                    compare_targets.append(
                        (
                            shot.id,
                            [
                                take_id
                                for take_id, _ in ranked[
                                    : settings.review_compare_max_candidates
                                ]
                            ],
                        )
                    )
            compare_ids = [
                j.id for j in enqueue_compare_jobs(session, project_id, compare_targets)
            ]

    if compare_ids:
        await wait_for_jobs(project_id, compare_ids)

    with _session(project_id) as session:
        reviews = _take_reviews(session, project_id)
        for shot in _shots_of_project(session, project_id):
            takes = takes_of_shot(session, project_id, shot.id)
            if not takes:
                needs_retake.append(shot.id)
                continue
            if any(reviews.get(t.id, {}).get("decision") == "KEEP" for t in takes):
                continue
            used = rounds.get(shot.id, 0)
            if used < settings.max_auto_retake_rounds:
                rounds[shot.id] = used + 1
                needs_retake.append(shot.id)
            else:
                blocked_notes.append(
                    f"镜头「{shot.title}」重拍 {used} 轮仍无 KEEP，按最高分兜底选片"
                )

    summary = f"{len(job_ids)} 条送审 / {len(compare_ids)} 组比较"
    update = _stage_update(
        project_id,
        "TAKE_SELECTION",
        f"审核完成（{summary}），{len(needs_retake)} 个镜头待重拍"
        if needs_retake
        else f"全部镜头通过审核（{summary}）",
    )
    return {
        **update,
        "retake_rounds": rounds,
        "needs_retake_shot_ids": needs_retake,
        "blocked_reason": "；".join(blocked_notes) if blocked_notes else "",
    }


def _route_after_review(state: ProductionState) -> str:
    if state.get("needs_retake_shot_ids"):
        return "video_generation"
    return "take_selection"


def _mean_score(review: dict) -> float:
    scores = review.get("scores") or {}
    values = [v for v in scores.values() if isinstance(v, (int, float))]
    return sum(values) / len(values) if values else 0.0


async def take_selection(state: ProductionState) -> dict:
    """每个镜头选入 KEEP 且评分最高的 Take；无 KEEP 时按最高分兜底。"""
    project_id = state["project_id"]
    with _session(project_id) as session:
        reviews = {
            a.subject_id: a.data
            for a in session.exec(
                select(AgentArtifact).where(
                    AgentArtifact.project_id == project_id,
                    AgentArtifact.kind == "take_review",
                )
            )
            if a.subject_id
        }
        for shot in _shots_of_project(session, project_id):
            takes = takes_of_shot(session, project_id, shot.id)
            if not takes:
                continue

            def rank(take: Take) -> tuple[int, float]:
                review = reviews.get(take.id, {})
                is_keep = 1 if review.get("decision") == "KEEP" else 0
                return (is_keep, _mean_score(review))

            best = max(takes, key=rank)
            shot.selected_take_id = best.id
            shot.status = "SELECTED"
            session.add(shot)
        session.commit()
    return _stage_update(project_id, "EDITING", "选片完成")


async def editing(state: ProductionState) -> dict:
    """Editor：按导演圣经剪辑规则自动组接时间线。"""
    project_id = state["project_id"]
    with _session(project_id) as session:
        bible = _latest_artifact(session, project_id, "director_bible")
        result = auto_edit_timeline(project_id, session)
        record_artifact(
            session,
            project_id,
            "editing",
            "editor",
            {
                "editing_rules": bible.get("editing_rules", []),
                "clip_count": result["clip_count"],
                "duration": result["duration"],
            },
        )
    return _stage_update(
        project_id,
        "PREVIEW",
        f"自动剪辑完成：{result['clip_count']} 个片段 / {result['duration']:.1f} 秒",
    )


async def rendering(state: ProductionState) -> dict:
    """成片出口前合规终审，然后 FFmpeg 渲染。"""
    project_id = state["project_id"]
    update = _stage_update(project_id, "RENDERING", "FFmpeg 渲染中")
    with _session(project_id) as session:
        clips = list(
            session.exec(
                select(TimelineClip)
                .where(TimelineClip.project_id == project_id)
                .order_by(TimelineClip.index)
            )
        )
        pairs = []
        for clip in clips:
            take = session.get(Take, clip.take_id) if clip.take_id else None
            if take is not None:
                pairs.append((clip, take))
    if not pairs:
        raise RuntimeError("时间线为空，无法渲染")
    for _, take in pairs:
        _compliance_guard(project_id, take.prompt, "pipeline.render")

    result = await asyncio.to_thread(render_timeline, project_id, pairs)
    render_url = f"/media/{project_id}/{result['path']}"
    return {"render_url": render_url, **update}


async def complete(state: ProductionState) -> dict:
    """归档：项目 COMPLETE + run_summary。"""
    project_id = state["project_id"]
    update = _stage_update(project_id, "COMPLETE", "成片就绪")
    with _session(project_id) as session:
        shots = _shots_of_project(session, project_id)
        record_artifact(
            session,
            project_id,
            "run_summary",
            "pipeline",
            {
                "render_url": state.get("render_url"),
                "blocked_reason": state.get("blocked_reason") or None,
                "retake_rounds": state.get("retake_rounds", {}),
                "shot_count": len(shots),
                "selected_count": sum(1 for s in shots if s.selected_take_id),
            },
        )
    return update


def build_film_graph():
    graph = StateGraph(ProductionState)
    graph.add_node("ideation", ideation)
    graph.add_node("scripting", scripting)
    graph.add_node("asset_design", asset_design)
    graph.add_node("storyboarding", storyboarding)
    graph.add_node("video_generation", video_generation)
    graph.add_node("reviewing", reviewing)
    graph.add_node("take_selection", take_selection)
    graph.add_node("editing", editing)
    graph.add_node("rendering", rendering)
    graph.add_node("complete", complete)

    graph.add_edge(START, "ideation")
    graph.add_edge("ideation", "scripting")
    graph.add_edge("scripting", "asset_design")
    graph.add_edge("asset_design", "storyboarding")
    graph.add_edge("storyboarding", "video_generation")
    graph.add_edge("video_generation", "reviewing")
    graph.add_conditional_edges(
        "reviewing",
        _route_after_review,
        {"video_generation": "video_generation", "take_selection": "take_selection"},
    )
    graph.add_edge("take_selection", "editing")
    graph.add_edge("editing", "rendering")
    graph.add_edge("rendering", "complete")
    graph.add_edge("complete", END)
    return graph.compile()


async def run_film_pipeline(project_id: str) -> dict:
    """图入口：读取项目创意并跑完整流程，返回最终 State。"""
    with _session(project_id) as session:
        project = session.get(Project, project_id)
        if project is None:
            raise RuntimeError("project not found")
        idea = project.idea or project.name
    initial: ProductionState = {
        "project_id": project_id,
        "idea": idea,
        "stage": "PROJECT_CREATED",
        "retake_rounds": {},
        "needs_retake_shot_ids": [],
    }
    final = await build_film_graph().ainvoke(initial)
    return dict(final)
