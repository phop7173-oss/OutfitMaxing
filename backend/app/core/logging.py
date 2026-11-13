"""Structured JSON logging.

What this module ENFORCES (by construction, not by pattern matching):
  * Exception messages are never written to logs. For an exception we log only the type chain
    and the traceback locations (file, line, function). No message text, no local variables.
  * Query strings are stripped from uvicorn access-log lines.
  * uvicorn's own loggers are routed through this formatter, so an exception that uvicorn logs
    after our handler has responded is sanitised the same way.

What this module does on a BEST-EFFORT basis (NOT guaranteed):
  * Redacting secrets that appear inside ordinary log messages or extra fields: values of the
    configured secrets (registered at start-up), credentials in URLs, bearer tokens,
    key=value pairs with sensitive names, and a few well-known key formats. A secret in an
    unknown format that someone interpolates into a log message will NOT be caught.
Rule for developers: never put secrets or personal data into log messages in the first place.
"""

import contextvars
import json
import logging
import re
import sys
import traceback
from collections.abc import Iterable
from datetime import UTC, datetime

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)

SENSITIVE_KEYS = ("token", "secret", "password", "api_key", "authorization")

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Telegram bot token
    (re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b"), "***"),
    # credentials embedded in URLs: scheme://user:password@host
    (re.compile(r"(\b[a-zA-Z][a-zA-Z0-9+.-]*://)([^/\s:@]*):([^@\s/]+)@"), r"\1\2:***@"),
    # bearer tokens
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer ***"),
    # key=value / key: value / "key": "value" with a sensitive key name
    (
        re.compile(
            r"(?i)(password|passwd|pwd|secret|token|api[_-]?key|authorization)"
            r"([\"']?\s*[=:]\s*)([\"']?)[^\s\"',;&]+"
        ),
        r"\1\2\3***",
    ),
    # Google API key format, generic sk- style keys
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"), "***"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "***"),
]

_known_secrets: tuple[str, ...] = ()


def register_secret_values(values: Iterable[str]) -> None:
    """Register exact secret values (replaces any earlier registration). Short values are ignored
    to avoid mangling ordinary text."""
    global _known_secrets
    cleaned = {v for v in values if isinstance(v, str) and len(v) >= 6}
    _known_secrets = tuple(sorted(cleaned, key=len, reverse=True))


def _redact_text(text: str) -> str:
    for secret in _known_secrets:
        text = text.replace(secret, "***")
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


def redact(value: object) -> object:
    if isinstance(value, dict):
        return {
            k: "***" if any(s in str(k).lower() for s in SENSITIVE_KEYS) else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _type_name(exc: BaseException) -> str:
    t = type(exc)
    return f"{t.__module__}.{t.__qualname__}"


def safe_exception_summary(exc: BaseException, max_frames: int = 15) -> dict[str, object]:
    """Diagnostics that cannot contain secrets from the exception text: types and code locations
    only. The exception message and local variables are deliberately omitted."""
    chain: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen and len(chain) < 5:
        seen.add(id(current))
        chain.append(_type_name(current))
        current = current.__cause__ or current.__context__
    frames = [
        f"{fs.filename}:{fs.lineno} in {fs.name}"
        for fs in traceback.extract_tb(exc.__traceback__)[-max_frames:]
    ]
    return {"error_type": chain[0], "error_chain": chain, "frames": frames}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
            "request_id": request_id_var.get(),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            payload.update(redact(extra))  # type: ignore[arg-type]
        if record.exc_info and record.exc_info[1] is not None:
            # Never format the exception text: type chain and locations only.
            payload.update(safe_exception_summary(record.exc_info[1]))
        return json.dumps(payload, default=str)


class AccessLogQueryFilter(logging.Filter):
    """Strip query strings (may carry tokens or personal data) from uvicorn access lines."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            record.args = (*args[:2], args[2].split("?", 1)[0], *args[3:])
        return True


def configure_logging(level: str = "INFO", secrets: Iterable[str] = ()) -> None:
    register_secret_values(secrets)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # uvicorn installs its own handlers (plain-text formatting, no redaction). Route its loggers
    # through ours; this runs after uvicorn's setup because the app module is imported later.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
    logging.getLogger("uvicorn.access").filters = [AccessLogQueryFilter()]
