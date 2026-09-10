"""一句话全自动生产流程 API（规格书第 47 节主工作流）。"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..db import abs_path, get_engine, project_session
from ..agents import get_text_model
from ..agents.film_agents import revise_video_prompt
from ..compliance import compliance_gate
from ..domain import AgentArtifact, Asset, Character, GenerationJob, Location, Project, Shot, Storyboard, Take
from ..domain.character import TURNAROUND_VIEWS
from ..domain.location import LOCATION_REF_KINDS
from ..jobs import generation_queue
from ..graph.film_graph import resume_film_pipeline, run_film_pipeline
from ..repositories import next_seq_and_id
from ..services.generation import enqueue_scene_video_job, record_artifact
from ..services.assets import (
    character_design_prompt,
    character_reference_sheets,
    enqueue_asset_job,
    location_design_prompt,
)

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["pipeline"])

ACTIVE_STATUSES = {"PENDING", "RUNNING"}

class HumanReviewIn(BaseModel):
    decision: str
    feedback: str = ""
    target_shot_ids: list[str] = []
    target_asset_ids: list[str] = []


def _latest_artifact(
    session: Session, project_id: str, kind: str, subject_id: str | None = None
) -> AgentArtifact | None:
    stmt = select(AgentArtifact).where(
        AgentArtifact.project_id == project_id, AgentArtifact.kind == kind
    )
    if subject_id is not None:
        stmt = stmt.where(AgentArtifact.subject_id == subject_id)
    return session.exec(stmt.order_by(AgentArtifact.index.desc())).first()

def _job_dict(job: GenerationJob) -> dict:
    return {
        "id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "payload": job.payload,
        "result": job.result,
        "error": job.error,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
    }


@router.post("/one-sentence", status_code=202)
async def one_sentence(
    project_id: str, session: Session = Depends(project_session)
):
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    if project.mode != "AUTO":
        raise HTTPException(
            status_code=409, detail="仅 AUTO 模式项目支持一句话全自动流程"
        )
    active = session.exec(
        select(GenerationJob).where(
            GenerationJob.project_id == project_id,
            GenerationJob.job_type == "PIPELINE",
            GenerationJob.status.in_(ACTIVE_STATUSES),  # type: ignore[arg-type]
        )
    ).first()
    if active is not None:
        raise HTTPException(
            status_code=409, detail=f"已有进行中的自动流程任务: {active.id}"
        )

    index, job_id = next_seq_and_id(session, GenerationJob, project_id, "job")
    job = GenerationJob(
        id=job_id,
        project_id=project_id,
        index=index,
        job_type="PIPELINE",
        priority="CRITICAL",
        status="RUNNING",
        payload={"idea": project.idea or project.name},
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    asyncio.create_task(_run_pipeline(project_id, job.id))
    return {"job": _job_dict(job), "message": "一句话全自动流程已启动"}


@router.post("/resume", status_code=202)
async def resume_pipeline(
    project_id: str, session: Session = Depends(project_session)
):
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    active = session.exec(
        select(GenerationJob).where(
            GenerationJob.project_id == project_id,
            GenerationJob.job_type == "PIPELINE",
            GenerationJob.status.in_(ACTIVE_STATUSES),  # type: ignore[arg-type]
        )
    ).first()
    if active is not None:
        raise HTTPException(status_code=409, detail=f"已有进行中的自动流程任务: {active.id}")

    index, job_id = next_seq_and_id(session, GenerationJob, project_id, "job")
    job = GenerationJob(
        id=job_id,
        project_id=project_id,
        index=index,
        job_type="PIPELINE",
        priority="CRITICAL",
        status="RUNNING",
        payload={"idea": project.idea or project.name, "resume_from": "REVIEWING"},
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    asyncio.create_task(_run_pipeline(project_id, job.id, resume=True))
    return {"job": _job_dict(job), "message": "已从审核阶段断点续跑"}


async def _run_pipeline(project_id: str, job_id: str, resume: bool = False) -> None:
    try:
        final = await (resume_film_pipeline(project_id) if resume else run_film_pipeline(project_id))
        with Session(get_engine(project_id)) as session:
            job = session.get(GenerationJob, job_id)
            if job is not None:
                job.status = "DONE"
                job.result = {
                    "stage": final.get("stage"),
                    "render_url": final.get("render_url"),
                    "blocked_reason": final.get("blocked_reason") or None,
                    "retake_rounds": final.get("retake_rounds", {}),
                }
                session.add(job)
                session.commit()
    except Exception as e:  # noqa: BLE001 — 编排任务必须记录失败原因
        with Session(get_engine(project_id)) as session:
            message = str(e)[:2000]
            try:
                record_artifact(
                    session,
                    project_id,
                    "model_error",
                    "pipeline",
                    {
                        "job_id": job_id,
                        "error_type": type(e).__name__,
                        "message": message,
                    },
                )
            except Exception:  # noqa: BLE001 - preserve original pipeline failure
                pass
            job = session.get(GenerationJob, job_id)
            if job is not None:
                job.status = "FAILED"
                job.error = message[:500]
                session.add(job)
                session.commit()

@router.get("/human-review")
def human_review_state(
    project_id: str, session: Session = Depends(project_session)
):
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    screenplay = _latest_artifact(session, project_id, "screenplay")
    script_decision = (
        _latest_artifact(session, project_id, "human_script_review", screenplay.id)
        if screenplay else None
    )
    char_cards = _latest_artifact(session, project_id, "character_cards")
    loc_cards = _latest_artifact(session, project_id, "location_cards")
    asset_decisions = {}
    for artifact in session.exec(
        select(AgentArtifact)
        .where(
            AgentArtifact.project_id == project_id,
            AgentArtifact.kind == "human_asset_review",
        )
        .order_by(AgentArtifact.index)
    ):
        if artifact.subject_id:
            asset_decisions[artifact.subject_id] = artifact.data
    sb_candidates = _latest_artifact(session, project_id, "storyboard_candidates")
    sb_decisions = {}
    for artifact in session.exec(
        select(AgentArtifact)
        .where(
            AgentArtifact.project_id == project_id,
            AgentArtifact.kind == "human_storyboard_review",
        )
        .order_by(AgentArtifact.index)
    ):
        if artifact.subject_id:
            sb_decisions[artifact.subject_id] = artifact.data
    take_decisions = {}
    for artifact in session.exec(
        select(AgentArtifact)
        .where(
            AgentArtifact.project_id == project_id,
            AgentArtifact.kind == "human_take_review",
        )
        .order_by(AgentArtifact.index)
    ):
        if artifact.subject_id:
            take_decisions[artifact.subject_id] = artifact.data
    characters = []
    from ..domain import Character, Location
    for c in session.exec(
        select(Character)
        .where(Character.project_id == project_id)
        .order_by(Character.index)
    ):
        characters.append({
            "id": c.id,
            "name": c.name,
            "gender": c.gender,
            "age_range": c.age_range,
            "role": c.role,
            "description": c.description,
            "appearance": c.appearance,
            "costume": c.costume,
            "personality": c.personality,
            "visual_anchors": c.visual_anchors,
            "immutable_traits": c.immutable_traits,
            "status": c.status,
        })
    locations = []
    for loc in session.exec(
        select(Location)
        .where(Location.project_id == project_id)
        .order_by(Location.index)
    ):
        locations.append({
            "id": loc.id,
            "name": loc.name,
            "description": loc.description,
            "visual_style": loc.visual_style,
            "time_of_day_default": loc.time_of_day_default,
            "materials": loc.materials,
            "colors": loc.colors,
            "visual_cues": loc.visual_cues,
            "immutable_elements": loc.immutable_elements,
            "lighting_rules": loc.lighting_rules,
            "status": loc.status,
        })
    storyboards = []
    from ..domain import Storyboard
    for sb in session.exec(
        select(Storyboard)
        .where(Storyboard.project_id == project_id)
        .order_by(Storyboard.index)
    ):
        storyboard_shot = session.get(Shot, sb.shot_id)
        image_path = sb.image_path.replace('\\', '/')
        storyboards.append({
            "id": sb.id,
            "shot_id": sb.shot_id,
            "prompt": sb.prompt,
            "image_path": sb.image_path,
            "media_url": f"/media/{project_id}/{image_path}",
            "status": sb.status,
            "is_selected": bool(
                storyboard_shot and storyboard_shot.storyboard_id == sb.id
            ),
            "is_locked": sb.status == "LOCKED",
            "shot_title": storyboard_shot.title if storyboard_shot else "",
            "shot_description": storyboard_shot.description if storyboard_shot else "",
            "duration": storyboard_shot.duration_target if storyboard_shot else None,
            "framing": storyboard_shot.framing if storyboard_shot else "",
            "camera_motion": storyboard_shot.camera_motion if storyboard_shot else "",
        })
    takes = []
    generating_shot_ids = {
        job.shot_id
        for job in session.exec(
            select(GenerationJob).where(
                GenerationJob.project_id == project_id,
                GenerationJob.job_type == "VIDEO",
                GenerationJob.status.in_(ACTIVE_STATUSES),  # type: ignore[arg-type]
            )
        )
        if job.shot_id
    }
    for shot in session.exec(
        select(Shot).where(Shot.project_id == project_id).order_by(Shot.index)
    ):
        shot_takes = list(session.exec(
            select(Take)
            .where(Take.project_id == project_id, Take.shot_id == shot.id)
            .order_by(Take.index)
        ))
        latest_id = shot_takes[-1].id if shot_takes else None
        for take in shot_takes:
            media_path = (take.proxy_path or take.original_path).replace('\\', '/')
            takes.append({
                "id": take.id,
                "shot_id": shot.id,
                "shot_title": shot.title,
                "shot_description": shot.description,
                "duration": take.duration,
                "prompt": take.prompt,
                "model": take.model,
                "media_url": f"/media/{project_id}/{media_path}",
                "is_latest": take.id == latest_id,
                "is_selected": take.id == shot.selected_take_id,
                "is_generating": shot.id in generating_shot_ids,
                "decision": take_decisions.get(take.id),
            })
    scenes_review = []
    from ..domain import Scene
    for scene in session.exec(
        select(Scene).where(Scene.project_id == project_id).order_by(Scene.index)
    ):
        scene_shots = list(session.exec(
            select(Shot).where(Shot.project_id == project_id, Shot.scene_id == scene.id).order_by(Shot.index)
        ))
        scene_jobs = list(session.exec(
            select(GenerationJob).where(
                GenerationJob.project_id == project_id,
                GenerationJob.job_type == "VIDEO",
            ).order_by(GenerationJob.index.desc())
        ))
        latest_scene_job = next((j for j in scene_jobs if (j.payload or {}).get("mode") == "scene_r2v" and (j.payload or {}).get("scene_id") == scene.id), None)
        result = (latest_scene_job.result or {}) if latest_scene_job else {}
        media_path = str(result.get("scene_media_path") or "").replace('\\', '/')
        scene_decision = _latest_artifact(session, project_id, "human_scene_review", scene.id)
        scenes_review.append({
            "id": scene.id,
            "index": scene.index,
            "title": scene.title,
            "description": scene.description,
            "duration": sum(max(float(s.duration_target), 1.0) for s in scene_shots),
            "shot_count": len(scene_shots),
            "shot_titles": [s.title for s in scene_shots],
            "job_id": latest_scene_job.id if latest_scene_job else None,
            "status": latest_scene_job.status if latest_scene_job else None,
            "prompt": str((latest_scene_job.payload or {}).get("prompt") or "") if latest_scene_job else "",
            "media_url": f"/media/{project_id}/{media_path}" if media_path else None,
            "decision": scene_decision.data if scene_decision else None,
        })
    return {
        "stage": project.status,
        "screenplay_id": screenplay.id if screenplay else None,
        "screenplay": screenplay.data if screenplay else None,
        "script_decision": script_decision.data if script_decision else None,
        "character_cards": char_cards.data if char_cards else None,
        "location_cards": loc_cards.data if loc_cards else None,
        "asset_decisions": asset_decisions,
        "characters": characters,
        "locations": locations,
        "storyboard_candidates_id": sb_candidates.id if sb_candidates else None,
        "storyboard_decisions": sb_decisions,
        "storyboards": storyboards,
        "takes": takes,
        "scenes_review": scenes_review,
        "take_decisions": take_decisions,
    }


@router.post("/script-review")
def submit_script_review(
    project_id: str,
    body: HumanReviewIn,
    session: Session = Depends(project_session),
):
    decision = body.decision.upper()
    if decision not in {"APPROVE", "REVISE"}:
        raise HTTPException(status_code=422, detail="decision must be APPROVE or REVISE")
    if decision == "REVISE" and not body.feedback.strip():
        raise HTTPException(status_code=422, detail="修改剧本时必须填写审核意见")
    screenplay = _latest_artifact(session, project_id, "screenplay")
    if screenplay is None:
        raise HTTPException(status_code=409, detail="尚无可审核剧本")
    artifact = record_artifact(
        session,
        project_id,
        "human_script_review",
        "human",
        {"decision": decision, "feedback": body.feedback.strip()},
        screenplay.id,
    )
    return {"ok": True, "review_id": artifact.id, "decision": decision}


@router.post("/asset-review")
def submit_asset_review(
    project_id: str,
    body: HumanReviewIn,
    session: Session = Depends(project_session),
):
    decision = body.decision.upper()
    if decision not in {"APPROVE", "REVISE"}:
        raise HTTPException(status_code=422, detail="decision must be APPROVE or REVISE")
    if decision == "REVISE" and not body.feedback.strip():
        raise HTTPException(status_code=422, detail="修改资产时必须填写审核意见")
    target_asset_ids = list(dict.fromkeys(body.target_asset_ids))
    if decision == "REVISE" and not target_asset_ids:
        raise HTTPException(status_code=422, detail="请至少选择一张要打回的资产图片")
    char_cards = _latest_artifact(session, project_id, "character_cards")
    loc_cards = _latest_artifact(session, project_id, "location_cards")
    if char_cards is None or loc_cards is None:
        raise HTTPException(status_code=409, detail="尚无可审核资产")
    if target_asset_ids:
        assets = list(session.exec(select(Asset).where(Asset.project_id == project_id)))
        valid_ids = {asset.id for asset in assets}
        invalid = [asset_id for asset_id in target_asset_ids if asset_id not in valid_ids]
        if invalid:
            raise HTTPException(status_code=422, detail=f"资产图片不存在: {', '.join(invalid)}")
    if decision == "REVISE":
        active_jobs = list(
            session.exec(
                select(GenerationJob).where(
                    GenerationJob.project_id == project_id,
                    GenerationJob.job_type.in_(["CHARACTER", "LOCATION"]),  # type: ignore[arg-type]
                    GenerationJob.status.in_(ACTIVE_STATUSES),  # type: ignore[arg-type]
                )
            )
        )
        active_targets = {
            (str(job.payload.get("owner_id") or ""), view)
            for job in active_jobs
            for view in (job.payload.get("views") or [])
        }
        jobs: list[GenerationJob] = []
        for asset_id in target_asset_ids:
            asset = session.get(Asset, asset_id)
            if asset is None:
                continue
            owner_id = str(asset.meta.get("owner_id") or "")
            view = str(asset.meta.get("view") or "")
            if (owner_id, view) in active_targets:
                raise HTTPException(status_code=409, detail=f"{asset_id} 已在重新生成")
            character = session.get(Character, owner_id)
            location = session.get(Location, owner_id)
            if character is not None:
                character.status = "PENDING_CONFIRM"
                session.add(character)
                prompt = f"{character_design_prompt(character)}。人工返修意见：{body.feedback.strip()}"
                jobs.append(enqueue_asset_job(
                    session, project_id, owner_id, "CHARACTER", prompt, [view],
                    width=768, height=1024,
                ))
            elif location is not None:
                location.status = "PENDING_CONFIRM"
                session.add(location)
                prompt = f"{location_design_prompt(location)}。人工返修意见：{body.feedback.strip()}"
                jobs.append(enqueue_asset_job(
                    session, project_id, owner_id, "LOCATION", prompt, [view],
                    references=character_reference_sheets(session, project_id),
                ))
        session.commit()
        artifact = record_artifact(
            session,
            project_id,
            "asset_rework_request",
            "human",
            {
                "decision": decision,
                "feedback": body.feedback.strip(),
                "target_asset_ids": target_asset_ids,
                "job_ids": [job.id for job in jobs],
            },
        )
        return {
            "ok": True,
            "review_id": artifact.id,
            "decision": decision,
            "job_ids": [job.id for job in jobs],
            "message": "指令已接收，所选图片已独立进入重新生成队列",
        }
    if decision == "APPROVE":
        active_asset_job = session.exec(
            select(GenerationJob).where(
                GenerationJob.project_id == project_id,
                GenerationJob.job_type.in_(["CHARACTER", "LOCATION"]),  # type: ignore[arg-type]
                GenerationJob.status.in_(ACTIVE_STATUSES),  # type: ignore[arg-type]
            )
        ).first()
        if active_asset_job is not None:
            raise HTTPException(
                status_code=409,
                detail="仍有资产图片正在生成；可以继续打回其他图片，全部完成后再整体通过",
            )
        refs = list(session.exec(select(Asset).where(Asset.project_id == project_id)))
        missing: list[str] = []
        owners_and_views = [
            *(
                (owner, TURNAROUND_VIEWS)
                for owner in session.exec(
                    select(Character).where(Character.project_id == project_id)
                )
            ),
            *(
                (owner, LOCATION_REF_KINDS)
                for owner in session.exec(
                    select(Location).where(Location.project_id == project_id)
                )
            ),
        ]
        for owner, expected_views in owners_and_views:
            present = {
                ref.meta.get("view")
                for ref in refs
                if ref.meta.get("owner_id") == owner.id
            }
            absent = [view for view in expected_views if view not in present]
            if absent:
                missing.append(f"{owner.name}（缺少 {', '.join(absent)}）")
        if missing:
            raise HTTPException(
                status_code=409,
                detail=f"以下资产尚无参考图，不能进入分镜：{'、'.join(missing)}",
            )
    artifact = record_artifact(
        session,
        project_id,
        "human_asset_review",
        "human",
        {"decision": decision, "feedback": body.feedback.strip(), "target_asset_ids": target_asset_ids},
        char_cards.id,
    )
    record_artifact(
        session,
        project_id,
        "human_asset_review",
        "human",
        {"decision": decision, "feedback": body.feedback.strip(), "target_asset_ids": target_asset_ids},
        loc_cards.id,
    )
    return {"ok": True, "review_id": artifact.id, "decision": decision}


@router.post("/storyboard-review")
def submit_storyboard_review(
    project_id: str,
    body: HumanReviewIn,
    session: Session = Depends(project_session),
):
    decision = body.decision.upper()
    if decision not in {"APPROVE", "REVISE"}:
        raise HTTPException(status_code=422, detail="decision must be APPROVE or REVISE")
    if decision == "REVISE" and not body.feedback.strip():
        raise HTTPException(status_code=422, detail="修改分镜时必须填写审核意见")
    sb_candidates = _latest_artifact(session, project_id, "storyboard_candidates")
    if sb_candidates is None:
        raise HTTPException(status_code=409, detail="尚无可审核分镜")
    artifact = record_artifact(
        session,
        project_id,
        "human_storyboard_review",
        "human",
        {"decision": decision, "feedback": body.feedback.strip(),
         "target_shot_ids": body.target_shot_ids},
        sb_candidates.id,
    )
    return {"ok": True, "review_id": artifact.id, "decision": decision}


@router.post("/scenes/{scene_id}/human-review", status_code=202)
async def submit_scene_review(
    project_id: str,
    scene_id: str,
    body: HumanReviewIn,
    session: Session = Depends(project_session),
):
    """Review and regenerate one complete scene, never an individual shot."""
    from ..domain import Scene
    decision = body.decision.upper()
    if decision not in {"APPROVE", "RETAKE"}:
        raise HTTPException(status_code=422, detail="decision must be APPROVE or RETAKE")
    if decision == "RETAKE" and not body.feedback.strip():
        raise HTTPException(status_code=422, detail="打回场景时必须填写具体修改方向")
    scene = session.get(Scene, scene_id)
    if scene is None or scene.project_id != project_id:
        raise HTTPException(status_code=404, detail="scene not found")
    shots = list(session.exec(
        select(Shot).where(Shot.project_id == project_id, Shot.scene_id == scene_id).order_by(Shot.index)
    ))
    if not shots:
        raise HTTPException(status_code=409, detail="场景内没有剧本镜头")

    review = record_artifact(session, project_id, "human_scene_review", "human", {
        "decision": decision, "feedback": body.feedback.strip(),
        "shot_ids": [shot.id for shot in shots],
    }, scene_id)
    if decision == "APPROVE":
        for shot in shots:
            latest = session.exec(
                select(Take).where(Take.project_id == project_id, Take.shot_id == shot.id)
                .order_by(Take.index.desc())
            ).first()
            if latest:
                shot.selected_take_id = latest.id
            shot.status = "HUMAN_APPROVED"
            session.add(shot)
        session.commit()
        return {"ok": True, "review_id": review.id, "decision": decision}

    shot_ids = [shot.id for shot in shots]
    active = session.exec(select(GenerationJob).where(
        GenerationJob.project_id == project_id,
        GenerationJob.job_type == "VIDEO",
        GenerationJob.shot_id.in_(shot_ids),  # type: ignore[arg-type]
        GenerationJob.status.in_(ACTIVE_STATUSES),  # type: ignore[arg-type]
    )).first()
    if active:
        raise HTTPException(status_code=409, detail=f"该场景已有生成任务: {active.id}")
    previous_jobs = list(session.exec(
        select(GenerationJob).where(
            GenerationJob.project_id == project_id,
            GenerationJob.job_type == "VIDEO",
        ).order_by(GenerationJob.index.desc())
    ))
    previous = next((job for job in previous_jobs if (job.payload or {}).get("mode") == "scene_r2v" and (job.payload or {}).get("scene_id") == scene_id), None)
    old_prompt = str((previous.payload or {}).get("prompt") or scene.description) if previous else scene.description
    screenplay = _latest_artifact(session, project_id, "screenplay")
    combined = "；".join(f"{shot.title}：{shot.description}" for shot in shots)
    revised_prompt = await revise_video_prompt(get_text_model(), screenplay.data if screenplay else {}, {
        "id": scene.id, "title": scene.title, "description": combined,
        "duration": sum(max(float(shot.duration_target), 1.0) for shot in shots),
        "framing": "MULTI_SHOT", "camera_motion": "SCRIPT_CONTROLLED",
    }, old_prompt, old_prompt, body.feedback.strip())
    compliance_gate.check_or_raise(revised_prompt, "scene.human_retake", project_id)
    job = enqueue_scene_video_job(
        session, project_id, scene_id, shot_ids, revised_prompt,
        sum(max(float(shot.duration_target), 1.0) for shot in shots),
        list((previous.payload or {}).get("references") or []) if previous else [],
    )
    return {"ok": True, "review_id": review.id, "decision": decision,
            "job": _job_dict(job), "revised_prompt": revised_prompt}


@router.post("/takes/{take_id}/human-review", status_code=202)
async def submit_take_review(
    project_id: str,
    take_id: str,
    body: HumanReviewIn,
    session: Session = Depends(project_session),
):
    decision = body.decision.upper()
    if decision not in {"APPROVE", "RETAKE"}:
        raise HTTPException(status_code=422, detail="decision must be APPROVE or RETAKE")
    if decision == "RETAKE" and not body.feedback.strip():
        raise HTTPException(status_code=422, detail="打回重拍时必须填写具体修改方向")
    take = session.get(Take, take_id)
    if take is None or take.project_id != project_id:
        raise HTTPException(status_code=404, detail="take not found")
    shot = session.get(Shot, take.shot_id)
    if shot is None:
        raise HTTPException(status_code=404, detail="shot not found")

    if decision == "APPROVE":
        review = record_artifact(
            session,
            project_id,
            "human_take_review",
            "human",
            {"decision": decision, "feedback": body.feedback.strip(), "shot_id": shot.id},
            take.id,
        )
        shot.selected_take_id = take.id
        shot.status = "HUMAN_APPROVED"
        session.add(shot)
        session.commit()
        return {"ok": True, "review_id": review.id, "decision": decision}

    active = session.exec(
        select(GenerationJob).where(
            GenerationJob.project_id == project_id,
            GenerationJob.shot_id == shot.id,
            GenerationJob.job_type == "VIDEO",
            GenerationJob.status.in_(ACTIVE_STATUSES),  # type: ignore[arg-type]
        )
    ).first()
    if active is not None:
        raise HTTPException(status_code=409, detail=f"该镜头已有生成任务: {active.id}")
    review = record_artifact(
        session,
        project_id,
        "human_take_review",
        "human",
        {"decision": decision, "feedback": body.feedback.strip(), "shot_id": shot.id},
        take.id,
    )
    screenplay = _latest_artifact(session, project_id, "screenplay")
    storyboard = session.get(Storyboard, shot.storyboard_id) if shot.storyboard_id else None
    prompt = await revise_video_prompt(
        get_text_model(),
        screenplay.data if screenplay else {},
        {
            "id": shot.id,
            "title": shot.title,
            "description": shot.description,
            "duration": shot.duration_target,
            "framing": shot.framing,
            "camera_motion": shot.camera_motion,
        },
        storyboard.prompt if storyboard else "",
        take.prompt,
        body.feedback.strip(),
    )
    compliance_gate.check_or_raise(prompt, "take.human_retake", project_id)
    # Manual retakes obey the same continuity rule as normal generation:
    # only inherit the previous Take tail inside the same scene.  A new scene
    # starts from its own storyboard frame and is joined by a direct cut.
    first_frame: str | None = None
    ordered_shots = list(
        session.exec(
            select(Shot)
            .where(Shot.project_id == project_id)
            .order_by(Shot.index)
        )
    )
    position = next((i for i, item in enumerate(ordered_shots) if item.id == shot.id), -1)
    if position > 0:
        previous_shot = ordered_shots[position - 1]
        if previous_shot.scene_id == shot.scene_id:
            previous_take = (
                session.get(Take, previous_shot.selected_take_id)
                if previous_shot.selected_take_id
                else session.exec(
                    select(Take)
                    .where(Take.project_id == project_id, Take.shot_id == previous_shot.id)
                    .order_by(Take.index.desc())
                ).first()
            )
            if previous_take is not None:
                tail_path = abs_path(project_id, f"frames/{previous_take.id}_tail.png")
                if tail_path.exists():
                    first_frame = str(tail_path)
    index, job_id = next_seq_and_id(session, GenerationJob, project_id, "job")
    job = GenerationJob(
        id=job_id,
        project_id=project_id,
        shot_id=shot.id,
        index=index,
        job_type="VIDEO",
        priority="HIGH",
        payload={
            "shot_id": shot.id,
            "prompt": prompt,
            "human_feedback": body.feedback.strip(),
            "replaces_take_id": take.id,
            "first_frame": first_frame,
        },
    )
    session.add(job)
    # A rejected Take invalidates the previous selection immediately.  Without
    # this, the graph can observe an older APPROVE record and skip the new job.
    shot.selected_take_id = None
    shot.status = "GENERATING"
    session.add(shot)
    session.commit()
    session.refresh(job)
    generation_queue.notify()
    return {
        "ok": True,
        "review_id": review.id,
        "decision": decision,
        "job": job.model_dump(),
        "revised_prompt": prompt,
    }

@router.get("/pipeline")
def get_pipeline(project_id: str, session: Session = Depends(project_session)):
    job = session.exec(
        select(GenerationJob)
        .where(
            GenerationJob.project_id == project_id,
            GenerationJob.job_type == "PIPELINE",
        )
        .order_by(GenerationJob.index.desc())
    ).first()
    stages = [
        a.data
        for a in session.exec(
            select(AgentArtifact)
            .where(
                AgentArtifact.project_id == project_id,
                AgentArtifact.kind == "stage",
            )
            .order_by(AgentArtifact.index)
        )
    ]
    return {"job": _job_dict(job) if job else None, "stages": stages}


@router.get("/artifacts")
def list_artifacts(
    project_id: str,
    kind: str | None = None,
    limit: int = 100,
    session: Session = Depends(project_session),
):
    stmt = select(AgentArtifact).where(AgentArtifact.project_id == project_id)
    if kind:
        stmt = stmt.where(AgentArtifact.kind == kind)
    stmt = stmt.order_by(AgentArtifact.index.desc()).limit(max(1, min(limit, 500)))
    return [
        {
            "id": a.id,
            "kind": a.kind,
            "agent": a.agent,
            "subject_id": a.subject_id,
            "data": a.data,
            "created_at": a.created_at.isoformat(),
        }
        for a in session.exec(stmt)
    ]
