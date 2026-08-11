"""Fail-first tests for SQL-side session-search winner selection."""

import json
import logging
import threading
import time

import pytest

from hermes_state import SessionDB
from tools.session_search_tool import _order_for_recall, session_search


@pytest.fixture
def db(tmp_path):
    return SessionDB(tmp_path / "state.db")


def _message(db, session_id, content, role="user"):
    return db.append_message(session_id, role=role, content=content)


def _create(db, session_id, source="cli", parent=None):
    db.create_session(session_id, source=source)
    if parent is not None:
        db._conn.execute(
            "UPDATE sessions SET parent_session_id = ? WHERE id = ?",
            (parent, session_id),
        )
        db._conn.commit()


def test_sql_winners_keep_best_hit_per_lineage_and_preserve_candidate_scan(db):
    # Only a positive compression-continuation edge (parent ended by
    # compression, child not a tool session, no parent-bound marker)
    # collapses a child into its parent.  Generic parentage is NOT lineage.
    _create(db, "root", source="cli")
    root_id = _message(db, "root", "needle root")
    db.end_session("root", "compression")
    _create(db, "child", source="cli", parent="root")
    child_id = _message(db, "child", "needle child")
    _message(db, "child", "needle child second")
    _create(db, "other", source="cli")
    other_id = _message(db, "other", "needle other")

    result = db.search_session_winners(
        "needle",
        role_filter=["user"],
        candidate_limit=4,
        result_limit=2,
    )

    winners = result["winners"]
    assert len(winners) == 2
    assert {row["lineage_root_id"] for row in winners} == {"root", "other"}
    assert {row["session_id"] for row in winners} == {"root", "other"}
    assert child_id not in {row["id"] for row in winners}
    assert root_id in {row["id"] for row in winners}
    assert other_id in {row["id"] for row in winners}
    assert result["stats"]["candidate_count"] == 4
    assert result["stats"]["candidate_unique_sessions"] == 3
    assert all("content" not in row for row in winners)
    assert all("context" not in row for row in winners)


def test_sql_winners_match_existing_python_oracle_for_all_temporal_orders(db):
    # The Python oracle resolves lineage with the SAME positive
    # compression-continuation semantics as the SQL winner seam (the
    # public resolve_compression_lineage wrapper), so this test validates
    # the candidate ranking / owner dedupe / early-K integration, not a
    # second, generic-parent definition of lineage.
    _create(db, "oracle-root", source="telegram")
    _message(db, "oracle-root", "oracle needle root")
    db.end_session("oracle-root", "compression")
    _create(db, "oracle-child", source="cron", parent="oracle-root")
    _create(db, "oracle-other", source="cli")
    _create(db, "oracle-cron", source="cron")
    _message(db, "oracle-child", "oracle needle child")
    _message(db, "oracle-other", "oracle needle other")
    _message(db, "oracle-cron", "oracle needle cron")

    for sort in (None, "newest", "oldest"):
        raw = db.search_messages(
            "oracle",
            role_filter=["user"],
            exclude_sources=["subagent", "tool"],
            limit=300,
            sort=sort,
        )
        expected = []
        seen = set()
        for hit in _order_for_recall(raw):
            root = db.resolve_compression_lineage(hit["session_id"])
            if root is None:
                continue
            if root in seen:
                continue
            seen.add(root)
            expected.append(
                (
                    hit["session_id"],
                    root,
                    hit["id"],
                    hit["role"],
                    hit["snippet"],
                    hit["source"],
                )
            )
            if len(expected) == 3:
                break

        actual = db.search_session_winners(
            "oracle",
            role_filter=["user"],
            exclude_sources=["subagent", "tool"],
            candidate_limit=300,
            result_limit=3,
            sort=sort,
        )["winners"]
        actual = [
            (
                row["session_id"],
                row["lineage_root_id"],
                row["id"],
                row["role"],
                row["snippet"],
                row["source"],
            )
            for row in actual
        ]
        assert actual == expected, f"sort={sort!r}"


def test_sql_winners_apply_source_priority_before_final_limit(db):
    _create(db, "interactive", source="telegram")
    _create(db, "cron", source="cron")
    _message(db, "interactive", "priority needle")
    _message(db, "cron", "priority needle")

    result = db.search_session_winners(
        "priority",
        role_filter=["user"],
        candidate_limit=300,
        result_limit=1,
    )

    assert [row["session_id"] for row in result["winners"]] == ["interactive"]
    assert result["winners"][0]["source_priority"] == 0


def test_sql_winners_exclude_current_and_explicit_lineages(db):
    # Production current-session exclusion (raw id re-resolved in-snapshot):
    # the live continuation child is excluded, its compression-ended parent's
    # archived content surfaces, and an explicit excluded root is removed.
    _create(db, "s_parent", source="cli")
    _message(db, "s_parent", "filter needle")
    db.end_session("s_parent", "compression")
    _create(db, "s_current", source="cli", parent="s_parent")
    _message(db, "s_current", "filter needle")
    _create(db, "excluded", source="cli")
    _message(db, "excluded", "filter needle")
    _create(db, "kept", source="cli")
    _message(db, "kept", "filter needle")

    result = db.search_session_winners(
        "filter",
        role_filter=["user"],
        result_limit=10,
        excluded_lineage_roots=("excluded",),
        current_session_id="s_current",
    )

    by_root = {row["lineage_root_id"] for row in result["winners"]}
    assert by_root == {"s_parent", "kept"}
    assert all(row["session_id"] != "s_current" for row in result["winners"])


def test_sql_winners_current_exclusion_generic_child_stays_distinct(db):
    # Generic parentage is NOT compression lineage: a plain child of the
    # current root keeps its own root and must not be swallowed by the
    # current-session exclusion.
    _create(db, "cur", source="cli")
    _create(db, "gen-child", source="cli", parent="cur")
    _message(db, "cur", "filter needle")
    _message(db, "gen-child", "filter needle")

    result = db.search_session_winners(
        "filter",
        role_filter=["user"],
        result_limit=10,
        current_lineage_root="cur",
    )

    assert [row["lineage_root_id"] for row in result["winners"]] == ["gen-child"]


def _set_parent(db, session_id, parent):
    """Point *session_id* at *parent* (FK-safe for dangling parents)."""
    db._conn.execute("PRAGMA foreign_keys = OFF")
    db._conn.execute(
        "UPDATE sessions SET parent_session_id = ? WHERE id = ?",
        (parent, session_id),
    )
    db._conn.commit()
    db._conn.execute("PRAGMA foreign_keys = ON")


def _set_marker(db, session_id, **markers):
    """Write branch/delegate markers into a session's model_config JSON."""
    row = db._conn.execute(
        "SELECT model_config FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    cfg = json.loads(row["model_config"]) if row["model_config"] else {}
    cfg.update(markers)
    db._conn.execute(
        "UPDATE sessions SET model_config = ? WHERE id = ?",
        (json.dumps(cfg), session_id),
    )
    db._conn.commit()


def test_sql_winners_handle_missing_parent_cycle_and_depth_cap(db):
    # #68: missing parents and positive cycles fail closed (the candidate is
    # dropped, never given a fabricated root), and generic parentage is not
    # lineage identity, so lineage_depth_cap no longer participates.
    _create(db, "missing-parent-child", source="cli")
    _set_parent(db, "missing-parent-child", "missing-parent")
    _create(db, "cycle-a", source="cli")
    _create(db, "cycle-b", source="cli")
    for sid in ("cycle-a", "cycle-b"):
        _message(db, sid, "edge needle")
        db.end_session(sid, "compression")
    _set_parent(db, "cycle-a", "cycle-b")
    _set_parent(db, "cycle-b", "cycle-a")
    _create(db, "depth-root", source="cli")
    _create(db, "depth-child", source="cli", parent="depth-root")
    _create(db, "depth-grandchild", source="cli", parent="depth-child")
    for sid in (
        "missing-parent-child",
        "depth-grandchild",
    ):
        _message(db, sid, "edge needle")

    result = db.search_session_winners(
        "edge",
        role_filter=["user"],
        result_limit=10,
        lineage_depth_cap=1,
    )
    by_session = {row["session_id"]: row for row in result["winners"]}

    assert "missing-parent-child" not in by_session
    assert "cycle-a" not in by_session
    assert "cycle-b" not in by_session
    assert by_session["depth-grandchild"]["lineage_root_id"] == "depth-grandchild"


def test_discovery_does_not_hydrate_candidate_context(db, caplog):
    _create(db, "s1", source="cli")
    _create(db, "s2", source="cli")
    _message(db, "s1", "workload needle")
    _message(db, "s2", "workload needle")

    caplog.clear()
    with caplog.at_level(logging.INFO):
        payload = json.loads(session_search(query="workload", limit=2, db=db))

    assert payload["success"] is True
    messages = [record.getMessage() for record in caplog.records]
    assert not any("query_fingerprint=session_search_context" in message for message in messages)
    assert not any("SEARCH_CONTEXT_SESSION" in message for message in messages)
    assert payload["count"] == 2
    assert all(hit["match_message_id"] in {m["id"] for m in hit["messages"]}
               for hit in payload["results"])


def test_title_discovery_does_not_call_get_messages(db, monkeypatch):
    _create(db, "title-session", source="cli")
    db.set_session_title("title-session", "bounded-title")
    _message(db, "title-session", "title anchor content")

    called = False
    original = db.get_messages

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        return original(*args, **kwargs)

    monkeypatch.setattr(db, "get_messages", fail_if_called)
    payload = json.loads(session_search(query="bounded-title", db=db))

    assert payload["success"] is True
    assert payload["count"] == 1
    assert payload["results"][0]["match_message_id"] is not None
    assert called is False


def test_sql_winners_cjk_like_fallback_has_same_lightweight_shape(db):
    _create(db, "cjk-like", source="cli")
    _message(db, "cjk-like", "專案搜尋 needle")
    db._trigram_available = False

    result = db.search_session_winners(
        "專案",
        role_filter=["user"],
        result_limit=1,
    )

    assert result["stats"]["route"] == "like"
    assert result["winners"][0]["session_id"] == "cjk-like"
    assert "content" not in result["winners"][0]


def test_sql_winners_cjk_trigram_route_when_available(db):
    if not db._trigram_available:
        pytest.skip("simple tokenizer/trigram table unavailable in this environment")
    _create(db, "cjk-trigram", source="cli")
    _message(db, "cjk-trigram", "資料庫 winner")

    result = db.search_session_winners(
        "資料庫",
        role_filter=["user"],
        result_limit=1,
    )

    assert result["stats"]["route"] == "trigram"
    assert result["winners"][0]["session_id"] == "cjk-trigram"
    assert "content" not in result["winners"][0]


# =====================================================================
# #68 compression-lineage resolver contract
#
# These tests exercise the observable winner-search contract: positive
# compression-continuation roots, distinct-owner candidates with the best
# preserved anchor, fail-closed missing/cycle semantics, query-local memo
# reuse, early-K, and the B=2000 work budget.
# =====================================================================


def _chain_sessions(db, prefix, n, source="cli"):
    """Create *n* sessions named ``<prefix>-<i>``; returns their ids."""
    ids = [f"{prefix}-{i}" for i in range(n)]
    for sid in ids:
        db.create_session(sid, source=source)
    return ids


def _link_positive_chain(db, ids):
    """Link ``ids[i] -> ids[i+1]`` with positive compression edges.

    Each parent ends by ``'compression'``, so resolving ``ids[0]`` walks
    ``len(ids)`` successful uncached point lookups to the root ``ids[-1]``.
    Call AFTER appending messages (append_message refuses compression-ended
    sessions).
    """
    for i in range(len(ids) - 1):
        child, parent = ids[i], ids[i + 1]
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


def test_sql_winners_branch_marker_to_parent_stays_distinct(db):
    _create(db, "broot", source="cli")
    _message(db, "broot", "needle branch")
    db.end_session("broot", "compression")
    _create(db, "bchild", source="cli", parent="broot")
    _set_marker(db, "bchild", _branched_from="broot")
    _message(db, "bchild", "needle branch")

    winners = db.search_session_winners(
        "needle", role_filter=["user"], result_limit=5
    )["winners"]
    by_root = {row["lineage_root_id"] for row in winners}
    assert {"broot", "bchild"} <= by_root


def test_sql_winners_delegate_marker_to_parent_stays_distinct(db):
    _create(db, "droot", source="cli")
    _message(db, "droot", "needle delegate")
    db.end_session("droot", "compression")
    _create(db, "dchild", source="cli", parent="droot")
    _set_marker(db, "dchild", _delegate_from="droot")
    _message(db, "dchild", "needle delegate")

    winners = db.search_session_winners(
        "needle", role_filter=["user"], result_limit=5
    )["winners"]
    by_root = {row["lineage_root_id"] for row in winners}
    assert {"droot", "dchild"} <= by_root


def test_sql_winners_tool_child_stays_distinct(db):
    _create(db, "troot", source="cli")
    _message(db, "troot", "needle toolchild")
    db.end_session("troot", "compression")
    _create(db, "tchild", source="tool", parent="troot")
    _message(db, "tchild", "needle toolchild")

    winners = db.search_session_winners(
        "needle", role_filter=["user"], result_limit=5
    )["winners"]
    by_root = {row["lineage_root_id"] for row in winners}
    assert {"troot", "tchild"} <= by_root


def test_sql_winners_foreign_marker_does_not_block_continuation(db):
    # A branch/delegate marker pointing somewhere OTHER than the parent is
    # foreign and must not disqualify a real compression-continuation edge.
    _create(db, "froot", source="cli")
    _message(db, "froot", "needle foreign")
    db.end_session("froot", "compression")
    _create(db, "fchild", source="cli", parent="froot")
    _set_marker(db, "fchild", _branched_from="somewhere-else")
    _message(db, "fchild", "needle foreign")

    winners = db.search_session_winners(
        "needle", role_filter=["user"], result_limit=5
    )["winners"]
    assert {row["lineage_root_id"] for row in winners} == {"froot"}
    assert all(row["session_id"] != "fchild" for row in winners)


def test_sql_winners_owner_dedupe_preserves_best_anchor(db):
    # Two raw hits owned by one session become ONE owner candidate whose
    # anchor is the earliest/highest-ranked raw hit (first by candidate
    # order), not the last one.
    _create(db, "multi", source="cli")
    first_id = _message(db, "multi", "needle anchor first")
    _message(db, "multi", "needle anchor second")
    _create(db, "multi2", source="cli")
    _message(db, "multi2", "needle anchor other")

    winners = db.search_session_winners(
        "needle", role_filter=["user"], result_limit=5
    )["winners"]
    multi_winner = next(w for w in winners if w["session_id"] == "multi")
    assert multi_winner["id"] == first_id
    assert len(winners) == 2


def test_sql_winners_early_k_stops_before_trailing_candidates(db):
    # K=1 must stop after the first accepted root even when a pathological
    # candidate sits later in the ranked scan.
    _create(db, "e1", source="cli")
    _message(db, "e1", "early needle")
    _create(db, "e2", source="cli")
    db.end_session("e2", "compression")
    for i in range(30):
        _create(db, f"e2-child-{i}", source="cli", parent="e2")
        _message(db, f"e2-child-{i}", "early needle")

    result = db.search_session_winners(
        "early", role_filter=["user"], candidate_limit=100, result_limit=1
    )
    assert len(result["winners"]) == 1
    assert result["stats"]["lineage_candidates_inspected"] <= 2


def test_sql_winners_missing_parent_fails_closed_and_memo_reuses(db):
    # A dangling parent fails closed (no fabricated root).  A descendant that
    # enters the already-proven bad path reuses the unresolved memo at zero
    # extra lookup cost.
    _create(db, "m-bad", source="cli")
    _message(db, "m-bad", "needle missing")
    db.end_session("m-bad", "compression")
    _set_parent(db, "m-bad", "m-missing")
    _create(db, "m-bad-child", source="cli", parent="m-bad")
    _message(db, "m-bad-child", "needle missing")
    _create(db, "m-ok", source="cli")
    _message(db, "m-ok", "needle missing")

    result = db.search_session_winners(
        "needle", role_filter=["user"], result_limit=10
    )
    winners = result["winners"]
    assert [row["session_id"] for row in winners] == ["m-ok"]
    # m-bad resolves with one lookup; m-bad-child walks one lookup then hits
    # m-bad's proven-unresolved memo at zero additional work.
    assert result["stats"]["lineage_work"] == 3  # m-bad + m-bad-child + m-ok
    assert result["stats"]["lineage_memo_hits"] >= 1


def test_sql_winners_two_node_cycle_fails_closed(db):
    # Every edge must be a positive compression edge for the cycle to be
    # reachable, so each member ends by compression (messages first).
    _create(db, "c-a", source="cli")
    _create(db, "c-b", source="cli")
    _message(db, "c-a", "needle cycle")
    _message(db, "c-b", "needle cycle")
    db.end_session("c-a", "compression")
    db.end_session("c-b", "compression")
    _set_parent(db, "c-a", "c-b")
    _set_parent(db, "c-b", "c-a")

    winners = db.search_session_winners(
        "needle", role_filter=["user"], result_limit=10
    )["winners"]
    assert winners == []


def test_sql_winners_long_cycle_fails_closed(db):
    _create(db, "l-a", source="cli")
    _create(db, "l-b", source="cli")
    _create(db, "l-c", source="cli")
    for sid in ("l-a", "l-b", "l-c"):
        _message(db, sid, "needle cycle")
        db.end_session(sid, "compression")
    _set_parent(db, "l-a", "l-b")
    _set_parent(db, "l-b", "l-c")
    _set_parent(db, "l-c", "l-a")

    winners = db.search_session_winners(
        "needle", role_filter=["user"], result_limit=10
    )["winners"]
    assert winners == []


def test_sql_winners_tail_entering_cycle_fails_closed(db):
    _create(db, "t-a", source="cli")
    _create(db, "t-b", source="cli")
    _create(db, "t-c", source="cli")
    _message(db, "t-a", "needle cycle")
    _message(db, "t-b", "needle cycle")
    _message(db, "t-c", "needle cycle")
    db.end_session("t-b", "compression")
    db.end_session("t-c", "compression")
    _set_parent(db, "t-a", "t-b")
    _set_parent(db, "t-b", "t-c")
    _set_parent(db, "t-c", "t-b")

    winners = db.search_session_winners(
        "needle", role_filter=["user"], result_limit=10
    )["winners"]
    assert winners == []


def test_sql_winners_positive_lineage_memo_reuse(db):
    # A 15-session positive lineage (observed depth-14/size-15 tail): every
    # candidate in the lineage resolves to the same root, and after the
    # first candidate all later ones are memo hits (zero extra lookups).
    ids = _chain_sessions(db, "memo", 15)
    for sid in ids:
        _message(db, sid, "needle memo")
    _link_positive_chain(db, ids)

    result = db.search_session_winners(
        "needle", role_filter=["user"], candidate_limit=100, result_limit=10
    )
    winners = result["winners"]
    assert len(winners) == 1
    assert winners[0]["lineage_root_id"] == ids[-1]
    # 15 distinct owner candidates: first walk is 15 lookups, the rest are
    # memo hits -> 15 successful uncached row fetches total.
    assert result["stats"]["lineage_work"] == 15
    assert result["stats"]["lineage_memo_hits"] == 14
    assert result["stats"]["lineage_memo_entries"] == 15


def test_sql_winners_cycle_at_work_bound_is_cycle_not_bound_hit(
    db, monkeypatch
):
    # Cycle becomes provable from the traversal-local seen-set when work has
    # already consumed the whole budget: classify as cycle, NOT bound-hit.
    monkeypatch.setattr("hermes_state_search._LINEAGE_WORK_BUDGET", 3)
    _create(db, "cb-a", source="cli")
    _create(db, "cb-b", source="cli")
    _create(db, "cb-c", source="cli")
    for sid in ("cb-a", "cb-b", "cb-c"):
        _message(db, sid, "needle cycle")
        db.end_session(sid, "compression")
    _set_parent(db, "cb-a", "cb-b")
    _set_parent(db, "cb-b", "cb-c")
    _set_parent(db, "cb-c", "cb-a")

    result = db.search_session_winners(
        "needle", role_filter=["user"], result_limit=10
    )
    assert result["winners"] == []
    assert result["stats"]["lineage_bound_hit"] is False
    assert result["stats"]["lineage_work"] == 3


def test_sql_winners_bound_hit_before_cycle_proof(db, monkeypatch):
    # Proving the cycle would need one MORE uncached lookup after the budget
    # is exhausted -> bound-hit, and the partial path must not be memoized as
    # unresolved (a fresh query resolves it).
    monkeypatch.setattr("hermes_state_search._LINEAGE_WORK_BUDGET", 4)
    ids = _chain_sessions(db, "bp", 5)
    for sid in ids:
        _message(db, sid, "needle cycle")
    _link_positive_chain(db, ids)
    # Close the 5-node chain into a cycle: bp-4 -> bp-0 (positive edge).
    db._conn.execute(
        "UPDATE sessions SET end_reason = 'compression' WHERE id = ?",
        (ids[0],),
    )
    db._conn.commit()
    _set_parent(db, ids[-1], ids[0])

    result = db.search_session_winners(
        "needle", role_filter=["user"], result_limit=10
    )
    assert result["winners"] == []
    assert result["stats"]["lineage_bound_hit"] is True
    assert result["stats"]["lineage_work"] == 4


def test_sql_winners_root_resolved_exactly_at_work_bound(db, monkeypatch):
    # A root resolved on successful lookup number B succeeds (boundary is
    # inclusive of legitimate completed work).
    monkeypatch.setattr("hermes_state_search._LINEAGE_WORK_BUDGET", 5)
    ids = _chain_sessions(db, "b5", 5)
    _message(db, ids[0], "needle exact")
    _link_positive_chain(db, ids)

    result = db.search_session_winners(
        "needle", role_filter=["user"], result_limit=10
    )
    assert result["winners"][0]["lineage_root_id"] == ids[-1]
    assert result["stats"]["lineage_work"] == 5
    assert result["stats"]["lineage_bound_hit"] is False


def test_sql_winners_lookup_beyond_work_bound_truncates(db, monkeypatch):
    # A resolution that requires lookup B+1 stops BEFORE that lookup,
    # flags bound-hit, and returns only already-proven safe winners.
    monkeypatch.setattr("hermes_state_search._LINEAGE_WORK_BUDGET", 5)
    ids = _chain_sessions(db, "b6", 6)
    _message(db, ids[0], "needle trunc")
    _link_positive_chain(db, ids)
    _create(db, "trunc-safe", source="cli")
    _message(db, "trunc-safe", "needle trunc")

    result = db.search_session_winners(
        "needle", role_filter=["user"], result_limit=10
    )
    assert result["stats"]["lineage_bound_hit"] is True
    # The bound is reached on the first (higher-ranked) candidate, so the
    # scan stops rather than accepting the later safe candidate.
    assert result["winners"] == []


def test_sql_winners_bound_exhaustion_does_not_poison_memo(db, monkeypatch):
    monkeypatch.setattr("hermes_state_search._LINEAGE_WORK_BUDGET", 3)
    ids = _chain_sessions(db, "poison", 5)
    _message(db, ids[0], "needle poison")
    _link_positive_chain(db, ids)

    first = db.search_session_winners(
        "needle", role_filter=["user"], result_limit=10
    )
    assert first["stats"]["lineage_bound_hit"] is True

    # A fresh query (new memo + restored budget) resolves the same chain
    # normally, proving the exhausted partial path was never memoized.
    monkeypatch.setattr("hermes_state_search._LINEAGE_WORK_BUDGET", 2000)
    second = db.search_session_winners(
        "needle", role_filter=["user"], result_limit=10
    )
    assert second["stats"]["lineage_bound_hit"] is False
    assert second["winners"][0]["lineage_root_id"] == ids[-1]


def test_sql_winners_stats_report_work_and_bound(db):
    _create(db, "st-root", source="cli")
    _message(db, "st-root", "needle stats")
    db.end_session("st-root", "compression")
    _create(db, "st-child", source="cli", parent="st-root")
    _message(db, "st-child", "needle stats")
    _create(db, "st-other", source="cli")
    _message(db, "st-other", "needle stats")

    result = db.search_session_winners(
        "needle", role_filter=["user"], result_limit=5
    )
    stats = result["stats"]
    assert stats["candidate_count"] == 3
    assert stats["candidate_unique_sessions"] == 3
    assert stats["lineage_count"] == 2
    assert stats["winner_count"] == 2
    assert stats["lineage_work"] >= 1
    assert stats["lineage_candidates_inspected"] == 3
    assert stats["lineage_bound_hit"] is False


def test_sql_winners_k1_k3_k10_early_stop(db):
    _create(db, "k0", source="cli")
    db.end_session("k0", "compression")
    for i in range(12):
        _create(db, f"k0-child-{i}", source="cli", parent="k0")
        _message(db, f"k0-child-{i}", "needle k")
    for i in range(1, 15):
        _create(db, f"k{i}", source="cli")
        _message(db, f"k{i}", "needle k")

    for k in (1, 3, 10):
        result = db.search_session_winners(
            "needle", role_filter=["user"], candidate_limit=100, result_limit=k
        )
        assert len(result["winners"]) == k
        assert result["stats"]["lineage_bound_hit"] is False


def test_sql_winners_long_acyclic_chain_cut_by_budget_not_depth_cap(
    db, monkeypatch,
):
    # A long acyclic chain is cut by the work budget, not by any semantic
    # depth cap.  The resolver is iterative + memoized, so a chain longer
    # than B simply stops at B successful lookups.  (Fixture stays small by
    # monkeypatching B; the default 2000 is just that same constant.)
    monkeypatch.setattr("hermes_state_search._LINEAGE_WORK_BUDGET", 40)
    n = 50
    now = time.time()
    base = "path"
    db._conn.executemany(
        "INSERT INTO sessions (id, source, started_at) VALUES (?, ?, ?)",
        [(f"{base}-{i}", "cli", now) for i in range(n)],
    )
    for i in range(n - 1):
        child, parent = f"{base}-{i}", f"{base}-{i + 1}"
        db._conn.execute(
            "UPDATE sessions SET parent_session_id = ?, "
            "end_reason = 'compression' WHERE id = ?",
            (parent, child),
        )
    db._conn.commit()
    _message(db, f"{base}-0", "needle path")

    result = db.search_session_winners(
        "needle", role_filter=["user"], result_limit=10
    )
    assert result["stats"]["lineage_bound_hit"] is True
    assert result["stats"]["lineage_work"] == 40
    assert result["winners"] == []


# =====================================================================
# #68 review round: title in-snapshot parity, compacted-history anchor
# fallback, and the mandated acceptance matrix (real B boundaries,
# concurrency snapshot, 10k chain, internal-K stress).
# =====================================================================


def _bulk_positive_chain(db, prefix, n, source="cli"):
    """Bulk-build a positive compression chain of *n* sessions.

    SessionDB's writer connection is autocommit, so a per-row insert would
    fsync every row (~40x slower).  Wrapping the inserts + parent links in
    one explicit BEGIN/COMMIT keeps the fixture fast enough for real
    B=2000-boundary chains.
    """
    base = prefix
    now = time.time()
    db._conn.execute("BEGIN")
    db._conn.executemany(
        "INSERT INTO sessions (id, source, started_at) VALUES (?, ?, ?)",
        [(f"{base}-{i}", source, now) for i in range(n)],
    )
    for i in range(n - 1):
        child, parent = f"{base}-{i}", f"{base}-{i + 1}"
        db._conn.execute(
            "UPDATE sessions SET parent_session_id = ? WHERE id = ?",
            (parent, child),
        )
        db._conn.execute(
            "UPDATE sessions SET end_reason = 'compression' WHERE id = ?",
            (parent,),
        )
    db._conn.execute("COMMIT")
    return [f"{base}-{i}" for i in range(n)]


def test_sql_winners_title_root_excludes_compression_lineage(db):
    # Exact-title exclusion arrives as the title's resolved root in
    # excluded_lineage_roots (resolved by the caller with the same
    # compression-lineage implementation): the whole compression lineage is
    # fully excluded from content winners.
    _create(db, "troot", source="cli")
    _message(db, "troot", "needle title root")
    db.end_session("troot", "compression")
    _create(db, "tchild", source="cli", parent="troot")
    _message(db, "tchild", "needle title child")
    _create(db, "other", source="cli")
    _message(db, "other", "needle title other")

    result = db.search_session_winners(
        "needle", role_filter=["user"], result_limit=5,
        excluded_lineage_roots=("troot",),
    )
    # both the title root and its compression child are excluded
    assert [row["session_id"] for row in result["winners"]] == ["other"]
    assert result["stats"]["lineage_bound_hit"] is False


def test_sql_winners_current_lineage_compacted_history_anchor_fallback(db):
    # In the current lineage, the NEWEST hit is live (current-excluded), but
    # the older compacted hit of the same owner must still surface AS THE
    # ANCHOR — per-owner dedupe must never erase compacted history.
    db.create_session("cur", source="cli")
    compacted_id = _message(db, "cur", "old compacted needle")
    db.archive_and_compact("cur", [{"role": "assistant", "content": "summary"}])
    _message(db, "cur", "new live needle")

    result = db.search_session_winners(
        "needle", role_filter=["user", "assistant"], result_limit=5,
        sort="newest", current_session_id="cur",
    )
    winners = result["winners"]
    assert len(winners) == 1
    assert winners[0]["session_id"] == "cur"
    assert winners[0]["id"] == compacted_id


def test_sql_winners_default_budget_1999_succeeds(db):
    # A root resolved on successful lookup 1999 succeeds with the default B.
    ids = _bulk_positive_chain(db, "b1999", 1999)
    _message(db, ids[0], "needle 1999")

    result = db.search_session_winners(
        "needle", role_filter=["user"], result_limit=5
    )
    assert result["winners"][0]["lineage_root_id"] == ids[-1]
    assert result["stats"]["lineage_work"] == 1999
    assert result["stats"]["lineage_bound_hit"] is False


def test_sql_winners_default_budget_2000_succeeds(db):
    # A root resolved on successful lookup 2000 (the boundary) succeeds.
    ids = _bulk_positive_chain(db, "b2000", 2000)
    _message(db, ids[0], "needle 2000")

    result = db.search_session_winners(
        "needle", role_filter=["user"], result_limit=5
    )
    assert result["winners"][0]["lineage_root_id"] == ids[-1]
    assert result["stats"]["lineage_work"] == 2000
    assert result["stats"]["lineage_bound_hit"] is False


def test_sql_winners_default_budget_2001_truncates(db):
    # A resolution that would need lookup 2001 stops BEFORE it with the
    # default B, flags bound-hit, and returns no fabricated winner.
    ids = _bulk_positive_chain(db, "b2001", 2001)
    _message(db, ids[0], "needle 2001")

    result = db.search_session_winners(
        "needle", role_filter=["user"], result_limit=5
    )
    assert result["stats"]["lineage_bound_hit"] is True
    assert result["winners"] == []


def test_sql_winners_10k_acyclic_chain_cut_by_default_budget(db):
    # A real 10k-node acyclic chain is cut by the default B=2000 work budget,
    # not by any semantic depth cap.
    ids = _bulk_positive_chain(db, "path10k", 10000)
    _message(db, ids[0], "needle path")

    result = db.search_session_winners(
        "needle", role_filter=["user"], result_limit=5
    )
    assert result["stats"]["lineage_bound_hit"] is True
    assert result["stats"]["lineage_work"] == 2000
    assert result["winners"] == []


def test_sql_winners_internal_k_stress(db):
    # Larger internal K (beyond the public K<=10 tool path) is a defensive DB
    # contract, not the normal product path.
    for i in range(60):
        _create(db, f"ik-{i}", source="cli")
        _message(db, f"ik-{i}", "needle internal k")

    result = db.search_session_winners(
        "needle", role_filter=["user"], candidate_limit=200, result_limit=60
    )
    assert len(result["winners"]) == 60
    assert result["stats"]["lineage_bound_hit"] is False


def test_sql_winners_lineage_lookups_in_one_explicit_read_transaction(
    db, monkeypatch,
):
    # Candidate selection plus every lineage point lookup for one logical
    # search must run inside ONE explicit read transaction (one coherent
    # logical snapshot), not separate autocommit statements.
    original = SessionDB._resolve_compression_lineage_on_conn
    observed = []

    def spy(self, conn, session_id, state):
        observed.append(conn.in_transaction)
        return original(self, conn, session_id, state)

    monkeypatch.setattr(SessionDB, "_resolve_compression_lineage_on_conn", spy)
    _create(db, "root", source="cli")
    _message(db, "root", "snap needle")
    db.end_session("root", "compression")
    _create(db, "child", source="cli", parent="root")
    _message(db, "child", "snap needle")

    db.search_session_winners("snap", role_filter=["user"], result_limit=5)

    assert observed
    assert all(observed)


def test_sql_winners_concurrent_writer_does_not_tear_winner_snapshot(
    db, monkeypatch,
):
    # A writer hammering the locked write path during a winner search cannot
    # interleave into the winner snapshot: the search observes a coherent
    # root set from one logical moment.
    original = SessionDB._resolve_compression_lineage_on_conn

    def slow(self, conn, session_id, state):
        time.sleep(0.02)
        return original(self, conn, session_id, state)

    monkeypatch.setattr(SessionDB, "_resolve_compression_lineage_on_conn", slow)
    _create(db, "root", source="cli")
    _message(db, "root", "snap needle")
    db.end_session("root", "compression")
    _create(db, "child", source="cli", parent="root")
    _message(db, "child", "snap needle")
    _create(db, "other", source="cli")
    _message(db, "other", "snap needle")

    def writer():
        for i in range(10):
            db.create_session(f"cw-{i}", source="cli")

    t = threading.Thread(target=writer)
    t.start()
    result = db.search_session_winners("snap", role_filter=["user"], result_limit=5)
    t.join()

    roots = {row["lineage_root_id"] for row in result["winners"]}
    assert roots == {"root", "other"}
    assert result["stats"]["lineage_bound_hit"] is False


def test_sql_winners_candidates_inspected_counts_distinct_owners(db):
    # The resolver consumes ranked DISTINCT owner candidates: two raw hits
    # owned by one session count as ONE inspected candidate, even though the
    # compacted-anchor fallback keeps all of the owner's hits available.
    _create(db, "m1", source="cli")
    _message(db, "m1", "needle first")
    _message(db, "m1", "needle second")
    _create(db, "m2", source="cli")
    _message(db, "m2", "needle other")

    result = db.search_session_winners(
        "needle", role_filter=["user"], result_limit=5
    )
    assert result["stats"]["candidate_count"] == 3        # raw hits
    assert result["stats"]["candidate_unique_sessions"] == 2  # distinct owners
    assert result["stats"]["lineage_candidates_inspected"] == 2  # not 3
    assert len(result["winners"]) == 2


def test_sql_winners_displayable_anchor_ordering_not_live_rank(db):
    # Winner ordering follows the rank of each owner's FIRST DISPLAYABLE
    # anchor.  With K=1 and 'cur-live(#1) -> other(#2) -> cur-compacted(#3)',
    # the higher-ranked displayable 'other' must win — cur's live #1 hit is
    # current-excluded and must NOT let cur's later compacted anchor jump
    # ahead of a genuinely higher-ranked winner.
    db.create_session("cur", source="cli")
    compacted_id = _message(db, "cur", "old compacted needle")
    db.archive_and_compact("cur", [{"role": "assistant", "content": "summary"}])
    live_id = _message(db, "cur", "new live needle")
    _create(db, "other-s", source="cli")
    other_id = _message(db, "other-s", "needle other")
    now = time.time()
    # newest-first: cur-live(#1) -> other(#2) -> cur-compacted(#3)
    db._conn.execute("UPDATE messages SET timestamp = ? WHERE id = ?", (now - 100, live_id))
    db._conn.execute("UPDATE messages SET timestamp = ? WHERE id = ?", (now - 200, other_id))
    db._conn.execute("UPDATE messages SET timestamp = ? WHERE id = ?", (now - 300, compacted_id))
    db._conn.commit()

    result = db.search_session_winners(
        "needle", role_filter=["user"], result_limit=1, sort="newest",
        current_session_id="cur",
    )
    winners = result["winners"]
    assert len(winners) == 1
    assert winners[0]["session_id"] == "other-s"
    assert winners[0]["id"] == other_id
