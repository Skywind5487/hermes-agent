"""Intent-level contracts for the session-search compression lineage resolver."""

import sqlite3

from hermes_state_lineage import SessionSearchMixin, _LineageState


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            parent_session_id TEXT,
            source TEXT,
            model_config TEXT,
            end_reason TEXT
        )
        """
    )
    return conn


def _session(
    conn,
    session_id,
    *,
    parent=None,
    source="cli",
    model_config=None,
    end_reason=None,
):
    conn.execute(
        "INSERT INTO sessions "
        "(id, parent_session_id, source, model_config, end_reason) "
        "VALUES (?, ?, ?, ?, ?)",
        (session_id, parent, source, model_config, end_reason),
    )


def _resolve(conn, session_id, *, budget=1500, state=None):
    resolver = SessionSearchMixin()
    state = state or _LineageState(budget)
    root = resolver._resolve_compression_lineage_on_conn(conn, session_id, state)
    return root, state


def test_only_positive_compression_continuations_collapse():
    conn = _conn()
    _session(conn, "compressed-parent", end_reason="compression")
    _session(conn, "plain-parent")
    _session(conn, "continuation", parent="compressed-parent")
    _session(
        conn,
        "branch",
        parent="compressed-parent",
        model_config='{"_branched_from":"compressed-parent"}',
    )
    _session(
        conn,
        "delegate",
        parent="compressed-parent",
        model_config='{"_delegate_from":"compressed-parent"}',
    )
    _session(conn, "tool-child", parent="compressed-parent", source="tool")
    _session(
        conn,
        "foreign-marker",
        parent="compressed-parent",
        model_config='{"_delegate_from":"some-other-parent"}',
    )
    _session(conn, "generic-child", parent="plain-parent")

    assert _resolve(conn, "continuation")[0] == "compressed-parent"
    assert _resolve(conn, "branch")[0] == "branch"
    assert _resolve(conn, "delegate")[0] == "delegate"
    assert _resolve(conn, "tool-child")[0] == "tool-child"
    assert _resolve(conn, "foreign-marker")[0] == "compressed-parent"
    assert _resolve(conn, "generic-child")[0] == "generic-child"


def test_malformed_config_does_not_create_a_positive_edge():
    conn = _conn()
    _session(conn, "root", end_reason="compression")
    _session(conn, "child", parent="root", model_config="{broken-json")

    assert _resolve(conn, "child")[0] == "child"


def test_missing_parent_and_positive_cycle_fail_closed():
    conn = _conn()
    _session(conn, "missing", parent="gone")
    _session(conn, "cycle-a", parent="cycle-b", end_reason="compression")
    _session(conn, "cycle-b", parent="cycle-a", end_reason="compression")

    assert _resolve(conn, "missing")[0] is None
    assert _resolve(conn, "cycle-a")[0] is None


def test_global_budget_fails_closed_without_fabricating_root():
    conn = _conn()
    _session(conn, "root", end_reason="compression")
    _session(conn, "one", parent="root", end_reason="compression")
    _session(conn, "two", parent="one")

    root, state = _resolve(conn, "two", budget=2)

    assert root is None
    assert state.bound_hit is True
    assert state.work == 2


def test_query_local_path_compression_reuses_depth14_lineage():
    conn = _conn()
    _session(conn, "n0", end_reason="compression")
    for idx in range(1, 15):
        _session(
            conn,
            f"n{idx}",
            parent=f"n{idx - 1}",
            end_reason="compression" if idx < 14 else None,
        )

    resolver = SessionSearchMixin()
    state = _LineageState(1500)

    assert resolver._resolve_compression_lineage_on_conn(conn, "n14", state) == "n0"
    assert state.work == 15

    for idx in range(13, -1, -1):
        assert resolver._resolve_compression_lineage_on_conn(
            conn, f"n{idx}", state
        ) == "n0"
    assert state.work == 15
    assert state.bound_hit is False
