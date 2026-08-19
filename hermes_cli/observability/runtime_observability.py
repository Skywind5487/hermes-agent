"""Residual runtime diagnostics on top of current upstream observability.

Current upstream already owns two parts of the historical fork capability:

* lifecycle/service-health signals live in ``agent.monitoring`` and bounded
  task lifecycle metrics live in Relay shared metrics;
* bounded tool outcome/latency/retry metrics live in Relay shared metrics.

This module therefore does *not* create a second lifecycle/tool trajectory.
It fills only the residual operator-diagnostic gap for stream, SQLite/session
persistence, and delivery failures by projecting a tiny content-free fact set
onto the existing ``agent.monitoring`` diagnostic sink.

The process-lifetime log adapter is installed when ``hermes_cli`` is imported,
not on the first session. Installation itself is dormant and does not read
configuration or emit anything. Exact matched facts consult the opt-in config;
when enabled they use the monitoring emitter's explicit pre-subscriber event
path so startup diagnostics survive until the exporter attaches without
turning on unrelated monitoring producers.

The contract is intentionally closed: no prompts/messages/streamed text, tool
arguments/results, raw errors, IDs, filesystem paths, provider responses,
per-token stream events, or SQLite implementation counters.
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

# Public stable residual event contract. These are diagnosable domain facts,
# not an inventory of implementation branches.
STABLE_FACTS: dict[str, frozenset[str]] = {
    "stream": frozenset({"failed"}),
    "sqlite": frozenset({
        "persistence_failed",
        "connection_recovered",
        "connection_recovery_failed",
    }),
    "delivery": frozenset({
        "retry_succeeded",
        "retries_exhausted",
        "fallback_failed",
    }),
}

# Freeze the current public persistence buckets into this telemetry contract.
# If upstream adds a new classifier result it degrades to "unknown" until this
# contract is deliberately reviewed; observability must not widen silently.
_SQLITE_ERROR_CODES = frozenset({
    "locked",
    "compression",
    "compression_closed",
    "turn_lease",
    "corrupt",
    "disk",
    "unknown",
})

# Fixed error classes only. Never use exception class names or exception text
# here: those can contain provider strings, paths, or user-controlled content.
_ERROR_CLASS = {
    ("stream", "failed"): "stream_failure",
    ("sqlite", "persistence_failed"): "persistence_failure",
    ("sqlite", "connection_recovered"): "none",
    ("sqlite", "connection_recovery_failed"): "connection_recovery_failure",
    ("delivery", "retry_succeeded"): "none",
    ("delivery", "retries_exhausted"): "delivery_failure",
    ("delivery", "fallback_failed"): "delivery_failure",
}

_SEVERITY = {
    ("stream", "failed"): "error",
    ("sqlite", "persistence_failed"): "error",
    ("sqlite", "connection_recovered"): "info",
    ("sqlite", "connection_recovery_failed"): "error",
    ("delivery", "retry_succeeded"): "info",
    ("delivery", "retries_exhausted"): "error",
    ("delivery", "fallback_failed"): "error",
}


def enabled() -> bool:
    """Return whether the optional residual runtime diagnostics are enabled."""
    try:
        from hermes_cli.config import read_raw_config_readonly

        config = read_raw_config_readonly()
        monitoring = config.get("monitoring") if isinstance(config, dict) else None
        runtime = (
            monitoring.get("runtime_observability")
            if isinstance(monitoring, dict)
            else None
        )
        return bool(runtime.get("enabled", False)) if isinstance(runtime, dict) else False
    except Exception:
        # Configuration/telemetry failures can never change runtime behavior.
        return False


def _emit_fact(
    subsystem: str,
    fact: str,
    *,
    error_code: str | None = None,
) -> None:
    """Emit one validated, content-free residual runtime fact. Never raises."""
    try:
        if fact not in STABLE_FACTS.get(subsystem, frozenset()):
            return
        if error_code is not None:
            if subsystem != "sqlite" or error_code not in _SQLITE_ERROR_CODES:
                error_code = "unknown" if subsystem == "sqlite" else None

        from agent.monitoring.emitter import get_emitter
        from agent.monitoring.events import GatewayDiagnosticEvent

        event = GatewayDiagnosticEvent(
            name=f"runtime.{subsystem}.{fact}",
            subsystem=subsystem,
            error_class=_ERROR_CLASS[(subsystem, fact)],
            error_code=error_code,
            severity=_SEVERITY[(subsystem, fact)],
        )
        # The producer is process-lifetime, while the exporter subscribes later
        # during gateway startup. Queue THIS already-opted-in residual event in
        # the existing bounded emitter even before that subscriber exists.
        # Unlike ordinary emit(), this does not enable unrelated producers.
        get_emitter().emit_buffered(event)
    except Exception:
        # The observed operation always wins over observability.
        logger.debug("runtime observability emit failed", exc_info=True)


def _classify_persistence_record(record: logging.LogRecord) -> str:
    """Return one frozen coarse persistence bucket without exporting the error."""
    try:
        args = record.args
        if not isinstance(args, tuple) or len(args) < 2:
            return "unknown"
        from hermes_state import classify_persistence_error

        cause = classify_persistence_error(args[1])
        return cause if cause in _SQLITE_ERROR_CODES else "unknown"
    except Exception:
        return "unknown"


# These residual domains currently expose stable, code-owned terminal/failure
# log templates rather than lifecycle hooks. Normalize only the STATIC
# template in LogRecord.msg. Stream/delivery never inspect interpolation args.
# The one SQLite persistence mapping passes only its exception object through
# the existing bounded classifier and exports only the frozen cause bucket.
#
# Maintenance warning: these templates are intentionally fail-closed. Wording
# drift drops a structured fact rather than widening the schema by fuzzy
# parsing. When an upstream log template changes, this explicit table must be
# reviewed and updated with the emitting runtime boundary.
_LOG_FACTS: dict[tuple[str, str], tuple[str, str]] = {
    (
        "gateway.stream_consumer",
        "Stream consumer error: %s",
    ): ("stream", "failed"),
    (
        "agent.tool_executor",
        "Incremental tool-call persistence failed after %s: %s",
    ): ("sqlite", "persistence_failed"),
    (
        "hermes_state",
        "state.db connection reopened successfully; retrying the failed write.",
    ): ("sqlite", "connection_recovered"),
    (
        "hermes_state",
        "state.db reconnect after 'file is not a database' failed (%s); the database may need the full offline repair path.",
    ): ("sqlite", "connection_recovery_failed"),
    (
        "gateway.platforms.base",
        "[%s] Send succeeded on retry %d",
    ): ("delivery", "retry_succeeded"),
    (
        "gateway.platforms.base",
        "[%s] Failed to deliver response after %d retries: %s",
    ): ("delivery", "retries_exhausted"),
    (
        "gateway.platforms.base",
        "[%s] Fallback send also failed: %s",
    ): ("delivery", "fallback_failed"),
}
_PERSISTENCE_LOG_KEY = (
    "agent.tool_executor",
    "Incremental tool-call persistence failed after %s: %s",
)
_LOGGER_NAMES = frozenset(name for name, _template in _LOG_FACTS)
_LOG_HANDLER_MARKER = "_hermes_runtime_observability_handler"
_log_observer_lock = threading.Lock()


class _RuntimeDiagnosticLogHandler(logging.Handler):
    """Normalize selected static runtime log templates into bounded facts."""

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401 - logging API
        try:
            if not isinstance(record.msg, str):
                return
            key = (record.name, record.msg)
            mapped = _LOG_FACTS.get(key)
            if mapped is None:
                return
            # Installation is process-lifetime and intentionally dormant when
            # disabled. Check enablement only after an exact template match so
            # unrelated logs pay no config-read cost.
            if not enabled():
                return
            if key == _PERSISTENCE_LOG_KEY:
                _emit_fact(*mapped, error_code=_classify_persistence_record(record))
            else:
                _emit_fact(*mapped)
        except Exception:
            # logging.Handler.handle() does not protect callers from arbitrary
            # handler exceptions; fail-open explicitly at this boundary.
            return


def install() -> None:
    """Attach the dormant residual log normalizer for the process lifetime.

    Called from ``hermes_cli.__init__`` so the boundary exists before SessionDB,
    stream, or delivery runtime construction. This function deliberately does
    not consult configuration: disabled installs are inert, and exact matched
    records perform the opt-in check at emit time.
    """
    with _log_observer_lock:
        for name in _LOGGER_NAMES:
            target = logging.getLogger(name)
            if any(getattr(h, _LOG_HANDLER_MARKER, False) for h in target.handlers):
                continue
            handler = _RuntimeDiagnosticLogHandler()
            setattr(handler, _LOG_HANDLER_MARKER, True)
            target.addHandler(handler)


def reset_for_tests() -> None:
    """Remove installed handlers. Test-only helper."""
    with _log_observer_lock:
        for name in _LOGGER_NAMES:
            target = logging.getLogger(name)
            for handler in list(target.handlers):
                if getattr(handler, _LOG_HANDLER_MARKER, False):
                    target.removeHandler(handler)
                    try:
                        handler.close()
                    except Exception:
                        pass


__all__ = [
    "STABLE_FACTS",
    "enabled",
    "install",
    "reset_for_tests",
]
