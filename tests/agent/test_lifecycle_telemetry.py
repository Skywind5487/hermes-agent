import json
import logging

from agent.lifecycle_telemetry import emit_lifecycle


def test_emit_lifecycle_is_structured_and_does_not_log_payload(caplog):
    with caplog.at_level(logging.INFO, logger="hermes.lifecycle"):
        emit_lifecycle(
            "TOOL_END",
            status="success",
            trace_id="turn-1",
            span_id="tool-1",
            parent_span_id="api-1",
            function_name="read_file",
            duration_ms=12,
            result_payload="must-not-appear",
        )

    record = next(r for r in caplog.records if r.name == "hermes.lifecycle")
    assert record.message.startswith("HERMES_LIFECYCLE ")
    payload = json.loads(record.message.removeprefix("HERMES_LIFECYCLE "))
    assert payload["event"] == "TOOL_END"
    assert payload["status"] == "success"
    assert payload["trace_id"] == "turn-1"
    assert payload["span_id"] == "tool-1"
    assert payload["parent_span_id"] == "api-1"
    assert payload["function_name"] == "read_file"
    assert payload["duration_ms"] == 12
    assert "result_payload" not in payload


def test_emit_lifecycle_keeps_failure_reason_but_not_exception_object(caplog):
    with caplog.at_level(logging.INFO, logger="hermes.lifecycle"):
        emit_lifecycle(
            "API_CALL_END",
            status="error",
            reason="provider_timeout",
            error_type="TimeoutError",
            error_message="secret response body",
        )

    record = next(r for r in caplog.records if r.name == "hermes.lifecycle")
    payload = json.loads(record.message.removeprefix("HERMES_LIFECYCLE "))
    assert payload["reason"] == "provider_timeout"
    assert payload["error_type"] == "TimeoutError"
    assert "secret response body" not in record.message
