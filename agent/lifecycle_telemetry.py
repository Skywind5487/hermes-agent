"""Low-volume, payload-free lifecycle telemetry for end-to-end latency tracing."""

from __future__ import annotations

import contextvars
import json
import logging
import time
from contextlib import contextmanager
from typing import Any, Iterator

logger = logging.getLogger("hermes.lifecycle")

# Keep this allow-list deliberately small.  In particular, request/response
# bodies and tool arguments/results must never enter the normal log stream.
_LIFECYCLE_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "hermes_lifecycle_context", default={}
)


def get_lifecycle_context() -> dict[str, Any]:
    """Return the current scalar correlation context as a defensive copy."""
    return dict(_LIFECYCLE_CONTEXT.get() or {})


@contextmanager
def lifecycle_context(**fields: Any) -> Iterator[None]:
    """Temporarily bind correlation IDs for nested tool/DB operations.

    This carries metadata only; prompts, tool arguments/results, and response
    bodies are intentionally not accepted as a separate payload channel.
    """
    parent = get_lifecycle_context()
    parent.update({key: value for key, value in fields.items() if value is not None})
    token = _LIFECYCLE_CONTEXT.set(parent)
    try:
        yield
    finally:
        _LIFECYCLE_CONTEXT.reset(token)


_ALLOWED_FIELDS = {
    "trace_id",
    "span_id",
    "parent_span_id",
    "session_id",
    "turn_id",
    "task_id",
    "api_request_id",
    "tool_call_id",
    "operation_kind",
    "operation_name",
    "function_name",
    "phase",
    "status",
    "reason",
    "error_type",
    "retry_count",
    "duration_ms",
    "chunk_count",
    "char_count",
    "byte_count",
    "message_id",
    "message_kind",
    "platform",
    "chat_id",
    "station",
}


def emit_lifecycle(event: str, **fields: Any) -> None:
    """Emit one JSON lifecycle record without untrusted payloads.

    Logging failures are swallowed: observability must not break the agent
    request it observes.  Values are restricted to scalar JSON-compatible
    types and to the explicit allow-list above.
    """
    payload: dict[str, Any] = {
        "event": str(event),
        "wall_time_ns": time.time_ns(),
        "monotonic_ns": time.monotonic_ns(),
    }
    for key, value in fields.items():
        if key not in _ALLOWED_FIELDS or value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            payload[key] = value
    try:
        logger.info("HERMES_LIFECYCLE %s", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    except Exception:
        logger.debug("lifecycle telemetry emission failed", exc_info=True)
