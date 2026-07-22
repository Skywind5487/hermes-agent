from __future__ import annotations

from agent.sqlite_native_telemetry import SQLiteNativeProbe


class _OwnerConnection:
    def __init__(self):
        self.progress_calls = []
        self.db_status_calls = 0
        self._status = {
            "cache_hit": 10,
            "cache_miss": 2,
            "cache_write": 3,
            "cache_spill": 0,
            "cache_used_bytes": 4096,
            "schema_used_bytes": 128,
            "stmt_used_bytes": 256,
        }

    def set_progress_handler(self, callback, interval):
        self.progress_calls.append((callback, interval))

    def _hermes_db_status(self):
        self.db_status_calls += 1
        return dict(self._status)


class _OwnerCursor:
    def __init__(self):
        self.status_calls = 0

    def _hermes_stmt_status(self):
        self.status_calls += 1
        return {
            "vm_steps": 17,
            "fullscan_steps": 4,
            "sort_operations": 1,
            "autoindex_operations": 0,
            "reprepare_count": 0,
        }


def test_owner_layer_uses_exact_scalar_counters_without_progress_handler():
    connection = _OwnerConnection()
    cursor = _OwnerCursor()
    events = []
    probe = SQLiteNativeProbe.try_attach(connection, events.append)

    token = probe.start_query("query-owner", "session_search_context")
    connection._status["cache_hit"] = 15
    connection._status["cache_miss"] = 3
    probe.finish_query(token, cursor=cursor)

    profile = next(event for event in events if event["event"] == "DB_NATIVE_PROFILE")
    status = next(event for event in events if event["event"] == "DB_NATIVE_STATUS")

    assert probe.backend == "cpython_owner"
    assert probe.native_api_available is True
    assert connection.progress_calls == []
    assert cursor.status_calls == 1
    assert connection.db_status_calls == 2
    assert profile["vm_steps"] == 17
    assert profile["vm_steps_exact"] is True
    assert profile["fullscan_steps"] == 4
    assert status["cache_hit_delta"] == 5
    assert status["cache_miss_delta"] == 1
    assert status["cache_used_bytes"] == 4096
    metric_keys = {
        "native_ns",
        "vm_steps",
        "fullscan_steps",
        "sort_operations",
        "autoindex_operations",
        "reprepare_count",
        "cache_hit_delta",
        "cache_miss_delta",
        "cache_write_delta",
        "cache_spill_delta",
        "cache_used_bytes",
        "schema_used_bytes",
        "stmt_used_bytes",
    }
    assert all(
        isinstance(event[key], (int, type(None)))
        for event in (profile, status)
        for key in metric_keys.intersection(event)
    )


def test_owner_layer_probe_failure_is_fail_open():
    class BrokenConnection(_OwnerConnection):
        def _hermes_db_status(self):
            raise RuntimeError("diagnostic failure")

    events = []
    probe = SQLiteNativeProbe.try_attach(BrokenConnection(), events.append)
    assert probe.available is True
    token = probe.start_query("query-broken", "session_search_context")
    probe.finish_query(token, cursor=object())
    assert any(event["event"] == "DB_NATIVE_ERROR" for event in events)
