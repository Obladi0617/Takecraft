"""一句话全自动生产流程 API（规格书第 47 节主工作流）。"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..db import get_engine, project_session
from ..domain import AgentArtifact, GenerationJob, Project
from ..graph.film_graph import run_film_pipeline
from ..repositories import next_seq_and_id

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["pipeline"])

ACTIVE_STATUSES = {"PENDING", "RUNNING"}


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


async def _run_pipeline(project_id: str, job_id: str) -> None:
    try:
        final = await run_film_pipeline(project_id)
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
            job = session.get(GenerationJob, job_id)
            if job is not None:
                job.status = "FAILED"
                job.error = str(e)[:500]
                session.add(job)
                session.commit()


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
