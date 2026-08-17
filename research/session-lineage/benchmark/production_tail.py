#!/usr/bin/env python3
"""Read-only conditional tail stats for the frozen production lineage corpus."""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
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


def percentile(values, p):
    ordered = sorted(values)
    if not ordered:
        return None
    pos = (len(ordered) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def summary(values):
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "sum": 0, "mean": None, "median": None, "p95": None, "p99": None, "min": None, "max": None}
    return {
        "count": len(ordered),
        "sum": sum(ordered),
        "mean": statistics.fmean(ordered),
        "median": statistics.median(ordered),
        "p95": percentile(ordered, 0.95),
        "p99": percentile(ordered, 0.99),
        "min": ordered[0],
        "max": ordered[-1],
    }


def build_positive_parent(rows, columns):
    by_id = {str(row["id"]): row for row in rows}
    positive_parent = {}
    for sid, row in by_id.items():
        parent_id = row.get("parent_session_id") if "parent_session_id" in columns else None
        if not parent_id:
            continue
        parent = by_id.get(str(parent_id))
        if parent is None:
            continue
        config = safe_json(row.get("model_config")) if "model_config" in columns else {}
        if config is None:
            config = {}
        branched = config.get("_branched_from") is not None
        delegated = config.get("_delegate_from") is not None
        is_tool = str(row.get("source") or "") == "tool" if "source" in columns else False
        parent_reason = str(parent.get("end_reason") or "") if "end_reason" in columns else ""
        if parent_reason == "compression" and not branched and not delegated and not is_tool:
            positive_parent[sid] = str(parent_id)
    return by_id, positive_parent


def resolve_depths(by_id, positive_parent):
    memo = {}
    cycles = set()

    def resolve(start):
        if start in memo:
            return memo[start]
        path = []
        index = {}
        cur = start
        while True:
            if cur in memo:
                root, base_depth = memo[cur]
                if root is None:
                    for node in path:
                        memo[node] = (None, None)
                else:
                    for offset, node in enumerate(reversed(path), 1):
                        memo[node] = (root, base_depth + offset)
                return memo[start]
            if cur in index:
                cycles.update(path[index[cur]:])
                for node in path:
                    memo[node] = (None, None)
                return (None, None)
            index[cur] = len(path)
            path.append(cur)
            parent = positive_parent.get(cur)
            if parent is None:
                memo[cur] = (cur, 0)
                for offset, node in enumerate(reversed(path[:-1]), 1):
                    memo[node] = (cur, offset)
                return memo[start]
            cur = parent

    for sid in by_id:
        resolve(sid)
    return memo, cycles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=AUTHORITATIVE_PATH)
    parser.add_argument("--expected-sha", default=AUTHORITATIVE_SHA256)
    parser.add_argument("--out", type=Path, default=Path("/tmp/hermes-production-lineage-tail.json"))
    args = parser.parse_args()

    before_identity = file_identity(args.db)
    actual_sha, sidecars = enforce_safe_source(args.db, args.expected_sha)
    conn = open_immutable(args.db)
    try:
        wanted, rows = load_session_rows(conn)
    finally:
        conn.close()

    by_id, positive_parent = build_positive_parent(rows, set(wanted))
    memo, cycles = resolve_depths(by_id, positive_parent)
    resolved = [(sid, root, depth) for sid, (root, depth) in memo.items() if root is not None and depth is not None]
    depths = [depth for _, _, depth in resolved]
    lineage_sizes = Counter(root for _, root, _ in resolved)
    multi_sizes = [size for size in lineage_sizes.values() if size > 1]

    report = {
        "source": {
            "path": str(args.db),
            "sha256": actual_sha,
            "sidecars": sidecars,
            "opened_mode": "ro+immutable+query_only",
            "mutations_performed": False,
        },
        "session_count": len(rows),
        "positive_compression_edges": len(positive_parent),
        "positive_lineage_cycles": len(cycles),
        "depth_histogram": dict(sorted(Counter(depths).items())),
        "depth": {
            "all": summary(depths),
            "gt_0": summary([d for d in depths if d > 0]),
            "gt_1": summary([d for d in depths if d > 1]),
            "ge_4": summary([d for d in depths if d >= 4]),
        },
        "lineage_size": {
            "all": summary(list(lineage_sizes.values())),
            "multi_session_only": summary(multi_sizes),
            "multi_session_lineage_count": len(multi_sizes),
            "sessions_in_multi_session_lineages": sum(multi_sizes),
            "histogram_multi_session_only": dict(sorted(Counter(multi_sizes).items())),
        },
    }

    after_identity = file_identity(args.db)
    after_sha = sha256_file(args.db)
    if before_identity != after_identity or after_sha != actual_sha:
        raise RuntimeError("SAFETY FAILURE: frozen DB identity/hash changed during tail profiling")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
