#!/usr/bin/env python3
"""Apply the small #62 wiring edits that GitHub Contents cannot patch atomically.

Temporary helper: after it succeeds, remove this script before committing.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{label}: expected exactly one occurrence of replacement target, found {count}"
        )
    return text.replace(old, new, 1)


def _replace_regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
    new_text, count = re.subn(pattern, replacement, text, count=1, flags=re.DOTALL)
    if count != 1:
        raise RuntimeError(f"{label}: regex replacement target not found exactly once")
    return new_text


def main() -> int:
    state_path = ROOT / "hermes_state.py"
    tool_path = ROOT / "tools" / "session_search_tool.py"
    test_path = ROOT / "tests" / "test_session_search_sql_winners.py"

    state = state_path.read_text(encoding="utf-8")
    tool = tool_path.read_text(encoding="utf-8")
    tests = test_path.read_text(encoding="utf-8")

    state = _replace_once(
        state,
        "from hermes_state_search import SessionSearchMixin\n",
        "from hermes_state_lineage import SessionSearchMixin\n",
        "hermes_state.py import",
    )

    tool = _replace_regex_once(
        tool,
        r"def _resolve_lineage\(db, session_id: str\) -> str:\n.*?\n\n(?=def _is_compression_ended)",
        """def _resolve_lineage(db, session_id: str) -> str:
    \"\"\"Resolve the session-search compression lineage root.

    Production ``SessionDB`` exposes the same positive compression-continuation
    resolver used by SQL winner selection. Keep the old generic-parent helper
    only as a compatibility fallback for small test doubles / older DB objects.
    A failed production resolution is conservative: keep the session separate
    instead of broadening current/title exclusion to an unproven ancestor.
    \"\"\"
    if not session_id:
        return session_id
    resolver = getattr(db, \"resolve_compression_lineage\", None)
    if resolver is None:
        return _resolve_to_parent(db, session_id)[0]
    try:
        return resolver(session_id) or session_id
    except Exception:
        logging.debug(
            \"compression lineage resolution failed for %s\",
            session_id,
            exc_info=True,
        )
        return session_id


""",
        "tools/session_search_tool.py::_resolve_lineage",
    )

    tests = _replace_once(
        tests,
        "from tools.session_search_tool import _order_for_recall, _resolve_to_parent, session_search\n",
        "from tools.session_search_tool import _order_for_recall, _resolve_lineage, session_search\n",
        "test import",
    )

    marker = """def _mark_compression_end(db, session_id):
    db._conn.execute(
        \"UPDATE sessions SET end_reason = 'compression' WHERE id = ?\",
        (session_id,),
    )
    db._conn.commit()


"""
    tests = _replace_once(
        tests,
        "\n\ndef test_sql_winners_keep_best_hit_per_lineage_and_preserve_candidate_scan(db):\n",
        "\n\n" + marker
        + "def test_sql_winners_keep_best_hit_per_lineage_and_preserve_candidate_scan(db):\n",
        "test compression helper insertion",
    )
    tests = _replace_once(
        tests,
        '    _create(db, "root", source="cli")\n    _create(db, "child", source="cli", parent="root")\n',
        '    _create(db, "root", source="cli")\n'
        '    _mark_compression_end(db, "root")\n'
        '    _create(db, "child", source="cli", parent="root")\n',
        "root compression fixture",
    )
    tests = _replace_once(
        tests,
        '    _create(db, "oracle-root", source="telegram")\n'
        '    _create(db, "oracle-child", source="cron", parent="oracle-root")\n',
        '    _create(db, "oracle-root", source="telegram")\n'
        '    _mark_compression_end(db, "oracle-root")\n'
        '    _create(db, "oracle-child", source="cron", parent="oracle-root")\n',
        "oracle compression fixture",
    )
    tests = _replace_once(
        tests,
        '            root = _resolve_to_parent(db, hit["session_id"])\n',
        '            root = _resolve_lineage(db, hit["session_id"])\n',
        "oracle resolver",
    )
    tests = _replace_once(
        tests,
        '    _create(db, "current-root", source="cli")\n'
        '    _create(db, "current-child", source="cli", parent="current-root")\n',
        '    _create(db, "current-root", source="cli")\n'
        '    _mark_compression_end(db, "current-root")\n'
        '    _create(db, "current-child", source="cli", parent="current-root")\n',
        "current compression fixture",
    )

    tests = _replace_regex_once(
        tests,
        r"def test_sql_winners_handle_missing_parent_cycle_and_depth_cap\(db\):\n.*?\n\n(?=def test_discovery_does_not_hydrate_candidate_context)",
        """def test_sql_winners_fail_closed_on_missing_parent_and_positive_cycle(db):
    _create(db, \"missing-parent-child\", source=\"cli\")
    db._conn.execute(\"PRAGMA foreign_keys = OFF\")
    db._conn.execute(
        \"UPDATE sessions SET parent_session_id = ? WHERE id = ?\",
        (\"missing-parent\", \"missing-parent-child\"),
    )
    _create(db, \"cycle-a\", source=\"cli\")
    _create(db, \"cycle-b\", source=\"cli\")
    db._conn.execute(
        \"UPDATE sessions SET parent_session_id = ?, end_reason = 'compression' WHERE id = ?\",
        (\"cycle-b\", \"cycle-a\"),
    )
    db._conn.execute(
        \"UPDATE sessions SET parent_session_id = ?, end_reason = 'compression' WHERE id = ?\",
        (\"cycle-a\", \"cycle-b\"),
    )
    db._conn.commit()
    db._conn.execute(\"PRAGMA foreign_keys = ON\")
    _create(db, \"good-root\", source=\"cli\")
    for sid in (
        \"missing-parent-child\",
        \"cycle-a\",
        \"cycle-b\",
        \"good-root\",
    ):
        _message(db, sid, \"edge needle\")

    result = db.search_session_winners(
        \"edge\",
        role_filter=[\"user\"],
        result_limit=10,
    )
    by_session = {row[\"session_id\"]: row for row in result[\"winners\"]}

    assert \"missing-parent-child\" not in by_session
    assert \"cycle-a\" not in by_session
    assert \"cycle-b\" not in by_session
    assert by_session[\"good-root\"][\"lineage_root_id\"] == \"good-root\"
    assert result[\"stats\"][\"lineage_bound_hit\"] is False


def test_sql_winners_keep_branch_delegate_and_tool_parentage_separate(db):
    _create(db, \"compressed-root\", source=\"cli\")
    _mark_compression_end(db, \"compressed-root\")
    _create(db, \"continuation\", source=\"cli\", parent=\"compressed-root\")
    _create(db, \"branch\", source=\"cli\", parent=\"compressed-root\")
    _create(db, \"delegate\", source=\"cli\", parent=\"compressed-root\")
    _create(db, \"tool-child\", source=\"tool\", parent=\"compressed-root\")
    _create(db, \"foreign-marker\", source=\"cli\", parent=\"compressed-root\")
    db._conn.execute(
        \"UPDATE sessions SET model_config = ? WHERE id = ?\",
        (json.dumps({\"_branched_from\": \"compressed-root\"}), \"branch\"),
    )
    db._conn.execute(
        \"UPDATE sessions SET model_config = ? WHERE id = ?\",
        (json.dumps({\"_delegate_from\": \"compressed-root\"}), \"delegate\"),
    )
    db._conn.execute(
        \"UPDATE sessions SET model_config = ? WHERE id = ?\",
        (json.dumps({\"_delegate_from\": \"some-other-parent\"}), \"foreign-marker\"),
    )
    db._conn.commit()
    for sid in (
        \"compressed-root\",
        \"continuation\",
        \"branch\",
        \"delegate\",
        \"tool-child\",
        \"foreign-marker\",
    ):
        _message(db, sid, \"semantic needle\")

    result = db.search_session_winners(
        \"semantic\",
        role_filter=[\"user\"],
        result_limit=10,
    )
    roots = {row[\"lineage_root_id\"] for row in result[\"winners\"]}

    assert roots == {\"compressed-root\", \"branch\", \"delegate\", \"tool-child\"}
    assert _resolve_lineage(db, \"continuation\") == \"compressed-root\"
    assert _resolve_lineage(db, \"foreign-marker\") == \"compressed-root\"
    assert _resolve_lineage(db, \"branch\") == \"branch\"
    assert _resolve_lineage(db, \"delegate\") == \"delegate\"
    assert _resolve_lineage(db, \"tool-child\") == \"tool-child\"


""",
        "missing/cycle + semantic integration tests",
    )

    transformed = {
        state_path: state,
        tool_path: tool,
        test_path: tests,
    }
    for path, text in transformed.items():
        compile(text, str(path), "exec")

    for path, text in transformed.items():
        path.write_text(text, encoding="utf-8")

    print("Applied #62 session-lineage wiring and syntax-checked changed Python files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
