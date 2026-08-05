"""Rate-limited heap release for long-lived Hermes gateway processes.

On Linux/glibc, ``malloc_trim(0)`` can return pages from freed Python/C
allocations to the OS.  Other platforms and allocators are safe no-ops.
Behavior is configured under ``context.memory_trim`` in ``config.yaml``.

Instrumentation (branch mem-trim-instrumentation, 2026-08-05):
- T1: split gc.collect() and malloc_trim(0) timing (gc_ms / trim_ms) so the
  freeze mechanism can be attributed (GIL-holding gc vs C-level trim).
- T2: malloc_info heap stats (system/free bytes -> frag_pct) for fragment
  rate measurement.
- T3: VmSwap before/after so swap pressure can be correlated with duration.
- threshold_mb: RSS low-water gate — skip trim entirely below the threshold
  (config ``context.memory_trim.threshold_mb``).
"""

from __future__ import annotations

import ctypes
import gc
import logging
import os
import platform
import re
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_COOLDOWN_SECONDS = 60.0
_DEFAULT_LOG_EVERY_N = 1
_DEFAULT_INFO_LOG_MIN_DELTA_MB = 0.0
_DEFAULT_THRESHOLD_MB = None  # None = gate disabled (trim always allowed)
_trim_lock = threading.Lock()
_last_trim_monotonic = 0.0
_probe_done = False
_malloc_trim: Callable[[int], int] | None = None
_trim_call_count = 0


def _config_settings() -> tuple[bool, float, int, float, float | None]:
    """Return fail-open settings from the normal Hermes config path."""
    enabled = True
    cooldown: Any = _DEFAULT_COOLDOWN_SECONDS
    log_every_n: Any = _DEFAULT_LOG_EVERY_N
    info_log_min_delta_mb: Any = _DEFAULT_INFO_LOG_MIN_DELTA_MB
    threshold_mb: Any = _DEFAULT_THRESHOLD_MB
    try:
        # Read-only access: settings are only .get()ed and coerced, never
        # mutated — use the no-deepcopy variant. This runs on EVERY trim
        # attempt (before the cooldown check), and generating a full-config
        # deepcopy per attempt is exactly the allocator garbage this module
        # exists to release.
        from hermes_cli.config import load_config_readonly

        config = load_config_readonly() or {}
        context = config.get("context") if isinstance(config, dict) else None
        settings = context.get("memory_trim") if isinstance(context, dict) else None
        if isinstance(settings, dict):
            configured_enabled = settings.get("enabled")
            if isinstance(configured_enabled, bool):
                enabled = configured_enabled
            cooldown = settings.get("cooldown_seconds", _DEFAULT_COOLDOWN_SECONDS)
            log_every_n = settings.get("log_every_n", _DEFAULT_LOG_EVERY_N)
            info_log_min_delta_mb = settings.get(
                "info_log_min_delta_mb", _DEFAULT_INFO_LOG_MIN_DELTA_MB
            )
            threshold_mb = settings.get("threshold_mb", _DEFAULT_THRESHOLD_MB)
    except Exception:
        pass
    return (
        enabled,
        _cooldown_seconds(cooldown),
        _log_every_n(log_every_n),
        _nonnegative_float(info_log_min_delta_mb, _DEFAULT_INFO_LOG_MIN_DELTA_MB),
        _threshold_mb(threshold_mb),
    )


def _threshold_mb(value: Any) -> float | None:
    """Coerce threshold_mb; non-positive or non-numeric -> disabled (None)."""
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _cooldown_seconds(value: Any) -> float:
    if isinstance(value, bool):
        return _DEFAULT_COOLDOWN_SECONDS
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return _DEFAULT_COOLDOWN_SECONDS


def _log_every_n(value: Any) -> int:
    if isinstance(value, bool):
        return _DEFAULT_LOG_EVERY_N
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return _DEFAULT_LOG_EVERY_N


def _nonnegative_float(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return default


def _read_proc_status() -> str | None:
    """Read Linux process status without making non-Linux callers special-case."""
    if sys.platform != "linux":
        return None
    try:
        return Path("/proc/self/status").read_text(encoding="utf-8")
    except OSError:
        return None


def collect_memory_snapshot(history_bytes: int | None = None) -> dict[str, int | None]:
    """Return lightweight process-memory telemetry for trim logs and canaries.

    ``VmRSS``, ``RssAnon`` and ``VmSwap`` are Linux-only best effort fields.
    The helper is intentionally dependency-free so allocation recovery never
    requires psutil.
    """
    snapshot: dict[str, int | None] = {
        "rss_kib": None,
        "rss_anon_kib": None,
        "vm_swap_kib": None,
        "thread_count": threading.active_count(),
    }
    status = _read_proc_status()
    if status:
        for line in status.splitlines():
            key, separator, raw_value = line.partition(":")
            if not separator or key not in {"VmRSS", "RssAnon", "VmSwap"}:
                continue
            value = raw_value.strip().split(maxsplit=1)
            if value and value[0].isdigit():
                if key == "VmRSS":
                    snapshot["rss_kib"] = int(value[0])
                elif key == "RssAnon":
                    snapshot["rss_anon_kib"] = int(value[0])
                elif key == "VmSwap":
                    snapshot["vm_swap_kib"] = int(value[0])
    if isinstance(history_bytes, int) and history_bytes >= 0:
        snapshot["history_bytes"] = history_bytes
    return snapshot


def _malloc_info_stats() -> dict[str, float | None] | None:
    """Collect glibc heap stats via malloc_info(3): system/free bytes -> frag%.

    T2 instrumentation (branch mem-trim-instrumentation). Best effort; returns
    None on any failure (non-glibc, permissions, parse error).
    """
    try:
        libc = ctypes.CDLL(None)
        libc.malloc_info.argtypes = [ctypes.c_int, ctypes.c_void_p]
        libc.malloc_info.restype = ctypes.c_int
        libc.fopen.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        libc.fopen.restype = ctypes.c_void_p
        libc.fclose.argtypes = [ctypes.c_void_p]
        libc.fclose.restype = ctypes.c_int
        path = f"/tmp/hermes_malloc_info_{os.getpid()}.xml"
        f = libc.fopen(path.encode(), b"wb")
        if not f:
            return None
        try:
            if libc.malloc_info(0, f) != 0:
                return None
        finally:
            libc.fclose(f)
        try:
            txt = Path(path).read_text(encoding="utf-8", errors="replace")
        finally:
            Path(path).unlink(missing_ok=True)
        m_sys = re.findall(r'<system type="current" size="(\d+)"/>', txt)
        m_free = re.findall(r'<total type="(?:fast|rest)" size="(\d+)"/>', txt)
        system = sum(int(s) for s in m_sys) if m_sys else 0
        free = sum(int(s) for s in m_free) if m_free else 0
        frag_pct = round(free * 100.0 / system, 1) if system else None
        return {
            "system_bytes": system,
            "free_bytes": free,
            "frag_pct": frag_pct,
        }
    except Exception:
        return None


def _should_log_trim(
    *, force: bool, log_every_n: int, call_count: int, before: dict[str, int | None],
    after: dict[str, int | None], info_log_min_delta_mb: float,
) -> bool:
    # trim_memory calls this only after malloc_trim reported success. A forced
    # successful trim is an explicit observability event, regardless of RSS.
    if force:
        return True
    if not force and call_count % log_every_n:
        return False
    before_rss = before.get("rss_kib")
    after_rss = after.get("rss_kib")
    if before_rss is None or after_rss is None:
        return True
    return abs(after_rss - before_rss) >= info_log_min_delta_mb * 1024


def _probe_glibc_malloc_trim() -> Callable[[int], int] | None:
    """Resolve glibc's malloc_trim once; return None on unsupported systems."""
    global _malloc_trim, _probe_done
    if _probe_done:
        return _malloc_trim
    _probe_done = True
    if sys.platform != "linux":
        return None
    try:
        if platform.libc_ver()[0].lower() != "glibc":
            return None
        libc = ctypes.CDLL(None)
        trim = libc.malloc_trim
        trim.argtypes = [ctypes.c_size_t]
        trim.restype = ctypes.c_int
        _malloc_trim = trim
    except Exception as exc:
        logger.debug("malloc_trim unavailable: %s", exc)
    return _malloc_trim


def trim_memory(
    *,
    force: bool = False,
    reason: str = "",
    cooldown_seconds: float | None = None,
) -> bool:
    """Collect cycles and ask glibc to release free heap pages.

    Returns ``True`` only when ``malloc_trim(0)`` ran and reported success.
    Unsupported allocators, the config kill switch, cooldown suppression,
    the RSS low-water threshold (``threshold_mb``), and all runtime errors
    return ``False`` without affecting the caller.
    """
    (
        enabled,
        configured_cooldown,
        log_every_n,
        info_log_min_delta_mb,
        threshold_mb,
    ) = _config_settings()
    if not enabled:
        return False

    global _last_trim_monotonic, _trim_call_count
    with _trim_lock:
        trim = _probe_glibc_malloc_trim()
        if trim is None:
            return False
        now = time.monotonic()
        cooldown = (
            configured_cooldown
            if cooldown_seconds is None
            else _cooldown_seconds(cooldown_seconds)
        )
        if not force and _last_trim_monotonic and now - _last_trim_monotonic < cooldown:
            return False
        # Even forced trims honor a short floor: AIAgent.close() forces a trim,
        # and delegate batches close N child subagents back-to-back in the SAME
        # process — without a floor that stacks N+1 uncooled full gc.collect()
        # passes (50-500ms each in a large gateway process). 5s coalesces the
        # burst while keeping the parent's final close-trim effective.
        _FORCE_FLOOR_SECONDS = 5.0
        if (
            force
            and _last_trim_monotonic
            and now - _last_trim_monotonic < _FORCE_FLOOR_SECONDS
        ):
            return False
        # Record the attempt before calling into libc so repeated failures do not
        # turn every turn boundary into an expensive full collection.
        _last_trim_monotonic = now
        try:
            before = collect_memory_snapshot()
            # RSS low-water gate (T-instrumentation): skip when the process is
            # small — a sub-300MB heap trims in ~0.3s and releases 1-17MB, i.e.
            # pure busywork on every 60s housekeeping tick.
            if (
                not force
                and threshold_mb is not None
                and (before.get("rss_kib") or 0) < threshold_mb * 1024
            ):
                return False
            started = time.perf_counter()
            gc.collect()
            gc_ms = (time.perf_counter() - started) * 1000
            t0 = time.perf_counter()
            trim_result = trim(0)
            trim_ms = (time.perf_counter() - t0) * 1000
            duration_ms = gc_ms + trim_ms
            released = bool(trim_result)
            after = collect_memory_snapshot()
            heap = _malloc_info_stats()
            _trim_call_count += 1
            if released and _should_log_trim(
                force=force,
                log_every_n=log_every_n,
                call_count=_trim_call_count,
                before=before,
                after=after,
                info_log_min_delta_mb=info_log_min_delta_mb,
            ):
                logger.info(
                    "memory trim: reason=%s malloc_trim=%s rss_kib=%s->%s "
                    "rss_anon_kib=%s->%s swap_kib=%s->%s threads=%s "
                    "duration_ms=%.1f gc_ms=%.1f trim_ms=%.1f "
                    "heap_system_mb=%s heap_free_mb=%s frag_pct=%s",
                    reason or "cleanup",
                    trim_result,
                    before.get("rss_kib"),
                    after.get("rss_kib"),
                    before.get("rss_anon_kib"),
                    after.get("rss_anon_kib"),
                    before.get("vm_swap_kib"),
                    after.get("vm_swap_kib"),
                    after.get("thread_count"),
                    duration_ms,
                    gc_ms,
                    trim_ms,
                    (heap["system_bytes"] / (1024 * 1024)) if heap else None,
                    (heap["free_bytes"] / (1024 * 1024)) if heap else None,
                    heap["frag_pct"] if heap else None,
                )
            return released
        except Exception as exc:
            logger.warning(
                "memory trim failed after %s: %s: %s",
                reason or "cleanup",
                type(exc).__name__,
                exc,
            )
            return False
