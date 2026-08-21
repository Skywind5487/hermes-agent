"""Tests for the monitoring emitter: hot-path invariant + subscriber fan-out."""

from __future__ import annotations

import time
import threading

from agent.monitoring.emitter import MonitoringEmitter
from agent.monitoring.events import GatewayHealthEvent


def test_emit_never_raises_when_disabled():
    em = MonitoringEmitter(enabled=False)
    em.emit({"event": "gateway_health", "name": "gateway.health_snapshot"})
    assert em.stats()["queued"] == 0
    em.close()


def test_process_singleton_stays_dormant_until_subscribed():
    from agent.monitoring import emitter

    emitter.reset_emitter_for_tests()
    try:
        emitter.emit({"event": "gateway_health", "name": "gateway.lifecycle"})
        singleton = emitter.get_emitter()
        assert singleton.stats()["queued"] == 0
        assert singleton._started is False

        subscriber = lambda _batch: None  # noqa: E731
        singleton.subscribe(subscriber)
        emitter.emit({"event": "gateway_health", "name": "gateway.lifecycle"})
        assert singleton._started is True
        singleton.unsubscribe(subscriber)
    finally:
        emitter.reset_emitter_for_tests()


def test_subscribe_never_starts_dispatch_on_buffered_events():
    """subscribe() must not start dispatch just because the queue is non-empty.

    Regression for #114: a residual event buffered before any subscriber must
    survive while gateway subscribers attach one at a time (span streamer
    first, diagnostic streamer second). Starting dispatch from subscribe()
    would dequeue it into a partial fan-out set and lose it.
    """
    em = MonitoringEmitter(enabled=False)
    try:
        em.emit_buffered({"event": "gateway_diagnostic", "name": "early"})
        assert em.stats()["queued"] == 1
        assert em._started is False

        # First subscriber attaches with a non-empty queue — still no dispatch.
        em.subscribe(lambda _batch: None)
        assert em._started is False
        assert em.stats()["queued"] == 1

        # The first ordinary emit after subscribe starts dispatch and drains.
        em.emit({"event": "gateway_health", "name": "snapshot"})
        em.flush(timeout=1.0)
        assert em.stats()["queued"] == 0
    finally:
        em.close()


def test_flush_is_noop_before_dispatcher_starts():
    """flush() must not wait when a subscriber exists but dispatch never ran.

    Regression for #114: a partial startup path (buffered residual → subscriber
    attached → initial snapshot never emitted) leaves the dispatcher unstarted.
    flush() with a subscriber but no dispatcher would block on queue.join()
    until the timeout, adding an artificial shutdown delay — fail-open requires
    an instant no-op instead.
    """
    em = MonitoringEmitter(enabled=False)
    try:
        em.emit_buffered({"event": "gateway_diagnostic", "name": "early"})
        em.subscribe(lambda _batch: None)
        assert em._started is False

        start = time.monotonic()
        em.flush(timeout=2.0)
        elapsed = time.monotonic() - start

        # Buggy behavior blocks the full 2s timeout; a no-op returns instantly.
        assert elapsed < 1.0
        assert em.stats()["queued"] == 1
        assert em._started is False
    finally:
        em.close()


def test_unsubscribe_stops_delivery():
    em = MonitoringEmitter()
    seen: list = []
    cb = lambda batch: seen.extend(batch)  # noqa: E731
    em.subscribe(cb)
    em.emit({"event": "gateway_health", "name": "a"})
    em.flush()
    em.unsubscribe(cb)
    em.emit({"event": "gateway_health", "name": "b"})
    em.flush()
    em.close()
    assert [ev["name"] for ev in seen] == ["a"]




def test_hot_path_is_fast():
    em = MonitoringEmitter()
    start = time.perf_counter()
    for _ in range(1_000):
        em.emit({"event": "gateway_health", "name": "gateway.health_snapshot"})
    elapsed = time.perf_counter() - start
    em.close()
    # 1000 emits should be far under a second even on slow CI.
    assert elapsed < 1.0
