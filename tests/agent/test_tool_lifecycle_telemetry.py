import json
import logging

import model_tools

from agent.tool_executor import _emit_terminal_post_tool_call, _emit_tool_start


class _Agent:
    session_id = "session-1"
    _current_turn_id = "turn-1"
    _current_api_request_id = "turn-1:api:1"


def _lifecycle_records(caplog):
    return [
        json.loads(record.message.removeprefix("HERMES_LIFECYCLE "))
        for record in caplog.records
        if record.name == "hermes.lifecycle"
    ]


def test_terminal_tool_path_emits_payload_free_end_event(monkeypatch, caplog):
    monkeypatch.setattr(model_tools, "_emit_post_tool_call_hook", lambda **kwargs: None)

    with caplog.at_level(logging.INFO, logger="hermes.lifecycle"):
        _emit_terminal_post_tool_call(
            _Agent(),
            function_name="read_file",
            function_args={"path": "/tmp/secret.txt"},
            result="secret contents must not be logged",
            effective_task_id="task-1",
            tool_call_id="tool-1",
            duration_ms=27,
            status="success",
        )

    records = _lifecycle_records(caplog)
    assert len(records) == 1
    record = records[0]
    assert record["event"] == "TOOL_END"
    assert record["trace_id"] == "turn-1"
    assert record["span_id"] == "tool-1"
    assert record["parent_span_id"] == "turn-1:api:1"
    assert record["function_name"] == "read_file"
    assert record["status"] == "success"
    assert record["duration_ms"] == 27
    assert "/tmp/secret.txt" not in caplog.text
    assert "secret contents must not be logged" not in caplog.text


def test_tool_start_event_uses_api_call_as_parent(caplog):
    with caplog.at_level(logging.INFO, logger="hermes.lifecycle"):
        _emit_tool_start(
            _Agent(),
            function_name="terminal",
            effective_task_id="task-1",
            tool_call_id="tool-2",
        )

    records = _lifecycle_records(caplog)
    assert len(records) == 1
    record = records[0]
    assert record["event"] == "TOOL_START"
    assert record["span_id"] == "tool-2"
    assert record["parent_span_id"] == "turn-1:api:1"
    assert record["function_name"] == "terminal"
    assert record["status"] == "started"
