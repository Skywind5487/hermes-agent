"""Tests for the #26 session-metadata CJK FTS lifecycle.

CJK session metadata is an OPTIONAL specialization of #25's Unicode
external-content architecture: the same stable ``sessions.row_id`` identity,
the same external-content/resumable H/P rebuild model, and the same generic
chunk/finish engine — while keeping tokenizer capability (worker-operable)
separate from search-serving availability, using independent CJK-session
durable markers, and degrading safely when the ``cjk_unicode61`` tokenizer is
unavailable.

Builds the loadable tokenizer from ``native/fts5_cjk/fts5_cjk.c`` on the fly
(CI/Linux images ship gcc); the capable-host tests skip when no C toolchain
is present. The degraded/unavailable-host tests that need no tokenizer always
run.
"""

import sqlite3
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from hermes_state import SCHEMA_SQL, SessionDB

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "native" / "fts5_cjk" / "fts5_cjk.c"
VENDOR = REPO / "native" / "fts5_cjk" / "vendor"


def _build_populated_sessions_db(db_path, n=1200):
    """Build a DB with ``n`` sessions (explicit row_ids 1..n) and no FTS
    surfaces, so the open stages a full H/P claim over an empty index (the
    real #25/#26 migration shape). Mirrors the #25 module helper."""
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


def _fts_sql(conn, table):
    """The stored CREATE statement for ``table`` from sqlite_master."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return (row[0] if isinstance(row, sqlite3.Row) else row[0]) if row else ""

# Durable CJK-session marker keys (independent from both the Unicode-session
# pair and the message-CJK pair).
CJK_HW = "fts_session_cjk_rebuild_high_water"
CJK_PROG = "fts_session_cjk_rebuild_progress"
CJK_STALE = "fts_session_cjk_stale"
UNI_HW = "fts_session_rebuild_high_water"


@pytest.fixture()
def db(tmp_path):
    """Fresh SessionDB with no CJK tokenizer (plain FTS5) — used by the
    tokenizer-independent fallback tests."""
    d = SessionDB(db_path=tmp_path / "state.db")
    yield d
    d.close()


@pytest.fixture(scope="session")
def cjk_so(tmp_path_factory):
    """Build the cjk_unicode61 loadable tokenizer from source; skip when no C
    toolchain / extension loading is available (mirrors test_fts_cjk_bigram)."""
    if shutil.which("gcc") is None or not SRC.exists():
        pytest.skip("no C toolchain / tokenizer source")
    out = tmp_path_factory.mktemp("fts5cjk") / "libfts5_cjk.so"
    try:
        subprocess.run(
            ["gcc", "-shared", "-fPIC", "-O2", f"-I{VENDOR}", str(SRC),
             "-o", str(out)],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as e:
        pytest.skip(f"tokenizer build failed: {e.stderr[:200]}")
    probe = sqlite3.connect(":memory:")
    try:
        probe.enable_load_extension(True)
        probe.load_extension(str(out))
    except Exception as e:
        pytest.skip(f"extension loading unavailable: {e}")
    finally:
        probe.close()
    return out


@pytest.fixture()
def cjk_db(cjk_so, tmp_path, monkeypatch):
    """Fresh SessionDB on a tokenizer-capable host (empty DB)."""
    monkeypatch.setenv("HERMES_FTS5_CJK_SO", str(cjk_so))
    d = SessionDB(db_path=tmp_path / "state.db")
    yield d
    d.close()


# The pre-#26 sessions_fts_cjk shape: internal-content, title-only, one-shot,
# three broad triggers (mirrors the pinned SESSIONS_FTS_CJK_SQL).
_LEGACY_SESSIONS_FTS_CJK_DDL = """
CREATE VIRTUAL TABLE sessions_fts_cjk USING fts5(
    title,
    tokenize='cjk_unicode61'
);

CREATE TRIGGER sessions_fts_cjk_insert AFTER INSERT ON sessions BEGIN
    INSERT INTO sessions_fts_cjk(rowid, title) VALUES (new.rowid, new.title);
END;

CREATE TRIGGER sessions_fts_cjk_delete AFTER DELETE ON sessions BEGIN
    DELETE FROM sessions_fts_cjk WHERE rowid = old.rowid;
END;

CREATE TRIGGER sessions_fts_cjk_update AFTER UPDATE ON sessions BEGIN
    DELETE FROM sessions_fts_cjk WHERE rowid = old.rowid;
    INSERT INTO sessions_fts_cjk(rowid, title) VALUES (new.rowid, new.title);
END;
"""


def _build_legacy_cjk_db(db_path, cjk_so, n=10):
    """Build a pre-#26 DB: modern sessions (named row_id) with a legacy
    internal-content title-only ``sessions_fts_cjk`` + broad triggers. The
    tokenizer must be loaded on the build connection to create the legacy
    table (tokenize='cjk_unicode61')."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(SCHEMA_SQL)
    conn.enable_load_extension(True)
    conn.load_extension(str(cjk_so))
    t0 = time.time()
    conn.executemany(
        "INSERT INTO sessions (row_id, id, source, started_at, title) "
        "VALUES (?, ?, 'cli', ?, ?)",
        [(i, f"s{i}", t0 + i, f"標題 {i}") for i in range(1, n + 1)],
    )
    conn.executescript(_LEGACY_SESSIONS_FTS_CJK_DDL)
    conn.commit()
    conn.close()


def _open_capable(db_path, cjk_so, monkeypatch):
    """Open SessionDB with the tokenizer available."""
    monkeypatch.setenv("HERMES_FTS5_CJK_SO", str(cjk_so))
    return SessionDB(db_path=db_path)


def _open_incapable(db_path, tmp_path, monkeypatch):
    """Open SessionDB with the tokenizer unavailable (absent .so)."""
    monkeypatch.setenv("HERMES_FTS5_CJK_SO", str(tmp_path / "absent.so"))
    return SessionDB(db_path=db_path)


def _cjk_fts_rowids(db, query):
    """CJK-session FTS rowids (sessions.row_id) that MATCH, read DIRECTLY
    from the index — no canonical-sessions JOIN (delete-test probe)."""
    with db._read_ctx() as conn:
        rows = conn.execute(
            "SELECT rowid FROM sessions_fts_cjk WHERE sessions_fts_cjk MATCH ?",
            (query,),
        ).fetchall()
    return [r["rowid"] for r in rows]


def _cjk_match_ids(db, query):
    """Session ids matched through the completed CJK metadata lane."""
    servable, candidates = db._fts_cjk_metadata_candidates(query)
    assert servable is True, "CJK lane should be servable here"
    return [c["id"] for c in candidates]


def _assert_sessions_fts_cjk_integrity(db):
    """rank=1 integrity check: cross-checks the CJK external index against
    the canonical sessions content table (catches orphan/stale postings)."""
    db._conn.execute(
        "INSERT INTO sessions_fts_cjk(sessions_fts_cjk, rank) "
        "VALUES('integrity-check', 1)"
    )


# =========================================================================
# Group A — external-content raw shape (replaces legacy title-only)
# =========================================================================


class TestCjkExternalContentShape:
    def test_sessions_fts_cjk_ddl_is_external_content_raw_metadata(self, cjk_db):
        """sessions_fts_cjk is external-content over raw (title, id,
        display_name) keyed by named ``row_id`` — NOT the legacy title-only
        internal shape."""
        sql = _fts_sql(cjk_db._conn, "sessions_fts_cjk")
        assert "content='sessions'" in sql
        assert "content_rowid='row_id'" in sql
        assert "tokenize='cjk_unicode61'" in sql
        for col in ("title", "id", "display_name"):
            assert col in sql

    def test_update_trigger_is_narrow(self, cjk_db):
        """The CJK update trigger is AFTER UPDATE OF title,id,display_name
        with a value-change guard — unrelated metadata writes do not rewrite
        the CJK index."""
        trig = cjk_db._conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'trigger' AND name = 'sessions_fts_cjk_update'"
        ).fetchone()
        sql = trig[0] if not isinstance(trig, sqlite3.Row) else trig["sql"]
        compact = " ".join(sql.split())
        assert "AFTER UPDATE OF title, id, display_name" in compact

    def test_legacy_internal_cjk_table_replaced_on_capable_open(
        self, cjk_so, tmp_path, monkeypatch
    ):
        """A pre-#26 internal title-only sessions_fts_cjk is replaced by the
        external-content shape on a tokenizer-capable open; the legacy shadow
        tables are gone."""
        db_path = tmp_path / "legacy.db"
        _build_legacy_cjk_db(db_path, cjk_so, n=10)
        d = _open_capable(db_path, cjk_so, monkeypatch)
        try:
            sql = _fts_sql(d._conn, "sessions_fts_cjk")
            assert "content='sessions'" in sql
            assert "content_rowid='row_id'" in sql
            # Legacy broad triggers are gone; gated external triggers exist.
            live = {
                r[0] for r in d._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger' "
                    "AND name LIKE 'sessions_fts_cjk_%'"
                ).fetchall()
            }
            assert {"sessions_fts_cjk_insert", "sessions_fts_cjk_delete",
                    "sessions_fts_cjk_update"} <= live
        finally:
            d.close()


# =========================================================================
# Group B — independent CJK-session H/P + worker-progress deadlock pin
# =========================================================================


class TestCjkRebuildMarkers:
    def test_populated_db_stages_cjk_session_markers(self, cjk_so, tmp_path, monkeypatch):
        """A populated DB opened on a capable host stages CJK-session-owned
        H/P markers — independent of the Unicode session pair (the two indexes
        may sit at different completion points)."""
        db_path = tmp_path / "s.db"
        _build_populated_sessions_db(db_path, n=50)
        d = _open_capable(db_path, cjk_so, monkeypatch)
        try:
            assert d.get_meta(CJK_HW) is not None
            assert d.get_meta(CJK_PROG) == "0"
            # Independent markers: seeding CJK must never borrow the Unicode
            # claim (and vice versa).
            assert d.get_meta(UNI_HW) is not None  # Unicode also staged
            # The CJK and Unicode claims are separate keys.
            assert CJK_HW != UNI_HW
        finally:
            d.close()

    def test_empty_db_cjk_complete_no_markers(self, cjk_db):
        """An empty DB's CJK index is complete by construction — no claim, and
        search-serving is available immediately."""
        assert cjk_db.get_meta(CJK_HW) is None
        assert cjk_db._sessions_cjk_available is True

    def test_cjk_pending_worker_progresses_while_search_unavailable(
        self, cjk_so, tmp_path, monkeypatch
    ):
        """THE deadlock regression: a pending CJK backfill has worker-operable
        = true and search-serving = false; the worker must still advance, run
        finish, clear the markers, and only then become search-serving."""
        db_path = tmp_path / "s.db"
        _build_populated_sessions_db(db_path, n=50)
        d = _open_capable(db_path, cjk_so, monkeypatch)
        try:
            assert d._sessions_cjk_worker_operable is True
            assert d._sessions_cjk_available is False
            assert d.fts_session_cjk_rebuild_status() is not None

            steps = 0
            while d.fts_session_cjk_rebuild_step() and steps < 200:
                steps += 1
            assert d.get_meta(CJK_HW) is None
            assert d.get_meta(CJK_PROG) is None
            assert d._sessions_cjk_available is True
        finally:
            d.close()

    def test_cjk_markers_survive_reopen_no_reseed(self, cjk_so, tmp_path, monkeypatch):
        """A partial CJK rebuild is not reseeded to zero on reopen — the same
        H/P resume as the Unicode lifecycle."""
        db_path = tmp_path / "s.db"
        _build_populated_sessions_db(db_path, n=50)
        w = _open_capable(db_path, cjk_so, monkeypatch)
        w.fts_session_cjk_rebuild_step()  # advance a bit
        hw = w.get_meta(CJK_HW)
        prog = w.get_meta(CJK_PROG)
        w.close()

        r = _open_capable(db_path, cjk_so, monkeypatch)
        try:
            assert r.get_meta(CJK_HW) == hw
            assert r.get_meta(CJK_PROG) == prog
        finally:
            r.close()

    def test_cjk_finish_sets_search_serving_after_boundary_sweep(
        self, cjk_so, tmp_path, monkeypatch
    ):
        """Finish runs the boundary sweep under the SAME operability gate as
        step, then clears CJK H/P and flips search-serving on."""
        db_path = tmp_path / "s.db"
        _build_populated_sessions_db(db_path, n=50)
        d = _open_capable(db_path, cjk_so, monkeypatch)
        try:
            # Backfill everything by hand, then delete one doc to simulate a
            # write that slipped at the boundary; a single step (P >= H) must
            # run the finish sweep and restore the missing doc.
            d._conn.execute(
                "INSERT INTO sessions_fts_cjk(rowid, title, id, display_name) "
                "SELECT row_id, title, id, display_name FROM sessions "
                "WHERE row_id <= 50"
            )
            d._conn.execute(
                "UPDATE state_meta SET value = '50' WHERE key = ?", (CJK_PROG,)
            )
            d._conn.execute(
                "INSERT INTO sessions_fts_cjk(sessions_fts_cjk, rowid, title, id, display_name) "
                "SELECT 'delete', s.row_id, s.title, s.id, s.display_name "
                "FROM sessions s WHERE s.id = 's25'"
            )
            d._conn.commit()
            assert 25 not in _cjk_fts_rowids(d, "標題")
            assert d.fts_session_cjk_rebuild_step() is False
            assert d.get_meta(CJK_HW) is None
            assert d._sessions_cjk_available is True
        finally:
            d.close()

    def test_stale_capable_restart_resets_and_rebuilds_from_fresh_highwater(
        self, cjk_so, tmp_path, monkeypatch
    ):
        """A tokenizer-less host dropping the CJK triggers marks the index
        stale. A later capable host must reset to a known-empty surface and
        reseed a fresh CJK H/P from the CURRENT MAX(row_id), then rebuild —
        never reuse the old partial claim."""
        db_path = tmp_path / "s.db"
        _build_populated_sessions_db(db_path, n=20)
        w = _open_capable(db_path, cjk_so, monkeypatch)
        # Stage a partial CJK claim.
        w.set_meta(CJK_HW, "20")
        w.set_meta(CJK_PROG, "5")
        w.close()

        # Incapable open: drops triggers, leaves durable state, marks stale.
        r = _open_incapable(db_path, tmp_path, monkeypatch)
        assert r.get_meta(CJK_HW) == "20"
        assert r.get_meta(CJK_STALE) == "1"
        r.close()

        # New sessions are added while the index is unusable.
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "INSERT INTO sessions (id, source, started_at) "
                "VALUES ('s21', 'cli', ?)", (time.time(),)
            )
            conn.commit()

        # Capable reopen + optimize: reset + reseed from MAX(row_id)=21 and
        # rebuild to completion -> search-serving.
        d = _open_capable(db_path, cjk_so, monkeypatch)
        try:
            assert d.get_meta(CJK_STALE) is not None
            result = d.optimize_fts_storage(vacuum=False)
            assert result["ok"] is True, result
            assert d.get_meta(CJK_HW) is None
            assert d.get_meta(CJK_STALE) is None
            assert d._sessions_cjk_available is True
        finally:
            d.close()


# =========================================================================
# Group C — tokenizer-unavailable degradation
# =========================================================================


class TestCjkDegradation:
    def test_incapable_host_preserves_pending_and_keeps_unicode_healthy(
        self, cjk_so, tmp_path, monkeypatch
    ):
        """Reopening a DB with pending CJK work on a tokenizer-less host keeps
        the durable pending state (missing capability is never evidence of
        completion), leaves Unicode/session writes and search healthy, and
        marks the index stale for a later capable rebuild."""
        db_path = tmp_path / "s.db"
        _build_populated_sessions_db(db_path, n=20)
        w = _open_capable(db_path, cjk_so, monkeypatch)
        w.fts_session_cjk_rebuild_step()  # partial progress
        hw = w.get_meta(CJK_HW)
        prog = w.get_meta(CJK_PROG)
        assert hw is not None
        w.close()

        r = _open_incapable(db_path, tmp_path, monkeypatch)
        try:
            assert r._sessions_cjk_worker_operable is False
            assert r._sessions_cjk_available is False
            # Pending durable state untouched — no false completion.
            assert r.get_meta(CJK_HW) == hw
            assert r.get_meta(CJK_PROG) == prog
            # Canonical session writes still work.
            r.create_session("S", source="cli")
            r.set_session_title("S", "Unicode Session New")
            # Unicode metadata search still works.
            _, cands = r._fts_metadata_candidates("unicode")
            assert any(c["id"] == "S" for c in cands)
            # The index is marked stale so a capable host resets/rebuilds.
            assert r.get_meta(CJK_STALE) == "1"
        finally:
            r.close()

    def test_incapable_host_fresh_empty_db_is_healthy(self, tmp_path, monkeypatch):
        """A tokenizer-less host on a fresh DB: no CJK table created, Unicode
        sessions work normally, no stale breadcrumb fabricated."""
        monkeypatch.setenv("HERMES_FTS5_CJK_SO", str(tmp_path / "absent.so"))
        d = SessionDB(db_path=tmp_path / "fresh.db")
        try:
            assert d._sessions_cjk_worker_operable is False
            assert d._sessions_cjk_available is False
            assert d.get_meta(CJK_STALE) is None
            d.create_session("s1", source="cli")
            d.set_session_title("s1", "Hello")
            assert d.get_session_by_title("Hello")["id"] == "s1"
        finally:
            d.close()


# =========================================================================
# Group D — search seam: pending/unavailable/1-char fallback vs zero matches
# =========================================================================


class TestCjkSearchSeam:
    def test_lone_cjk_char_is_fallback_only(self, db):
        """A one-character CJK query is classified as fallback-only — the
        bigram index's useful lower bound is two CJK characters. Does not
        require the tokenizer: the fallback decision is made before touching
        the index."""
        servable, candidates = db._fts_cjk_metadata_candidates("中")
        assert servable is False
        assert candidates is None

    def test_pending_cjk_not_served(self, cjk_so, tmp_path, monkeypatch):
        """A pending CJK backfill must not serve search: the seam reports
        unservable (canonical fallback), never a partial index."""
        db_path = tmp_path / "s.db"
        _build_populated_sessions_db(db_path, n=50)
        d = _open_capable(db_path, cjk_so, monkeypatch)
        try:
            assert d._sessions_cjk_available is False
            servable, candidates = d._fts_cjk_metadata_candidates("標題")
            assert servable is False
            assert candidates is None
            # But canonical fallback still finds the row.
            _, uni = d._fts_metadata_candidates("標題")
            assert any(c["id"] == "s1" for c in uni)
        finally:
            d.close()

    def test_unavailable_tokenizer_not_served(self, tmp_path, monkeypatch):
        """No tokenizer: the seam reports unservable and routing falls back."""
        monkeypatch.setenv("HERMES_FTS5_CJK_SO", str(tmp_path / "absent.so"))
        d = SessionDB(db_path=tmp_path / "s.db")
        try:
            d.create_session("s1", source="cli")
            d.set_session_title("s1", "日本語セッション")
            servable, candidates = d._fts_cjk_metadata_candidates("日本語")
            assert servable is False
            assert candidates is None
        finally:
            d.close()

    def test_completed_cjk_index_finds_title_id_display_name(
        self, cjk_so, tmp_path, monkeypatch
    ):
        """A completed CJK index finds representative 2+ char CJK in title,
        logical session id, AND display_name."""
        db_path = tmp_path / "s.db"
        _build_populated_sessions_db(db_path, n=20)
        d = _open_capable(db_path, cjk_so, monkeypatch)
        try:
            while d.fts_session_cjk_rebuild_step():
                pass
            assert d._sessions_cjk_available is True
            d.create_session("日本語ID", source="cli")
            d.set_session_title("日本語ID", "東京タワー計画")
            d._conn.execute(
                "UPDATE sessions SET display_name = 'カタカナ表示' "
                "WHERE id = '日本語ID'"
            )
            d._conn.commit()
            assert _cjk_match_ids(d, "東京") == ["日本語ID"]      # title
            assert _cjk_match_ids(d, "日本語") == ["日本語ID"]    # logical id
            assert _cjk_match_ids(d, "カタカナ") == ["日本語ID"]  # display_name
        finally:
            d.close()

    def test_cjk_valid_zero_match_distinct_from_capability_failure(
        self, cjk_so, tmp_path, monkeypatch
    ):
        """A servable CJK query with no matches returns (True, []) — distinct
        from (False, None) capability/pending failure so #14 can route."""
        db_path = tmp_path / "s.db"
        _build_populated_sessions_db(db_path, n=20)
        d = _open_capable(db_path, cjk_so, monkeypatch)
        try:
            while d.fts_session_cjk_rebuild_step():
                pass
            servable, candidates = d._fts_cjk_metadata_candidates("不存在詞彙")
            assert servable is True
            assert candidates == []
        finally:
            d.close()

    def test_completed_cjk_index_integrity_after_deletes(
        self, cjk_so, tmp_path, monkeypatch
    ):
        """After a completed CJK backfill, deleting a live row leaves no
        orphan posting (rank=1 external-content consistency)."""
        db_path = tmp_path / "s.db"
        _build_populated_sessions_db(db_path, n=20)
        d = _open_capable(db_path, cjk_so, monkeypatch)
        try:
            while d.fts_session_cjk_rebuild_step():
                pass
            d.set_session_title("s1", "東京タワー")
            d.delete_session("s1")
            assert 1 not in _cjk_fts_rowids(d, "東京")
            _assert_sessions_fts_cjk_integrity(d)
        finally:
            d.close()
