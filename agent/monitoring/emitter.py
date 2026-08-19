"""Monitoring emitter: fire-and-forget queue + background dispatcher.

The emitter is the single seam between producers (gateway status hooks, the
diagnostic log handler) and consumers (the OTLP streamers). Its contract is
the hot-path invariant:

    ``emit()`` MUST return in O(microseconds), MUST NOT block on disk/network,
    and MUST NEVER raise into the caller. A monitoring failure is logged
    locally and dropped — it can never affect the gateway or a session.

Mechanism:
  * ``emit(event)`` does a non-blocking ``queue.put_nowait`` wrapped in a bare
    except. On a full queue it drops the *oldest* event and counts the drop.
  * A daemon thread drains the queue and fans each batch out to subscribers
    (the OTLP metric/span/log streamers). Each subscriber is fail-isolated —
    a slow or raising subscriber never affects the hot path or its peers.
  * A process-lifetime producer whose event predates its exporter may use
    ``emit_buffered(event)``. That one event enters the same bounded queue even
    before the first subscriber exists; it does not enable unrelated producers
    or create a second queue/store.

Nothing is persisted here. Monitoring is an egress path, not a local store.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_MAX_QUEUE = 10_000  # ring-buffer depth; oldest dropped when full
_DRAIN_BATCH = 256


class MonitoringEmitter:
    """Owns the queue, the dispatcher thread, and the subscriber list."""

    def __init__(self, *, enabled: bool = True) -> None:
        self._enabled = enabled
        self._q: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=_MAX_QUEUE)
        self._dropped = 0
        self._dispatched = 0
        self._stop = threading.Event()
        self._started = False
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        # Live subscribers (the OTLP streamers). Called from the dispatcher
        # thread, fully fail-isolated. Each subscriber is callable(batch: list[dict]).
        self._subscribers: list = []

    # ── public API (hot path) ───────────────────────────────────────────────
    def _enqueue(self, event: Any) -> None:
        """Put one event into the bounded queue. Never blocks, never raises."""
        try:
            payload = event.to_dict() if hasattr(event, "to_dict") else dict(event)
            payload.setdefault("ts_ns", time.time_ns())
            try:
                self._q.put_nowait(payload)
            except queue.Full:
                # Drop oldest to make room — bounded memory, newest-wins.
                try:
                    self._q.get_nowait()
                    self._q.task_done()
                    self._dropped += 1
                    self._q.put_nowait(payload)
                except Exception:
                    self._dropped += 1
            if self._subscribers:
                self._ensure_started()
        except Exception:
            logger.debug("monitoring emit failed", exc_info=True)

    def emit(self, event: Any) -> None:
        """Enqueue an event when the monitoring plane is enabled.

        ``event`` may be a dataclass with ``to_dict()`` or a plain dict.
        Never blocks and never raises.
        """
        if not self._enabled:
            return
        self._enqueue(event)

    def emit_buffered(self, event: Any) -> None:
        """Enqueue one opt-in event even before a subscriber is attached.

        This deliberately does *not* set ``_enabled``. It exists for an
        already-opted-in producer whose observation lifetime begins before its
        exporter/subscriber is constructed. Unrelated producers still see the
        normal disabled ``emit()`` behavior. If no subscriber ever attaches,
        only explicitly buffered events occupy the existing bounded queue.
        """
        self._enqueue(event)

    # ── lifecycle ───────────────────────────────────────────────────────────
    def _ensure_started(self) -> None:
        if self._started:
            return
        with self._lock:
            if self._started:
                return
            self._thread = threading.Thread(
                target=self._run, name="hermes-monitoring-dispatch", daemon=True
            )
            self._thread.start()
            self._started = True

    def _run(self) -> None:
        while not self._stop.is_set():
            # A subscriber may detach after the dispatcher has started. Leave
            # explicitly buffered events queued until another subscriber
            # attaches rather than consuming them into an empty fan-out set.
            if not self._subscribers:
                self._stop.wait(0.1)
                continue
            try:
                first = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            batch = [first]
            while len(batch) < _DRAIN_BATCH:
                try:
                    batch.append(self._q.get_nowait())
                except queue.Empty:
                    break
            try:
                self._dispatch(batch)
            finally:
                for _ in batch:
                    self._q.task_done()

    def _dispatch(self, batch) -> None:
        # Fan-out to subscribers (OTLP streamers) — fully fail-isolated.
        for sub in list(self._subscribers):
            try:
                sub(batch)
            except Exception:
                logger.debug("monitoring subscriber failed", exc_info=True)
        self._dispatched += len(batch)

    def subscribe(self, callback) -> None:
        """Register a live batch subscriber (callable(batch: list[dict]))."""
        if callback not in self._subscribers:
            self._subscribers.append(callback)
        self._enabled = True
        # Never start dispatch from subscribe(). A residual event buffered
        # before any subscriber may sit in the queue while the gateway attaches
        # subscribers one at a time (span streamer first, diagnostic streamer
        # second); an early dispatcher would dequeue it into a partial fan-out
        # and lose it. The first ordinary emit() after the full subscriber set
        # attaches starts dispatch and drains buffered events through the
        # complete fan-out.

    def unsubscribe(self, callback) -> None:
        try:
            self._subscribers.remove(callback)
        except ValueError:
            pass
        if not self._subscribers:
            self._enabled = False

    # ── introspection / shutdown (tests, CLI) ───────────────────────────────
    def flush(self, timeout: float = 2.0) -> None:
        """Wait boundedly for queued and in-flight batches to finish dispatch.

        With no subscriber there is no sink that can make queued explicitly
        buffered events complete, so flushing is a no-op rather than a timeout
        delay. Those events remain bounded in memory for a later subscriber.

        The same holds before the dispatcher has started: a subscriber may be
        attached while nothing can drain the queue (the startup snapshot was
        never emitted), so waiting would only add an artificial shutdown delay.
        Fail-open: no-op unless dispatch is actually running.
        """
        if timeout <= 0 or not self._subscribers or not self._started:
            return

        finished = threading.Event()

        def _wait_for_completion() -> None:
            self._q.join()
            finished.set()

        waiter = threading.Thread(
            target=_wait_for_completion,
            name="hermes-monitoring-flush",
            daemon=True,
        )
        waiter.start()
        finished.wait(timeout=timeout)

    def stats(self) -> Dict[str, int]:
        return {
            "queued": self._q.qsize(),
            "dispatched": self._dispatched,
            "dropped": self._dropped,
            "subscribers": len(self._subscribers),
        }

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._started = False


# ── process-wide singleton ──────────────────────────────────────────────────
_EMITTER: Optional[MonitoringEmitter] = None
_EMITTER_LOCK = threading.Lock()


def get_emitter() -> MonitoringEmitter:
    """Return the process-wide monitoring emitter."""
    global _EMITTER
    if _EMITTER is not None:
        return _EMITTER
    with _EMITTER_LOCK:
        if _EMITTER is None:
            # Collection is opt-in. A plane exporter enables the singleton by
            # attaching its first subscriber; until then ordinary producers
            # are no-ops. Explicit pre-subscriber events use emit_buffered().
            _EMITTER = MonitoringEmitter(enabled=False)
    return _EMITTER


def emit(event: Any) -> None:
    """Module-level convenience: emit via the singleton."""
    get_emitter().emit(event)


def reset_emitter_for_tests(emitter: Optional[MonitoringEmitter] = None) -> None:
    """Swap the singleton (tests only)."""
    global _EMITTER
    with _EMITTER_LOCK:
        if _EMITTER is not None and emitter is not _EMITTER:
            try:
                _EMITTER.close()
            except Exception:
                pass
        _EMITTER = emitter


# Back-compat alias for the salvaged class name used in emozilla's tests.
TelemetryEmitter = MonitoringEmitter

__all__ = [
    "MonitoringEmitter",
    "TelemetryEmitter",
    "get_emitter",
    "emit",
    "reset_emitter_for_tests",
]
