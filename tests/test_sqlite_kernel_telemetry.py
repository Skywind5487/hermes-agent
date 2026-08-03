from __future__ import annotations

import importlib.util
import json
import logging
import os

import sqlite3
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
TRACE_SCRIPT = ROOT / "scripts" / "hermes_kernel_trace.py"


def _load_trace_module():
    spec = importlib.util.spec_from_file_location("hermes_kernel_trace", TRACE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _fields(message):
    fields = {}
    for token in message.split()[1:]:
        if "=" in token:
            key, value = token.split("=", 1)
            fields[key] = value
    return fields


def test_sqlite_native_probe_emits_profile_and_status(tmp_path):
    from agent.sqlite_native_telemetry import SQLiteNativeProbe

    conn = sqlite3.connect(tmp_path / "native.db")
    events = []
    probe = SQLiteNativeProbe.try_attach(conn, events.append)
    assert probe.available is True
    try:
        conn.execute("CREATE TABLE numbers(value INTEGER)")
        token = probe.start_query("query-1", "session_search_context")
        rows = conn.execute("SELECT value FROM numbers ORDER BY value").fetchall()
        assert len(rows) == 0
        probe.finish_query(token)
    finally:
        probe.close()
        conn.close()

    names = {event["event"] for event in events}
    assert "DB_NATIVE_PROFILE" in names
    assert "DB_NATIVE_STATUS" in names
    profile = next(event for event in events if event["event"] == "DB_NATIVE_PROFILE")
    assert profile["query_id"] == "query-1"
    assert profile["query_fingerprint"] == "session_search_context"
    assert profile["native_backend"] == "progress_handler"
    assert profile["native_api_available"] is False
    assert profile["vm_steps_exact"] is False
    assert int(profile["native_ns"]) >= 0
    assert int(profile["vm_steps"]) >= 0
    encoded = json.dumps(events)
    assert "SELECT value" not in encoded
    assert "numbers" not in encoded


def test_sqlite_native_probe_failure_is_non_fatal(caplog):
    from agent.sqlite_native_telemetry import SQLiteNativeProbe

    events = []
    with caplog.at_level(logging.INFO):
        probe = SQLiteNativeProbe.try_attach(object(), events.append)
    assert probe.available is False
    assert probe.error_type
    assert events
    assert events[-1]["event"] == "DB_NATIVE_ERROR"


def test_cgroup_io_parser_aggregates_devices(tmp_path):
    from agent.kernel_telemetry import _read_cgroup_io

    path = tmp_path / "io.stat"
    path.write_text(
        "8:0 rbytes=10 wbytes=2 rios=3 wios=4\n"
        "8:1 rbytes=5 wbytes=7 rios=11 wios=13\n"
    )
    assert _read_cgroup_io(path) == {
        "rbytes": 15,
        "wbytes": 9,
        "rios": 14,
        "wios": 17,
    }


def test_kernel_snapshot_delta_has_identity_and_request_window_fields(tmp_path):
    from agent.kernel_telemetry import capture_snapshot, diff_snapshot

    db = tmp_path / "state.db"
    db.write_bytes(b"state")
    before = capture_snapshot(pid=os.getpid(), tid=None, db_path=db)
    db.write_bytes(b"state-expanded")
    after = capture_snapshot(pid=os.getpid(), tid=None, db_path=db)
    delta = diff_snapshot(before, after)

    assert before["boot_id"]
    assert before["machine_monotonic_ns"] > 0
    assert before["pid"] == os.getpid()
    assert before["thread_id"]
    assert delta["state_db_size_bytes_delta"] == len(b"-expanded")
    assert "sched_run_delay_ns_delta" in delta
    assert "proc_read_bytes_delta" in delta
    assert "vm_pgmajfault_delta" in delta
    assert "cgroup_cpu_throttled_usec_delta" in delta
    assert "psi_io_some_avg10_before" in delta
    assert "disk_root_io_ticks_ms_delta" in delta


def test_trace_capture_restores_fake_tracefs_state(tmp_path):
    module = _load_trace_module()
    trace_root = tmp_path / "tracing"
    for relative, value in {
        "tracing_on": "0\n",
        "current_tracer": "nop\n",
        "set_event": "sched:sched_switch\n",
        "events/block/block_rq_issue/enable": "0\n",
        "events/block/block_rq_complete/enable": "1\n",
        "events/sched/sched_switch/enable": "1\n",
    }.items():
        path = trace_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)

    capture = module.TraceCapture(
        trace_root=trace_root,
        events=("block/block_rq_issue", "block/block_rq_complete"),
    )
    capture.start()
    assert (trace_root / "tracing_on").read_text() == "1\n"
    assert (trace_root / "events/block/block_rq_issue/enable").read_text() == "1\n"
    capture.stop()

    assert (trace_root / "tracing_on").read_text() == "0\n"
    assert (trace_root / "current_tracer").read_text() == "nop\n"
    assert (trace_root / "set_event").read_text() == "sched:sched_switch\n"
    assert (trace_root / "events/block/block_rq_issue/enable").read_text() == "0\n"
    assert (trace_root / "events/block/block_rq_complete/enable").read_text() == "1\n"
    assert (trace_root / "events/sched/sched_switch/enable").read_text() == "1\n"


def test_trace_capture_rolls_back_partial_start(tmp_path):
    module = _load_trace_module()
    trace_root = tmp_path / "tracing"
    first_event = trace_root / "events/block/block_rq_issue"
    first_event.mkdir(parents=True)
    (first_event / "enable").write_text("0\n")
    (trace_root / "events/block/block_rq_complete/enable").mkdir(parents=True)
    (trace_root / "tracing_on").write_text("0\n")

    capture = module.TraceCapture(
        trace_root=trace_root,
        events=("block/block_rq_issue", "block/block_rq_complete"),
    )
    with pytest.raises(IsADirectoryError):
        capture.start()

    assert (trace_root / "tracing_on").read_text() == "0\n"
    assert (first_event / "enable").read_text() == "0\n"
    assert capture._saved == {}


def test_trace_capture_does_not_overwrite_external_drift(tmp_path):
    module = _load_trace_module()
    trace_root = tmp_path / "tracing"
    enable = trace_root / "events/block/block_rq_issue/enable"
    enable.parent.mkdir(parents=True)
    enable.write_text("0\n")
    (trace_root / "tracing_on").write_text("0\n")

    capture = module.TraceCapture(
        trace_root=trace_root,
        events=("block/block_rq_issue",),
    )
    capture.start()
    enable.write_text("external-owner\n")
    capture.stop()

    assert enable.read_text() == "external-owner\n"
    assert str(enable) in capture.restore_conflicts


def test_trace_marker_is_scalar_and_bounded(tmp_path):
    module = _load_trace_module()
    marker = module.safe_marker("DB_QUERY_START", {"query_id": "q-1", "request_id": "r-1"})
    assert marker == "HERMES_DB_QUERY_START query_id=q-1 request_id=r-1"
    assert "\n" not in marker
    assert len(marker) < 512


def test_sessiondb_opt_in_emits_correlated_native_and_kernel_windows(tmp_path, caplog, monkeypatch):
    from hermes_state import SessionDB

    monkeypatch.setenv("HERMES_SQLITE_NATIVE", "1")
    monkeypatch.setenv("HERMES_KERNEL_SNAPSHOT", "1")
    monkeypatch.setenv("HERMES_LIBSIMPLE_PATH", "/home/skywind5487/.hermes/libsimple/libsimple.so")
    caplog.set_level(logging.INFO, logger="hermes_state")
    db = SessionDB(db_path=tmp_path / "instrumented.db")
    probe = db._native_probe
    try:
        db.create_session(session_id="instrumented", source="test")
        db.append_message("instrumented", role="user", content="probe-token")
        db.search_messages("probe-token", request_id="request-native", limit=5)
    finally:
        db.close()
    assert probe is not None
    assert probe.available is False

    records = [_fields(record.getMessage()) for record in caplog.records]
    native = [record for record in records if record.get("event") == "DB_NATIVE_PROFILE"]
    kernel = [record for record in records if record.get("event") == "DB_KERNEL_WINDOW"]
    assert native
    assert kernel
    assert any(record.get("query_id") not in (None, "-") for record in native)
    assert all(record.get("request_id") == "request-native" for record in kernel)
    assert all(record.get("query_id") not in (None, "-") for record in kernel)
    serialized = " ".join(record.getMessage() for record in caplog.records)
    assert "probe-token" not in serialized
