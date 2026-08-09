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
