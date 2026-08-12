"""Storage-v2 settlement state machine for the session-metadata FTS
architecture (#31).

The whole ticket is the question: **when may ``fts_storage_version = 2`` be
claimed, and does that claim survive/recover across interruption and
reopen?** These tests exercise the ONE shared completion predicate
(``SessionDB._fts_storage_v2_blocker``) and the single evaluator-driven
surface that startup auto-settlement, ``fts_optimize_available()``, the
foreground pre-VACUUM refusal, and the final transactional stamp all
consume — so no completion decision can ever diverge.

Scoped per #31: settlement/completion ONLY. Per-lane worker/repair/rebuild
engines belong to #25/#26/#27/#30; search routing/ranking to #14/#28;
message-layout demote/teardown to the v23 path.
"""

import sqlite3
import time

import pytest

from hermes_state import FTS_STORAGE_VERSION, SCHEMA_SQL, SessionDB
from hermes_state_common import (
    FTS_CJK_STALE_KEY,
    FTS_SESSION_CJK_STALE_KEY,
    FTS_SESSION_TRIGRAM_STALE_KEY,
)


@pytest.fixture()
def db(tmp_path):
    """Fresh writable SessionDB over a temp file (v23 message layout)."""
    db_path = tmp_path / "state.db"
    session_db = SessionDB(db_path=db_path)
    yield session_db
    session_db.close()


def _cjk_incapable_db(tmp_path, monkeypatch):
    """Fresh SessionDB on a host that cannot load the optional cjk tokenizer
    (the durable-state cases must not depend on local CJK capability)."""
    monkeypatch.setenv("HERMES_FTS5_CJK_SO", str(tmp_path / "absent-cjk.so"))
    return SessionDB(db_path=tmp_path / "state.db")


def _plant(db, **markers):
    """Write durable ``state_meta`` markers (H/P/stale) directly."""
    for key, value in markers.items():
        db.set_meta(key, str(value))


def _blocker(db):
    """Run the shared SELECT-only storage-v2 evaluator on the live conn."""
    return db._fts_storage_v2_blocker(db._conn)


def _build_populated_sessions_db(db_path, n=12):
    """DB with ``n`` canonical sessions and NO FTS objects yet — the shape
    every startup ensure path migrates/claims over (same builder shape as
    the #30 suite)."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(SCHEMA_SQL)
    t0 = time.time()
    conn.executemany(
        "INSERT INTO sessions (row_id, id, source, started_at, title) "
        "VALUES (?, ?, 'cli', ?, ?)",
        [(i, f"s{i}", t0 + i, f"Title {i}") for i in range(1, n + 1)],
    )
    conn.commit()
    conn.close()


def _build_unknown_same_name_trigram_db(db_path):
    """DB whose ``sessions_fts_trigram`` is an UNRECOGNIZED same-name object
    (a unicode61 vtable, not the modern #30 shape) and whose canonical
    ``sessions`` table is EMPTY (so no session lane stages H/P markers and
    the trigram state is the only blocker). #31 must refuse v2 and leave it
    byte/schema-identical — no destructive convergence."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "CREATE VIRTUAL TABLE sessions_fts_trigram USING fts5(x, "
        "tokenize='unicode61')"
    )
    conn.commit()
    conn.close()


def _trigram_ddl(db):
    with db._read_ctx() as conn:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' "
            "AND name = 'sessions_fts_trigram'"
        ).fetchone()
    return (row[0] if isinstance(row, sqlite3.Row) else row[0]) if row else ""


def _message_docsize(db):
    with db._read_ctx() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM messages_fts_docsize"
        ).fetchone()[0]


# ── The single evaluator: refusal-state matrix (commit 1) ────────────────

def test_fresh_db_is_acceptance_complete(db):
    """No durable work anywhere → no blocker; v2 may settle; optimize not
    advertised."""
    assert _blocker(db) is None
    assert db.get_meta("fts_storage_version") == str(FTS_STORAGE_VERSION)
    assert db.fts_optimize_available() is False


def test_fts5_unavailable_refuses_and_is_not_actionable(db, monkeypatch):
    monkeypatch.setattr(db, "_fts_enabled", False)
    assert _blocker(db) == ("fts5_unavailable", False)
    assert db.fts_optimize_available() is False


def test_message_hp_blocks_and_is_actionable(db):
    _plant(db, fts_rebuild_high_water=5, fts_rebuild_progress=0)
    assert _blocker(db) == ("backfill_incomplete", True)
    assert db.fts_optimize_available() is True


def test_message_p_only_fails_closed(db):
    """A stray P-only leftover is never evidence of completion."""
    _plant(db, fts_rebuild_progress=3)
    assert _blocker(db) == ("backfill_incomplete", True)


def test_message_trash_blocks(db):
    db._conn.execute("CREATE TABLE fts_v22_trash_messages_fts_data (x)")
    db._conn.commit()
    assert _blocker(db) == ("teardown_incomplete", True)
    assert db.fts_optimize_available() is True


def test_session_unicode_hp_blocks_while_messages_settled(db):
    """Session Unicode pending blocks v2 even when the message base is fully
    settled (the pre-#31 startup stamp missed exactly this)."""
    db.create_session(session_id="s1", source="cli")
    _plant(db, fts_session_rebuild_high_water=1, fts_session_rebuild_progress=0)
    assert _blocker(db) == ("session_unicode_incomplete", True)
    assert db.fts_optimize_available() is True


def test_session_unicode_p_only_fails_closed(db):
    db.create_session(session_id="s1", source="cli")
    _plant(db, fts_session_rebuild_progress=1)
    assert _blocker(db)[0] == "session_unicode_incomplete"


def test_message_cjk_hp_blocks_even_on_incapable_host(tmp_path, monkeypatch):
    d = _cjk_incapable_db(tmp_path, monkeypatch)
    try:
        assert not d._fts_cjk_loaded
        _plant(d, fts_cjk_rebuild_high_water=5, fts_cjk_rebuild_progress=0)
        assert _blocker(d) == ("message_cjk_incomplete", False)
        # Blocked + not actionable here → not advertised as optimizable.
        assert d.fts_optimize_available() is False
    finally:
        d.close()


def test_message_cjk_stale_only_blocks(tmp_path, monkeypatch):
    d = _cjk_incapable_db(tmp_path, monkeypatch)
    try:
        _plant(d, **{FTS_CJK_STALE_KEY: 1})
        assert _blocker(d)[0] == "message_cjk_incomplete"
    finally:
        d.close()


def test_session_cjk_hp_blocks_even_on_incapable_host(tmp_path, monkeypatch):
    d = _cjk_incapable_db(tmp_path, monkeypatch)
    try:
        _plant(d, fts_session_cjk_rebuild_high_water=5,
               fts_session_cjk_rebuild_progress=0)
        assert _blocker(d) == ("session_cjk_incomplete", False)
        assert d.fts_optimize_available() is False
    finally:
        d.close()


def test_session_cjk_stale_blocks_even_on_incapable_host(tmp_path, monkeypatch):
    d = _cjk_incapable_db(tmp_path, monkeypatch)
    try:
        _plant(d, **{FTS_SESSION_CJK_STALE_KEY: 1})
        assert _blocker(d)[0] == "session_cjk_incomplete"
    finally:
        d.close()


def test_optional_cjk_absence_on_incapable_host_does_not_block(
    tmp_path, monkeypatch
):
    """Never-established optional CJK with no durable state is valid
    degraded absence — it must not deadlock the required settlement."""
    d = _cjk_incapable_db(tmp_path, monkeypatch)
    try:
        assert _blocker(d) is None
        assert d.fts_optimize_available() is False
    finally:
        d.close()


def test_session_trigram_hp_blocks_when_local_serving_false(db, monkeypatch):
    """The block decision is durable-state based: even with the local serving
    flag forced False, trigram H/P still blocks v2 (capability is never
    evidence of completion). Only the 'actionable here' flag drops."""
    _plant(db, fts_session_trigram_rebuild_high_water=1,
           fts_session_trigram_rebuild_progress=0)
    monkeypatch.setattr(db, "_sessions_trigram_available", False)
    assert _blocker(db) == ("session_trigram_incomplete", False)
    assert db.fts_optimize_available() is False


def test_session_trigram_stale_blocks(db):
    _plant(db, **{FTS_SESSION_TRIGRAM_STALE_KEY: 1})
    assert _blocker(db)[0] == "session_trigram_incomplete"


def test_trigram_unknown_same_name_blocks_and_survives_untouched(tmp_path):
    """The historical fork-only ``tokenize='simple'`` same-name root is an
    ``unknown_same_name`` shape: #31 refuses v2, fail-closed, and must NOT
    delete or demote it."""
    db_path = tmp_path / "u.db"
    _build_unknown_same_name_trigram_db(db_path)
    d = SessionDB(db_path=db_path)
    try:
        assert _blocker(d) == ("session_trigram_unknown_same_name", False)
        assert d.fts_optimize_available() is False
        # Byte/schema-identical: no destructive convergence.
        assert "tokenize='unicode61'" in _trigram_ddl(d)
    finally:
        d.close()
