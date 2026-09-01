from sqlmodel import Session, func, select

from ..domain import Scene, Shot, Take, TimelineClip


def _next_seq(session: Session, model, project_id: str) -> int:
    stmt = (
        select(func.max(model.index))
        .where(model.project_id == project_id)  # type: ignore[attr-defined]
    )
    current = session.exec(stmt).one()
    return (current or 0) + 1


def next_seq_and_id(
    session: Session, model, project_id: str, prefix: str
) -> tuple[int, str]:
    n = _next_seq(session, model, project_id)
    return n, f"{prefix}_{n:03d}"


__all__ = ["next_seq_and_id", "Scene", "Shot", "Take", "TimelineClip"]
