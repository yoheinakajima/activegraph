"""Structured logging. CONTRACT v0.8 #6–#7, #16.

The framework emits structured logs through stdlib ``logging``. We do
NOT auto-configure on import — a library that does is hostile. By
default the framework attaches to ``logging.getLogger("activegraph")``
and lets the operator's config handle output.

If the operator wants the opinionated setup, ``configure_logging`` adds
a JSON-line handler with the documented schema.

The schema is the operator contract. Dashboards built against these
field names keep working across framework versions.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any, Callable, Optional


LOGGER_ROOT = "activegraph"

# The documented operator-facing log schema. Fields appear when
# applicable; fields that don't apply are omitted (not nulled).
LOG_FIELDS: tuple[str, ...] = (
    "timestamp",
    "level",
    "logger",
    "message",
    "run_id",
    "event_id",
    "behavior",
    "tool",
    "model",
    "cache_hit",
    "cost_usd",
    "latency_seconds",
    "reason",
    "error_type",
    "error_message",
    # v1.0.3 #3: behavior.failed WARNING log carries the More: URL
    # for the failure reason's documentation page. Operators tail
    # logs and click through to the reason's doc-page from the URL.
    "doc_url",
)

# Reserved attributes on every LogRecord (stdlib internals). Any
# `extra=` field that collides is renamed in extras-pickup logic below.
_RESERVED_RECORD_ATTRS = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "message", "module",
        "msecs", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "thread", "threadName",
        "taskName",  # 3.12+
    }
)


_payload_redactor_state: dict[str, Optional[Callable[[dict[str, Any]], dict[str, Any]]]] = {
    "fn": None
}


def set_payload_redactor(fn: Optional[Callable[[dict[str, Any]], dict[str, Any]]]) -> None:
    """Install a redactor that runs on any payload before it enters a log
    record's ``extra`` dict. Idempotent. Pass None to remove.
    """
    _payload_redactor_state["fn"] = fn


def redact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply the configured redactor (or identity)."""
    fn = _payload_redactor_state["fn"]
    return fn(payload) if fn is not None else payload


class JsonLineFormatter(logging.Formatter):
    """One JSON object per log record, on one line.

    Populates the documented LOG_FIELDS from the record's reserved
    attributes plus any ``extra=`` dict the caller passed. Omits fields
    that aren't present.
    """

    def __init__(self) -> None:
        super().__init__()

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "timestamp": _iso_utc(record.created),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Pluck extras: any record attribute not in the reserved set is
        # treated as a documented field. We only emit fields documented
        # in LOG_FIELDS so the schema is stable.
        for k in LOG_FIELDS:
            if k in out:
                continue
            v = getattr(record, k, _MISSING)
            if v is _MISSING or v is None:
                continue
            out[k] = v
        # Allow exc_info to flow into error_type / error_message even if
        # the caller didn't pass them explicitly.
        if record.exc_info and "error_type" not in out:
            etype, evalue, _ = record.exc_info
            if etype is not None:
                out["error_type"] = etype.__name__
            if evalue is not None and "error_message" not in out:
                out["error_message"] = str(evalue)
        return json.dumps(out, separators=(",", ":"), ensure_ascii=False)


class _MISSING_T:  # sentinel
    pass


_MISSING = _MISSING_T()


def _iso_utc(ts: float) -> str:
    # Avoid datetime import overhead per record.
    lt = time.gmtime(ts)
    ms = int((ts - int(ts)) * 1000)
    return (
        f"{lt.tm_year:04d}-{lt.tm_mon:02d}-{lt.tm_mday:02d}T"
        f"{lt.tm_hour:02d}:{lt.tm_min:02d}:{lt.tm_sec:02d}.{ms:03d}Z"
    )


def get_logger(name: str = LOGGER_ROOT) -> logging.Logger:
    """Get a logger in the activegraph namespace.

    ``get_logger("runtime")`` → ``logging.getLogger("activegraph.runtime")``.
    ``get_logger("activegraph")`` → root activegraph logger.
    """
    if name == LOGGER_ROOT or name.startswith(LOGGER_ROOT + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{LOGGER_ROOT}.{name}")


def runtime_log_extra(**fields: Any) -> dict[str, Any]:
    """Build an ``extra=`` dict for a log call, dropping None values and
    rejecting reserved LogRecord attribute names.

    Use:
        log.info("event emitted", extra=runtime_log_extra(
            run_id=rt.run_id, event_id=e.id, behavior=b.name,
        ))
    """
    out: dict[str, Any] = {}
    for k, v in fields.items():
        if v is None:
            continue
        if k in _RESERVED_RECORD_ATTRS:
            # Don't smash stdlib internals; prefix the key.
            out[f"ag_{k}"] = v
            continue
        out[k] = v
    return out


def configure_logging(
    level: str | int = "INFO",
    *,
    json_output: bool = True,
    stream: Any = None,
    payload_redactor: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None,
) -> logging.Logger:
    """Configure the activegraph logger hierarchy.

    Idempotent: repeated calls replace the existing handler rather than
    stacking. Returns the activegraph root logger.

    Args:
        level: numeric or string level name.
        json_output: True for the documented JSON-line format; False for
            the stdlib default (one human-readable line).
        stream: where to write. Defaults to stderr (the logging default).
        payload_redactor: optional callable(dict) -> dict applied to any
            payload before it's added to a log record's extra fields.
    """
    set_payload_redactor(payload_redactor)
    logger = logging.getLogger(LOGGER_ROOT)
    logger.setLevel(level)
    # Replace existing activegraph handlers so calls are idempotent.
    for h in list(logger.handlers):
        if getattr(h, "_activegraph", False):
            logger.removeHandler(h)
    handler = logging.StreamHandler(stream or sys.stderr)
    handler._activegraph = True  # type: ignore[attr-defined]
    handler.setFormatter(
        JsonLineFormatter()
        if json_output
        else logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        )
    )
    logger.addHandler(handler)
    # Don't propagate up to root if we own a handler — operators with
    # their own root handler would double-print.
    logger.propagate = False
    return logger
