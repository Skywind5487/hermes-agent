"""
E2E: real timeout trigger through _run_browser_command.

Calls _run_browser_command with a very short timeout (5s) that will
definitely fire TimeoutExpired because starting Chrome daemon on this
VM takes ~15s. Verifies the cleanup code actually runs.
"""

import os
import sys
import time
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))


def e2e_timeout_trigger():
    task_id = f"e2e-{int(time.time())}"
    tmpdir = tempfile.mkdtemp()

    # Pre-populate _active_sessions so _get_session_info doesn't create one
    import tools.browser_tool as bt

    session_name = f"e2e-session-{int(time.time())}"
    with bt._cleanup_lock:
        bt._active_sessions[task_id] = {"session_name": session_name}
        bt._session_last_activity[task_id] = time.time()
    bt._last_active_session_key[task_id] = task_id
    bt._recording_sessions.add(task_id)

    print(f"[Setup] task_id={task_id}, session_name={session_name}")
    print(f"[Setup] _active_sessions has {len(bt._active_sessions)} key(s)")
    print(f"[Setup] _recording_sessions has task_id: {task_id in bt._recording_sessions}")
    print(f"[Setup] _last_active_session_key has task_id: {task_id in bt._last_active_session_key}")

    # Wait for any previous browser daemon cleanup
    # Patch _socket_safe_tmpdir to use our temp dir so we can check cleanup
    original_tmpdir = bt._socket_safe_tmpdir
    bt._socket_safe_tmpdir = lambda: tmpdir

    try:
        # Call with 5s timeout — Chrome takes ~15s to start on this VM
        print(f"\n[Call] _run_browser_command(task_id, 'open', args=['about:blank'], timeout=5)")
        print(f"[Call] Expecting TimeoutExpired...")
        result = bt._run_browser_command(task_id, "open", args=["about:blank"], timeout=5)

        print(f"\n[Result] {result}")
        assert result["success"] is False, "Expected failure"
        assert "timed out" in result["error"], f"Expected timeout error, got: {result['error']}"
        print(f"[Result] ✅ Correctly returned timeout error")

        # Verify cleanup
        with bt._cleanup_lock:
            active_has = task_id in bt._active_sessions
            last_activity_has = task_id in bt._session_last_activity

        recording_has = task_id in bt._recording_sessions
        last_key_has = task_id in bt._last_active_session_key

        print(f"\n[Cleanup] _active_sessions has task_id: {active_has} → expect False")
        print(f"[Cleanup] _session_last_activity has task_id: {last_activity_has} → expect False")
        print(f"[Cleanup] _recording_sessions has task_id: {recording_has} → expect False")
        print(f"[Cleanup] _last_active_session_key has task_id: {last_key_has} → expect False")

        assert not active_has, "_active_sessions not cleaned"
        assert not last_activity_has, "_session_last_activity not cleaned"
        assert not recording_has, "_recording_sessions not cleaned"
        assert not last_key_has, "_last_active_session_key not cleaned"
        print(f"[Cleanup] ✅ All dicts/sets properly cleaned")

        # Verify socket dir was cleaned
        sdir = os.path.join(tmpdir, f"agent-browser-{session_name}")
        if os.path.isdir(sdir):
            print(f"[Cleanup] ⚠️  Socket dir still exists: {sdir}")
            print(f"  Contents: {os.listdir(sdir)}")
        else:
            print(f"[Cleanup] ✅ Socket dir was removed")

        print(f"\n{'='*50}")
        print(f"✅ E2E PASSED — timeout handler correctly cleaned up")
        return True

    except Exception as e:
        import traceback
        traceback.print_exc()

        print(f"\n{'='*50}")
        print(f"❌ E2E FAILED: {e}")
        return False

    finally:
        bt._socket_safe_tmpdir = original_tmpdir
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    success = e2e_timeout_trigger()
    sys.exit(0 if success else 1)
