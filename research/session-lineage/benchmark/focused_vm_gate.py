#!/usr/bin/env python3
"""Focused #54 resolver gate for the production VM.

This intentionally does less than ``vm_gate.py``. It answers the narrowed
architecture question only:

- simple ranked sequential point traversal, no memo;
- same scheduler with a query-local Python ``node -> root`` memo;
- Pure TEMP and Fixed3 as established references.

Normal/performance evidence uses only current shallow topology plus the real
historical depth-14 / size-15 compatibility envelope. Synthetic 5k/10k chains
are emitted separately as safety/B evidence for the two actual decision
candidates only.

Full mode deliberately burns CPU before timing so an e2-micro run is measured
after the known shared-core burst window rather than accidentally reporting only
burst-state latency. Quick smoke skips that precondition.

This script never opens production ``state.db``.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from fixed3_optimized import fixed3_shared_cross
from focused_scenarios import make_focused_scenarios
from gate_sweeps import eqp_probe, repeated, write_csv
from per_seed import per_seed_point
from python_memo import python_dict_memo
from scenarios import make_pathological_scenarios
from temp_memo import pure_temp
from vm_gate import machine_receipt

HERE = Path(__file__).resolve().parent

DECISION_ALGORITHMS = [
    ("per_seed_no_memo", per_seed_point),
    ("python_dict_memo", python_dict_memo),
]
REFERENCE_ALGORITHMS = [
    ("pure_temp_reference", pure_temp),
    ("fixed3_reference", fixed3_shared_cross),
]
FOCUSED_ALGORITHMS = DECISION_ALGORITHMS + REFERENCE_ALGORITHMS


def cpu_precondition(seconds: float):
    """Consume CPU continuously before e2-micro timing.

    #54's long-run probe found the deployment VM transitions from burst to its
    sustained shared-core regime after roughly 28 seconds of CPU-heavy work.
    Sleeping would not consume burst credits, so this intentionally performs a
    tiny integer workload until the requested wall duration has elapsed.
    """
    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    deadline = wall_start + seconds
    x = 0x12345678
    iterations = 0
    while time.perf_counter() < deadline:
        for _ in range(50_000):
            x = ((x * 1664525) + 1013904223) & 0xFFFFFFFF
        iterations += 50_000
    return {
        "requested_seconds": seconds,
        "wall_seconds": time.perf_counter() - wall_start,
        "process_cpu_seconds": time.process_time() - cpu_start,
        "iterations": iterations,
        "checksum": x,
    }


def focused_performance(*, repeats: int, filler: int, budget: int):
    rows = []
    for scenario in make_focused_scenarios():
        for name, fn in FOCUSED_ALGORITHMS:
            result = repeated(
                scenario,
                fn,
                filler=filler,
                B=budget,
                repeats=repeats,
            )
            rows.append(
                {
                    "workload_class": "normal" if "normal" in scenario.name else "historical_compatibility",
                    "scenario": scenario.name,
                    "description": scenario.description,
                    "candidates_input": len(scenario.candidates),
                    "k": scenario.k,
                    "algorithm": name,
                    "filler": filler,
                    "budget": budget,
                    **result,
                }
            )
    return rows


def focused_budget(*, repeats: int, budgets: tuple[int, ...]):
    """Safety/B curve for production decision candidates only."""
    rows = []
    for scenario in make_pathological_scenarios():
        for budget in budgets:
            for name, fn in DECISION_ALGORITHMS:
                result = repeated(
                    scenario,
                    fn,
                    filler=0,
                    B=budget,
                    repeats=repeats,
                    warmup=False,
                )
                rows.append(
                    {
                        "workload_class": "safety_only",
                        "scenario": scenario.name,
                        "description": scenario.description,
                        "candidates_input": len(scenario.candidates),
                        "k": scenario.k,
                        "algorithm": name,
                        "budget": budget,
                        **result,
                    }
                )
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(HERE / "out" / "focused-vm-gate"))
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument(
        "--precondition-seconds",
        type=float,
        default=None,
        help="CPU burn before timing; default 35s in full mode and 0 in quick mode",
    )
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "receipt.json").write_text(
        json.dumps(machine_receipt(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    if not args.skip_tests:
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", str(HERE / "tests"), "-v"],
            cwd=HERE,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        (out / "tests.txt").write_text(proc.stdout, encoding="utf-8")
        print(proc.stdout, end="")
        if proc.returncode:
            raise SystemExit(proc.returncode)

    if args.quick:
        repeats = 2
        filler = 3_000
        budgets = (64, 256, 1_000, 5_000)
        budget_repeats = 1
        eqp_filler = 20_000
        default_precondition = 0.0
    else:
        repeats = 9
        filler = 20_000
        budgets = (64, 128, 256, 512, 1_000, 1_500, 2_000, 5_000, 10_000)
        budget_repeats = 2
        eqp_filler = 250_000
        default_precondition = 35.0

    precondition_seconds = default_precondition if args.precondition_seconds is None else max(0.0, args.precondition_seconds)
    if precondition_seconds:
        print(f"==> sustained CPU precondition ({precondition_seconds:g}s)", flush=True)
        precondition = cpu_precondition(precondition_seconds)
    else:
        precondition = {
            "requested_seconds": 0.0,
            "wall_seconds": 0.0,
            "process_cpu_seconds": 0.0,
            "iterations": 0,
            "checksum": None,
        }
    (out / "precondition.json").write_text(
        json.dumps(precondition, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("==> focused_gate.csv", flush=True)
    perf = focused_performance(repeats=repeats, filler=filler, budget=10_000)
    write_csv(out / "focused_gate.csv", perf)

    print("==> focused_budget.csv", flush=True)
    safety = focused_budget(repeats=budget_repeats, budgets=budgets)
    write_csv(out / "focused_budget.csv", safety)

    print("==> fixed3_eqp.json", flush=True)
    eqp = eqp_probe(filler=eqp_filler)
    (out / "fixed3_eqp.json").write_text(
        json.dumps(eqp, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    if eqp["full_child_scan"] or eqp["suspicious_sessions_scan"]:
        raise SystemExit(f"EQP gate failed: {eqp['bad_details']}")

    meta = {
        "quick": args.quick,
        "production_db_opened": False,
        "decision_algorithms": [name for name, _ in DECISION_ALGORITHMS],
        "reference_algorithms": [name for name, _ in REFERENCE_ALGORITHMS],
        "performance_rows": len(perf),
        "safety_rows": len(safety),
        "normal_budget": 10_000,
        "precondition_seconds": precondition_seconds,
        "timing_regime": "burst-state/unspecified" if precondition_seconds == 0 else "post-CPU-precondition; intended sustained e2-micro regime",
        "note": "Final production B is intentionally not selected by this runner; focused_budget contains only the two production decision candidates and must be analyzed separately from normal/historical performance.",
    }
    (out / "suite_meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
