"""Tests for the single-shape session_search tool.

Three calling shapes:
  1. DISCOVERY — pass query → FTS5 + anchored window + bookends per hit
  2. SCROLL    — pass session_id + around_message_id → just the window
  3. BROWSE    — no args → recent sessions chronologically

All run zero LLM calls.
"""
import json
import time

import pytest

from hermes_state import SessionDB
from tools.session_search_tool import (
    SESSION_SEARCH_SCHEMA,
    _format_timestamp,
    _is_compacted_message,
    _is_compression_ended,
    _resolve_to_parent,
    _session_link,
    session_search,
)


@pytest.fixture
def db(tmp_path):
    return SessionDB(tmp_path / "state.db")


def _seed_modpack_sessions(db):
    """Create three sessions about a modpack so FTS5 has hits to dedupe."""
    now = int(time.time())
    # Older session — modpack origin
    db.create_session("s_oldest", source="cli")
    db._conn.execute("UPDATE sessions SET started_at = ?, title = ? WHERE id = ?",
                     (now - 30000, "Building the Modpack", "s_oldest"))
    db.append_message("s_oldest", role="user", content="Let's build a Minecraft modpack")
    db.append_message("s_oldest", role="assistant", content="Great. Let me scaffold the modpack repo.")
    db.append_message("s_oldest", role="user", content="Use NeoForge 1.21.1")
    db.append_message("s_oldest", role="assistant", content="Done. Modpack repo created with NeoForge 1.21.1.")
    db.append_message("s_oldest", role="assistant", content="Tier-0 mods installed; modpack smoke test passes.")

    # Middle session — modpack quest coverage
    db.create_session("s_middle", source="cli")
    db._conn.execute("UPDATE sessions SET started_at = ?, title = ? WHERE id = ?",
                     (now - 15000, "Modpack Quest Coverage", "s_middle"))
    db.append_message("s_middle", role="user", content="Deep-dive every modpack reference quest guide")
    db.append_message("s_middle", role="assistant", content="Surveying ATM10 questbook for modpack inspiration.")
    db.append_message("s_middle", role="user", content="Update the modpack version too")
    db.append_message("s_middle", role="assistant", content="Modpack version bumped 0.4 → 0.8.5; quest coverage page added.")

    # Newest session — modpack mob spawn fix
    db.create_session("s_newest", source="cli")
    db._conn.execute("UPDATE sessions SET started_at = ?, title = ? WHERE id = ?",
                     (now - 1000, "Modpack Mob Spawn Fix", "s_newest"))
    db.append_message("s_newest", role="user", content="Fix the modpack mob spawning")
    db.append_message("s_newest", role="assistant", content="Investigating elite mob gating in the modpack KubeJS.")
    db.append_message("s_newest", role="assistant", content="Shipped commit b850442. Modpack alternator nerfed too.")
    db._conn.commit()


# =========================================================================
# Schema invariants
# =========================================================================

class TestSchema:
    def test_schema_params_cover_every_shape(self):
        params = SESSION_SEARCH_SCHEMA["parameters"]["properties"]
        # Discovery shape
        assert "query" in params
        assert "limit" in params
        assert params["sort"]["enum"] == ["newest", "oldest"]
        # Scroll shape
        assert "session_id" in params
        assert "around_message_id" in params
        assert "window" in params
        # Shared
        assert "role_filter" in params
        # Mode is inferred from which args are set — no explicit mode param
        assert "mode" not in params


class TestFormatTimestamp:
    def test_formats_unix_and_passes_through_the_rest(self):
        assert "2023" in _format_timestamp(1700000000)
        assert _format_timestamp(None) == "unknown"
        assert _format_timestamp("not-a-number-string") == "not-a-number-string"


# =========================================================================
# Browse shape (no args)
# =========================================================================

class TestBrowseShape:
    def test_no_args_returns_recent_sessions(self, db):
        _seed_modpack_sessions(db)
        result = json.loads(session_search(db=db))
        assert result["success"] is True
        assert result["mode"] == "browse"
        assert result["count"] >= 3

    def test_browse_excludes_current_session(self, db):
        _seed_modpack_sessions(db)
        result = json.loads(session_search(db=db, current_session_id="s_newest"))
        sids = [r["session_id"] for r in result["results"]]
        assert "s_newest" not in sids


# =========================================================================
# Discovery shape (with query)
# =========================================================================

class TestDiscoveryShape:
    def test_discovery_does_not_use_search_messages(self, db, monkeypatch):
        # Discovery winner selection runs through the SQL winner seam, not
        # the per-message search_messages() path, so candidate context is
        # never projected.  (Replaced the pre-SQL era field-plan test.)
        _seed_modpack_sessions(db)
        original = db.search_messages

        def fail_if_called(*args, **kwargs):
            raise AssertionError("discovery must not call search_messages()")

        monkeypatch.setattr(db, "search_messages", fail_if_called)

        result = json.loads(session_search(query="modpack", limit=1, db=db))

        assert result["success"] is True
        assert len(result["results"]) == 1
        hit = result["results"][0]
        assert "bookend_start" in hit
        assert hit["messages"]
        assert "bookend_end" in hit

    def test_discovery_result_has_bookends_and_window(self, db):
        _seed_modpack_sessions(db)
        result = json.loads(session_search(query="modpack", limit=3, db=db))
        assert result["success"] is True
        assert result["mode"] == "discover"
        assert result["count"] >= 1
        for hit in result["results"]:
            assert "bookend_start" in hit
            assert "messages" in hit
            assert "bookend_end" in hit
            assert "match_message_id" in hit
            assert "snippet" in hit
            assert "messages_before" in hit
            assert "messages_after" in hit


    def test_current_session_filtered_out(self, db):
        _seed_modpack_sessions(db)
        result = json.loads(session_search(query="modpack", db=db, current_session_id="s_newest"))
        sids = [r["session_id"] for r in result["results"]]
        assert "s_newest" not in sids


class TestDiscoverySort:
    def test_sort_newest_orders_by_recency(self, db):
        _seed_modpack_sessions(db)
        result = json.loads(session_search(query="modpack", limit=3, sort="newest", db=db))
        # First result should be the most recent session
        first = result["results"][0]
        assert first["session_id"] == "s_newest" or "Newest" in (first.get("title") or "")

    def test_sort_oldest_orders_by_age(self, db):
        _seed_modpack_sessions(db)
        result = json.loads(session_search(query="modpack", limit=3, sort="oldest", db=db))
        first = result["results"][0]
        assert first["session_id"] == "s_oldest"


# =========================================================================
# Scroll shape (session_id + around_message_id)
# =========================================================================

class TestScrollShape:
    def test_scroll_returns_anchored_window_without_bookends(self, db):
        _seed_modpack_sessions(db)
        # Get an anchor first via discovery
        disc = json.loads(session_search(query="modpack", limit=1, db=db))
        anchor_sid = disc["results"][0]["session_id"]
        anchor_mid = disc["results"][0]["match_message_id"]

        # Now scroll
        result = json.loads(session_search(
            session_id=anchor_sid, around_message_id=anchor_mid, window=2, db=db
        ))
        assert result["success"] is True
        assert result["mode"] == "scroll"
        # Scroll shape has no bookends
        assert "bookend_start" not in result
        assert "bookend_end" not in result
        # The anchor is in the window and flagged
        anchor_in_window = [m for m in result["messages"] if m["id"] == anchor_mid]
        assert len(anchor_in_window) == 1
        assert anchor_in_window[0].get("anchor") is True

    def test_scroll_window_clamped_to_20(self, db):
        _seed_modpack_sessions(db)
        disc = json.loads(session_search(query="modpack", limit=1, db=db))
        anchor_sid = disc["results"][0]["session_id"]
        anchor_mid = disc["results"][0]["match_message_id"]
        result = json.loads(session_search(
            session_id=anchor_sid, around_message_id=anchor_mid, window=999, db=db
        ))
        assert result["window"] == 20


    def test_scroll_rejects_active_delegation_child_in_current_lineage(self, db):
        db.create_session("s_current", source="cli")
        db.create_session(
            "s_delegate", source="delegate", parent_session_id="s_current"
        )
        mid = db.append_message(
            "s_delegate", role="assistant", content="live delegated result"
        )

        result = json.loads(session_search(
            session_id="s_delegate", around_message_id=mid, db=db,
            current_session_id="s_current",
        ))

        assert result["success"] is False
        assert "current session" in result.get("error", "").lower()


class TestScrollPattern:
    """The forward/backward scroll loop using tool output."""

    def test_scroll_forward_from_last_id(self, db):
        # Long session
        db.create_session("s_long", source="cli")
        ids = []
        for i in range(20):
            ids.append(db.append_message("s_long", role="user" if i % 2 == 0 else "assistant",
                                         content=f"long session msg {i}"))

        v1 = json.loads(session_search(
            session_id="s_long", around_message_id=ids[5], window=3, db=db
        ))
        last_id = v1["messages"][-1]["id"]
        v2 = json.loads(session_search(
            session_id="s_long", around_message_id=last_id, window=3, db=db
        ))
        # Forward scroll: v2 should reach further than v1
        assert max(m["id"] for m in v2["messages"]) > max(m["id"] for m in v1["messages"])
        # Boundary id appears in both
        assert last_id in [m["id"] for m in v1["messages"]]
        assert last_id in [m["id"] for m in v2["messages"]]


# =========================================================================
# Shape precedence
# =========================================================================

class TestShapePrecedence:
    def test_scroll_args_beat_query(self, db):
        _seed_modpack_sessions(db)
        disc = json.loads(session_search(query="modpack", limit=1, db=db))
        anchor_sid = disc["results"][0]["session_id"]
        anchor_mid = disc["results"][0]["match_message_id"]
        # Pass both query and scroll args — scroll should win
        result = json.loads(session_search(
            query="modpack",  # would normally trigger discovery
            session_id=anchor_sid, around_message_id=anchor_mid, db=db,
        ))
        assert result["mode"] == "scroll"


    def test_session_id_without_anchor_reads(self, db):
        _seed_modpack_sessions(db)
        # session_id alone (no anchor, no query) → read shape, not browse.
        result = json.loads(session_search(session_id="s_oldest", db=db))
        assert result["mode"] == "read"


# =========================================================================
# Read shape — dump a whole session by id (serves @session links)
# =========================================================================

class TestReadShape:
    def test_read_returns_full_session(self, db):
        _seed_modpack_sessions(db)
        result = json.loads(session_search(session_id="s_oldest", db=db))
        assert result["success"] is True
        assert result["mode"] == "read"
        assert result["session_id"] == "s_oldest"
        assert result["message_count"] == 5
        assert result["truncated"] is False
        assert len(result["messages"]) == 5
        assert result["session_meta"]["title"] == "Building the Modpack"

    def test_read_strips_ansi_sequences_from_messages(self, db):
        db.create_session("s_ansi", source="cli")
        db.append_message("s_ansi", role="user", content="plain")
        db.append_message(
            "s_ansi", role="assistant", content="\u001b[31mred text\u001b[0m and more"
        )
        db._conn.commit()
        result = json.loads(session_search(session_id="s_ansi", db=db))
        assert result["success"] is True
        rendered = [m["content"] for m in result["messages"] if m.get("content")]
        assert any(text == "red text and more" for text in rendered)
        assert all("\u001b" not in text for text in rendered)

    def test_read_truncates_large_session(self, db):
        db.create_session("s_big", source="cli")
        for i in range(50):
            db.append_message("s_big", role="user" if i % 2 == 0 else "assistant", content=f"m{i}")
        db._conn.commit()
        result = json.loads(session_search(session_id="s_big", db=db))
        assert result["mode"] == "read"
        assert result["message_count"] == 50
        assert result["truncated"] is True
        assert len(result["messages"]) == 30  # head 20 + tail 10


# =========================================================================
# Session links — the value the agent writes to point the user at a session
# =========================================================================

def _linked_session_id(link: str) -> str:
    """Recover the session id from an `@session:[<profile>/]<id>` value."""
    assert link.startswith("@session:"), link
    value = link[len("@session:"):]

    return value.rsplit("/", 1)[-1]


class TestSessionLink:
    def test_link_carries_the_named_profile(self):
        assert _session_link("s_oldest", "work") == "@session:work/s_oldest"


    def test_every_discovery_result_links_to_its_own_session(self, db):
        _seed_modpack_sessions(db)
        result = json.loads(session_search(query="modpack", limit=5, db=db))

        assert result["results"]
        for entry in result["results"]:
            assert _linked_session_id(entry["link"]) == entry["session_id"]


# =========================================================================
# Cross-profile read — `profile` swaps in another profile's DB (read-only)
# =========================================================================

class TestCrossProfileRead:
    def _patch_profiles(self, monkeypatch, home, exists=True):
        from hermes_cli import profiles as profiles_mod
        monkeypatch.setattr(profiles_mod, "normalize_profile_name", lambda n: n)
        monkeypatch.setattr(profiles_mod, "validate_profile_name", lambda n: None)
        monkeypatch.setattr(profiles_mod, "profile_exists", lambda n: exists)
        monkeypatch.setattr(profiles_mod, "get_profile_dir", lambda n: home)

    def test_bare_id_locates_across_profiles(self, db, tmp_path, monkeypatch):
        # The real-world failure: model dropped the owning profile and passed a
        # bare id. The tool must scan profiles and find it anyway.
        other_home = tmp_path / "asdf_home"
        other_home.mkdir()
        other = SessionDB(other_home / "state.db")
        other.create_session("s_far", source="cli")
        other.append_message("s_far", role="user", content="hi")
        other._conn.commit()

        from collections import namedtuple
        from hermes_cli import profiles as profiles_mod
        Info = namedtuple("Info", "name path")
        monkeypatch.setattr(profiles_mod, "get_profile_dir", lambda n: tmp_path / "default_home")
        monkeypatch.setattr(profiles_mod, "list_profiles", lambda: [Info("asdf", other_home)])

        # `db` (current profile) lacks s_far; no profile passed → scan finds it.
        result = json.loads(session_search(session_id="s_far", db=db))
        assert result["success"] is True
        assert result["mode"] == "read"
        assert result["profile"] == "asdf"


    def test_combined_value_autosplits(self, db, tmp_path, monkeypatch):
        # Agent passed the raw "@session:<profile>/<id>" value as session_id with
        # no separate profile — the tool should recover both.
        other_home = tmp_path / "other_home"
        other_home.mkdir()
        other = SessionDB(other_home / "state.db")
        other.create_session("s_other", source="cli")
        other.append_message("s_other", role="user", content="hi")
        other._conn.commit()

        self._patch_profiles(monkeypatch, other_home)

        # Every permutation the model might send must resolve to (asdf, s_other).
        for kwargs in (
            {"session_id": "asdf/s_other"},                    # full value, no profile
            {"session_id": "asdf/s_other", "profile": "asdf"},  # full value AND profile
            {"session_id": "s_other", "profile": "asdf"},       # bare id + profile
        ):
            result = json.loads(session_search(db=db, **kwargs))
            assert result["success"] is True, kwargs
            assert result["mode"] == "read"
            assert result["session_id"] == "s_other"


# =========================================================================
# Cron demotion in discover ranking (#19434)
# =========================================================================

class TestCronDemotion:
    def _seed_cron_and_interactive(self, db):
        """One interactive (telegram) session and several cron sessions, all
        matching the same query. Cron rows accumulate repetitive vocabulary
        and out-number the user's single interactive session — the live-data
        symptom in #19434.
        """
        now = int(time.time())
        # Interactive user session — older, so it loses on bare recency too.
        db.create_session("s_user", source="telegram")
        db._conn.execute("UPDATE sessions SET started_at = ? WHERE id = ?",
                         (now - 90000, "s_user"))
        db.append_message("s_user", role="user", content="how is the venom project going")
        db.append_message("s_user", role="assistant", content="The venom project shipped its first milestone.")
        # Several cron sessions, all newer and all stuffed with the same terms.
        for i in range(8):
            sid = f"cron_{i}"
            db.create_session(sid, source="cron")
            db._conn.execute("UPDATE sessions SET started_at = ? WHERE id = ?",
                             (now - 1000 - i, sid))
            db.append_message(sid, role="user", content="venom project daily status")
            db.append_message(sid, role="assistant", content="venom project venom project venom summary")
        db._conn.commit()

    def test_interactive_session_surfaces_above_cron(self, db):
        self._seed_cron_and_interactive(db)
        result = json.loads(session_search(query="venom project", limit=1, db=db))
        assert result["success"] is True
        assert result["count"] == 1
        # With cron drowning FTS, bare BM25/recency would return a cron_* hit.
        # Demotion must put the user's interactive session first.
        assert result["results"][0]["source"] == "telegram"
        assert result["results"][0]["session_id"] == "s_user"

    def test_cron_still_reachable_when_only_match(self, db):
        """Demotion must not exclude cron — when only cron matches, it still
        comes back."""
        now = int(time.time())
        db.create_session("cron_only", source="cron")
        db._conn.execute("UPDATE sessions SET started_at = ? WHERE id = ?",
                         (now - 500, "cron_only"))
        db.append_message("cron_only", role="user", content="quarterly archive sweep")
        db.append_message("cron_only", role="assistant", content="Archive sweep complete.")
        db._conn.commit()
        result = json.loads(session_search(query="archive sweep", db=db))
        assert result["success"] is True
        assert result["count"] == 1
        assert result["results"][0]["source"] == "cron"


# =========================================================================
# Compaction summary filtering (#43175)
# =========================================================================

class TestCompactionSummaryFiltering:
    """session_search discovery must exclude compaction handoffs from bookends."""

    def test_is_compaction_summary_detects_prefix(self):
        from tools.session_search_tool import _is_compaction_summary
        assert _is_compaction_summary("[CONTEXT COMPACTION — REFERENCE ONLY] foo")
        assert _is_compaction_summary("[CONTEXT SUMMARY]: old summary")
        assert not _is_compaction_summary("Hello, how can I help?")
        assert not _is_compaction_summary("")
        assert not _is_compaction_summary(None)

    def test_compaction_summary_excluded_from_bookend_start(self, db):
        """Compaction handoff in bookend_start position must be filtered out."""
        db.create_session("s_compact", source="cli")
        # First message: a compaction handoff (should be filtered)
        db.append_message("s_compact", role="user",
                          content="[CONTEXT COMPACTION — REFERENCE ONLY] "
                                  "Earlier turns were compacted into the summary below. " + "x" * 50000)
        # Second message: normal user message
        db.append_message("s_compact", role="user", content="Fix the zorgblat rendering bug")
        # Padding messages to push window away from session start (so bookend has room)
        for i in range(10):
            db.append_message("s_compact", role="user", content=f"setup step {i}")
            db.append_message("s_compact", role="assistant", content=f"setup done {i}")
        # Match target: uses a unique term so FTS5 anchors here, not at the start
        db.append_message("s_compact", role="user", content="investigate the frobnitz mob spawning in KubeJS")
        db.append_message("s_compact", role="assistant", content="I'll look into the frobnitz mob spawning issue.")
        # Tail messages
        for i in range(5):
            db.append_message("s_compact", role="user", content=f"tail {i}")
            db.append_message("s_compact", role="assistant", content=f"done tail {i}")
        db._conn.commit()

        result = json.loads(session_search(query="frobnitz mob spawning", db=db, limit=1))
        assert result["success"] is True
        assert len(result["results"]) >= 1
        entry = result["results"][0]
        # bookend_start must NOT contain the compaction handoff
        for msg in entry.get("bookend_start", []):
            assert "[CONTEXT COMPACTION" not in (msg.get("content") or "")
        # The normal message should still be present in bookend_start
        bookend_contents = [m.get("content", "") for m in entry.get("bookend_start", [])]
        assert any("zorgblat" in c for c in bookend_contents)


# =========================================================================
# Compression-aware discovery (#6256)
#
# After compression (in-place compaction or legacy rotation), pre-compaction
# content is no longer in the live context but MUST stay discoverable via
# session_search. The old code skipped any FTS hit on the current session or
# lineage, creating a "memory black hole". Delegation children must STAY
# excluded — their content is still visible to the parent agent.
# =========================================================================

class TestResolveToParent:
    """Unit tests for _resolve_to_parent's compression-aware tuple return."""

    def test_legacy_rotation_detects_compression(self, db):
        """Parent ended with end_reason='compression', child has parent_session_id."""
        db.create_session("s_parent", source="cli")
        db.end_session("s_parent", "compression")
        db.create_session("s_child", source="cli", parent_session_id="s_parent")
        root, has_compression = _resolve_to_parent(db, "s_child")
        assert root == "s_parent"
        assert has_compression is True


    def test_chain_with_mixed_edges(self, db):
        """Compression grandparent → parent → child (no end_reason on parent)."""
        db.create_session("s_gp", source="cli")
        db.end_session("s_gp", "compression")
        db.create_session("s_p", source="cli", parent_session_id="s_gp")
        # s_p does NOT end with compression — but ancestor s_gp does
        db.create_session("s_c", source="cli", parent_session_id="s_p")
        root, has_compression = _resolve_to_parent(db, "s_c")
        assert root == "s_gp"
        assert has_compression is True


class TestIsCompactedMessage:
    """Unit tests for the _is_compacted_message helper."""

    def test_active_message_returns_false(self, db):
        db.create_session("s1", source="cli")
        mid = db.append_message("s1", role="user", content="hello")
        assert _is_compacted_message(db, mid) is False

    def test_compacted_message_returns_true(self, db):
        db.create_session("s1", source="cli")
        mid = db.append_message("s1", role="user", content="archived content")
        db.archive_and_compact("s1", [
            {"role": "assistant", "content": "compacted summary"},
        ])
        # mid is now active=0, compacted=1
        assert _is_compacted_message(db, mid) is True


class TestInPlaceCompactionDiscovery:
    """In-place compaction: archived turns on the SAME session_id must be
    discoverable from the current session."""

    def test_archived_content_discoverable_after_compaction(self, db):
        """The core regression: pre-compaction content on the current session
        must surface in discovery even though raw_sid == current_session_id."""
        db.create_session("s_compact", source="cli")
        db.append_message("s_compact", role="user",
                          content="The spectral phoenix only spawns during full moons")
        db.append_message("s_compact", role="assistant",
                          content="Spectral phoenix requires moonstone bait")
        db.archive_and_compact("s_compact", [
            {"role": "user", "content": "Summary: spectral phoenix discussed"},
            {"role": "assistant", "content": "Acknowledged spectral phoenix info"},
        ])

        result = json.loads(session_search(
            query="spectral phoenix", db=db, current_session_id="s_compact",
        ))
        assert result["success"] is True
        assert result["count"] >= 1
        # The hit should be from the same session (archived rows)
        hit = result["results"][0]
        assert hit["session_id"] == "s_compact"

    def test_live_content_still_filtered_on_current_session(self, db):
        """Non-compacted (active) content on the current session stays filtered."""
        db.create_session("s_live", source="cli")
        db.append_message("s_live", role="user", content="crystal golem farming route")
        result = json.loads(session_search(
            query="crystal golem", db=db, current_session_id="s_live",
        ))
        assert result["count"] == 0


class TestLegacyRotationDiscovery:
    """Legacy rotation: parent session ended with end_reason='compression',
    child session created. Parent's pre-compaction content must be discoverable
    from the child."""

    def test_compression_parent_discoverable_from_child(self, db):
        db.create_session("s_parent", source="cli")
        db.append_message("s_parent", role="user",
                          content="The void crystal mining requires diamond pickaxe")
        db.append_message("s_parent", role="assistant",
                          content="Void crystal found in the deep caverns")
        db.end_session("s_parent", "compression")

        db.create_session("s_child", source="cli", parent_session_id="s_parent")
        db.append_message("s_child", role="user", content="Continue void crystal work")

        result = json.loads(session_search(
            query="void crystal", db=db, current_session_id="s_child",
        ))
        assert result["success"] is True
        assert result["count"] >= 1
        sids = [r["session_id"] for r in result["results"]]
        assert "s_parent" in sids


class TestDelegationExclusion:
    """Delegation children (delegate_task) must STAY excluded — their content
    is still visible to the parent agent. parent_session_id is set but the
    parent does NOT have end_reason='compression'."""

    def test_delegation_parent_excluded_from_child(self, db):
        """Child can see its own content but parent's live content stays
        excluded (it's in context via delegation)."""
        db.create_session("s_parent", source="cli")
        db.append_message("s_parent", role="user",
                          content="nebula deployment infrastructure setup")
        db.append_message("s_parent", role="assistant",
                          content="Nebula deployment configured successfully")

        db.create_session("s_child", source="cli", parent_session_id="s_parent")
        db.append_message("s_child", role="user",
                          content="delegated nebula deployment subtask")

        result = json.loads(session_search(
            query="nebula deployment", db=db, current_session_id="s_child",
        ))
        assert result["count"] == 0


# =========================================================================
# Both layers together: discovery scope (#63144) × bookend bounding (#69334)
#
# Compaction touches two independent layers of session_search:
#   1. Discovery scope — compaction-archived rows on the current session must
#      surface in discovery (this PR).
#   2. Content bounding — bookends must exclude generated compaction handoff
#      summaries and cap message content length (#43175 / #69334).
# A compacted session exercises both at once: its archived content is the FTS
# hit, while the compaction summary row it produced sits at the session tail,
# exactly where bookend_end is sampled.
# =========================================================================

class TestCompactionDiscoveryBothLayers:
    """Compacted-session content is discoverable AND its bookends still
    exclude compaction summaries / cap content length."""

    def _seed_compacted_session(self, db):
        db.create_session("s_both", source="cli")
        # Long normal opening — exercises the 1200-char bookend cap.
        db.append_message("s_both", role="user",
                          content="Kick off the obsidian gateway migration. " + "o" * 5000)
        db.append_message("s_both", role="assistant",
                          content="Starting the obsidian gateway migration plan.")
        # Padding so the anchored window doesn't swallow the bookends.
        for i in range(10):
            db.append_message("s_both", role="user", content=f"migration step {i}")
            db.append_message("s_both", role="assistant", content=f"migration step {i} done")
        # The FTS match target — will be archived by compaction below.
        db.append_message("s_both", role="user",
                          content="the obsidian gateway needs a quartz keystone to activate")
        db.append_message("s_both", role="assistant",
                          content="Noted: quartz keystone required for the obsidian gateway.")
        for i in range(5):
            db.append_message("s_both", role="user", content=f"wrap-up {i}")
            db.append_message("s_both", role="assistant", content=f"wrapped {i}")
        # Compact in place: everything above becomes active=0/compacted=1 and
        # the handoff summary is inserted as the new live tail.
        db.archive_and_compact("s_both", [
            {"role": "user",
             "content": "[CONTEXT COMPACTION — REFERENCE ONLY] "
                        "Earlier turns were compacted into this summary. " + "s" * 50000},
            {"role": "assistant", "content": "Continuing after compaction."},
        ])
        db._conn.commit()

    def test_archived_hit_surfaces_with_bounded_summary_free_bookends(self, db):
        self._seed_compacted_session(db)

        result = json.loads(session_search(
            query="quartz keystone", db=db, current_session_id="s_both",
        ))

        # Layer 1 — discovery scope: the archived (active=0, compacted=1)
        # content on the CURRENT session must surface.
        assert result["success"] is True
        assert result["count"] >= 1
        entry = result["results"][0]
        assert entry["session_id"] == "s_both"

        # Layer 2a — summary exclusion: the compaction handoff row sits at the
        # session tail (freshly inserted by archive_and_compact), exactly where
        # bookend_end samples — it must be filtered out.
        for msg in entry.get("bookend_start", []) + entry.get("bookend_end", []):
            assert "[CONTEXT COMPACTION" not in (msg.get("content") or "")

        # Layer 2b — content caps: bookends ≤1200 chars, window ≤4000 chars.
        for msg in entry.get("bookend_start", []) + entry.get("bookend_end", []):
            assert len(msg.get("content") or "") <= 1210
        for msg in entry.get("messages", []):
            assert len(msg.get("content") or "") <= 4010

        # The long-but-legitimate opening survives (capped, not dropped).
        bookend_contents = [m.get("content") or "" for m in entry.get("bookend_start", [])]
        assert any("obsidian gateway migration" in c for c in bookend_contents)


# =========================================================================
# Teknium review round 2: rewind exclusion + delegation-under-compression
# =========================================================================

class TestRewindExclusion:
    """Rewind/undo rows (active=0, compacted=0) must STAY hidden — only
    compaction archives (active=0, compacted=1) should surface."""

    def test_compacted_messages_still_surface_alongside_rewind(self, db):
        """On the same session: compacted rows surface, rewind rows don't."""
        db.create_session("s_mixed", source="cli")
        # Message that will be compacted
        db.append_message("s_mixed", role="user",
                          content="compaction archived content beta")
        db.archive_and_compact("s_mixed", [
            {"role": "assistant", "content": "Summary of beta"},
        ])
        # Now add a post-compaction message and rewind it
        mid2 = db.append_message("s_mixed", role="user",
                                 content="rewound content gamma")
        db._conn.execute(
            "UPDATE messages SET active = 0, compacted = 0 WHERE id = ?",
            (mid2,),
        )
        db._conn.commit()

        # Compacted content should be discoverable
        result_compact = json.loads(session_search(
            query="compaction archived content beta", db=db,
            current_session_id="s_mixed",
        ))
        assert result_compact["count"] >= 1

        # Rewound content should NOT be discoverable
        result_rewind = json.loads(session_search(
            query="rewound content gamma", db=db,
            current_session_id="s_mixed",
        ))
        assert result_rewind["count"] == 0


class TestCompressionEndedHelper:
    """Unit tests for _is_compression_ended."""

    def test_compression_ended_session(self, db):
        db.create_session("s1", source="cli")
        db.end_session("s1", "compression")
        assert _is_compression_ended(db, "s1") is True

    def test_delegation_child_not_ended(self, db):
        """A delegation child under a compression continuation does NOT have
        end_reason='compression' itself."""
        db.create_session("s_parent", source="cli")
        db.end_session("s_parent", "compression")
        db.create_session("s_continuation", source="cli", parent_session_id="s_parent")
        db.create_session("s_delegate_child", source="cli", parent_session_id="s_continuation")
        assert _is_compression_ended(db, "s_delegate_child") is False


class TestLegacyContinuationPlusDelegation:
    """Regression: a delegation child created under a compression continuation
    must stay excluded — its content is still live to the parent agent.
    Only the compression-ended ancestor's content should surface."""

    def test_compression_parent_surfaces_but_delegate_child_excluded(self, db):
        """Setup: grandparent (compression) → parent (compression) → child
        (active, current session). A delegation grandchild is created under
        the parent. Searching from the child should find grandparent/parent
        content but NOT the delegation grandchild's content."""
        # Grandparent: compression-ended, has searchable content
        db.create_session("s_gp", source="cli")
        db.append_message("s_gp", role="user",
                          content="grandparent cosmic anomaly research data")
        db.end_session("s_gp", "compression")

        # Parent: compression-ended continuation
        db.create_session("s_p", source="cli", parent_session_id="s_gp")
        db.append_message("s_p", role="user",
                          content="parent cosmic anomaly follow-up notes")
        db.end_session("s_p", "compression")

        # Current session: active child
        db.create_session("s_current", source="cli", parent_session_id="s_p")

        # Delegation child under s_p (not compression-ended)
        db.create_session("s_delegate", source="cli", parent_session_id="s_p")
        db.append_message("s_delegate", role="assistant",
                          content="delegated cosmic anomaly subtask results")

        result = json.loads(session_search(
            query="cosmic anomaly", db=db,
            current_session_id="s_current",
        ))

        # Compression-ended ancestors should be discoverable
        sids = [r["session_id"] for r in result["results"]]
        assert "s_gp" in sids or "s_p" in sids

        # Delegation child must NOT appear
        assert "s_delegate" not in sids


# =========================================================================
# #68 compression-root parity at the tool layer
# =========================================================================

class TestCompressionRootToolParity:
    """Current-session / exact-title exclusion shares the DB winner seam's
    positive compression-continuation root semantics."""

    def test_current_compression_child_live_content_excluded_ancestor_surfaces(self, db):
        """Searching from a compression continuation child: the child's own
        live content is in context (excluded), but the compression-ended
        ancestor's archived content surfaces."""
        db.create_session("s_parent", source="cli")
        db.append_message("s_parent", role="user",
                          content="parent needle archived")
        db.end_session("s_parent", "compression")
        db.create_session("s_current", source="cli", parent_session_id="s_parent")
        db.append_message("s_current", role="user",
                          content="current live needle")

        result = json.loads(session_search(
            query="needle", db=db, current_session_id="s_current",
        ))
        sids = [r["session_id"] for r in result["results"]]
        assert "s_current" not in sids
        assert "s_parent" in sids

    def test_branch_child_under_current_lineage_stays_distinct(self, db):
        """A branch child (parent-bound _branched_from marker) is NOT part of
        the current compression lineage, so it surfaces as its own result."""
        db.create_session("s_parent", source="cli")
        db.append_message("s_parent", role="user",
                          content="parent needle content")
        db.end_session("s_parent", "compression")
        db.create_session("s_current", source="cli", parent_session_id="s_parent")
        db.create_session("s_branch", source="cli", parent_session_id="s_parent")
        db._conn.execute(
            "UPDATE sessions SET model_config = ? WHERE id = ?",
            (json.dumps({"_branched_from": "s_parent"}), "s_branch"),
        )
        db._conn.commit()
        db.append_message("s_branch", role="user",
                          content="branch needle content")

        result = json.loads(session_search(
            query="needle", db=db, current_session_id="s_current",
        ))
        sids = [r["session_id"] for r in result["results"]]
        assert "s_parent" in sids
        assert "s_branch" in sids

    def test_title_and_content_lanes_share_compression_root_semantics(self, db):
        """A generic (non-compression) child of the title-matched session is
        NOT in the title's compression lineage, so the content lane surfaces it
        instead of swallowing it into the title slot's exclusion."""
        db.create_session("t_parent", source="cli")
        db.set_session_title("t_parent", "quartzgate")
        db.append_message("t_parent", role="user",
                          content="irrelevant filler content")
        db.create_session("t_generic_child", source="cli",
                          parent_session_id="t_parent")
        db.append_message("t_generic_child", role="user",
                          content="quartzgate is the key here")

        result = json.loads(session_search(query="quartzgate", db=db, limit=5))
        sids = [r["session_id"] for r in result["results"]]
        assert "t_parent" in sids
        assert "t_generic_child" in sids
        assert result["count"] == 2

    def test_compacted_history_discoverable_even_when_best_hit_is_live(self, db):
        """sort=newest ranks the LIVE needle first; the older compacted needle
        of the same current session must still surface (with the compacted
        anchor) instead of being erased by per-owner dedupe (#68 review)."""
        db.create_session("s_cur", source="cli")
        compacted_id = db.append_message(
            "s_cur", role="user", content="old compacted needle content"
        )
        db.archive_and_compact("s_cur", [
            {"role": "assistant", "content": "summary"},
        ])
        live_id = db.append_message(
            "s_cur", role="user", content="new live needle content"
        )

        result = json.loads(session_search(
            query="needle", db=db, current_session_id="s_cur", sort="newest",
        ))

        assert result["success"] is True
        assert result["count"] >= 1
        hit = result["results"][0]
        assert hit["session_id"] == "s_cur"
        # the anchor is the compacted (archived) message, not the live one
        assert hit["match_message_id"] == compacted_id
        assert hit["match_message_id"] != live_id

    def test_title_and_content_share_compression_root_in_snapshot(self, db):
        """Exact-title exclusion resolves the title's compression lineage
        (same implementation as the winner phase) and excludes it from the
        content lane: a content hit in the title's compression lineage is
        excluded even though its own session id differs from the title session
        id (#68 review round-5)."""
        db.create_session("t_parent", source="cli")
        db.set_session_title("t_parent", "needle-title")
        db.append_message("t_parent", role="user", content="parent filler")
        db.end_session("t_parent", "compression")
        db.create_session("t_child", source="cli",
                          parent_session_id="t_parent")
        db.append_message("t_child", role="user",
                          content="needle-title appears in child")

        result = json.loads(session_search(query="needle-title", db=db, limit=5))

        assert result["success"] is True
        sids = [r["session_id"] for r in result["results"]]
        # Title slot: t_parent. Content lane: t_child is the same compression
        # lineage -> excluded, so the title is the only result.
        assert sids == ["t_parent"]
        assert result.get("truncated") is False

    def test_title_only_limit_one_surfaces_bound_exhaustion(self, db, monkeypatch):
        """A title-only K=1 search must still run the winner phase with the
        current session id.  When the current chain is deeper than B the
        answer is honestly truncated, but the exact-title result is preserved
        (no proof-gate drops it) — the title lane and the content lane are
        independent (#68 review round-5)."""
        import hermes_state_search
        monkeypatch.setattr(hermes_state_search, "_LINEAGE_WORK_BUDGET", 4)
        # current session is the tip of a compression chain deeper than B
        for i in range(6):
            db.create_session(f"cur-{i}", source="cli")
        for i in range(5):
            child, parent = f"cur-{i}", f"cur-{i + 1}"
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
        # a separate exact-title session
        db.create_session("title-session", source="cli")
        db.set_session_title("title-session", "needle-title")
        db.append_message("title-session", role="user",
                          content="title content filler")

        result = json.loads(session_search(
            query="needle-title", db=db, limit=1,
            current_session_id="cur-0",
        ))

        assert result["success"] is True
        # the exact title is preserved; B exhaustion on the current chain is
        # surfaced as truncation, not as a silently complete answer
        assert result["count"] == 1
        assert [r["session_id"] for r in result["results"]] == ["title-session"]
        assert result.get("truncated") is True
        assert "warning" in result

    def test_title_equal_to_current_session_excluded_and_truncated(self, db, monkeypatch):
        """When the exact-title session IS the current session, its title is
        excluded by the current-lineage guard (a session never surfaces as its
        own result) — NOT by any proof gate.  The winner phase still runs and
        honestly surfaces B exhaustion on the >B current chain via truncation
        (#68 review round-5)."""
        import hermes_state_search
        monkeypatch.setattr(hermes_state_search, "_LINEAGE_WORK_BUDGET", 4)
        for i in range(6):
            db.create_session(f"cur-{i}", source="cli")
        for i in range(5):
            child, parent = f"cur-{i}", f"cur-{i + 1}"
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
        db.set_session_title("cur-0", "needle-title")
        db.append_message("cur-0", role="user",
                          content="needle-title content")

        result = json.loads(session_search(
            query="needle-title", db=db, limit=3,
            current_session_id="cur-0",
        ))

        assert result["success"] is True
        # current session is never its own result; the >B current chain is
        # honestly truncated
        assert result["count"] == 0
        assert result["results"] == []
        assert result.get("truncated") is True
        assert "warning" in result

    def test_title_preserved_when_content_lane_unavailable(self, db):
        """When the content FTS lane cannot run (FTS disabled / empty query /
        MATCH error), the winner phase early-returns with no root stats.  The
        title safety gate must NOT kill the already-found exact-title — that
        is the designed title-only fallback, not a B-limited unproven result
        (#68 review round-4)."""
        db._fts_enabled = False
        db.create_session("cur", source="cli")
        db.create_session("title-session", source="cli")
        db.set_session_title("title-session", "foo AND OR bar")
        db.append_message("title-session", role="user",
                          content="some content")

        result = json.loads(session_search(
            query="foo AND OR bar", db=db, limit=1,
            current_session_id="cur",
        ))

        assert result["success"] is True
        assert result["count"] == 1
        assert result["results"][0]["session_id"] == "title-session"
        assert result.get("truncated") is False
        assert "No matching sessions found" not in (result.get("message") or "")


class TestDiscoveryTruncation:
    """B-hit responses are explicitly incomplete, never a silent top-K."""

    def _seed_bound_chain(self, db, n=6):
        """A positive compression chain longer than the monkeypatched budget."""
        base = "bhit"
        for i in range(n):
            db.create_session(f"{base}-{i}", source="cli")
        for i in range(n - 1):
            child, parent = f"{base}-{i}", f"{base}-{i + 1}"
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
        db.append_message(f"{base}-0", role="user",
                          content="bound needle chain")

    def test_bound_hit_returns_truncated_safe_prefix(self, db, monkeypatch):
        import hermes_state_search
        monkeypatch.setattr(hermes_state_search, "_LINEAGE_WORK_BUDGET", 5)
        # Safe winner ranks first and is accepted before the bound chain hits.
        db.create_session("safe", source="cli")
        db.append_message("safe", role="user", content="bound needle safe")
        self._seed_bound_chain(db)

        result = json.loads(session_search(query="bound needle", db=db))

        assert result["success"] is True
        assert result.get("truncated") is True
        assert "warning" in result
        assert result["count"] == 1
        assert [r["session_id"] for r in result["results"]] == ["safe"]

    def test_bound_hit_with_zero_winners_is_incomplete_not_no_match(self, db, monkeypatch):
        import hermes_state_search
        monkeypatch.setattr(hermes_state_search, "_LINEAGE_WORK_BUDGET", 5)
        self._seed_bound_chain(db)

        result = json.loads(session_search(query="bound needle", db=db))

        assert result["success"] is True
        assert result.get("truncated") is True
        assert result["count"] == 0
        assert "No matching sessions found" not in (result.get("message") or "")

    def test_normal_discovery_truncated_false(self, db):
        _seed_modpack_sessions(db)
        result = json.loads(session_search(query="modpack", db=db))
        assert result["success"] is True
        assert result.get("truncated") is False
        assert "warning" not in result
