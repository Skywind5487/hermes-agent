#!/usr/bin/env python3
"""Short, reversible tracefs capture for Hermes DB investigations.

Run explicitly as root, for example:
  sudo python3 scripts/hermes_kernel_trace.py --pid 1316031 --duration 30

The capture changes only tracing_on and selected event enable/filter files. It
snapshots those files, detects external drift before restore, and never changes
current_tracer, set_event, or the trace buffer. It does not start a daemon or
modify permanent kernel settings.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import select
import signal
import sys
import time
from pathlib import Path
from typing import Dict, Iterable


DEFAULT_EVENTS = (
    "sched/sched_switch",
    "sched/sched_stat_iowait",
    "block/block_rq_issue",
    "block/block_rq_complete",
    "writeback/writeback_wait",
    "writeback/writeback_written",
    "filemap/mm_filemap_add_to_page_cache",
    "filemap/mm_filemap_delete_from_page_cache",
)
_SAFE = re.compile(r"[^A-Za-z0-9_.:-]+")


def safe_marker(event: str, fields: Dict[str, object]) -> str:
    """Create a bounded, single-line marker containing scalar metadata only."""
    parts = [f"HERMES_{_SAFE.sub('_', str(event))[:80]}"]
    for key, value in fields.items():
        clean_key = _SAFE.sub("_", str(key))[:40]
        clean_value = _SAFE.sub("_", str(value))[:160]
        parts.append(f"{clean_key}={clean_value}")
    return " ".join(parts)[:480]


def write_trace_marker(trace_root: Path, event: str, fields: Dict[str, object]) -> bool:
    try:
        marker = trace_root / "trace_marker"
        marker.write_text(safe_marker(event, fields) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


class TraceCapture:
    """Enable selected tracepoints and restore owned control files safely."""

    def __init__(
        self,
        trace_root: Path = Path("/sys/kernel/tracing"),
        events: Iterable[str] = DEFAULT_EVENTS,
        pid: int | None = None,
        tid: int | None = None,
    ):
        self.trace_root = Path(trace_root)
        self.events = tuple(events)
        self.pid = pid
        self.tid = tid
        self._saved: Dict[Path, str] = {}
        self._owned: Dict[Path, str] = {}
        self.restore_conflicts: list[str] = []
        self._started = False

    def _path(self, relative: str) -> Path:
        return self.trace_root / relative

    def _save(self, path: Path) -> None:
        if path in self._saved or not path.exists():
            return
        self._saved[path] = path.read_text(encoding="utf-8")

    def _write(self, path: Path, value: str) -> None:
        self._save(path)
        path.write_text(value, encoding="utf-8")
        self._owned[path] = value

    def start(self) -> None:
        if self._started:
            return
        required = self._path("tracing_on")
        if not required.exists():
            raise RuntimeError(f"tracefs is unavailable: {required}")
        try:
            self._write(required, "0\n")
            for event in self.events:
                enable = self._path(f"events/{event}/enable")
                if not enable.exists():
                    continue
                self._write(enable, "1\n")
                if self.pid is not None:
                    filter_path = enable.parent / "filter"
                    if filter_path.exists():
                        self._write(
                            filter_path,
                            f"common_pid == {int(self.tid or self.pid)}\n",
                        )
            self._write(required, "1\n")
            self._started = True
        except Exception:
            # Partial enable must be rolled back before the error escapes.
            self.stop()
            raise

    def _restore_one(self, path: Path, value: str) -> None:
        try:
            current = path.read_text(encoding="utf-8")
        except OSError:
            return
        expected = self._owned.get(path)
        if expected is not None and current != expected:
            self.restore_conflicts.append(str(path))
            return
        try:
            path.write_text(value, encoding="utf-8")
        except OSError:
            pass

    def stop(self) -> None:
        if not self._started and not self._saved:
            return
        tracing_on = self._path("tracing_on")
        if tracing_on in self._saved:
            # Stop tracing first, but do not overwrite an external update.
            self._restore_one(tracing_on, self._saved[tracing_on])
        for path, value in reversed(tuple(self._saved.items())):
            if path == tracing_on:
                continue
            self._restore_one(path, value)
        self._started = False
        self._saved.clear()
        self._owned.clear()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop()
        return False


def _capture(args) -> int:
    trace_root = Path(args.trace_root)
    events = tuple(args.events.split(",")) if args.events else DEFAULT_EVENTS
    capture = TraceCapture(trace_root, events, args.pid, args.tid)
    output = Path(args.output)
    metadata_path = Path(args.metadata)
    output.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    started_ns = time.monotonic_ns()
    boot_path = Path("/proc/sys/kernel/random/boot_id")
    metadata = {
        "event": "KERNEL_TRACE_SESSION",
        "status": "started",
        "pid": args.pid,
        "tid": args.tid,
        "trace_root": str(trace_root),
        "events": list(events),
        "started_monotonic_ns": started_ns,
        "boot_id": boot_path.read_text().strip() if boot_path.exists() else None,
    }
    stop_requested = False
    interrupted = False
    previous_handlers = {}

    def request_stop(signum, _frame):
        nonlocal stop_requested
        stop_requested = True
        metadata["stop_signal"] = int(signum)

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, request_stop)
        capture.start()
        with output.open("w", encoding="utf-8") as handle:
            pipe = trace_root / "trace_pipe"
            deadline = time.monotonic() + max(0.0, args.duration)
            with pipe.open("r", encoding="utf-8") as pipe_handle:
                while not stop_requested and time.monotonic() < deadline:
                    remaining = max(0.0, deadline - time.monotonic())
                    ready, _, _ = select.select([pipe_handle], [], [], min(0.25, remaining))
                    if not ready:
                        continue
                    line = pipe_handle.readline()
                    if line:
                        handle.write(line)
                        handle.flush()
    except KeyboardInterrupt:
        interrupted = True
    finally:
        capture.stop()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)

    metadata.update(
        {
            "status": "interrupted" if interrupted or stop_requested else "completed",
            "restore_conflicts": capture.restore_conflicts,
            "ended_monotonic_ns": time.monotonic_ns(),
        }
    )
    metadata["duration_ms"] = int(
        (metadata["ended_monotonic_ns"] - started_ns) / 1_000_000
    )
    metadata_path.write_text(json.dumps(metadata, sort_keys=True) + "\n", encoding="utf-8")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace-root", default="/sys/kernel/tracing")
    parser.add_argument("--pid", type=int)
    parser.add_argument("--tid", type=int)
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--output", default="/tmp/hermes-kernel.trace")
    parser.add_argument("--metadata", default="/tmp/hermes-kernel.jsonl")
    parser.add_argument("--events", default=",".join(DEFAULT_EVENTS))
    args = parser.parse_args(argv)
    try:
        return _capture(args)
    except (OSError, RuntimeError) as exc:
        print(f"hermes_kernel_trace: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
