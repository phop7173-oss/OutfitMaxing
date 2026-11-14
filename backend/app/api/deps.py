from typing import Annotated

from fastapi import Depends, Header

from app.core.config import Settings, get_settings
from app.core.exceptions import ServiceUnavailableError, UnauthorizedError
from app.core.security import secrets_match


def require_admin(
    settings: Annotated[Settings, Depends(get_settings)],
    x_admin_key: Annotated[str | None, Header()] = None,
) -> None:
    """Interim guard for write endpoints (replaced by login + roles in Phase 6).

    Fails closed: with no ADMIN_API_KEY configured the admin API is disabled."""
    if settings.admin_api_key is None:
        raise ServiceUnavailableError("Admin API is not configured")
    if not secrets_match(x_admin_key, settings.admin_api_key.get_secret_value()):
        raise UnauthorizedError("Missing or invalid admin key")
