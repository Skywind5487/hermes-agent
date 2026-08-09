#!/usr/bin/env python3
"""Focused #54 resolver gate for the production VM.

This intentionally does less than ``vm_gate.py``. It answers the narrowed
architecture question only:

- simple ranked sequential point traversal, no memo;
- same scheduler with a query-local Python ``node -> root`` memo;
- Pure TEMP and Fixed3 as established references.

Normal/performance evidence uses only current shallow topology plus the real
historical depth-14 / size-15 compatibility envelope. Synthetic 5k/10k chains
are emitted separately as safety/B evidence.

This script never opens production ``state.db``.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
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

FOCUSED_ALGORITHMS = [
    ("per_seed_no_memo", per_seed_point),
    ("python_dict_memo", python_dict_memo),
    ("pure_temp_reference", pure_temp),
    ("fixed3_reference", fixed3_shared_cross),
]


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
    rows = []
    for scenario in make_pathological_scenarios():
        for budget in budgets:
            for name, fn in FOCUSED_ALGORITHMS:
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
    else:
        repeats = 9
        filler = 20_000
        budgets = (64, 128, 256, 512, 1_000, 1_500, 2_000, 5_000, 10_000)
        budget_repeats = 2
        eqp_filler = 250_000

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
        "decision_algorithms": ["per_seed_no_memo", "python_dict_memo"],
        "reference_algorithms": ["pure_temp_reference", "fixed3_reference"],
        "performance_rows": len(perf),
        "safety_rows": len(safety),
        "normal_budget": 10_000,
        "note": "Final production B is intentionally not selected by this runner; analyze focused_budget separately from normal/historical performance.",
    }
    (out / "suite_meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
