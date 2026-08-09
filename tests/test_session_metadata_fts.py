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

from hermes_state import SCHEMA_SQL, SessionDB


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
    """FTS5 integrity-check raises on a corrupt external-content index."""
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


# =========================================================================
# Group D — trigger ownership regions (<=P, (P,H], >H) + deletes + integrity
# =========================================================================


class TestTriggerOwnershipRegions:
    def test_delete_indexed_prefix_row_removes_doc(self, tmp_path):
        """Deleting a ``<=P`` row removes its already-indexed document."""
        r = _three_region_db(tmp_path)
        try:
            r.delete_session("A")  # row_id 1, indexed
            assert "A" not in _raw_metadata_match_ids(r, "title")
            _assert_sessions_fts_integrity(r)
        finally:
            r.close()

    def test_delete_gap_row_does_not_issue_fts_delete(self, tmp_path):
        """Deleting a ``(P,H]`` row (never indexed) must not issue an
        external-content FTS delete; integrity stays healthy."""
        r = _three_region_db(tmp_path)
        try:
            r.delete_session("C")  # row_id 3, in (P,H]
            _assert_sessions_fts_integrity(r)
            # Row is simply absent — backfill later finds no canonical row.
            assert r.get_session("C") is None
        finally:
            r.close()

    def test_delete_live_row_removes_doc(self, tmp_path):
        """Deleting a ``>H`` live row (indexed by the trigger at insert time)
        removes its live document."""
        r = _three_region_db(tmp_path)
        try:
            r.create_session("E", source="cli")  # row_id 5 > H
            r.set_session_title("E", "Live Echo")
            assert _raw_metadata_match_ids(r, "echo") == ["E"]
            r.delete_session("E")
            assert "E" not in _raw_metadata_match_ids(r, "echo")
            _assert_sessions_fts_integrity(r)
        finally:
            r.close()

    def test_gap_region_update_does_not_corrupt_index(self, tmp_path):
        """Updating a ``(P,H]`` row must not rewrite a document that was never
        indexed; integrity stays healthy."""
        r = _three_region_db(tmp_path)
        try:
            r.set_session_title("C", "New Title C")  # row_id 3, in (P,H]
            _assert_sessions_fts_integrity(r)
        finally:
            r.close()

    def test_live_session_searchable_while_backfill_pending(self, tmp_path):
        """A session created after high-water capture is searchable
        immediately while the historical backfill remains incomplete."""
        r = _three_region_db(tmp_path)
        try:
            r.create_session("E", source="cli")  # row_id 5 > H
            r.set_session_title("E", "Fresh Live Title")
            assert _raw_metadata_match_ids(r, "fresh") == ["E"]
        finally:
            r.close()


# =========================================================================
# Group E — bounded-gap search supplementation + boundary finish
# =========================================================================


def _metadata_search_ids(db, query):
    """Search session metadata through the raw Unicode lane + bounded (P,H]
    supplement (issue #25). Returns logical session ids, deduplicated."""
    return [c["id"] for c in db._fts_metadata_candidates(query)]


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
