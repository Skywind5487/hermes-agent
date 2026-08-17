#!/usr/bin/env python3
"""Compare compression-continuation edge predicates on the frozen corpus.

This is a read-only research audit.  It intentionally does NOT pick one predicate
silently: upstream has used several continuation tests over time, and current
paths still differ.  The report shows how each predicate changes production
edge counts and extreme lineage topology before #54 relies on those numbers.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from production_profile import (
    AUTHORITATIVE_PATH,
    AUTHORITATIVE_SHA256,
    enforce_safe_source,
    file_identity,
    load_session_rows,
    open_immutable,
    safe_json,
    sha256_file,
)
from production_tail import resolve_depths, summary


def parse_ts(value):
    if value in (None, ""):
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            return None
    ax = abs(x)
    if ax > 1e17:
        x /= 1e9
    elif ax > 1e14:
        x /= 1e6
    elif ax > 1e11:
        x /= 1e3
    try:
        return datetime.fromtimestamp(x, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def month(dt):
    return dt.strftime("%Y-%m") if dt else "unknown"


def source(row):
    return str(row.get("source") or "<empty>")


def marker(row, key):
    cfg = safe_json(row.get("model_config"))
    if not isinstance(cfg, dict):
        return None
    value = cfg.get(key)
    return None if value in (None, "") else str(value)


def edge_fact(child, parent):
    pid = str(parent["id"])
    child_start = parse_ts(child.get("started_at"))
    parent_end = parse_ts(parent.get("ended_at"))
    branch = marker(child, "_branched_from")
    delegate = marker(child, "_delegate_from")
    return {
        "child": str(child["id"]),
        "parent": pid,
        "source": source(child),
        "child_month": month(child_start),
        "child_started_at": child_start.isoformat() if child_start else None,
        "parent_ended_at": parent_end.isoformat() if parent_end else None,
        "branch_marker": branch,
        "delegate_marker": delegate,
        "branch_points_to_parent": branch == pid,
        "delegate_points_to_parent": delegate == pid,
        "has_any_marker": branch is not None or delegate is not None,
        "timing_known": child_start is not None and parent_end is not None,
        "started_after_parent_end": (
            child_start >= parent_end if child_start is not None and parent_end is not None else None
        ),
    }


def predicates(fact):
    non_tool = fact["source"] != "tool"
    no_marker_presence = not fact["has_any_marker"]
    markers_do_not_point_here = not fact["branch_points_to_parent"] and not fact["delegate_points_to_parent"]
    timing = fact["started_after_parent_end"] is True
    # Parent end_reason='compression' is the candidate precondition for every
    # row passed to this function.
    return {
        # Current shared _COMPRESSION_CHILD_SQL in hermes_state_common.py.
        "parent_end_only": True,
        # Predicate used by our first production profiler; now known too strict
        # for continuations inheriting foreign markers.
        "old_research_marker_presence": non_tool and no_marker_presence,
        # Shape used by older read/projection walks after marker exclusions were
        # added: fail closed on any marker plus the historical timing guard.
        "projection_marker_presence_timing": non_tool and no_marker_presence and timing,
        # a0801b live adoption/reopen correction: a marker disqualifies only
        # when it points at this queried parent.
        "parent_bound_markers": non_tool and markers_do_not_point_here,
        # Same parent-bound marker semantics plus the historical continuation
        # timing guard, useful as a conservative static-corpus variant.
        "parent_bound_markers_timing": non_tool and markers_do_not_point_here and timing,
    }


def topology(by_id, parent_map):
    memo, cycles = resolve_depths(by_id, parent_map)
    depths = [d for root, d in memo.values() if root is not None and d is not None]
    lineage_sizes = Counter(root for root, d in memo.values() if root is not None and d is not None)
    multi_sizes = [n for n in lineage_sizes.values() if n > 1]
    extreme = []
    members = defaultdict(list)
    for sid, (root, depth) in memo.items():
        if root is not None and depth is not None:
            members[root].append((sid, depth))
    for root, items in members.items():
        if len(items) <= 1:
            continue
        months = Counter()
        sources = Counter()
        for sid, _depth in items:
            row = by_id[sid]
            months[month(parse_ts(row.get("started_at")))] += 1
            sources[source(row)] += 1
        extreme.append({
            "root": root,
            "size": len(items),
            "max_depth": max(d for _, d in items),
            "months": dict(months.most_common()),
            "sources": dict(sources.most_common()),
            "deepest_members": [
                {"session_id": sid, "depth": depth, "source": source(by_id[sid])}
                for sid, depth in sorted(items, key=lambda x: x[1], reverse=True)[:8]
            ],
        })
    extreme.sort(key=lambda x: (x["max_depth"], x["size"]), reverse=True)
    return {
        "edge_count": len(parent_map),
        "cycles": len(cycles),
        "depth": {
            "all": summary(depths),
            "gt_0": summary([d for d in depths if d > 0]),
            "gt_1": summary([d for d in depths if d > 1]),
            "ge_4": summary([d for d in depths if d >= 4]),
        },
        "multi_session_lineage_count": len(multi_sizes),
        "sessions_in_multi_session_lineages": sum(multi_sizes),
        "lineage_size_multi_only": summary(multi_sizes),
        "extremes": extreme[:20],
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, default=AUTHORITATIVE_PATH)
    p.add_argument("--expected-sha", default=AUTHORITATIVE_SHA256)
    p.add_argument("--cutoff", default="2026-06-01T00:00:00+08:00")
    p.add_argument("--out", type=Path, default=Path("/tmp/hermes-lineage-edge-audit.json"))
    args = p.parse_args()

    cutoff = datetime.fromisoformat(args.cutoff.replace("Z", "+00:00"))
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    cutoff = cutoff.astimezone(timezone.utc)

    before = file_identity(args.db)
    actual_sha, sidecars = enforce_safe_source(args.db, args.expected_sha)
    conn = open_immutable(args.db)
    try:
        wanted, rows = load_session_rows(conn)
    finally:
        conn.close()

    by_id = {str(r["id"]): r for r in rows}
    candidates = []
    for child in rows:
        pid = child.get("parent_session_id")
        if not pid:
            continue
        parent = by_id.get(str(pid))
        if parent is None or str(parent.get("end_reason") or "") != "compression":
            continue
        fact = edge_fact(child, parent)
        fact["predicates"] = predicates(fact)
        candidates.append(fact)

    names = list(predicates({
        "source": "x", "has_any_marker": False,
        "branch_points_to_parent": False, "delegate_points_to_parent": False,
        "started_after_parent_end": True,
    }))
    maps = {name: {} for name in names}
    for fact in candidates:
        for name, ok in fact["predicates"].items():
            if ok:
                maps[name][fact["child"]] = fact["parent"]

    variants = {}
    for name in names:
        edges = [f for f in candidates if f["predicates"][name]]
        pre = [f for f in edges if parse_ts(by_id[f["child"]].get("started_at")) and parse_ts(by_id[f["child"]].get("started_at")) < cutoff]
        post = [f for f in edges if parse_ts(by_id[f["child"]].get("started_at")) and parse_ts(by_id[f["child"]].get("started_at")) >= cutoff]
        variants[name] = {
            "topology": topology(by_id, maps[name]),
            "edge_source_counts": dict(Counter(f["source"] for f in edges).most_common()),
            "edge_month_counts": dict(Counter(f["child_month"] for f in edges).most_common()),
            "pre_cutoff_edge_count": len(pre),
            "post_cutoff_edge_count": len(post),
        }

    old = maps["old_research_marker_presence"]
    bound = maps["parent_bound_markers"]
    added_ids = sorted(set(bound) - set(old))
    removed_ids = sorted(set(old) - set(bound))
    fact_by_child = {f["child"]: f for f in candidates}

    report = {
        "source": {
            "path": str(args.db), "sha256": actual_sha, "sidecars": sidecars,
            "opened_mode": "ro+immutable+query_only", "mutations_performed": False,
        },
        "cutoff_utc": cutoff.isoformat(),
        "session_count": len(rows),
        "candidate_parent_ended_compression_edges": len(candidates),
        "predicate_notes": {
            "parent_end_only": "current shared _COMPRESSION_CHILD_SQL: parent row ended with end_reason=compression",
            "old_research_marker_presence": "old #54 profiler: reject any branch/delegate marker and source=tool",
            "projection_marker_presence_timing": "older fail-closed projection shape: old marker-presence exclusion + child started_at>=parent ended_at",
            "parent_bound_markers": "a0801b corrected live adoption/reopen shape: reject marker only when its value equals queried parent; reject tool",
            "parent_bound_markers_timing": "parent-bound marker rule plus historical timing guard; conservative static variant",
        },
        "variants": variants,
        "parent_bound_vs_old": {
            "added_edge_count": len(added_ids),
            "removed_edge_count": len(removed_ids),
            "added_edges": [fact_by_child[sid] for sid in added_ids],
            "removed_edges": [fact_by_child[sid] for sid in removed_ids],
        },
        "candidate_edge_facts": candidates,
    }

    after = file_identity(args.db)
    after_sha = sha256_file(args.db)
    if before != after or after_sha != actual_sha:
        raise RuntimeError("SAFETY FAILURE: frozen DB identity/hash changed during edge audit")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "candidate_edges": len(candidates),
        "variants": {
            name: {
                "edges": data["topology"]["edge_count"],
                "post_cutoff_edges": data["post_cutoff_edge_count"],
                "max_depth": data["topology"]["depth"]["all"]["max"],
                "max_multi_lineage_size": data["topology"]["lineage_size_multi_only"]["max"],
            }
            for name, data in variants.items()
        },
        "parent_bound_vs_old_added": len(added_ids),
    }, indent=2, ensure_ascii=False))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
