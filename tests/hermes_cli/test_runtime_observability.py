from __future__ import annotations

import ast
import importlib
import json
import logging
from pathlib import Path

import pytest

from hermes_cli.observability import runtime_observability as runtime_obs


@pytest.fixture(autouse=True)
def _clean_runtime_observability():
    from agent.monitoring.emitter import reset_emitter_for_tests

    runtime_obs.reset_for_tests()
    reset_emitter_for_tests()
    yield
    runtime_obs.reset_for_tests()
    reset_emitter_for_tests()


def _capture_events(monkeypatch):
    captured = []
    monkeypatch.setattr(runtime_obs, "enabled", lambda: True)

    import agent.monitoring.emitter as emitter_module

    class _CaptureEmitter:
        def emit_buffered(self, event):
            captured.append(event)

    monkeypatch.setattr(emitter_module, "get_emitter", lambda: _CaptureEmitter())
    return captured


def _event_dicts(events):
    return [event.to_dict() for event in events]


def _literal_log_templates(path: Path) -> set[str]:
    """Return literal first arguments of logger calls in one source file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    templates: set[str] = set()
    log_methods = {"debug", "info", "warning", "error", "critical", "exception"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr not in log_methods:
            continue
        try:
            value = ast.literal_eval(node.args[0])
        except (ValueError, TypeError):
            continue
        if isinstance(value, str):
            templates.add(value)
    return templates


def test_stable_facts_cover_residual_domains_only():
    assert set(runtime_obs.STABLE_FACTS) == {"stream", "sqlite", "delivery"}
    assert all(runtime_obs.STABLE_FACTS.values())


def test_upstream_relay_remains_the_tool_lifecycle_owner():
    from hermes_cli.observability import relay_shared_metrics

    assert "on_session_start" in relay_shared_metrics.HANDLED_HOOKS
    assert "post_tool_call" in relay_shared_metrics.HANDLED_HOOKS
    assert not hasattr(runtime_obs, "HANDLED_HOOKS")


def test_mapped_static_templates_are_anchored_to_current_emit_sites():
    """Turn exact-log wording drift into a reviewable CI failure.

    Runtime matching stays fail-closed: it never fuzzy-parses rendered logs.
    This test makes the maintenance liability explicit by proving every mapped
    literal still exists at the code-owned logger call that defines the fact.
    """
    repo_root = Path(__file__).resolve().parents[2]
    source_for_logger = {
        "gateway.stream_consumer": repo_root / "gateway" / "stream_consumer.py",
        "agent.tool_executor": repo_root / "agent" / "tool_executor.py",
        "hermes_state": repo_root / "hermes_state.py",
        "gateway.platforms.base": repo_root / "gateway" / "platforms" / "base.py",
    }
    templates_by_logger = {
        logger_name: _literal_log_templates(path)
        for logger_name, path in source_for_logger.items()
    }

    for (logger_name, template), _fact in runtime_obs._LOG_FACTS.items():
        assert template in templates_by_logger[logger_name], (
            f"runtime observability mapping drifted from {source_for_logger[logger_name]}: "
            f"{template!r}"
        )


def test_hermes_cli_import_installs_process_lifetime_boundary(monkeypatch):
    import hermes_cli

    runtime_obs.reset_for_tests()

    # Installation must not depend on feature enablement or config I/O. The
    # handler exists for the process lifetime and remains dormant when disabled.
    monkeypatch.setattr(
        runtime_obs,
        "enabled",
        lambda: (_ for _ in ()).throw(AssertionError("install must not read config")),
    )
    importlib.reload(hermes_cli)

    for name in runtime_obs._LOGGER_NAMES:
        assert any(
            getattr(handler, runtime_obs._LOG_HANDLER_MARKER, False)
            for handler in logging.getLogger(name).handlers
        )


def test_disabled_process_lifetime_observer_is_dormant(monkeypatch):
    runtime_obs.install()
    monkeypatch.setattr(runtime_obs, "enabled", lambda: False)

    logging.getLogger("gateway.stream_consumer").handle(logging.LogRecord(
        "gateway.stream_consumer",
        logging.ERROR,
        __file__,
        1,
        "Stream consumer error: %s",
        ("secret streamed text",),
        None,
    ))

    from agent.monitoring.emitter import get_emitter

    assert get_emitter().stats()["queued"] == 0
    # Disabled means dormant, not absent: the observation boundary is already
    # installed and can become active without waiting for a session hook.
    for name in runtime_obs._LOGGER_NAMES:
        assert any(
            getattr(handler, runtime_obs._LOG_HANDLER_MARKER, False)
            for handler in logging.getLogger(name).handlers
        )


def test_startup_fact_survives_multi_sink_subscribe_order(monkeypatch):
    """Regression for #114 review: no bootstrap lifetime hole across sinks.

    Gateway startup attaches the span streamer (filter: gateway_health /
    cron_execution) before the diagnostic-log streamer. A residual diagnostic
    buffered before either subscriber must survive that gap and reach the
    diagnostic sink after the full fan-out attaches — subscribe() must not
    start dispatch into a partial subscriber set.
    """
    from agent.monitoring.emitter import MonitoringEmitter, reset_emitter_for_tests

    emitter = MonitoringEmitter(enabled=False)
    reset_emitter_for_tests(emitter)
    monkeypatch.setattr(runtime_obs, "enabled", lambda: True)
    runtime_obs.install()

    # Model a startup SQLite reconnect failure before gateway OTLP setup has
    # attached any subscriber. Use the installed logger path, not the handler
    # directly, so this exercises the process-lifetime bootstrap contract.
    logging.getLogger("hermes_state").handle(logging.LogRecord(
        "hermes_state",
        logging.ERROR,
        __file__,
        1,
        "state.db reconnect after 'file is not a database' failed (%s); the database may need the full offline repair path.",
        ("private /home/user/state.db detail",),
        None,
    ))

    assert emitter.stats() == {
        "queued": 1,
        "dispatched": 0,
        "dropped": 0,
        "subscribers": 0,
    }

    span_received: list[dict] = []
    diag_received: list[dict] = []

    def _span_subscriber(batch):
        # Mirrors otlp_exporter.start_streaming(event_filter=_gateway_health_event).
        for ev in batch:
            if ev.get("event") in {"gateway_health", "cron_execution"}:
                span_received.append(ev)

    def _diag_subscriber(batch):
        # Mirrors GatewayDiagnosticLogStreamer.__call__.
        for ev in batch:
            if ev.get("event") == "gateway_diagnostic":
                diag_received.append(ev)

    # Span streamer attaches first with a non-empty queue. subscribe() must not
    # start dispatch here — the residual must survive until the full fan-out.
    emitter.subscribe(_span_subscriber)
    assert emitter._started is False
    assert emitter.stats()["queued"] == 1

    # Diagnostic streamer attaches; the full fan-out is now in place.
    emitter.subscribe(_diag_subscriber)

    # The ordinary initial snapshot emitted after both subscribers attach
    # starts dispatch and drains the buffered residual through both sinks.
    emitter.emit({"event": "gateway_health", "name": "gateway.health_snapshot"})
    emitter.flush(timeout=1.0)

    assert [ev["name"] for ev in diag_received] == [
        "runtime.sqlite.connection_recovery_failed"
    ]
    assert [ev["name"] for ev in span_received] == ["gateway.health_snapshot"]
    # Sensitive startup detail never crosses the event boundary.
    assert "private /home/user/state.db detail" not in json.dumps(diag_received)
    assert emitter.stats()["queued"] == 0
    assert emitter.stats()["dispatched"] == 2
    emitter.close()


def test_pre_subscriber_buffer_does_not_enable_unrelated_producers():
    from agent.monitoring.emitter import MonitoringEmitter

    emitter = MonitoringEmitter(enabled=False)
    try:
        # Normal monitoring semantics stay untouched while no exporter exists.
        emitter.emit({"event": "gateway_diagnostic", "name": "ordinary"})
        assert emitter.stats()["queued"] == 0

        # Only an explicitly buffered, already-opted-in producer can cross the
        # pre-subscriber lifetime boundary.
        emitter.emit_buffered({"event": "gateway_diagnostic", "name": "early"})
        assert emitter.stats()["queued"] == 1
        assert emitter.stats()["dispatched"] == 0
        assert emitter._started is False

        # Another ordinary producer is still disabled; emit_buffered did not
        # mutate global plane enablement.
        emitter.emit({"event": "gateway_diagnostic", "name": "ordinary-2"})
        assert emitter.stats()["queued"] == 1

        delivered = []
        emitter.subscribe(delivered.extend)
        # subscribe() must not start dispatch on buffered events; the first
        # ordinary emit after the full subscriber set attaches does (mirrors
        # the gateway snapshot emit after both streamers attach).
        assert emitter._started is False
        emitter.emit({"event": "gateway_health", "name": "snapshot"})
        emitter.flush(timeout=1.0)

        assert [item["name"] for item in delivered] == ["early", "snapshot"]
        assert "ordinary-2" not in [item["name"] for item in delivered]
        assert emitter.stats()["queued"] == 0
        assert emitter.stats()["dispatched"] == 2
    finally:
        emitter.close()


def test_static_log_normalizer_covers_stream_sqlite_and_delivery(monkeypatch):
    captured = _capture_events(monkeypatch)
    handler = runtime_obs._RuntimeDiagnosticLogHandler()

    records = [
        logging.LogRecord(
            "gateway.stream_consumer",
            logging.ERROR,
            __file__,
            1,
            "Stream consumer error: %s",
            ("secret streamed text",),
            None,
        ),
        logging.LogRecord(
            "agent.tool_executor",
            logging.WARNING,
            __file__,
            1,
            "Incremental tool-call persistence failed after %s: %s",
            ("secret-stage", "database is locked at /secret/state.db"),
            None,
        ),
        logging.LogRecord(
            "gateway.platforms.base",
            logging.ERROR,
            __file__,
            1,
            "[%s] Failed to deliver response after %d retries: %s",
            ("telegram-secret-chat", 2, "secret provider response"),
            None,
        ),
    ]
    for record in records:
        handler.emit(record)

    assert [event.name for event in captured] == [
        "runtime.stream.failed",
        "runtime.sqlite.persistence_failed",
        "runtime.delivery.retries_exhausted",
    ]
    assert captured[1].error_code == "locked"
    payload = json.dumps(_event_dicts(captured), sort_keys=True)
    for secret in (
        "secret streamed text",
        "secret-stage",
        "/secret/state.db",
        "telegram-secret-chat",
        "secret provider response",
    ):
        assert secret not in payload


def test_persistence_classifier_is_closed_to_frozen_codes(monkeypatch):
    captured = _capture_events(monkeypatch)
    monkeypatch.setattr(
        runtime_obs,
        "_classify_persistence_record",
        lambda _record: "future_unreviewed_bucket",
    )
    handler = runtime_obs._RuntimeDiagnosticLogHandler()

    handler.emit(logging.LogRecord(
        "agent.tool_executor",
        logging.WARNING,
        __file__,
        1,
        "Incremental tool-call persistence failed after %s: %s",
        ("stage", RuntimeError("private")),
        None,
    ))

    assert len(captured) == 1
    assert captured[0].error_code == "unknown"


def test_recovery_and_retry_success_are_bounded_facts(monkeypatch):
    captured = _capture_events(monkeypatch)
    handler = runtime_obs._RuntimeDiagnosticLogHandler()

    handler.emit(logging.LogRecord(
        "hermes_state",
        logging.WARNING,
        __file__,
        1,
        "state.db connection reopened successfully; retrying the failed write.",
        (),
        None,
    ))
    handler.emit(logging.LogRecord(
        "gateway.platforms.base",
        logging.INFO,
        __file__,
        1,
        "[%s] Send succeeded on retry %d",
        ("private-platform-instance", 1),
        None,
    ))

    assert [event.name for event in captured] == [
        "runtime.sqlite.connection_recovered",
        "runtime.delivery.retry_succeeded",
    ]
    payload = json.dumps(_event_dicts(captured), sort_keys=True)
    assert "private-platform-instance" not in payload


def test_static_log_normalizer_ignores_interpolated_or_unknown_messages(monkeypatch):
    captured = _capture_events(monkeypatch)
    handler = runtime_obs._RuntimeDiagnosticLogHandler()

    # Same human-readable idea, but not the exact code-owned template. The
    # adapter must not fuzzy-match arbitrary application/user-controlled text.
    record = logging.LogRecord(
        "gateway.stream_consumer",
        logging.ERROR,
        __file__,
        1,
        "Stream consumer error: already interpolated secret",
        (),
        None,
    )
    handler.emit(record)

    assert captured == []


def test_stream_and_delivery_paths_never_format_record_args(monkeypatch):
    captured = _capture_events(monkeypatch)
    handler = runtime_obs._RuntimeDiagnosticLogHandler()

    class _MustNotStringify:
        def __str__(self):
            raise AssertionError("record args must never be formatted")

    handler.emit(logging.LogRecord(
        "gateway.stream_consumer",
        logging.ERROR,
        __file__,
        1,
        "Stream consumer error: %s",
        (_MustNotStringify(),),
        None,
    ))
    handler.emit(logging.LogRecord(
        "gateway.platforms.base",
        logging.ERROR,
        __file__,
        1,
        "[%s] Fallback send also failed: %s",
        (_MustNotStringify(), _MustNotStringify()),
        None,
    ))

    assert [event.name for event in captured] == [
        "runtime.stream.failed",
        "runtime.delivery.fallback_failed",
    ]


def test_log_observer_installs_once():
    runtime_obs.install()
    runtime_obs.install()

    for name in runtime_obs._LOGGER_NAMES:
        handlers = [
            handler
            for handler in logging.getLogger(name).handlers
            if getattr(handler, runtime_obs._LOG_HANDLER_MARKER, False)
        ]
        assert len(handlers) == 1


def test_telemetry_failure_cannot_escape_into_runtime(monkeypatch):
    monkeypatch.setattr(runtime_obs, "enabled", lambda: True)

    import agent.monitoring.emitter as emitter_module

    class _BoomEmitter:
        def emit_buffered(self, _event):
            raise RuntimeError("collector is unavailable")

    monkeypatch.setattr(emitter_module, "get_emitter", lambda: _BoomEmitter())

    handler = runtime_obs._RuntimeDiagnosticLogHandler()
    # No exception escapes even when the sink raises.
    handler.emit(logging.LogRecord(
        "gateway.stream_consumer",
        logging.ERROR,
        __file__,
        1,
        "Stream consumer error: %s",
        ("private failure detail",),
        None,
    ))


def test_builtin_lifecycle_dispatcher_remains_relay_only(monkeypatch):
    from hermes_cli import observability
    from hermes_cli.observability import relay_shared_metrics

    monkeypatch.setattr(relay_shared_metrics, "handles_hook", lambda _hook: False)

    # Residual diagnostics no longer use lifecycle hooks as a bootstrap seam.
    assert observability.handles_hook("on_session_start") is False
    assert observability.handles_hook("post_tool_call") is False
