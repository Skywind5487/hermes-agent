"""Diagnostics extension tests for memory-runtime reconstruction (#110)."""

from unittest.mock import Mock

import pytest

import hermes_cli.mem_trim as mem_trim


@pytest.fixture(autouse=True)
def _reset_trim_state(monkeypatch):
    monkeypatch.setattr(mem_trim, "_last_trim_monotonic", 0.0)
    monkeypatch.setattr(mem_trim, "_last_gc_monotonic", 0.0)
    monkeypatch.setattr(mem_trim, "_probe_done", True)
    monkeypatch.setattr(mem_trim, "_malloc_trim", None)
    monkeypatch.setattr(mem_trim, "_trim_call_count", 0)


def _snapshot(rss_kib=4096, rss_anon_kib=3072, vm_swap_kib=None):
    snapshot = {
        "rss_kib": rss_kib,
        "rss_anon_kib": rss_anon_kib,
        "thread_count": 3,
    }
    if vm_swap_kib is not None:
        snapshot["vm_swap_kib"] = vm_swap_kib
    return snapshot


def test_memory_snapshot_parses_vmswap_when_available(monkeypatch):
    monkeypatch.setattr(
        mem_trim,
        "_read_proc_status",
        lambda: (
            "Name:\tpython\nVmRSS:\t1234 kB\n"
            "RssAnon:\t567 kB\nVmSwap:\t89 kB\n"
        ),
    )
    monkeypatch.setattr(mem_trim.threading, "active_count", lambda: 9)

    assert mem_trim.collect_memory_snapshot() == {
        "rss_kib": 1234,
        "rss_anon_kib": 567,
        "vm_swap_kib": 89,
        "thread_count": 9,
    }


def test_malloc_info_parser_uses_process_totals_without_double_counting():
    xml = """<malloc version="1">
      <heap nr="0">
        <total type="fast" count="1" size="50"/>
        <total type="rest" count="1" size="350"/>
        <system type="current" size="1000"/>
      </heap>
      <total type="fast" count="2" size="100"/>
      <total type="rest" count="3" size="400"/>
      <system type="current" size="2000"/>
    </malloc>"""

    assert mem_trim._parse_malloc_info(xml) == {
        "system_bytes": 2000,
        "free_bytes": 500,
        "frag_pct": 25.0,
    }


def test_malformed_malloc_info_is_best_effort():
    assert mem_trim._parse_malloc_info("<malloc>") is None


def test_diagnostics_failure_does_not_block_recovery(monkeypatch):
    collect = Mock()
    trim = Mock(return_value=1)
    monkeypatch.setattr(mem_trim.gc, "collect", collect)
    monkeypatch.setattr(mem_trim, "_malloc_trim", trim)
    monkeypatch.setattr(
        mem_trim,
        "_malloc_info_stats",
        Mock(side_effect=RuntimeError("diagnostics unavailable")),
    )
    monkeypatch.setattr(mem_trim.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(mem_trim, "collect_memory_snapshot", lambda: _snapshot())

    assert mem_trim.trim_memory(reason="diagnostics-fail") is True
    collect.assert_called_once_with()
    trim.assert_called_once_with(0)


def test_log_attributes_gc_trim_fragmentation_and_swap(monkeypatch, caplog):
    monkeypatch.setattr(mem_trim.gc, "collect", lambda: None)
    monkeypatch.setattr(mem_trim, "_malloc_trim", lambda _pad: 1)
    monkeypatch.setattr(
        mem_trim,
        "_malloc_info_stats",
        lambda: {
            "system_bytes": 4 * 1024 * 1024,
            "free_bytes": 1 * 1024 * 1024,
            "frag_pct": 25.0,
        },
    )
    monkeypatch.setattr(
        mem_trim, "_config_settings", lambda: (True, 0.0, 300.0, 1, 0.0, None)
    )
    monkeypatch.setattr(mem_trim.time, "monotonic", lambda: 100.0)
    perf_ticks = iter([10.0, 10.003, 20.0, 20.007])
    monkeypatch.setattr(mem_trim.time, "perf_counter", lambda: next(perf_ticks))
    snapshots = iter(
        (
            _snapshot(4096, 3072, 128),
            _snapshot(2048, 1024, 64),
        )
    )
    monkeypatch.setattr(mem_trim, "collect_memory_snapshot", lambda: next(snapshots))

    with caplog.at_level("INFO", logger="hermes_cli.mem_trim"):
        assert mem_trim.trim_memory(reason="diagnostic-test") is True

    assert "gc_ran=True" in caplog.text
    assert "gc_ms=3.0" in caplog.text
    assert "trim_ms=7.0" in caplog.text
    assert "swap_kib=128->64" in caplog.text
    assert "frag_pct=25.0" in caplog.text


def test_log_marks_gc_cooldown_without_suppressing_trim(monkeypatch, caplog):
    collect = Mock()
    monkeypatch.setattr(mem_trim.gc, "collect", collect)
    monkeypatch.setattr(mem_trim, "_malloc_trim", lambda _pad: 1)
    monkeypatch.setattr(mem_trim, "_malloc_info_stats", lambda: None)
    monkeypatch.setattr(mem_trim, "_last_gc_monotonic", 90.0)
    monkeypatch.setattr(
        mem_trim, "_config_settings", lambda: (True, 0.0, 300.0, 1, 0.0, None)
    )
    monkeypatch.setattr(mem_trim.time, "monotonic", lambda: 100.0)
    perf_ticks = iter([20.0, 20.004])
    monkeypatch.setattr(mem_trim.time, "perf_counter", lambda: next(perf_ticks))
    monkeypatch.setattr(mem_trim, "collect_memory_snapshot", lambda: _snapshot())

    with caplog.at_level("INFO", logger="hermes_cli.mem_trim"):
        assert mem_trim.trim_memory(reason="gc-cooling") is True

    collect.assert_not_called()
    assert "gc_ran=False" in caplog.text
    assert "gc_ms=0.0" in caplog.text
    assert "trim_ms=4.0" in caplog.text
