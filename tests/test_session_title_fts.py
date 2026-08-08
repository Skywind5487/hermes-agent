"""Tests for the v26 session-title FTS architecture (issue #12).

Covers the named ``sessions.row_id`` migration, the external-content
session-title indexes, the resumable chunked backfill with bounded-gap
title search, the title-only UPDATE triggers, and the unified FTS
maintenance lifecycle (optimize / rebuild / optimize-storage settlement).
"""

import sqlite3
import time

import pytest

from hermes_state import FTS_STORAGE_VERSION, SCHEMA_SQL, SCHEMA_VERSION, SessionDB


@pytest.fixture()
def db(tmp_path):
    """Fresh SessionDB (v26 layout: named sessions.row_id + external
    session-title FTS) over a temp database file."""
    db_path = tmp_path / "state.db"
    session_db = SessionDB(db_path=db_path)
    yield session_db
    session_db.close()


def _column_names(conn, table):
    return {
        r[1] if isinstance(r, (tuple, list)) else r["name"]
        for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    }


def _fts_sql(conn, table):
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return (row[0] if isinstance(row, sqlite3.Row) else row[0]) if row else ""


def _build_v25_db(db_path):
    """Build a pre-v26 DB by hand: sessions WITHOUT row_id (id TEXT PRIMARY
    KEY), internal-content sessions_fts, schema_version 25. Everything else
    matches the modern schema (SCHEMA_SQL), so the open path must add row_id
    via the v26 migration and keep the legacy session FTS working."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(SCHEMA_SQL)

    # Demote sessions to the v25 shape (no row_id).
    conn.executescript("""
        DROP TABLE sessions;
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
            pinned INTEGER NOT NULL DEFAULT 0
        );
    """)

    # Replace the v26 external session-title FTS with the v25 internal shape.
    conn.executescript("""
        DROP TRIGGER IF EXISTS sessions_fts_insert;
        DROP TRIGGER IF EXISTS sessions_fts_delete;
        DROP TRIGGER IF EXISTS sessions_fts_update;
        DROP TABLE IF EXISTS sessions_fts;
        DROP TABLE IF EXISTS sessions_fts_cjk;

        CREATE VIRTUAL TABLE sessions_fts USING fts5(title, tokenize='unicode61');
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
    """)

    # Stamp v25 so the v26 migration runs on open.
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version (version) VALUES (25)")

    conn.execute(
        "INSERT INTO sessions (id, source, started_at, title) VALUES (?, ?, ?, ?)",
        ("root", "cli", time.time(), "Root Project"),
    )
    conn.execute(
        "INSERT INTO sessions (id, source, started_at, title, parent_session_id) "
        "VALUES (?, ?, ?, ?, ?)",
        ("child", "cli", time.time() + 1, "Root Project #2", "root"),
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) "
        "VALUES (?, ?, ?, ?)",
        ("root", "user", "hello", time.time()),
    )
    conn.commit()
    conn.close()


# =========================================================================
# Named row_id on sessions
# =========================================================================


class TestNamedRowId:
    def test_fresh_install_sessions_has_named_row_id(self, db):
        """v26 installs are born with the named INTEGER PRIMARY KEY rowid."""
        assert "row_id" in _column_names(db._conn, "sessions")
        # The text id stays the logical identity (NOT NULL UNIQUE).
        db.create_session("s1", source="cli")
        db.create_session("s2", source="cli")
        rows = db._conn.execute(
            "SELECT row_id, id FROM sessions ORDER BY row_id"
        ).fetchall()
        ids = [r[1] if isinstance(r, sqlite3.Row) else r[1] for r in rows]
        assert ids == ["s1", "s2"]
        row_ids = [r[0] if isinstance(r, sqlite3.Row) else r[0] for r in rows]
        assert row_ids == sorted(row_ids)

    def test_row_id_stable_across_vacuum(self, db):
        """VACUUM must not renumber the named rowid alias (issue #12)."""
        db.create_session("a", source="cli")
        db.create_session("b", source="cli")
        db.create_session("c", source="cli")
        before = {
            r[0]: r[1]
            for r in db._conn.execute(
                "SELECT id, row_id FROM sessions"
            ).fetchall()
        }
        db.vacuum()
        after = {
            r[0]: r[1]
            for r in db._conn.execute(
                "SELECT id, row_id FROM sessions"
            ).fetchall()
        }
        assert before == after

    def test_v25_migration_preserves_ids_relationships_and_rowids(self, tmp_path):
        """Opening a v25 DB adds row_id, preserves every logical id and the
        messages.session_id relationship, and keeps legacy session FTS
        working through the rebuild."""
        db_path = tmp_path / "legacy.db"
        _build_v25_db(db_path)

        session_db = SessionDB(db_path=db_path)
        try:
            # row_id added by the v26 migration.
            assert "row_id" in _column_names(session_db._conn, "sessions")
            # Logical ids preserved and unique.
            ids = [
                r[0] for r in session_db._conn.execute(
                    "SELECT id FROM sessions ORDER BY row_id"
                ).fetchall()
            ]
            assert ids == ["root", "child"]
            # The parent/child relationship keyed by text id survived.
            parent = session_db._conn.execute(
                "SELECT parent_session_id FROM sessions WHERE id = 'child'"
            ).fetchone()
            assert parent[0] == "root"
            # messages.session_id -> sessions.id still resolves.
            msg = session_db._conn.execute(
                "SELECT session_id FROM messages"
            ).fetchone()
            assert msg[0] == "root"
            # The legacy internal-content session-title FTS stayed coherent:
            # the rebuild copies in rowid order, so the legacy index rows
            # still point at the right sessions.
            assert session_db._db_has_legacy_session_inline_fts(session_db._conn)
            assert session_db.get_session_by_title("Root Project") is not None
            # schema_version advanced to current.
            ver = session_db._conn.execute(
                "SELECT version FROM schema_version LIMIT 1"
            ).fetchone()
            assert int(ver[0]) == SCHEMA_VERSION
        finally:
            session_db.close()


# =========================================================================
# External-content session-title FTS
# =========================================================================


class TestSessionTitleFTS:
    def test_fresh_session_fts_is_external_content(self, db):
        sql = _fts_sql(db._conn, "sessions_fts")
        assert "content='sessions'" in sql
        assert "content_rowid='row_id'" in sql

    def test_title_search_via_external_fts(self, db):
        db.create_session("s1", source="cli")
        db.create_session("s2", source="cli")
        db.set_session_title("s1", "Bounded Title")
        db.set_session_title("s2", "Bounded Title #2")
        # Numbered-variant resolution reads through sessions_fts.
        assert db.resolve_session_by_title("Bounded Title") == "s2"
        assert db.get_next_title_in_lineage("Bounded Title") == "Bounded Title #3"

    def test_title_only_update_trigger_does_not_rewrite_index(self, db):
        db.create_session("s1", source="cli")
        db.set_session_title("s1", "Alpha")
        before = db._conn.execute(
            "SELECT COUNT(*) FROM sessions_fts_docsize"
        ).fetchone()[0]
        # An unrelated session metadata write must NOT fire the title trigger.
        db._conn.execute(
            "UPDATE sessions SET last_activity_at = ? WHERE id = 's1'",
            (time.time(),),
        )
        db._conn.commit()
        after = db._conn.execute(
            "SELECT COUNT(*) FROM sessions_fts_docsize"
        ).fetchone()[0]
        assert after == before
        # Title still searchable.
        assert db.get_session_by_title("Alpha") is not None

    def test_tokenizer_less_open_drops_session_cjk_triggers(self, db):
        """A tokenizer-less process must not leave live sessions_fts_cjk
        triggers behind — they would break every session write (issue #12
        self-heal, mirroring the message CJK index)."""
        db.create_session("s1", source="cli")
        db.set_session_title("s1", "Alpha")
        # Simulate a DB that carries the session CJK index + live triggers
        # (built on a tokenizer-capable host) being opened by a process whose
        # cjk_unicode61 extension cannot load. unicode61 stands in for the
        # tokenizer here — the table shape is what the self-heal inspects.
        db._conn.executescript("""
            CREATE VIRTUAL TABLE sessions_fts_cjk USING fts5(
                title, tokenize='unicode61'
            );
            CREATE TRIGGER sessions_fts_cjk_insert AFTER INSERT ON sessions BEGIN
                INSERT INTO sessions_fts_cjk(rowid, title)
                VALUES (new.row_id, new.title);
            END;
            CREATE TRIGGER sessions_fts_cjk_delete AFTER DELETE ON sessions BEGIN
                DELETE FROM sessions_fts_cjk WHERE rowid = old.row_id;
            END;
            CREATE TRIGGER sessions_fts_cjk_update AFTER UPDATE ON sessions BEGIN
                DELETE FROM sessions_fts_cjk WHERE rowid = old.row_id;
                INSERT INTO sessions_fts_cjk(rowid, title)
                VALUES (new.row_id, new.title);
            END;
        """)
        db._conn.commit()
        db._fts_cjk_loaded = False

        db._ensure_session_fts_cjk_schema(db._conn)

        # Live triggers dropped, stale breadcrumb set, index not served.
        live = [
            r[0] for r in db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                "AND name LIKE 'sessions_fts_cjk_%'"
            ).fetchall()
        ]
        assert live == []
        assert db.get_meta("fts_session_cjk_stale") == "1"
        assert db._sessions_cjk_available is False
        # Session writes still work (no cjk trigger fires on INSERT).
        db.create_session("s2", source="cli")
        db.set_session_title("s2", "Beta")
        assert db.get_session_by_title("Beta") is not None


# =========================================================================
# Bounded-gap title search during backfill
# =========================================================================


class TestBoundedGapSupplement:
    def test_gap_rows_found_while_backfill_pending(self, db):
        db.create_session("s1", source="cli")
        db.set_session_title("s1", "Foo")
        # Pretend a session-title rebuild is mid-flight: it captured
        # high_water = 3 before s2/s3 existed and has backfilled through 2.
        db._conn.execute(
            "INSERT INTO state_meta (key, value) VALUES "
            "('fts_session_rebuild_high_water', '3')"
        )
        db._conn.execute(
            "INSERT INTO state_meta (key, value) VALUES "
            "('fts_session_rebuild_progress', '2')"
        )
        db._conn.commit()
        # s2 (row_id 2 <= P) is indexed live by the triggers; s3 (row_id 3,
        # in (2, 3]) is NOT — it lives in the unindexed gap. Give s3 a clearly
        # later started_at so "latest continuation" is deterministic.
        db.create_session("s2", source="cli")
        db.set_session_title("s2", "Foo #1")
        db.create_session("s3", source="cli")
        db.set_session_title("s3", "Foo #2")
        db._conn.execute(
            "UPDATE sessions SET started_at = ? WHERE id = 's3'",
            (time.time() + 100,),
        )
        db._conn.commit()

        hits = db._fts_numbered_variants("Foo")
        assert hits is not None
        hit_ids = {r["id"] for r in hits}
        assert hit_ids == {"s2", "s3"}, \
            "gap row (s3) must be supplemented so no valid session is hidden"
        # resolve prefers the latest continuation.
        assert db.resolve_session_by_title("Foo") == "s3"

    def test_no_gap_when_no_backfill_pending(self, db):
        db.create_session("s1", source="cli")
        db.set_session_title("s1", "Bar")
        assert db._session_fts_rebuild_gap() is None


# =========================================================================
# optimize-storage conversion + unified lifecycle
# =========================================================================


class TestOptimizeStorageSessionFTS:
    def test_legacy_session_fts_converted_and_settles_to_v2(self, tmp_path):
        db_path = tmp_path / "legacy.db"
        _build_v25_db(db_path)

        session_db = SessionDB(db_path=db_path)
        try:
            # Legacy internal session-title FTS is reported as optimizable.
            assert session_db.fts_optimize_available() is True

            result = session_db.optimize_fts_storage(vacuum=False)
            assert result["ok"] is True

            # Session-title FTS is now external-content.
            assert not session_db._db_has_legacy_session_inline_fts(
                session_db._conn
            )
            sql = _fts_sql(session_db._conn, "sessions_fts")
            assert "content='sessions'" in sql
            assert "content_rowid='row_id'" in sql

            # Historical titles were backfilled (external index is populated).
            n = session_db._conn.execute(
                "SELECT COUNT(*) FROM sessions_fts_docsize"
            ).fetchone()[0]
            assert n == 2  # root + child

            # Title search works through the external index.
            assert session_db.get_session_by_title("Root Project") is not None

            # Storage layout settled to v2 (message + session external).
            ver = session_db.get_meta("fts_storage_version")
            assert int(ver) == FTS_STORAGE_VERSION
            # Nothing left to optimize.
            assert session_db.fts_optimize_available() is False
        finally:
            session_db.close()

    def test_session_tables_in_unified_registry(self, db):
        """optimize / incremental merge / rebuild / VACUUM all see the
        session-title indexes via the one _FTS_TABLES registry."""
        assert "sessions_fts" in db._FTS_TABLES
        assert "sessions_fts_cjk" in db._FTS_TABLES
        # optimize + rebuild run over the whole registry without error.
        db.create_session("s1", source="cli")
        db.set_session_title("s1", "Registry Title")
        optimized = db.optimize_fts()
        assert optimized >= 2  # at least messages_fts + sessions_fts
        rebuilt = db.rebuild_fts()
        assert rebuilt >= 2
        # After rebuild, the session-title search still resolves.
        assert db.get_session_by_title("Registry Title") is not None
