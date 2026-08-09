#!/usr/bin/env python3
"""Counterbalanced follow-up for #54 real-corpus lineage exploration.

Reuses production_explore_real.py semantics and candidate constructors, but avoids
fixed variant-order cache bias by cycling all six variant permutations evenly.
Also extends the work-budget sweep to K=100 so the DB-level defensive result-limit
contract is measured separately from the public tool's usual K<=10 path.

The authoritative DB remains read-only/immutable/query-only and is hash-verified
before and after. This still does NOT claim structural orderings are real search
ranking; real ranked-candidate replay remains a separate evidence cell.
"""
from __future__ import annotations

import argparse
import itertools
import json
import random
import time
from pathlib import Path

import production_explore_real as base
from production_profile import (
    AUTHORITATIVE_PATH,
    AUTHORITATIVE_SHA256,
    enforce_safe_source,
    file_identity,
    load_session_rows,
    open_immutable,
    sha256_file,
)

VARIANTS = ("no_memo", "always_memo", "lazy_after_k")
ORDERS = tuple(itertools.permutations(VARIANTS))


def balanced_order(trial: int, scenario_index: int, k_index: int) -> tuple[str, ...]:
    # Six permutations, rotated independently by scenario/K. With repeats divisible
    # by 6 each variant occupies each ordinal position equally often.
    return ORDERS[(trial + scenario_index + k_index) % len(ORDERS)]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=AUTHORITATIVE_PATH)
    ap.add_argument("--expected-sha", default=AUTHORITATIVE_SHA256)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--repeats", type=int, default=60)
    ap.add_argument("--seed", type=int, default=20260809)
    args = ap.parse_args()
    if args.repeats < 6 or args.repeats % 6:
        raise SystemExit("--repeats must be a positive multiple of 6 (recommended: 60)")

    db = args.db.resolve()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = args.out or (Path.home() / "hermes-lineage-explore-counterbalanced" / stamp)
    out_dir.mkdir(parents=True, exist_ok=False)

    before_identity = file_identity(db)
    actual_sha, sidecars = enforce_safe_source(db, args.expected_sha)
    rng = random.Random(args.seed)

    conn = open_immutable(db)
    try:
        columns, rows = load_session_rows(conn)
        topo = base.build_topology(rows)
        ids = list(topo["rows"])
        shallow = [sid for sid, depth in topo["depth"].items() if depth == 0]
        largest_root, largest_members = max(topo["lineages"].items(), key=lambda item: len(item[1]))
        largest_members = sorted(largest_members, key=lambda sid: topo["depth"][sid] or 0, reverse=True)
        tail_then_roots = largest_members + [rid for rid in base.root_pool(topo) if rid != largest_root]
        top_depth = sorted(ids, key=lambda sid: (topo["depth"].get(sid) or 0, sid), reverse=True)

        timing_rows: list[dict] = []
        for k_index, k in enumerate((3, 10)):
            deterministic = {
                "largest_lineage_then_roots": tail_then_roots[:300],
                "top_depth_300": top_depth[:300],
                "adversarial_real_topology_c300": base.adversarial(topo, k, 300),
                "adversarial_real_topology_c1000": base.adversarial(topo, k, 1000),
            }
            for trial in range(args.repeats):
                scenarios = dict(deterministic)
                scenarios["random_all_c300"] = rng.sample(ids, min(300, len(ids)))
                scenarios["random_shallow_c300"] = rng.sample(shallow, min(300, len(shallow)))

                # Rotate scenario traversal too, so a deterministic scenario is not
                # always the first/last block within each trial.
                items = list(scenarios.items())
                shift = trial % len(items)
                items = items[shift:] + items[:shift]
                for scenario_index, (name, candidates) in enumerate(items):
                    order = balanced_order(trial, scenario_index, k_index)
                    for position, variant in enumerate(order):
                        result = base.run_query(conn, candidates, k, variant)
                        result.update({
                            "scenario": name,
                            "trial": trial,
                            "variant_order": ">".join(order),
                            "variant_position": position,
                        })
                        timing_rows.append(result)

        b_rows: list[dict] = []
        for k_index, k in enumerate((3, 10, 100)):
            candidates = base.adversarial(topo, k, 1000)
            for budget_index, budget in enumerate(base.BUDGETS):
                # Budget rows are work/bound evidence first. Rotate order anyway so
                # their exploratory timing is not systematically biased.
                order = ORDERS[(budget_index + k_index) % len(ORDERS)]
                for position, variant in enumerate(order):
                    result = base.run_query(conn, candidates, k, variant, budget=budget)
                    result.update({
                        "scenario": "adversarial_real_topology_c1000",
                        "budget": budget,
                        "variant_order": ">".join(order),
                        "variant_position": position,
                    })
                    b_rows.append(result)
    finally:
        conn.close()

    after_identity = file_identity(db)
    after_sha = sha256_file(db)
    if before_identity != after_identity or after_sha != actual_sha:
        raise RuntimeError("SAFETY FAILURE: frozen DB identity/hash changed during read-only exploration")

    report = {
        "status": "counterbalanced_timing_not_real_ranked_candidate_replay",
        "source": {
            "path": str(db),
            "sha256": actual_sha,
            "identity": before_identity,
            "sidecars": sidecars,
            "opened_mode": "ro+immutable+query_only",
            "mutations_performed": False,
        },
        "session_columns_used": columns,
        "timing_design": {
            "repeats": args.repeats,
            "variant_orders": [">".join(order) for order in ORDERS],
            "balanced": True,
            "same_connection": True,
            "scenario_order_rotated": True,
            "warning": "Structural/random/adversarial candidates are not real session_search ranking.",
        },
        "timing_summary": base.timing_summary(timing_rows),
        "b_status": "OPEN; includes K=3/10/100 work-bound sweep",
    }
    (out_dir / "summary.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    base.write_csv(out_dir / "timing_trials.csv", timing_rows)
    base.write_csv(out_dir / "b_curve.csv", b_rows)
    (out_dir / "README.md").write_text(
        "# #54 counterbalanced follow-up\n\n"
        "This run fixes the fixed no_memo -> always_memo -> lazy_after_k order bias.\n"
        "All six variant permutations are balanced across trials; scenario block order is rotated.\n"
        "K=100 is added to the B/work sweep.\n"
        "This still does not substitute for real ranked-candidate replay.\n",
        encoding="utf-8",
    )

    print(f"OUTPUT_DIR={out_dir}")
    print(f"SHA256={actual_sha}")
    print(f"REPEATS={args.repeats}")
    print("ORDERS_BALANCED=YES")
    print("B_K=3,10,100")
    print("DONE")


if __name__ == "__main__":
    main()
