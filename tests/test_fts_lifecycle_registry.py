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


# ── Offline / destructive repair covers the session indexes (issue #27) ───

def test_offline_repair_rebuilds_corrupt_session_unicode_index(tmp_path):
    from hermes_state import _db_opens_cleanly, repair_state_db_schema

    db_path = _build_db_with_session(tmp_path)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "UPDATE sessions_fts_data SET block = X'BADC0FFEE0DDF00D'"
        )
        conn.commit()
    finally:
        conn.close()
    assert _db_opens_cleanly(db_path) is not None
    report = repair_state_db_schema(db_path, backup=False)
    assert report["repaired"] is True
    assert report["strategy"] == "rebuild_fts"
    assert _db_opens_cleanly(db_path) is None
    # Canonical rows unchanged.
    conn = sqlite3.connect(str(db_path))
    try:
        titles = [
            r[0]
            for r in conn.execute(
                "SELECT title FROM sessions WHERE id = 's1'"
            )
        ]
    finally:
        conn.close()
    assert titles == ["pizzaparty"]
    # Session search works again through the rebuilt index.
    reopened = SessionDB(db_path=db_path)
    try:
        assert reopened.resolve_session_by_title("pizzaparty") == "s1"
    finally:
        reopened.close()


def test_offline_repair_rebuilds_corrupt_session_trigram_index(tmp_path):
    from hermes_state import _db_opens_cleanly, repair_state_db_schema

    db_path = _build_db_with_session(tmp_path, title="pizza pie party")
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "UPDATE sessions_fts_trigram_data SET block = X'BADC0FFEE0DDF00D'"
        )
        conn.commit()
    finally:
        conn.close()
    assert _db_opens_cleanly(db_path) is not None
    report = repair_state_db_schema(db_path, backup=False)
    assert report["repaired"] is True
    assert report["strategy"] == "rebuild_fts"
    assert _db_opens_cleanly(db_path) is None


def test_owned_fts_object_names_covers_six_indexes(db):
    from hermes_state import _owned_fts_object_names

    names = _owned_fts_object_names(db._conn)
    for desc in FTS_INDEXES:
        assert desc.table in names
        for shadow in ("data", "idx", "content", "docsize", "config"):
            assert f"{desc.table}_{shadow}" in names
        for trig in desc.trigger_names:
            assert trig in names
        for _obj_type, obj_name in desc.derived_objects:
            assert obj_name in names
    assert len(names) == len(set(names))  # no duplicates


def _install_foreign_trigram_source(conn):
    """Replace the canonical derived VIEW with a raw (uncompacted) foreign
    one — real mixed DDL, the state #30's ownership classifier must reject."""
    conn.execute("DROP VIEW IF EXISTS sessions_fts_trigram_src")
    conn.execute(
        "CREATE VIEW sessions_fts_trigram_src AS "
        "SELECT row_id, title, id, display_name FROM sessions"
    )
    conn.commit()


def _install_foreign_trigram_triggers(conn, names=None):
    """Replace the named canonical trigram triggers with foreign inert
    same-name occupants (``AFTER UPDATE ON sessions ... SELECT 1``)."""
    names = names or (
        "sessions_fts_trigram_insert",
        "sessions_fts_trigram_delete",
        "sessions_fts_trigram_update_before",
        "sessions_fts_trigram_update_after",
    )
    for name in names:
        conn.execute(f"DROP TRIGGER IF EXISTS {name}")
        conn.execute(
            f"CREATE TRIGGER {name} AFTER UPDATE ON sessions "
            "BEGIN SELECT 1; END"
        )
    conn.commit()


def test_owned_objects_fail_closed_for_foreign_trigram_source(db):
    """Canonical root + foreign (mismatched) source VIEW is NOT owned — the
    destructive namespace list must exclude the trigram (real mixed DDL, not
    a mock)."""
    from hermes_state import _owned_fts_object_names

    _install_foreign_trigram_source(db._conn)
    names = _owned_fts_object_names(db._conn)
    assert not any(n.startswith("sessions_fts_trigram") for n in names)
    # The other five members are still owned.
    for table in (
        "messages_fts",
        "messages_fts_trigram",
        "messages_fts_cjk",
        "sessions_fts",
        "sessions_fts_cjk",
    ):
        assert table in names


def test_owned_objects_fail_closed_for_foreign_trigram_trigger(db):
    """Canonical root/source + foreign same-name trigger occupant is NOT
    owned — destructive namespace list excludes the trigram."""
    from hermes_state import _owned_fts_object_names

    _install_foreign_trigram_triggers(
        db._conn, names=("sessions_fts_trigram_update_before",)
    )
    names = _owned_fts_object_names(db._conn)
    assert not any(n.startswith("sessions_fts_trigram") for n in names)


def test_destructive_repair_leaves_foreign_trigram_source_untouched(tmp_path):
    """Strategy 2 must not delete a canonical root whose source VIEW is
    foreign — the foreign VIEW stays byte/DDL-identical."""
    from hermes_state import _drop_owned_fts_derived_schema

    db_path = _build_db_with_session(tmp_path, title="pizza pie party")
    conn = sqlite3.connect(str(db_path))
    _install_foreign_trigram_source(conn)
    before_sql = conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE name = 'sessions_fts_trigram_src'"
    ).fetchone()[0]
    _drop_owned_fts_derived_schema(conn)
    after_sql = conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE name = 'sessions_fts_trigram_src'"
    ).fetchone()[0]
    assert after_sql == before_sql
    conn.close()


def test_destructive_repair_leaves_foreign_trigram_trigger_untouched(tmp_path):
    """Strategy 2 must not delete a canonical root/source namespace that has
    a foreign same-name trigger occupant — the foreign trigger stays
    byte/DDL-identical and the (unowned) trigram root survives."""
    from hermes_state import _drop_owned_fts_derived_schema

    db_path = _build_db_with_session(tmp_path, title="pizza pie party")
    conn = sqlite3.connect(str(db_path))
    _install_foreign_trigram_triggers(
        conn, names=("sessions_fts_trigram_update_before",)
    )
    before_sql = conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'trigger' AND name = 'sessions_fts_trigram_update_before'"
    ).fetchone()[0]
    _drop_owned_fts_derived_schema(conn)
    after_sql = conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'trigger' AND name = 'sessions_fts_trigram_update_before'"
    ).fetchone()[0]
    assert after_sql == before_sql
    # The excluded (unowned) trigram root survives too.
    assert conn.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type = 'table' AND name = 'sessions_fts_trigram'"
    ).fetchone() is not None
    conn.close()


def test_drop_fts_triggers_leaves_foreign_trigram_trigger_untouched(db):
    """The whole-FTS5-unavailable teardown drops canonical triggers but never
    a foreign occupant of a sessions_fts_trigram_* name (#30 preflight)."""
    _install_foreign_trigram_triggers(
        db._conn, names=("sessions_fts_trigram_update_before",)
    )
    before_sql = db._conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'trigger' AND name = 'sessions_fts_trigram_update_before'"
    ).fetchone()[0]
    db._drop_fts_triggers(db._conn)
    db._conn.commit()
    after_sql = db._conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'trigger' AND name = 'sessions_fts_trigram_update_before'"
    ).fetchone()[0]
    assert after_sql == before_sql  # foreign occupant survives
    # Canonical message/session triggers were still dropped.
    left = db._conn.execute(
        "SELECT COUNT(*) FROM sqlite_master "
        "WHERE type = 'trigger' AND name IN "
        "('messages_fts_insert', 'sessions_fts_insert')"
    ).fetchone()[0]
    assert left == 0


def test_offline_repair_leaves_foreign_trigram_untouched(tmp_path):
    """Strategy 0 rebuilds owned indexes but never 'rebuild' a foreign
    same-name sessions_fts_trigram (which can legally accept the command).
    The corrupt foreign data is outside owned repair, so auto-recovery fails
    closed (never reports success while corruption survives) and the foreign
    objects stay byte/DDL-identical."""
    from hermes_state import repair_state_db_schema

    db_path = _build_db_with_session(tmp_path, title="pizza pie party")
    conn = sqlite3.connect(str(db_path))
    # Foreign occupants across the whole trigram trigger namespace.
    _install_foreign_trigram_triggers(conn)
    # Corrupt an OWNED index (sessions_fts) so repair actually runs...
    conn.execute("UPDATE sessions_fts_data SET block = X'BADC0FFEE0DDF00D'")
    # ...and corrupt the foreign-gated trigram data too (repair must not
    # clear it, and must not report success while it survives).
    conn.execute(
        "UPDATE sessions_fts_trigram_data SET block = X'BADC0FFEE0DDF00D'"
    )
    conn.commit()
    before_trigger = conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'trigger' AND name = 'sessions_fts_trigram_update_before'"
    ).fetchone()[0]
    conn.close()

    report = repair_state_db_schema(db_path, backup=False)
    # Fail closed: a foreign-gated corrupt index cannot be cleared, so repair
    # must not report success (it must never delete/rebuild the foreign
    # object to make the DB "healthy").
    assert report["repaired"] is False

    conn = sqlite3.connect(str(db_path))
    try:
        after_trigger = conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'trigger' AND name = 'sessions_fts_trigram_update_before'"
        ).fetchone()[0]
        assert after_trigger == before_trigger  # foreign occupant untouched
        # The corrupt foreign trigram data was NOT rebuilt (untouched).
        bad = conn.execute(
            "SELECT COUNT(*) FROM sessions_fts_trigram_data "
            "WHERE block = X'BADC0FFEE0DDF00D'"
        ).fetchone()[0]
        assert bad >= 1
    finally:
        conn.close()


def test_destructive_repair_removes_owned_objects_preserves_canonical(tmp_path):
    from hermes_state import _drop_owned_fts_derived_schema

    db_path = tmp_path / "state.db"
    session_db = SessionDB(db_path=db_path)
    session_db.create_session(session_id="s1", source="cli")
    session_db.set_session_title("s1", "pizzaparty")
    session_db.append_message("s1", role="user", content="hello world")
    session_db.close()

    conn = sqlite3.connect(str(db_path))
    try:
        before_sessions = conn.execute(
            "SELECT row_id, id, title FROM sessions ORDER BY row_id"
        ).fetchall()
        before_messages = conn.execute(
            "SELECT id, content FROM messages ORDER BY id"
        ).fetchall()
        assert before_sessions and before_messages
        _drop_owned_fts_derived_schema(conn)
        remaining = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type IN ('table','view','trigger','index') "
                "AND (name LIKE 'messages_fts%' OR name LIKE 'sessions_fts%')"
            ).fetchall()
        ]
        assert remaining == []
        after_sessions = conn.execute(
            "SELECT row_id, id, title FROM sessions ORDER BY row_id"
        ).fetchall()
        after_messages = conn.execute(
            "SELECT id, content FROM messages ORDER BY id"
        ).fetchall()
        assert after_sessions == before_sessions
        assert after_messages == before_messages
    finally:
        conn.close()

    # Reopen recreates the supported derived schema; canonical search works.
    reopened = SessionDB(db_path=db_path)
    try:
        assert len(reopened.search_messages("hello")) == 1
        assert reopened.resolve_session_by_title("pizzaparty") == "s1"
    finally:
        reopened.close()


# ── Read-only capability discovery + shared lane surface (issue #27) ──────

def test_read_only_discovers_session_unicode_and_trigram(tmp_path):
    """A read-only open discovers the existing session Unicode/trigram
    capabilities with SELECTs only (no DDL / mutation), and can serve them."""
    db_path = _build_db_with_session(tmp_path, title="pizza pie party")
    ro = SessionDB(db_path=db_path, read_only=True)
    try:
        assert ro._sessions_fts_available is True
        assert ro._sessions_trigram_available is True
        # Read-only session search can use the discovered lanes.
        fts_ok, candidates = ro._fts_metadata_candidates("pizza")
        assert fts_ok is True
        assert any(c["id"] == "s1" for c in candidates)
        servable, trigram_candidates = ro._fts_session_trigram_candidates("pizza")
        assert servable is True
        assert any(c["id"] == "s1" for c in trigram_candidates)
    finally:
        ro.close()


def test_read_only_unknown_trigram_fail_closed(tmp_path):
    """A read-only open never serves a sessions_fts_trigram namespace with a
    foreign same-name trigger occupant — #30 fail-closed covers the trigger
    namespace, not just root + source VIEW (issue #27 review R3, real mixed
    DDL instead of a monkeypatched classifier)."""
    db_path = _build_db_with_session(tmp_path)
    conn = sqlite3.connect(str(db_path))
    _install_foreign_trigram_triggers(
        conn, names=("sessions_fts_trigram_update_before",)
    )
    conn.close()

    ro = SessionDB(db_path=db_path, read_only=True)
    try:
        assert ro._sessions_trigram_available is False
    finally:
        ro.close()


def test_lane_surface_sequences_pending_lanes(db, monkeypatch):
    """The shared deferred-rebuild lane surface runs every lane with pending
    work and skips lanes without it (issue #27)."""
    calls = []
    pending = {"pending": True, "total": 1, "indexed": 0, "percent": 0}
    monkeypatch.setattr(db, "fts_rebuild_status", lambda: pending)
    monkeypatch.setattr(db, "fts_cjk_rebuild_status", lambda: None)
    monkeypatch.setattr(db, "fts_session_rebuild_status", lambda: pending)
    monkeypatch.setattr(db, "fts_session_trigram_rebuild_status", lambda: None)
    monkeypatch.setattr(db, "fts_session_cjk_rebuild_status", lambda: None)
    monkeypatch.setattr(
        db, "fts_rebuild_step",
        lambda: calls.append("messages") or False,
    )
    monkeypatch.setattr(
        db, "fts_session_rebuild_step",
        lambda: calls.append("sessions") or False,
    )
    # A lane with no pending work must never be stepped.
    monkeypatch.setattr(
        db, "fts_cjk_rebuild_step",
        lambda: (_ for _ in ()).throw(
            AssertionError("cjk lane must not run without pending work")
        ),
    )

    db._fts_run_pending_lane_steps()
    assert calls == ["messages", "sessions"]


def test_fts_optimize_available_is_lane_driven(db):
    """fts_optimize_available advertises work through the shared lane surface
    — a session Unicode H marker is enough, and a healthy DB reports False."""
    assert db.fts_optimize_available() is False
    db.set_meta("fts_session_rebuild_high_water", "1")
    db.set_meta("fts_session_rebuild_progress", "0")
    assert db.fts_optimize_available() is True


# ── Six-index regression matrix (issue #27 commit 5) ──────────────────────

def test_vacuum_reaches_session_indexes_and_preserves_row_ids(db):
    """VACUUM reaches session FTS only through the shared optimize path (no
    session-specific VACUUM hook) and preserves sessions.row_id."""
    db.create_session(session_id="s1", source="cli")
    db.set_session_title("s1", "vacuum title")
    db.append_message("s1", role="user", content="vacuum needle")
    before = db._conn.execute(
        "SELECT row_id FROM sessions WHERE id = 's1'"
    ).fetchone()[0]
    db.vacuum()
    after = db._conn.execute(
        "SELECT row_id FROM sessions WHERE id = 's1'"
    ).fetchone()[0]
    assert after == before
    assert len(db.search_messages("vacuum")) == 1
    assert _session_fts_matches(db, "vacuum")


def test_rebuild_session_trigram_through_derived_compact_view(db):
    """Session trigram rebuild consumes the #30 derived compact VIEW — not
    raw metadata — so separator-bearing titles stay searchable by their
    compact form after repair."""
    db.create_session(session_id="s1", source="cli")
    db.set_session_title("s1", "AN-94 Rifle")
    # The trigger indexed the COMPACTED "AN94Rifle" (hyphen/space removed).
    assert _trigram_fts_matches(db, "an94")
    _corrupt_shadow(db, "sessions_fts_trigram_data")
    with pytest.raises(sqlite3.DatabaseError):
        _trigram_fts_matches(db, "an94")
    db.rebuild_fts()
    # A naive rebuild from raw "AN-94 Rifle" would NOT contain the substring
    # "an94"; reading through the derived VIEW restores the compact form.
    assert _trigram_fts_matches(db, "an94")


def test_runtime_recovery_repairs_corrupt_session_fts_write(db):
    """A session-metadata write hitting corrupt session FTS invokes the
    existing one-shot rebuild/retry and succeeds; canonical rows preserved.

    Uses the session INSERT path (``create_session``), whose trigger drives
    the real sessions_fts write. (A session-metadata UPDATE that fires the
    ``sessions_fts_update`` 'delete' half hits an FTS5 in-transaction
    connection-state quirk that blocks an in-place rebuild on the SAME
    connection; that path degrades to the registry-driven offline repair /
    startup auto-heal, which fixes it on a fresh connection.)
    """
    db.create_session(session_id="s1", source="cli")
    db.set_session_title("s1", "original title")
    # Write-class corruption: base tables read fine, session FTS writes fail.
    db._conn.execute(
        "UPDATE sessions_fts_data SET block = X'DEADBEEFDEADBEEF'"
    )
    db._conn.commit()
    ok = db.create_session(session_id="s2", source="cli")
    assert ok == "s2"
    assert db._fts_runtime_rebuild_attempted is True
    # The rebuilt session index serves both rows.
    assert _session_fts_matches(db, "s2")
