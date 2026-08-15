"""Tests for #19: EOL retirement of the fork's legacy ``simple``-tokenizer FTS.

Final #12/#30 classify the historical fork-only
``messages_fts_trigram(tokenize='simple')`` /
``sessions_fts_trigram(tokenize='simple')`` residue as unsupported history,
not a supported migration state. #19 removes the global loadable-``simple``
shim and structurally detaches the retired residue on writable open — before
schema init can touch the unloadable tokenizer — preserving canonical
``sessions``/``messages`` rows while the modern FTS lifecycle converges.

``libsimple.so`` is never loadable in CI; residue fixtures are fabricated
with the writable_schema repro technique (rewrite the stored DDL to
``tokenize='simple'``).
"""

import sqlite3
import time

from hermes_state import (
    SCHEMA_SQL,
    SessionDB,
    _db_opens_cleanly,
)


def _rewrite_to_simple(conn, table):
    """Fabricate the retired ``tokenize='simple'`` declaration via
    writable_schema (the simple tokenizer is never loadable in CI)."""
    conn.execute("PRAGMA writable_schema=ON")
    conn.execute(
        "UPDATE sqlite_master "
        "SET sql = replace(sql, 'tokenize=''trigram''', 'tokenize=''simple''') "
        "WHERE name = ? AND type = 'table'",
        (table,),
    )
    ver = conn.execute("PRAGMA schema_version").fetchone()[0]
    conn.execute(f"PRAGMA schema_version={ver + 1}")
    conn.execute("PRAGMA writable_schema=OFF")


def _simple_count(db):
    with db._read_ctx() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE sql LIKE '%tokenize=''simple''%'"
        ).fetchone()[0]


def _message_simple_residue_db(path, n=5):
    """Historical pre-v23 message layout: legacy inline ``messages_fts`` plus
    the retired ``messages_fts_trigram(tokenize='simple')`` residue with its
    sync triggers. ``simple`` is never loaded."""
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(SCHEMA_SQL)
    for i in range(n):
        conn.execute(
            "INSERT INTO messages (session_id, role, content, timestamp) "
            "VALUES (?, ?, ?, ?)",
            (f"s{i}", "user", f"content {i}", time.time()),
        )
    conn.executescript(
        """
        CREATE VIRTUAL TABLE messages_fts USING fts5(content);
        CREATE VIRTUAL TABLE messages_fts_trigram USING fts5(content, tokenize='trigram');
        CREATE TRIGGER IF NOT EXISTS messages_fts_trigram_insert AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts_trigram(rowid, content) VALUES (new.id, new.content);
        END;
        CREATE TRIGGER IF NOT EXISTS messages_fts_trigram_delete AFTER DELETE ON messages BEGIN
            DELETE FROM messages_fts_trigram WHERE rowid = old.id;
        END;
        """
    )
    _rewrite_to_simple(conn, "messages_fts_trigram")
    conn.commit()
    conn.close()


def _session_simple_residue_db(path, n=5):
    """Historical ``sessions_fts_trigram(tokenize='simple')`` residue
    (title-only, TEXT-id sync triggers) over canonical sessions rows."""
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(SCHEMA_SQL)
    for i in range(n):
        conn.execute(
            "INSERT INTO sessions (id, source, started_at, title, display_name) "
            "VALUES (?, ?, ?, ?, ?)",
            (f"sess-{i}", "test", time.time(), f"Title {i}", f"Display {i}"),
        )
    conn.executescript(
        """
        CREATE VIRTUAL TABLE sessions_fts_trigram USING fts5(title, tokenize='trigram');
        CREATE TRIGGER IF NOT EXISTS sessions_fts_trigram_insert AFTER INSERT ON sessions BEGIN
            INSERT INTO sessions_fts_trigram(rowid, title) VALUES (new.id, new.title);
        END;
        CREATE TRIGGER IF NOT EXISTS sessions_fts_trigram_delete AFTER DELETE ON sessions BEGIN
            DELETE FROM sessions_fts_trigram WHERE rowid = old.id;
        END;
        """
    )
    _rewrite_to_simple(conn, "sessions_fts_trigram")
    conn.commit()
    conn.close()


def test_message_simple_residue_converges(tmp_path):
    db_path = tmp_path / "state.db"
    _message_simple_residue_db(db_path)
    session_db = SessionDB(db_path=db_path)
    try:
        # Canonical message rows are preserved; the residue is gone.
        with session_db._read_ctx() as conn:
            rows = conn.execute(
                "SELECT id, session_id, content FROM messages ORDER BY id"
            ).fetchall()
        assert [r["content"] for r in rows] == [f"content {i}" for i in range(5)]
        assert _simple_count(session_db) == 0
        # Modern message FTS rebuilt from canonical rows.
        assert session_db._trigram_available is True
    finally:
        session_db.close()


def test_session_simple_residue_converges(tmp_path):
    db_path = tmp_path / "state.db"
    _session_simple_residue_db(db_path)
    session_db = SessionDB(db_path=db_path)
    try:
        with session_db._read_ctx() as conn:
            rows = conn.execute(
                "SELECT row_id, id, title, display_name FROM sessions ORDER BY row_id"
            ).fetchall()
        assert [r["id"] for r in rows] == [f"sess-{i}" for i in range(5)]
        assert [r["title"] for r in rows] == [f"Title {i}" for i in range(5)]
        assert [r["display_name"] for r in rows] == [f"Display {i}" for i in range(5)]
        assert _simple_count(session_db) == 0
        # Modern normalized trigram serves (residue removed as EOL debt).
        assert session_db._sessions_trigram_available is True
        # Canonical session writes still work after the residue is gone.
        session_db.create_session("fresh-session", "test")
    finally:
        session_db.close()


def test_unknown_same_name_untouched(tmp_path):
    """A foreign same-name ``sessions_fts_trigram`` (unicode61, not the retired
    simple shape) is never deleted by name — it survives byte-for-schema."""
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "CREATE VIRTUAL TABLE sessions_fts_trigram USING fts5(x, tokenize='unicode61')"
    )
    conn.commit()
    conn.close()
    session_db = SessionDB(db_path=db_path)
    try:
        with session_db._read_ctx() as conn:
            sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'sessions_fts_trigram'"
            ).fetchone()[0]
        assert "tokenize='unicode61'" in sql
        session_db.create_session("fresh-session", "test")
    finally:
        session_db.close()


def test_loader_removed(tmp_path, monkeypatch):
    """The global simple-extension shim is gone: no ``_simple_loaded`` flag,
    and a bogus ``HERMES_LIBSIMPLE_PATH`` can't affect a fresh writer/read."""
    monkeypatch.setenv(
        "HERMES_LIBSIMPLE_PATH", str(tmp_path / "missing" / "libsimple.so")
    )
    session_db = SessionDB(db_path=tmp_path / "state.db")
    try:
        assert not hasattr(session_db, "_simple_loaded")
        session_db.set_meta("k", "v")
        with session_db._read_ctx() as conn:
            assert (
                conn.execute(
                    "SELECT value FROM state_meta WHERE key = 'k'"
                ).fetchone()[0]
                == "v"
            )
    finally:
        session_db.close()


def test_health_probe_needs_no_simple(tmp_path):
    db_path = tmp_path / "state.db"
    SessionDB(db_path=db_path).close()
    assert _db_opens_cleanly(db_path) is None
