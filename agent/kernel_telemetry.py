"""Request-window Linux kernel/process telemetry using procfs and cgroupfs."""

from __future__ import annotations

import datetime as dt
import os
import socket
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable


_COUNTER_PREFIXES = (
    "sched_",
    "proc_",
    "thread_",
    "vm_",
    "cgroup_",
    "disk_",
    "state_db_",
)


def _read_int(path: Path, default=None):
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return default


def _read_key_values(path: Path) -> Dict[str, int]:
    result: Dict[str, int] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) >= 2:
                try:
                    result[parts[0]] = int(parts[1])
                except ValueError:
                    continue
    except OSError:
        pass
    return result


def _proc_stat(pid: int, tid: int | None) -> Dict[str, int]:
    path = Path("/proc") / str(pid) / "task" / str(tid or threading.get_native_id()) / "stat"
    try:
        text = path.read_text(encoding="utf-8")
        tail = text[text.rfind(")") + 2 :].split()
        # tail[11] = field 14 utime; tail[12] = stime; field 42 is processor.
        return {
            "utime_ticks": int(tail[11]),
            "stime_ticks": int(tail[12]),
            "processor": int(tail[36]) if len(tail) > 36 else -1,
        }
    except (OSError, ValueError, IndexError):
        return {}


def _proc_status(pid: int) -> Dict[str, int]:
    values: Dict[str, int] = {}
    try:
        lines = (Path("/proc") / str(pid) / "status").read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        key, _, raw = line.partition(":")
        if key in {"voluntary_ctxt_switches", "nonvoluntary_ctxt_switches"}:
            try:
                values[key] = int(raw.strip())
            except ValueError:
                pass
        elif key in {"VmRSS", "VmSwap", "VmSize"}:
            try:
                values[key] = int(raw.strip().split()[0]) * 1024
            except (ValueError, IndexError):
                pass
    return values


def _proc_io(pid: int) -> Dict[str, int]:
    result: Dict[str, int] = {}
    try:
        lines = (Path("/proc") / str(pid) / "io").read_text(encoding="utf-8").splitlines()
    except OSError:
        return result
    for line in lines:
        key, _, raw = line.partition(":")
        if key in {"read_bytes", "write_bytes", "rchar", "wchar", "syscr", "syscw", "cancelled_write_bytes"}:
            try:
                result[key] = int(raw.strip())
            except ValueError:
                pass
    return result


def _schedstat(pid: int, tid: int | None) -> Dict[str, int]:
    path = Path("/proc") / str(pid) / "task" / str(tid or threading.get_native_id()) / "schedstat"
    try:
        values = [int(value) for value in path.read_text(encoding="utf-8").split()[:3]]
    except (OSError, ValueError):
        return {}
    return {
        "sched_exec_runtime_ns": values[0] if len(values) > 0 else 0,
        "sched_run_delay_ns": values[1] if len(values) > 1 else 0,
        "sched_timeslices": values[2] if len(values) > 2 else 0,
    }


def _pressure(name: str) -> Dict[str, float] | None:
    try:
        text = Path("/proc/pressure") .joinpath(name).read_text(encoding="utf-8")
    except OSError:
        return None
    result: Dict[str, float] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        prefix = parts[0]
        for item in parts[1:]:
            key, _, value = item.partition("=")
            if key in {"avg10", "avg60", "avg300"}:
                try:
                    result[f"{prefix}_{key}"] = float(value)
                except ValueError:
                    pass
    return result


def _diskstats() -> Dict[str, Dict[str, int]]:
    result: Dict[str, Dict[str, int]] = {}
    try:
        lines = Path("/proc/diskstats").read_text(encoding="utf-8").splitlines()
    except OSError:
        return result
    for line in lines:
        parts = line.split()
        if len(parts) < 14:
            continue
        name = parts[2]
        try:
            result[name] = {
                "reads_completed": int(parts[3]),
                "sectors_read": int(parts[5]),
                "io_in_progress": int(parts[11]),
                "io_ticks_ms": int(parts[12]),
                "weighted_io_ms": int(parts[13]),
                "writes_completed": int(parts[7]),
                "sectors_written": int(parts[9]),
            }
        except ValueError:
            continue
    return result


def _root_device(db_path: Path | None) -> str | None:
    if db_path is None:
        return None
    try:
        device = os.stat(db_path).st_dev
        major = os.major(device)
        minor = os.minor(device)
        link = Path("/sys/dev/block") / f"{major}:{minor}"
        return link.resolve().name
    except (OSError, ValueError):
        return None


def _read_cgroup_io(path: Path) -> Dict[str, int]:
    """Aggregate cgroup v2 io.stat counters across all listed devices."""
    result: Dict[str, int] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return result
    for line in lines:
        parts = line.split()
        for token in parts[1:]:
            key, separator, raw = token.partition("=")
            if not separator:
                continue
            try:
                result[key] = result.get(key, 0) + int(raw)
            except ValueError:
                continue
    return result


def _cgroup_snapshot() -> Dict[str, int]:
    result = {}
    result.update({f"cpu_{key}": value for key, value in _read_key_values(Path("/sys/fs/cgroup/cpu.stat")).items()})
    result.update({f"io_{key}": value for key, value in _read_cgroup_io(Path("/sys/fs/cgroup/io.stat")).items()})
    return result


def capture_snapshot(pid: int | None = None, tid: int | None = None, db_path: Path | None = None) -> Dict[str, Any]:
    pid = int(pid or os.getpid())
    tid = int(tid or threading.get_native_id())
    db_path = Path(db_path) if db_path is not None else None
    process_stat = _proc_stat(pid, tid)
    process_status = _proc_status(pid)
    io = _proc_io(pid)
    sched = _schedstat(pid, tid)
    vmstat = _read_key_values(Path("/proc/vmstat"))
    disk = _diskstats()
    root_device = _root_device(db_path)
    root_disk = disk.get(root_device or "", {})
    cgroup = _cgroup_snapshot()
    wal = Path(str(db_path) + "-wal") if db_path else None
    shm = Path(str(db_path) + "-shm") if db_path else None
    state_size = db_path.stat().st_size if db_path and db_path.exists() else None
    pressure = {name: _pressure(name) for name in ("cpu", "io", "memory")}
    return {
        "machine_wall_time": dt.datetime.now(dt.timezone.utc).isoformat(),
        "machine_monotonic_ns": time.monotonic_ns(),
        "boot_id": (Path("/proc/sys/kernel/random/boot_id").read_text().strip() if Path("/proc/sys/kernel/random/boot_id").exists() else None),
        "hostname": socket.gethostname(),
        "pid": pid,
        "thread_id": tid,
        "thread_cpu_ns": time.thread_time_ns() if tid == threading.get_native_id() else None,
        "sched_exec_runtime_ns": sched.get("sched_exec_runtime_ns"),
        "sched_run_delay_ns": sched.get("sched_run_delay_ns"),
        "sched_timeslices": sched.get("sched_timeslices"),
        "proc_utime_ticks": process_stat.get("utime_ticks"),
        "proc_stime_ticks": process_stat.get("stime_ticks"),
        "proc_read_bytes": io.get("read_bytes"),
        "proc_write_bytes": io.get("write_bytes"),
        "proc_rchar": io.get("rchar"),
        "proc_wchar": io.get("wchar"),
        "proc_syscr": io.get("syscr"),
        "proc_syscw": io.get("syscw"),
        "proc_rss_bytes": process_status.get("VmRSS"),
        "proc_swap_bytes": process_status.get("VmSwap"),
        "voluntary_ctxt_switches": process_status.get("voluntary_ctxt_switches"),
        "nonvoluntary_ctxt_switches": process_status.get("nonvoluntary_ctxt_switches"),
        "vm_pgmajfault": vmstat.get("pgmajfault"),
        "vm_pswpin": vmstat.get("pswpin"),
        "vm_pswpout": vmstat.get("pswpout"),
        "vm_pgscan_kswapd": vmstat.get("pgscan_kswapd"),
        "vm_pgsteal_kswapd": vmstat.get("pgsteal_kswapd"),
        "vm_allocstall_normal": vmstat.get("allocstall_normal"),
        "vm_compact_stall": vmstat.get("compact_stall"),
        "cgroup_cpu_usage_usec": cgroup.get("cpu_usage_usec"),
        "cgroup_cpu_user_usec": cgroup.get("cpu_user_usec"),
        "cgroup_cpu_system_usec": cgroup.get("cpu_system_usec"),
        "cgroup_cpu_nr_throttled": cgroup.get("cpu_nr_throttled"),
        "cgroup_cpu_throttled_usec": cgroup.get("cpu_throttled_usec"),
        "cgroup_io_rbytes": cgroup.get("io_rbytes"),
        "cgroup_io_wbytes": cgroup.get("io_wbytes"),
        "cgroup_io_rios": cgroup.get("io_rios"),
        "cgroup_io_wios": cgroup.get("io_wios"),
        "psi": pressure,
        "disk_root_device": root_device,
        "disk_root_reads_completed": root_disk.get("reads_completed"),
        "disk_root_writes_completed": root_disk.get("writes_completed"),
        "disk_root_sectors_read": root_disk.get("sectors_read"),
        "disk_root_sectors_written": root_disk.get("sectors_written"),
        "disk_root_io_ticks_ms": root_disk.get("io_ticks_ms"),
        "disk_root_weighted_io_ms": root_disk.get("weighted_io_ms"),
        "state_db_size_bytes": state_size,
        "state_db_wal_size_bytes": wal.stat().st_size if wal and wal.exists() else 0,
        "state_db_shm_size_bytes": shm.stat().st_size if shm and shm.exists() else 0,
    }


def _value_delta(before: Any, after: Any):
    if before is None or after is None:
        return None
    try:
        return after - before
    except TypeError:
        return None


def diff_snapshot(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "boot_id": after.get("boot_id"),
        "pid": after.get("pid"),
        "thread_id": after.get("thread_id"),
        "wall_elapsed_ns": _value_delta(before.get("machine_monotonic_ns"), after.get("machine_monotonic_ns")),
    }
    for key, value in after.items():
        if key in {"machine_wall_time", "machine_monotonic_ns", "boot_id", "pid", "thread_id", "psi", "disk_root_device"}:
            continue
        if any(key.startswith(prefix) for prefix in _COUNTER_PREFIXES) or key == "thread_cpu_ns":
            result[f"{key}_delta"] = _value_delta(before.get(key), value)
    for pressure_name in ("cpu", "io", "memory"):
        for stall in ("some", "full"):
            key = f"{stall}_avg10"
            result[f"psi_{pressure_name}_{stall}_avg10_before"] = (before.get("psi", {}).get(pressure_name) or {}).get(key)
            result[f"psi_{pressure_name}_{stall}_avg10_after"] = (after.get("psi", {}).get(pressure_name) or {}).get(key)
    return result
