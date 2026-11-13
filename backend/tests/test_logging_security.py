import json
import logging

from app.core.logging import JsonFormatter, redact
from app.core.security import secrets_match


def test_redacts_sensitive_keys_and_bot_tokens():
    out = redact({"api_key": "abc", "nested": {"Password": "x"}, "msg": "123456789:" + "A" * 30})
    assert out["api_key"] == "***"
    assert out["nested"]["Password"] == "***"
    assert out["msg"] == "***"


def test_json_formatter_emits_valid_json():
    rec = logging.LogRecord("t", logging.INFO, __file__, 1, "hello", None, None)
    assert json.loads(JsonFormatter().format(rec))["message"] == "hello"


def test_secrets_match():
    assert secrets_match("a", "a")
    assert not secrets_match("a", "b")
    assert not secrets_match(None, "a")
    assert not secrets_match("a", None)
    assert not secrets_match("", "")


# ---- Issue 1: secret leakage in logs --------------------------------------------------------
import io  # noqa: E402

from app.core.logging import (  # noqa: E402
    AccessLogQueryFilter,
    register_secret_values,
    safe_exception_summary,
)

FAKE_DB_PASSWORD = "Zq9-FAKE-db-pw-7731"
FAKE_API_KEY = "fake-api-key-AAAA1111BBBB"
FAKE_BOT_TOKEN = "123456789:FAKEfakeFAKEfakeFAKEfake12345"
UNKNOWN_FORMAT = "QWERTY-unknown-format-secret-9981"
ALL_FAKES = [FAKE_DB_PASSWORD, FAKE_API_KEY, FAKE_BOT_TOKEN, UNKNOWN_FORMAT]


def _capture_logger(name: str) -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    lg = logging.getLogger(name)
    lg.handlers = [handler]
    lg.propagate = False
    lg.setLevel(logging.DEBUG)
    return lg, stream


def _raise_with_secrets() -> None:
    try:
        raise ValueError(UNKNOWN_FORMAT)
    except ValueError as inner:
        raise RuntimeError(
            f"cannot connect postgresql://shop:{FAKE_DB_PASSWORD}@db:5432/shop "
            f"api_key={FAKE_API_KEY} bot={FAKE_BOT_TOKEN} {UNKNOWN_FORMAT}"
        ) from inner


def test_exception_messages_never_reach_logs_via_exc_info():
    lg, stream = _capture_logger("test.exc_info")
    try:
        _raise_with_secrets()
    except RuntimeError:
        lg.exception("something failed")
    out = stream.getvalue()
    for fake in ALL_FAKES:
        assert fake not in out
    assert "RuntimeError" in out and "ValueError" in out  # diagnostics preserved
    assert "_raise_with_secrets" in out  # code location preserved


def test_safe_exception_summary_has_no_message_text():
    try:
        _raise_with_secrets()
    except RuntimeError as exc:
        summary = json.dumps(safe_exception_summary(exc))
    for fake in ALL_FAKES:
        assert fake not in summary
    assert "builtins.RuntimeError" in summary


def test_redacts_url_credentials_bearer_and_key_value_pairs():
    text = (
        f"db postgresql://shop:{FAKE_DB_PASSWORD}@db/shop; Authorization: Bearer {FAKE_API_KEY}; "
        f"password={UNKNOWN_FORMAT}; TELEGRAM_BOT_TOKEN={FAKE_BOT_TOKEN}"
    )
    out = redact(text)
    for fake in ALL_FAKES:
        assert fake not in out
    assert "postgresql://shop:***@db/shop" in out


def test_redacts_well_known_key_formats():
    out = redact("keys AIza" + "A" * 35 + " and sk-" + "b" * 24)
    assert "AIza" not in out and "sk-b" not in out


def test_registered_secret_values_are_redacted_in_free_text():
    register_secret_values([UNKNOWN_FORMAT])
    try:
        assert UNKNOWN_FORMAT not in redact(f"value was {UNKNOWN_FORMAT} here")
    finally:
        register_secret_values([])


def test_unregistered_unknown_format_secret_in_free_text_is_not_guaranteed():
    # Documents the limitation: best-effort redaction cannot catch arbitrary secret formats
    # placed in ordinary log messages. This is why developers must not log secrets.
    register_secret_values([])
    assert UNKNOWN_FORMAT in redact(f"value was {UNKNOWN_FORMAT} here")


def test_access_log_filter_strips_query_string():
    rec = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d', ("1.2.3.4:5", "GET", f"/x?token={UNKNOWN_FORMAT}", "1.1", 200),
        None,
    )
    assert AccessLogQueryFilter().filter(rec)
    assert UNKNOWN_FORMAT not in rec.getMessage()
    assert "/x" in rec.getMessage()
