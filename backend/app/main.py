import logging
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

from app.api import health
from app.core.config import get_settings
from app.core.exceptions import internal_error_response, register_exception_handlers
from app.core.logging import configure_logging, request_id_var


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, settings.secret_values())
    app = FastAPI(title="OutfitMaxing API", version="0.1.0")

    @app.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        rid = uuid.uuid4().hex
        request.state.request_id = rid
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        except Exception as exc:
            # Handled here (not re-raised) so the server never logs the raw exception text.
            response = internal_error_response(request, exc)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        return response

    register_exception_handlers(app)
    app.include_router(health.router)
    logging.getLogger(__name__).info("app_started", extra={"fields": {"env": settings.app_env}})
    return app


app = create_app()
