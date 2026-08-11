"""Six-index FTS lifecycle registry (issue #27).

The authoritative ``FTS_INDEXES`` descriptor registry in
``hermes_state_common`` must drive ordinary maintenance (optimize / bounded
merge / explicit rebuild) across ALL six modern FTS indexes — not just the
three message indexes — while preserving per-index capability/ownership
gating. This file pins the new lifecycle behavior; the pre-#27 message-only
assertions in ``test_hermes_state.py::TestOptimizeFts`` were updated to the
six-index expectation.

Storage-v2 settlement (#31) is out of scope: none of these tests touch
``fts_storage_version`` semantics.
"""

import sqlite3

import pytest

from hermes_state import (
    FTS_INDEXES,
    SessionDB,
    _fts_descriptor,
)
from hermes_state_search import (
    _FTS_MESSAGE_CJK_SPEC,
    _FTS_MESSAGE_SPEC,
    _FTS_SESSION_CJK_SPEC,
    _FTS_SESSION_SPEC,
    _FTS_SESSION_TRIGRAM_SPEC,
)

# On a default test host (no loadable cjk tokenizer) the applicable owned
# indexes are the four built-in ones; the two optional CJK members are gated
# off by tokenizer capability.
_REQUIRED_MAINTENANCE_TABLES = (
    "messages_fts",
    "messages_fts_trigram",
    "sessions_fts",
    "sessions_fts_trigram",
)


@pytest.fixture()
def db(tmp_path):
    """Fresh SessionDB over a temp database file (all six-index members are
    created at open: message Unicode/trigram + session Unicode/trigram; the
    CJK members only when a loadable tokenizer is present)."""
    db_path = tmp_path / "state.db"
    session_db = SessionDB(db_path=db_path)
    yield session_db
    session_db.close()


def _table_names():
    return {d.table for d in FTS_INDEXES}


def _fts_table_from_special_sql(sql: str) -> str:
    """Recover the target table from an FTS5 special-command statement, e.g.
    ``INSERT INTO sessions_fts(sessions_fts) VALUES('optimize')``."""
    rest = sql.split("INSERT INTO ", 1)[1]
    return rest.split("(", 1)[0].strip()


def _session_fts_matches(db, needle: str) -> bool:
    row = db._conn.execute(
        "SELECT 1 FROM sessions_fts WHERE sessions_fts MATCH ? LIMIT 1",
        (f'"{needle}"',),
    ).fetchone()
    return row is not None


def _trigram_fts_matches(db, needle: str) -> bool:
    row = db._conn.execute(
        "SELECT 1 FROM sessions_fts_trigram "
        "WHERE sessions_fts_trigram MATCH ? LIMIT 1",
        (needle,),
    ).fetchone()
    return row is not None


# ── Registry shape ────────────────────────────────────────────────────────

def test_registry_has_six_modern_members():
    assert _table_names() == {
        "messages_fts",
        "messages_fts_trigram",
        "messages_fts_cjk",
        "sessions_fts",
        "sessions_fts_cjk",
        "sessions_fts_trigram",
    }
    for desc in FTS_INDEXES:
        assert desc.table
        assert desc.source
        assert desc.row_key
        assert desc.columns
        assert desc.trigger_names
        assert desc.capability in ("fts5", "trigram", "cjk")


def test_registry_sources_row_keys_and_capabilities():
    by_table = {d.table: d for d in FTS_INDEXES}
    # Message lane: Unicode over canonical messages, derived trigram/cjk
    # sources exclude tool rows.
    assert by_table["messages_fts"].source == "messages"
    assert by_table["messages_fts"].row_key == "id"
    assert by_table["messages_fts"].capability == "fts5"
    assert by_table["messages_fts_trigram"].source == "messages_fts_trigram_src"
    assert by_table["messages_fts_trigram"].row_key == "id"
    assert by_table["messages_fts_trigram"].capability == "trigram"
    assert ("view", "messages_fts_trigram_src") in (
        by_table["messages_fts_trigram"].derived_objects
    )
    assert by_table["messages_fts_cjk"].source == "messages_fts_cjk_src"
    assert by_table["messages_fts_cjk"].capability == "cjk"
    assert ("view", "messages_fts_cjk_src") in (
        by_table["messages_fts_cjk"].derived_objects
    )
    # Session lane: raw Unicode metadata keyed by named row_id; trigram reads
    # the derived compact VIEW.
    assert by_table["sessions_fts"].source == "sessions"
    assert by_table["sessions_fts"].row_key == "row_id"
    assert by_table["sessions_fts"].columns == ("title", "id", "display_name")
    assert by_table["sessions_fts"].capability == "fts5"
    assert by_table["sessions_fts_cjk"].source == "sessions"
    assert by_table["sessions_fts_cjk"].row_key == "row_id"
    assert by_table["sessions_fts_cjk"].capability == "cjk"
    assert by_table["sessions_fts_trigram"].source == "sessions_fts_trigram_src"
    assert by_table["sessions_fts_trigram"].row_key == "row_id"
    assert by_table["sessions_fts_trigram"].capability == "trigram"
    assert ("view", "sessions_fts_trigram_src") in (
        by_table["sessions_fts_trigram"].derived_objects
    )


# ── Rebuild specs derive their static identity from the registry ─────────

@pytest.mark.parametrize(
    "spec,expected_table",
    [
        (_FTS_MESSAGE_SPEC, "messages_fts"),
        (_FTS_SESSION_SPEC, "sessions_fts"),
        (_FTS_SESSION_TRIGRAM_SPEC, "sessions_fts_trigram"),
        (_FTS_SESSION_CJK_SPEC, "sessions_fts_cjk"),
        (_FTS_MESSAGE_CJK_SPEC, "messages_fts_cjk"),
    ],
)
def test_rebuild_specs_derive_identity_from_registry(spec, expected_table):
    desc = _fts_descriptor(expected_table)
    assert spec["descriptor"] is desc
    assert spec["fts_table"] == desc.table
    assert spec["source_table"] == desc.source
    assert spec["row_key"] == desc.row_key
    assert spec["fts_columns"] == desc.columns
    assert spec["source_columns"] == desc.columns


def test_message_spec_trigram_derives_from_registry():
    assert (
        _FTS_MESSAGE_SPEC["trigram_descriptor"]
        is _fts_descriptor("messages_fts_trigram")
    )
    assert _FTS_MESSAGE_SPEC["trigram_fts"] == "messages_fts_trigram"
    assert _FTS_MESSAGE_SPEC["trigram_columns"] == (
        "content", "tool_name", "tool_calls",
    )


def test_message_cjk_spec_has_own_markers_and_shared_engine_shape():
    assert _FTS_MESSAGE_CJK_SPEC["high_water_key"] == "fts_cjk_rebuild_high_water"
    assert _FTS_MESSAGE_CJK_SPEC["progress_key"] == "fts_cjk_rebuild_progress"
    # Distinct from the message Unicode pair (never shared).
    assert _FTS_MESSAGE_CJK_SPEC["high_water_key"] != _FTS_MESSAGE_SPEC["high_water_key"]
    assert _FTS_MESSAGE_CJK_SPEC["source_table"] == "messages_fts_cjk_src"


# ── Ordinary maintenance covers the session indexes ──────────────────────

def test_maintenance_tables_covers_owned_session_indexes(db):
    assert db._fts_maintenance_tables() == _REQUIRED_MAINTENANCE_TABLES
    assert set(db._fts_maintenance_tables()) <= _table_names()


def test_optimize_touches_all_owned_indexes(db):
    db.create_session(session_id="s1", source="cli")
    db.set_session_title("s1", "alpha beta")
    statements = []
    db._conn.set_trace_callback(statements.append)
    try:
        count = db.optimize_fts()
    finally:
        db._conn.set_trace_callback(None)
    assert count == len(_REQUIRED_MAINTENANCE_TABLES)
    optimized = {
        _fts_table_from_special_sql(sql)
        for sql in statements
        if "'optimize'" in sql
    }
    assert optimized == set(_REQUIRED_MAINTENANCE_TABLES)


def test_bounded_merge_touches_all_owned_indexes(db):
    db.create_session(session_id="s1", source="cli")
    db.set_session_title("s1", "bounded merge")
    statements = []
    db._conn.set_trace_callback(statements.append)
    try:
        executed = db._merge_fts_incrementally(max_pages=37)
    finally:
        db._conn.set_trace_callback(None)
    merge_sql = [sql for sql in statements if "VALUES('merge', 37)" in sql]
    assert len(merge_sql) == executed
    merged = {_fts_table_from_special_sql(sql) for sql in merge_sql}
    assert merged == set(_REQUIRED_MAINTENANCE_TABLES)


def _corrupt_shadow(db, shadow_table: str) -> None:
    db._conn.execute(
        f"UPDATE {shadow_table} SET block = X'BADC0FFEE0DDF00D'"
    )
    db._conn.commit()


def test_rebuild_repairs_corrupt_session_unicode_index(db):
    db.create_session(session_id="s1", source="cli")
    db.set_session_title("s1", "pizzaparty")
    assert _session_fts_matches(db, "pizzaparty")
    _corrupt_shadow(db, "sessions_fts_data")
    with pytest.raises(sqlite3.DatabaseError):
        _session_fts_matches(db, "pizzaparty")
    rebuilt = db.rebuild_fts()
    assert rebuilt >= len(_REQUIRED_MAINTENANCE_TABLES)
    assert _session_fts_matches(db, "pizzaparty")


def test_rebuild_repairs_corrupt_session_trigram_index(db):
    db.create_session(session_id="s1", source="cli")
    db.set_session_title("s1", "pizza pie party")
    assert _trigram_fts_matches(db, "pizza")
    _corrupt_shadow(db, "sessions_fts_trigram_data")
    with pytest.raises(sqlite3.DatabaseError):
        _trigram_fts_matches(db, "pizza")
    rebuilt = db.rebuild_fts()
    assert rebuilt >= len(_REQUIRED_MAINTENANCE_TABLES)
    assert _trigram_fts_matches(db, "pizza")


def test_rebuild_skips_unavailable_trigram_target(db):
    """Registry membership is NOT authorization to mutate a trigram target
    this host cannot own (unknown same-name / quarantined #30 shape)."""
    db.create_session(session_id="s1", source="cli")
    db.set_session_title("s1", "skip trigram")
    db._sessions_trigram_available = False  # simulate quarantined/unknown
    statements = []
    db._conn.set_trace_callback(statements.append)
    try:
        db.rebuild_fts()
    finally:
        db._conn.set_trace_callback(None)
    rebuild_sql = [sql for sql in statements if "'rebuild'" in sql]
    rebuilt_tables = {_fts_table_from_special_sql(sql) for sql in rebuild_sql}
    assert "sessions_fts_trigram" not in rebuilt_tables
    # The required Unicode session index is still rebuilt.
    assert any(
        "INSERT INTO sessions_fts(sessions_fts) VALUES('rebuild')" in sql
        for sql in statements
    )


# ── Message-CJK rebuild now shares the generic engine ─────────────────────

def test_message_cjk_rebuild_step_delegates_to_shared_engine(db, monkeypatch):
    calls = []

    def _spy_step(spec=None):
        calls.append(spec)
        return False

    monkeypatch.setattr(db, "fts_rebuild_step", _spy_step)
    assert db.fts_cjk_rebuild_step() is False
    assert calls == [_FTS_MESSAGE_CJK_SPEC]


def test_message_cjk_rebuild_status_delegates_to_shared_engine(db, monkeypatch):
    calls = []
    monkeypatch.setattr(
        db,
        "fts_rebuild_status",
        lambda spec=None: calls.append(spec) or None,
    )
    assert db.fts_cjk_rebuild_status() is None
    assert calls == [_FTS_MESSAGE_CJK_SPEC]


def test_message_cjk_rebuild_finish_delegates_to_shared_engine(db, monkeypatch):
    calls = []
    monkeypatch.setattr(
        db,
        "_fts_rebuild_finish",
        lambda spec=None: calls.append(spec),
    )
    db._fts_cjk_rebuild_finish()
    assert calls == [_FTS_MESSAGE_CJK_SPEC]


# ── Health probes cover the session indexes (issue #27) ───────────────────

def _build_db_with_session(tmp_path, title="pizzaparty"):
    db_path = tmp_path / "state.db"
    session_db = SessionDB(db_path=db_path)
    session_db.create_session(session_id="s1", source="cli")
    session_db.set_session_title("s1", title)
    session_db.close()
    return db_path


def test_health_read_probe_detects_corrupt_session_unicode_index(tmp_path):
    from hermes_state import _db_opens_cleanly

    db_path = _build_db_with_session(tmp_path)
    assert _db_opens_cleanly(db_path) is None  # healthy before
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "UPDATE sessions_fts_data SET block = X'BADC0FFEE0DDF00D'"
        )
        conn.commit()
    finally:
        conn.close()
    reason = _db_opens_cleanly(db_path)
    assert reason is not None
    assert "sessions_fts" in reason


def test_health_read_probe_detects_corrupt_session_trigram_index(tmp_path):
    from hermes_state import _db_opens_cleanly

    db_path = _build_db_with_session(tmp_path, title="pizza pie party")
    assert _db_opens_cleanly(db_path) is None
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "UPDATE sessions_fts_trigram_data SET block = X'BADC0FFEE0DDF00D'"
        )
        conn.commit()
    finally:
        conn.close()
    reason = _db_opens_cleanly(db_path)
    assert reason is not None
    assert "sessions_fts_trigram" in reason


def test_health_write_probe_detects_corrupt_session_fts_write(tmp_path):
    """The rollback-only write probe inserts non-null session metadata, so a
    broken session FTS write path is detected without persisting probe data."""
    from hermes_state import _db_opens_cleanly

    db_path = _build_db_with_session(tmp_path)
    conn = sqlite3.connect(str(db_path))
    try:
        # Write-class corruption: base reads fine, session FTS writes fail.
        conn.execute(
            "UPDATE sessions_fts_data SET block = X'DEADBEEFDEADBEEF'"
        )
        conn.commit()
    finally:
        conn.close()
    reason = _db_opens_cleanly(db_path)
    assert reason is not None


def test_health_write_probe_persists_no_rows(tmp_path):
    from hermes_state import _db_opens_cleanly

    db_path = _build_db_with_session(tmp_path)
    assert _db_opens_cleanly(db_path) is None
    conn = sqlite3.connect(str(db_path))
    try:
        probe_rows = conn.execute(
            "SELECT COUNT(*) FROM sessions "
            "WHERE id LIKE '_hermes_fts_health_probe_%'"
        ).fetchone()[0]
        messages = conn.execute(
            "SELECT COUNT(*) FROM messages "
            "WHERE session_id LIKE '_hermes_fts_health_probe_%'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert probe_rows == 0
    assert messages == 0


# ── Trigger inventory / convergence derives from the registry ─────────────

def test_drop_fts_triggers_covers_all_owned_session_triggers(db):
    # Message + session Unicode + session trigram triggers exist on a default
    # host (the optional CJK members are gated off without a tokenizer).
    expected_present = (
        _fts_descriptor("messages_fts").trigger_names
        + _fts_descriptor("messages_fts_trigram").trigger_names
        + _fts_descriptor("sessions_fts").trigger_names
        + _fts_descriptor("sessions_fts_trigram").trigger_names
    )
    for name in expected_present:
        row = db._conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'trigger' AND name = ?",
            (name,),
        ).fetchone()
        assert row is not None, f"expected owned trigger {name}"
    # _drop_fts_triggers removes every owned registry trigger name (message
    # AND session), including the #30 trigram ones.
    db._drop_fts_triggers(db._conn)
    db._conn.commit()
    all_owned = [n for d in FTS_INDEXES for n in d.trigger_names]
    placeholders = ", ".join("?" for _ in all_owned)
    remaining = [
        r[0]
        for r in db._conn.execute(
            "SELECT name FROM sqlite_master "
            f"WHERE type = 'trigger' AND name IN ({placeholders})",
            all_owned,
        ).fetchall()
    ]
    assert remaining == []


def test_migrate_converges_broad_session_unicode_update_trigger(db):
    """A broad dev-era sessions_fts_update converges to the canonical narrow
    AFTER UPDATE OF title, id, display_name (issue #27)."""
    db._conn.execute("DROP TRIGGER IF EXISTS sessions_fts_update")
    db._conn.execute(
        """
        CREATE TRIGGER sessions_fts_update AFTER UPDATE ON sessions
        BEGIN
            SELECT 1;
        END
        """
    )
    db._conn.commit()
    before = db._conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'trigger' AND name = 'sessions_fts_update'"
    ).fetchone()[0]
    assert "AFTER UPDATE OF" not in " ".join(before.split()).upper()

    dropped = db._migrate_broad_fts_update_triggers(db._conn)
    db._conn.commit()
    assert dropped >= 1
    after = db._conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'trigger' AND name = 'sessions_fts_update'"
    ).fetchone()[0]
    assert "AFTER UPDATE OF title, id, display_name" in after


def test_migrate_converges_broad_session_cjk_update_trigger(db):
    """A broad sessions_fts_cjk_update converges to the canonical narrow DDL
    on a tokenizer-capable host (issue #27/#26)."""
    if not db._sessions_cjk_worker_operable:
        pytest.skip("no loadable CJK tokenizer on this host")
    db._conn.execute("DROP TRIGGER IF EXISTS sessions_fts_cjk_update")
    db._conn.execute(
        """
        CREATE TRIGGER sessions_fts_cjk_update AFTER UPDATE ON sessions
        BEGIN
            SELECT 1;
        END
        """
    )
    db._conn.commit()
    dropped = db._migrate_broad_fts_update_triggers(db._conn)
    db._conn.commit()
    assert dropped >= 1
    after = db._conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'trigger' AND name = 'sessions_fts_cjk_update'"
    ).fetchone()[0]
    assert "AFTER UPDATE OF title, id, display_name" in after


def test_migrate_leaves_trigram_update_triggers_under_30_identity(db):
    """sessions_fts_trigram_update_before/_after are canonical narrow #30
    triggers (BEFORE/AFTER UPDATE OF title, id, display_name) and must never
    be rewritten by the broad→narrow migration."""
    for name in (
        "sessions_fts_trigram_update_before",
        "sessions_fts_trigram_update_after",
    ):
        row = db._conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'trigger' AND name = ?",
            (name,),
        ).fetchone()
        assert row is not None
        sql = row[0]
        assert "UPDATE OF title, id, display_name" in sql
    # Migration is a no-op on an already-converged DB.
    assert db._migrate_broad_fts_update_triggers(db._conn) == 0
