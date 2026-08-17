#!/usr/bin/env python3
"""Read-only real-corpus lineage explorer for #54.

Measures two MECE things:
1) real topology distribution + explicit tail rows;
2) resolver work/timing on true corpus topology for no-memo, always-memo,
   and one Occam hybrid: speculate no-memo through the first K candidates,
   then allocate/backfill a query-local memo only if K roots were not obtained.

Structural/random/adversarial candidate orderings are NOT claimed to reproduce
real session_search ranking. If this does not decide the mechanism, collect a
bounded replay of real ranked candidate sets next; do not invent more algorithms.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from production_profile import (
    AUTHORITATIVE_PATH,
    AUTHORITATIVE_SHA256,
    enforce_safe_source,
    file_identity,
    load_session_rows,
    open_immutable,
    sha256_file,
)

BUDGETS = (16, 32, 64, 128, 256, 512, 1000, 1254, 1500, 2000)
NODE_SQL = """
SELECT c.id AS child_id,
       c.parent_session_id,
       c.source,
       c.model_config,
       p.id AS parent_exists,
       p.end_reason AS parent_end_reason
FROM sessions AS c
LEFT JOIN sessions AS p ON p.id = c.parent_session_id
WHERE c.id = ?
"""


def pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    ys = sorted(xs)
    pos = (len(ys) - 1) * p
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return ys[lo]
    f = pos - lo
    return ys[lo] * (1 - f) + ys[hi] * f


def summary(values: list[int | float]) -> dict[str, int | float | None]:
    xs = [float(x) for x in values]
    if not xs:
        return {"count": 0, "min": None, "mean": None, "p50": None, "p90": None, "p95": None, "p99": None, "max": None}
    ys = sorted(xs)
    return {
        "count": len(ys),
        "min": ys[0],
        "mean": statistics.fmean(ys),
        "p50": pct(ys, 0.50),
        "p90": pct(ys, 0.90),
        "p95": pct(ys, 0.95),
        "p99": pct(ys, 0.99),
        "max": ys[-1],
    }


def parse_config(raw: Any) -> tuple[bool, dict[str, Any]]:
    if raw in (None, ""):
        return True, {}
    if not isinstance(raw, str):
        return False, {}
    try:
        obj = json.loads(raw)
    except Exception:
        return False, {}
    if not isinstance(obj, dict):
        return False, {}
    for key in ("_branched_from", "_delegate_from"):
        if obj.get(key) is not None and not isinstance(obj.get(key), str):
            return False, {}
    return True, obj


def build_topology(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {str(row["id"]): row for row in rows}
    positive: dict[str, str] = {}
    fail_closed_terminal: set[str] = set()
    reasons: Counter[str] = Counter()
    parsed: dict[str, tuple[bool, dict[str, Any]]] = {}

    for sid, row in by_id.items():
        valid, cfg = parse_config(row.get("model_config"))
        parsed[sid] = (valid, cfg)
        parent_raw = row.get("parent_session_id")
        if parent_raw in (None, ""):
            reasons["no_parent"] += 1
            continue
        parent_id = str(parent_raw)
        parent = by_id.get(parent_id)
        if parent is None:
            reasons["missing_parent_fail_closed"] += 1
            fail_closed_terminal.add(sid)
            continue
        if not valid:
            reasons["malformed_config_stays_separate"] += 1
            continue
        if str(row.get("source") or "") == "tool":
            reasons["tool_stays_separate"] += 1
            continue
        if cfg.get("_branched_from") == parent_id:
            reasons["branch_to_parent_stays_separate"] += 1
            continue
        if cfg.get("_delegate_from") == parent_id:
            reasons["delegate_to_parent_stays_separate"] += 1
            continue
        if str(parent.get("end_reason") or "") != "compression":
            reasons["parent_not_compression"] += 1
            continue
        positive[sid] = parent_id
        reasons["positive_compression"] += 1
        if cfg.get("_branched_from") and cfg.get("_branched_from") != parent_id:
            reasons["positive_with_foreign_branch_marker"] += 1
        if cfg.get("_delegate_from") and cfg.get("_delegate_from") != parent_id:
            reasons["positive_with_foreign_delegate_marker"] += 1

    root: dict[str, str | None] = {}
    depth: dict[str, int | None] = {}
    cycles: set[str] = set()

    for start in by_id:
        if start in root:
            continue
        path: list[str] = []
        seen: dict[str, int] = {}
        cur = start
        while True:
            if cur in root:
                known_root = root[cur]
                known_depth = depth[cur]
                if known_root is None or known_depth is None:
                    for node in path:
                        root[node] = None
                        depth[node] = None
                else:
                    for offset, node in enumerate(reversed(path), 1):
                        root[node] = known_root
                        depth[node] = known_depth + offset
                break
            if cur in seen:
                cycles.update(path[seen[cur]:])
                for node in path:
                    root[node] = None
                    depth[node] = None
                break
            seen[cur] = len(path)
            path.append(cur)
            if cur in fail_closed_terminal:
                for node in path:
                    root[node] = None
                    depth[node] = None
                break
            parent_id = positive.get(cur)
            if parent_id is None:
                root[cur] = cur
                depth[cur] = 0
                for offset, node in enumerate(reversed(path[:-1]), 1):
                    root[node] = cur
                    depth[node] = offset
                break
            cur = parent_id

    lineages: dict[str, list[str]] = defaultdict(list)
    for sid, rid in root.items():
        if rid is not None:
            lineages[rid].append(sid)

    return {
        "rows": by_id,
        "parsed": parsed,
        "positive": positive,
        "root": root,
        "depth": depth,
        "lineages": dict(lineages),
        "reasons": reasons,
        "cycles": cycles,
    }


def depth_bucket(d: int) -> str:
    if d == 0: return "0"
    if d == 1: return "1"
    if d <= 3: return "2-3"
    if d <= 5: return "4-5"
    if d <= 10: return "6-10"
    return "11+"


def size_bucket(n: int) -> str:
    if n == 1: return "1"
    if n == 2: return "2"
    if n <= 5: return "3-5"
    if n <= 10: return "6-10"
    return "11+"


def topology_report(topo: dict[str, Any], tail_n: int) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    depths = [int(d) for d in topo["depth"].values() if d is not None]
    sizes = [len(members) for members in topo["lineages"].values()]
    report = {
        "session_count": len(topo["rows"]),
        "resolved_session_count": len(depths),
        "failed_closed_session_count": len(topo["rows"]) - len(depths),
        "positive_compression_edges": len(topo["positive"]),
        "positive_lineage_cycles": len(topo["cycles"]),
        "edge_reason_counts": dict(topo["reasons"]),
        "depth": summary(depths),
        "depth_buckets": dict(Counter(depth_bucket(d) for d in depths)),
        "lineage_size": summary(sizes),
        "lineage_size_buckets": dict(Counter(size_bucket(n) for n in sizes)),
        "lineage_count": len(sizes),
        "multi_session_lineage_count": sum(n > 1 for n in sizes),
        "sessions_in_multi_session_lineages": sum(n for n in sizes if n > 1),
        "sum_positive_depth": sum(depths),
        "all_sessions_no_memo_work": sum(d + 1 for d in depths),
    }

    deepest = sorted(
        (sid for sid, d in topo["depth"].items() if d is not None),
        key=lambda sid: (topo["depth"][sid], sid), reverse=True,
    )[:tail_n]
    tail_sessions = []
    for sid in deepest:
        row = topo["rows"][sid]
        valid, cfg = topo["parsed"][sid]
        rid = topo["root"][sid]
        tail_sessions.append({
            "session_id": sid,
            "root_id": rid,
            "depth": topo["depth"][sid],
            "lineage_size": len(topo["lineages"].get(rid, [])),
            "parent_session_id": row.get("parent_session_id"),
            "source": row.get("source"),
            "end_reason": row.get("end_reason"),
            "started_at": row.get("started_at"),
            "ended_at": row.get("ended_at"),
            "config_valid": valid,
            "branched_from": cfg.get("_branched_from") if valid else None,
            "delegate_from": cfg.get("_delegate_from") if valid else None,
        })

    ranked = sorted(
        topo["lineages"].items(),
        key=lambda item: (len(item[1]), max(int(topo["depth"][sid] or 0) for sid in item[1]), item[0]),
        reverse=True,
    )[:tail_n]
    tail_lineages = []
    for rid, members in ranked:
        members = sorted(members, key=lambda sid: (topo["depth"][sid] or 0, sid), reverse=True)
        member_depths = [int(topo["depth"][sid] or 0) for sid in members]
        tail_lineages.append({
            "root_id": rid,
            "size": len(members),
            "mean_depth": statistics.fmean(member_depths),
            "max_depth": max(member_depths),
            "naive_full_lineage_work": sum(d + 1 for d in member_depths),
            "members_deepest_first": members,
        })
    return report, tail_sessions, tail_lineages


def row_semantics(row: Any) -> tuple[str | None, str]:
    if row is None:
        return None, "missing_child"
    child = str(row["child_id"])
    parent_raw = row["parent_session_id"]
    if parent_raw in (None, ""):
        return child, "root"
    parent = str(parent_raw)
    if row["parent_exists"] is None:
        return None, "missing_parent"
    valid, cfg = parse_config(row["model_config"])
    if not valid:
        return child, "malformed_config"
    if str(row["source"] or "") == "tool":
        return child, "tool"
    if cfg.get("_branched_from") == parent:
        return child, "branch"
    if cfg.get("_delegate_from") == parent:
        return child, "delegate"
    if str(row["parent_end_reason"] or "") != "compression":
        return child, "not_compression"
    return parent, "continue"


def resolve_no_memo(conn: Any, sid: str, state: dict[str, Any], budget: int | None) -> tuple[str | None, list[str]]:
    cur = sid
    path: list[str] = []
    seen: set[str] = set()
    while True:
        if budget is not None and state["work"] >= budget:
            state["bound_hit"] = True
            return None, path
        row = conn.execute(NODE_SQL, (cur,)).fetchone()
        if row is None:
            return None, path
        state["work"] += 1
        child = str(row["child_id"])
        if child in seen:
            path.append(child)
            return None, path
        seen.add(child)
        path.append(child)
        nxt, kind = row_semantics(row)
        if kind == "continue":
            cur = str(nxt)
            continue
        return nxt, path


def resolve_memo(conn: Any, sid: str, state: dict[str, Any], memo: dict[str, str | None], budget: int | None) -> tuple[str | None, list[str]]:
    if sid in memo:
        return memo[sid], []
    cur = sid
    path: list[str] = []
    seen: set[str] = set()
    while True:
        if cur in memo:
            rid = memo[cur]
            for node in path:
                memo[node] = rid
            return rid, path
        if cur in seen:
            for node in path:
                memo[node] = None
            return None, path
        seen.add(cur)
        if budget is not None and state["work"] >= budget:
            state["bound_hit"] = True
            return None, path
        row = conn.execute(NODE_SQL, (cur,)).fetchone()
        if row is None:
            for node in path:
                memo[node] = None
            return None, path
        state["work"] += 1
        child = str(row["child_id"])
        path.append(child)
        nxt, kind = row_semantics(row)
        if kind == "continue":
            cur = str(nxt)
            continue
        for node in path:
            memo[node] = nxt
        return nxt, path


def run_query(conn: Any, candidates: list[str], k: int, variant: str, budget: int | None = None) -> dict[str, Any]:
    state = {"work": 0, "bound_hit": False}
    winners: list[str] = []
    winner_set: set[str] = set()
    memo: dict[str, str | None] | None = {} if variant == "always_memo" else None
    speculative: list[tuple[list[str], str | None]] = []
    activated_at: int | None = 0 if variant == "always_memo" else None
    consumed = 0
    started = time.perf_counter_ns()

    for index, sid in enumerate(candidates):
        if len(winners) >= k or state["bound_hit"]:
            break
        if variant == "lazy_after_k" and memo is None and index >= k:
            memo = {}
            activated_at = index
            for path, rid in speculative:
                for node in path:
                    memo[node] = rid
            speculative.clear()

        if memo is None:
            rid, path = resolve_no_memo(conn, sid, state, budget)
            if variant == "lazy_after_k":
                speculative.append((path, rid))
        else:
            rid, _ = resolve_memo(conn, sid, state, memo, budget)
        consumed += 1
        if rid is not None and rid not in winner_set:
            winner_set.add(rid)
            winners.append(rid)

    elapsed_ns = time.perf_counter_ns() - started
    return {
        "variant": variant,
        "k": k,
        "candidate_count": len(candidates),
        "candidates_consumed": consumed,
        "winner_count": len(winners),
        "work": state["work"],
        "bound_hit": state["bound_hit"],
        "memo_entries": len(memo) if memo is not None else 0,
        "memo_activated_at_candidate": activated_at,
        "elapsed_ms": elapsed_ns / 1_000_000.0,
    }


def root_pool(topo: dict[str, Any]) -> list[str]:
    return [rid for rid in topo["lineages"]]


def adversarial(topo: dict[str, Any], k: int, cap: int) -> list[str]:
    groups = []
    for rid, members in topo["lineages"].items():
        ordered = sorted(members, key=lambda sid: topo["depth"][sid] or 0, reverse=True)
        score = sum(int(topo["depth"][sid] or 0) + 1 for sid in ordered)
        groups.append((score, rid, ordered))
    groups.sort(reverse=True)
    out: list[str] = []
    for _score, _rid, members in groups[:max(k - 1, 0)]:
        for sid in members:
            if len(out) >= max(cap - 1, 0):
                break
            out.append(sid)
    if len(out) < cap and len(groups) >= k:
        out.append(groups[k - 1][2][0])
    return out[:cap]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for k, v in row.items()})


def timing_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["scenario"], row["variant"], int(row["k"]))].append(row)
    out = []
    for (scenario, variant, k), group in sorted(groups.items()):
        out.append({
            "scenario": scenario,
            "variant": variant,
            "k": k,
            "runs": len(group),
            "elapsed_ms": summary([r["elapsed_ms"] for r in group]),
            "work": summary([r["work"] for r in group]),
            "candidates_consumed": summary([r["candidates_consumed"] for r in group]),
            "bound_hit_count": sum(bool(r["bound_hit"]) for r in group),
            "lazy_activation_count": sum(r["memo_activated_at_candidate"] is not None for r in group) if variant == "lazy_after_k" else 0,
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=AUTHORITATIVE_PATH)
    ap.add_argument("--expected-sha", default=AUTHORITATIVE_SHA256)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--tail-n", type=int, default=40)
    ap.add_argument("--repeats", type=int, default=40)
    ap.add_argument("--seed", type=int, default=20260809)
    args = ap.parse_args()

    db = args.db.resolve()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = args.out or (Path.home() / "hermes-lineage-explore" / stamp)
    out_dir.mkdir(parents=True, exist_ok=False)

    before_identity = file_identity(db)
    actual_sha, sidecars = enforce_safe_source(db, args.expected_sha)
    rng = random.Random(args.seed)

    conn = open_immutable(db)
    try:
        columns, rows = load_session_rows(conn)
        topo = build_topology(rows)
        topo_report, tail_sessions, tail_lineages = topology_report(topo, args.tail_n)
        ids = list(topo["rows"])
        shallow = [sid for sid, d in topo["depth"].items() if d == 0]
        largest_root, largest_members = max(topo["lineages"].items(), key=lambda item: len(item[1]))
        largest_members = sorted(largest_members, key=lambda sid: topo["depth"][sid] or 0, reverse=True)
        tail_then_roots = largest_members + [rid for rid in root_pool(topo) if rid != largest_root]
        top_depth = sorted(ids, key=lambda sid: (topo["depth"].get(sid) or 0, sid), reverse=True)

        timing_rows: list[dict[str, Any]] = []
        variants = ("no_memo", "always_memo", "lazy_after_k")
        for k in (3, 10):
            deterministic = {
                "largest_lineage_then_roots": tail_then_roots[:300],
                "top_depth_300": top_depth[:300],
                "adversarial_real_topology_c300": adversarial(topo, k, 300),
                "adversarial_real_topology_c1000": adversarial(topo, k, 1000),
            }
            for trial in range(args.repeats):
                random_all = rng.sample(ids, min(300, len(ids)))
                random_shallow = rng.sample(shallow, min(300, len(shallow)))
                scenarios = dict(deterministic)
                scenarios["random_all_c300"] = random_all
                scenarios["random_shallow_c300"] = random_shallow
                for name, candidates in scenarios.items():
                    for variant in variants:
                        result = run_query(conn, candidates, k, variant)
                        result.update({"scenario": name, "trial": trial})
                        timing_rows.append(result)

        b_rows: list[dict[str, Any]] = []
        for k in (3, 10):
            candidates = adversarial(topo, k, 1000)
            for budget in BUDGETS:
                for variant in variants:
                    result = run_query(conn, candidates, k, variant, budget=budget)
                    result.update({"scenario": "adversarial_real_topology_c1000", "budget": budget})
                    b_rows.append(result)
    finally:
        conn.close()

    after_identity = file_identity(db)
    after_sha = sha256_file(db)
    if before_identity != after_identity or after_sha != actual_sha:
        raise RuntimeError("SAFETY FAILURE: frozen DB identity/hash changed during read-only exploration")

    report = {
        "status": "research_only_not_real_ranked_candidate_replay",
        "source": {
            "path": str(db),
            "sha256": actual_sha,
            "identity": before_identity,
            "sidecars": sidecars,
            "opened_mode": "ro+immutable+query_only",
            "mutations_performed": False,
        },
        "session_columns_used": columns,
        "measurement_rule": {
            "required": ["mean", "p50", "p90", "p95", "p99", "max", "explicit_tail_rows"],
            "warning": "Random/structural/adversarial order is not real session_search ranking.",
        },
        "topology": topo_report,
        "timing_summary": timing_summary(timing_rows),
        "hybrid": {
            "name": "lazy_after_k",
            "rule": "Try the first K ranked candidates without a shared memo. If they already yield K roots, finish without memo allocation. Otherwise allocate a query-local memo, backfill the speculative paths from those first K candidates, and continue with path compression.",
            "magic_thresholds": 0,
        },
        "b_status": "OPEN; b_curve.csv is evidence, not a decision",
    }
    (out_dir / "summary.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    write_csv(out_dir / "tail_deepest_sessions.csv", tail_sessions)
    write_csv(out_dir / "tail_largest_lineages.csv", tail_lineages)
    write_csv(out_dir / "timing_trials.csv", timing_rows)
    write_csv(out_dir / "b_curve.csv", b_rows)
    (out_dir / "README.md").write_text(
        "# #54 real-corpus explorer\n\n"
        "Read summary.json first, then inspect BOTH tail CSVs. Do not decide from the all-corpus median alone.\n"
        "timing_trials.csv compares no_memo / always_memo / lazy_after_k.\n"
        "b_curve.csv brackets B on an adversarial ordering built from real topology.\n"
        "None of these structural orderings are claimed to be real search ranking.\n",
        encoding="utf-8",
    )

    print(f"OUTPUT_DIR={out_dir}")
    print("DEPTH=" + json.dumps(topo_report["depth"], ensure_ascii=False))
    print("LINEAGE_SIZE=" + json.dumps(topo_report["lineage_size"], ensure_ascii=False))
    print(f"POSITIVE_EDGES={topo_report['positive_compression_edges']}")
    print(f"FAILED_CLOSED={topo_report['failed_closed_session_count']}")
    print("HYBRID=lazy_after_k")
    print("DONE")


if __name__ == "__main__":
    main()
