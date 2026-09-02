"""生成入队与等待：API 路由与 LangGraph 图共用的服务层。"""

import asyncio

from sqlmodel import Session, select

from ..db import get_engine
from ..domain import AgentArtifact, GenerationJob, Shot, Storyboard, Take
from ..jobs import generation_queue
from ..repositories import next_seq_and_id

TERMINAL = {"DONE", "FAILED", "CANCELLED"}


def record_artifact(
    session: Session,
    project_id: str,
    kind: str,
    agent: str,
    data: dict,
    subject_id: str | None = None,
) -> AgentArtifact:
    index, art_id = next_seq_and_id(session, AgentArtifact, project_id, "art")
    artifact = AgentArtifact(
        id=art_id,
        project_id=project_id,
        index=index,
        kind=kind,
        agent=agent,
        subject_id=subject_id,
        data=data,
    )
    session.add(artifact)
    session.commit()
    session.refresh(artifact)
    return artifact


def enqueue_storyboard_job(
    session: Session,
    project_id: str,
    shot_id: str,
    prompt: str,
    count: int = 4,
    width: int = 1024,
    height: int = 576,
) -> GenerationJob:
    index, job_id = next_seq_and_id(session, GenerationJob, project_id, "job")
    job = GenerationJob(
        id=job_id,
        project_id=project_id,
        shot_id=shot_id,
        index=index,
        job_type="STORYBOARD",
        payload={
            "shot_id": shot_id,
            "prompt": prompt,
            "count": max(count, 1),
            "width": width,
            "height": height,
        },
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    generation_queue.notify()
    return job


def enqueue_video_jobs(
    session: Session,
    project_id: str,
    shot_id: str,
    count: int,
    prompt: str,
) -> list[GenerationJob]:
    jobs = []
    for _ in range(max(count, 1)):
        index, job_id = next_seq_and_id(session, GenerationJob, project_id, "job")
        job = GenerationJob(
            id=job_id,
            project_id=project_id,
            shot_id=shot_id,
            index=index,
            job_type="VIDEO",
            payload={"shot_id": shot_id, "prompt": prompt},
        )
        session.add(job)
        jobs.append(job)
    session.commit()
    for job in jobs:
        session.refresh(job)
    generation_queue.notify()
    return jobs


def select_and_lock_storyboard(
    session: Session, project_id: str, shot: Shot, sb_id: str
) -> None:
    for other in session.exec(
        select(Storyboard).where(
            Storyboard.project_id == project_id,
            Storyboard.shot_id == shot.id,
            Storyboard.status == "LOCKED",
        )
    ):
        other.status = "CANDIDATE"
        session.add(other)
    sb = session.get(Storyboard, sb_id)
    if sb is not None:
        sb.status = "LOCKED"
        session.add(sb)
    shot.storyboard_id = sb_id
    session.add(shot)
    session.commit()


async def wait_for_jobs(
    project_id: str, job_ids: list[str], timeout: float = 900.0, interval: float = 0.5
) -> dict[str, str]:
    """轮询直到全部任务进入终态，返回 {job_id: status}。"""
    deadline = asyncio.get_event_loop().time() + timeout
    statuses: dict[str, str] = {}
    while asyncio.get_event_loop().time() < deadline:
        with Session(get_engine(project_id)) as session:
            for job_id in job_ids:
                job = session.get(GenerationJob, job_id)
                if job is not None:
                    statuses[job_id] = job.status
        if all(statuses.get(j) in TERMINAL for j in job_ids):
            return statuses
        await asyncio.sleep(interval)
    for job_id in job_ids:
        statuses.setdefault(job_id, "TIMEOUT")
    return statuses


def takes_of_shot(session: Session, project_id: str, shot_id: str) -> list[Take]:
    return list(
        session.exec(
            select(Take)
            .where(Take.project_id == project_id, Take.shot_id == shot_id)
            .order_by(Take.index)
        )
    )
