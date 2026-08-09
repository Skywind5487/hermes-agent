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
