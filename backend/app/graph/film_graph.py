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
    prompt_scene_video,
    prompt_storyboard,
    revise_character_cards,
    revise_location_cards,
    revise_screenplay,
    revise_storyboard_prompts,
    review_take,
    select_storyboard,
    writer_draft,
)
from ..compliance import compliance_gate
from ..config import settings
from ..db import abs_path, get_engine, write_project_json
from ..domain import (
    AgentArtifact,
    Asset,
    Character,
    GenerationJob,
    Location,
    Project,
    Scene,
    Shot,
    Storyboard,
    Take,
    TimelineClip,
)
from ..media.render import render_timeline
from ..jobs import generation_queue
from ..repositories import next_seq_and_id
from ..services.assets import (
    character_design_prompt,
    character_prompt_block,
    create_assets_from_cards,
    enqueue_asset_job,
    enqueue_character_turnaround,
    enqueue_location_refs,
    character_reference_sheets,
    link_shots_to_assets,
    location_design_prompt,
    location_prompt_block,
    lock_character,
    lock_location,
    refs_of,
    shot_asset_blocks,
)
from ..services.generation import (
    enqueue_compare_jobs,
    enqueue_review_jobs,
    enqueue_storyboard_job,
    enqueue_scene_video_job,
    enqueue_video_jobs,
    record_artifact,
    select_and_lock_storyboard,
    takes_of_shot,
    wait_for_jobs,
)
from ..services.review import rank_takes
from ..services.timeline import auto_edit_timeline

# 人工审核的是镜头内容而不是同一提示词的随机抽卡；默认每镜仅生成一张。
STORYBOARD_CANDIDATES = 1


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


def _latest_human_decisions(
    session: Session, project_id: str, kind: str
) -> dict[str, dict]:
    decisions: dict[str, dict] = {}
    artifacts = session.exec(
        select(AgentArtifact)
        .where(AgentArtifact.project_id == project_id, AgentArtifact.kind == kind)
        .order_by(AgentArtifact.index)
    )
    for artifact in artifacts:
        if artifact.subject_id:
            decisions[artifact.subject_id] = artifact.data
    return decisions

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


async def script_review(state: ProductionState) -> dict:
    """Durable human gate: approve the screenplay or revise it from feedback."""
    project_id, idea = state["project_id"], state["idea"]
    _stage_update(project_id, "SCRIPT_REVIEW", "剧本已生成，等待人工审核")
    model = get_text_model()
    while True:
        with _session(project_id) as session:
            artifact = session.exec(
                select(AgentArtifact)
                .where(
                    AgentArtifact.project_id == project_id,
                    AgentArtifact.kind == "screenplay",
                )
                .order_by(AgentArtifact.index.desc())
            ).first()
            if artifact is None:
                raise RuntimeError("剧本产物不存在")
            screenplay_id = artifact.id
            screenplay = artifact.data
            decision = _latest_human_decisions(
                session, project_id, "human_script_review"
            ).get(screenplay_id)
        if not decision:
            await asyncio.sleep(1.0)
            continue
        if decision.get("decision") == "APPROVE":
            return _stage_update(project_id, "SCRIPTING", "人工审核已通过剧本")
        feedback = str(decision.get("feedback") or "").strip()
        if not feedback:
            await asyncio.sleep(1.0)
            continue
        revised = await revise_screenplay(model, idea, screenplay, feedback)
        with _session(project_id) as session:
            record_artifact(session, project_id, "screenplay", "writer", revised)
        _stage_update(project_id, "SCRIPT_REVIEW", "已按人工意见修改，等待再次审核")

async def scripting(state: ProductionState) -> dict:
    """剧本落库：Scene + Shot。"""
    project_id = state["project_id"]
    with _session(project_id) as session:
        screenplay = _latest_artifact(session, project_id, "screenplay")
        scene_ids: list[str] = []
        shot_ids: list[str] = []
        for sc in screenplay.get("scenes", []):
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
                        duration_target=float(sh.get("duration") or 2.0),
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
        take_count = 1
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
        character_ids = [c.id for c in characters]
        location_ids = [loc.id for loc in locations]
        character_job_ids = [
            enqueue_character_turnaround(
                session, project_id, character, character_design_prompt(character)
            ).id
            for character in characters
        ]
        character_statuses = await wait_for_jobs(project_id, character_job_ids) if character_job_ids else {}
        failed_characters = [job_id for job_id, status in character_statuses.items() if status != "DONE"]
        if failed_characters:
            raise RuntimeError(f"角色参考图生成失败: {failed_characters}")
        asset_job_ids = [
            enqueue_location_refs(
                session, project_id, location, location_design_prompt(location)
            ).id
            for location in locations
        ]

    # Human asset review must inspect actual images, not just text cards.  Wait
    # here so the review stage cannot be entered with empty image slots.
    asset_statuses = await wait_for_jobs(project_id, asset_job_ids) if asset_job_ids else {}
    failed_assets = [job_id for job_id, status in asset_statuses.items() if status != "DONE"]
    if failed_assets:
        raise RuntimeError(f"资产参考图生成失败: {failed_assets}")

    note = (
        f"导演圣经与生产计划就绪（每镜 {take_count} Take）；"
        f"角色 {len(characters)} 个 / 场景 {len(locations)} 个，参考图已生成，等待人工审核"
    )
    return {
        **_stage_update(project_id, "ASSET_REVIEW", note),
        "character_ids": character_ids,
        "location_ids": location_ids,
    }


async def asset_review(state: ProductionState) -> dict:
    """Durable human gate: approve characters & locations or revise from feedback."""
    project_id = state["project_id"]
    _stage_update(project_id, "ASSET_REVIEW", "资产已生成，等待人工审核")
    model = get_text_model()
    while True:
        with _session(project_id) as session:
            char_art = session.exec(
                select(AgentArtifact)
                .where(
                    AgentArtifact.project_id == project_id,
                    AgentArtifact.kind == "character_cards",
                )
                .order_by(AgentArtifact.index.desc())
            ).first()
            loc_art = session.exec(
                select(AgentArtifact)
                .where(
                    AgentArtifact.project_id == project_id,
                    AgentArtifact.kind == "location_cards",
                )
                .order_by(AgentArtifact.index.desc())
            ).first()
            if char_art is None or loc_art is None:
                raise RuntimeError("资产产物不存在")
            decisions = _latest_human_decisions(
                session, project_id, "human_asset_review"
            )
            decision = decisions.get(char_art.id) or decisions.get(loc_art.id)
            old_chars = list(char_art.data.get('characters', []))
            old_locs = list(loc_art.data.get('locations', []))
        if not decision:
            await asyncio.sleep(1.0)
            continue
        if decision.get("decision") == "APPROVE":
            with _session(project_id) as session:
                characters = list(
                    session.exec(
                        select(Character)
                        .where(Character.project_id == project_id)
                        .order_by(Character.index)
                    )
                )
                locations = list(
                    session.exec(
                        select(Location)
                        .where(Location.project_id == project_id)
                        .order_by(Location.index)
                    )
                )
                for character in characters:
                    if character.status != "LOCKED":
                        lock_character(session, character)
                for location in locations:
                    if location.status != "LOCKED":
                        lock_location(session, location)
                link_shots_to_assets(session, project_id)
                character_names = [character.name for character in characters]
                location_names = [location.name for location in locations]
            with _session(project_id) as session:
                for character in session.exec(
                    select(Character).where(Character.project_id == project_id)
                ):
                    if character.status != "LOCKED":
                        lock_character(session, character)
                for location in session.exec(
                    select(Location).where(Location.project_id == project_id)
                ):
                    if location.status != "LOCKED":
                        lock_location(session, location)
                link_shots_to_assets(session, project_id)
                record_artifact(
                    session,
                    project_id,
                    "asset_lock",
                    "producer",
                    {
                        "characters": character_names,
                        "locations": location_names,
                        "failed_asset_jobs": [],
                    },
                )
            note = "人工审核已通过全部现有资产，不重复生成参考图"
            return _stage_update(project_id, "STORYBOARDING", note)
        feedback = str(decision.get("feedback") or "").strip()
        if not feedback:
            await asyncio.sleep(1.0)
            continue
        target_asset_ids = [str(value) for value in decision.get("target_asset_ids", []) if value]
        if target_asset_ids:
            with _session(project_id) as session:
                selected_assets = [
                    asset for asset_id in target_asset_ids
                    if (asset := session.get(Asset, asset_id)) is not None
                    and asset.project_id == project_id
                ]
                job_ids: list[str] = []
                for asset in selected_assets:
                    owner_id = str(asset.meta.get("owner_id") or "")
                    view = str(asset.meta.get("view") or "")
                    character = session.get(Character, owner_id)
                    location = session.get(Location, owner_id)
                    if character is not None:
                        prompt = f"{character_design_prompt(character)}。人工返修意见：{feedback}"
                        job_ids.append(enqueue_asset_job(
                            session, project_id, owner_id, "CHARACTER", prompt, [view],
                            width=768, height=1024,
                        ).id)
                    elif location is not None:
                        prompt = f"{location_design_prompt(location)}。人工返修意见：{feedback}"
                        job_ids.append(enqueue_asset_job(
                            session, project_id, owner_id, "LOCATION", prompt, [view],
                            references=character_reference_sheets(session, project_id),
                        ).id)
                if not job_ids:
                    raise RuntimeError("所选资产图片已不存在，无法打回")
            statuses = await wait_for_jobs(project_id, job_ids)
            failed = [job_id for job_id, status in statuses.items() if status != "DONE"]
            if failed:
                raise RuntimeError(f"所选资产图片重新生成失败: {failed}")
            # Create fresh card revisions so this decision is consumed exactly once.
            with _session(project_id) as session:
                record_artifact(session, project_id, "character_cards", "character_designer", {"characters": old_chars})
                record_artifact(session, project_id, "location_cards", "art_director", {"locations": old_locs})
            _stage_update(project_id, "ASSET_REVIEW", f"已按意见重生成 {len(job_ids)} 张图片，等待再次审核")
            continue
        with _session(project_id) as session:
            screenplay = _latest_artifact(session, project_id, "screenplay")
        # Generate and validate both replacement card sets before touching the
        # current assets. A malformed model response must never leave review
        # showing zero characters/locations.
        new_chars = await revise_character_cards(model, screenplay, old_chars, feedback)
        new_locs = await revise_location_cards(model, screenplay, old_locs, feedback)
        with _session(project_id) as session:
            for char in session.exec(
                select(Character).where(Character.project_id == project_id)
            ):
                session.delete(char)
            for loc in session.exec(
                select(Location).where(Location.project_id == project_id)
            ):
                session.delete(loc)
            session.commit()
            record_artifact(
                session, project_id, "character_cards", "character_designer",
                {"characters": new_chars},
            )
            record_artifact(
                session, project_id, "location_cards", "art_director",
                {"locations": new_locs},
            )
            characters, locations = create_assets_from_cards(
                session, project_id, new_chars, new_locs
            )
            character_job_ids = [
                enqueue_character_turnaround(
                    session, project_id, c, character_design_prompt(c)
                ).id
                for c in characters
            ]
        character_statuses = await wait_for_jobs(project_id, character_job_ids) if character_job_ids else {}
        failed_characters = [job_id for job_id, status in character_statuses.items() if status != "DONE"]
        if failed_characters:
            raise RuntimeError(f"角色参考图重新生成失败: {failed_characters}")
        with _session(project_id) as session:
            locations = list(session.exec(select(Location).where(Location.project_id == project_id)))
            job_ids = [
                enqueue_location_refs(
                    session, project_id, loc, location_design_prompt(loc)
                ).id
                for loc in locations
            ]
        await wait_for_jobs(project_id, job_ids) if job_ids else None
        _stage_update(project_id, "ASSET_REVIEW", "已按人工意见修改资产，等待再次审核")


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
        record_artifact(
            session, project_id, "storyboard_candidates", "prompt_engineer",
            {"shot_count": len(_shots_of_project(session, project_id))},
        )
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
                select_and_lock_storyboard(session, project_id, shot, sb_id)
    return _stage_update(project_id, "STORYBOARD_REVIEW", "分镜候选已生成，等待人工审核")


async def storyboard_review(state: ProductionState) -> dict:
    """Durable human gate: approve storyboards or revise from feedback."""
    project_id = state["project_id"]
    _stage_update(project_id, "STORYBOARD_REVIEW", "分镜候选已生成，等待人工审核")
    model = get_text_model()
    while True:
        with _session(project_id) as session:
            shots = _shots_of_project(session, project_id)
            sb_art = session.exec(
                select(AgentArtifact)
                .where(
                    AgentArtifact.project_id == project_id,
                    AgentArtifact.kind == "storyboard_candidates",
                )
                .order_by(AgentArtifact.index.desc())
            ).first()
            if sb_art is None:
                await asyncio.sleep(1.0)
                continue
            decision = _latest_human_decisions(
                session, project_id, "human_storyboard_review"
            ).get(sb_art.id)
        if not decision:
            await asyncio.sleep(1.0)
            continue
        if decision.get("decision") == "APPROVE":
            with _session(project_id) as session:
                bible = _latest_artifact(session, project_id, "director_bible")
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
                    selected = next((candidate for candidate in candidates if candidate.id == shot.storyboard_id), None)
                    if selected is not None:
                        # Human selection is authoritative. Never let the model
                        # replace the version explicitly chosen for video.
                        sb_id = selected.id
                    elif settings.image_backend == "mock" or len(candidates) == 1:
                        sb_id = candidates[0].id
                    else:
                        sb_id = await select_storyboard(
                            model,
                            shot.title,
                            bible,
                            [{"id": c.id, "prompt": c.prompt} for c in candidates],
                        )
                    select_and_lock_storyboard(session, project_id, shot, sb_id)
            return _stage_update(project_id, "VIDEO_GENERATION", "人工审核已通过分镜")
        feedback = str(decision.get("feedback") or "").strip()
        target_shot_ids = set(decision.get("target_shot_ids") or [])
        if not feedback:
            await asyncio.sleep(1.0)
            continue
        with _session(project_id) as session:
            bible = _latest_artifact(session, project_id, "director_bible")
            shots = _shots_of_project(session, project_id)
            job_ids = []
            for shot in shots:
                if target_shot_ids and shot.id not in target_shot_ids:
                    continue
                scene = session.get(Scene, shot.scene_id) if shot.scene_id else None
                assets = shot_asset_blocks(session, project_id, shot)
                old_sb = list(
                    session.exec(
                        select(Storyboard)
                        .where(
                            Storyboard.project_id == project_id,
                            Storyboard.shot_id == shot.id,
                        )
                    )
                )
                old_prompt = old_sb[0].prompt if old_sb else shot.description
                new_prompt = await revise_storyboard_prompts(
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
                    old_prompt,
                    feedback,
                    characters=assets["characters"],
                    location=assets["location"],
                )
                _compliance_guard(project_id, new_prompt, "pipeline.storyboard.revision")
                job = enqueue_storyboard_job(
                    session,
                    project_id,
                    shot.id,
                    new_prompt,
                    count=STORYBOARD_CANDIDATES,
                    references=assets["reference_images"],
                )
                job_ids.append(job.id)
            record_artifact(
                session,
                project_id,
                "storyboard_candidates",
                "prompt_engineer",
                {"revision_feedback": feedback},
            )
        await wait_for_jobs(project_id, job_ids)
        _stage_update(
            project_id, "STORYBOARD_REVIEW", "已按人工意见修改分镜，等待再次审核"
        )


async def video_generation(state: ProductionState) -> dict:
    """每个大场景合成一个 R2V 任务，再按剧本时长切回各 Take。"""
    import logging

    logger = logging.getLogger(__name__)
    project_id = state["project_id"]
    targets = state.get("needs_retake_shot_ids") or state.get("shot_ids") or []
    job_ids: list[str] = []
    completed_count = 0
    _stage_update(
        project_id,
        "HUMAN_TAKE_REVIEW",
        f"视频逐镜生成中（0/{len(targets)}）；每完成一条即可立即播放复审",
    )
    with _session(project_id) as session:
        ordered = [s for s in _shots_of_project(session, project_id) if s.id in targets]
        screenplay = _latest_artifact(session, project_id, "screenplay")
        bible = _latest_artifact(session, project_id, "director_bible")
    groups: list[tuple[str, list[Shot]]] = []
    for shot in ordered:
        key = shot.scene_id or f"shot:{shot.id}"
        if not groups or groups[-1][0] != key:
            groups.append((key, []))
        groups[-1][1].append(shot)
    model = get_text_model()
    for scene_id, scene_shots in groups:
        with _session(project_id) as session:
            scene = session.get(Scene, scene_id) if not scene_id.startswith("shot:") else None
            references: list[str] = []
            characters: list[dict] = []
            locations: list[dict] = []
            shot_specs: list[dict] = []
            for shot in scene_shots:
                current = session.get(Shot, shot.id)
                if current is None:
                    continue
                assets = shot_asset_blocks(session, project_id, current)
                for block in assets.get("characters") or []:
                    if block not in characters:
                        characters.append(block)
                if assets.get("location") and assets["location"] not in locations:
                    locations.append(assets["location"])
                # Scene R2V gets the actual locked character and location sheets,
                # rather than the three-image cap used by storyboard generation.
                owner_ids = [*(current.character_ids or [])]
                if current.location_id:
                    owner_ids.append(current.location_id)
                for owner_id in owner_ids:
                    for asset in refs_of(session, project_id, owner_id):
                        ref = str(abs_path(project_id, asset.path))
                        if ref not in references:
                            references.append(ref)
                shot_specs.append({"id": current.id, "title": current.title,
                    "description": current.description, "framing": current.framing,
                    "camera_motion": current.camera_motion, "duration": current.duration_target})
            scene_data = {"id": scene_id, "title": scene.title if scene else "",
                          "description": scene.description if scene else ""}
            # Include named props/armies such as the Trojan horse even when an
            # older project was created before those assets were linked to Shot.character_ids.
            searchable = " ".join([scene_data["title"], scene_data["description"], *(
                f"{item['title']} {item['description']}" for item in shot_specs)])
            candidates = list(session.exec(select(Character).where(
                Character.project_id == project_id, Character.status == "LOCKED")))
            for character in candidates:
                aliases = {character.name, character.name.replace("特洛伊", "")}
                if any(alias and alias in searchable for alias in aliases):
                    block = character.prompt_block or character_prompt_block(character)
                    if block not in characters:
                        characters.append(block)
                    for asset in refs_of(session, project_id, character.id):
                        ref = str(abs_path(project_id, asset.path))
                        if ref not in references:
                            references.append(ref)
            location_candidates = list(session.exec(select(Location).where(
                Location.project_id == project_id, Location.status == "LOCKED")))
            for location in location_candidates:
                aliases = {location.name, location.name.replace("特洛伊", ""),
                           location.name.replace("被毁灭的", "")}
                if any(alias and alias in searchable for alias in aliases):
                    block = location.prompt_block or location_prompt_block(location)
                    if block not in locations:
                        locations.append(block)
                    for asset in refs_of(session, project_id, location.id):
                        ref = str(abs_path(project_id, asset.path))
                        if ref not in references:
                            references.append(ref)
        # Every new scene generation must go through the text model.  Reusing
        # an old failed job's prompt hid ModelScope failures and made the UI
        # appear to skip the text-API stage entirely.
        logger.info("[SCENE_PROMPT] submitting scene %s to the text API", scene_id)
        try:
            prompt = await prompt_scene_video(
                model, screenplay, bible, scene_data, shot_specs, characters, locations
            )
        except Exception as exc:
            raise RuntimeError(
                f"场景 {scene_data['title'] or scene_id} 的魔搭文本提示词生成失败；"
                "未提交R2V任务，请先检查魔搭API连接："
                f"{exc}"
            ) from exc
        _compliance_guard(project_id, prompt, "pipeline.video.scene_prompt")
        with _session(project_id) as session:
            job = enqueue_scene_video_job(session, project_id, scene_id,
                [shot.id for shot in scene_shots], prompt,
                sum(max(float(shot.duration_target), 1.0) for shot in scene_shots),
                references[:9])
            for shot in scene_shots:
                current = session.get(Shot, shot.id)
                if current:
                    current.status = "GENERATING"
                    session.add(current)
            session.commit()
            job_ids.append(job.id)
        # A 1 MP MiniMax H3 R2V render commonly takes 25-40 minutes on DGX.
        # Keep the orchestration wait longer than the adapter deadline so the
        # outer pipeline never marks a healthy RUNNING render as failed.
        video_wait_timeout = max(float(settings.comfyui_timeout) + 300.0, 3600.0)
        shot_statuses = await wait_for_jobs(
            project_id, [job.id], timeout=video_wait_timeout
        )
        if shot_statuses.get(job.id) != "DONE":
            raise RuntimeError(f"场景 {scene_id} 视频生成失败: {shot_statuses}")
        completed_count += len(scene_shots)
        _stage_update(
            project_id,
            "HUMAN_TAKE_REVIEW",
            f"完整场景视频生成中（已覆盖 {completed_count}/{len(targets)} 个剧本镜头）；小镜头仅用于控制场景内部节奏",
        )

    statuses = {job_id: "DONE" for job_id in job_ids}
    done = sum(1 for s in statuses.values() if s == "DONE")
    if done != len(job_ids):
        raise RuntimeError(f"视频未全部生成成功: {statuses}")

    with _session(project_id) as session:
        for shot_id in targets:
            shot = session.get(Shot, shot_id)
            if shot is not None:
                shot.status = "REVIEWING"
                session.add(shot)
        session.commit()
    return {
        **_stage_update(
            project_id, "HUMAN_TAKE_REVIEW", f"本轮 {done}/{len(job_ids)} 个完整场景视频生成完成，等待人工复审"
        ),
        "needs_retake_shot_ids": [],
    }


async def asset_generation_recovery(state: ProductionState) -> dict:
    """Retry only failed asset image jobs after ComfyUI reconnects."""
    project_id = state["project_id"]
    job_ids: list[str] = []
    with _session(project_id) as session:
        jobs = list(
            session.exec(
                select(GenerationJob)
                .where(
                    GenerationJob.project_id == project_id,
                    GenerationJob.job_type.in_(["CHARACTER", "LOCATION"]),  # type: ignore[arg-type]
                )
                .order_by(GenerationJob.index)
            )
        )
        for job in jobs:
            if job.status in {"FAILED", "CANCELLED"}:
                job.status = "PENDING"
                job.error = None
                job.result = {}
                job.retry_count = 0
                session.add(job)
            if job.status in {"PENDING", "RUNNING", "FAILED", "CANCELLED"}:
                job_ids.append(job.id)
        session.commit()
    if not job_ids:
        raise RuntimeError("没有可恢复的资产生成任务")
    generation_queue.notify()
    _stage_update(project_id, "ASSET_DESIGN", f"正在恢复 {len(job_ids)} 个失败的资产图片任务")
    statuses = await wait_for_jobs(project_id, job_ids)
    failed = [job_id for job_id, status in statuses.items() if status != "DONE"]
    if failed:
        raise RuntimeError(f"资产参考图恢复失败: {failed}")
    return {
        **_stage_update(project_id, "ASSET_REVIEW", "资产参考图已恢复，等待人工审核"),
        "character_ids": [],
        "location_ids": [],
    }


async def reviewing(state: ProductionState) -> dict:
    """Reviewer 真实看片并给出参考意见，但不再自动决定重拍。

    审核不在图节点里直接调模型：视觉模型调用需要限并发、可重试、在前端可见，
    这三件事统一队列已经有了。最终通过或重拍完全交给人工复审，避免模型
    误判引发整批重复生成和额度浪费。
    """
    project_id = state["project_id"]
    rounds = dict(state.get("retake_rounds", {}))
    blocked_notes: list[str] = []

    with _session(project_id) as session:
        previous_reviews = _take_reviews(session, project_id)
        # Technical decode/sampling failures are retried after a restart or tool
        # repair; they do not represent a creative rejection of the Take.
        reviewed = {
            take_id
            for take_id, review in previous_reviews.items()
            if not (
                review.get("reviewer", {}).get("frame_count", 0) == 0
                and any(
                    gate.get("code") in {"decode", "sampling"}
                    for gate in review.get("gates", [])
                )
            )
        }
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

    summary = f"{len(job_ids)} 条送审 / {len(compare_ids)} 组比较"
    update = _stage_update(
        project_id,
        "HUMAN_TAKE_REVIEW",
        f"机器审核完成（{summary}），等待人工逐条通过或打回",
    )
    return {
        **update,
        "retake_rounds": rounds,
        "needs_retake_shot_ids": [],
        "blocked_reason": "；".join(blocked_notes) if blocked_notes else "",
    }


async def human_take_review(state: ProductionState) -> dict:
    """Pause until a human approves one generated Take for every shot."""
    project_id = state["project_id"]
    _stage_update(
        project_id,
        "HUMAN_TAKE_REVIEW",
        "Take 已生成，等待人工逐镜复审；可通过或填写意见后打回重拍",
    )
    while True:
        all_approved = True
        with _session(project_id) as session:
            decisions = _latest_human_decisions(
                session, project_id, "human_take_review"
            )
            for shot in _shots_of_project(session, project_id):
                takes = takes_of_shot(session, project_id, shot.id)
                # Only the newest Take can release a shot.  An older approved
                # Take must not win while its replacement is still generating.
                latest = takes[-1] if takes else None
                approved = (
                    latest
                    if latest
                    and decisions.get(latest.id, {}).get("decision") == "APPROVE"
                    else None
                )
                if approved is None:
                    all_approved = False
                    continue
                shot.selected_take_id = approved.id
                shot.status = "HUMAN_APPROVED"
                session.add(shot)
            session.commit()
        if all_approved:
            return _stage_update(project_id, "TAKE_SELECTION", "全部镜头已通过人工复审")
        await asyncio.sleep(1.0)

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
        human_decisions = _latest_human_decisions(session, project_id, "human_take_review")
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
                human_keep = 2 if human_decisions.get(take.id, {}).get("decision") == "APPROVE" else 0
                auto_keep = 1 if review.get("decision") == "KEEP" else 0
                return (human_keep + auto_keep, _mean_score(review))

            human_approved = [
                take
                for take in takes
                if human_decisions.get(take.id, {}).get("decision") == "APPROVE"
            ]
            # Human approval is authoritative; when a shot has been retaken,
            # the most recently approved result supersedes older approvals.
            best = human_approved[-1] if human_approved else max(takes, key=rank)
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
            from ..services.timeline import resolve_clip_take
            take = resolve_clip_take(session, clip)
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
    graph.add_node("script_review", script_review)
    graph.add_node("scripting", scripting)
    graph.add_node("asset_design", asset_design)
    graph.add_node("asset_review", asset_review)
    graph.add_node("storyboarding", storyboarding)
    graph.add_node("storyboard_review", storyboard_review)
    graph.add_node("video_generation", video_generation)
    graph.add_node("reviewing", reviewing)
    graph.add_node("human_take_review", human_take_review)
    graph.add_node("take_selection", take_selection)
    graph.add_node("editing", editing)
    graph.add_node("rendering", rendering)
    graph.add_node("complete", complete)

    graph.add_edge(START, "ideation")
    graph.add_edge("ideation", "script_review")
    graph.add_edge("script_review", "scripting")
    graph.add_edge("scripting", "asset_design")
    graph.add_edge("asset_design", "asset_review")
    graph.add_edge("asset_review", "storyboarding")
    graph.add_edge("storyboarding", "storyboard_review")
    graph.add_edge("storyboard_review", "video_generation")
    graph.add_edge("video_generation", "reviewing")
    graph.add_edge("reviewing", "human_take_review")
    graph.add_edge("human_take_review", "take_selection")
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

def build_resume_graph(start_node: str = "reviewing"):
    """Resume an interrupted production from review, preserving all existing media."""
    graph = StateGraph(ProductionState)
    graph.add_node("asset_review", asset_review)
    graph.add_node("asset_generation_recovery", asset_generation_recovery)
    graph.add_node("storyboarding", storyboarding)
    graph.add_node("storyboard_review", storyboard_review)
    graph.add_node("reviewing", reviewing)
    graph.add_node("video_generation", video_generation)
    graph.add_node("human_take_review", human_take_review)
    graph.add_node("take_selection", take_selection)
    graph.add_node("editing", editing)
    graph.add_node("rendering", rendering)
    graph.add_node("complete", complete)
    graph.add_edge(START, start_node)
    graph.add_edge("asset_generation_recovery", "asset_review")
    graph.add_edge("asset_review", "storyboarding")
    graph.add_edge("storyboarding", "storyboard_review")
    graph.add_edge("storyboard_review", "video_generation")
    graph.add_edge("reviewing", "human_take_review")
    graph.add_edge("human_take_review", "take_selection")

    graph.add_edge("video_generation", "reviewing")
    graph.add_edge("take_selection", "editing")
    graph.add_edge("editing", "rendering")
    graph.add_edge("rendering", "complete")
    graph.add_edge("complete", END)
    return graph.compile()


async def resume_film_pipeline(project_id: str) -> dict:
    """Resume after video generation/review without rebuilding script or assets."""
    with _session(project_id) as session:
        project = session.get(Project, project_id)
        if project is None:
            raise RuntimeError("project not found")
        all_shots = _shots_of_project(session, project_id)
        # A scene is complete only when the new scene-level R2V source exists
        # and was generated for the current screenplay duration. Legacy jobs
        # that only left split Takes must be regenerated as complete scenes.
        scene_jobs = list(session.exec(
            select(GenerationJob).where(
                GenerationJob.project_id == project_id,
                GenerationJob.job_type == "VIDEO",
                GenerationJob.status == "DONE",
            ).order_by(GenerationJob.index.desc())
        ))
        shot_ids: list[str] = []
        scene_keys = list(dict.fromkeys(shot.scene_id or f"shot:{shot.id}" for shot in all_shots))
        for scene_key in scene_keys:
            scene_shots = [shot for shot in all_shots if (shot.scene_id or f"shot:{shot.id}") == scene_key]
            expected = sum(max(float(shot.duration_target), 1.0) for shot in scene_shots)
            latest = next((job for job in scene_jobs if
                (job.payload or {}).get("mode") == "scene_r2v"
                and (job.payload or {}).get("scene_id") == scene_key), None)
            result = (latest.result or {}) if latest else {}
            media_path = str(result.get("scene_media_path") or "")
            generated_for = float((latest.payload or {}).get("duration") or 0.0) if latest else 0.0
            complete = bool(
                media_path
                and abs_path(project_id, media_path).is_file()
                and abs(generated_for - expected) <= 0.05
            )
            if not complete:
                shot_ids.extend(shot.id for shot in scene_shots)
    initial: ProductionState = {
        "project_id": project_id,
        "idea": project.idea or project.name,
        "stage": "REVIEWING",
        "shot_ids": shot_ids,
        "retake_rounds": {},
        "needs_retake_shot_ids": [],
    }
    start_node = {
        "ASSET_DESIGN": "asset_generation_recovery",
        "ASSET_REVIEW": "asset_review",
        "STORYBOARDING": "storyboarding",
        "STORYBOARD_REVIEW": "storyboard_review",
    }.get(project.status, "video_generation")
    final = await build_resume_graph(start_node).ainvoke(initial)
    return dict(final)
