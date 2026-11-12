"""Application logging.

* Production (AUTH_ENABLED=true) logs one JSON object per line, which Render's log search and any log drain can filter
  (`request_id`, `user_id`, `match_id`, `status`, ...). Local development logs readable text. Override with LOG_FORMAT.
* Every log line is scrubbed for secrets (bearer tokens, JWTs, Stripe and Groq keys, passwords in URLs, email addresses)
  before it is written, as a second line of defence. The code also never passes such values to the logger in the first place:
  no prompts, no model output, no email addresses, no query strings.
* Each HTTP request gets a request id (accepted from X-Request-ID or generated), returned in the X-Request-ID response header
  and attached to every log line written while handling it, including the background analysis it starts.
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Optional, TextIO

request_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("request_id", default=None)

_HANDLER_FLAG = "_football_ai_handler"
_LOGGERS = ("football_ai", "psycopg")  # our code and the database pool; httpx/httpcore stay quiet (they log full URLs)

_STANDARD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime", "taskName", "request_id"}

_REDACTIONS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [redacted]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"), "[jwt]"),
    (re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]+"), "[stripe-key]"),
    (re.compile(r"\bwhsec_[A-Za-z0-9]+"), "[stripe-webhook-secret]"),
    (re.compile(r"\bgsk_[A-Za-z0-9]+"), "[groq-key]"),
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://[^:/\s@]+):[^@\s]+@", re.I), r"\1:[redacted]@"),
    (re.compile(r"(?i)\b(api[_-]?key|x-auth-token|authorization|secret|password|passwd|access[_-]?token|refresh[_-]?token)(\"?\s*[:=]\s*\"?)[^\s\"',;&}]+"), r"\1\2[redacted]"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"), "[email]"),
]


def redact(text: str) -> str:
    for pattern, repl in _REDACTIONS:
        text = pattern.sub(repl, text)
    return text


def _clean(value):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    return redact(str(value))


def _fields(record: logging.LogRecord) -> dict:
    out = {k: _clean(v) for k, v in record.__dict__.items() if k not in _STANDARD and not k.startswith("_")}
    rid = request_id_var.get()
    if rid:
        out = {"request_id": rid, **out}
    return out


class _Base(logging.Formatter):
    def _exc(self, record: logging.LogRecord) -> Optional[str]:
        return redact(self.formatException(record.exc_info)) if record.exc_info else None


class JsonFormatter(_Base):
    def format(self, record: logging.LogRecord) -> str:
        data = {"ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
                "level": record.levelname, "logger": record.name, "msg": redact(record.getMessage()), **_fields(record)}
        exc = self._exc(record)
        if exc:
            data["exc"] = exc
        return json.dumps(data, default=str, ensure_ascii=False)


class TextFormatter(_Base):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        extras = " ".join(f"{k}={v}" for k, v in _fields(record).items())
        line = f"{ts} {record.levelname:<7} {record.name}: {redact(record.getMessage())}" + (f"  [{extras}]" if extras else "")
        exc = self._exc(record)
        return f"{line}\n{exc}" if exc else line


def setup_logging(level: str = "INFO", fmt: str = "text", stream: Optional[TextIO] = None) -> None:
    """Idempotent: replaces the handler installed by an earlier call (create_app runs once per app, and tests build many)."""
    lvl = logging.getLevelName(level.upper()) if isinstance(level, str) else level
    if not isinstance(lvl, int):
        lvl = logging.INFO
    handler = logging.StreamHandler(stream or sys.stderr)
    setattr(handler, _HANDLER_FLAG, True)
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    for name in _LOGGERS:
        lg = logging.getLogger(name)
        for h in [h for h in lg.handlers if getattr(h, _HANDLER_FLAG, False)]:
            lg.removeHandler(h)
        lg.addHandler(handler)
        lg.setLevel(lvl if name == "football_ai" else max(lvl, logging.WARNING))
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_RID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def clean_request_id(value: Optional[str]) -> Optional[str]:
    """Accept a caller-supplied request id only if it is plain and short, so it cannot inject anything into the logs."""
    return value if value and _RID.match(value) else None
