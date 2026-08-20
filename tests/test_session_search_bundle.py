"""Bundle regressions for the composed #109 Session Search feature.

Composes the three accepted child line contracts on one current-generation
tree (composition base ``main@243352e7b``):

- #128 metadata discovery — title / logical id / gateway ``display_name``
  through the shared ``SessionDB.list_sessions_rich(search_query=...)`` seam;
- #129 compression-aware lineage identity — compression-root winner
  dedupe, generic branch/delegation ancestry kept distinct, deferred/bounded
  hydration;
- #130 literal-safe exact/numbered title binding.

These REDs pin that the lines compose WITHOUT inventing a fourth Session
Search implementation (recon #109 @ 243352e7b):

- RED 1 (#128 x #130): fuzzy metadata discovery and exact title binding
  remain different contracts — ``foo #bar`` is discoverable as a literal
  metadata title but never binds as a numbered continuation of ``foo``.
- RED 2 (#129 x #130): title-first binding composes with compression-root
  winner selection through the shared ``session_search`` caller — positive
  compression segments dedupe to one logical result while generic
  branch/delegation ancestry stays distinct, and hydration remains
  deferred/bounded.
- RED 3 (#128 x current upstream lineage substrate): Desktop
  ``GET /api/sessions/search`` composes the metadata lane with the existing
  compression dedup + message-content lane.
"""
import asyncio
import json
import time

import pytest

from hermes_state import SessionDB
from tools.session_search_tool import _order_for_recall, session_search


@pytest.fixture
def db(tmp_path):
    return SessionDB(tmp_path / "state.db")


def _create(db, session_id, source="cli", parent=None):
    db.create_session(session_id, source=source)
    if parent is not None:
        db._conn.execute("PRAGMA foreign_keys = OFF")
        db._conn.execute(
            "UPDATE sessions SET parent_session_id = ? WHERE id = ?",
            (parent, session_id),
        )
        db._conn.commit()
        db._conn.execute("PRAGMA foreign_keys = ON")


def _message(db, session_id, content, role="user"):
    return db.append_message(session_id, role=role, content=content)


def _title(db, session_id, title, at=None):
    """Create (or re-use) a titled session; pin started_at when *at* given."""
    db.set_session_title(session_id, title)
    if at is not None:
        db._conn.execute(
            "UPDATE sessions SET started_at = ? WHERE id = ?", (at, session_id)
        )


def _link_compression(db, child, parent):
    """Positive compression edge child -> parent (parent ended by compression).

    Call AFTER appending messages: append_message refuses compression-ended
    sessions.
    """
    db._conn.execute("PRAGMA foreign_keys = OFF")
    db._conn.execute(
        "UPDATE sessions SET parent_session_id = ? WHERE id = ?",
        (parent, child),
    )
    db._conn.execute(
        "UPDATE sessions SET end_reason = 'compression' WHERE id = ?",
        (parent,),
    )
    db._conn.commit()
    db._conn.execute("PRAGMA foreign_keys = ON")


def _ranked(db, query, limit=300):
    """Ranked raw-hit candidates from the real FTS lane (tool discovery shape).

    Matches the tool's slim field projection (``_DISCOVER_SEARCH_FIELDS``) so
    winner rows never carry hydrated content — proving hydration stays
    deferred until after winner selection.
    """
    return _order_for_recall(
        db.search_messages(
            query,
            role_filter=["user", "assistant"],
            exclude_sources=["subagent", "tool"],
            limit=limit,
            fields=("id", "session_id", "role", "snippet", "source", "model", "session_started"),
        )
    )


def _metadata_ids(db, search_query):
    """The surfaced metadata-search candidates for *search_query*."""
    rows = db.list_sessions_rich(
        search_query=search_query,
        order_by_last_active=True,
        include_archived=True,
        limit=100,
    )
    return [r["id"] for r in rows]


# ---------------------------------------------------------------------------
# RED 1 — fuzzy metadata discovery and exact title binding stay distinct
# (#128 x #130)
# ---------------------------------------------------------------------------


class TestBundleFuzzyMetadataVsExactBinding:
    """``foo #bar`` is discoverable as literal metadata but never binds as a
    numbered continuation of ``foo``."""

    def _seed_foo_pair(self, db):
        t0 = time.time() - 1000
        _create(db, "red1-base")
        _title(db, "red1-base", "foo", at=t0)
        _message(db, "red1-base", "unrelated content alpha")
        _create(db, "red1-lookalike")
        _title(db, "red1-lookalike", "foo #bar", at=t0 + 1000)
        _message(db, "red1-lookalike", "unrelated content beta")

    def test_fuzzy_discovers_lookalike_but_exact_binds_base(self, db):
        self._seed_foo_pair(db)

        # 1. Fuzzy metadata discovery finds the newer `foo #bar` lookalike by
        #    its literal stored title, even though no message body contains it.
        assert "red1-lookalike" in _metadata_ids(db, "foo #bar")

        # 2. Exact title binding for `foo` resolves the older base and NEVER
        #    the `foo #bar` lookalike.
        assert db.resolve_session_by_title("foo") == "red1-base"

    @pytest.mark.parametrize(
        ("base", "lookalike"),
        [
            ("100% sure", "100Xsure #5"),  # literal %
            ("test_project", "testXproject #5"),  # literal _
            ("path\\to", "pathXto #5"),  # literal backslash
            ("topic # hash", "topicXhash #5"),  # embedded #
        ],
    )
    def test_literal_wildcard_base_does_not_widen_either_lane(
        self, db, base, lookalike
    ):
        """A LIKE-metachar / embedded-# base keeps its own family in BOTH lanes.

        The fuzzy lane may only surface the base and its strict ``base #2``
        child; the lookalike ``X``-variant must not appear. The exact lane
        resolves to the base's own newest strict continuation, never the
        lookalike.
        """
        t0 = time.time() - 1000
        _create(db, "red1w-base")
        _title(db, "red1w-base", base, at=t0)
        _message(db, "red1w-base", "unrelated alpha")
        _create(db, "red1w-child")
        _title(db, "red1w-child", f"{base} #2", at=t0 + 500)
        _message(db, "red1w-child", "unrelated beta")
        _create(db, "red1w-lookalike")
        _title(db, "red1w-lookalike", lookalike, at=t0 + 1000)
        _message(db, "red1w-lookalike", "unrelated gamma")

        # Exact lane: newest strict continuation of the base family.
        assert db.resolve_session_by_title(base) == "red1w-child"

        # Fuzzy lane: base family only — the X-lookalike is not a metadata hit.
        assert set(_metadata_ids(db, base)) == {"red1w-base", "red1w-child"}


# ---------------------------------------------------------------------------
# RED 2 — title binding and compression-lineage identity compose through the
# shared session_search caller (#129 x #130)
# ---------------------------------------------------------------------------


class TestBundleTitleBindingWithCompressionLineage:
    """Title-first binding picks the strict `foo #N` family while compression
    segments dedupe to one logical winner and generic branch/delegation
    ancestry stays a distinct winner."""

    def _seed(self, db):
        t0 = time.time() - 1000
        # foo family: base -> valid `foo #2` continuation -> compression tip.
        _create(db, "b2-base")
        _title(db, "b2-base", "foo", at=t0)
        _message(db, "b2-base", "foo base notes")
        _create(db, "b2-child")
        _title(db, "b2-child", "foo #2", at=t0 + 500)
        _message(db, "b2-child", "foo child notes")
        _create(db, "b2-tip", parent="b2-child")
        _message(db, "b2-tip", "foo tip notes")
        _link_compression(db, "b2-child", "b2-base")
        _link_compression(db, "b2-tip", "b2-child")
        # Invalid lookalike, newer — never binds as a continuation.
        _create(db, "b2-lookalike")
        _title(db, "b2-lookalike", "foo #bar", at=t0 + 1000)
        _message(db, "b2-lookalike", "unrelated lookalike content")
        # Generic branch/delegation ancestry with searchable content.
        _create(db, "b2-gparent")
        _message(db, "b2-gparent", "generic parent notes about foo")
        _create(db, "b2-gchild", parent="b2-gparent")
        _message(db, "b2-gchild", "generic child notes about foo")

    def test_title_binding_and_compression_root_winner_composition(self, db):
        self._seed(db)

        result = json.loads(session_search(query="foo", db=db, limit=10))
        assert result["success"] is True
        hits = result["results"]

        # 1. Title-first binding: exactly one title result, bound to the
        #    strict `foo #2` continuation — never the `foo #bar` lookalike.
        title_hits = [r for r in hits if r.get("matched_role") == "session_title"]
        assert len(title_hits) == 1
        assert title_hits[0]["session_id"] == "b2-child"

        # 2. The foo lineage surfaces ONLY via the title slot: its compression
        #    segments (base / child / tip) are not separate content winners —
        #    the title's compression root excludes them from winner selection.
        winner_ids = [r["session_id"] for r in hits]
        assert winner_ids.count("b2-base") == 0
        assert winner_ids.count("b2-tip") == 0

        # 3. Generic branch/delegation ancestry remains a distinct winner.
        assert {"b2-gparent", "b2-gchild"} <= set(winner_ids)

        # 4. The lookalike never surfaces in either lane.
        assert "b2-lookalike" not in set(winner_ids)

    def test_compression_root_winner_selection_stays_unhydrated(self, db):
        """Deferred/bounded hydration (#129): winner selection returns rows
        without content/context; hydration is a later bounded step."""
        self._seed(db)

        title_root = db.resolve_compression_lineage("b2-child")
        assert title_root == "b2-base"
        winners = db.resolve_lineage_winners(
            _ranked(db, "foo"),
            result_limit=10,
            excluded_lineage_roots=(title_root,),
        )["winners"]

        # The title's compression lineage is excluded; generic ancestry is not.
        roots = {row["lineage_root_id"] for row in winners}
        assert "b2-base" not in roots
        assert {"b2-gparent", "b2-gchild"} <= roots
        # No content/context hydration inside winner selection.
        assert all("content" not in row and "context" not in row for row in winners)


# ---------------------------------------------------------------------------
# RED 3 — Desktop metadata discovery composes with compression + content lane
# (#128 x current upstream lineage substrate)
# ---------------------------------------------------------------------------


class TestBundleDesktopMetadataComposition:
    """``GET /api/sessions/search`` surfaces a compressed conversation by its
    stored metadata title while keeping compression dedup and the
    message-content lane."""

    def _seed(self):
        from hermes_constants import get_hermes_home
        from hermes_state import SessionDB

        db_path = get_hermes_home() / "state.db"
        seed = SessionDB(db_path=db_path)
        try:
            croot = seed.create_session("b3-croot", source="cli")
            seed.set_session_title(croot, "Acme Guild / #finance")
            seed.append_message(croot, "user", "rotation seed; nothing about finance")
            seed.end_session(croot, "compression")
            ctip = seed.create_session("b3-ctip", source="cli")
            seed._conn.execute(
                "UPDATE sessions SET parent_session_id = ? WHERE id = ?",
                (croot, ctip),
            )
            seed._conn.commit()
            seed.set_session_title(ctip, "Acme Guild / #finance")
            seed.append_message(ctip, "user", "unrelated continuation content")
            other = seed.create_session("b3-other", source="desktop")
            seed.append_message(other, "user", "the needle appears in the body")
        finally:
            seed.close()
        return croot, ctip, other

    def test_compressed_conversation_discoverable_by_stored_title(self):
        from hermes_cli import web_server

        croot, ctip, other = self._seed()

        # Metadata-only conversation is discoverable by stored title and the
        # compression chain collapses to ONE surfaced conversation.
        response = asyncio.run(web_server.search_sessions(q="finance", limit=5))
        hits = [r for r in response["results"] if r["lineage_root"] == croot]
        assert len(hits) == 1, response["results"]
        # Stored title survives into the Desktop row.
        assert hits[0]["title"] == "Acme Guild / #finance"
        # Projected to the live tip, not the ended root.
        assert hits[0]["session_id"] == ctip

        # limit still applies to the composed lane.
        one = asyncio.run(web_server.search_sessions(q="finance", limit=1))
        assert len(one["results"]) == 1

        # source filters still apply to the metadata lane.
        none_here = asyncio.run(
            web_server.search_sessions(q="finance", limit=5, source="desktop")
        )
        assert not any(r["lineage_root"] == croot for r in none_here["results"])

        # Message-content discovery remains available as the subsequent lane.
        content = asyncio.run(web_server.search_sessions(q="needle", limit=5))
        assert {r["session_id"] for r in content["results"]} == {other}
