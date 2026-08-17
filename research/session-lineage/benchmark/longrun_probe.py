#!/usr/bin/env python3
"""Sustained synthetic resolver probe for e2-micro burst/throttling attribution.

This NEVER opens production state.db. It holds one disposable synthetic SQLite
scenario and one resolver algorithm constant for a sustained run, recording each
resolver call's wall/CPU/scheduler/fault/I/O/swap attribution. Use it to test
whether a latency cliff appears only after sustained CPU load.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from fixed3_optimized import fixed3_shared_cross
from per_seed import per_seed_point
from resource_probe_v2 import (
    cgroup_cpu_stat,
    findmnt,
    measured_call,
    status_vmswap_kb,
    vmstat,
)
from scenarios import ScenarioFile, make_pathological_scenarios, make_perf_scenarios, reference
from temp_memo import pure_temp

ALGORITHMS = {
    "per_seed": per_seed_point,
    "pure_temp": pure_temp,
    "fixed": fixed3_shared_cross,
}


def percentile(values, p):
    ordered = sorted(values)
    if not ordered:
        return None
    pos = (len(ordered) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def metric_summary(rows):
    if not rows:
        return {"count": 0}
    def vals(key):
        return [float(r[key]) for r in rows if r.get(key) is not None]
    out = {"count": len(rows)}
    for key in ("wall_ms", "cpu_ms", "non_cpu_wall_ms", "sched_runqueue_ms", "guest_steal_ms"):
        values = vals(key)
        out[key] = {
            "median": statistics.median(values) if values else None,
            "p95": percentile(values, 0.95) if values else None,
            "max": max(values) if values else None,
        }
    return out


def time_bins(rows, width_s=5.0):
    bins = {}
    for row in rows:
        idx = int(float(row["elapsed_s"]) // width_s)
        bins.setdefault(idx, []).append(row)
    out = []
    for idx in sorted(bins):
        chunk = bins[idx]
        out.append({
            "start_s": idx * width_s,
            "end_s": (idx + 1) * width_s,
            **metric_summary(chunk),
        })
    return out


def write_csv(path, rows):
    fields = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fields.append(key)
                seen.add(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def select_scenario(name):
    if name == "path5000":
        scenario = next(s for s in make_pathological_scenarios() if s.name == "path_depth5000_concentrated_c300")
        return scenario, 5_000
    if name == "blocked":
        scenario = next(s for s in make_perf_scenarios() if s.name == "blocked_50_lineages_depth5_k3")
        return scenario, 10_000
    raise ValueError(name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--duration-s", type=float, default=120.0)
    parser.add_argument("--algorithm", choices=sorted(ALGORITHMS), default="pure_temp")
    parser.add_argument("--scenario", choices=("path5000", "blocked"), default="path5000")
    parser.add_argument("--budget", type=int, default=None, help="override scenario default global work budget")
    parser.add_argument("--max-iterations", type=int, default=100_000)
    parser.add_argument("--bin-s", type=float, default=5.0)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    scenario, default_budget = select_scenario(args.scenario)
    budget = args.budget if args.budget is not None else default_budget
    fn = ALGORITHMS[args.algorithm]

    receipt = {
        "probe": "longrun_probe",
        "production_db_opened": False,
        "timing_window": "resolver-call-only; attribution snapshots outside timer",
        "algorithm": args.algorithm,
        "scenario": scenario.name,
        "budget": budget,
        "duration_s_requested": args.duration_s,
        "max_iterations": args.max_iterations,
        "tmpdir": tempfile.gettempdir(),
        "tmpdir_mount": findmnt(tempfile.gettempdir()),
        "cwd_mount": findmnt(str(Path.cwd())),
        "cgroup_cpu_before": cgroup_cpu_stat(),
        "vmstat_before": vmstat(),
        "process_vmswap_kb_before": status_vmswap_kb(),
    }

    sf = ScenarioFile(scenario, filler=20_000, journal_mode="WAL", temp_store="MEMORY", reader_mode="ro")
    rows = []
    try:
        conn = sf.open()
        try:
            expected = reference(conn, scenario.k)
            db = SimpleNamespace(conn=conn, scenario=scenario, observe_temp=False, after_snapshot_read=None)
            warm = fn(db, scenario.k, budget)
            if warm["roots"] != expected and not warm.get("bound_hit"):
                raise RuntimeError("warmup result mismatch without bound hit")

            wall_start = time.perf_counter()
            cpu_start = time.process_time()
            iteration = 0
            while iteration < args.max_iterations:
                if iteration and time.perf_counter() - wall_start >= args.duration_s:
                    break
                iteration += 1
                result, metrics = measured_call(fn, db, scenario.k, budget)
                elapsed_s = time.perf_counter() - wall_start
                cumulative_cpu_s = time.process_time() - cpu_start
                row = {
                    "iteration": iteration,
                    "elapsed_s": elapsed_s,
                    "cumulative_process_cpu_s": cumulative_cpu_s,
                    "cumulative_cpu_over_wall": cumulative_cpu_s / elapsed_s if elapsed_s else None,
                    "exact": result["roots"] == expected,
                    "work": result.get("work"),
                    "bound_hit": result.get("bound_hit"),
                    "statements": result.get("statements"),
                    "candidates": result.get("candidates"),
                    **metrics,
                }
                rows.append(row)
                if iteration == 1 or iteration % 25 == 0 or metrics["wall_ms"] >= 100.0:
                    print(
                        f"#{iteration:05d} t={elapsed_s:8.3f}s wall={metrics['wall_ms']:8.3f}ms "
                        f"cpu={metrics['cpu_ms']:8.3f}ms noncpu={metrics['non_cpu_wall_ms']:8.3f}ms "
                        f"runq={metrics['sched_runqueue_ms']:8.3f}ms steal={metrics['guest_steal_ms']:8.3f}ms "
                        f"majflt={metrics['major_faults']} read={metrics['read_bytes']} swapin={metrics['system_pswpin']}",
                        flush=True,
                    )
        finally:
            conn.close()
    finally:
        sf.close()

    receipt.update({
        "duration_s_actual": rows[-1]["elapsed_s"] if rows else 0.0,
        "iterations": len(rows),
        "cgroup_cpu_after": cgroup_cpu_stat(),
        "vmstat_after": vmstat(),
        "process_vmswap_kb_after": status_vmswap_kb(),
    })

    early_n = min(25, len(rows))
    early = rows[:early_n]
    late = rows[-early_n:] if early_n else []
    baseline = statistics.median([r["wall_ms"] for r in early]) if early else None
    cliff_threshold = max(100.0, baseline * 3.0) if baseline is not None else None
    cliffs = [r for r in rows if cliff_threshold is not None and r["wall_ms"] >= cliff_threshold]
    summary = {
        "baseline_first_calls": metric_summary(early),
        "tail_last_calls": metric_summary(late),
        "all": metric_summary(rows),
        "baseline_wall_median_ms": baseline,
        "cliff_threshold_ms": cliff_threshold,
        "cliff_count": len(cliffs),
        "first_cliff": cliffs[0] if cliffs else None,
        "worst_cliffs": sorted(cliffs, key=lambda r: r["wall_ms"], reverse=True)[:20],
        "time_bins": time_bins(rows, args.bin_s),
    }

    write_csv(args.out / "longrun.csv", rows)
    (args.out / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({
        "iterations": len(rows),
        "duration_s": receipt["duration_s_actual"],
        "baseline_wall_median_ms": baseline,
        "cliff_threshold_ms": cliff_threshold,
        "cliff_count": len(cliffs),
        "first_cliff_elapsed_s": cliffs[0]["elapsed_s"] if cliffs else None,
        "max_wall_ms": max((r["wall_ms"] for r in rows), default=None),
        "final_cumulative_cpu_over_wall": rows[-1]["cumulative_cpu_over_wall"] if rows else None,
    }, indent=2))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
