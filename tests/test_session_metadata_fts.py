"""Tests for the #25 session-metadata FTS architecture.

Covers the named ``sessions.row_id`` migration (preserving exact legacy
hidden rowids including deleted-row holes), the raw ``(title, id,
display_name)`` Unicode external-content ``sessions_fts``, the resumable
crash-safe H/P rebuild, bounded-gap search supplementation, and the shared
throttle/concurrency contract.

Scoped per #25: raw Unicode only. CJK (#26), normalized trigram (#30), and
unified lifecycle/storage settlement (#27) are explicitly out of scope here.
"""

import sqlite3
import time

import pytest

from hermes_state import (
    LEGACY_FTS_SQL,
    SCHEMA_SQL,
    SessionDB,
    _fts_query_positive_terms,
    _fts_unicode61_fold,
)


@pytest.fixture()
def db(tmp_path):
    """Fresh SessionDB (#25 layout: named sessions.row_id + raw Unicode
    external-content session metadata FTS) over a temp database file."""
    db_path = tmp_path / "state.db"
    session_db = SessionDB(db_path=db_path)
    yield session_db
    session_db.close()


def _column_names(conn, table):
    return {
        r[1] if isinstance(r, (tuple, list)) else r["name"]
        for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    }


def _row_id_map(conn):
    """Return ``{id: row_id}`` for every session."""
    return {
        (r[0] if isinstance(r, sqlite3.Row) else r[0]):
        (r[1] if isinstance(r, sqlite3.Row) else r[1])
        for r in conn.execute("SELECT id, row_id FROM sessions").fetchall()
    }


# The pre-#25 sessions shape: ``id TEXT PRIMARY KEY`` with NO named row_id.
# (SCHEMA_SQL now declares the new shape, so fixtures that need a real legacy
# table must demote ``sessions`` to this explicitly.)
_LEGACY_SESSIONS_DDL = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    user_id TEXT,
    session_key TEXT,
    chat_id TEXT,
    chat_type TEXT,
    thread_id TEXT,
    display_name TEXT,
    origin_json TEXT,
    expiry_finalized INTEGER DEFAULT 0,
    model TEXT,
    model_config TEXT,
    system_prompt TEXT,
    system_prompt_hash TEXT,
    parent_session_id TEXT,
    started_at REAL NOT NULL,
    ended_at REAL,
    end_reason TEXT,
    message_count INTEGER DEFAULT 0,
    tool_call_count INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    reasoning_tokens INTEGER DEFAULT 0,
    cwd TEXT,
    git_branch TEXT,
    git_repo_root TEXT,
    billing_provider TEXT,
    billing_base_url TEXT,
    billing_mode TEXT,
    estimated_cost_usd REAL,
    actual_cost_usd REAL,
    cost_status TEXT,
    cost_source TEXT,
    pricing_version TEXT,
    title TEXT,
    last_activity_at REAL,
    last_activity_description TEXT,
    last_activity_provenance TEXT,
    api_call_count INTEGER DEFAULT 0,
    handoff_state TEXT,
    handoff_platform TEXT,
    handoff_error TEXT,
    compression_failure_cooldown_until REAL,
    compression_failure_error TEXT,
    compression_fallback_streak INTEGER NOT NULL DEFAULT 0,
    compression_ineffective_count INTEGER NOT NULL DEFAULT 0,
    profile_name TEXT,
    rewind_count INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    pinned INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (parent_session_id) REFERENCES sessions(id),
    FOREIGN KEY (system_prompt_hash) REFERENCES system_prompts(hash)
)
"""


# The pre-#25 sessions_fts shape (base/dev): INTERNAL-content, title-only,
# unicode61, maintained by three broad triggers keyed by the hidden rowid.
# (#25 replaces this with the external-content Unicode metadata index.)
_LEGACY_SESSIONS_FTS_DDL = """
CREATE VIRTUAL TABLE sessions_fts USING fts5(
    title,
    tokenize='unicode61'
);

CREATE TRIGGER sessions_fts_insert AFTER INSERT ON sessions BEGIN
    INSERT INTO sessions_fts(rowid, title) VALUES (new.rowid, new.title);
END;

CREATE TRIGGER sessions_fts_delete AFTER DELETE ON sessions BEGIN
    DELETE FROM sessions_fts WHERE rowid = old.rowid;
END;

CREATE TRIGGER sessions_fts_update AFTER UPDATE ON sessions BEGIN
    DELETE FROM sessions_fts WHERE rowid = old.rowid;
    INSERT INTO sessions_fts(rowid, title) VALUES (new.rowid, new.title);
END;
"""


def _build_legacy_sessions_db(db_path):
    """Build a pre-#25 DB by hand: sessions WITHOUT ``row_id`` (``id TEXT
    PRIMARY KEY``), with explicit hidden rowids that contain deleted-row
    holes (1=A, 3=B, 7=C — 2,4,5,6 never existed / were deleted), plus
    relationship rows keyed by text ``id``. Everything else matches the
    modern schema (SCHEMA_SQL), so the open path must add ``row_id`` via the
    #25 migration and preserve every surviving numeric rowid.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(SCHEMA_SQL)
    # Demote sessions to the pre-#25 shape (no named row_id).
    conn.execute("DROP TABLE sessions")
    conn.executescript(_LEGACY_SESSIONS_DDL)

    t0 = time.time()
    for rowid, sid, started_at, title in (
        (1, "A", t0, "Alpha Project"),
        (3, "B", t0 + 1, "Beta Project"),
        (7, "C", t0 + 2, "Gamma Project"),
    ):
        conn.execute(
            "INSERT INTO sessions (rowid, id, source, started_at, title) "
            "VALUES (?, ?, 'cli', ?, ?)",
            (rowid, sid, started_at, title),
        )

    # Relationships keyed by text id (messages, model usage, parent/child).
    conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) "
        "VALUES (?, 'user', 'message-for-A', ?)",
        ("A", t0),
    )
    conn.execute(
        "INSERT INTO session_model_usage (session_id, model) VALUES (?, ?)",
        ("B", "model-x"),
    )
    conn.execute(
        "INSERT INTO sessions (id, source, started_at, parent_session_id) "
        "VALUES ('A-child', 'cli', ?, 'A')",
        (t0 + 3,),
    )

    conn.commit()
    conn.close()


def _build_legacy_message_and_session_fts_db(db_path):
    """Build the real pre-#25 CROSS-LAYOUT DB the #25 upgrade path must
    handle: legacy v22 INLINE messages_fts + old INTERNAL title-only
    sessions_fts + broad session triggers + legacy sessions (no named
    row_id) with historical rows.

    Opening the new SessionDB on this must (a) migrate sessions to named
    row_id (DROP TABLE sessions — which SQLite uses to delete the old
    sessions_fts triggers too), (b) STILL convert sessions_fts to the
    external Unicode shape + recreate the gated triggers + stage the H/P
    claim even though the legacy-message branch is taken, and (c) settle
    BOTH message and session migration in a single optimize_fts_storage()
    call with no reopen.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(SCHEMA_SQL)
    # Demote sessions to the pre-#25 shape (no named row_id).
    conn.execute("DROP TABLE sessions")
    conn.executescript(_LEGACY_SESSIONS_DDL)
    # Historical sessions with deleted-row holes in hidden rowids.
    t0 = time.time()
    for rowid, sid, title in (
        (1, "A", "Alpha Project"),
        (3, "B", "Beta Project"),
        (7, "C", "Gamma Project"),
    ):
        conn.execute(
            "INSERT INTO sessions (rowid, id, source, started_at, title) "
            "VALUES (?, ?, 'cli', ?, ?)",
            (rowid, sid, t0 + rowid, title),
        )
    conn.execute(
        "INSERT INTO sessions (id, source, started_at) "
        "VALUES ('A-child', 'cli', ?)",
        (t0 + 8,),
    )
    # Legacy v22 inline messages_fts (+ triggers) over a couple of messages
    # so the message optimize phase has real work to migrate.
    conn.executescript(LEGACY_FTS_SQL)
    conn.executemany(
        "INSERT INTO messages (session_id, role, content, timestamp) "
        "VALUES (?, 'user', ?, ?)",
        [("A", "hello legacy world", t0), ("B", "second legacy note", t0 + 1)],
    )
    # Old INTERNAL title-only sessions_fts (+ broad triggers keyed by the
    # hidden rowid) — the pre-#25 shape _ensure_sessions_fts_schema must
    # detect and convert.
    conn.executescript(_LEGACY_SESSIONS_FTS_DDL)
    conn.commit()
    conn.close()


def _build_legacy_empty_session_fts_db(db_path):
    """Legacy DB with ZERO sessions: old INTERNAL title-only sessions_fts +
    broad triggers over a legacy sessions table (no named row_id, no rows).
    Opening it must convert sessions_fts to external WITHOUT staging an
    H=0/P=0 claim that would leave optimize permanently pending."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(SCHEMA_SQL)
    conn.execute("DROP TABLE sessions")
    conn.executescript(_LEGACY_SESSIONS_DDL)
    conn.executescript(_LEGACY_SESSIONS_FTS_DDL)
    conn.commit()
    conn.close()


# =========================================================================
# Group A — named row_id migration (preserve exact legacy storage identity)
# =========================================================================


class TestNamedRowIdMigration:
    def test_fresh_install_sessions_has_named_row_id(self, db):
        """Fresh installs are born with ``row_id INTEGER PRIMARY KEY`` and
        keep ``id`` as the NOT NULL UNIQUE logical identity."""
        cols = _column_names(db._conn, "sessions")
        assert "row_id" in cols
        pk = db._conn.execute(
            "PRAGMA table_info('sessions')"
        ).fetchall()
        row_id_col = next(
            r for r in pk
            if (r["name"] if isinstance(r, sqlite3.Row) else r[1]) == "row_id"
        )
        assert (row_id_col["pk"] if isinstance(row_id_col, sqlite3.Row) else row_id_col[5]) == 1

        db.create_session("s1", source="cli")
        db.create_session("s2", source="cli")
        ids = [
            r[1] if isinstance(r, sqlite3.Row) else r[1]
            for r in db._conn.execute(
                "SELECT row_id, id FROM sessions ORDER BY row_id"
            ).fetchall()
        ]
        assert ids == ["s1", "s2"]

    def test_legacy_rowid_holes_preserved_exactly(self, tmp_path):
        """Opening a legacy DB preserves each surviving hidden rowid as the
        same numeric ``row_id``, including deleted-row gaps.

        old hidden rowids: 1=A, 3=B, 7=C
        required row_id:   1=A, 3=B, 7=C
        WRONG (densified): 1=A, 2=B, 3=C
        """
        db_path = tmp_path / "legacy.db"
        _build_legacy_sessions_db(db_path)

        session_db = SessionDB(db_path=db_path)
        try:
            assert _row_id_map(session_db._conn) == {
                "A": 1, "B": 3, "C": 7, "A-child": 8,
            }
            # id stays NOT NULL UNIQUE (logical identity).
            ids = {
                r["name"]: r for r in session_db._conn.execute(
                    "PRAGMA table_info('sessions')"
                ).fetchall()
            }
            id_col = ids["id"]
            assert id_col["notnull"] == 1
            unique_idx = session_db._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'index' "
                "AND name = 'sqlite_autoindex_sessions_1'"
            ).fetchone()
            assert unique_idx is not None
        finally:
            session_db.close()

    def test_relationships_survive_migration(self, tmp_path):
        """Message ownership, model usage, and parent/child sessions keyed by
        text ``sessions.id`` continue to resolve unchanged after migration."""
        db_path = tmp_path / "legacy.db"
        _build_legacy_sessions_db(db_path)

        session_db = SessionDB(db_path=db_path)
        try:
            # messages.session_id -> sessions.id still resolves.
            msgs = session_db.get_messages("A")
            assert [m["content"] for m in msgs] == ["message-for-A"]
            # model usage keyed by session id.
            usage = session_db._conn.execute(
                "SELECT model FROM session_model_usage WHERE session_id = 'B'"
            ).fetchone()
            assert (usage[0] if not isinstance(usage, sqlite3.Row) else usage["model"]) == "model-x"
            # parent/child by text id.
            child = session_db._conn.execute(
                "SELECT parent_session_id FROM sessions WHERE id = 'A-child'"
            ).fetchone()
            assert (
                child[0] if not isinstance(child, sqlite3.Row) else child["parent_session_id"]
            ) == "A"
        finally:
            session_db.close()

    def test_vacuum_preserves_named_row_id(self, db):
        """VACUUM must not renumber the named ``row_id`` (SQLite only
        guarantees rowid stability for an explicit INTEGER PRIMARY KEY)."""
        db.create_session("a", source="cli")
        db.create_session("b", source="cli")
        db.create_session("c", source="cli")
        before = _row_id_map(db._conn)
        db.vacuum()
        after = _row_id_map(db._conn)
        assert before == after

    def test_fresh_session_row_id_above_max_legacy(self, tmp_path):
        """A fresh session created after migration gets a ``row_id`` strictly
        greater than every legacy row_id (AUTOINCREMENT monotonicity — the
        property the > H live-trigger ownership relies on)."""
        db_path = tmp_path / "legacy.db"
        _build_legacy_sessions_db(db_path)

        session_db = SessionDB(db_path=db_path)
        try:
            session_db.create_session("D", source="cli")
            row = session_db._conn.execute(
                "SELECT row_id FROM sessions WHERE id = 'D'"
            ).fetchone()
            new_row_id = row[0] if not isinstance(row, sqlite3.Row) else row["row_id"]
            assert new_row_id > 7
        finally:
            session_db.close()

    def test_stale_sessions_new_leftover_is_cleaned_up(self, tmp_path):
        """A donor-style interrupted swap can leave a partial ``sessions_new``
        behind (autocommit DDL between DROP/rename). The #25 migration must
        drop stale ``sessions_new`` inside its transaction and never lose a
        canonical session row."""
        db_path = tmp_path / "legacy.db"
        _build_legacy_sessions_db(db_path)
        # Simulate a non-transactional interrupted attempt.
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "CREATE TABLE sessions_new (id TEXT PRIMARY KEY, source TEXT)"
            )
            conn.commit()

        session_db = SessionDB(db_path=db_path)
        try:
            assert _row_id_map(session_db._conn) == {
                "A": 1, "B": 3, "C": 7, "A-child": 8,
            }
            leftover = session_db._conn.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = 'sessions_new'"
            ).fetchone()
            assert leftover is None
        finally:
            session_db.close()

    def test_legacy_duplicate_titles_do_not_break_open(self, tmp_path):
        """A legacy DB with duplicate titles (allowed before the unique-title
        constraint was enforced) must still open after the row_id migration.

        The migration must NOT rebuild ``idx_sessions_title_unique`` inside
        its swap transaction — a duplicate title would raise
        ``UNIQUE constraint failed``, roll back the whole open, and the
        existing post-migration duplicate repair would never run.
        """
        db_path = tmp_path / "legacy.db"
        _build_legacy_sessions_db(db_path)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE sessions SET title = 'dupe-title' "
                "WHERE id IN ('A', 'B')"
            )
            conn.commit()

        session_db = SessionDB(db_path=db_path)
        try:
            rows = {
                r["id"]: r["title"] for r in session_db._conn.execute(
                    "SELECT id, title FROM sessions"
                ).fetchall()
            }
            # Every row survives; the duplicate is repaired by the existing
            # post-migration block (older rowid loses the alias).
            assert set(rows) == {"A", "B", "C", "A-child"}
            assert rows["A"] is None
            assert rows["B"] == "dupe-title"
            assert rows["C"] == "Gamma Project"
            # The unique title index now exists (built after the repair).
            idx = session_db._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'index' "
                "AND name = 'idx_sessions_title_unique'"
            ).fetchone()
            assert idx is not None
        finally:
            session_db.close()


# =========================================================================
# Raw Unicode external-content sessions_fts — helpers
# =========================================================================


def _fts_sql(conn, table):
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return (row[0] if isinstance(row, sqlite3.Row) else row[0]) if row else ""


def _set_session_rebuild_markers(db, high_water, progress):
    db.set_meta("fts_session_rebuild_high_water", str(high_water))
    db.set_meta("fts_session_rebuild_progress", str(progress))


def _assert_sessions_fts_integrity(db):
    """FTS5 integrity-check in the mode that also verifies external-content /
    content consistency (``rank = 1``).

    The plain ``integrity-check`` only checks the index's internal shadow-table
    structure; it does NOT detect an orphan posting whose canonical row was
    deleted. The ``rank = 1`` form additionally cross-checks the index against
    the ``sessions`` content table, which is what catches a stale posting left
    by a broken delete trigger.
    """
    db._conn.execute(
        "INSERT INTO sessions_fts(sessions_fts, rank) VALUES('integrity-check', 1)"
    )


def _assert_sessions_fts_internal_integrity(db):
    """Ordinary FTS5 internal integrity-check (shadow-table structure only).

    Unlike ``_assert_sessions_fts_integrity`` (rank=1), this does NOT
    cross-check against the content table, so it is safe mid-migration when
    the ``(P, H]`` gap rows are legitimately unindexed.
    """
    db._conn.execute(
        "INSERT INTO sessions_fts(sessions_fts) VALUES('integrity-check')"
    )


def _raw_metadata_match_ids(db, query):
    """Return session ids whose raw Unicode metadata document MATCHes query."""
    with db._read_ctx() as conn:
        rows = conn.execute(
            "SELECT s.id FROM sessions_fts f "
            "JOIN sessions s ON s.row_id = f.rowid "
            "WHERE sessions_fts MATCH ?",
            (query,),
        ).fetchall()
    return [r["id"] for r in rows]


def _raw_fts_rowids(db, query):
    """Return the FTS rowids (sessions.row_id values) that MATCH query, read
    DIRECTLY from the index — no canonical-sessions JOIN.

    This is the delete-test probe: a stale posting left behind by a broken
    delete trigger would still MATCH here even though its canonical row is
    gone, whereas the JOIN-based ``_raw_metadata_match_ids`` would hide it.
    """
    with db._read_ctx() as conn:
        rows = conn.execute(
            "SELECT rowid FROM sessions_fts WHERE sessions_fts MATCH ?",
            (query,),
        ).fetchall()
    return [r["rowid"] for r in rows]


def _gap_db(tmp_path):
    """Populated DB with a staged #25 rebuild: the external sessions_fts was
    freshly created on a populated DB, so the durable H/P claim exists and
    every historical row falls in the (0, H] gap (nothing indexed yet)."""
    db_path = tmp_path / "s.db"
    _build_legacy_sessions_db(db_path)  # sessions A(1), B(3), C(7), A-child(8)
    r = SessionDB(db_path=db_path)      # stages markers H=8, P=0; empty index
    return r


def _three_region_db(tmp_path):
    """SessionDB with a #25 rebuild staged so all three ownership regions are
    represented: rows <= P are backfilled into FTS, rows (P,H] are the
    unindexed gap, and newly created sessions are > H (live-indexed by the
    triggers). Uses the real migration shape (fresh external table on a
    populated DB), not a fresh-DB-then-create flow where every row would be
    live-indexed."""
    r = _gap_db(tmp_path)  # H=8, P=0, empty index
    # Backfill the <= P prefix the way the chunk engine would.
    r._conn.execute(
        "INSERT INTO sessions_fts(rowid, title, id, display_name) "
        "SELECT row_id, title, id, display_name FROM sessions "
        "WHERE row_id <= 3"
    )
    r.set_meta("fts_session_rebuild_progress", "3")
    return r


# =========================================================================
# Group B — raw Unicode external-content shape and search dimensions
# =========================================================================


class TestUnicodeExternalContent:
    def test_sessions_fts_ddl_is_external_content_raw_metadata(self, db):
        """sessions_fts is external-content over raw (title, id, display_name)
        keyed by named ``row_id``."""
        sql = _fts_sql(db._conn, "sessions_fts")
        assert "content='sessions'" in sql
        assert "content_rowid='row_id'" in sql
        assert "tokenize='unicode61'" in sql
        for col in ("title", "id", "display_name"):
            assert col in sql

    def test_raw_unicode_search_covers_title_id_display_name(self, db):
        db.create_session("s1", source="cli")
        db.set_session_title("s1", "Alpha Project")
        db._conn.execute(
            "UPDATE sessions SET display_name = 'Alpha Display' WHERE id = 's1'"
        )
        db._conn.commit()
        assert _raw_metadata_match_ids(db, "alpha") == ["s1"]      # title
        assert _raw_metadata_match_ids(db, "s1") == ["s1"]         # logical id
        assert _raw_metadata_match_ids(db, "display") == ["s1"]    # display_name

    def test_unrelated_metadata_update_does_not_rewrite_fts(self, db):
        """Only narrow UPDATE OF title/id/display_name maintain the index; an
        unrelated column write must not fire the FTS update trigger."""
        db.create_session("s1", source="cli")
        db.set_session_title("s1", "Alpha")
        db._conn.execute(
            "UPDATE sessions SET message_count = 5 WHERE id = 's1'"
        )
        db._conn.commit()
        assert _raw_metadata_match_ids(db, "alpha") == ["s1"]
        trig = db._conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'trigger' AND name = 'sessions_fts_update'"
        ).fetchone()
        sql = trig[0] if not isinstance(trig, sqlite3.Row) else trig["sql"]
        compact = " ".join(sql.split())
        assert "AFTER UPDATE OF title, id, display_name" in compact

    def test_list_sessions_rich_search_covers_display_name(self, db):
        """The production session listing's search_query lane matches the raw
        display_name dimension (issue #25), not only title / logical id."""
        db.create_session("s1", source="cli")
        db._conn.execute(
            "UPDATE sessions SET display_name = 'Team Zebra Display' "
            "WHERE id = 's1'"
        )
        db._conn.commit()
        rows = db.list_sessions_rich(
            search_query="zebra", order_by_last_active=True
        )
        assert [r["id"] for r in rows] == ["s1"]


# =========================================================================
# Group C — crash/restart H/P bookkeeping
# =========================================================================


class TestRebuildMarkers:
    def test_populated_db_stages_rebuild_markers(self, tmp_path):
        """Opening a populated DB whose external sessions_fts is freshly
        created must stage the durable H/P rebuild claim (never serve an
        empty index as complete). Sessions added after a table already exists
        are live-indexed by the triggers and need no claim."""
        db_path = tmp_path / "s.db"
        # Build a populated DB WITHOUT opening a SessionDB first, so no
        # external sessions_fts exists yet — the open creates it and must
        # stage the claim over the historical rows.
        _build_legacy_sessions_db(db_path)
        r = SessionDB(db_path=db_path)
        try:
            assert r.get_meta("fts_session_rebuild_high_water") is not None
            assert r.get_meta("fts_session_rebuild_progress") == "0"
        finally:
            r.close()

    def test_empty_db_has_no_markers(self, db):
        """An empty DB's index is complete by construction — no claim."""
        assert db.get_meta("fts_session_rebuild_high_water") is None

    def test_markers_survive_reopen_no_reseed(self, tmp_path):
        """A completed/advanced progress must never be reseeded to zero on
        reopen."""
        db_path = tmp_path / "s.db"
        w = SessionDB(db_path=db_path)
        w.create_session("A", source="cli")
        w.create_session("B", source="cli")
        w.close()
        r = SessionDB(db_path=db_path)
        _set_session_rebuild_markers(r, 2, 1)
        r.close()
        r2 = SessionDB(db_path=db_path)
        try:
            assert r2.get_meta("fts_session_rebuild_high_water") == "2"
            assert r2.get_meta("fts_session_rebuild_progress") == "1"
        finally:
            r2.close()

    def test_crash_after_claim_before_schema_resumes(self, tmp_path):
        """Durable markers with the external table missing (death between the
        claim commit and the schema ensure) must re-ensure the external schema
        on reopen — never stamp the migration complete."""
        db_path = tmp_path / "s.db"
        w = SessionDB(db_path=db_path)
        w.create_session("A", source="cli")
        w.create_session("B", source="cli")
        w.close()
        with sqlite3.connect(db_path) as conn:
            for t in (
                "sessions_fts", "sessions_fts_data", "sessions_fts_idx",
                "sessions_fts_content", "sessions_fts_docsize",
                "sessions_fts_config",
            ):
                conn.execute(f"DROP TABLE IF EXISTS {t}")
            conn.commit()
        r = SessionDB(db_path=db_path)
        try:
            assert "content='sessions'" in _fts_sql(r._conn, "sessions_fts")
            # Backfill still pending (markers present), not stamped done.
            assert r.get_meta("fts_session_rebuild_high_water") is not None
        finally:
            r.close()

    def test_partial_index_orphan_hp_resets_and_replays(self, tmp_path):
        """#32: an orphan H-without-P over a PARTIALLY populated index must be
        reset known-empty and replayed without duplicates — never serve the
        partial index as complete. optimize must settle (doc count == sessions,
        no duplicate rowid, rank=1 integrity, markers cleared) and reopen stays
        settled."""
        db_path = tmp_path / "s.db"
        _build_populated_sessions_db(db_path, n=50)  # 50 sessions, no sessions_fts
        r = SessionDB(db_path=db_path)  # stages H=50, P=0 over an empty index
        try:
            # Backfill a prefix by hand, then remove P to simulate an orphan
            # high-water-without-progress (partial index of unknown extent).
            r._conn.execute(
                "INSERT INTO sessions_fts(rowid, title, id, display_name) "
                "SELECT row_id, title, id, display_name FROM sessions "
                "WHERE row_id <= 20"
            )
            r._conn.execute(
                "DELETE FROM state_meta WHERE key = 'fts_session_rebuild_progress'"
            )
            r._conn.commit()
            # optimize repairs (reset known-empty + P=0) then backfills fully.
            result = r.optimize_fts_storage(vacuum=False)
            assert result["ok"] is True, result
            assert r.get_meta("fts_session_rebuild_high_water") is None
            assert r.get_meta("fts_session_rebuild_progress") is None
            n_sessions = r._conn.execute(
                "SELECT COUNT(*) FROM sessions"
            ).fetchone()[0]
            n_docs = r._conn.execute(
                "SELECT COUNT(*) FROM sessions_fts_docsize"
            ).fetchone()[0]
            assert n_docs == n_sessions
            dup = r._conn.execute(
                "SELECT COUNT(*) FROM (SELECT rowid FROM sessions_fts_docsize "
                "GROUP BY rowid HAVING COUNT(*) > 1)"
            ).fetchone()[0]
            assert dup == 0
            _assert_sessions_fts_integrity(r)
        finally:
            r.close()

        # Reopen remains settled.
        r2 = SessionDB(db_path=db_path)
        try:
            assert r2.get_meta("fts_session_rebuild_high_water") is None
            assert r2.fts_optimize_available() is False
        finally:
            r2.close()


# =========================================================================
# Group D — trigger ownership regions (<=P, (P,H], >H) + deletes + integrity
# =========================================================================


class TestTriggerOwnershipRegions:
    def test_delete_indexed_prefix_row_removes_doc(self, tmp_path):
        """Deleting a ``<=P`` row removes its already-indexed document."""
        r = _three_region_db(tmp_path)
        try:
            # A (row 1) is indexed with title "Alpha Project".
            assert _raw_fts_rowids(r, "alpha") == [1]
            r.delete_session("A")
            # Direct index probe: no stale posting survives the delete, and
            # the canonical row is gone too. (The rank=1 consistency check is
            # intentionally NOT used here: the (P,H] gap rows are legitimately
            # unindexed mid-rebuild, so full-content consistency is expected
            # only after the backfill completes.)
            assert _raw_fts_rowids(r, "alpha") == []
            assert _raw_metadata_match_ids(r, "alpha") == []
            # Ordinary internal integrity-check (safe mid-migration).
            _assert_sessions_fts_internal_integrity(r)
        finally:
            r.close()

    def test_delete_gap_row_does_not_issue_fts_delete(self, tmp_path):
        """Deleting a ``(P,H]`` row (never indexed) must not issue an
        external-content FTS delete; the index stays free of stale postings
        and the canonical row is simply absent."""
        r = _three_region_db(tmp_path)
        try:
            # C (row 7) is in (P,H] — never indexed, so no posting exists.
            assert _raw_fts_rowids(r, "gamma") == []
            r.delete_session("C")
            # Still no posting (a broken trigger would have tried to 'delete'
            # an unindexed doc and either corrupted the index or left a
            # posting); the canonical row is simply absent.
            assert _raw_fts_rowids(r, "gamma") == []
            assert r.get_session("C") is None
            # Ordinary internal integrity-check (safe mid-migration).
            _assert_sessions_fts_internal_integrity(r)
        finally:
            r.close()

    def test_delete_live_row_removes_doc(self, tmp_path):
        """Deleting a ``>H`` live row (indexed by the trigger at insert time)
        removes its live document."""
        r = _three_region_db(tmp_path)
        try:
            r.create_session("E", source="cli")  # row_id > H
            r.set_session_title("E", "Live Echo")
            assert _raw_fts_rowids(r, "echo") != []
            r.delete_session("E")
            # Direct index probe: the live document is gone, not hidden by a
            # canonical-row JOIN.
            assert _raw_fts_rowids(r, "echo") == []
            # Ordinary internal integrity-check (safe mid-migration).
            _assert_sessions_fts_internal_integrity(r)
        finally:
            r.close()

    def test_deleted_row_leaves_no_orphan_in_completed_index(self, tmp_path):
        """After the backfill completes (index fully consistent), deleting an
        indexed row must leave NO orphan posting — proven by the rank=1
        external-content consistency check, which a plain integrity-check
        would miss."""
        r = _gap_db(tmp_path)  # H=8, P=0, empty index
        try:
            while r.fts_session_rebuild_step():
                pass
            assert r.get_meta("fts_session_rebuild_high_water") is None
            # Full index, one live delete.
            r.delete_session("A")
            assert _raw_fts_rowids(r, "alpha") == []
            # rank=1 cross-checks the index against the content table.
            _assert_sessions_fts_integrity(r)
        finally:
            r.close()

    def test_gap_region_update_does_not_corrupt_index(self, tmp_path):
        """Updating a ``(P,H]`` row must not rewrite a document that was never
        indexed; integrity stays healthy."""
        r = _three_region_db(tmp_path)
        try:
            r.set_session_title("C", "New Title C")  # row 7 in (P,H]
            # The gap row is still not indexed (gate is off), and the index
            # carries no stale entry for the old title either.
            assert _raw_fts_rowids(r, "gamma") == []
            assert _raw_fts_rowids(r, "newtitle") == []
            # Ordinary internal integrity-check (safe mid-migration).
            _assert_sessions_fts_internal_integrity(r)
        finally:
            r.close()

    def test_live_session_searchable_while_backfill_pending(self, tmp_path):
        """A session created after high-water capture is searchable
        immediately while the historical backfill remains incomplete."""
        r = _three_region_db(tmp_path)
        try:
            r.create_session("E", source="cli")  # row_id > H
            r.set_session_title("E", "Fresh Live Title")
            assert _raw_metadata_match_ids(r, "fresh") == ["E"]
        finally:
            r.close()

    def test_window_insert_between_teardown_and_trigger_install_is_caught_up(
        self, tmp_path, monkeypatch
    ):
        """A session committed by ANOTHER connection in the window between the
        H capture / old-table teardown and the install of the new gated
        triggers (the stage transaction commits before the external schema is
        ensured) is neither trigger-indexed nor in the (P,H] gap supplement —
        the transition catch-up must index it, or it stays invisible until a
        full rebuild."""
        db_path = tmp_path / "legacy.db"
        _build_legacy_message_and_session_fts_db(db_path)  # A(1) B(3) C(7) A-child(8)
        real_transition = SessionDB._fts_session_schema_transition

        def _transition_with_window_insert(self, cursor):
            # Simulate the other-process write in the trigger-free window:
            # the stage transaction already committed H; insert a row above
            # it via a second raw connection (no sessions_fts trigger exists
            # yet, so the row is not indexed).
            with sqlite3.connect(str(db_path)) as conn:
                hw = conn.execute(
                    "SELECT CAST(value AS INTEGER) FROM state_meta "
                    "WHERE key = 'fts_session_rebuild_high_water'"
                ).fetchone()
                hw = int(hw[0]) if hw else 0
                conn.execute(
                    "INSERT INTO sessions (id, source, started_at) "
                    "VALUES ('W', 'cli', ?)",
                    (time.time(),),
                )
                conn.commit()
            return real_transition(self, cursor)

        monkeypatch.setattr(
            SessionDB, "_fts_session_schema_transition", _transition_with_window_insert
        )
        r = SessionDB(db_path=db_path)
        try:
            # The window row (row_id > H) must be searchable immediately —
            # the transition catch-up indexed it.
            assert _raw_metadata_match_ids(r, "W") == ["W"]
            # optimize settles with every session covered (no invisible row).
            result = r.optimize_fts_storage(vacuum=False)
            assert result["ok"] is True, result
            n_sessions = r._conn.execute(
                "SELECT COUNT(*) FROM sessions"
            ).fetchone()[0]
            n_docs = r._conn.execute(
                "SELECT COUNT(*) FROM sessions_fts_docsize"
            ).fetchone()[0]
            assert n_docs == n_sessions
        finally:
            r.close()

    def test_fresh_create_window_insert_is_caught_up(self, tmp_path, monkeypatch):
        """Same transition window on the fresh-create-over-populated path (no
        pre-existing internal sessions_fts): a >H row committed by another
        connection between the H/P seed and the trigger install must be caught
        up, not left invisible."""
        db_path = tmp_path / "s.db"
        _build_populated_sessions_db(db_path, n=50)  # no sessions_fts yet
        real_transition = SessionDB._fts_session_schema_transition

        def _transition_with_window_insert(self, cursor):
            with sqlite3.connect(str(db_path)) as conn:
                hw = conn.execute(
                    "SELECT CAST(value AS INTEGER) FROM state_meta "
                    "WHERE key = 'fts_session_rebuild_high_water'"
                ).fetchone()
                hw = int(hw[0]) if hw else 0
                conn.execute(
                    "INSERT INTO sessions (id, source, started_at) "
                    "VALUES ('W', 'cli', ?)",
                    (time.time(),),
                )
                conn.commit()
            return real_transition(self, cursor)

        monkeypatch.setattr(
            SessionDB, "_fts_session_schema_transition", _transition_with_window_insert
        )
        r = SessionDB(db_path=db_path)
        try:
            assert _raw_metadata_match_ids(r, "W") == ["W"]
            result = r.optimize_fts_storage(vacuum=False)
            assert result["ok"] is True, result
            n_sessions = r._conn.execute(
                "SELECT COUNT(*) FROM sessions"
            ).fetchone()[0]
            n_docs = r._conn.execute(
                "SELECT COUNT(*) FROM sessions_fts_docsize"
            ).fetchone()[0]
            assert n_docs == n_sessions
        finally:
            r.close()

    def test_empty_legacy_first_row_window_is_caught_up(
        self, tmp_path, monkeypatch
    ):
        """Case A: on a legacy empty DB the H/P markers are deliberately
        absent (complete-by-construction), so an early-return on 'no markers'
        would drop a FIRST row inserted by another connection in the
        trigger-free window. The crash-atomic transition's COALESCE(H,-1)
        predicate makes every row trigger-owned, so the first window row is
        caught up and immediately searchable."""
        db_path = tmp_path / "legacy-empty.db"
        _build_legacy_empty_session_fts_db(db_path)  # old internal FTS, 0 rows
        real_transition = SessionDB._fts_session_schema_transition

        def _transition_with_first_row(self, cursor):
            # Insert the FIRST session via a second connection in the
            # trigger-free window (the old internal table + triggers were
            # dropped by the stage commit; no external trigger exists yet).
            with sqlite3.connect(str(db_path)) as conn:
                conn.execute(
                    "INSERT INTO sessions (id, source, started_at) "
                    "VALUES ('W', 'cli', ?)",
                    (time.time(),),
                )
                conn.commit()
            return real_transition(self, cursor)

        monkeypatch.setattr(
            SessionDB, "_fts_session_schema_transition", _transition_with_first_row
        )
        r = SessionDB(db_path=db_path)
        try:
            # The first window row is immediately searchable (COALESCE(H,-1)
            # covers the no-marker case).
            assert _raw_metadata_match_ids(r, "W") == ["W"]
            result = r.optimize_fts_storage(vacuum=False)
            assert result["ok"] is True, result
            n_sessions = r._conn.execute(
                "SELECT COUNT(*) FROM sessions"
            ).fetchone()[0]
            n_docs = r._conn.execute(
                "SELECT COUNT(*) FROM sessions_fts_docsize"
            ).fetchone()[0]
            assert n_docs == n_sessions
        finally:
            r.close()

    def test_populated_crash_before_transition_reopen_catches_up(
        self, tmp_path
    ):
        """Case B: schema/triggers committed but the catch-up never became
        durable (a crash between the old separate transactions). With the
        crash-atomic transition this state means schema + catch-up rolled back
        together, so a reopen re-runs the transition and a >H window row is
        caught up — never left invisible."""
        db_path = tmp_path / "s.db"
        _build_populated_sessions_db(db_path, n=50)  # 50 sessions, no sessions_fts
        w = SessionDB(db_path=db_path)  # completes the atomic transition
        w.close()
        # Simulate the crash window: external schema + triggers rolled back,
        # H/P markers durable, and a >H row committed in the trigger-free
        # window (no trigger existed to index it).
        with sqlite3.connect(db_path) as conn:
            for t in (
                "sessions_fts", "sessions_fts_data", "sessions_fts_idx",
                "sessions_fts_content", "sessions_fts_docsize",
                "sessions_fts_config",
            ):
                conn.execute(f"DROP TABLE IF EXISTS {t}")
            for trig in (
                "sessions_fts_insert", "sessions_fts_delete",
                "sessions_fts_update",
            ):
                conn.execute(f"DROP TRIGGER IF EXISTS {trig}")
            conn.execute(
                "INSERT INTO sessions (id, source, started_at) "
                "VALUES ('W', 'cli', ?)",
                (time.time(),),
            )
            conn.commit()
        r = SessionDB(db_path=db_path)
        try:
            # The >H row is immediately searchable after the reopened atomic
            # transition catches it up.
            assert _raw_metadata_match_ids(r, "W") == ["W"]
            result = r.optimize_fts_storage(vacuum=False)
            assert result["ok"] is True, result
            n_sessions = r._conn.execute(
                "SELECT COUNT(*) FROM sessions"
            ).fetchone()[0]
            n_docs = r._conn.execute(
                "SELECT COUNT(*) FROM sessions_fts_docsize"
            ).fetchone()[0]
            assert n_docs == n_sessions
        finally:
            r.close()


# =========================================================================
# Group E — bounded-gap search supplementation + boundary finish
# =========================================================================


def _metadata_search_ids(db, query):
    """Search session metadata through the raw Unicode lane + bounded (P,H]
    supplement (issue #25). Returns logical session ids, deduplicated."""
    _, candidates = db._fts_metadata_candidates(query)
    return [c["id"] for c in candidates]


def _build_populated_sessions_db(db_path, n=1200):
    """Build a DB with ``n`` sessions (explicit row_ids 1..n) and NO
    sessions_fts, so the open stages a full H/P claim over an empty external
    index (the real #25 migration shape)."""
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


class TestBoundedGapSearch:
    def test_gap_session_searchable_via_bounded_supplement(self, tmp_path):
        """A session whose row_id is in the unbackfilled (P,H] gap is still
        found by raw metadata search through bounded supplementation."""
        r = _three_region_db(tmp_path)
        try:
            # C (row 7) is in (P,H] = (3,8], so its title is NOT in FTS.
            assert _raw_metadata_match_ids(r, "gamma") == []
            # The bounded supplement must surface it.
            assert _metadata_search_ids(r, "gamma") == ["C"]
        finally:
            r.close()

    def test_gap_supplement_dedupes_with_fts_candidates(self, tmp_path):
        """A session reachable through both the FTS lane and the supplemental
        (P,H] route (a boundary-overlap row) is returned exactly once."""
        r = _three_region_db(tmp_path)
        try:
            # B (row 3) is <= P so it is in FTS. Narrow P below B so the same
            # row also falls in the gap — the boundary overlap case.
            _set_session_rebuild_markers(r, 8, 1)  # gap = (1, 8]
            hits = _metadata_search_ids(r, "beta")
            assert hits == ["B"]
            # A (row 1) stays <= P: it must NOT be re-scanned by the gap.
            assert _metadata_search_ids(r, "alpha") == ["A"]
        finally:
            r.close()

    def test_supplement_is_bounded_to_gap(self, tmp_path):
        """The supplemental query must be bounded by ``row_id > P AND row_id
        <= H`` — never an unbounded all-sessions migration scan."""
        r = _three_region_db(tmp_path)
        try:
            r.create_session("E", source="cli")  # row 9 > H, live-indexed
            r.set_session_title("E", "Live Term E")
            # > H rows are in FTS (live trigger) — found without the gap.
            assert _metadata_search_ids(r, "live") == ["E"]
            gap = r._session_fts_rebuild_gap()
            assert gap == (3, 8)
        finally:
            r.close()

    def test_finish_repairs_missing_boundary_doc(self, tmp_path):
        """Finish performs a narrow boundary sweep: a document missing near H
        is re-inserted before the rebuild markers are cleared."""
        r = _gap_db(tmp_path)  # H=8, P=0, empty index
        try:
            # Backfill everything by hand, then remove C's document to
            # simulate a write that slipped at the boundary.
            r._conn.execute(
                "INSERT INTO sessions_fts(rowid, title, id, display_name) "
                "SELECT row_id, title, id, display_name FROM sessions "
                "WHERE row_id <= 8"
            )
            r.set_meta("fts_session_rebuild_progress", "8")
            r._conn.execute(
                "INSERT INTO sessions_fts(sessions_fts, rowid, title, id, display_name) "
                "SELECT 'delete', s.row_id, s.title, s.id, s.display_name "
                "FROM sessions s WHERE s.id = 'C'"
            )
            r._conn.commit()
            assert "C" not in _raw_metadata_match_ids(r, "gamma")
            # A single step: no chunk to claim (P >= H), but the status check
            # runs the finish boundary sweep before clearing the markers.
            assert r.fts_session_rebuild_step() is False
            assert r.get_meta("fts_session_rebuild_high_water") is None
            assert _raw_metadata_match_ids(r, "gamma") == ["C"]
        finally:
            r.close()

    def test_deleted_gap_row_not_resurrected_by_finish(self, tmp_path):
        """A canonical row deleted inside (P,H] is simply absent: the boundary
        sweep must not resurrect it."""
        r = _gap_db(tmp_path)  # H=8, P=0, empty index
        try:
            r.delete_session("C")  # row 7 in gap
            while r.fts_session_rebuild_step():
                pass
            assert r.get_meta("fts_session_rebuild_high_water") is None
            assert _metadata_search_ids(r, "gamma") == []
            _assert_sessions_fts_integrity(r)
        finally:
            r.close()

    def test_gap_supplement_unicode_folding_matches_fts(self, tmp_path):
        """The bounded-gap supplement folds with a conservative Unicode
        approximation of the unicode61 rules (case fold + diacritic removal),
        so both 'ecole' and 'école' find a gap row titled 'École des
        Beaux-Arts' — a session must not vanish while it sits in (P,H] just
        because it is not backfilled yet (SQLite's ASCII-only LOWER() would
        hide it). The approximation is looser than exact tokenizer parity by
        design (see test_gap_fold_is_conservative_not_exact_parity)."""
        r = _gap_db(tmp_path)  # H=8, P=0: every row is in the gap
        try:
            r.set_session_title("C", "École des Beaux-Arts")  # row 7 in gap
            # FTS lane alone finds nothing: C is in the gap, not indexed.
            assert _raw_metadata_match_ids(r, "ecole") == []
            # The Unicode-aware supplement finds it for both spellings. The
            # non-ASCII spelling ('école') also over-matches other gap rows
            # (per-codepoint emission is a superset by design) — what matters
            # is C is never hidden.
            assert _metadata_search_ids(r, "ecole") == ["C"]
            assert "C" in _metadata_search_ids(r, "école")
        finally:
            r.close()

    def test_gap_fold_is_conservative_not_exact_parity(self, tmp_path):
        """The gap fold is a conservative approximation of unicode61, NOT a
        parity mirror: it strips ALL combining marks across scripts and
        casefolds, so a single-codepoint multi-diacritic character like 'ộ'
        (U+1ED9) folds to 'o' — broader than the real tokenizer, which
        preserves such characters under remove_diacritics=1. This temporary
        false positive is accepted: the #25 gap lane exists to avoid MISSING
        a migration result, not to exactly mirror the index."""
        r = _gap_db(tmp_path)  # H=8, P=0: every row is in the gap
        try:
            # The helper itself is looser than the tokenizer would be.
            assert _fts_unicode61_fold("ộ") == "o"
            r.set_session_title("B", "Một ộ")  # row 3 in the gap
            # The gap supplement surfaces B for 'o' — a candidate the
            # authoritative tokenizer may not match once the row is
            # backfilled. Over-matching is the accepted conservative side.
            assert "B" in _metadata_search_ids(r, "o")
        finally:
            r.close()

    def test_gap_supplement_multi_token_query_no_hide(self, tmp_path):
        """A multi-token FTS query (implicit AND) must not hide a gap row:
        'Alpha middle Project' MATCHes 'Alpha Project' once indexed (both
        tokens present), so the gap supplement must find it too — the old
        whole-string substring test ('alpha project' in 'alpha middle
        project' → False) would hide it until backfill."""
        r = _three_region_db(tmp_path)  # A(1)/B(3) indexed, C(7) in gap
        try:
            # A's default title is already 'Alpha Project' (indexed, <= P).
            r.set_session_title("C", "Alpha middle Project")  # gap row
            # Indexed lane: A matches the two-token implicit-AND query.
            assert _raw_metadata_match_ids(r, "Alpha Project") == ["A"]
            # Gap supplement must ALSO surface C (not hide it mid-migration).
            hits = _metadata_search_ids(r, "Alpha Project")
            assert "A" in hits and "C" in hits
            # After the backfill completes, C is findable via the index.
            while r.fts_session_rebuild_step():
                pass
            assert r.get_meta("fts_session_rebuild_high_water") is None
            assert "C" in _raw_metadata_match_ids(r, "Alpha Project")
        finally:
            r.close()

    def test_gap_supplement_or_query_no_hide(self, tmp_path):
        """A boolean OR query must not hide a gap row: the indexed lane
        matches title='Alpha' for MATCH 'alpha OR beta', so the gap
        supplement must surface it too. The supplement extracts positive
        terms from the query ('alpha', 'beta') and matches ANY of them —
        the old whole-string substring test ('alpha or beta' in 'alpha')
        would hide the row until backfill."""
        r = _gap_db(tmp_path)  # H=8, P=0: every row is in the gap
        try:
            r.set_session_title("C", "Alpha")  # row 7 in gap
            # Nothing is indexed yet (P=0): the raw index has no hits.
            assert _raw_metadata_match_ids(r, "alpha OR beta") == []
            # The gap supplement surfaces C via the 'alpha' term.
            assert "C" in _metadata_search_ids(r, "alpha OR beta")
        finally:
            r.close()

    def test_positive_term_extractor_splits_punctuation(self):
        """The term extractor splits on ASCII non-alphanumerics only — Python
        '\\w' wrongly keeps '_', and Python's Unicode categories can't mirror
        unicode61 (Unicode 6.1). Non-ASCII characters are always kept inside
        terms (never excluded), so the gap predicate stays a superset of the
        FTS predicate across sanitizer-quoted [._-] punctuation, quoted
        boolean literals, PUA, and unicode61-vs-Python category drift.
        Boolean operator WORDS are kept as terms: a quoted '"AND"' is a
        literal FTS phrase, not an operator."""
        assert _fts_query_positive_terms("foo_bar") == ["foo", "bar"]
        assert _fts_query_positive_terms("foo-bar") == ["foo", "bar"]
        assert _fts_query_positive_terms("foo.bar") == ["foo", "bar"]
        assert _fts_query_positive_terms("Alpha Project") == ["alpha", "project"]
        assert _fts_query_positive_terms("alpha OR beta") == ["alpha", "or", "beta"]
        assert _fts_query_positive_terms('"AND"') == ["and"]
        assert _fts_query_positive_terms('"foo bar"') == ["foo", "bar"]
        # ASCII-only boundary is unchanged; non-ASCII is kept + per-codepoint.
        assert _fts_query_positive_terms("École") == ["ecole", "cole", "e"]
        # PUA (Co) and a unicode61-token-char-that-Python-calls-So (U+1018C)
        # are kept as terms (the merged run + the codepoint).
        assert _fts_query_positive_terms("\ue000") == ["\ue000"]
        assert _fts_query_positive_terms("a\ue000b") == [
            "a\ue000b", "a", "b", "\ue000",
        ]
        assert _fts_query_positive_terms("\U0001018C") == ["\U0001018C"]

    @pytest.mark.parametrize("query", ["foo_bar", "foo-bar", "foo.bar"])
    def test_gap_supplement_punctuation_query_no_hide(self, tmp_path, query):
        """Punctuation the sanitizer quotes into an FTS phrase ('.', '-', '_')
        must not hide a gap row: the index tokenizes a 'foo bar ...' document
        and a 'foo_bar' query to the same two tokens, so the gap supplement
        must too — the term extractor treats all three as separators, never a
        literal part of the term."""
        r = _three_region_db(tmp_path)  # A(1)/B(3) indexed, C(7) in gap
        try:
            # Distinct titles (the unique-title constraint forbids sharing the
            # exact string) that both carry the adjacent 'foo bar' phrase.
            r.set_session_title("A", "foo bar alpha")  # <= P, indexed
            r.set_session_title("C", "foo bar beta")   # (P,H], gap
            # Indexed control via the same sanitized quoted phrase the FTS
            # lane uses (a bare '.foo.'/'-' query is not valid FTS5 syntax).
            sanitized = r._sanitize_fts5_query(query)
            assert _raw_metadata_match_ids(r, sanitized) == ["A"]
            # Gap supplement must ALSO surface C.
            hits = _metadata_search_ids(r, query)
            assert "A" in hits and "C" in hits
            # After backfill completes, C is index-findable too.
            while r.fts_session_rebuild_step():
                pass
            assert r.get_meta("fts_session_rebuild_high_water") is None
            assert "C" in _raw_metadata_match_ids(r, sanitized)
        finally:
            r.close()

    def test_gap_supplement_quoted_boolean_literal_no_hide(self, tmp_path):
        """A quoted boolean word ('"AND"') is a literal FTS phrase (the
        sanitizer protects balanced quotes), NOT an operator — so the term
        extractor must keep it as a term. An indexed 'AND' row is hit, a gap
        row carrying the 'and' token must be surfaced too, and after backfill
        it stays index-findable."""
        r = _three_region_db(tmp_path)  # A(1)/B(3) indexed, C(7) in gap
        try:
            r.set_session_title("A", "AND")         # <= P, indexed
            r.set_session_title("C", "AND beyond")  # (P,H], gap
            # Indexed control: '"AND"' is a valid quoted-phrase query.
            assert _raw_metadata_match_ids(r, '"AND"') == ["A"]
            # Gap supplement must ALSO surface C (keeps 'and' as a term).
            hits = _metadata_search_ids(r, '"AND"')
            assert "A" in hits and "C" in hits
            # After backfill completes, C is index-findable too.
            while r.fts_session_rebuild_step():
                pass
            assert r.get_meta("fts_session_rebuild_high_water") is None
            assert "C" in _raw_metadata_match_ids(r, '"AND"')
        finally:
            r.close()

    def test_gap_supplement_private_use_codepoint_no_hide(self, tmp_path):
        """Private-Use codepoints (Unicode category Co) are unicode61 token
        characters — U+E000 is indexable and MATCH-able — so the term
        extractor must keep them too (isalnum() alone drops Co). A gap row
        carrying the PUA token must be surfaced, or it hides until backfill."""
        r = _three_region_db(tmp_path)  # A(1)/B(3) indexed, C(7) in gap
        try:
            r.set_session_title("A", "\ue000 alpha")  # <= P, indexed
            r.set_session_title("C", "\ue000 beta")   # (P,H], gap
            # Indexed control: U+E000 is a unicode61 token.
            assert _raw_metadata_match_ids(r, "\ue000") == ["A"]
            # Gap supplement must ALSO surface C.
            hits = _metadata_search_ids(r, "\ue000")
            assert "A" in hits and "C" in hits
            # After backfill completes, C is index-findable too.
            while r.fts_session_rebuild_step():
                pass
            assert r.get_meta("fts_session_rebuild_high_water") is None
            assert "C" in _raw_metadata_match_ids(r, "\ue000")
        finally:
            r.close()

    def test_resolve_title_prefers_newer_gap_continuation(self, tmp_path):
        """resolve_session_by_title must resume the LATEST continuation even
        when the newer one is still in the (P,H] gap and the older one is
        already indexed — the FTS+gap merge is sorted globally by
        started_at DESC, not lane-then-gap."""
        r = _three_region_db(tmp_path)  # P=3, H=8: A(1)/B(3) indexed, C(7) gap
        try:
            t0 = 1_000_000.0
            r._conn.execute(
                "UPDATE sessions SET title = 'Project #2', started_at = ? "
                "WHERE id = 'A'",
                (t0,),
            )
            r._conn.execute(
                "UPDATE sessions SET title = 'Project #3', started_at = ? "
                "WHERE id = 'C'",
                (t0 + 100,),
            )
            r._conn.commit()
            # A (row 1, <= P) is indexed -> its FTS doc is now "Project #2".
            # C (row 7, gap) is not indexed -> only the supplement sees it.
            assert r.resolve_session_by_title("Project") == "C"
        finally:
            r.close()

    def test_resolve_title_falls_back_to_like_when_fts_lane_fails(
        self, tmp_path
    ):
        """If the sessions_fts MATCH lane itself fails (table unavailable /
        corrupt), numbered-title resolution must signal the LIKE fallback
        instead of returning a partial/no-match result."""
        db_path = tmp_path / "s.db"
        w = SessionDB(db_path=db_path)
        w.create_session("base", source="cli")
        w.create_session("base2", source="cli")
        w.set_session_title("base2", "Base Title #2")
        w.close()
        r = SessionDB(db_path=db_path)
        try:
            assert r.resolve_session_by_title("Base Title") == "base2"
            # Break only the session FTS lane (message FTS stays alive).
            r._conn.execute("DROP TABLE sessions_fts")
            r._conn.commit()
            assert r.resolve_session_by_title("Base Title") == "base2"
        finally:
            r.close()

    def test_resolve_title_non_numeric_rejected_via_like_fallback(
        self, tmp_path
    ):
        """Even when the FTS lane is down, the LIKE fallback must reject
        non-integer '#N' lookalikes — the ' #%' LIKE prefix over-matches
        'foo #bar' and must be post-filtered (#15)."""
        db_path = tmp_path / "s.db"
        w = SessionDB(db_path=db_path)
        w.create_session("foo1", source="cli")
        w.set_session_title("foo1", "foo #bar")
        w.close()
        r = SessionDB(db_path=db_path)
        try:
            # Break only the session FTS lane (message FTS stays alive).
            r._conn.execute("DROP TABLE sessions_fts")
            r._conn.commit()
            assert r.resolve_session_by_title("foo") is None
        finally:
            r.close()

    def test_resolve_like_lane_accepts_integer_suffix(self, tmp_path):
        """The LIKE fallback lane must also RESOLVE integer '#N' continuations
        whose base contains a wildcard ('my_notes' -> 'my_notes #2') (#15)."""
        db_path = tmp_path / "s.db"
        w = SessionDB(db_path=db_path)
        w.create_session("s1", source="cli")
        w.set_session_title("s1", "my_notes #2")
        w.close()
        r = SessionDB(db_path=db_path)
        try:
            # Break only the session FTS lane (message FTS stays alive).
            r._conn.execute("DROP TABLE sessions_fts")
            r._conn.commit()
            assert r.resolve_session_by_title("my_notes") == "s1"
        finally:
            r.close()


# =========================================================================
# Group F — concurrency + shared throttle
# =========================================================================


class TestConcurrencyAndThrottle:
    def test_two_runners_claim_disjoint_chunks(self, tmp_path):
        """Two CONCURRENT rebuild runners cannot claim/settle the same chunk:
        progress advances through non-overlapping ranges, every document
        appears exactly once, and the index stays healthy.

        The runners are two threads with a barrier so both genuinely enter
        the claim together — a naive ``a.step() or b.step()`` short-circuits
        and never actually races.
        """
        import threading

        db_path = tmp_path / "s.db"
        _build_populated_sessions_db(db_path, n=1200)
        r1 = SessionDB(db_path=db_path)  # stages H=1200, P=0
        r2 = SessionDB(db_path=db_path)
        try:
            assert r1.get_meta("fts_session_rebuild_high_water") == "1200"
            assert r2.get_meta("fts_session_rebuild_high_water") == "1200"

            barrier = threading.Barrier(2)

            def _runner(db, out):
                barrier.wait()  # both runners enter the claim together
                while db.fts_session_rebuild_step():
                    pass
                out["done"] = True

            outs = ({}, {})
            t1 = threading.Thread(target=_runner, args=(r1, outs[0]))
            t2 = threading.Thread(target=_runner, args=(r2, outs[1]))
            t1.start()
            t2.start()
            t1.join(timeout=120)
            t2.join(timeout=120)
            assert not t1.is_alive() and not t2.is_alive(), "runner stuck"
            assert outs[0].get("done") and outs[1].get("done")

            # Both runners saw completion; the markers were cleared once.
            assert r1.get_meta("fts_session_rebuild_high_water") is None
            assert r2.get_meta("fts_session_rebuild_high_water") is None
            n_sessions = r1._conn.execute(
                "SELECT COUNT(*) FROM sessions"
            ).fetchone()[0]
            n_docs = r1._conn.execute(
                "SELECT COUNT(*) FROM sessions_fts_docsize"
            ).fetchone()[0]
            assert n_docs == n_sessions
            dup = r1._conn.execute(
                "SELECT COUNT(*) FROM (SELECT rowid FROM sessions_fts_docsize "
                "GROUP BY rowid HAVING COUNT(*) > 1)"
            ).fetchone()[0]
            assert dup == 0
            r1._conn.execute(
                "INSERT INTO sessions_fts(sessions_fts, rank) "
                "VALUES('integrity-check', 1)"
            )
        finally:
            r1.close()
            r2.close()

    def test_shared_pause_formula_monkeypatched(self, tmp_path, monkeypatch):
        """The shared pause is max(min_pause, build_time * duty_factor), proven
        without wall-clock sleeps."""
        db_path = tmp_path / "s.db"
        _build_populated_sessions_db(db_path, n=20)
        r = SessionDB(db_path=db_path)
        try:
            r._FTS_REBUILD_DUTY_FACTOR = 4.0
            r._FTS_REBUILD_MIN_PAUSE = 0.2
            sleeps = []
            monkeypatch.setattr("hermes_state_search.time.sleep", sleeps.append)
            r._fts_rebuild_pause(0.5)   # 0.5 * 4.0 = 2.0 >= 0.2
            assert sleeps[-1] == 2.0
            r._fts_rebuild_pause(0.01)  # 0.01 * 4.0 = 0.04 < min 0.2
            assert sleeps[-1] == 0.2
        finally:
            r.close()

    def test_session_loop_routes_through_shared_pause(self, tmp_path, monkeypatch):
        """optimize_fts_storage's session backfill phase uses the SAME shared
        pause helper as the message rebuild — no session copy of the policy."""
        db_path = tmp_path / "s.db"
        _build_populated_sessions_db(db_path, n=1200)
        r = SessionDB(db_path=db_path)
        try:
            sleeps = []
            monkeypatch.setattr("hermes_state_search.time.sleep", sleeps.append)
            result = r.optimize_fts_storage(vacuum=False)
            assert result["ok"] is True
            # The only chunks optimize runs here are the session backfill ones
            # (no messages, no trash, no cjk tokenizer), so every recorded
            # pause went through the shared helper and honors the floor.
            assert sleeps
            assert all(s >= r._FTS_REBUILD_MIN_PAUSE for s in sleeps)
            assert r.get_meta("fts_session_rebuild_high_water") is None
        finally:
            r.close()


# =========================================================================
# Group G — legacy-message-FTS × old-session-FTS cross-layout upgrade path
# =========================================================================


class TestLegacyMessageFTSUpgradePath:
    def test_legacy_message_fts_does_not_block_session_fts_upgrade(
        self, tmp_path
    ):
        """A legacy v22 INLINE messages_fts must NOT block the independent
        sessions_fts upgrade (#25), and a single optimize must settle BOTH.

        _migrate_sessions_row_id() rebuilds sessions via DROP TABLE, and
        SQLite drops table triggers with the table — so the pre-#25
        sessions_fts_* triggers are gone the moment the swap lands. The
        sessions_fts ensure must therefore run for legacy-message DBs too
        (not just the v23-message path), or the DB is left with the old
        internal title-only index, no triggers, and no H/P claim.
        """
        db_path = tmp_path / "legacy-cross.db"
        _build_legacy_message_and_session_fts_db(db_path)

        r = SessionDB(db_path=db_path)
        try:
            # sessions migrated to named row_id (hidden-rowid holes kept).
            assert _row_id_map(r._conn) == {
                "A": 1, "B": 3, "C": 7, "A-child": 8,
            }
            # messages_fts is STILL the legacy inline shape — the #25 path
            # must not have forced the message v23 migration (decoupled).
            assert "tool_name" not in _fts_sql(r._conn, "messages_fts")
            # sessions_fts was converted to the #25 external Unicode shape.
            sql = _fts_sql(r._conn, "sessions_fts")
            assert "content='sessions'" in sql
            assert "content_rowid='row_id'" in sql
            assert "tokenize='unicode61'" in sql
            # Durable H/P claim staged over the historical rows.
            assert r.get_meta("fts_session_rebuild_high_water") == "8"
            assert r.get_meta("fts_session_rebuild_progress") == "0"
            # All three gated session triggers exist (recreated, since the
            # DROP TABLE swap removed the pre-#25 ones).
            for trig in (
                "sessions_fts_insert", "sessions_fts_delete",
                "sessions_fts_update",
            ):
                assert r._conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'trigger' "
                    "AND name = ?", (trig,)
                ).fetchone() is not None
            # A session created after high-water capture is searchable
            # immediately — live trigger maintenance works on this path.
            r.create_session("E", source="cli")
            r.set_session_title("E", "Fresh Post-Capture Title")
            assert _raw_metadata_match_ids(r, "fresh") == ["E"]

            # ONE optimize settles BOTH the legacy message migration AND the
            # session backfill — no close/reopen + second optimize needed.
            result = r.optimize_fts_storage(vacuum=False)
            assert result["ok"] is True, result
            assert r.fts_optimize_available() is False
            assert r.get_meta("fts_rebuild_high_water") is None
            assert r.get_meta("fts_session_rebuild_high_water") is None
            # messages_fts is now v23 external (tool metadata indexed).
            assert "tool_name" in _fts_sql(r._conn, "messages_fts")
            # Historical sessions backfilled and searchable; index fully
            # consistent (rank=1 cross-check).
            assert _raw_metadata_match_ids(r, "alpha") == ["A"]
            assert _raw_metadata_match_ids(r, "gamma") == ["C"]
            _assert_sessions_fts_integrity(r)
        finally:
            r.close()

    def test_empty_legacy_db_leaves_no_zombie_hp_markers(self, tmp_path):
        """An old INTERNAL sessions_fts on a legacy DB with ZERO sessions
        must not leave a permanent H=0/P=0 claim after conversion to
        external: with 0 rows the index is complete by construction, so no
        markers should be staged, optimize must settle (not
        backfill_incomplete), and a reopen must stay clean."""
        db_path = tmp_path / "legacy-empty.db"
        _build_legacy_empty_session_fts_db(db_path)

        r = SessionDB(db_path=db_path)
        try:
            # sessions_fts converted to the #25 external Unicode shape.
            assert "content='sessions'" in _fts_sql(r._conn, "sessions_fts")
            # No zombie H/P pair (0 sessions = complete by construction).
            assert r.get_meta("fts_session_rebuild_high_water") is None
            assert r.get_meta("fts_session_rebuild_progress") is None
            # optimize settles — not permanently pending.
            result = r.optimize_fts_storage(vacuum=False)
            assert result["ok"] is True, result
            assert r.fts_optimize_available() is False
        finally:
            r.close()

        # Reopen stays clean (markers do not reappear).
        r2 = SessionDB(db_path=db_path)
        try:
            assert r2.get_meta("fts_session_rebuild_high_water") is None
            assert r2.fts_optimize_available() is False
        finally:
            r2.close()
