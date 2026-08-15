"""Tests for #14: routed session-metadata picker candidates.

Covers the #37 routing contract implemented on top of the accepted #12
three-index substrate:

- the query classifier (Unicode token lane / CJK+Unicode union / normalized
  trigram lane / direct canonical-LIKE fallback);
- the metadata candidate router (whole-store ``row_id`` candidates inside one
  read snapshot, zero-result and route-failure fallback running LIKE exactly
  once);
- #16 field semantics through each routed lane (title, logical id,
  display_name; compact ``AN-94`` -> ``an94``; interior fragments; raw
  punctuated IDs);
- the canonical LIKE fallback contract (literal ``%`` ``_`` ``\\``, the
  canonical ``- _ . space`` compact policy for BOTH title and display_name,
  stable ``row_id``-only projection).

Whole-store lineage/eligibility/pagination behavior lives in
``TestWholeStoreListingSearch`` (added with the listing integration).
"""

import pytest

from hermes_state import SessionDB
from hermes_state_common import MAX_FTS5_QUERY_CHARS


@pytest.fixture()
def db(tmp_path):
    """Fresh SessionDB over a temp database file."""
    session_db = SessionDB(db_path=tmp_path / "state.db")
    yield session_db
    session_db.close()


def _seed_an94(db):
    """The canonical #30 sample metadata row (live, > H on a fresh DB)."""
    db.create_session("an94", source="cli")
    db._conn.execute(
        "UPDATE sessions SET title = 'AN-94 Prestige.Barrel', "
        "display_name = 'Acme / #an-94-ops' WHERE id = 'an94'"
    )
    db._conn.commit()


def _row_ids_to_ids(db, row_ids):
    if not row_ids:
        return set()
    with db._read_ctx() as conn:
        rows = conn.execute(
            "SELECT id FROM sessions WHERE row_id IN (%s)"
            % ",".join("?" for _ in row_ids),
            tuple(row_ids),
        ).fetchall()
        return {r["id"] for r in rows}


class TestClassifyMetadataQuery:
    """Every classifier row of the #37 routing table."""

    def test_empty_is_no_search(self, db):
        assert db._classify_metadata_query("") == "none"
        assert db._classify_metadata_query("   ") == "none"

    def test_lone_cjk_run_is_direct_like(self, db):
        assert db._classify_metadata_query("中") == "like"
        assert db._classify_metadata_query("a中b") == "like"

    def test_cjk_2plus_is_cjk_route(self, db):
        assert db._classify_metadata_query("中文") == "cjk"
        assert db._classify_metadata_query("中文 測試") == "cjk"
        assert db._classify_metadata_query("ab中文") == "cjk"

    def test_explicit_token_syntax_is_unicode(self, db):
        assert db._classify_metadata_query('"alpha project"') == "unicode"
        assert db._classify_metadata_query("alpha AND beta") == "unicode"
        assert db._classify_metadata_query("alpha OR beta") == "unicode"
        assert db._classify_metadata_query("NOT alpha") == "unicode"
        assert db._classify_metadata_query("alph*") == "unicode"

    def test_plain_literal_is_trigram(self, db):
        assert db._classify_metadata_query("winton") == "trigram"
        assert db._classify_metadata_query("an-94") == "trigram"
        assert db._classify_metadata_query("stige Bar") == "trigram"

    def test_short_literal_is_direct_like(self, db):
        assert db._classify_metadata_query("ab") == "like"
        assert db._classify_metadata_query("a") == "like"

    def test_compact_shorter_than_3_is_direct_like(self, db):
        # Raw needle >= 3 but compact needle < 3 -> direct LIKE.
        assert db._classify_metadata_query("---ab") == "like"

    def test_only_separators_is_direct_like(self, db):
        assert db._classify_metadata_query("---") == "like"
        assert db._classify_metadata_query("...") == "like"


class TestRouteAndFallbackContract:
    """The router normalizes lane outcomes into hits / zero / direct-LIKE.

    A successful non-empty routed result must never run the LIKE fallback; a
    valid zero or route failure must run it exactly once.
    """

    def _route(self, db, q):
        return db._metadata_candidate_row_ids(q)

    def _route_counting_like(self, db, monkeypatch, q):
        calls = []
        orig = SessionDB._metadata_like_fallback_row_ids

        def spy(self_, needle, *, conn=None, limit=None):
            calls.append(needle)
            return orig(self_, needle, conn=conn, limit=limit)

        monkeypatch.setattr(SessionDB, "_metadata_like_fallback_row_ids", spy)
        result = db._metadata_candidate_row_ids(q)
        return result, calls

    def test_trigram_hits_do_not_run_like(self, db, monkeypatch):
        _seed_an94(db)
        result, calls = self._route_counting_like(db, monkeypatch, "an94")
        assert result.path == "trigram"
        assert result.status == "hits"
        assert calls == []

    def test_trigram_zero_runs_like_exactly_once(self, db, monkeypatch):
        _seed_an94(db)
        result, calls = self._route_counting_like(db, monkeypatch, "zzzzzz")
        assert result.path == "like"
        assert calls == ["zzzzzz"]

    def test_trigram_unavailable_runs_like_exactly_once(self, db, monkeypatch):
        _seed_an94(db)
        db._sessions_trigram_available = False
        result, calls = self._route_counting_like(db, monkeypatch, "an94")
        assert result.path == "like"
        assert result.status == "hits"  # found by the canonical LIKE fallback
        assert calls == ["an94"]

    def test_unicode_hits_do_not_run_like(self, db, monkeypatch):
        _seed_an94(db)
        result, calls = self._route_counting_like(db, monkeypatch, '"acme"')
        assert result.path == "unicode"
        assert result.status == "hits"
        assert calls == []

    def test_unicode_zero_runs_like_once(self, db, monkeypatch):
        _seed_an94(db)
        result, calls = self._route_counting_like(db, monkeypatch, '"zzzz"')
        assert result.path == "like"
        assert calls == ['"zzzz"']

    def test_direct_like_lone_cjk(self, db, monkeypatch):
        _seed_an94(db)
        result, calls = self._route_counting_like(db, monkeypatch, "中")
        assert result.path == "like"
        assert calls == ["中"]

    def test_direct_like_short_literal(self, db, monkeypatch):
        result, calls = self._route_counting_like(db, monkeypatch, "ab")
        assert result.path == "like"
        assert calls == ["ab"]

    def test_cjk_unservable_falls_back_to_like(self, db, monkeypatch):
        _seed_an94(db)
        result, calls = self._route_counting_like(db, monkeypatch, "中文")
        # On a CJK-capable host the CJK+Unicode union serves; on this host the
        # CJK lane is unservable so the route group must fall back to LIKE.
        assert len(calls) == 1
        assert result.status == "hits" or result.path == "like"

    def test_router_bounds_query_input(self, db):
        _seed_an94(db)
        huge = "a" * (MAX_FTS5_QUERY_CHARS + 50)
        result = self._route(db, huge)
        assert result.status in ("hits", "zero")


class TestWildcardEscaping:
    """Fallback input ``%`` ``_`` ``\\`` stay literal; no match-all scan."""

    def _fallback_ids(self, db, query):
        row_ids = db._metadata_like_fallback_row_ids(query)
        return _row_ids_to_ids(db, row_ids)

    def test_percent_is_literal_not_match_all(self, db):
        db.create_session("plain", source="cli")
        db.set_session_title("plain", "Nothing Special")
        db.create_session("pct", source="cli")
        db.set_session_title("pct", "100% Done")
        db._conn.commit()
        ids = self._fallback_ids(db, "%")
        assert ids == {"pct"}  # only the literal '%' row, not the whole store

    def test_underscore_is_literal(self, db):
        db.create_session("plain", source="cli")
        db.set_session_title("plain", "Nothing Special")
        db.create_session("under", source="cli")
        db._conn.execute(
            "UPDATE sessions SET display_name = 'under_score' WHERE id = 'under'"
        )
        db._conn.commit()
        ids = self._fallback_ids(db, "_")
        assert ids == {"under"}

    def test_backslash_is_literal(self, db):
        db.create_session("bs", source="cli")
        db.set_session_title("bs", "Path A\\B")
        db.create_session("plain", source="cli")
        db.set_session_title("plain", "Nothing Special")
        db._conn.commit()
        ids = self._fallback_ids(db, "\\")
        assert ids == {"bs"}


class TestFieldSemantics:
    """#16 field semantics through each routed lane."""

    def test_an94_matches_title_and_display_through_trigram(self, db):
        _seed_an94(db)
        result = db._metadata_candidate_row_ids("an94")
        assert result.path == "trigram"
        assert result.status == "hits"
        assert "an94" in _row_ids_to_ids(db, result.row_ids)

    def test_interior_fragment_matches_through_trigram(self, db):
        db.create_session("frag", source="cli")
        db.set_session_title("frag", "Prestige Barrel Custom")
        db._conn.commit()
        result = db._metadata_candidate_row_ids("stige Bar")
        assert result.path == "trigram"
        assert result.status == "hits"
        assert "frag" in _row_ids_to_ids(db, result.row_ids)

    def test_raw_punctuated_id_matches_through_trigram(self, db):
        db.create_session("thr", source="cli")
        db._conn.execute(
            "UPDATE sessions SET id = 'discord:thread-123' WHERE id = 'thr'"
        )
        db._conn.commit()
        result = db._metadata_candidate_row_ids("thread-123")
        assert result.path == "trigram"
        assert result.status == "hits"
        assert "discord:thread-123" in _row_ids_to_ids(db, result.row_ids)

    def test_fallback_compacts_display_name_not_just_title(self, db):
        """The fallback's compact predicate covers display_name (a current
        drift the old list seam lacked)."""
        db.create_session("disp", source="cli")
        db.set_session_title("disp", "Plain Title")
        db._conn.execute(
            "UPDATE sessions SET display_name = 'Acme / #an-94-ops' WHERE id = 'disp'"
        )
        db._conn.commit()
        db._sessions_trigram_available = False  # force the LIKE fallback
        result = db._metadata_candidate_row_ids("an94")
        assert result.path == "like"
        assert result.status == "hits"
        assert "disp" in _row_ids_to_ids(db, result.row_ids)

    def test_fallback_id_is_raw_never_compacted(self, db):
        db.create_session("thr", source="cli")
        db._conn.execute(
            "UPDATE sessions SET id = 'discord:thread-123' WHERE id = 'thr'"
        )
        db._conn.commit()
        db._sessions_trigram_available = False
        result = db._metadata_candidate_row_ids("thread-123")
        assert result.path == "like"
        assert result.status == "hits"
        assert "discord:thread-123" in _row_ids_to_ids(db, result.row_ids)

    def test_unicode_covers_title_id_display(self, db):
        _seed_an94(db)
        # Explicit token queries route to the raw Unicode lane: the logical id
        # "an94" is a single unicode61 token and the display_name "Acme / #an-94-ops"
        # tokenizes to "acme" etc.
        r_id = db._metadata_candidate_row_ids('"an94"')
        assert r_id.path == "unicode"
        assert r_id.status == "hits"
        r_display = db._metadata_candidate_row_ids('"acme"')
        assert r_display.path == "unicode"
        assert r_display.status == "hits"
        # A phrase the unicode61 tokenizer splits ("an94ops" = an + 94 + ops)
        # zeroes the Unicode lane and recalls through the compact display_name
        # LIKE fallback instead.
        r_phrase = db._metadata_candidate_row_ids('"an94ops"')
        assert r_phrase.status == "hits"
        assert "an94" in _row_ids_to_ids(db, r_phrase.row_ids)


class TestCjkRoute:
    def test_cjk_query_classifies_cjk(self, db):
        assert db._classify_metadata_query("中文") == "cjk"

    def test_cjk_union_serves_when_capable(self, db):
        if not getattr(db, "_sessions_cjk_available", False):
            pytest.skip("CJK tokenizer not available in this environment")
        _seed_an94(db)
        db.create_session("cjk", source="cli")
        db.set_session_title("cjk", "中文測試")
        db._conn.commit()
        result = db._metadata_candidate_row_ids("中文")
        assert result.status == "hits"
        assert "cjk" in _row_ids_to_ids(db, result.row_ids)


class TestSnapshotRule:
    """Route guards/MATCH/fallback share one explicit read snapshot."""

    def test_router_with_conn_opens_no_fresh_reads(self, db, monkeypatch):
        _seed_an94(db)
        calls = []
        orig = SessionDB._read_ctx

        def spy(self_):
            calls.append(1)
            return orig(self_)

        with db._read_ctx() as conn:
            conn.execute("BEGIN")
            try:
                monkeypatch.setattr(SessionDB, "_read_ctx", spy)
                result = db._metadata_candidate_row_ids("an94", conn=conn)
                assert result.status == "hits"
            finally:
                conn.execute("ROLLBACK")
        # Only the caller's single read context was used — the router and its
        # lanes must not open fresh per-query reads.
        assert calls == []
