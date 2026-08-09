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

import contextlib
import os
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


def _loadable_extension(path: Path) -> bool:
    """True when ``path`` is a real loadable FTS5 extension on this build."""
    probe = sqlite3.connect(":memory:")
    try:
        probe.enable_load_extension(True)
        probe.load_extension(str(path))
        return True
    except Exception:
        return False
    finally:
        probe.close()


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
    """Provide a loadable cjk_unicode61 tokenizer extension.

    Honors a prebuilt artifact supplied via ``HERMES_FTS5_CJK_SO`` (e.g. a
    Windows ``.dll`` cross-compiled from ``native/fts5_cjk/fts5_cjk.c`` with
    mingw) so capable-host tests run without a local C toolchain — the same
    env override production's ``fts5_cjk_so_path()`` honors. Otherwise builds
    from source (CI/Linux images ship gcc). Skips when neither is available.
    """
    prebuilt = os.environ.get("HERMES_FTS5_CJK_SO")
    if prebuilt:
        p = Path(prebuilt)
        if p.is_file() and _loadable_extension(p):
            return p
        pytest.skip(f"HERMES_FTS5_CJK_SO set but not loadable: {p}")
    if shutil.which("gcc") is None or not SRC.exists():
        pytest.skip("no C toolchain / tokenizer source")
    ext = "dll" if os.name == "nt" else "so"
    out = tmp_path_factory.mktemp("fts5cjk") / f"libfts5_cjk.{ext}"
    try:
        subprocess.run(
            ["gcc", "-shared", "-fPIC", "-O2", f"-I{VENDOR}", str(SRC),
             "-o", str(out)],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as e:
        pytest.skip(f"tokenizer build failed: {e.stderr[:200]}")
    if not _loadable_extension(out):
        pytest.skip("built tokenizer not loadable in this build")
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


def _pre_enable_wal(db_path):
    """Pre-set journal_mode=WAL on disk so SessionDB's vulnerable-runtime
    fallback leaves it in WAL (``_apply_delete_for_wal_reset_bug`` only
    downgrades DBs that are not already WAL). The P1 interleaving test needs
    WAL: a concurrent committed writer must be able to land between a reader's
    guard and MATCH (snapshot isolation), which DELETE-mode locking forbids.
    Harmless for a throwaway temp DB."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    finally:
        conn.close()


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


def _assert_sessions_fts_cjk_internal_integrity(db):
    """Ordinary FTS5 internal integrity-check (shadow-table structure only).
    Unlike the rank=1 form, safe mid-migration when the (P, H] gap rows are
    legitimately unindexed."""
    db._conn.execute(
        "INSERT INTO sessions_fts_cjk(sessions_fts_cjk) "
        "VALUES('integrity-check')"
    )


def _cjk_three_region_db(cjk_so, tmp_path, monkeypatch):
    """SessionDB with a CJK rebuild staged so all three ownership regions are
    represented: rows <= P are backfilled into the CJK index, rows (P,H] are
    the unindexed worker-owned gap, and newly created sessions are > H
    (live-indexed by the triggers)."""
    db_path = tmp_path / "s.db"
    _build_populated_sessions_db(db_path, n=8)  # row 1..8
    d = _open_capable(db_path, cjk_so, monkeypatch)  # CJK H=8, P=0
    d._conn.execute(
        "INSERT INTO sessions_fts_cjk(rowid, title, id, display_name) "
        "SELECT row_id, title, id, display_name FROM sessions "
        "WHERE row_id <= 3"
    )
    d.set_meta(CJK_PROG, "3")
    return d


# =========================================================================
# Group A — external-content raw shape (replaces legacy title-only)
# =========================================================================


# =========================================================================
# Group B2 — trigger ownership regions behavioral matrix (<=P, (P,H], >H)
# =========================================================================


class TestCjkTriggerOwnershipRegions:
    """Behavioral matrix for the CJK trigger ownership regions during a
    pending backfill — acceptance criterion: trigger behavior follows the
    same indexed-region invariant as Unicode (row <= P or > H live; (P,H]
    worker-owned and left alone)."""

    def test_insert_above_high_water_is_live_indexed(
        self, cjk_so, tmp_path, monkeypatch
    ):
        """A session created above the captured H is indexed immediately by
        the insert trigger."""
        d = _cjk_three_region_db(cjk_so, tmp_path, monkeypatch)
        try:
            d.create_session("s9", source="cli")   # row 9 > H
            d.set_session_title("s9", "Live Insert")
            assert _cjk_fts_rowids(d, "live") == [9]
        finally:
            d.close()

    def test_delete_indexed_prefix_row_removes_doc(
        self, cjk_so, tmp_path, monkeypatch
    ):
        """Deleting a <=P row (already indexed) removes its CJK document."""
        d = _cjk_three_region_db(cjk_so, tmp_path, monkeypatch)
        try:
            d.set_session_title("s1", "Alpha One")   # row 1 <= P
            assert _cjk_fts_rowids(d, "alpha") == [1]
            d.delete_session("s1")
            assert _cjk_fts_rowids(d, "alpha") == []
            _assert_sessions_fts_cjk_internal_integrity(d)
        finally:
            d.close()

    def test_delete_gap_row_issues_no_fts_delete(
        self, cjk_so, tmp_path, monkeypatch
    ):
        """Deleting a (P,H] row (never indexed) issues no external-content
        delete; the index stays free of stale postings."""
        d = _cjk_three_region_db(cjk_so, tmp_path, monkeypatch)
        try:
            d.set_session_title("s7", "Gamma Gap")   # row 7 in (P,H]
            assert _cjk_fts_rowids(d, "gamma") == []
            d.delete_session("s7")
            assert _cjk_fts_rowids(d, "gamma") == []
            assert d.get_session("s7") is None
            _assert_sessions_fts_cjk_internal_integrity(d)
        finally:
            d.close()

    def test_delete_live_row_removes_doc(self, cjk_so, tmp_path, monkeypatch):
        """Deleting a >H live row removes its live-indexed document."""
        d = _cjk_three_region_db(cjk_so, tmp_path, monkeypatch)
        try:
            d.create_session("s9", source="cli")
            d.set_session_title("s9", "Live Echo")
            assert _cjk_fts_rowids(d, "echo") == [9]
            d.delete_session("s9")
            assert _cjk_fts_rowids(d, "echo") == []
            _assert_sessions_fts_cjk_internal_integrity(d)
        finally:
            d.close()

    def test_update_indexed_prefix_row_rewrites_doc(
        self, cjk_so, tmp_path, monkeypatch
    ):
        """Updating a <=P row rewrites its already-indexed CJK document."""
        d = _cjk_three_region_db(cjk_so, tmp_path, monkeypatch)
        try:
            d.set_session_title("s1", "Alpha One")
            assert _cjk_fts_rowids(d, "alpha") == [1]
            d.set_session_title("s1", "Alpha Updated")
            assert _cjk_fts_rowids(d, "alpha") == [1]
            assert _cjk_fts_rowids(d, "updated") == [1]
        finally:
            d.close()

    def test_update_gap_row_does_not_rewrite_index(
        self, cjk_so, tmp_path, monkeypatch
    ):
        """Updating a (P,H] row must not rewrite a document that was never
        indexed; the index stays healthy."""
        d = _cjk_three_region_db(cjk_so, tmp_path, monkeypatch)
        try:
            d.set_session_title("s7", "Gamma Gap")
            d.set_session_title("s7", "Gamma Changed")
            assert _cjk_fts_rowids(d, "gamma") == []
            assert _cjk_fts_rowids(d, "changed") == []
            _assert_sessions_fts_cjk_internal_integrity(d)
        finally:
            d.close()

    def test_update_live_row_rewrites_doc(self, cjk_so, tmp_path, monkeypatch):
        """Updating a >H live row rewrites its live-indexed document."""
        d = _cjk_three_region_db(cjk_so, tmp_path, monkeypatch)
        try:
            d.create_session("s9", source="cli")
            d.set_session_title("s9", "Live Delta")
            assert _cjk_fts_rowids(d, "delta") == [9]
            d.set_session_title("s9", "Live Changed")
            assert _cjk_fts_rowids(d, "delta") == []
            assert _cjk_fts_rowids(d, "changed") == [9]
        finally:
            d.close()

    def test_unrelated_column_update_does_not_rewrite_index(
        self, cjk_so, tmp_path, monkeypatch
    ):
        """A narrow UPDATE OF title,id,display_name trigger: an unrelated
        metadata column write must not rewrite the CJK index."""
        d = _cjk_three_region_db(cjk_so, tmp_path, monkeypatch)
        try:
            d.set_session_title("s1", "Alpha One")
            assert _cjk_fts_rowids(d, "alpha") == [1]
            d._conn.execute(
                "UPDATE sessions SET message_count = 5 WHERE row_id = 1"
            )
            d._conn.commit()
            assert _cjk_fts_rowids(d, "alpha") == [1]
        finally:
            d.close()


# =========================================================================
# Group C2 — read-only connection capability seam
# =========================================================================


class TestCjkReadOnly:
    def test_read_only_completed_cjk_index_matches(
        self, cjk_so, tmp_path, monkeypatch
    ):
        """A dedicated SessionDB(read_only=True) with the tokenizer loaded on
        its connection must MATCH the completed CJK index — tokenizer
        capability is connection-local, and the read seam derives serving
        from the durable state in the same snapshot."""
        db_path = tmp_path / "s.db"
        _build_populated_sessions_db(db_path, n=20)
        w = _open_capable(db_path, cjk_so, monkeypatch)
        while w.fts_session_cjk_rebuild_step():
            pass
        w.create_session("日本語ID", source="cli")
        w.set_session_title("日本語ID", "東京タワー計画")
        w.close()
        monkeypatch.setenv("HERMES_FTS5_CJK_SO", str(cjk_so))
        ro = SessionDB(db_path=db_path, read_only=True)
        try:
            servable, candidates = ro._fts_cjk_metadata_candidates("東京")
            assert servable is True
            assert any(c["id"] == "日本語ID" for c in candidates)
        finally:
            ro.close()

    def test_read_only_without_tokenizer_falls_back(
        self, cjk_so, tmp_path, monkeypatch
    ):
        """A tokenizer-less read-only consumer cannot MATCH the CJK index: it
        reports unservable (canonical fallback), never a partial result."""
        db_path = tmp_path / "s.db"
        _build_populated_sessions_db(db_path, n=20)
        w = _open_capable(db_path, cjk_so, monkeypatch)
        while w.fts_session_cjk_rebuild_step():
            pass
        w.close()
        monkeypatch.setenv("HERMES_FTS5_CJK_SO", str(tmp_path / "absent.so"))
        ro = SessionDB(db_path=db_path, read_only=True)
        try:
            servable, candidates = ro._fts_cjk_metadata_candidates("東京")
            assert servable is False
            assert candidates is None
        finally:
            ro.close()


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

    def test_finish_hook_keeps_search_unavailable_when_stale(
        self, cjk_so, tmp_path, monkeypatch
    ):
        """A stale breadcrumb persisted by an incapable host mid-rebuild (it
        dropped the CJK triggers, leaving a gap of unknown extent) must keep
        search-serving OFF even after finish clears the H/P markers — an index
        with an unknown gap is never served in-process (#77629/#26)."""
        db_path = tmp_path / "s.db"
        _build_populated_sessions_db(db_path, n=50)
        d = _open_capable(db_path, cjk_so, monkeypatch)
        try:
            # Simulate an incapable host persisting the stale breadcrumb
            # while the rebuild is in flight.
            d.set_meta(CJK_STALE, "1")
            while d.fts_session_cjk_rebuild_step():
                pass
            # Finish ran (markers cleared) but search must stay unavailable.
            assert d.get_meta(CJK_HW) is None
            assert d.get_meta(CJK_PROG) is None
            assert d.get_meta(CJK_STALE) == "1"
            assert d._sessions_cjk_available is False
        finally:
            d.close()

    def test_schema_soft_fail_keeps_worker_operable_false(
        self, cjk_so, tmp_path, monkeypatch
    ):
        """A soft-failed CJK schema transition must not leave worker-operable
        True — otherwise optimize's phase 1d would retry the missing/unusable
        table forever (fts_rebuild_step treats OperationalError as transient).
        worker-operable means 'can operate the target NOW', not just '.so
        loaded once'."""
        db_path = tmp_path / "s.db"
        _build_populated_sessions_db(db_path, n=20)
        monkeypatch.setattr(
            SessionDB,
            "_fts_session_cjk_schema_transition",
            lambda self, cursor: False,
        )
        d = _open_capable(db_path, cjk_so, monkeypatch)
        try:
            assert d._sessions_cjk_worker_operable is False
            assert d._sessions_cjk_available is False
            # The worker refuses to advance (no infinite retry loop).
            assert d.fts_session_cjk_rebuild_step() is False
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
        # Opening a populated DB stages CJK H=20, P=0. Force a partial
        # in-flight progress so the degradation path is exercised
        # deterministically (a single step could otherwise complete the small
        # index in one chunk and clear the markers).
        w.set_meta(CJK_PROG, "5")
        hw = w.get_meta(CJK_HW)
        prog = w.get_meta(CJK_PROG)
        assert hw == "20" and prog == "5"
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
            # A pending CJK backfill must not serve the CJK lane.
            servable, candidates = d._fts_cjk_metadata_candidates("標題")
            assert servable is False
            assert candidates is None
            # But canonical fallback (the Unicode lane) still finds the same
            # session through its ASCII token while CJK is pending.
            d.create_session("s51", source="cli")
            d.set_session_title("s51", "CJK 標題 Plan")
            _, uni = d._fts_metadata_candidates("plan")
            assert any(c["id"] == "s51" for c in uni)
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

    def test_cross_process_stale_keeps_serving_off(
        self, cjk_so, tmp_path, monkeypatch
    ):
        """A long-lived capable process whose cache says the CJK index is
        servable must still stop serving when a tokenizer-less sibling
        persists the durable stale breadcrumb. The durable state — read in
        the same snapshot as the MATCH — is the source of truth, never the
        process-local flag."""
        db_path = tmp_path / "s.db"
        _build_populated_sessions_db(db_path, n=20)
        d = _open_capable(db_path, cjk_so, monkeypatch)
        try:
            while d.fts_session_cjk_rebuild_step():
                pass
            d.create_session("日本語ID", source="cli")
            d.set_session_title("日本語ID", "東京タワー計画")
            # In-memory cache says serving...
            assert d._sessions_cjk_available is True
            servable, candidates = d._fts_cjk_metadata_candidates("東京")
            assert servable is True
            # ...but a sibling process persisted the durable stale breadcrumb.
            d.set_meta(CJK_STALE, "1")
            servable, candidates = d._fts_cjk_metadata_candidates("東京")
            assert servable is False
            assert candidates is None
        finally:
            d.close()

    def test_stale_write_between_guard_and_match_stays_consistent(
        self, cjk_so, tmp_path, monkeypatch
    ):
        """P1 (PR #65): the durable guard and the MATCH must share ONE SQLite
        snapshot. This manufactures the exact interleaving the above test
        cannot: it wraps the connection ``_read_ctx`` yields in a proxy that
        commits a concurrent sibling's write (mark stale + drop the CJK
        triggers + add AND directly index a new canonical session) on a
        SEPARATE connection right AFTER the guard's first read completes but
        BEFORE the MATCH runs.

        Pre-fix (autocommit: a fresh snapshot per SELECT) the MATCH opens a
        new snapshot that already contains the sibling's new index row → the
        lane reports ``(servable=True, ...)`` over a stale-guarded, partially
        rebuilt index. Post-fix the MATCH stays on the guard's snapshot → it
        returns the consistent pre-write rows and never serves a row that
        belongs to a later, stale-guarded snapshot.
        """
        db_path = tmp_path / "s.db"
        _build_populated_sessions_db(db_path, n=20)
        _pre_enable_wal(db_path)  # WAL: concurrent committed writer required
        d = _open_capable(db_path, cjk_so, monkeypatch)
        try:
            while d.fts_session_cjk_rebuild_step():
                pass
            d.create_session("日本語ID", source="cli")
            d.set_session_title("日本語ID", "東京タワー計画")
            assert d._sessions_cjk_available is True

            def commit_sibling():
                # The sibling's mid-flight write, committed on a SEPARATE
                # connection while our read transaction stays open: mark
                # stale, drop the CJK triggers, add a new canonical session
                # AND index it directly — so the CJK index now differs from
                # the guard's snapshot.
                b = sqlite3.connect(str(db_path), isolation_level=None)
                b.enable_load_extension(True)
                b.load_extension(str(cjk_so))
                try:
                    b.execute("BEGIN IMMEDIATE")
                    b.execute(
                        "INSERT INTO state_meta (key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (CJK_STALE, "1"),
                    )
                    b.execute("DROP TRIGGER IF EXISTS sessions_fts_cjk_insert")
                    b.execute("DROP TRIGGER IF EXISTS sessions_fts_cjk_delete")
                    b.execute("DROP TRIGGER IF EXISTS sessions_fts_cjk_update")
                    b.execute(
                        "INSERT INTO sessions (row_id, id, source, "
                        "started_at, title) VALUES (?, ?, 'cli', ?, ?)",
                        (1000, "sibling_new", 1.0, "東京ライバル"),
                    )
                    b.execute(
                        "INSERT INTO sessions_fts_cjk "
                        "(rowid, title, id, display_name) "
                        "VALUES (?, ?, ?, NULL)",
                        (1000, "東京ライバル", "sibling_new"),
                    )
                    b.execute("COMMIT")
                finally:
                    b.close()

            real_read_ctx = SessionDB._read_ctx

            class _RacyReadConn:
                """Delegates to the real read connection, but after the FIRST
                durable guard SELECT completes it commits the sibling's
                concurrent write — manufacturing the P1 interleaving between
                the guard and the MATCH that a plain sequential test cannot.
                (``sqlite3.Connection.execute`` is a C-level slot and cannot
                be monkeypatched directly.)"""

                def __init__(self, conn, on_guard_read):
                    self._conn = conn
                    self._on_guard_read = on_guard_read
                    self._fired = False

                @property
                def in_transaction(self):
                    return self._conn.in_transaction

                def execute(self, sql, *args, **kwargs):
                    cur = self._conn.execute(sql, *args, **kwargs)
                    if (
                        not self._fired
                        and isinstance(sql, str)
                        and "state_meta" in sql
                        and sql.lstrip().upper().startswith("SELECT")
                    ):
                        # First guard read done — its snapshot (no stale) is
                        # established. Now let the sibling's write land.
                        self._fired = True
                        self._on_guard_read()
                    return cur

            @contextlib.contextmanager
            def racy_read_ctx(self):
                with real_read_ctx(self) as conn:
                    yield _RacyReadConn(conn, commit_sibling)

            monkeypatch.setattr(SessionDB, "_read_ctx", racy_read_ctx)
            servable, candidates = d._fts_cjk_metadata_candidates("東京")
            # Post-fix: consistent pre-write snapshot — the pre-existing row
            # is found and the sibling's post-snapshot row is NOT served as
            # if current (it lives on a later, stale-guarded snapshot the
            # guard never saw).
            assert servable is True
            assert any(c["id"] == "日本語ID" for c in candidates)
            assert all(c["id"] != "sibling_new" for c in candidates)
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
