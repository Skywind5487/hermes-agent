"""Tests for the #30 normalized external-content trigram session-metadata FTS.

Covers the modern ``sessions_fts_trigram`` (FTS5 ``tokenize='trigram'``
external-content over the derived ``sessions_fts_trigram_src`` VIEW: compact
title, raw id, compact display_name), its own independent resumable H/P
rebuild lane, the canonical compact-separator policy, live narrow
maintenance triggers, and the legacy same-name ``tokenize='simple'``
convergence (detected by schema identity, never by table name alone).

Scoped per #30: normalized trigram only. Raw Unicode (#25), CJK (#26), the
unified lifecycle registry (#27), and storage-v2 settlement (#31) are out of
scope here.
"""

import sqlite3
import time

import pytest

from hermes_state import SCHEMA_SQL, SessionDB
from hermes_state_common import (
    SESSION_METADATA_COMPACT_SEPARATORS,
    compact_session_metadata_text,
)


@pytest.fixture()
def db(tmp_path):
    """Fresh SessionDB (#30 layout: modern external-content trigram session
    metadata index) over a temp database file."""
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


def _view_sql(conn, view):
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'view' AND name = ?",
        (view,),
    ).fetchone()
    return (row[0] if isinstance(row, sqlite3.Row) else row[0]) if row else ""


class _FtsProbeBlockingCursor:
    """Delegates to a real cursor but raises on ``PRAGMA table_info`` —
    simulating a SQLite build where CONNECTING the FTS5 vtable fails because
    the declared tokenizer is missing on this host. The classifier's identity
    checks are pure ``sqlite_master`` DDL reads and must never trip it."""

    def __init__(self, real):
        self._real = real

    def execute(self, sql, *args, **kwargs):
        if "PRAGMA table_info" in sql:
            raise sqlite3.OperationalError("no such tokenizer: trigram")
        return self._real.execute(sql, *args, **kwargs)


def _set_trigram_rebuild_markers(db, high_water, progress):
    db.set_meta("fts_session_trigram_rebuild_high_water", str(high_water))
    db.set_meta("fts_session_trigram_rebuild_progress", str(progress))


def _trigram_docsize_count(db):
    with db._read_ctx() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM sessions_fts_trigram_docsize"
        ).fetchone()[0]


def _assert_trigram_internal_integrity(db):
    """Ordinary FTS5 internal integrity-check (safe mid-migration when the
    ``(P, H]`` gap rows are legitimately unindexed)."""
    with db._read_ctx() as conn:
        conn.execute(
            "INSERT INTO sessions_fts_trigram(sessions_fts_trigram) "
            "VALUES('integrity-check')"
        )


def _assert_trigram_integrity(db):
    """FTS5 integrity-check that also cross-checks external content
    (``rank = 1``) — only valid on a complete index."""
    with db._read_ctx() as conn:
        conn.execute(
            "INSERT INTO sessions_fts_trigram(sessions_fts_trigram, rank) "
            "VALUES('integrity-check', 1)"
        )


def _trigram_match_ids(db, query):
    """Session ids whose trigram document MATCHes query (canonical join)."""
    with db._read_ctx() as conn:
        rows = conn.execute(
            "SELECT s.id FROM sessions_fts_trigram f "
            "JOIN sessions s ON s.row_id = f.rowid "
            "WHERE sessions_fts_trigram MATCH ?",
            (query,),
        ).fetchall()
    return [r["id"] for r in rows]


def _trigram_rowids(db, query):
    """FTS rowids MATCHing query, read DIRECTLY from the index (delete-test
    probe: a stale posting would still MATCH here even with the canonical row
    gone)."""
    with db._read_ctx() as conn:
        rows = conn.execute(
            "SELECT rowid FROM sessions_fts_trigram "
            "WHERE sessions_fts_trigram MATCH ?",
            (query,),
        ).fetchall()
    return [r["rowid"] for r in rows]


def _build_populated_sessions_db(db_path, n=12):
    """DB with ``n`` sessions (row_ids 1..n, a couple carrying the #30 sample
    metadata) and NO trigram index — opening stages a full trigram H/P claim
    over a populated DB (the real migration shape)."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(SCHEMA_SQL)
    t0 = time.time()
    conn.executemany(
        "INSERT INTO sessions (row_id, id, source, started_at, title) "
        "VALUES (?, ?, 'cli', ?, ?)",
        [(i, f"s{i}", t0 + i, f"Title {i}") for i in range(1, n + 1)],
    )
    conn.execute(
        "UPDATE sessions SET display_name = 'Acme / #an-94-ops' "
        "WHERE row_id = 1"
    )
    conn.execute(
        "UPDATE sessions SET title = 'AN-94 Prestige.Barrel', "
        "id = 'discord:thread-123' WHERE row_id = 2"
    )
    conn.commit()
    conn.close()


def _gap_trigram_db(tmp_path):
    """Populated DB with a staged #30 trigram rebuild: the external trigram
    index was freshly created on a populated DB, so the durable H/P claim
    exists and every historical row falls in the (0, H] gap (nothing indexed
    yet)."""
    db_path = tmp_path / "s.db"
    _build_populated_sessions_db(db_path)
    return SessionDB(db_path=db_path)


def _build_legacy_simple_sessions_trigram_db(db_path):
    """Build a DB carrying the exact historical same-name
    ``sessions_fts_trigram(tokenize='simple')`` object: FTS5, title-only,
    INTERNAL content, three broad triggers keyed by the text session id.

    ``simple`` is not loadable in the test environment, so the fixture builds
    a real vtable and rewrites its stored sqlite_master declaration to
    ``tokenize='simple'`` (the #34 writable_schema repro technique) — the
    classifier must key on the stored declaration, not the runtime.
    """
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(SCHEMA_SQL)
    t0 = time.time()
    conn.executemany(
        "INSERT INTO sessions (row_id, id, source, started_at, title) "
        "VALUES (?, ?, 'cli', ?, ?)",
        [(1, "A", t0, "Alpha Project"), (2, "B", t0 + 1, "AN-94 Prestige")],
    )
    conn.executescript(
        """
        CREATE VIRTUAL TABLE sessions_fts_trigram USING fts5(
            title,
            tokenize='trigram'
        );

        CREATE TRIGGER sessions_fts_trigram_insert AFTER INSERT ON sessions BEGIN
            INSERT INTO sessions_fts_trigram(rowid, title) VALUES (new.id, new.title);
        END;

        CREATE TRIGGER sessions_fts_trigram_delete AFTER DELETE ON sessions BEGIN
            DELETE FROM sessions_fts_trigram WHERE rowid = old.id;
        END;

        CREATE TRIGGER sessions_fts_trigram_update AFTER UPDATE ON sessions BEGIN
            DELETE FROM sessions_fts_trigram WHERE rowid = old.id;
            INSERT INTO sessions_fts_trigram(rowid, title) VALUES (new.id, new.title);
        END;
        """
    )
    conn.execute("PRAGMA writable_schema=ON")
    conn.execute(
        "UPDATE sqlite_master "
        "SET sql = replace(sql, \"tokenize='trigram'\", \"tokenize='simple'\") "
        "WHERE name = 'sessions_fts_trigram' AND type = 'table'"
    )
    ver = conn.execute("PRAGMA schema_version").fetchone()[0]
    conn.execute(f"PRAGMA schema_version={ver + 1}")
    conn.execute("PRAGMA writable_schema=OFF")
    conn.commit()
    conn.close()


def _build_unknown_same_name_trigram_db(db_path):
    """DB whose ``sessions_fts_trigram`` is an UNRECOGNIZED same-name object
    (a unicode61 vtable with a different column shape — not the historical
    simple shape, not the modern trigram shape). SessionDB must fail closed
    and leave it untouched."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(SCHEMA_SQL)
    t0 = time.time()
    conn.execute(
        "INSERT INTO sessions (row_id, id, source, started_at) "
        "VALUES (1, 'A', 'cli', ?)",
        (t0,),
    )
    conn.execute(
        "CREATE VIRTUAL TABLE sessions_fts_trigram USING fts5(x, "
        "tokenize='unicode61')"
    )
    conn.commit()
    conn.close()


def _rewrite_tokenizer_to_simple(conn, table):
    """Emulate the historical same-name ``tokenize='simple'`` declaration by
    rewriting the stored sqlite_master.sql (the #34 writable_schema repro)."""
    conn.execute("PRAGMA writable_schema=ON")
    conn.execute(
        "UPDATE sqlite_master "
        "SET sql = replace(sql, \"tokenize='trigram'\", \"tokenize='simple'\") "
        "WHERE name = ? AND type = 'table'",
        (table,),
    )
    ver = conn.execute("PRAGMA schema_version").fetchone()[0]
    conn.execute(f"PRAGMA schema_version={ver + 1}")
    conn.execute("PRAGMA writable_schema=OFF")


def _build_legacy_simple_wrong_shape_trigram_db(db_path):
    """DB whose ``sessions_fts_trigram`` declares ``tokenize='simple'``
    INTERNAL content but is NOT the historical Hermes title-only shape (it
    carries title + display_name). A same-name simple object that is not
    ours — must classify unknown and never be demoted/deleted."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(SCHEMA_SQL)
    t0 = time.time()
    conn.execute(
        "INSERT INTO sessions (row_id, id, source, started_at) "
        "VALUES (1, 'A', 'cli', ?)",
        (t0,),
    )
    conn.execute(
        "CREATE VIRTUAL TABLE sessions_fts_trigram USING fts5("
        "title, display_name, tokenize='trigram')"
    )
    _rewrite_tokenizer_to_simple(conn, "sessions_fts_trigram")
    conn.commit()
    conn.close()


def _build_trigram_missing_id_db(db_path):
    """DB whose ``sessions_fts_trigram`` declares the modern trigram +
    ``content='sessions_fts_trigram_src'`` + ``content_rowid='row_id'`` but
    LACKS the logical ``id`` column — a near-match modern shape (the old
    ``'id' in sql`` check was fooled by ``content_rowid='row_id'``). Must
    classify unknown and fail closed."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(SCHEMA_SQL)
    t0 = time.time()
    conn.execute(
        "INSERT INTO sessions (row_id, id, source, started_at) "
        "VALUES (1, 'A', 'cli', ?)",
        (t0,),
    )
    conn.execute(
        "CREATE VIEW sessions_fts_trigram_src AS "
        "SELECT row_id, title, id, display_name FROM sessions"
    )
    conn.execute(
        "CREATE VIRTUAL TABLE sessions_fts_trigram USING fts5("
        "title, display_name, "
        "content='sessions_fts_trigram_src', content_rowid='row_id', "
        "tokenize='trigram')"
    )
    conn.commit()
    conn.close()


def _build_modern_root_incompatible_view_db(db_path):
    """DB whose ``sessions_fts_trigram`` is exactly modern (title, id,
    display_name) but whose derived source VIEW projects an incompatible
    shape (row_id, title only) — the index would read wrong content. Must
    classify unknown and fail closed."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(SCHEMA_SQL)
    t0 = time.time()
    conn.execute(
        "INSERT INTO sessions (row_id, id, source, started_at) "
        "VALUES (1, 'A', 'cli', ?)",
        (t0,),
    )
    conn.execute(
        "CREATE VIEW sessions_fts_trigram_src AS "
        "SELECT row_id, title FROM sessions"
    )
    conn.execute(
        "CREATE VIRTUAL TABLE sessions_fts_trigram USING fts5("
        "title, id, display_name, "
        "content='sessions_fts_trigram_src', content_rowid='row_id', "
        "tokenize='trigram')"
    )
    conn.commit()
    conn.close()


def _build_modern_root_miswired_view_db(db_path):
    """DB whose modern root references a source VIEW whose output column NAMES
    are right (row_id/title/id/display_name) but whose EXPRESSIONS are rewired
    (``display_name AS id``, ``id AS display_name``, raw title) — PRAGMA
    table_info alone cannot see the miswiring; the stored definition must be
    checked against the canonical compact/raw projection. Must classify
    unknown and fail closed."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(SCHEMA_SQL)
    t0 = time.time()
    conn.execute(
        "INSERT INTO sessions (row_id, id, source, started_at) "
        "VALUES (1, 'A', 'cli', ?)",
        (t0,),
    )
    conn.execute(
        "CREATE VIEW sessions_fts_trigram_src AS "
        "SELECT row_id, title, display_name AS id, id AS display_name "
        "FROM sessions"
    )
    conn.execute(
        "CREATE VIRTUAL TABLE sessions_fts_trigram USING fts5("
        "title, id, display_name, "
        "content='sessions_fts_trigram_src', content_rowid='row_id', "
        "tokenize='trigram')"
    )
    conn.commit()
    conn.close()


def _build_modern_root_same_name_table_src_db(db_path):
    """DB whose modern root references a same-name TABLE (not a VIEW) as its
    content source. ``CREATE VIEW IF NOT EXISTS`` silently no-ops over a
    table, so the mismatch is never healable and must classify unknown / fail
    closed — the same-name table survives untouched."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(SCHEMA_SQL)
    t0 = time.time()
    conn.execute(
        "INSERT INTO sessions (row_id, id, source, started_at) "
        "VALUES (1, 'A', 'cli', ?)",
        (t0,),
    )
    conn.execute(
        "CREATE TABLE sessions_fts_trigram_src ("
        "row_id INTEGER PRIMARY KEY, title TEXT, id TEXT, display_name TEXT)"
    )
    conn.execute(
        "CREATE VIRTUAL TABLE sessions_fts_trigram USING fts5("
        "title, id, display_name, "
        "content='sessions_fts_trigram_src', content_rowid='row_id', "
        "tokenize='trigram')"
    )
    conn.commit()
    conn.close()


def _build_root_absent_source_table_db(db_path):
    """DB with NO ``sessions_fts_trigram`` root but a same-name TABLE
    occupying the source name ``sessions_fts_trigram_src`` (with sessions
    present). #30 must fail closed BEFORE creating the VIEW / seeding H/P:
    ``CREATE VIEW IF NOT EXISTS`` silently no-ops over a table, so a modern
    index built here would read the wrong (empty) source and index nothing."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(SCHEMA_SQL)
    t0 = time.time()
    conn.executemany(
        "INSERT INTO sessions (row_id, id, source, started_at, title) "
        "VALUES (?, ?, 'cli', ?, ?)",
        [(1, "A", t0, "Alpha Project"), (2, "B", t0 + 1, "AN-94 Prestige")],
    )
    conn.execute(
        "CREATE TABLE sessions_fts_trigram_src (row_id INTEGER, title TEXT)"
    )
    conn.commit()
    conn.close()


def _build_legacy_simple_with_source_table_db(db_path):
    """Exact legacy ``tokenize='simple'`` same-name root PLUS a same-name
    TABLE occupying the source name ``sessions_fts_trigram_src``. #30 must
    NOT demote the legacy root before exposing the source collision — the
    demoted path would build a modern index against the wrong source."""
    _build_legacy_simple_sessions_trigram_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE sessions_fts_trigram_src (row_id INTEGER, title TEXT)"
    )
    conn.commit()
    conn.close()


def _build_same_name_root_view_db(db_path):
    """DB whose ``sessions_fts_trigram`` NAME is occupied by a plain VIEW
    (not an FTS5 vtable). #34: an unknown same-name object must classify
    ``unknown_same_name`` — NEVER ``absent``, which would let the ensure path
    ``CREATE VIRTUAL TABLE IF NOT EXISTS`` silently no-op over the VIEW, seed
    H/P, and run a catch-up INSERT against a non-updatable VIEW (open
    error)."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(SCHEMA_SQL)
    t0 = time.time()
    conn.execute(
        "INSERT INTO sessions (row_id, id, source, started_at) "
        "VALUES (1, 'A', 'cli', ?)",
        (t0,),
    )
    conn.execute("CREATE VIEW sessions_fts_trigram AS SELECT 1 AS x")
    conn.commit()
    conn.close()


def _build_same_name_root_index_db(db_path):
    """DB whose ``sessions_fts_trigram`` NAME is occupied by a plain INDEX
    (on ``sessions``; no table/view with that name). #34: an unknown
    same-name object must classify ``unknown_same_name`` — NEVER ``absent``,
    which would let the ensure path ``CREATE VIRTUAL TABLE IF NOT EXISTS``
    raise ``there is already an index named sessions_fts_trigram`` instead of
    failing closed."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(SCHEMA_SQL)
    t0 = time.time()
    conn.execute(
        "INSERT INTO sessions (row_id, id, source, started_at) "
        "VALUES (1, 'A', 'cli', ?)",
        (t0,),
    )
    conn.execute("CREATE INDEX sessions_fts_trigram ON sessions(id)")
    conn.commit()
    conn.close()


def _build_legacy_simple_with_unrelated_shadow_db(db_path):
    """Exact legacy ``tokenize='simple'`` same-name root PLUS an unrelated
    table that merely shares the ``sessions_fts_trigram_`` prefix
    (``sessions_fts_trigram_unrelated``) with a sentinel row. The demotion's
    shadow discovery must NOT sweep it into ``fts_v22_trash_*`` (teardown
    would delete it) — only the exact legacy FTS5 shadow tables may move."""
    _build_legacy_simple_sessions_trigram_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE sessions_fts_trigram_unrelated (k TEXT PRIMARY KEY, v TEXT)"
    )
    conn.execute(
        "INSERT INTO sessions_fts_trigram_unrelated VALUES ('sentinel', 'keep')"
    )
    conn.commit()
    conn.close()


def _seed_an94_row(db):
    """Insert the canonical #30 sample metadata row (live, > H on a fresh
    DB with no markers) so search fixtures share one shape."""
    db.create_session("an94", source="cli")
    db._conn.execute(
        "UPDATE sessions SET title = 'AN-94 Prestige.Barrel', "
        "display_name = 'Acme / #an-94-ops' WHERE id = 'an94'"
    )
    db._conn.commit()


# =========================================================================
# Group A — modern schema / representation identity
# =========================================================================


class TestModernSchemaIdentity:
    def test_modern_trigram_ddl_is_external_content(self, db):
        """sessions_fts_trigram is a modern FTS5 external-content table with
        ``tokenize='trigram'``, keyed by stable ``sessions.row_id``."""
        sql = _fts_sql(db._conn, "sessions_fts_trigram")
        assert "content='sessions_fts_trigram_src'" in sql
        assert "content_rowid='row_id'" in sql
        assert "tokenize='trigram'" in sql
        for col in ("title", "id", "display_name"):
            assert col in sql

    def test_trigram_src_view_projects_compact_and_raw(self, db):
        """The derived VIEW exposes compact title/display_name and RAW id
        without persistent normalized columns."""
        _seed_an94_row(db)
        src = _view_sql(db._conn, "sessions_fts_trigram_src")
        assert "CREATE VIEW" in src
        assert "FROM sessions" in src
        # No persistence: the VIEW reads through canonical sessions.
        row = db._conn.execute(
            "SELECT row_id, title, id, display_name "
            "FROM sessions_fts_trigram_src"
        ).fetchall()
        by_id = {r["id"]: r for r in row}
        rec = by_id["an94"]
        assert rec["title"] == "AN94PrestigeBarrel"
        assert rec["id"] == "an94"  # raw logical id
        assert rec["display_name"] == "Acme/#an94ops"

    def test_compact_policy_removes_only_documented_separators(self):
        """The canonical compact policy removes exactly ``- _ . space`` and
        never silently broadens to arbitrary ``\\W`` punctuation."""
        assert SESSION_METADATA_COMPACT_SEPARATORS == ("-", "_", ".", " ")
        text = "Acme / #an-94_ops.Space"
        assert compact_session_metadata_text(text) == "Acme/#an94opsSpace"
        # The old broad Python regex would strip /, # and every non-word char.
        assert "Acme/#an94opsSpace" != "Acmean94opsSpace"

    def test_no_persistent_normalized_columns(self, db):
        """Feeding the index must not add permanent normalized canonical
        columns to ``sessions``."""
        cols = _column_names(db._conn, "sessions")
        assert "title_search_norm" not in cols
        assert "display_name_search_norm" not in cols


class TestSchemaClassifier:
    def test_classifier_absent(self, tmp_path):
        """A DB without the object classifies absent."""
        db_path = tmp_path / "s.db"
        _build_populated_sessions_db(db_path, n=3)  # SCHEMA_SQL, no trigram
        raw = sqlite3.connect(str(db_path))
        try:
            assert raw.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'sessions_fts_trigram'"
            ).fetchone() is None
            # The classifier is a static method reading the stored schema.
            assert SessionDB._classify_sessions_fts_trigram(raw) == "absent"
        finally:
            raw.close()

    def test_classifier_modern(self, db):
        """The #30 object classifies modern by schema identity (tokenizer +
        content source), not by table name."""
        assert db._classify_sessions_fts_trigram(db._conn) == "modern_trigram"

    def test_classifier_legacy_simple(self, tmp_path):
        """A same-name ``tokenize='simple'`` internal-content object classifies
        as the recognized historical legacy shape."""
        db_path = tmp_path / "legacy.db"
        _build_legacy_simple_sessions_trigram_db(db_path)
        raw = sqlite3.connect(str(db_path))
        try:
            sql = raw.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' "
                "AND name = 'sessions_fts_trigram'"
            ).fetchone()[0]
            assert "tokenize='simple'" in sql
        finally:
            raw.close()
        r = SessionDB(db_path=db_path)
        try:
            # The open path demotes legacy to modern; classify the FINAL shape.
            assert r._classify_sessions_fts_trigram(r._conn) == "modern_trigram"
            assert r.get_meta("fts_session_trigram_rebuild_high_water") is not None
        finally:
            r.close()

    def test_classifier_legacy_simple_without_simple_tokenizer(self, tmp_path):
        """The legacy identity must classify on a RAW connection with NO
        ``simple`` tokenizer loaded — and the open path must still demote it to
        modern. #34's contract: legacy-simple → modern must never require
        ``simple`` (PRAGMA table_info would connect the vtable and raise
        ``no such tokenizer: simple`` on a host without it)."""
        db_path = tmp_path / "legacy.db"
        _build_legacy_simple_sessions_trigram_db(db_path)
        raw = sqlite3.connect(str(db_path))  # no simple loaded
        try:
            assert SessionDB._classify_sessions_fts_trigram(raw) == "legacy_simple"
        finally:
            raw.close()
        r = SessionDB(db_path=db_path)
        try:
            assert r._sessions_trigram_available is True
            assert "tokenize='trigram'" in _fts_sql(r._conn, "sessions_fts_trigram")
            assert r.get_meta("fts_session_trigram_rebuild_high_water") is not None
        finally:
            r.close()

    def test_classifier_legacy_never_connects_vtable(self, tmp_path):
        """Legacy identity is decided from the stored DDL alone — it must
        never issue a statement that CONNECTS the FTS5 vtable (e.g. PRAGMA
        table_info), which raises ``no such tokenizer: simple`` on a host
        without the legacy tokenizer (the #34 legacy→modern-must-not-require-
        simple contract)."""
        db_path = tmp_path / "legacy.db"
        _build_legacy_simple_sessions_trigram_db(db_path)
        raw = sqlite3.connect(str(db_path))
        try:
            cursor = _FtsProbeBlockingCursor(raw.cursor())
            assert SessionDB._classify_sessions_fts_trigram(cursor) == "legacy_simple"
        finally:
            raw.close()

    def test_classifier_modern_never_connects_vtable(self, db):
        """Modern identity is decided from the stored DDL alone — it must
        never CONNECT the FTS5 vtable (PRAGMA table_info), which raises
        ``no such tokenizer: trigram`` on a host without the built-in trigram
        tokenizer. #34: schema identity (modern) is decidable independently
        of runtime capability (modern-but-unavailable)."""
        cursor = _FtsProbeBlockingCursor(db._conn.cursor())
        assert SessionDB._classify_sessions_fts_trigram(cursor) == "modern_trigram"

    def test_classifier_unknown_same_name(self, tmp_path):
        """An unrecognized same-name object fails closed — classified unknown,
        never mistaken for legacy or modern, and never deleted."""
        db_path = tmp_path / "unknown.db"
        _build_unknown_same_name_trigram_db(db_path)
        raw = sqlite3.connect(str(db_path))
        raw.close()
        r = SessionDB(db_path=db_path)
        try:
            assert r._classify_sessions_fts_trigram(r._conn) == "unknown_same_name"
            # Fail closed: the object survives untouched, capability off.
            sql = _fts_sql(r._conn, "sessions_fts_trigram")
            assert "tokenize='unicode61'" in sql
            assert r._sessions_trigram_available is False
        finally:
            r.close()

    def test_classifier_simple_wrong_column_shape_unknown(self, tmp_path):
        """A same-name ``tokenize='simple'`` INTERNAL object that is NOT the
        historical Hermes title-only shape (here title + display_name) must
        classify unknown and be left untouched — never demoted/deleted."""
        db_path = tmp_path / "legacy_wrong.db"
        _build_legacy_simple_wrong_shape_trigram_db(db_path)
        raw = sqlite3.connect(str(db_path))
        raw.close()
        r = SessionDB(db_path=db_path)
        try:
            assert r._classify_sessions_fts_trigram(r._conn) == "unknown_same_name"
            assert r._sessions_trigram_available is False
            # Not demoted: the object (and its title+display_name shape)
            # survives untouched.
            sql = _fts_sql(r._conn, "sessions_fts_trigram")
            assert "tokenize='simple'" in sql
            assert "display_name" in sql
            # No durable trigram claim was staged for a non-ours object.
            assert r.get_meta("fts_session_trigram_rebuild_high_water") is None
        finally:
            r.close()

    def test_classifier_trigram_missing_id_column_unknown(self, tmp_path):
        """A near-match modern root (correct trigram + content + content_rowid
        but NO logical ``id`` column) must classify unknown — the old bare
        ``'id' in sql`` check was fooled by ``content_rowid='row_id'``."""
        db_path = tmp_path / "missing_id.db"
        _build_trigram_missing_id_db(db_path)
        raw = sqlite3.connect(str(db_path))
        raw.close()
        r = SessionDB(db_path=db_path)
        try:
            assert r._classify_sessions_fts_trigram(r._conn) == "unknown_same_name"
            assert r._sessions_trigram_available is False
            # Untouched: the object keeps its missing-id shape; no claim.
            sql = _fts_sql(r._conn, "sessions_fts_trigram")
            assert "tokenize='trigram'" in sql
            assert "display_name" in sql and "title" in sql
            assert r.get_meta("fts_session_trigram_rebuild_high_water") is None
        finally:
            r.close()

    def test_classifier_modern_root_incompatible_view_unknown(self, tmp_path):
        """A modern root whose derived source VIEW projects an incompatible
        shape must classify unknown / fail closed — the index would read
        wrong content."""
        db_path = tmp_path / "bad_view.db"
        _build_modern_root_incompatible_view_db(db_path)
        raw = sqlite3.connect(str(db_path))
        raw.close()
        r = SessionDB(db_path=db_path)
        try:
            assert r._classify_sessions_fts_trigram(r._conn) == "unknown_same_name"
            assert r._sessions_trigram_available is False
            # Untouched: the incompatible VIEW survives as built.
            sql = _view_sql(r._conn, "sessions_fts_trigram_src")
            assert "row_id, title" in sql
            assert r.get_meta("fts_session_trigram_rebuild_high_water") is None
        finally:
            r.close()

    def test_classifier_modern_root_miswired_view_unknown(self, tmp_path):
        """A modern root whose source VIEW has the right output column NAMES
        (row_id/title/id/display_name) but rewired expressions (raw title,
        ``display_name AS id``, ``id AS display_name``) must classify unknown
        — PRAGMA table_info cannot see the miswiring; the stored definition
        must match the canonical compact/raw projection."""
        db_path = tmp_path / "miswired_view.db"
        _build_modern_root_miswired_view_db(db_path)
        raw = sqlite3.connect(str(db_path))
        raw.close()
        r = SessionDB(db_path=db_path)
        try:
            assert r._classify_sessions_fts_trigram(r._conn) == "unknown_same_name"
            assert r._sessions_trigram_available is False
            # Untouched: the miswired VIEW survives as built.
            sql = _view_sql(r._conn, "sessions_fts_trigram_src")
            assert "display_name AS id" in sql
            assert r.get_meta("fts_session_trigram_rebuild_high_water") is None
        finally:
            r.close()

    def test_classifier_modern_root_same_name_table_src_unknown(self, tmp_path):
        """A modern root whose content source is a same-name TABLE (not a
        VIEW) must classify unknown — ``CREATE VIEW IF NOT EXISTS`` silently
        no-ops over a table, so the mismatch is never healable and must fail
        closed (the table survives untouched)."""
        db_path = tmp_path / "same_name_table.db"
        _build_modern_root_same_name_table_src_db(db_path)
        raw = sqlite3.connect(str(db_path))
        raw.close()
        r = SessionDB(db_path=db_path)
        try:
            assert r._classify_sessions_fts_trigram(r._conn) == "unknown_same_name"
            assert r._sessions_trigram_available is False
            # The same-name table is not replaced / deleted.
            obj = r._conn.execute(
                "SELECT type FROM sqlite_master "
                "WHERE name = 'sessions_fts_trigram_src'"
            ).fetchone()
            assert (obj["type"] if isinstance(obj, sqlite3.Row) else obj[0]) == "table"
            assert r.get_meta("fts_session_trigram_rebuild_high_water") is None
        finally:
            r.close()

    def test_classifier_root_same_name_view_unknown(self, tmp_path):
        """A same-name VIEW occupying the root name must classify
        ``unknown_same_name`` — NEVER ``absent``. Classifying it ``absent``
        would let the ensure path ``CREATE VIRTUAL TABLE IF NOT EXISTS``
        silently no-op over the VIEW, seed H/P, and run a catch-up INSERT
        against a non-updatable VIEW → open error. The open path must fail
        closed: no H/P seed, no schema transition, the VIEW preserved, and
        open must not raise."""
        db_path = tmp_path / "root_view.db"
        _build_same_name_root_view_db(db_path)
        raw = sqlite3.connect(str(db_path))
        raw.close()
        r = SessionDB(db_path=db_path)  # must not raise
        try:
            assert r._classify_sessions_fts_trigram(r._conn) == "unknown_same_name"
            assert r._sessions_trigram_available is False
            # No durable trigram claim was staged.
            assert r.get_meta("fts_session_trigram_rebuild_high_water") is None
            assert r.get_meta("fts_session_trigram_rebuild_progress") is None
            # The same-name VIEW survives untouched (no modern FTS table).
            obj = r._conn.execute(
                "SELECT type FROM sqlite_master "
                "WHERE name = 'sessions_fts_trigram'"
            ).fetchone()
            assert (obj["type"] if isinstance(obj, sqlite3.Row) else obj[0]) == "view"
        finally:
            r.close()

    def test_classifier_root_same_name_index_unknown(self, tmp_path):
        """A same-name INDEX occupying the root name must classify
        ``unknown_same_name`` — NEVER ``absent``. ``absent`` would let the
        ensure path ``CREATE VIRTUAL TABLE IF NOT EXISTS`` raise ``there is
        already an index named ...`` instead of failing closed. The open path
        must fail closed: no H/P seed, the index preserved, capability false,
        and open must not raise."""
        db_path = tmp_path / "root_index.db"
        _build_same_name_root_index_db(db_path)
        raw = sqlite3.connect(str(db_path))
        raw.close()
        r = SessionDB(db_path=db_path)  # must not raise
        try:
            assert r._classify_sessions_fts_trigram(r._conn) == "unknown_same_name"
            assert r._sessions_trigram_available is False
            # No durable trigram claim was staged.
            assert r.get_meta("fts_session_trigram_rebuild_high_water") is None
            assert r.get_meta("fts_session_trigram_rebuild_progress") is None
            # The same-name index survives untouched.
            obj = r._conn.execute(
                "SELECT type FROM sqlite_master "
                "WHERE name = 'sessions_fts_trigram'"
            ).fetchone()
            assert (obj["type"] if isinstance(obj, sqlite3.Row) else obj[0]) == "index"
        finally:
            r.close()


# =========================================================================
# Group B — search representation
# =========================================================================


class TestSearchRepresentation:
    def test_compact_title_matches_an94(self, db):
        """``AN-94`` is discoverable by the compact query ``an94``."""
        _seed_an94_row(db)
        assert _trigram_match_ids(db, "an94") == ["an94"]

    def test_compact_display_name_matches_an94(self, db):
        """A gateway display name such as ``Acme / #an-94-ops`` is discoverable
        by the compact query ``an94`` through display_name."""
        _seed_an94_row(db)
        assert _trigram_match_ids(db, "an94") == ["an94"]

    def test_interior_title_fragment_matches(self, db):
        """A true interior title fragment matches through trigram."""
        db.create_session("frag", source="cli")
        db.set_session_title("frag", "Prestige.Barrel Custom")
        db._conn.commit()
        assert _trigram_match_ids(db, "prestigebarrel") == ["frag"]

    def test_raw_punctuation_id_interior_fragment(self, db):
        """Session IDs stay RAW — a punctuation-bearing interior id substring
        matches without compacting the id."""
        db.create_session("thr", source="cli")
        db._conn.execute(
            "UPDATE sessions SET id = 'discord:thread-123' WHERE id = 'thr'"
        )
        db._conn.commit()
        # Via the candidate lane (the #14 seam): the raw id needle (kept raw,
        # never compacted) matches the interior fragment.
        ok, hits = db._fts_session_trigram_candidates("thread-123")
        assert ok is True
        # The canonical logical id IS the raw id — never compacted.
        assert [h["id"] for h in hits] == ["discord:thread-123"]

    def test_candidate_lane_returns_failure_vs_zero(self, db):
        """_fts_session_trigram_candidates returns (True, []) for a valid
        no-match query and (False, ...) only when the FTS lane itself failed —
        the distinction #14's routing needs."""
        _seed_an94_row(db)
        ok, hits = db._fts_session_trigram_candidates("zzzz")
        assert ok is True and hits == []
        ok, hits = db._fts_session_trigram_candidates("an94")
        assert ok is True and [h["id"] for h in hits] == ["an94"]

    def test_gap_supplement_uses_same_semantics(self, tmp_path):
        """The pending (P,H] gap supplement uses the same compact-title /
        compact-display / raw-ID semantics as the indexed lane, so no session
        hides while the backfill is pending."""
        r = _gap_trigram_db(tmp_path)  # H staged, P=0, nothing indexed
        try:
            # Row 2's canonical id is the raw 'discord:thread-123' (the
            # fixture rewrote it); it is in the (0, H] gap and must surface
            # via the compact-title needle 'an94'.
            ok, hits = r._fts_session_trigram_candidates("an94")
            assert ok is True
            ids = {h["id"] for h in hits}
            assert "discord:thread-123" in ids  # row 2 (compact title)
            assert "s1" in ids  # row 1 (compact display_name)
        finally:
            r.close()


# =========================================================================
# Group C — narrow live maintenance
# =========================================================================


class TestNarrowLiveMaintenance:
    def test_insert_produces_one_doc(self, db):
        _seed_an94_row(db)
        assert _trigram_docsize_count(db) == 1
        assert _trigram_rowids(db, "an94") != []

    def test_delete_removes_doc(self, db):
        _seed_an94_row(db)
        rid = db._conn.execute(
            "SELECT row_id FROM sessions WHERE id = 'an94'"
        ).fetchone()["row_id"]
        db.delete_session("an94")
        assert _trigram_rowids(db, "an94") == []
        assert _trigram_docsize_count(db) == 0
        _assert_trigram_integrity(db)

    def test_metadata_update_rewrites_doc(self, db):
        """A title change rewrites the indexed content: the old title's
        compact tokens leave and the new title's arrive. The RAW id ('an94')
        still matches — the #16 raw-id contract, not a stale title posting.
        """
        _seed_an94_row(db)
        db.set_session_title("an94", "Gun-Build V2")
        db._conn.commit()
        assert _trigram_match_ids(db, "gunbuildv2") == ["an94"]
        assert _trigram_match_ids(db, "prestige") == []
        # 'an94' still matches through the RAW id field, not the title.
        assert _trigram_match_ids(db, "an94") == ["an94"]
        _assert_trigram_integrity(db)

    def test_display_name_update_rewrites_doc(self, db):
        _seed_an94_row(db)
        db._conn.execute(
            "UPDATE sessions SET display_name = 'Zulu / #z-1-ops' "
            "WHERE id = 'an94'"
        )
        db._conn.commit()
        assert _trigram_match_ids(db, "z1ops") == ["an94"]
        assert _trigram_match_ids(db, "acme") == []

    def test_unrelated_update_does_not_rewrite(self, db):
        """A heartbeat/accounting update (not title/id/display_name) must not
        rewrite the trigram document."""
        _seed_an94_row(db)
        db._conn.execute(
            "UPDATE sessions SET message_count = 5 WHERE id = 'an94'"
        )
        db._conn.commit()
        assert _trigram_match_ids(db, "an94") == ["an94"]
        trig = db._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
            "AND name = 'sessions_fts_trigram_update_before'"
        ).fetchone()
        sql = trig[0] if not isinstance(trig, sqlite3.Row) else trig["sql"]
        compact = " ".join(sql.split())
        assert "UPDATE OF title, id, display_name" in compact

    def test_same_value_update_does_not_rewrite(self, db):
        _seed_an94_row(db)
        db._conn.execute(
            "UPDATE sessions SET title = 'AN-94 Prestige.Barrel' "
            "WHERE id = 'an94'"
        )
        db._conn.commit()
        assert _trigram_match_ids(db, "an94") == ["an94"]
        _assert_trigram_integrity(db)

    def test_gap_rows_not_double_written_by_triggers(self, tmp_path):
        """Rows in ``(P, H]`` are worker-owned: live triggers leave them alone,
        so the chunk backfill never duplicates documents."""
        r = _gap_trigram_db(tmp_path)  # H staged, P=0, (0, H] unindexed
        try:
            assert _trigram_docsize_count(r) == 0
            # Simulate a worker-owned row update — the triggers must skip it.
            r._conn.execute(
                "UPDATE sessions SET title = 'Changed In Gap' WHERE row_id = 1"
            )
            r._conn.commit()
            assert _trigram_docsize_count(r) == 0
            _assert_trigram_internal_integrity(r)
        finally:
            r.close()


# =========================================================================
# Group D — independent H/P / crash safety
# =========================================================================


class TestIndependentHPRebuild:
    def test_unicode_complete_while_trigram_pending(self, tmp_path):
        """Unicode's P can be cleared/complete while the trigram lane stays
        pending — the trigram index must remain incomplete and correct."""
        r = _gap_trigram_db(tmp_path)  # both lanes staged H, P=0
        try:
            # Finish the Unicode lane only.
            assert r.get_meta("fts_session_rebuild_high_water") is not None
            while r.fts_session_rebuild_step():
                pass
            assert r.get_meta("fts_session_rebuild_high_water") is None
            # Trigram lane still pending on its OWN markers.
            assert r.get_meta("fts_session_trigram_rebuild_high_water") is not None
            assert _trigram_docsize_count(r) == 0
            ok, hits = r._fts_session_trigram_candidates("an94")
            ids = {h["id"] for h in hits}
            assert ok is True and "discord:thread-123" in ids
        finally:
            r.close()

    def test_trigram_resumes_after_restart(self, tmp_path):
        """The trigram worker resumes from its OWN progress after a restart."""
        db_path = tmp_path / "s.db"
        _build_populated_sessions_db(db_path, n=50)
        r = SessionDB(db_path=db_path)
        try:
            # Backfill a prefix by hand, persist, then "restart".
            r._conn.execute(
                "INSERT INTO sessions_fts_trigram(rowid, title, id, display_name) "
                "SELECT row_id, title, id, display_name "
                "FROM sessions_fts_trigram_src WHERE row_id <= 20"
            )
            _set_trigram_rebuild_markers(r, 50, 20)
            r._conn.commit()
        finally:
            r.close()
        r2 = SessionDB(db_path=db_path)
        try:
            assert r2.get_meta("fts_session_trigram_rebuild_progress") == "20"
            assert _trigram_docsize_count(r2) == 20
            while r2.fts_session_trigram_rebuild_step():
                pass
            assert r2.get_meta("fts_session_trigram_rebuild_high_water") is None
            assert _trigram_docsize_count(r2) == 50
            _assert_trigram_integrity(r2)
        finally:
            r2.close()

    def test_orphan_hp_resets_only_trigram(self, tmp_path):
        """An orphan trigram H-without-P resets ONLY the trigram target to a
        known-empty surface before replay — never touches the Unicode lane."""
        db_path = tmp_path / "s.db"
        _build_populated_sessions_db(db_path, n=50)
        r = SessionDB(db_path=db_path)
        try:
            # Backfill a trigram prefix, then lose P (partial index of unknown
            # extent). The Unicode lane is healthy and separate.
            r._conn.execute(
                "INSERT INTO sessions_fts_trigram(rowid, title, id, display_name) "
                "SELECT row_id, title, id, display_name "
                "FROM sessions_fts_trigram_src WHERE row_id <= 20"
            )
            r._conn.execute(
                "DELETE FROM state_meta "
                "WHERE key = 'fts_session_trigram_rebuild_progress'"
            )
            r._conn.commit()
            result = r.optimize_fts_storage(vacuum=False)
            assert result["ok"] is True, result
            assert r.get_meta("fts_session_trigram_rebuild_high_water") is None
            n_docs = _trigram_docsize_count(r)
            n_sessions = r._conn.execute(
                "SELECT COUNT(*) FROM sessions"
            ).fetchone()[0]
            assert n_docs == n_sessions
            _assert_trigram_integrity(r)
        finally:
            r.close()

    def test_empty_trigram_populated_source_seeds_claim(self, tmp_path):
        """Empty modern trigram index + populated source + no trigram markers
        (crash window) seeds a full trigram claim on optimize."""
        db_path = tmp_path / "s.db"
        _build_populated_sessions_db(db_path, n=12)
        r = SessionDB(db_path=db_path)
        try:
            # Drop the markers to simulate the claim-loss crash window.
            r._conn.execute(
                "DELETE FROM state_meta WHERE key IN "
                "('fts_session_trigram_rebuild_high_water', "
                "'fts_session_trigram_rebuild_progress')"
            )
            r._conn.commit()
            assert r.fts_optimize_available() is True
            result = r.optimize_fts_storage(vacuum=False)
            assert result["ok"] is True, result
            assert _trigram_docsize_count(r) == 12
            _assert_trigram_integrity(r)
        finally:
            r.close()

    def test_finish_clears_trigram_markers(self, tmp_path):
        """Completing the trigram backfill clears the trigram markers; once
        every pending lane (incl. the parallel Unicode lane staged on the
        same populated DB) completes, optimize stops advertising work."""
        r = _gap_trigram_db(tmp_path)
        try:
            assert r.fts_optimize_available() is True
            while r.fts_session_trigram_rebuild_step():
                pass
            assert r.get_meta("fts_session_trigram_rebuild_high_water") is None
            # The Unicode session lane is staged independently on the same
            # populated DB — finish it too, then optimize settles.
            while r.fts_session_rebuild_step():
                pass
            assert r.fts_optimize_available() is False
        finally:
            r.close()


class TestTokenizerAbsent:
    def test_trigram_tokenizer_missing_clears_fresh_claim(self, tmp_path, monkeypatch):
        """A host without the trigram tokenizer must not leave a durable
        trigram claim that can never be fulfilled (criterion 10 reverse
        invariant — the #26 CJK stale precedent). The fresh-create claim is
        cleared when the schema transition fails on an incapable host, so
        optimize never advertises permanently-pending trigram work; a later
        capable reopen heals by re-seeding and backfilling."""
        db_path = tmp_path / "s.db"
        _build_populated_sessions_db(db_path, n=12)
        # Simulate the tokenizer-absent outcome: the real transition catches
        # ``no such tokenizer: trigram`` and returns False (fts5/trigram
        # unavailable) — a capable transition failure path is never a False.
        with monkeypatch.context() as m:
            m.setattr(
                SessionDB,
                "_fts_session_trigram_schema_transition",
                lambda self, cursor: False,
            )
            r = SessionDB(db_path=db_path)
            try:
                assert r._sessions_trigram_available is False
                # No stuck claim — the fresh claim seeded before the
                # transition was rolled back because it can never be
                # fulfilled here.
                assert r.get_meta("fts_session_trigram_rebuild_high_water") is None
                assert r.get_meta("fts_session_trigram_rebuild_progress") is None
            finally:
                r.close()

        # Patch undone → a capable reopen heals: fresh-create re-seeds a real
        # claim, and the chunk engine backfills to a complete index.
        r2 = SessionDB(db_path=db_path)
        try:
            assert r2._sessions_trigram_available is True
            assert r2.get_meta("fts_session_trigram_rebuild_high_water") is not None
            while r2.fts_session_trigram_rebuild_step():
                pass
            assert r2.get_meta("fts_session_trigram_rebuild_high_water") is None
            assert _trigram_docsize_count(r2) == 12
            _assert_trigram_integrity(r2)
        finally:
            r2.close()


class TestSourceCollisionGuard:
    """The ensure path must gate the derived-source VIEW creation, the H/P
    seed, and the legacy demotion on ``_sessions_trigram_src_compatible``.
    ``CREATE VIEW IF NOT EXISTS`` silently no-ops when the source NAME is
    occupied by a same-name TABLE or a non-canonical VIEW — the rebuild H
    would then be computed from the wrong source and a modern index would
    silently index nothing (or worse: the legacy root demoted first)."""

    def test_root_absent_source_table_fail_closed(self, tmp_path):
        """Root absent + same-name source TABLE: must NOT build the modern
        index against the wrong source and must NOT seed a durable H/P claim
        — the table survives untouched, capability off."""
        db_path = tmp_path / "absent_src_table.db"
        _build_root_absent_source_table_db(db_path)
        r = SessionDB(db_path=db_path)
        try:
            assert r._sessions_trigram_available is False
            # No modern index was built against the wrong source.
            assert _fts_sql(r._conn, "sessions_fts_trigram") == ""
            # No durable trigram claim was staged.
            assert r.get_meta("fts_session_trigram_rebuild_high_water") is None
            assert r.get_meta("fts_session_trigram_rebuild_progress") is None
            # The same-name source TABLE survives untouched.
            obj = r._conn.execute(
                "SELECT type FROM sqlite_master "
                "WHERE name = 'sessions_fts_trigram_src'"
            ).fetchone()
            assert (obj["type"] if isinstance(obj, sqlite3.Row) else obj[0]) == "table"
        finally:
            r.close()

    def test_legacy_simple_bad_source_does_not_demote(self, tmp_path):
        """exact legacy-simple root + source-name collision (same-name
        TABLE): #30 must NOT demote the legacy root before exposing the
        collision — the demoted path would build a modern index against the
        wrong source. Fail closed: legacy survives, no modern build, no H/P
        seed."""
        db_path = tmp_path / "legacy_src_table.db"
        _build_legacy_simple_with_source_table_db(db_path)
        r = SessionDB(db_path=db_path)
        try:
            assert r._sessions_trigram_available is False
            # The legacy root was NOT demoted — still the historical shape.
            assert r._classify_sessions_fts_trigram(r._conn) == "legacy_simple"
            assert "tokenize='simple'" in _fts_sql(
                r._conn, "sessions_fts_trigram"
            )
            # No durable trigram claim was staged.
            assert r.get_meta("fts_session_trigram_rebuild_high_water") is None
            assert r.get_meta("fts_session_trigram_rebuild_progress") is None
            # The same-name source TABLE survives untouched.
            obj = r._conn.execute(
                "SELECT type FROM sqlite_master "
                "WHERE name = 'sessions_fts_trigram_src'"
            ).fetchone()
            assert (obj["type"] if isinstance(obj, sqlite3.Row) else obj[0]) == "table"
        finally:
            r.close()


# =========================================================================
# Group E — legacy same-name convergence
# =========================================================================


class TestLegacySameNameConvergence:
    def test_legacy_simple_converges_to_modern(self, tmp_path):
        """Opening a legacy-simple DB converges to the modern external-content
        trigram object and stages its own H/P claim."""
        db_path = tmp_path / "legacy.db"
        _build_legacy_simple_sessions_trigram_db(db_path)
        r = SessionDB(db_path=db_path)
        try:
            sql = _fts_sql(r._conn, "sessions_fts_trigram")
            assert "tokenize='trigram'" in sql
            assert "content='sessions_fts_trigram_src'" in sql
            assert r._sessions_trigram_available is True
            assert r.get_meta("fts_session_trigram_rebuild_high_water") is not None
        finally:
            r.close()

    def test_legacy_shadow_tables_moved_to_trash(self, tmp_path):
        """The demoted legacy shadows land in the ordinary FTS trash namespace
        (no longer requiring `simple`) and teardown reclaims them."""
        db_path = tmp_path / "legacy.db"
        _build_legacy_simple_sessions_trigram_db(db_path)
        r = SessionDB(db_path=db_path)
        try:
            trash = [
                row[0] for row in r._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' "
                    "AND name LIKE 'fts_v22_trash_sessions_fts_trigram%' "
                    "ESCAPE '\\'"
                ).fetchall()
            ]
            assert trash, "legacy shadows expected in trash namespace"
            # Teardown drains and drops them.
            while r._fts_teardown_trash_step():
                pass
            remaining = r._conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' "
                "AND name LIKE 'fts_v22_trash_%' ESCAPE '\\'"
            ).fetchone()[0]
            assert remaining == 0
        finally:
            r.close()

    def test_legacy_demotion_leaves_unrelated_prefix_table(self, tmp_path):
        """P1: the demotion's shadow discovery must only move the exact
        legacy FTS5 shadow tables — an unrelated table that merely shares the
        ``sessions_fts_trigram_`` prefix must survive migration AND teardown
        (with its data), never being swept into ``fts_v22_trash_*``."""
        db_path = tmp_path / "legacy_unrelated.db"
        _build_legacy_simple_with_unrelated_shadow_db(db_path)
        r = SessionDB(db_path=db_path)
        try:
            assert r._sessions_trigram_available is True
            # The unrelated table was NOT renamed to trash — it survives in
            # place with its sentinel data.
            obj = r._conn.execute(
                "SELECT type FROM sqlite_master "
                "WHERE name = 'sessions_fts_trigram_unrelated'"
            ).fetchone()
            assert obj is not None
            assert (obj["type"] if isinstance(obj, sqlite3.Row) else obj[0]) == "table"
            row = r._conn.execute(
                "SELECT v FROM sessions_fts_trigram_unrelated WHERE k = 'sentinel'"
            ).fetchone()
            assert (row["v"] if isinstance(row, sqlite3.Row) else row[0]) == "keep"
            # Run the trash teardown to completion — the unrelated table must
            # not be touched even after teardown.
            while r._fts_teardown_trash_step():
                pass
            obj2 = r._conn.execute(
                "SELECT type FROM sqlite_master "
                "WHERE name = 'sessions_fts_trigram_unrelated'"
            ).fetchone()
            assert obj2 is not None
            row2 = r._conn.execute(
                "SELECT v FROM sessions_fts_trigram_unrelated WHERE k = 'sentinel'"
            ).fetchone()
            assert (row2["v"] if isinstance(row2, sqlite3.Row) else row2[0]) == "keep"
        finally:
            r.close()

    def test_legacy_demotion_does_not_require_simple(self, tmp_path):
        """The demotion never touches the legacy vtable directly (no SELECT /
        DROP on it), so it works on a runtime where `simple` is absent."""
        db_path = tmp_path / "legacy.db"
        _build_legacy_simple_sessions_trigram_db(db_path)
        # Confirm the environment really lacks `simple`.
        raw = sqlite3.connect(":memory:")
        try:
            raw.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize='simple')")
            pytest.skip("simple tokenizer unexpectedly available")
        except sqlite3.OperationalError:
            pass
        finally:
            raw.close()
        r = SessionDB(db_path=db_path)
        try:
            assert r._sessions_trigram_available is True
            assert "tokenize='trigram'" in _fts_sql(r._conn, "sessions_fts_trigram")
        finally:
            r.close()

    def test_unknown_same_name_not_deleted(self, tmp_path):
        """An unknown same-name shape is never deleted and never treated as
        the search implementation."""
        db_path = tmp_path / "unknown.db"
        _build_unknown_same_name_trigram_db(db_path)
        r = SessionDB(db_path=db_path)
        try:
            assert _fts_sql(r._conn, "sessions_fts_trigram") != ""
            assert r._sessions_trigram_available is False
            # The object is untouched by the open path.
            assert "tokenize='unicode61'" in _fts_sql(
                r._conn, "sessions_fts_trigram"
            )
        finally:
            r.close()

    def test_legacy_simple_demotion_before_modern_create(self, tmp_path):
        """Demotion-before-modern-create: after the legacy root is removed but
        before the modern schema lands, the durable trigram H/P claim exists
        and reopen resumes the ensure."""
        db_path = tmp_path / "legacy.db"
        _build_legacy_simple_sessions_trigram_db(db_path)
        # Stage the demotion by hand (drop legacy triggers + remove root +
        # rename shadows to trash + seed markers) and LEAVE the modern schema
        # uncreated — the crash window between demotion commit and schema
        # ensure. This mirrors the production demotion's atomic outcome.
        raw = sqlite3.connect(str(db_path))
        raw.execute("BEGIN IMMEDIATE")
        for t in (
            "sessions_fts_trigram_insert",
            "sessions_fts_trigram_delete",
            "sessions_fts_trigram_update",
        ):
            raw.execute(f"DROP TRIGGER IF EXISTS {t}")
        raw.execute("PRAGMA writable_schema=ON")
        raw.execute(
            "DELETE FROM sqlite_master WHERE type = 'table' "
            "AND name = 'sessions_fts_trigram'"
        )
        raw.execute("PRAGMA writable_schema=RESET")
        shadows = [
            r[0] for r in raw.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name LIKE 'sessions_fts_trigram\\_%' ESCAPE '\\'"
            ).fetchall()
        ]
        for sh in shadows:
            raw.execute(f"ALTER TABLE {sh} RENAME TO fts_v22_trash_{sh}")
        hw = raw.execute("SELECT COALESCE(MAX(row_id), 0) FROM sessions").fetchone()[0]
        raw.execute(
            "INSERT INTO state_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("fts_session_trigram_rebuild_high_water", str(hw)),
        )
        raw.execute(
            "INSERT INTO state_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("fts_session_trigram_rebuild_progress", "0"),
        )
        raw.commit()
        raw.close()

        r = SessionDB(db_path=db_path)
        try:
            assert r._sessions_trigram_available is True
            sql = _fts_sql(r._conn, "sessions_fts_trigram")
            assert "tokenize='trigram'" in sql
            # The preserved claim is still pending (not stamped complete).
            assert r.get_meta("fts_session_trigram_rebuild_high_water") is not None
        finally:
            r.close()


# =========================================================================
# Group F — end to end
# =========================================================================


class TestEndToEnd:
    def test_normalized_session_trigram_e2e(self, db):
        """A title/display-name compact search and a raw-ID search both resolve
        the canonical session; an unrelated update leaves it intact."""
        _seed_an94_row(db)
        # A second session whose raw id carries punctuation-bearing interior
        # text (the #16 raw-id contract).
        db.create_session("thr", source="cli")
        db._conn.execute(
            "UPDATE sessions SET id = 'discord:thread-123', "
            "title = 'Weapon Ops' WHERE id = 'thr'"
        )
        db._conn.commit()
        # Compact title/display discovery.
        rows = db._fts_session_trigram_candidates("an94")[1]
        assert [h["id"] for h in rows] == ["an94"]
        # Raw id interior discovery through the raw id field.
        ok, rows = db._fts_session_trigram_candidates("discord:thread-123")
        assert ok is True
        assert "discord:thread-123" in [h["id"] for h in rows]
        # Canonical join fields present.
        rec = next(h for h in rows if h["id"] == "discord:thread-123")
        assert rec["title"] == "Weapon Ops"
        assert rec["row_id"] is not None
        # Unrelated update leaves the trigram document intact.
        db._conn.execute(
            "UPDATE sessions SET message_count = 9 WHERE id = 'discord:thread-123'"
        )
        db._conn.commit()
        ok, rows = db._fts_session_trigram_candidates("discord:thread-123")
        assert ok is True and "discord:thread-123" in [h["id"] for h in rows]
        _assert_trigram_integrity(db)

    def test_canonical_sessions_row_id_unchanged(self, db):
        """The canonical ``sessions.row_id`` identity is untouched by #30."""
        db.create_session("x", source="cli")
        db.create_session("y", source="cli")
        ids = [
            r["id"] if isinstance(r, sqlite3.Row) else r[1]
            for r in db._conn.execute(
                "SELECT row_id, id FROM sessions ORDER BY row_id"
            ).fetchall()
        ]
        assert ids == ["x", "y"]
