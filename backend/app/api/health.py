import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.logging import safe_exception_summary
from app.database.session import get_db

log = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
def liveness() -> dict[str, str]:
    """Process is up. Does not touch dependencies."""
    return {"status": "ok"}


@router.get("/health/ready")
def readiness(db: Session = Depends(get_db)) -> JSONResponse:  # noqa: B008
    """Database reachable."""
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        # Type and location only; the message could contain connection details.
        log.warning("readiness_db_check_failed", extra={"fields": safe_exception_summary(exc)})
        return JSONResponse(status_code=503, content={"status": "unavailable", "database": "down"})
    return JSONResponse(content={"status": "ok", "database": "up"})
