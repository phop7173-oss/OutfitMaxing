"""Security helpers. Only primitives needed by the foundation; auth/RBAC arrive in Phase 6."""

import hmac


def secrets_match(provided: str | None, expected: str | None) -> bool:
    """Constant-time comparison. Missing values never match (used for webhook secrets later)."""
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided.encode(), expected.encode())
