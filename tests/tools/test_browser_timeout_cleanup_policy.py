"""Acceptance tests for issue #115 browser timeout cleanup policy."""

import subprocess
import sys
import types
from unittest.mock import MagicMock

import pytest

import tools.browser_tool as bt


@pytest.fixture(autouse=True)
def clean_browser_state():
    bt._active_sessions.clear()
    bt._recording_sessions.clear()
    bt._session_last_activity.clear()
    bt._last_active_session_key.clear()
    bt._cached_terminate_daemon = None
    bt._terminate_daemon_resolved = False
    yield
    bt._active_sessions.clear()
    bt._recording_sessions.clear()
    bt._session_last_activity.clear()
    bt._last_active_session_key.clear()
    bt._cached_terminate_daemon = None
    bt._terminate_daemon_resolved = False


def _session(task_id: str, session_name: str) -> None:
    bt._active_sessions[task_id] = {
        "session_name": session_name,
        "cdp_url": None,
        "session_key": task_id,
        "owner_task_id": bt._bare_task_id_for_session_key(task_id),
    }
    bt._recording_sessions.add(task_id)
    bt._session_last_activity[task_id] = 1.0


def _pid_file(tmp_path, session_name: str, pid: int) -> None:
    socket_dir = tmp_path / f"agent-browser-{session_name}"
    socket_dir.mkdir(parents=True, exist_ok=True)
    (socket_dir / f"{session_name}.pid").write_text(str(pid), encoding="utf-8")


def _fake_psutil_module(monkeypatch, **attrs):
    """Inject a stub ``psutil`` module so lazy ``import psutil`` resolves.

    ``tools.process_registry`` imports ``psutil`` lazily inside
    ``_terminate_host_pid``.  The hermetic test venv may not ship psutil, so
    we stub the module in ``sys.modules`` rather than monkeypatching an
    attribute on a module that may not be importable.
    """
    fake = types.ModuleType("psutil")
    for name, value in attrs.items():
        setattr(fake, name, value)
    monkeypatch.setitem(sys.modules, "psutil", fake)
    return fake


def test_timeout_policy_defaults_false_and_parses_false_string(monkeypatch):
    monkeypatch.setattr(
        "hermes_cli.config.read_raw_config",
        lambda: {"browser": {"terminate_daemon_on_timeout": "false"}},
    )

    assert bt._should_terminate_daemon_on_timeout() is False


def test_timeout_policy_true_is_cached(monkeypatch):
    calls = 0

    def read_config():
        nonlocal calls
        calls += 1
        return {"browser": {"terminate_daemon_on_timeout": True}}

    monkeypatch.setattr("hermes_cli.config.read_raw_config", read_config)

    assert bt._should_terminate_daemon_on_timeout() is True
    assert bt._should_terminate_daemon_on_timeout() is True
    assert calls == 1


def test_multiplexed_profiles_resolve_policy_per_call(monkeypatch):
    """Multiplexed profile turns resolve their context-local config per call.

    Regression for the profile-leak blocker: a process-global cache would let
    profile A's ``terminate_daemon_on_timeout=true`` leak into profile B's
    ``false`` (and back), silently enabling immediate teardown for a profile
    that did not opt in.
    """
    values = iter([True, False, True])

    def read_config():
        return {"browser": {"terminate_daemon_on_timeout": next(values)}}

    monkeypatch.setattr("hermes_cli.config.read_raw_config", read_config)
    monkeypatch.setattr(bt, "get_hermes_home_override", lambda: "/profiles/b")

    assert bt._should_terminate_daemon_on_timeout() is True
    assert bt._should_terminate_daemon_on_timeout() is False
    assert bt._should_terminate_daemon_on_timeout() is True


def test_single_profile_policy_is_cached(monkeypatch):
    """Single profile caches the resolved policy (reads config once)."""
    calls = 0

    def read_config():
        nonlocal calls
        calls += 1
        return {"browser": {"terminate_daemon_on_timeout": True}}

    monkeypatch.setattr("hermes_cli.config.read_raw_config", read_config)
    monkeypatch.setattr(bt, "get_hermes_home_override", lambda: None)

    assert bt._should_terminate_daemon_on_timeout() is True
    assert bt._should_terminate_daemon_on_timeout() is True
    assert calls == 1


def test_cleanup_bare_task_includes_local_sidecar(tmp_path, monkeypatch):
    _session("task", "primary")
    _session("task::local", "sidecar")
    bt._last_active_session_key["task"] = "task::local"
    _pid_file(tmp_path, "primary", 101)
    _pid_file(tmp_path, "sidecar", 202)

    killed = []

    def _kill_confirmed(pid):
        killed.append(pid)
        return True

    monkeypatch.setattr(bt, "_socket_safe_tmpdir", lambda: str(tmp_path))
    monkeypatch.setattr(bt, "_stop_cdp_supervisor", lambda _key: None)
    monkeypatch.setattr(
        bt, "_verify_reapable_browser_daemon", lambda *_args: True
    )
    monkeypatch.setattr(
        "tools.process_registry.ProcessRegistry._terminate_host_pid",
        _kill_confirmed,
    )

    bt._cleanup_local_browser_after_timeout("task", "snapshot")

    assert killed == [101, 202]
    assert "task" not in bt._active_sessions
    assert "task::local" not in bt._active_sessions
    assert not bt._recording_sessions
    assert not bt._session_last_activity
    assert "task" not in bt._last_active_session_key
    assert not (tmp_path / "agent-browser-primary").exists()
    assert not (tmp_path / "agent-browser-sidecar").exists()


def test_sidecar_timeout_does_not_destroy_live_primary_binding(tmp_path, monkeypatch):
    _session("task", "primary")
    _session("task::local", "sidecar")
    bt._last_active_session_key["task"] = "task"
    _pid_file(tmp_path, "sidecar", 303)

    monkeypatch.setattr(bt, "_socket_safe_tmpdir", lambda: str(tmp_path))
    monkeypatch.setattr(bt, "_stop_cdp_supervisor", lambda _key: None)
    monkeypatch.setattr(
        bt, "_verify_reapable_browser_daemon", lambda *_args: True
    )
    monkeypatch.setattr(
        "tools.process_registry.ProcessRegistry._terminate_host_pid",
        lambda _pid: True,
    )

    bt._cleanup_local_browser_after_timeout("task::local", "click")

    assert "task" in bt._active_sessions
    assert "task::local" not in bt._active_sessions
    assert bt._last_active_session_key["task"] == "task"


def test_sidecar_timeout_drops_binding_when_sidecar_owned_it(tmp_path, monkeypatch):
    _session("task", "primary")
    _session("task::local", "sidecar")
    bt._last_active_session_key["task"] = "task::local"
    _pid_file(tmp_path, "sidecar", 404)

    monkeypatch.setattr(bt, "_socket_safe_tmpdir", lambda: str(tmp_path))
    monkeypatch.setattr(bt, "_stop_cdp_supervisor", lambda _key: None)
    monkeypatch.setattr(
        bt, "_verify_reapable_browser_daemon", lambda *_args: True
    )
    monkeypatch.setattr(
        "tools.process_registry.ProcessRegistry._terminate_host_pid",
        lambda _pid: True,
    )

    bt._cleanup_local_browser_after_timeout("task::local", "click")

    assert "task" in bt._active_sessions
    assert "task" not in bt._last_active_session_key


def test_termination_failure_preserves_recovery_metadata(tmp_path, monkeypatch):
    _session("task", "primary")
    bt._last_active_session_key["task"] = "task"
    _pid_file(tmp_path, "primary", 505)

    monkeypatch.setattr(bt, "_socket_safe_tmpdir", lambda: str(tmp_path))
    monkeypatch.setattr(bt, "_stop_cdp_supervisor", lambda _key: None)
    monkeypatch.setattr(
        bt, "_verify_reapable_browser_daemon", lambda *_args: True
    )
    monkeypatch.setattr(
        "tools.process_registry.ProcessRegistry._terminate_host_pid",
        MagicMock(side_effect=RuntimeError("kill exploded")),
    )

    bt._cleanup_local_browser_after_timeout("task", "open")

    assert "task" in bt._active_sessions
    assert "task" in bt._recording_sessions
    assert "task" in bt._session_last_activity
    assert bt._last_active_session_key["task"] == "task"
    assert (tmp_path / "agent-browser-primary").exists()


def test_unconfirmed_termination_preserves_recovery_metadata(tmp_path, monkeypatch):
    """Termination that returns without confirmation must preserve evidence.

    Regression for the review blocker: ``_terminate_host_pid`` can fail (e.g.
    Windows ``taskkill`` returns non-zero, POSIX ``AccessDenied`` swallowed)
    without raising, so a test that only simulates an exception misses the
    real failure mode.  The strict timeout path must treat a ``False`` return
    exactly like a failure: keep socket dir + session metadata.
    """
    _session("task", "primary")
    bt._last_active_session_key["task"] = "task"
    _pid_file(tmp_path, "primary", 909)
    terminate = MagicMock(return_value=False)  # kill ran but death unconfirmed

    monkeypatch.setattr(bt, "_socket_safe_tmpdir", lambda: str(tmp_path))
    monkeypatch.setattr(bt, "_stop_cdp_supervisor", lambda _key: None)
    monkeypatch.setattr(
        bt, "_verify_reapable_browser_daemon", lambda *_args: True
    )
    monkeypatch.setattr(
        "tools.process_registry.ProcessRegistry._terminate_host_pid", terminate
    )

    bt._cleanup_local_browser_after_timeout("task", "open")

    terminate.assert_called_once_with(909)
    assert "task" in bt._active_sessions
    assert "task" in bt._recording_sessions
    assert "task" in bt._session_last_activity
    assert bt._last_active_session_key["task"] == "task"
    assert (tmp_path / "agent-browser-primary").exists()


def test_confirmed_termination_removes_runtime_state(tmp_path, monkeypatch):
    """Verified ownership + confirmed termination → destructive cleanup."""
    _session("task", "primary")
    bt._last_active_session_key["task"] = "task"
    _pid_file(tmp_path, "primary", 1010)

    monkeypatch.setattr(bt, "_socket_safe_tmpdir", lambda: str(tmp_path))
    monkeypatch.setattr(bt, "_stop_cdp_supervisor", lambda _key: None)
    monkeypatch.setattr(
        bt, "_verify_reapable_browser_daemon", lambda *_args: True
    )
    monkeypatch.setattr(
        "tools.process_registry.ProcessRegistry._terminate_host_pid",
        MagicMock(return_value=True),
    )

    bt._cleanup_local_browser_after_timeout("task", "open")

    assert "task" not in bt._active_sessions
    assert "task" not in bt._recording_sessions
    assert "task" not in bt._session_last_activity
    assert "task" not in bt._last_active_session_key
    assert not (tmp_path / "agent-browser-primary").exists()


def test_terminate_host_pid_windows_taskkill_failure_returns_false(monkeypatch):
    """Windows taskkill returning non-zero must NOT count as success.

    Covers the real termination implementation (not a mocked helper): a failed
    ``taskkill`` with no exception must still yield ``False`` so the caller
    preserves recovery evidence instead of deleting a still-alive daemon.
    """
    from tools.process_registry import ProcessRegistry

    killed = []
    monkeypatch.setattr("tools.process_registry._IS_WINDOWS", True)
    monkeypatch.setattr(
        "subprocess.run",
        MagicMock(return_value=MagicMock(returncode=1)),
    )
    monkeypatch.setattr("os.kill", lambda pid, sig: killed.append(pid))
    monkeypatch.setattr(
        ProcessRegistry, "_pid_gone_within", MagicMock(return_value=False)
    )

    assert ProcessRegistry._terminate_host_pid(12345) is False
    assert killed == [12345]  # fallback signal was attempted


def test_terminate_host_pid_posix_confirmed_dead_returns_true(monkeypatch):
    """POSIX: whole tree confirmed gone after terminate → True."""
    from tools.process_registry import ProcessRegistry

    class FakeProc:
        pid = 12345

        def children(self, recursive=True):
            return []

        def terminate(self):
            pass

    class _NoSuchProcess(Exception):
        pass

    _fake_psutil_module(
        monkeypatch,
        Process=MagicMock(return_value=FakeProc()),
        NoSuchProcess=_NoSuchProcess,
        AccessDenied=PermissionError,
    )
    monkeypatch.setattr("tools.process_registry._IS_WINDOWS", False)
    monkeypatch.setattr(
        ProcessRegistry, "_daemon_term_grace_seconds", lambda: 1.0
    )
    monkeypatch.setattr(ProcessRegistry, "_proc_alive", lambda _p: False)

    assert ProcessRegistry._terminate_host_pid(12345) is True


def test_terminate_host_pid_posix_survivor_returns_false(monkeypatch):
    """POSIX: a surviving tree member → termination not confirmed → False."""
    from tools.process_registry import ProcessRegistry

    class FakeProc:
        pid = 12345

        def children(self, recursive=True):
            return []

        def terminate(self):
            pass

    class _NoSuchProcess(Exception):
        pass

    _fake_psutil_module(
        monkeypatch,
        Process=MagicMock(return_value=FakeProc()),
        NoSuchProcess=_NoSuchProcess,
        AccessDenied=PermissionError,
    )
    monkeypatch.setattr("tools.process_registry._IS_WINDOWS", False)
    monkeypatch.setattr(
        ProcessRegistry, "_daemon_term_grace_seconds", lambda: 0.0
    )
    monkeypatch.setattr(ProcessRegistry, "_proc_alive", lambda _p: True)

    assert ProcessRegistry._terminate_host_pid(12345) is False


def test_unverified_pid_preserves_recovery_metadata(tmp_path, monkeypatch):
    _session("task", "primary")
    bt._last_active_session_key["task"] = "task"
    _pid_file(tmp_path, "primary", 707)
    terminate = MagicMock()

    monkeypatch.setattr(bt, "_socket_safe_tmpdir", lambda: str(tmp_path))
    monkeypatch.setattr(bt, "_stop_cdp_supervisor", lambda _key: None)
    monkeypatch.setattr(
        bt, "_verify_reapable_browser_daemon", lambda *_args: False
    )
    monkeypatch.setattr(
        "tools.process_registry.ProcessRegistry._terminate_host_pid", terminate
    )

    bt._cleanup_local_browser_after_timeout("task", "snapshot")

    terminate.assert_not_called()
    assert "task" in bt._active_sessions
    assert "task" in bt._recording_sessions
    assert "task" in bt._session_last_activity
    assert bt._last_active_session_key["task"] == "task"
    assert (tmp_path / "agent-browser-primary").exists()


def test_malformed_pid_preserves_recovery_metadata(tmp_path, monkeypatch):
    _session("task", "primary")
    socket_dir = tmp_path / "agent-browser-primary"
    socket_dir.mkdir(parents=True, exist_ok=True)
    (socket_dir / "primary.pid").write_text("not-a-pid", encoding="utf-8")
    verify = MagicMock()
    terminate = MagicMock()

    monkeypatch.setattr(bt, "_socket_safe_tmpdir", lambda: str(tmp_path))
    monkeypatch.setattr(bt, "_stop_cdp_supervisor", lambda _key: None)
    monkeypatch.setattr(bt, "_verify_reapable_browser_daemon", verify)
    monkeypatch.setattr(
        "tools.process_registry.ProcessRegistry._terminate_host_pid", terminate
    )

    bt._cleanup_local_browser_after_timeout("task", "open")

    verify.assert_not_called()
    terminate.assert_not_called()
    assert "task" in bt._active_sessions
    assert "task" in bt._recording_sessions
    assert "task" in bt._session_last_activity
    assert socket_dir.exists()



def test_missing_pid_preserves_recovery_metadata(tmp_path, monkeypatch):
    _session("task", "primary")
    socket_dir = tmp_path / "agent-browser-primary"
    socket_dir.mkdir(parents=True, exist_ok=True)
    terminate = MagicMock()

    monkeypatch.setattr(bt, "_socket_safe_tmpdir", lambda: str(tmp_path))
    monkeypatch.setattr(
        "tools.process_registry.ProcessRegistry._terminate_host_pid", terminate
    )

    bt._cleanup_local_browser_after_timeout("task", "snapshot")

    terminate.assert_not_called()
    assert "task" in bt._active_sessions
    assert "task" in bt._recording_sessions
    assert "task" in bt._session_last_activity
    assert socket_dir.exists()

def test_cleanup_all_resets_timeout_policy_cache(monkeypatch):
    bt._cached_terminate_daemon = True
    bt._terminate_daemon_resolved = True
    monkeypatch.setattr(
        "tools.browser_supervisor.SUPERVISOR_REGISTRY.stop_all", lambda: None
    )

    bt.cleanup_all_browsers()

    assert bt._cached_terminate_daemon is None
    assert bt._terminate_daemon_resolved is False


def test_cleanup_bare_task_always_drops_last_active_binding(monkeypatch):
    """Cleaning a bare task drops its last-active binding unconditionally.

    Existing hybrid-routing contract (tests/tools/test_browser_hybrid_routing.py)
    requires ``cleanup_browser("default")`` to drop
    ``_last_active_session_key["default"]`` even when a live session is still
    recorded — a later click/snapshot must not resurrect a cleaned session.
    """
    bt._active_sessions["task"] = {"session_name": "primary"}
    bt._active_sessions["task::local"] = {"session_name": "sidecar"}
    bt._last_active_session_key["task"] = "task::local"
    bt._recording_sessions.update({"task", "task::local"})

    monkeypatch.setattr(bt, "_cleanup_single_browser_session", lambda _key: None)

    bt.cleanup_browser("task")

    assert "task" not in bt._last_active_session_key


def test_cleanup_sidecar_keeps_live_primary_binding(monkeypatch):
    """Cleaning a ``::local`` sidecar keeps the primary's live binding."""
    bt._active_sessions["task"] = {"session_name": "primary"}
    bt._active_sessions["task::local"] = {"session_name": "sidecar"}
    bt._last_active_session_key["task"] = "task"
    bt._recording_sessions.update({"task", "task::local"})

    monkeypatch.setattr(bt, "_cleanup_single_browser_session", lambda _key: None)

    bt.cleanup_browser("task::local")

    assert bt._last_active_session_key["task"] == "task"


def _timeout_result(tmp_path, monkeypatch, *, terminate: bool, cloud: bool = False):
    session_info = {
        "session_name": "timeout-session",
        "cdp_url": "wss://example.invalid/devtools/browser/x" if cloud else None,
    }
    proc = MagicMock()
    waits = 0

    def wait(timeout=None):
        nonlocal waits
        waits += 1
        if waits == 1:
            raise subprocess.TimeoutExpired(cmd="agent-browser", timeout=5)
        return 0

    proc.wait.side_effect = wait
    proc.returncode = None
    cleanup = MagicMock()

    monkeypatch.setattr(bt, "_find_agent_browser", lambda: "/usr/bin/agent-browser")
    monkeypatch.setattr(bt, "_requires_real_termux_browser_install", lambda _cmd: False)
    monkeypatch.setattr(bt, "_is_local_mode", lambda: False)
    monkeypatch.setattr(bt, "_get_session_info", lambda _task: session_info)
    monkeypatch.setattr(bt, "_is_headed_mode", lambda: False)
    monkeypatch.setattr(bt, "_get_browser_engine", lambda: "auto")
    monkeypatch.setattr(bt, "_is_camofox_mode", lambda: False)
    monkeypatch.setattr(bt, "_socket_safe_tmpdir", lambda: str(tmp_path))
    monkeypatch.setattr(bt, "_write_owner_pid", lambda *_args: None)
    monkeypatch.setattr(bt, "_build_browser_env", lambda: {"PATH": ""})
    monkeypatch.setattr(bt, "_merge_browser_path", lambda _path: "")
    monkeypatch.setattr(bt, "_needs_chromium_sandbox_bypass", lambda: False)
    monkeypatch.setattr(bt, "_lightpanda_fallback_reason", lambda *_args: None)
    monkeypatch.setattr(bt, "_should_terminate_daemon_on_timeout", lambda: terminate)
    monkeypatch.setattr(bt, "_cleanup_local_browser_after_timeout", cleanup)
    monkeypatch.setattr("tools.interrupt.is_interrupted", lambda: False)
    monkeypatch.setattr(bt.subprocess, "Popen", MagicMock(return_value=proc))

    result = bt._run_browser_command("task", "snapshot", [], timeout=5)
    return result, cleanup


def test_default_timeout_keeps_session_and_skips_immediate_teardown(tmp_path, monkeypatch):
    result, cleanup = _timeout_result(tmp_path, monkeypatch, terminate=False)

    assert result["success"] is False
    assert "timed out" in result["error"].lower()
    cleanup.assert_not_called()


def test_opt_in_local_timeout_runs_immediate_teardown(tmp_path, monkeypatch):
    result, cleanup = _timeout_result(tmp_path, monkeypatch, terminate=True)

    assert result["success"] is False
    cleanup.assert_called_once_with("task", "snapshot")


def test_opt_in_does_not_apply_local_daemon_teardown_to_cloud(tmp_path, monkeypatch):
    result, cleanup = _timeout_result(
        tmp_path, monkeypatch, terminate=True, cloud=True
    )

    assert result["success"] is False
    cleanup.assert_not_called()


def test_timeout_diagnostic_is_built_before_cleanup(tmp_path, monkeypatch):
    events = []
    session_info = {"session_name": "timeout-session", "cdp_url": None}
    proc = MagicMock()
    waits = 0

    def wait(timeout=None):
        nonlocal waits
        waits += 1
        if waits == 1:
            raise subprocess.TimeoutExpired(cmd="agent-browser", timeout=5)
        return 0

    proc.wait.side_effect = wait

    monkeypatch.setattr(bt, "_find_agent_browser", lambda: "/usr/bin/agent-browser")
    monkeypatch.setattr(bt, "_requires_real_termux_browser_install", lambda _cmd: False)
    monkeypatch.setattr(bt, "_is_local_mode", lambda: False)
    monkeypatch.setattr(bt, "_get_session_info", lambda _task: session_info)
    monkeypatch.setattr(bt, "_is_headed_mode", lambda: False)
    monkeypatch.setattr(bt, "_get_browser_engine", lambda: "auto")
    monkeypatch.setattr(bt, "_is_camofox_mode", lambda: False)
    monkeypatch.setattr(bt, "_socket_safe_tmpdir", lambda: str(tmp_path))
    monkeypatch.setattr(bt, "_write_owner_pid", lambda *_args: None)
    monkeypatch.setattr(bt, "_build_browser_env", lambda: {"PATH": ""})
    monkeypatch.setattr(bt, "_merge_browser_path", lambda _path: "")
    monkeypatch.setattr(bt, "_needs_chromium_sandbox_bypass", lambda: False)
    monkeypatch.setattr(bt, "_lightpanda_fallback_reason", lambda *_args: None)
    monkeypatch.setattr(bt, "_should_terminate_daemon_on_timeout", lambda: True)
    monkeypatch.setattr("tools.interrupt.is_interrupted", lambda: False)
    monkeypatch.setattr(bt.subprocess, "Popen", MagicMock(return_value=proc))

    def read_outputs(*_args):
        events.append("read")
        return "", "daemon diagnostic"

    def cleanup(*_args):
        events.append("cleanup")

    monkeypatch.setattr(bt, "_read_command_output_files", read_outputs)
    monkeypatch.setattr(bt, "_cleanup_local_browser_after_timeout", cleanup)

    result = bt._run_browser_command("task", "snapshot", [], timeout=5)

    assert events == ["read", "cleanup"]
    assert "daemon diagnostic" in result["error"]
