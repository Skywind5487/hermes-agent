#!/usr/bin/env python3
"""Read-only real-corpus explorer for session-lineage resolver decisions (#54).

This deliberately separates:

1. topology distribution + explicit tail inspection;
2. resolver work/timing on true corpus topology.

It does NOT claim that structural/random/adversarial candidate orderings reproduce real
session_search ranking. If these measurements do not decide the mechanism, the next
step is bounded replay/instrumentation of real ranked candidate sets.

Safety inherits the recovery contract from production_profile.py: authoritative SHA,
no non-empty SQLite sidecars, mode=ro+immutable, PRAGMA query_only, and before/after
file identity + SHA verification. The database is never mutated.
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
from dataclasses import dataclass
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


DEFAULT_BUDGETS = (16, 32, 64, 128, 256, 512, 1000, 1254, 1500, 2000)


def percentile(ordered: list[float], p: float) -> float | None:
    if not ordered:
        return None
    pos = (len(ordered) - 1) * p
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    frac = pos - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def numeric_summary(values: list[float | int]) -> dict[str, float | int | None]:
    ordered = sorted(float(v) for v in values)
    if not ordered:
        return {
            "count": 0,
            "min": None,
            "mean": None,
            "p50": None,
            "p90": None,
            "p95": None,
            "p99": None,
            "max": None,
        }
    return {
        "count": len(ordered),
        "min": ordered[0],
        "mean": statistics.fmean(ordered),
        "p50": percentile(ordered, 0.50),
        "p90": percentile(ordered, 0.90),
        "p95": percentile(ordered, 0.95),
        "p99": percentile(ordered, 0.99),
        "max": ordered[-1],
    }


def bucket_depth(depth: int) -> str:
    if depth == 0:
        return "0"
    if depth == 1:
        return "1"
    if depth <= 3:
        return "2-3"
    if depth <= 5:
        return "4-5"
    if depth <= 10:
        return "6-10"
    if depth <= 32:
        return "11-32"
    return ">32"


def bucket_size(size: int) -> str:
    if size == 1:
        return "1"
    if size == 2:
        return "2"
    if size <= 5:
        return "3-5"
    if size <= 10:
        return "6-10"
    if size <= 20:
        return "11-20"
    return ">20"


def parse_config(value: Any) -> tuple[bool, dict[str, Any]]:
    if value in (None, ""):
        return True, {}
    if not isinstance(value, str):
        return False, {}
    try:
        parsed = json.loads(value)
    except Exception:
        return False, {}
    if not isinstance(parsed, dict):
        return False, {}
    for key in ("_branched_from", "_delegate_from"):
        marker = parsed.get(key)
        if marker is not None and not isinstance(marker, str):
            return False, {}
    return True, parsed


@dataclass(frozen=True)
class StaticNode:
    sid: str
    parent_id: str | None
    source: str
    end_reason: str
    started_at: Any
    ended_at: Any
    config_valid: bool
    branched_from: str | None
    delegate_from: str | None


@dataclass
class StaticTopology:
    nodes: dict[str, StaticNode]
    positive_parent: dict[str, str]
    root_by_id: dict[str, str | None]
    depth_by_id: dict[str, int | None]
    lineages: dict[str, list[str]]
    edge_reasons: Counter[str]
    cycles: set[str]


def build_static_topology(rows: list[dict[str, Any]]) -> StaticTopology:
    nodes: dict[str, StaticNode] = {}
    for row in rows:
        sid = str(row["id"])
        valid, config = parse_config(row.get("model_config"))
        parent_raw = row.get("parent_session_id")
        nodes[sid] = StaticNode(
            sid=sid,
            parent_id=str(parent_raw) if parent_raw not in (None, "") else None,
            source=str(row.get("source") or ""),
            end_reason=str(row.get("end_reason") or ""),
            started_at=row.get("started_at"),
            ended_at=row.get("ended_at"),
            config_valid=valid,
            branched_from=config.get("_branched_from") if valid else None,
            delegate_from=config.get("_delegate_from") if valid else None,
        )

    positive_parent: dict[str, str] = {}
    reasons: Counter[str] = Counter()
    for sid, node in nodes.items():
        parent_id = node.parent_id
        if not parent_id:
            reasons["no_parent"] += 1
            continue
        parent = nodes.get(parent_id)
        if parent is None:
            reasons["missing_parent"] += 1
            continue
        if not node.config_valid:
            reasons["malformed_config_stays_separate"] += 1
            continue
        if node.source == "tool":
            reasons["tool_stays_separate"] += 1
            continue
        if node.branched_from == parent_id:
            reasons["branch_to_parent_stays_separate"] += 1
            continue
        if node.delegate_from == parent_id:
            reasons["delegate_to_parent_stays_separate"] += 1
            continue
        if parent.end_reason != "compression":
            reasons["parent_not_compression"] += 1
            continue
        positive_parent[sid] = parent_id
        reasons["positive_compression"] += 1
        if node.branched_from and node.branched_from != parent_id:
            reasons["positive_with_foreign_branch_marker"] += 1
        if node.delegate_from and node.delegate_from != parent_id:
            reasons["positive_with_foreign_delegate_marker"] += 1

    root_by_id: dict[str, str | None] = {}
    depth_by_id: dict[str, int | None] = {}
    cycles: set[str] = set()

    for start in nodes:
        if start in root_by_id:
            continue
        path: list[str] = []
        index: dict[str, int] = {}
        cur = start
        while True:
            if cur in root_by_id:
                root = root_by_id[cur]
                base_depth = depth_by_id[cur]
                if root is None or base_depth is None:
                    for sid in path:
                        root_by_id[sid] = None
                        depth_by_id[sid] = None
                else:
                    for offset, sid in enumerate(reversed(path), 1):
                        root_by_id[sid] = root
                        depth_by_id[sid] = base_depth + offset
                break
            if cur in index:
                cycle_members = path[index[cur] :]
                cycles.update(cycle_members)
                for sid in path:
                    root_by_id[sid] = None
                    depth_by_id[sid] = None
                break
            index[cur] = len(path)
            path.append(cur)
            parent_id = positive_parent.get(cur)
            if parent_id is None:
                root_by_id[cur] = cur
                depth_by_id[cur] = 0
                for offset, sid in enumerate(reversed(path[:-1]), 1):
                    root_by_id[sid] = cur
                    depth_by_id[sid] = offset
                break
            cur = parent_id

    lineages: dict[str, list[str]] = defaultdict(list)
    for sid, root in root_by_id.items():
        if root is not None:
            lineages[root].append(sid)

    return StaticTopology(
        nodes=nodes,
        positive_parent=positive_parent,
        root_by_id=root_by_id,
        depth_by_id=depth_by_id,
        lineages=dict(lineages),
        edge_reasons=reasons,
        cycles=cycles,
    )


def static_report(topology: StaticTopology, *, tail_n: int) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    valid_depths = [d for d in topology.depth_by_id.values() if d is not None]
    lineage_sizes = [len(members) for members in topology.lineages.values()]

    depth_buckets = Counter(bucket_depth(int(d)) for d in valid_depths)
    size_buckets = Counter(bucket_size(int(s)) for s in lineage_sizes)

    deepest_ids = sorted(
        (sid for sid, depth in topology.depth_by_id.items() if depth is not None),
        key=lambda sid: (topology.depth_by_id[sid] or 0, sid),
        reverse=True,
    )[:tail_n]

    deepest_rows: list[dict[str, Any]] = []
    for sid in deepest_ids:
        node = topology.nodes[sid]
        root = topology.root_by_id[sid]
        deepest_rows.append(
            {
                "session_id": sid,
                "root_id": root,
                "depth": topology.depth_by_id[sid],
                "lineage_size": len(topology.lineages.get(root or "", [])),
                "parent_session_id": node.parent_id,
                "source": node.source,
                "end_reason": node.end_reason,
                "started_at": node.started_at,
                "ended_at": node.ended_at,
                "config_valid": node.config_valid,
                "branched_from": node.branched_from,
                "delegate_from": node.delegate_from,
            }
        )

    lineage_rows: list[dict[str, Any]] = []
    ranked_lineages = sorted(
        topology.lineages.items(),
        key=lambda item: (
            len(item[1]),
            max((topology.depth_by_id[sid] or 0) for sid in item[1]),
            item[0],
        ),
        reverse=True,
    )[:tail_n]
    for root, members in ranked_lineages:
        ordered_members = sorted(
            members,
            key=lambda sid: (topology.depth_by_id[sid] or 0, sid),
            reverse=True,
        )
        depths = [int(topology.depth_by_id[sid] or 0) for sid in members]
        lineage_rows.append(
            {
                "root_id": root,
                "size": len(members),
                "mean_depth": statistics.fmean(depths) if depths else 0.0,
                "max_depth": max(depths) if depths else 0,
                "naive_full_lineage_work": sum(d + 1 for d in depths),
                "members_deepest_first": ordered_members,
            }
        )

    multi = [size for size in lineage_sizes if size > 1]
    report = {
        "session_count": len(topology.nodes),
        "positive_compression_edges": len(topology.positive_parent),
        "positive_lineage_cycles": len(topology.cycles),
        "edge_reason_counts": dict(topology.edge_reasons),
        "depth": numeric_summary(valid_depths),
        "depth_buckets": dict(depth_buckets),
        "lineage_size": numeric_summary(lineage_sizes),
        "lineage_size_buckets": dict(size_buckets),
        "lineage_count": len(topology.lineages),
        "multi_session_lineage_count": len(multi),
        "sessions_in_multi_session_lineages": sum(multi),
        "all_sessions_no_memo_work": sum(int(d) + 1 for d in valid_depths),
        "sum_positive_depth": sum(int(d) for d in valid_depths),
    }
    return report, deepest_rows, lineage_rows


NODE_SQL = """
SELECT
    c.id AS child_id,
    c.parent_session_id AS parent_session_id,
    c.source AS source,
    c.model_config AS model_config,
    p.id AS parent_exists,
    p.end_reason AS parent_end_reason
FROM sessions AS c
LEFT JOIN sessions AS p ON p.id = c.parent_session_id
WHERE c.id = ?
"""


@dataclass
class QueryState:
    work: int = 0
    bound_hit: bool = False


def lookup_node(conn, sid: str, state: QueryState, budget: int | None) -> tuple[str | None, list[str]]:
    """Resolve one candidate without shared memo; return root + traversed path."""
    path: list[str] = []
    seen: set[str] = set()
    cur = sid
    while True:
        if budget is not None and state.work >= budget:
            state.bound_hit = True
            return None, path
        row = conn.execute(NODE_SQL, (cur,)).fetchone()
        if row is None:
            return None, path
        state.work += 1
        child_id = str(row["child_id"])
        path.append(child_id)
        if child_id in seen:
            return None, path
        seen.add(child_id)

        parent_raw = row["parent_session_id"]
        if parent_raw in (None, ""):
            return child_id, path
        parent_id = str(parent_raw)
        if row["parent_exists"] is None:
            return None, path

        valid, config = parse_config(row["model_config"])
        if not valid:
            return child_id, path
        if str(row["source"] or "") == "tool":
            return child_id, path
        if config.get("_branched_from") == parent_id:
            return child_id, path
        if config.get("_delegate_from") == parent_id:
            return child_id, path
        if str(row["parent_end_reason"] or "") != "compression":
            return child_id, path
        cur = parent_id


def lookup_node_memo(
    conn,
    sid: str,
    state: QueryState,
    memo: dict[str, str | None],
    budget: int | None,
) -> tuple[str | None, list[str]]:
    if sid in memo:
        return memo[sid], []

    path: list[str] = []
    seen: set[str] = set()
    cur = sid
    while True:
        if cur in memo:
            root = memo[cur]
            for node in path:
                memo[node] = root
            return root, path
        if cur in seen:
            for node in path:
                memo[node] = None
            return None, path
        seen.add(cur)

        if budget is not None and state.work >= budget:
            state.bound_hit = True
            return None, path
        row = conn.execute(NODE_SQL, (cur,)).fetchone()
        if row is None:
            for node in path:
                memo[node] = None
            return None, path
        state.work += 1
        child_id = str(row["child_id"])
        path.append(child_id)

        parent_raw = row["parent_session_id"]
        if parent_raw in (None, ""):
            root = child_id
        else:
            parent_id = str(parent_raw)
            if row["parent_exists"] is None:
                root = None
            else:
                valid, config = parse_config(row["model_config"])
                if not valid:
                    root = child_id
                elif str(row["source"] or "") == "tool":
                    root = child_id
                elif config.get("_branched_from") == parent_id:
                    root = child_id
                elif config.get("_delegate_from") == parent_id:
                    root = child_id
                elif str(row["parent_end_reason"] or "") != "compression":
                    root = child_id
                else:
                    cur = parent_id
                    continue

        for node in path:
            memo[node] = root
        return root, path


def run_candidates(
    conn,
    candidates: list[str],
    *,
    k: int,
    variant: str,
    budget: int | None = None,
) -> dict[str, Any]:
    state = QueryState()
    winners: list[str] = []
    winner_set: set[str] = set()
    memo: dict[str, str | None] | None = {} if variant == "always_memo" else None
    speculative_paths: list[tuple[list[str], str | None]] = []
    memo_activated_at: int | None = 0 if variant == "always_memo" else None
    consumed = 0

    started = time.perf_counter_ns()
    for index, sid in enumerate(candidates):
        if len(winners) >= k or state.bound_hit:
            break

        if variant == "lazy_after_k" and memo is None and index >= k:
            memo = {}
            memo_activated_at = index
            for path, root in speculative_paths:
                for node in path:
                    memo[node] = root
            speculative_paths.clear()

        if memo is None:
            root, path = lookup_node(conn, sid, state, budget)
            if variant == "lazy_after_k":
                speculative_paths.append((path, root))
        else:
            root, _path = lookup_node_memo(conn, sid, state, memo, budget)

        consumed += 1
        if root is not None and root not in winner_set:
            winner_set.add(root)
            winners.append(root)

    elapsed_ns = time.perf_counter_ns() - started
    return {
        "variant": variant,
        "k": k,
        "candidate_count": len(candidates),
        "candidates_consumed": consumed,
        "winner_count": len(winners),
        "work": state.work,
        "bound_hit": state.bound_hit,
        "memo_entries": len(memo) if memo is not None else 0,
        "memo_activated_at_candidate": memo_activated_at,
        "elapsed_ns": elapsed_ns,
        "elapsed_ms": elapsed_ns / 1_000_000.0,
    }


def unique_root_pool(topology: StaticTopology) -> list[str]:
    result = []
    for root, members in topology.lineages.items():
        if root in members:
            result.append(root)
        else:
            result.append(members[0])
    return result


def build_adversarial_candidates(topology: StaticTopology, *, k: int, cap: int) -> list[str]:
    groups = []
    for root, members in topology.lineages.items():
        ordered = sorted(members, key=lambda sid: topology.depth_by_id[sid] or 0, reverse=True)
        score = sum(int(topology.depth_by_id[sid] or 0) + 1 for sid in ordered)
        groups.append((score, root, ordered))
    groups.sort(reverse=True)

    blocked_groups = groups[: max(k - 1, 0)]
    candidates: list[str] = []
    for _score, _root, members in blocked_groups:
        for sid in members:
            if len(candidates) >= max(cap - 1, 0):
                break
            candidates.append(sid)
        if len(candidates) >= max(cap - 1, 0):
            break

    if len(candidates) < cap and len(groups) >= k:
        candidates.append(groups[k - 1][2][0])
    return candidates[:cap]


def make_scenarios(topology: StaticTopology, rng: random.Random, *, random_trials: int) -> list[dict[str, Any]]:
    all_ids = list(topology.nodes)
    shallow = [sid for sid, depth in topology.depth_by_id.items() if depth == 0]
    root_pool = unique_root_pool(topology)

    largest_root, largest_members = max(topology.lineages.items(), key=lambda item: len(item[1]))
    largest_members = sorted(
        largest_members,
        key=lambda sid: topology.depth_by_id[sid] or 0,
        reverse=True,
    )
    tail_then_roots = largest_members + [sid for sid in root_pool if topology.root_by_id.get(sid) != largest_root]

    top_depth = sorted(
        all_ids,
        key=lambda sid: (topology.depth_by_id.get(sid) or 0, sid),
        reverse=True,
    )

    scenarios: list[dict[str, Any]] = []
    for k in (3, 10):
        scenarios.append({"name": f"largest_lineage_then_roots_k{k}", "k": k, "candidates": tail_then_roots[:300], "kind": "tail"})
        scenarios.append({"name": f"top_depth_300_k{k}", "k": k, "candidates": top_depth[:300], "kind": "tail"})
        scenarios.append({"name": f"adversarial_real_topology_c300_k{k}", "k": k, "candidates": build_adversarial_candidates(topology, k=k, cap=300), "kind": "tail"})
        scenarios.append({"name": f"adversarial_real_topology_c1000_k{k}", "k": k, "candidates": build_adversarial_candidates(topology, k=k, cap=1000), "kind": "tail"})

        for trial in range(random_trials):
            c = min(300, len(all_ids))
            candidates = rng.sample(all_ids, c)
            scenarios.append({"name": f"random_all_c300_k{k}", "k": k, "candidates": candidates, "kind": "random", "trial": trial})
            if len(shallow) >= c:
                shallow_candidates = rng.sample(shallow, c)
                scenarios.append({"name": f"random_shallow_c300_k{k}", "k": k, "candidates": shallow_candidates, "kind": "random", "trial": trial})
    return scenarios


def aggregate_timing(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["scenario"], row["variant"], int(row["k"]))].append(row)

    result = []
    for (scenario, variant, k), group in sorted(grouped.items()):
        result.append(
            {
                "scenario": scenario,
                "variant": variant,
                "k": k,
                "runs": len(group),
                "elapsed_ms": numeric_summary([float(r["elapsed_ms"]) for r in group]),
                "work": numeric_summary([int(r["work"]) for r in group]),
                "candidates_consumed": numeric_summary([int(r["candidates_consumed"]) for r in group]),
                "bound_hit_count": sum(bool(r["bound_hit"]) for r in group),
                "memo_activated_count": sum(r["memo_activated_at_candidate"] is not None for r in group if variant == "lazy_after_k"),
            }
        )
    return result


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            cooked = {
                key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                for key, value in row.items()
            }
            writer.writerow(cooked)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=AUTHORITATIVE_PATH)
    parser.add_argument("--expected-sha", default=AUTHORITATIVE_SHA256)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--tail-n", type=int, default=40)
    parser.add_argument("--random-trials", type=int, default=40)
    parser.add_argument("--seed", type=int, default=20260809)
    args = parser.parse_args()

    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = args.out or (Path.home() / "hermes-lineage-explore" / stamp)
    out.mkdir(parents=True, exist_ok=False)

    db = args.db.resolve()
    before_identity = file_identity(db)
    actual_sha, sidecars = enforce_safe_source(db, args.expected_sha)

    conn = open_immutable(db)
    try:
        columns, rows = load_session_rows(conn)
        topology = build_static_topology(rows)
        structure, deepest_rows, lineage_rows = static_report(topology, tail_n=args.tail_n)

        rng = random.Random(args.seed)
        scenarios = make_scenarios(topology, rng, random_trials=args.random_trials)
        timing_rows: list[dict[str, Any]] = []
        for scenario in scenarios:
            for variant in ("no_memo", "always_memo", "lazy_after_k"):
                result = run_candidates(
                    conn,
                    scenario["candidates"],
                    k=int(scenario["k"]),
                    variant=variant,
                    budget=None,
                )
                result.update(
                    {
                        "scenario": scenario["name"],
                        "scenario_kind": scenario["kind"],
                        "trial": scenario.get("trial", 0),
                    }
                )
                timing_rows.append(result)

        b_rows: list[dict[str, Any]] = []
        for k in (3, 10):
            candidates = build_adversarial_candidates(topology, k=k, cap=1000)
            for budget in DEFAULT_BUDGETS:
                for variant in ("no_memo", "always_memo", "lazy_after_k"):
                    result = run_candidates(conn, candidates, k=k, variant=variant, budget=budget)
                    result.update(
                        {
                            "scenario": f"adversarial_real_topology_c1000_k{k}",
                            "budget": budget,
                        }
                    )
                    b_rows.append(result)
    finally:
        conn.close()

    after_identity = file_identity(db)
    after_sha = sha256_file(db)
    if before_identity != after_identity or after_sha != actual_sha:
        raise RuntimeError("SAFETY FAILURE: frozen DB identity/hash changed during read-only exploration")

    timing_summary = aggregate_timing(timing_rows)
    report = {
        "status": "research_only_not_production_ranking_replay",
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
            "required_statistics": ["mean", "p50", "p90", "p95", "p99", "max"],
            "tail_rows_emitted": True,
            "warning": "Structural/random/adversarial candidate order is not real session_search ranking.",
        },
        "topology": structure,
        "timing_summary": timing_summary,
        "b_status": "open_hypothesis_only",
        "hybrid": {
            "name": "lazy_after_k",
            "rule": "Resolve the first K ranked candidates without a shared memo. If they already yield K distinct roots, stop with no memo allocation. Otherwise allocate a query-local memo, backfill the bounded speculative paths from those first K candidates, then continue with memo/path compression.",
            "reason": "Natural threshold comes from the product goal K; no magic depth/work tuning constant.",
        },
    }

    (out / "summary.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    write_csv(out / "tail_deepest_sessions.csv", deepest_rows)
    write_csv(out / "tail_largest_lineages.csv", lineage_rows)
    write_csv(out / "timing_trials.csv", timing_rows)
    write_csv(out / "b_curve.csv", b_rows)

    readme = f"""# Hermes lineage real-corpus explorer\n\nSource: {db}\nSHA-256: {actual_sha}\n\nSafety: mode=ro + immutable=1 + query_only; SHA and file identity verified before/after.\nNo DB mutations were performed.\n\nInterpretation rules:\n- summary.json contains mean/p50/p90/p95/p99/max, not only aggregate/max.\n- tail_deepest_sessions.csv and tail_largest_lineages.csv are mandatory tail inspection evidence.\n- timing_trials.csv compares no_memo / always_memo / lazy_after_k on true corpus topology.\n- b_curve.csv is a real-topology adversarial bracket, not a final B decision.\n- random/adversarial structural candidate ordering is NOT a replay of real session_search ranking.\n\nIf these results do not clearly choose a mechanism, collect bounded real ranked-candidate replay next; do not invent more algorithms.\n"""
    (out / "README.md").write_text(readme, encoding="utf-8")

    print(f"OUTPUT_DIR={out}")
    print(f"SHA256={actual_sha}")
    print("DEPTH=" + json.dumps(structure["depth"], ensure_ascii=False))
    print("LINEAGE_SIZE=" + json.dumps(structure["lineage_size"], ensure_ascii=False))
    print(f"POSITIVE_EDGES={structure['positive_compression_edges']}")
    print(f"MULTI_LINEAGES={structure['multi_session_lineage_count']}")
    print(f"MAX_DEPTH={structure['depth']['max']}")
    print(f"MAX_LINEAGE_SIZE={structure['lineage_size']['max']}")
    print("HYBRID=lazy_after_k")
    print("DONE")


if __name__ == "__main__":
    main()
