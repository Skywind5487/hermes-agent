import logging
import threading
import time

from hermes_state import SessionDB, _telemetry_operation
from agent.lifecycle_telemetry import lifecycle_context
from tools.session_search_tool import session_search


def _events(caplog, prefix):
    return [record.getMessage() for record in caplog.records if record.getMessage().startswith(prefix)]


def _fields(message):
    fields = {}
    for token in message.split()[1:]:
        if "=" in token:
            key, value = token.split("=", 1)
            fields[key] = value
    return fields


def test_write_lock_events_form_one_owner_scope(tmp_path, caplog, monkeypatch):
    monkeypatch.setenv("HERMES_LIBSIMPLE_PATH", "/home/skywind5487/.hermes/libsimple/libsimple.so")
    caplog.set_level(logging.INFO, logger="hermes_state")
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session(session_id="telemetry", source="cli")
        db.append_message("telemetry", role="user", content="lock telemetry")
    finally:
        db.close()

    events = _events(caplog, "LOCK_")
    write_events = [_fields(event) for event in events if _fields(event).get("operation_name") == "write"]
    assert {event["event"] for event in write_events} >= {"LOCK_REQUESTED", "LOCK_ACQUIRED", "LOCK_RELEASED"}
    scopes = {event["lock_scope_id"] for event in write_events}
    assert len(scopes) >= 2
    for scope in scopes:
        scoped = [event for event in write_events if event["lock_scope_id"] == scope]
        assert {event["event"] for event in scoped} == {"LOCK_REQUESTED", "LOCK_ACQUIRED", "LOCK_RELEASED"}
        assert len({event["connection_id"] for event in scoped}) == 1
        assert len({event["lock_id"] for event in scoped}) == 1
        assert all(event.get("write_id") not in (None, "-") for event in scoped)
    assert "append_message" in {event.get("caller_name") for event in write_events}


def test_search_reports_exclusive_phase_timing_and_per_session_breakdown(tmp_path, caplog, monkeypatch):
    monkeypatch.setenv("HERMES_LIBSIMPLE_PATH", "/home/skywind5487/.hermes/libsimple/libsimple.so")
    caplog.set_level(logging.INFO, logger="hermes_state")
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        for sid in ("telemetry-a", "telemetry-b"):
            db.create_session(session_id=sid, source="cli")
            db.append_message(sid, role="user", content="needle unique telemetry")
            db.append_message(sid, role="assistant", content="context response")
        results = db.search_messages("needle", limit=20, request_id="req-telemetry")
    finally:
        db.close()

    assert len(results) == 2
    phase = _events(caplog, "SEARCH_PHASE request_id=req-telemetry")[-1]
    fields = _fields(phase)
    required = {
        "search_total_ms", "search_accounted_ms", "search_residual_ms",
        "fts_lock_wait_ms", "fts_execute_ms", "fts_first_row_ms",
        "fts_fetch_ms", "fts_lock_hold_ms", "ctx_lock_wait_ms",
        "ctx_execute_ms", "ctx_first_row_ms", "ctx_fetch_ms",
        "ctx_lock_hold_ms", "ctx_decode_ms", "ctx_build_ms",
        "ctx_fetch_thread_cpu_ms", "ctx_fetch_batch_count",
        "ctx_fetch_batch_max_wall_ms", "ctx_fetch_batch_max_thread_cpu_ms",
        "ctx_materialize_wall_ms", "ctx_materialize_thread_cpu_ms",
        "ctx_rows", "ctx_bytes_loaded",
    }
    assert required <= fields.keys()
    assert int(fields["search_total_ms"]) >= int(fields["search_accounted_ms"])
    assert int(fields["search_residual_ms"]) == int(fields["search_total_ms"]) - int(fields["search_accounted_ms"])

    session_events = _events(caplog, "SEARCH_CONTEXT_SESSION request_id=req-telemetry")
    assert { _fields(event)["target_session_id"] for event in session_events } == {"telemetry-a", "telemetry-b"}
    assert all(int(_fields(event)["rows_loaded"]) >= 2 for event in session_events)
    assert all("batch_query_ms" in _fields(event) for event in session_events)
    assert all(_fields(event)["survived_lineage_dedup"] == "unknown" for event in session_events)

    context_fetch_records = [
        _fields(event)
        for event in _events(caplog, "DB_QUERY_FETCH_END")
        if _fields(event).get("query_fingerprint") == "session_search_context"
    ]
    assert context_fetch_records
    assert int(fields["ctx_query_count"]) == len(context_fetch_records)
    assert int(fields["ctx_fetch_batch_count"]) == sum(
        int(record["fetch_batch_count"]) for record in context_fetch_records
    )
    assert int(fields["ctx_rows"]) == sum(
        int(record["rows_returned"]) for record in context_fetch_records
    )
    assert int(fields["ctx_bytes_loaded"]) >= 0


def test_db_lifecycle_events_have_correlatable_operation_and_transaction_fields(
    tmp_path, caplog, monkeypatch
):
    monkeypatch.setenv("HERMES_LIBSIMPLE_PATH", "/home/skywind5487/.hermes/libsimple/libsimple.so")
    caplog.set_level(logging.INFO, logger="hermes_state")
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session(session_id="db-lifecycle", source="cli")
        db.append_message("db-lifecycle", role="user", content="db lifecycle")
    finally:
        db.close()

    records = [_fields(event) for event in _events(caplog, "DB_")]
    names = {record.get("event") for record in records}
    assert {
        "DB_OPERATION_START",
        "DB_OPERATION_END",
        "DB_LOCK_REQUESTED",
        "DB_LOCK_ACQUIRED",
        "DB_LOCK_RELEASED",
        "DB_TRANSACTION_BEGIN_START",
        "DB_TRANSACTION_BEGIN_END",
        "DB_COMMIT_START",
        "DB_COMMIT_END",
    } <= names

    operation_records = [
        record
        for record in records
        if record.get("db_operation_id") not in (None, "-")
    ]
    operation_ids = {record.get("db_operation_id") for record in operation_records}
    assert len(operation_ids) >= 1
    assert all(record.get("trace_id") not in (None, "-") for record in operation_records)
    assert all(record.get("span_id") not in (None, "-") for record in operation_records)
    assert all(record.get("parent_span_id") not in (None, "-") for record in operation_records)
    assert all(record.get("connection_id") not in (None, "-") for record in operation_records)
    assert all(record.get("transaction_id") not in (None, "-") for record in operation_records)


def test_search_emits_payload_free_query_lifecycle(caplog, monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_LIBSIMPLE_PATH", "/home/skywind5487/.hermes/libsimple/libsimple.so")
    caplog.set_level(logging.INFO, logger="hermes_state")
    db = SessionDB(db_path=tmp_path / "query-state.db")
    try:
        db.create_session(session_id="query-session", source="cli")
        db.append_message("query-session", role="user", content="needle")
        db.search_messages("needle", request_id="query-request", limit=5)
    finally:
        db.close()

    records = [_fields(event) for event in _events(caplog, "DB_QUERY_")]
    names = {record.get("event") for record in records}
    assert {
        "DB_QUERY_START",
        "DB_QUERY_EXECUTED",
        "DB_QUERY_FIRST_ROW",
        "DB_QUERY_FETCH_END",
    } <= names
    assert any(
        record.get("query_fingerprint") in {
            "session_search_fts",
            "session_search_fts_trigram",
            "session_search_like",
        }
        for record in records
    )
    assert all(record.get("cursor_id") not in (None, "-") for record in records)
    assert all(record.get("trace_id") == "query-request" for record in records)
    fetch_records = [record for record in records if record.get("event") == "DB_QUERY_FETCH_END"]
    assert all(int(record.get("bytes_loaded", "0")) >= 0 for record in fetch_records)
    assert all("fetch_thread_cpu_ms" in record for record in fetch_records)
    assert all("fetch_batch_count" in record for record in fetch_records)
    assert all("fetch_batch_max_wall_ms" in record for record in fetch_records)
    assert all("materialize_wall_ms" in record for record in fetch_records)
    serialized = " ".join(event.message for event in caplog.records)
    assert "needle" not in serialized


def test_lock_wait_slow_points_waiter_to_current_owner(tmp_path, caplog, monkeypatch):
    monkeypatch.setenv("HERMES_LIBSIMPLE_PATH", "/home/skywind5487/.hermes/libsimple/libsimple.so")
    caplog.set_level(logging.INFO, logger="hermes_state")
    db = SessionDB(db_path=tmp_path / "owner-state.db")
    db.create_session(session_id="session-B", source="test")
    owner_ready = threading.Event()
    release_owner = threading.Event()
    waiter_done = threading.Event()
    errors = []

    def owner():
        try:
            with _telemetry_operation(
                "session_search_context",
                request_id="owner-request",
                active_session_id="session-A",
                connection_id=db._connection_id,
                connection_role=db._connection_role,
            ):
                with db._lock:
                    owner_ready.set()
                    release_owner.wait(timeout=2)
        except BaseException as exc:
            errors.append(exc)

    def waiter():
        try:
            with _telemetry_operation(
                "append_message",
                request_id="waiter-request",
                active_session_id="session-B",
                target_session_id="session-B",
                connection_id=db._connection_id,
                connection_role=db._connection_role,
            ):
                db._execute_write(
                    lambda conn: conn.execute(
                        "UPDATE sessions SET message_count = message_count WHERE id = ?",
                        ("session-B",),
                    )
                )
        except BaseException as exc:
            errors.append(exc)
        finally:
            waiter_done.set()

    try:
        owner_thread = threading.Thread(target=owner)
        owner_thread.start()
        assert owner_ready.wait(timeout=1)

        waiter_thread = threading.Thread(target=waiter)
        waiter_thread.start()
        time.sleep(0.18)
        release_owner.set()
        assert waiter_done.wait(timeout=2)
        owner_thread.join(timeout=2)
        waiter_thread.join(timeout=2)
    finally:
        release_owner.set()
        db.close()

    assert not errors
    slow_events = [
        _fields(event)
        for event in _events(caplog, "DB_LOCK_WAIT_SLOW")
        if _fields(event).get("request_id") == "waiter-request"
    ]
    assert slow_events
    slow = slow_events[-1]
    assert slow["waiter_operation_id"] not in (None, "-")
    assert slow["current_owner_operation_id"] not in (None, "-")
    assert slow["current_owner_operation_name"] == "session_search_context"
    assert slow["current_owner_active_session_id"] == "session-A"
    assert int(slow["queue_depth"]) >= 1
    assert int(slow["lock_wait_ms"]) >= 100


def test_failed_write_emits_payload_free_rollback_and_error(tmp_path, caplog, monkeypatch):
    monkeypatch.setenv("HERMES_LIBSIMPLE_PATH", "/home/skywind5487/.hermes/libsimple/libsimple.so")
    caplog.set_level(logging.INFO, logger="hermes_state")
    db = SessionDB(db_path=tmp_path / "error-state.db")
    try:
        try:
            with _telemetry_operation(
                "append_message",
                request_id="error-request",
                active_session_id="session-error",
                connection_id=db._connection_id,
                connection_role=db._connection_role,
            ):
                db._execute_write(
                    lambda conn: (_ for _ in ()).throw(RuntimeError("secret payload"))
                )
        except RuntimeError:
            pass
    finally:
        db.close()

    records = [_fields(event) for event in _events(caplog, "DB_")]
    error_records = [
        record
        for record in records
        if record.get("event") == "DB_OPERATION_ERROR"
        and record.get("request_id") == "error-request"
    ]
    rollback_records = [
        record
        for record in records
        if record.get("event") == "DB_ROLLBACK"
        and record.get("request_id") == "error-request"
    ]
    assert error_records
    assert rollback_records
    assert all(record.get("error_type") == "RuntimeError" for record in error_records)
    assert "secret payload" not in " ".join(event.message for event in caplog.records)


def test_session_search_carries_api_tool_correlation_into_db(tmp_path, caplog, monkeypatch):
    monkeypatch.setenv("HERMES_LIBSIMPLE_PATH", "/home/skywind5487/.hermes/libsimple/libsimple.so")
    caplog.set_level(logging.INFO, logger="hermes_state")
    db = SessionDB(db_path=tmp_path / "correlation-state.db")
    try:
        db.create_session(session_id="correlation-session", source="test")
        db.append_message("correlation-session", role="user", content="api db correlation needle")
        with lifecycle_context(
            trace_id="turn-correlation",
            turn_id="turn-correlation",
            api_request_id="turn-correlation:api:1",
            tool_call_id="tool-correlation",
            session_id="correlation-session",
        ):
            result = session_search(
                query="correlation needle",
                limit=1,
                db=db,
                current_session_id="different-session",
            )
    finally:
        db.close()

    assert '"success": true' in result
    records = [
        _fields(event)
        for event in _events(caplog, "DB_")
        if _fields(event).get("db_operation_id") not in (None, "-")
        and _fields(event).get("request_id") not in (None, "-")
    ]
    assert all(record["trace_id"] == "turn-correlation" for record in records)
    assert all(record["turn_id"] == "turn-correlation" for record in records)
    assert all(record["api_request_id"] == "turn-correlation:api:1" for record in records)
    assert all(record["tool_call_id"] == "tool-correlation" for record in records)
