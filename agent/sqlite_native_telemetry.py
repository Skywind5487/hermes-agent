"""Optional, payload-free SQLite query telemetry.

The preferred backend is the Hermes owner-layer API exposed by a patched CPython
``_sqlite3`` module.  It reads exact counters while the owner still has the
``sqlite3_stmt *`` and ``sqlite3 *`` handles.  Standard Python runtimes keep the
existing public ``set_progress_handler`` backend as an explicitly degraded
fallback; this module never inspects CPython private object layout with ctypes.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from typing import Any, Callable, Dict, List, Optional

EventSink = Callable[[Dict[str, Any]], None]
_PROGRESS_INTERVAL = 1000
_STMT_STATUS_KEYS = (
    "vm_steps",
    "fullscan_steps",
    "sort_operations",
    "autoindex_operations",
    "reprepare_count",
)
_DB_STATUS_KEYS = (
    "cache_hit",
    "cache_miss",
    "cache_write",
    "cache_spill",
    "cache_used_bytes",
    "schema_used_bytes",
    "stmt_used_bytes",
)


class SQLiteNativeProbe:
    """Safe, payload-free query timing and SQLite counter probe.

    A patched CPython owner layer may expose two private diagnostic methods:
    ``Connection._hermes_db_status()`` and
    ``Cursor._hermes_stmt_status()``.  When both the connection-side method is
    available, this class uses that backend and reports exact statement
    counters.  Normal CPython builds use the public progress-handler fallback,
    whose VM-step value is explicitly marked as an estimate.
    """

    def __init__(self, connection: Any, event_sink: EventSink):
        if not hasattr(connection, "set_progress_handler"):
            raise RuntimeError("sqlite connection has no progress handler")
        self.connection = connection
        self._emit = event_sink
        self._lock = threading.Lock()
        self._active: List[Dict[str, Any]] = []
        self._closed = False
        self.available = True
        self.error_type = None
        self.backend = (
            "cpython_owner"
            if callable(getattr(connection, "_hermes_db_status", None))
            else "progress_handler"
        )
        self.native_api_available = self.backend == "cpython_owner"
        if not self.native_api_available:
            self._safe_emit(
                {
                    "event": "DB_NATIVE_FALLBACK",
                    "status": "degraded",
                    "native_api_available": False,
                    "native_backend": self.backend,
                    "python_sqlite_version": sqlite3.sqlite_version,
                    "system_sqlite_version": None,
                    "reason": "owner_layer_api_unavailable",
                }
            )

    @classmethod
    def try_attach(cls, connection: Any, event_sink: EventSink) -> "SQLiteNativeProbe":
        try:
            return cls(connection, event_sink)
        except Exception as exc:  # diagnostics must be fail-open
            probe = object.__new__(cls)
            probe.connection = connection
            probe._emit = event_sink
            probe._lock = threading.Lock()
            probe._active = []
            probe._closed = True
            probe.available = False
            probe.native_api_available = False
            probe.backend = "unavailable"
            probe.error_type = type(exc).__name__
            probe._safe_emit(
                {
                    "event": "DB_NATIVE_ERROR",
                    "status": "error",
                    "native_api_available": False,
                    "native_backend": "unavailable",
                    "error_type": type(exc).__name__,
                }
            )
            return probe

    def _safe_emit(self, fields: Dict[str, Any]) -> None:
        try:
            self._emit(fields)
        except Exception:
            pass

    def _read_db_status(self) -> Optional[Dict[str, Optional[int]]]:
        if self.backend != "cpython_owner":
            return None
        try:
            raw = self.connection._hermes_db_status()
            if not isinstance(raw, dict):
                raise TypeError("owner DB status is not a dict")
            return {
                key: int(raw[key]) if isinstance(raw.get(key), int) else None
                for key in _DB_STATUS_KEYS
            }
        except Exception as exc:
            self._safe_emit(
                {
                    "event": "DB_NATIVE_ERROR",
                    "status": "error",
                    "native_backend": self.backend,
                    "native_api_available": True,
                    "error_type": type(exc).__name__,
                }
            )
            return None

    def _read_stmt_status(self, cursor: Any) -> Optional[Dict[str, Optional[int]]]:
        if self.backend != "cpython_owner" or cursor is None:
            return None
        method = getattr(cursor, "_hermes_stmt_status", None)
        if not callable(method):
            return None
        try:
            raw = method()
            if raw is None:
                return None
            if not isinstance(raw, dict):
                raise TypeError("owner statement status is not a dict")
            return {
                key: int(raw[key]) if isinstance(raw.get(key), int) else None
                for key in _STMT_STATUS_KEYS
            }
        except Exception as exc:
            self._safe_emit(
                {
                    "event": "DB_NATIVE_ERROR",
                    "status": "error",
                    "native_backend": self.backend,
                    "native_api_available": True,
                    "error_type": type(exc).__name__,
                }
            )
            return None

    def _progress(self) -> int:
        # This callback must remain scalar-only and non-blocking.  No logger,
        # DB operation, allocation-heavy serialization, or exception escapes.
        with self._lock:
            if self._active:
                self._active[-1]["progress_count"] += 1
        return 0

    def start_query(self, query_id: str, query_fingerprint: str) -> Optional[Dict[str, Any]]:
        if not self.available or self._closed:
            return None
        token = {
            "query_id": str(query_id),
            "query_fingerprint": str(query_fingerprint),
            "thread_id": threading.get_ident(),
            "started_ns": time.perf_counter_ns(),
            "progress_count": 0,
            "db_status_before": self._read_db_status(),
        }
        with self._lock:
            self._active.append(token)
        if self.backend == "cpython_owner":
            return token
        try:
            self.connection.set_progress_handler(self._progress, _PROGRESS_INTERVAL)
        except Exception:
            with self._lock:
                if token in self._active:
                    self._active.remove(token)
            return None
        return token

    def _finish_owner_query(self, token: Dict[str, Any], cursor: Any) -> None:
        stmt_status = self._read_stmt_status(cursor)
        db_before = token.get("db_status_before")
        db_after = self._read_db_status()
        common = {
            "status": "completed",
            "query_id": token["query_id"],
            "query_fingerprint": token["query_fingerprint"],
            "native_backend": self.backend,
            "native_api_available": True,
            "thread_id": token["thread_id"],
        }
        self._safe_emit(
            {
                "event": "DB_NATIVE_PROFILE",
                **common,
                "native_ns": max(0, time.perf_counter_ns() - token["started_ns"]),
                "vm_steps": (stmt_status or {}).get("vm_steps"),
                "vm_steps_exact": True,
                "fullscan_steps": (stmt_status or {}).get("fullscan_steps"),
                "sort_operations": (stmt_status or {}).get("sort_operations"),
                "autoindex_operations": (stmt_status or {}).get("autoindex_operations"),
                "reprepare_count": (stmt_status or {}).get("reprepare_count"),
            }
        )
        status = db_after or {}
        before = db_before or {}
        self._safe_emit(
            {
                "event": "DB_NATIVE_STATUS",
                **common,
                "cache_hit_delta": self._delta(before, db_after, "cache_hit"),
                "cache_miss_delta": self._delta(before, db_after, "cache_miss"),
                "cache_write_delta": self._delta(before, db_after, "cache_write"),
                "cache_spill_delta": self._delta(before, db_after, "cache_spill"),
                "cache_used_bytes": status.get("cache_used_bytes"),
                "schema_used_bytes": status.get("schema_used_bytes"),
                "stmt_used_bytes": status.get("stmt_used_bytes"),
            }
        )

    @staticmethod
    def _delta(
        before: Dict[str, Optional[int]],
        after: Optional[Dict[str, Optional[int]]],
        key: str,
    ) -> Optional[int]:
        if after is None or before.get(key) is None or after.get(key) is None:
            return None
        return int(after[key] - before[key])

    def finish_query(
        self,
        token: Optional[Dict[str, Any]],
        *,
        cursor: Any = None,
    ) -> None:
        if not token or not self.available:
            return
        with self._lock:
            if token in self._active:
                self._active.remove(token)
            has_outer_token = bool(self._active)
        if self.backend == "cpython_owner":
            self._finish_owner_query(token, cursor)
            return
        try:
            if has_outer_token:
                self.connection.set_progress_handler(self._progress, _PROGRESS_INTERVAL)
            else:
                self.connection.set_progress_handler(None, 0)
        except Exception:
            pass
        elapsed_ns = max(0, time.perf_counter_ns() - token["started_ns"])
        progress_steps = token["progress_count"] * _PROGRESS_INTERVAL
        common = {
            "status": "completed",
            "query_id": token["query_id"],
            "query_fingerprint": token["query_fingerprint"],
            "native_backend": self.backend,
            "native_api_available": False,
            "thread_id": token["thread_id"],
        }
        self._safe_emit(
            {
                "event": "DB_NATIVE_PROFILE",
                **common,
                "native_ns": elapsed_ns,
                "vm_steps": progress_steps,
                "vm_steps_exact": False,
                "fullscan_steps": None,
                "sort_operations": None,
                "autoindex_operations": None,
                "reprepare_count": None,
            }
        )
        self._safe_emit(
            {
                "event": "DB_NATIVE_STATUS",
                **common,
                "cache_hit_delta": None,
                "cache_miss_delta": None,
                "cache_write_delta": None,
                "cache_spill_delta": None,
                "cache_used_bytes": None,
                "schema_used_bytes": None,
                "stmt_used_bytes": None,
            }
        )

    def close(self) -> None:
        if self._closed:
            return
        if self.backend != "cpython_owner":
            try:
                self.connection.set_progress_handler(None, 0)
            except Exception:
                pass
        with self._lock:
            self._active.clear()
        self._closed = True
        self.available = False
