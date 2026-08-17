#!/usr/bin/env python3
"""Attribute resolver stalls to CPU, scheduler wait, faults, I/O, or swap.

The resolver timing window contains ONLY the resolver call. Expensive /proc and
cgroup counter reads happen outside that window. Run the same command with
TMPDIR=/tmp and TMPDIR=/dev/shm to compare backing stores without touching
production state.db.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import resource
import subprocess
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from fixed3_optimized import fixed3_shared_cross
from per_seed import per_seed_point
from scenarios import ScenarioFile, make_pathological_scenarios, make_perf_scenarios, reference
from temp_memo import pure_temp

ALGORITHMS = [
    ("per_seed_point", per_seed_point),
    ("pure_temp", pure_temp),
    ("fixed3_shared_bound", fixed3_shared_cross),
]


def read_kv_file(path):
    result = {}
    try:
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1].lstrip("-").isdigit():
                result[parts[0].rstrip(":")] = int(parts[1])
    except OSError:
        pass
    return result


def proc_io():
    return read_kv_file("/proc/self/io")


def vmstat():
    raw = read_kv_file("/proc/vmstat")
    return {k: raw.get(k, 0) for k in ("pswpin", "pswpout", "pgmajfault")}


def status_vmswap_kb():
    try:
        for line in Path("/proc/self/status").read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("VmSwap:"):
                return int(line.split()[1])
    except OSError:
        pass
    return 0


def cgroup_cpu_stat():
    return read_kv_file("/sys/fs/cgroup/cpu.stat")


def proc_stat_steal_ticks():
    try:
        line = Path("/proc/stat").read_text(encoding="utf-8", errors="replace").splitlines()[0]
        parts = line.split()
        if parts and parts[0] == "cpu" and len(parts) > 8:
            return int(parts[8])
    except (OSError, ValueError, IndexError):
        pass
    return 0


def self_schedstat():
    """Linux /proc/self/schedstat: CPU runtime ns, runqueue wait ns, timeslices."""
    try:
        parts = Path("/proc/self/schedstat").read_text(encoding="utf-8", errors="replace").split()
        if len(parts) >= 3:
            return {
                "runtime_ns": int(parts[0]),
                "runqueue_ns": int(parts[1]),
                "timeslices": int(parts[2]),
            }
    except (OSError, ValueError):
        pass
    return {"runtime_ns": 0, "runqueue_ns": 0, "timeslices": 0}


def counters():
    ru = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "minflt": ru.ru_minflt,
        "majflt": ru.ru_majflt,
        "inblock": ru.ru_inblock,
        "oublock": ru.ru_oublock,
        "nvcsw": ru.ru_nvcsw,
        "nivcsw": ru.ru_nivcsw,
        "proc_io": proc_io(),
        "vmstat": vmstat(),
        "vmswap_kb": status_vmswap_kb(),
        "cgroup_cpu": cgroup_cpu_stat(),
        "steal_ticks": proc_stat_steal_ticks(),
        "schedstat": self_schedstat(),
    }


def delta(a, b, key):
    return b.get(key, 0) - a.get(key, 0)


def nested_delta(a, b, group, key):
    return b.get(group, {}).get(key, 0) - a.get(group, {}).get(key, 0)


def measured_call(fn, db, k, budget):
    before = counters()
    wall0 = time.perf_counter_ns()
    cpu0 = time.process_time_ns()
    result = fn(db, k, budget)
    cpu1 = time.process_time_ns()
    wall1 = time.perf_counter_ns()
    after = counters()

    hz = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
    wall_ms = (wall1 - wall0) / 1e6
    cpu_ms = (cpu1 - cpu0) / 1e6
    return result, {
        "wall_ms": wall_ms,
        "cpu_ms": cpu_ms,
        "non_cpu_wall_ms": max(0.0, wall_ms - cpu_ms),
        "cpu_over_wall": cpu_ms / wall_ms if wall_ms else None,
        "minor_faults": delta(before, after, "minflt"),
        "major_faults": delta(before, after, "majflt"),
        "block_input_ops": delta(before, after, "inblock"),
        "block_output_ops": delta(before, after, "oublock"),
        "voluntary_ctx_switches": delta(before, after, "nvcsw"),
        "involuntary_ctx_switches": delta(before, after, "nivcsw"),
        "read_bytes": nested_delta(before, after, "proc_io", "read_bytes"),
        "write_bytes": nested_delta(before, after, "proc_io", "write_bytes"),
        "sys_read_calls": nested_delta(before, after, "proc_io", "syscr"),
        "sys_write_calls": nested_delta(before, after, "proc_io", "syscw"),
        "system_pswpin": nested_delta(before, after, "vmstat", "pswpin"),
        "system_pswpout": nested_delta(before, after, "vmstat", "pswpout"),
        "system_pgmajfault": nested_delta(before, after, "vmstat", "pgmajfault"),
        "process_vmswap_kb_before": before["vmswap_kb"],
        "process_vmswap_kb_after": after["vmswap_kb"],
        "cgroup_nr_throttled": nested_delta(before, after, "cgroup_cpu", "nr_throttled"),
        "cgroup_throttled_usec": nested_delta(before, after, "cgroup_cpu", "throttled_usec"),
        "guest_steal_ms": delta(before, after, "steal_ticks") * 1000.0 / hz,
        "sched_runtime_ms": nested_delta(before, after, "schedstat", "runtime_ns") / 1e6,
        "sched_runqueue_ms": nested_delta(before, after, "schedstat", "runqueue_ns") / 1e6,
        "sched_timeslices": nested_delta(before, after, "schedstat", "timeslices"),
    }


def findmnt(path):
    try:
        return subprocess.run(
            ["findmnt", "-T", path, "-o", "SOURCE,FSTYPE,OPTIONS", "-n"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        ).stdout.strip()
    except OSError as exc:
        return f"ERROR: {exc!r}"


def scenario_set():
    perf = {s.name: s for s in make_perf_scenarios()}
    path = {s.name: s for s in make_pathological_scenarios()}
    return [
        (perf["blocked_50_lineages_depth5_k3"], 10_000),
        (path["path_depth5000_concentrated_c300"], 5_000),
    ]


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=12)
    parser.add_argument("--cooldown-ms", type=float, default=0.0)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    tmpdir = tempfile.gettempdir()
    receipt = {
        "probe_version": 2,
        "timing_window": "resolver-call-only; /proc+cgroup snapshots outside timer",
        "tmpdir": tmpdir,
        "tmpdir_mount": findmnt(tmpdir),
        "cwd_mount": findmnt(str(Path.cwd())),
        "python": os.sys.executable,
        "pid": os.getpid(),
        "repeats": args.repeats,
        "cooldown_ms": args.cooldown_ms,
        "cpu_stat_before": cgroup_cpu_stat(),
        "vmstat_before": vmstat(),
        "process_vmswap_kb_before": status_vmswap_kb(),
    }
    rows = []

    for scenario, budget in scenario_set():
        for algorithm_name, fn in ALGORITHMS:
            sf = ScenarioFile(
                scenario,
                filler=20_000,
                journal_mode="WAL",
                temp_store="MEMORY",
                reader_mode="ro",
            )
            try:
                conn = sf.open()
                try:
                    expected = reference(conn, scenario.k)
                    db = SimpleNamespace(
                        conn=conn,
                        scenario=scenario,
                        observe_temp=False,
                        after_snapshot_read=None,
                    )
                    warm = fn(db, scenario.k, budget)
                    if warm["roots"] != expected and not warm.get("bound_hit"):
                        raise RuntimeError(f"warmup mismatch: {scenario.name} {algorithm_name}")
                    for iteration in range(1, args.repeats + 1):
                        result, metrics = measured_call(fn, db, scenario.k, budget)
                        exact = result["roots"] == expected
                        rows.append({
                            "scenario": scenario.name,
                            "algorithm": algorithm_name,
                            "iteration": iteration,
                            "budget": budget,
                            "exact": exact,
                            "work": result.get("work"),
                            "bound_hit": result.get("bound_hit"),
                            "statements": result.get("statements"),
                            "candidates": result.get("candidates"),
                            **metrics,
                        })
                        print(
                            f"{scenario.name:38s} {algorithm_name:20s} #{iteration:02d} "
                            f"wall={metrics['wall_ms']:.3f}ms cpu={metrics['cpu_ms']:.3f}ms "
                            f"noncpu={metrics['non_cpu_wall_ms']:.3f}ms "
                            f"runq={metrics['sched_runqueue_ms']:.3f}ms "
                            f"majflt={metrics['major_faults']} read={metrics['read_bytes']} "
                            f"write={metrics['write_bytes']} steal={metrics['guest_steal_ms']:.3f}ms"
                        )
                        if args.cooldown_ms:
                            time.sleep(args.cooldown_ms / 1000.0)
                finally:
                    conn.close()
            finally:
                sf.close()

    receipt.update({
        "cpu_stat_after": cgroup_cpu_stat(),
        "vmstat_after": vmstat(),
        "process_vmswap_kb_after": status_vmswap_kb(),
    })
    write_csv(args.out / "resource_probe.csv", rows)
    (args.out / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
