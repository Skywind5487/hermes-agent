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
    SESSIONS_FTS_TRIGRAM_SQL,
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


def _build_legacy_internal_db(db_path, table_ddl):
    """DB with an internal-content (no ``content=``) FTS virtual table over
    an EMPTY canonical ``sessions`` table (so no lane stages markers)."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(SCHEMA_SQL)
    conn.execute(table_ddl)
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
        assert d.get_meta("fts_storage_version") is None
        assert d.fts_optimize_available() is False
        # Byte/schema-identical: no destructive convergence.
        assert "tokenize='unicode61'" in _trigram_ddl(d)
    finally:
        d.close()


# ── Startup auto-settlement + interruption / reopen (commit 3) ────────────

def _build_claim_before_schema_db(db_path, n=3):
    """DB with ``n`` sessions and a durable session-Unicode H/P claim but NO
    external FTS table yet (the #76832 claim-before-schema crash window)."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(SCHEMA_SQL)
    t0 = time.time()
    conn.executemany(
        "INSERT INTO sessions (row_id, id, source, started_at) "
        "VALUES (?, ?, 'cli', ?)",
        [(i, f"s{i}", t0 + i) for i in range(1, n + 1)],
    )
    conn.execute(
        "INSERT INTO state_meta (key, value) VALUES (?, ?), (?, ?)",
        ("fts_session_rebuild_high_water", str(n),
         "fts_session_rebuild_progress", "0"),
    )
    conn.commit()
    conn.close()


def test_populated_first_open_does_not_stamp_v2(tmp_path):
    """The core #31 startup rule: a populated DB's first open stages session
    H/P claims and therefore does NOT stamp v2 (work remains)."""
    db_path = tmp_path / "s.db"
    _build_populated_sessions_db(db_path, n=12)
    d = SessionDB(db_path=db_path)
    try:
        assert d.get_meta("fts_session_rebuild_high_water") == "12"
        assert d.get_meta("fts_storage_version") is None
        assert d.fts_optimize_available() is True
    finally:
        d.close()


def test_claim_before_schema_reopen_preserves_claim_and_refuses_v2(tmp_path):
    """#76832 claim-before-schema: a durable claim committed before the
    external table existed survives reopen — the claim is preserved, the
    schema is re-ensured, and v2 stays absent (work remains)."""
    db_path = tmp_path / "s.db"
    _build_claim_before_schema_db(db_path, n=3)
    d = SessionDB(db_path=db_path)
    try:
        assert d.get_meta("fts_session_rebuild_high_water") == "3"
        assert d.get_meta("fts_session_rebuild_progress") == "0"
        assert d.get_meta("fts_storage_version") is None
        assert d.fts_optimize_available() is True
    finally:
        d.close()


def test_reopen_after_partial_backfill_resumes_and_does_not_stamp(tmp_path):
    """A crash mid-backfill (H/P staged, backfill unfinished): reopen
    preserves the claim, stays incomplete (no v2), and a re-run settles v2."""
    db_path = tmp_path / "s.db"
    _build_populated_sessions_db(db_path, n=12)
    d1 = SessionDB(db_path=db_path)
    try:
        # Markers staged at open; the process "crashes" before any completion.
        assert d1.get_meta("fts_session_rebuild_high_water") == "12"
        assert d1.get_meta("fts_session_rebuild_progress") == "0"
        assert d1.get_meta("fts_storage_version") is None
    finally:
        d1.close()

    d2 = SessionDB(db_path=db_path)
    try:
        assert d2.get_meta("fts_session_rebuild_high_water") == "12"
        assert d2.get_meta("fts_storage_version") is None
        assert d2.fts_optimize_available() is True
        result = d2.optimize_fts_storage(vacuum=False)
        assert result["ok"] is True, result
        assert d2.get_meta("fts_storage_version") == str(FTS_STORAGE_VERSION)
    finally:
        d2.close()


def test_h_without_p_repair_keeps_v2_absent_until_rebuild(tmp_path):
    """H-without-P is non-settled durable state: the shared repair restores
    P only after a known-empty reset, and v2 stays absent until the rebuild
    completes."""
    db_path = tmp_path / "s.db"
    _build_populated_sessions_db(db_path, n=5)
    d = SessionDB(db_path=db_path)
    try:
        d.set_meta("fts_rebuild_high_water", "5")
        assert d.get_meta("fts_rebuild_progress") is None
        assert d.get_meta("fts_storage_version") is None
        d._repair_optimize_bookkeeping()
        assert d.get_meta("fts_rebuild_progress") == "0"
        assert d.get_meta("fts_storage_version") is None
        assert _blocker(d)[0] == "backfill_incomplete"
        assert d.fts_optimize_available() is True
    finally:
        d.close()


def test_stale_after_healthy_optional_target_blocks_reopen_v2(tmp_path):
    """A formerly healthy modern trigram target quarantined (stale written,
    owned triggers dropped) can never be served as complete: reopen refuses
    v2 until a capable recovery re-establishes and backfills the lane."""
    db_path = tmp_path / "p1.db"
    _build_populated_sessions_db(db_path, n=2)
    d1 = SessionDB(db_path=db_path)
    try:
        assert d1.get_meta("fts_session_trigram_rebuild_high_water") == "2"
    finally:
        d1.close()
    # Simulate the tokenizer-less peer's quarantine side effects.
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO state_meta (key, value) VALUES (?, '1') "
        "ON CONFLICT(key) DO UPDATE SET value = '1'",
        (FTS_SESSION_TRIGRAM_STALE_KEY,),
    )
    for name in (
        "sessions_fts_trigram_insert",
        "sessions_fts_trigram_delete",
        "sessions_fts_trigram_update_before",
        "sessions_fts_trigram_update_after",
    ):
        conn.execute(f"DROP TRIGGER IF EXISTS {name}")
    conn.commit()
    conn.close()
    # Capable reopen recovers the stale target from canonical rows — but the
    # lane is still pending, so v2 must remain absent.
    d2 = SessionDB(db_path=db_path)
    try:
        assert d2.get_meta("fts_storage_version") is None
        assert d2.get_meta("fts_session_trigram_rebuild_high_water") == "2"
        assert d2.fts_optimize_available() is True
    finally:
        d2.close()


def test_final_transactional_recheck_refuses_race_before_stamp(
    tmp_path, monkeypatch
):
    """#76832 race rule: a blocker that appears after the pre-VACUUM
    preflight (a concurrent writer) must still refuse the stamp inside the
    final write transaction that would have written it."""
    db_path = tmp_path / "s.db"
    _build_populated_sessions_db(db_path, n=3)
    d = SessionDB(db_path=db_path)
    # Keep a real durable blocker alive through the backfill pass (an
    # unfinishable session lane — the same shape as the existing
    # settle-refusal test), while the pre-VACUUM preflight pretends complete.
    d.fts_session_rebuild_step = lambda: False  # type: ignore[method-assign]
    calls = {"n": 0}
    real = d._fts_storage_v2_blocker

    def _race(conn):
        calls["n"] += 1
        # First call is the pre-VACUUM preflight — pretend it saw complete (a
        # concurrent writer seeds work just afterwards). The final
        # transactional re-check must still see the real durable state.
        if calls["n"] == 1:
            return None
        return real(conn)

    monkeypatch.setattr(d, "_fts_storage_v2_blocker", _race)
    try:
        result = d.optimize_fts_storage(vacuum=False)
        assert result["ok"] is False
        assert result.get("reason") == "session_unicode_incomplete"
        assert d.get_meta("fts_storage_version") is None
    finally:
        d.close()


def test_completed_v2_reopen_creates_no_new_work(tmp_path):
    """A settled v2 DB reopened stays settled: no new H/P/stale markers, no
    rebuild work advertised, and the claim persists."""
    db_path = tmp_path / "s.db"
    _build_populated_sessions_db(db_path, n=12)
    d1 = SessionDB(db_path=db_path)
    try:
        result = d1.optimize_fts_storage(vacuum=False)
        assert result["ok"] is True, result
        assert d1.get_meta("fts_storage_version") == str(FTS_STORAGE_VERSION)
    finally:
        d1.close()

    d2 = SessionDB(db_path=db_path)
    try:
        assert d2.get_meta("fts_storage_version") == str(FTS_STORAGE_VERSION)
        assert d2.fts_optimize_available() is False
        assert _blocker(d2) is None
        # No durable markers were recreated on reopen.
        for key in (
            "fts_rebuild_high_water", "fts_rebuild_progress",
            "fts_cjk_rebuild_high_water", "fts_cjk_rebuild_progress",
            FTS_CJK_STALE_KEY,
            "fts_session_rebuild_high_water", "fts_session_rebuild_progress",
            "fts_session_trigram_rebuild_high_water",
            "fts_session_trigram_rebuild_progress",
            FTS_SESSION_TRIGRAM_STALE_KEY,
            "fts_session_cjk_rebuild_high_water",
            "fts_session_cjk_rebuild_progress",
            FTS_SESSION_CJK_STALE_KEY,
        ):
            assert d2.get_meta(key) is None, key
    finally:
        d2.close()


# ── Six-index acceptance matrix (commit 4) ────────────────────────────────

def test_complete_six_index_settlement_stamps_v2(tmp_path):
    """Full acceptance: with every required + applicable lane complete,
    optimize stamps v2, no work remains, and search behavior is unchanged."""
    db_path = tmp_path / "s.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(SCHEMA_SQL)
    t0 = time.time()
    conn.execute(
        "INSERT INTO sessions (row_id, id, source, started_at) "
        "VALUES (1, 's1', 'cli', ?)",
        (t0,),
    )
    conn.execute(
        "INSERT INTO messages (session_id, timestamp, role, content) "
        "VALUES ('s1', ?, 'user', 'needle message')",
        (t0 + 1,),
    )
    conn.commit()
    conn.close()

    d = SessionDB(db_path=db_path)
    try:
        # Populated first open: not stamped, work advertised.
        assert d.get_meta("fts_storage_version") is None
        assert d.fts_optimize_available() is True
        result = d.optimize_fts_storage(vacuum=False)
        assert result["ok"] is True, result
        assert d.get_meta("fts_storage_version") == str(FTS_STORAGE_VERSION)
        assert d.fts_optimize_available() is False
        assert _blocker(d) is None
        # No search behavior change.
        assert len(d.search_messages("needle")) == 1
    finally:
        d.close()


def test_reintroduced_blocker_withdraws_stale_v2_claim(tmp_path):
    """#31 invariant: a v2 claim is never left stale — reintroducing a
    blocker (a fresh session-lane claim on a settled DB) withdraws v2 on the
    next settlement evaluation, and re-completing the lane re-earns it."""
    db_path = tmp_path / "s.db"
    _build_populated_sessions_db(db_path, n=4)
    d1 = SessionDB(db_path=db_path)
    try:
        result = d1.optimize_fts_storage(vacuum=False)
        assert result["ok"] is True, result
        assert d1.get_meta("fts_storage_version") == str(FTS_STORAGE_VERSION)
        # A concurrent writer seeds fresh trigram work on the settled DB.
        d1.set_meta("fts_session_trigram_rebuild_high_water", "999")
        d1.set_meta("fts_session_trigram_rebuild_progress", "0")
        assert _blocker(d1) == ("session_trigram_incomplete", True)
    finally:
        d1.close()

    d2 = SessionDB(db_path=db_path)
    try:
        # Reopen startup settlement withdrew the now-stale v2 claim.
        assert d2.get_meta("fts_storage_version") is None
        assert d2.fts_optimize_available() is True
        # Re-completing the lane re-earns v2.
        result = d2.optimize_fts_storage(vacuum=False)
        assert result["ok"] is True, result
        assert d2.get_meta("fts_storage_version") == str(FTS_STORAGE_VERSION)
        assert d2.fts_optimize_available() is False
    finally:
        d2.close()


# ── Structural refusal branches (review-round pin) ────────────────────────
# The refusal table's schema-identity branches — legacy/internal shapes,
# orphan-empty targets, and #30 fail-closed trigram ownership — were
# implemented but not directly pinned. Each test below constructs the
# minimal durable/schema state and asserts the evaluator's exact blocker.

def _clear_session_metadata_markers(d):
    """Drop the Unicode + trigram session H/P markers so earlier marker-loop
    blockers cannot shadow a structural branch under test."""
    d._conn.execute(
        "DELETE FROM state_meta WHERE key IN "
        "('fts_session_rebuild_high_water', 'fts_session_rebuild_progress', "
        " 'fts_session_trigram_rebuild_high_water', "
        " 'fts_session_trigram_rebuild_progress')"
    )
    d._conn.commit()


def test_session_unicode_legacy_blocks(tmp_path, monkeypatch):
    """Pre-#25 internal-content ``sessions_fts`` fails closed: v2 refused."""
    db_path = tmp_path / "legacy.db"
    _build_legacy_internal_db(
        db_path, "CREATE VIRTUAL TABLE sessions_fts USING fts5(title)"
    )
    # Preserve the legacy shape: the normal ensure path would convert it.
    monkeypatch.setattr(
        SessionDB, "_ensure_sessions_fts_schema", lambda self, cursor: False
    )
    d = SessionDB(db_path=db_path)
    try:
        assert _blocker(d) == ("session_unicode_legacy", True)
        assert d.get_meta("fts_storage_version") is None
    finally:
        d.close()


def test_session_unicode_orphan_empty_blocks(tmp_path):
    """External ``sessions_fts`` empty over populated sessions with no claim
    (crash window) blocks v2."""
    db_path = tmp_path / "s.db"
    _build_populated_sessions_db(db_path, n=4)
    d = SessionDB(db_path=db_path)
    try:
        _clear_session_metadata_markers(d)
        assert _blocker(d) == ("session_unicode_orphan_empty", True)
    finally:
        d.close()


def test_session_cjk_legacy_blocks(tmp_path, monkeypatch):
    """Pre-#26 internal-content ``sessions_fts_cjk`` fails closed even when
    this host cannot load the cjk tokenizer."""
    db_path = tmp_path / "legacy-cjk.db"
    _build_legacy_internal_db(
        db_path, "CREATE VIRTUAL TABLE sessions_fts_cjk USING fts5(title)"
    )
    monkeypatch.setenv("HERMES_FTS5_CJK_SO", str(tmp_path / "absent-cjk.so"))
    d = SessionDB(db_path=db_path)
    try:
        assert not d._fts_cjk_loaded
        assert _blocker(d) == ("session_cjk_legacy", False)
    finally:
        d.close()


def test_session_cjk_orphan_empty_blocks(tmp_path, monkeypatch):
    """External ``sessions_fts_cjk`` empty over populated sessions, no claim
    — blocks even on a cjk-incapable host."""
    db_path = tmp_path / "s.db"
    _build_populated_sessions_db(db_path, n=4)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE VIRTUAL TABLE sessions_fts_cjk USING fts5("
        "title, id, display_name, content='sessions', content_rowid='row_id')"
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("HERMES_FTS5_CJK_SO", str(tmp_path / "absent-cjk.so"))
    d = SessionDB(db_path=db_path)
    try:
        # Backfill the Unicode lane so it cannot orphan-block first, then
        # drop the trigram markers so the marker loop cannot block first.
        while d.fts_session_rebuild_step():
            pass
        _clear_session_metadata_markers(d)
        assert _blocker(d) == ("session_cjk_orphan_empty", False)
    finally:
        d.close()


def test_message_cjk_orphan_empty_blocks(tmp_path, monkeypatch):
    """External ``messages_fts_cjk`` empty over populated non-tool messages
    with no claim blocks v2 even on a cjk-incapable host."""
    db_path = tmp_path / "s.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(SCHEMA_SQL)
    t0 = time.time()
    conn.execute(
        "INSERT INTO sessions (row_id, id, source, started_at) "
        "VALUES (1, 's1', 'cli', ?)",
        (t0,),
    )
    conn.execute(
        "INSERT INTO messages (session_id, timestamp, role, content) "
        "VALUES ('s1', ?, 'user', 'hello')",
        (t0 + 1,),
    )
    conn.executescript("""
        CREATE VIEW messages_fts_cjk_src AS
        SELECT id, role, content, tool_name, tool_calls FROM messages
        WHERE role <> 'tool';
        CREATE VIRTUAL TABLE messages_fts_cjk USING fts5(
            content, tool_name, tool_calls,
            content='messages_fts_cjk_src', content_rowid='id'
        );
    """)
    conn.commit()
    conn.close()
    monkeypatch.setenv("HERMES_FTS5_CJK_SO", str(tmp_path / "absent-cjk.so"))
    d = SessionDB(db_path=db_path)
    try:
        # The 1 session staged Unicode/trigram markers — clear them so the
        # message-CJK structural branch is the first blocker.
        _clear_session_metadata_markers(d)
        assert _blocker(d) == ("message_cjk_orphan_empty", False)
    finally:
        d.close()


def test_session_trigram_namespace_foreign_blocks(tmp_path):
    """A foreign same-name trigger occupant fails closed (v2 refused, no
    mutation)."""
    db_path = tmp_path / "f.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(SCHEMA_SQL)
    conn.executescript(SESSIONS_FTS_TRIGRAM_SQL)
    for name in (
        "sessions_fts_trigram_insert",
        "sessions_fts_trigram_delete",
        "sessions_fts_trigram_update_before",
        "sessions_fts_trigram_update_after",
    ):
        conn.execute(f"DROP TRIGGER IF EXISTS {name}")
    conn.execute(
        "CREATE TRIGGER sessions_fts_trigram_insert AFTER INSERT ON messages "
        "BEGIN SELECT 1; END"
    )
    conn.commit()
    conn.close()
    d = SessionDB(db_path=db_path)
    try:
        assert _blocker(d) == ("session_trigram_namespace_foreign", False)
        assert d.get_meta("fts_storage_version") is None
    finally:
        d.close()


def test_session_trigram_source_collision_blocks(tmp_path):
    """The derived source name occupied by a non-canonical object (a plain
    table) with no root fails closed."""
    db_path = tmp_path / "sc.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(SCHEMA_SQL)
    conn.execute("CREATE TABLE sessions_fts_trigram_src (x)")
    conn.commit()
    conn.close()
    d = SessionDB(db_path=db_path)
    try:
        assert _blocker(d) == ("session_trigram_source_collision", False)
    finally:
        d.close()


def test_session_trigram_trigger_incomplete_blocks(tmp_path):
    """A modern root with an incomplete exact trigger set (no stale breadcrumb)
    fails closed — v2 refused, not silently repaired."""
    db_path = tmp_path / "t.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(SCHEMA_SQL)
    conn.executescript(SESSIONS_FTS_TRIGRAM_SQL)
    for name in (
        "sessions_fts_trigram_update_before",
        "sessions_fts_trigram_update_after",
    ):
        conn.execute(f"DROP TRIGGER IF EXISTS {name}")
    conn.commit()
    conn.close()
    d = SessionDB(db_path=db_path)
    try:
        assert _blocker(d) == ("session_trigram_trigger_incomplete", False)
    finally:
        d.close()


def test_session_trigram_orphan_empty_blocks(tmp_path):
    """Modern session trigram target empty over populated derived source with
    no claim (claim-loss crash window) blocks v2."""
    db_path = tmp_path / "o.db"
    _build_populated_sessions_db(db_path, n=6)
    d = SessionDB(db_path=db_path)
    try:
        # Backfill the Unicode lane so it cannot orphan-block first, then
        # drop the trigram claim over the empty trigram index (claim-loss).
        while d.fts_session_rebuild_step():
            pass
        _clear_session_metadata_markers(d)
        assert _blocker(d) == ("session_trigram_orphan_empty", True)
    finally:
        d.close()

