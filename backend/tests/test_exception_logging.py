"""App-level checks that unexpected errors never put secrets in logs or responses."""

import io
import logging

import pytest

from app.core.logging import JsonFormatter
from app.database.session import get_db

FAKE_DB_PASSWORD = "Zq9-FAKE-db-pw-7731"
FAKE_API_KEY = "fake-api-key-AAAA1111BBBB"
FAKE_BOT_TOKEN = "123456789:FAKEfakeFAKEfakeFAKEfake12345"
UNKNOWN_FORMAT = "QWERTY-unknown-format-secret-9981"
ALL_FAKES = [FAKE_DB_PASSWORD, FAKE_API_KEY, FAKE_BOT_TOKEN, UNKNOWN_FORMAT]


def _leaky_error() -> RuntimeError:
    return RuntimeError(
        f"cannot connect postgresql://shop:{FAKE_DB_PASSWORD}@db:5432/shop "
        f"api_key={FAKE_API_KEY} bot={FAKE_BOT_TOKEN} {UNKNOWN_FORMAT}"
    )


@pytest.fixture
def log_output(client):
    """Capture everything the app logs, formatted by our JSON formatter.

    Depends on `client` so the app (which configures logging) exists before we attach."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    yield stream
    root.removeHandler(handler)


def test_unhandled_exception_does_not_leak_secrets_to_logs_or_response(client, log_output):
    def boom():
        raise _leaky_error()

    client.app.add_api_route("/boom", boom)
    r = client.get(f"/boom?secret={UNKNOWN_FORMAT}")
    out = log_output.getvalue()

    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error"
    assert r.json()["error"]["request_id"] == r.headers["X-Request-ID"]
    for fake in ALL_FAKES:
        assert fake not in r.text
        assert fake not in out  # message, traceback text and query string are all absent
    # useful diagnostics are still there
    assert "unhandled_error" in out
    assert "RuntimeError" in out
    assert "/boom" in out
    assert r.headers["X-Request-ID"] in out


def test_readiness_failure_logs_type_not_message(client, log_output):
    class BrokenSession:
        def execute(self, *_a, **_k):
            raise _leaky_error()

        def close(self):
            pass

    client.app.dependency_overrides[get_db] = lambda: BrokenSession()
    r = client.get("/health/ready")
    out = log_output.getvalue()

    assert r.status_code == 503
    for fake in ALL_FAKES:
        assert fake not in r.text
        assert fake not in out
    assert "readiness_db_check_failed" in out
    assert "RuntimeError" in out


def test_uvicorn_loggers_are_routed_through_the_redacting_formatter(client, log_output):
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        assert lg.handlers == []
        assert lg.propagate is True
    try:
        raise _leaky_error()
    except RuntimeError:
        # what uvicorn does with an exception that propagates out of the app
        logging.getLogger("uvicorn.error").exception("Exception in ASGI application")
    out = log_output.getvalue()
    for fake in ALL_FAKES:
        assert fake not in out
    assert "RuntimeError" in out
