"""Application errors and a consistent, non-leaking error envelope."""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.logging import safe_exception_summary

log = logging.getLogger(__name__)


class AppError(Exception):
    status_code = 400
    code = "app_error"

    def __init__(self, message: str = "Request could not be processed") -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


def _envelope(request: Request, code: str, message: str, status: int) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "request_id": request_id}},
    )


def internal_error_response(request: Request, exc: Exception) -> JSONResponse:
    """Log an unexpected error WITHOUT its message or traceback text, and return a generic 500.

    Deliberately not log.exception(): exception messages (database URLs, keys, tokens, personal
    data) must not reach logs. We keep the exception type chain, code locations, HTTP method and
    path (no query string) and the request id (added by the formatter).
    """
    log.error(
        "unhandled_error",
        extra={
            "fields": {
                "method": request.method,
                "path": request.url.path,
                **safe_exception_summary(exc),
            }
        },
    )
    return _envelope(request, "internal_error", "Internal server error", 500)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        return _envelope(request, exc.code, exc.message, exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        return internal_error_response(request, exc)
