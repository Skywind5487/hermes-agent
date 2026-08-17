#!/usr/bin/env python3
"""Read-only era/source/extreme-tail profiler for the frozen production lineage corpus.

This does not infer that pre-cutoff rows are imported. It reports time/source strata,
parent coverage, positive-compression depth, and extreme lineages so import-era bias
can be assessed explicitly before interpreting the whole-corpus depth=0 mass.
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
    sha256_file,
)
from production_tail import build_positive_parent, resolve_depths, summary


def parse_ts(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        x = float(value)
    else:
        text = str(value).strip()
        try:
            x = float(text)
        except ValueError:
            try:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
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


def month_key(dt):
    return dt.strftime("%Y-%m") if dt else "unknown"


def era_key(dt, cutoff):
    if dt is None:
        return "unknown"
    return "pre_cutoff" if dt < cutoff else "post_cutoff"


def source_key(row):
    value = row.get("source")
    return str(value) if value not in (None, "") else "<empty>"


def stratum(rows, memo, positive_parent, multi_roots):
    depths = []
    parent_rows = 0
    positive_rows = 0
    multi_rows = 0
    sources = Counter()
    for row in rows:
        sid = str(row["id"])
        if row.get("parent_session_id") not in (None, ""):
            parent_rows += 1
        if sid in positive_parent:
            positive_rows += 1
        resolved = memo.get(sid)
        if resolved and resolved[0] is not None and resolved[1] is not None:
            root, depth = resolved
            depths.append(depth)
            if root in multi_roots:
                multi_rows += 1
        sources[source_key(row)] += 1
    n = len(rows)
    return {
        "session_count": n,
        "rows_with_parent_session_id": parent_rows,
        "parent_session_id_fraction": parent_rows / n if n else 0,
        "positive_compression_children": positive_rows,
        "positive_compression_child_fraction": positive_rows / n if n else 0,
        "sessions_in_multi_session_compression_lineages": multi_rows,
        "multi_session_lineage_session_fraction": multi_rows / n if n else 0,
        "depth": {
            "all": summary(depths),
            "gt_0": summary([d for d in depths if d > 0]),
            "gt_1": summary([d for d in depths if d > 1]),
            "ge_4": summary([d for d in depths if d >= 4]),
        },
        "source_counts": dict(sources.most_common()),
    }


def extreme_lineages(by_id, memo, top_n=20):
    members = defaultdict(list)
    for sid, resolved in memo.items():
        root, depth = resolved
        if root is not None and depth is not None:
            members[root].append((sid, depth))
    out = []
    for root, items in members.items():
        if len(items) <= 1:
            continue
        started = []
        sources = Counter()
        months = Counter()
        deepest = sorted(items, key=lambda x: x[1], reverse=True)[:5]
        for sid, _depth in items:
            row = by_id[sid]
            dt = parse_ts(row.get("started_at"))
            if dt:
                started.append(dt)
            months[month_key(dt)] += 1
            sources[source_key(row)] += 1
        out.append({
            "root_session_id": root,
            "size": len(items),
            "max_depth": max(d for _, d in items),
            "started_at_min_utc": min(started).isoformat() if started else None,
            "started_at_max_utc": max(started).isoformat() if started else None,
            "month_counts": dict(months.most_common()),
            "source_counts": dict(sources.most_common()),
            "deepest_members": [
                {
                    "session_id": sid,
                    "depth": depth,
                    "started_at": str(by_id[sid].get("started_at")),
                    "source": source_key(by_id[sid]),
                }
                for sid, depth in deepest
            ],
        })
    return sorted(out, key=lambda x: (x["max_depth"], x["size"]), reverse=True)[:top_n]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=AUTHORITATIVE_PATH)
    parser.add_argument("--expected-sha", default=AUTHORITATIVE_SHA256)
    parser.add_argument("--cutoff", default="2026-06-01T00:00:00+00:00")
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--out", type=Path, default=Path("/tmp/hermes-lineage-distribution.json"))
    args = parser.parse_args()

    cutoff = datetime.fromisoformat(args.cutoff.replace("Z", "+00:00"))
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    cutoff = cutoff.astimezone(timezone.utc)

    before_identity = file_identity(args.db)
    actual_sha, sidecars = enforce_safe_source(args.db, args.expected_sha)
    conn = open_immutable(args.db)
    try:
        wanted, rows = load_session_rows(conn)
    finally:
        conn.close()

    columns = set(wanted)
    if "started_at" not in columns:
        raise RuntimeError("sessions.started_at is required for era stratification")

    by_id, positive_parent = build_positive_parent(rows, columns)
    memo, cycles = resolve_depths(by_id, positive_parent)
    lineage_sizes = Counter(root for root, depth in memo.values() if root is not None and depth is not None)
    multi_roots = {root for root, size in lineage_sizes.items() if size > 1}

    by_era = defaultdict(list)
    by_month = defaultdict(list)
    by_source = defaultdict(list)
    ts_parse_failures = 0
    for row in rows:
        dt = parse_ts(row.get("started_at"))
        if row.get("started_at") not in (None, "") and dt is None:
            ts_parse_failures += 1
        by_era[era_key(dt, cutoff)].append(row)
        by_month[month_key(dt)].append(row)
        by_source[source_key(row)].append(row)

    report = {
        "source": {
            "path": str(args.db),
            "sha256": actual_sha,
            "sidecars": sidecars,
            "opened_mode": "ro+immutable+query_only",
            "mutations_performed": False,
        },
        "cutoff_utc": cutoff.isoformat(),
        "interpretation_guardrail": "pre_cutoff is a time stratum only; do not equate it with imported rows unless provenance/source data proves that",
        "session_count": len(rows),
        "timestamp_parse_failures": ts_parse_failures,
        "positive_lineage_cycles": len(cycles),
        "overall": stratum(rows, memo, positive_parent, multi_roots),
        "by_era": {k: stratum(v, memo, positive_parent, multi_roots) for k, v in sorted(by_era.items())},
        "by_month": {k: stratum(v, memo, positive_parent, multi_roots) for k, v in sorted(by_month.items())},
        "by_source": {k: stratum(v, memo, positive_parent, multi_roots) for k, v in sorted(by_source.items(), key=lambda kv: (-len(kv[1]), kv[0]))},
        "extreme_lineages": extreme_lineages(by_id, memo, args.top_n),
    }

    after_identity = file_identity(args.db)
    after_sha = sha256_file(args.db)
    if before_identity != after_identity or after_sha != actual_sha:
        raise RuntimeError("SAFETY FAILURE: frozen DB identity/hash changed during distribution profiling")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "session_count": report["session_count"],
        "cutoff_utc": report["cutoff_utc"],
        "by_era": report["by_era"],
        "months": list(report["by_month"]),
        "extreme_lineages": report["extreme_lineages"][:5],
    }, indent=2, ensure_ascii=False))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
