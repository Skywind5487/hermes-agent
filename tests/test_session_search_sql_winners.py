"""Fail-first tests for SQL-side session-search winner selection."""

import json
import logging

import pytest

from hermes_state import SessionDB
from tools.session_search_tool import _order_for_recall, _resolve_to_parent, session_search


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
    _create(db, "root", source="cli")
    _create(db, "child", source="cli", parent="root")
    _create(db, "other", source="cli")
    root_id = _message(db, "root", "needle root")
    child_id = _message(db, "child", "needle child")
    _message(db, "child", "needle child second")
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
    _create(db, "oracle-root", source="telegram")
    _create(db, "oracle-child", source="cron", parent="oracle-root")
    _create(db, "oracle-other", source="cli")
    _create(db, "oracle-cron", source="cron")
    _message(db, "oracle-root", "oracle needle root")
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
            root = _resolve_to_parent(db, hit["session_id"])
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
    _create(db, "current-root", source="cli")
    _create(db, "current-child", source="cli", parent="current-root")
    _create(db, "excluded", source="cli")
    _create(db, "kept", source="cli")
    for sid in ("current-root", "current-child", "excluded", "kept"):
        _message(db, sid, "filter needle")

    result = db.search_session_winners(
        "filter",
        role_filter=["user"],
        result_limit=10,
        excluded_lineage_roots=("excluded",),
        current_lineage_root="current-root",
    )

    assert [row["lineage_root_id"] for row in result["winners"]] == ["kept"]


def test_sql_winners_handle_missing_parent_cycle_and_depth_cap(db):
    _create(db, "missing-parent-child", source="cli")
    db._conn.execute("PRAGMA foreign_keys = OFF")
    db._conn.execute(
        "UPDATE sessions SET parent_session_id = ? WHERE id = ?",
        ("missing-parent", "missing-parent-child"),
    )
    db._conn.commit()
    db._conn.execute("PRAGMA foreign_keys = ON")
    _create(db, "cycle-a", source="cli")
    _create(db, "cycle-b", source="cli")
    db._conn.execute("PRAGMA foreign_keys = OFF")
    db._conn.execute(
        "UPDATE sessions SET parent_session_id = ? WHERE id = ?",
        ("cycle-b", "cycle-a"),
    )
    db._conn.execute(
        "UPDATE sessions SET parent_session_id = ? WHERE id = ?",
        ("cycle-a", "cycle-b"),
    )
    db._conn.commit()
    db._conn.execute("PRAGMA foreign_keys = ON")
    _create(db, "depth-root", source="cli")
    _create(db, "depth-child", source="cli", parent="depth-root")
    _create(db, "depth-grandchild", source="cli", parent="depth-child")
    for sid in (
        "missing-parent-child",
        "cycle-a",
        "cycle-b",
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

    assert by_session["missing-parent-child"]["lineage_root_id"] == "missing-parent-child"
    assert by_session["cycle-a"]["lineage_root_id"] in {"cycle-a", "cycle-b"}
    assert by_session["depth-grandchild"]["lineage_root_id"] == "depth-child"


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
