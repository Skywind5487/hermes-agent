"""Rate-limited heap release for long-lived Hermes gateway processes.

On Linux/glibc, ``malloc_trim(0)`` can return pages from freed Python/C
allocations to the OS. Other platforms and allocators are safe no-ops.
Behavior is configured under ``context.memory_trim`` in ``config.yaml``.

The allocator trim and Python cyclic GC intentionally have independent cadence:
``malloc_trim(0)`` is cheap and may run at the normal trim cadence, while
``gc.collect()`` is separately rate-limited because a full collection can be
orders of magnitude more expensive in a large or swapped process.
"""

from __future__ import annotations

import ctypes
import gc
import logging
import os
import platform
import sys
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_COOLDOWN_SECONDS = 60.0
_DEFAULT_GC_COOLDOWN_SECONDS = 300.0
_DEFAULT_LOG_EVERY_N = 1
_DEFAULT_INFO_LOG_MIN_DELTA_MB = 0.0
_DEFAULT_THRESHOLD_MB = None
_trim_lock = threading.Lock()
_last_trim_monotonic = 0.0
_last_gc_monotonic = 0.0
_probe_done = False
_malloc_trim: Callable[[int], int] | None = None
_trim_call_count = 0


def _config_settings() -> tuple[bool, float, float, int, float, float | None]:
    """Return fail-open settings from the normal Hermes config path."""
    enabled = True
    cooldown: Any = _DEFAULT_COOLDOWN_SECONDS
    gc_cooldown: Any = _DEFAULT_GC_COOLDOWN_SECONDS
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
            gc_cooldown = settings.get(
                "gc_cooldown_seconds", _DEFAULT_GC_COOLDOWN_SECONDS
            )
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
        _cooldown_seconds(gc_cooldown, default=_DEFAULT_GC_COOLDOWN_SECONDS),
        _log_every_n(log_every_n),
        _nonnegative_float(info_log_min_delta_mb, _DEFAULT_INFO_LOG_MIN_DELTA_MB),
        _threshold_mb(threshold_mb),
    )


def _cooldown_seconds(
    value: Any, *, default: float = _DEFAULT_COOLDOWN_SECONDS
) -> float:
    if isinstance(value, bool):
        return default
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return default


def _threshold_mb(value: Any) -> float | None:
    """Coerce the RSS low-water mark; invalid/non-positive values disable it."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        threshold = float(value)
    except (TypeError, ValueError):
        return None
    return threshold if threshold > 0 else None


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

    ``VmRSS``, ``RssAnon``, and ``VmSwap`` are Linux-only best-effort fields.
    The helper is intentionally dependency-free so allocation recovery never
    requires psutil.
    """
    snapshot: dict[str, int | None] = {
        "rss_kib": None,
        "rss_anon_kib": None,
        "thread_count": threading.active_count(),
    }
    status = _read_proc_status()
    if status:
        field_names = {
            "VmRSS": "rss_kib",
            "RssAnon": "rss_anon_kib",
            "VmSwap": "vm_swap_kib",
        }
        for line in status.splitlines():
            key, separator, raw_value = line.partition(":")
            if not separator or key not in field_names:
                continue
            value = raw_value.strip().split(maxsplit=1)
            if value and value[0].isdigit():
                snapshot[field_names[key]] = int(value[0])
    if isinstance(history_bytes, int) and history_bytes >= 0:
        snapshot["history_bytes"] = history_bytes
    return snapshot


def _parse_malloc_info(xml_text: str) -> dict[str, float | int | None] | None:
    """Parse glibc malloc_info(3) top-level totals into fragmentation evidence.

    ``malloc_info`` repeats per-heap totals inside ``<heap>`` elements and then
    emits process-wide totals at the root. Only root-level values are summed so
    multi-arena processes are not double-counted.
    """
    try:
        root = ET.fromstring(xml_text)
        system_bytes = sum(
            int(node.attrib["size"])
            for node in root.findall("system")
            if node.attrib.get("type") == "current" and "size" in node.attrib
        )
        free_bytes = sum(
            int(node.attrib["size"])
            for node in root.findall("total")
            if node.attrib.get("type") in {"fast", "rest"}
            and "size" in node.attrib
        )
    except (ET.ParseError, KeyError, TypeError, ValueError):
        return None

    frag_pct = (
        round(free_bytes * 100.0 / system_bytes, 1) if system_bytes > 0 else None
    )
    return {
        "system_bytes": system_bytes,
        "free_bytes": free_bytes,
        "frag_pct": frag_pct,
    }


def _malloc_info_stats() -> dict[str, float | int | None] | None:
    """Return best-effort glibc heap fragmentation diagnostics.

    Use libc ``tmpfile()`` instead of a predictable path under /tmp. The stream
    is process-local/temporary and always closed. Any platform, libc, stdio,
    parser, or filesystem failure degrades to ``None`` and cannot veto recovery.
    """
    if sys.platform != "linux":
        return None
    try:
        libc = ctypes.CDLL(None)

        libc.malloc_info.argtypes = [ctypes.c_int, ctypes.c_void_p]
        libc.malloc_info.restype = ctypes.c_int
        libc.tmpfile.argtypes = []
        libc.tmpfile.restype = ctypes.c_void_p
        libc.fflush.argtypes = [ctypes.c_void_p]
        libc.fflush.restype = ctypes.c_int
        libc.fileno.argtypes = [ctypes.c_void_p]
        libc.fileno.restype = ctypes.c_int
        libc.fclose.argtypes = [ctypes.c_void_p]
        libc.fclose.restype = ctypes.c_int

        stream = libc.tmpfile()
        if not stream:
            return None
        try:
            if libc.malloc_info(0, stream) != 0:
                return None
            if libc.fflush(stream) != 0:
                return None
            fd = libc.fileno(stream)
            if fd < 0:
                return None
            os.lseek(fd, 0, os.SEEK_SET)
            chunks: list[bytes] = []
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
            return _parse_malloc_info(
                b"".join(chunks).decode("utf-8", errors="replace")
            )
        finally:
            libc.fclose(stream)
    except Exception:
        return None


def _should_log_trim(
    *,
    force: bool,
    log_every_n: int,
    call_count: int,
    before: dict[str, int | None],
    after: dict[str, int | None],
    info_log_min_delta_mb: float,
) -> bool:
    # trim_memory calls this only after malloc_trim reported success. A forced
    # successful trim is an explicit observability event, regardless of RSS.
    if force:
        return True
    if call_count % log_every_n:
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
    """Collect cycles when eligible and ask glibc to release free heap pages.

    Returns ``True`` only when ``malloc_trim(0)`` ran and reported success.
    Unsupported allocators, the config kill switch, cooldown suppression, the
    configured RSS low-water mark, and runtime errors return ``False`` without
    affecting the caller.
    """
    (
        enabled,
        configured_cooldown,
        gc_cooldown,
        log_every_n,
        info_log_min_delta_mb,
        threshold_mb,
    ) = _config_settings()
    if not enabled:
        return False

    global _last_trim_monotonic, _last_gc_monotonic, _trim_call_count
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
        if (
            not force
            and _last_trim_monotonic
            and now - _last_trim_monotonic < cooldown
        ):
            return False

        # Even forced trims honor a short floor: AIAgent.close() forces a trim,
        # and delegate batches close N child subagents back-to-back in the SAME
        # process. The floor coalesces that burst while keeping a later parent
        # close effective.
        _FORCE_FLOOR_SECONDS = 5.0
        if (
            force
            and _last_trim_monotonic
            and now - _last_trim_monotonic < _FORCE_FLOOR_SECONDS
        ):
            return False

        try:
            before = collect_memory_snapshot()

            # A missing RSS sample cannot prove that the process is below the
            # low-water mark. Fail open so platform/telemetry unavailability
            # never disables memory recovery.
            current_rss_kib = before.get("rss_kib")
            if (
                not force
                and threshold_mb is not None
                and current_rss_kib is not None
                and current_rss_kib < threshold_mb * 1024
            ):
                # This was only a cheap eligibility check, not a trim attempt.
                # Do not consume the normal cooldown or the forced-close floor.
                return False

            # From here on an actual recovery attempt is eligible. Record it
            # before expensive work so malloc_trim failures remain rate-limited.
            _last_trim_monotonic = now

            should_gc = (
                force
                or not _last_gc_monotonic
                or now - _last_gc_monotonic >= gc_cooldown
            )

            # Diagnostics extend policy: even an unexpected diagnostics failure
            # must not suppress gc/trim. Collect fragmentation before recovery so
            # it describes the heap state that motivated this attempt.
            try:
                heap = _malloc_info_stats()
            except Exception as exc:
                logger.debug("malloc_info diagnostics failed: %s", exc)
                heap = None

            gc_ms = 0.0
            if should_gc:
                gc_started = time.perf_counter()
                gc.collect()
                gc_ms = (time.perf_counter() - gc_started) * 1000
                _last_gc_monotonic = time.monotonic()

            trim_started = time.perf_counter()
            trim_result = trim(0)
            trim_ms = (time.perf_counter() - trim_started) * 1000
            released = bool(trim_result)
            after = collect_memory_snapshot()
            duration_ms = gc_ms + trim_ms
            _trim_call_count += 1
            if released and _should_log_trim(
                force=force,
                log_every_n=log_every_n,
                call_count=_trim_call_count,
                before=before,
                after=after,
                info_log_min_delta_mb=info_log_min_delta_mb,
            ):
                heap_system_mb = (
                    heap["system_bytes"] / (1024 * 1024) if heap else None
                )
                heap_free_mb = heap["free_bytes"] / (1024 * 1024) if heap else None
                logger.info(
                    "memory trim: reason=%s malloc_trim=%s rss_kib=%s->%s "
                    "rss_anon_kib=%s->%s swap_kib=%s->%s threads=%s "
                    "duration_ms=%.1f gc_ran=%s gc_ms=%.1f trim_ms=%.1f "
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
                    should_gc,
                    gc_ms,
                    trim_ms,
                    heap_system_mb,
                    heap_free_mb,
                    heap.get("frag_pct") if heap else None,
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
