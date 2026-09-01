from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from ..db import project_session
from ..domain import GenerationJob

router = APIRouter(prefix="/api/v1/projects/{project_id}", tags=["generation-jobs"])


@router.get("/generation-jobs")
def list_jobs(
    project_id: str,
    shot_id: str | None = None,
    limit: int = 50,
    session: Session = Depends(project_session),
):
    stmt = select(GenerationJob).where(GenerationJob.project_id == project_id)
    if shot_id is not None:
        stmt = stmt.where(GenerationJob.shot_id == shot_id)
    jobs = list(
        session.exec(
            stmt.order_by(GenerationJob.index.desc())  # type: ignore[attr-defined]
        )
    )
    return jobs[: min(limit, 200)]


@router.post("/generation-jobs/{job_id}/cancel")
def cancel_job(
    project_id: str, job_id: str, session: Session = Depends(project_session)
):
    job = session.get(GenerationJob, job_id)
    if job is None or job.project_id != project_id:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status in ("DONE", "FAILED", "CANCELLED"):
        raise HTTPException(status_code=409, detail=f"任务已结束: {job.status}")
    if job.status == "RUNNING":
        raise HTTPException(status_code=409, detail="任务运行中，暂不支持取消")
    job.status = "CANCELLED"
    session.add(job)
    session.commit()
    return {"ok": True, "job_id": job_id, "status": job.status}
