from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.modules.notifications.models import OutboxEvent


def emit(
    session: Session, topic: str, payload: dict[str, Any], *, dedupe_key: str | None = None
) -> None:
    """Queue an event in the caller's transaction (the caller commits). A repeated dedupe_key is
    ignored, so a retried or repeated operation cannot notify twice."""
    stmt = pg_insert(OutboxEvent).values(topic=topic, payload=payload, dedupe_key=dedupe_key)
    if dedupe_key is not None:
        stmt = stmt.on_conflict_do_nothing(index_elements=["dedupe_key"])
    session.execute(stmt)
