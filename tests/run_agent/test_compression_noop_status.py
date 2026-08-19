"""Regression tests for plugin-reported compression no-op boundaries.

These cases intentionally use output that differs from the input. Equal-copy
results are already covered by the semantic no-progress guard merged upstream
in NousResearch/hermes-agent#67938; this file protects the residual contract
where an external context engine reports ``noop`` after cleanup-only active
context changes that must be adopted without minting a compression boundary.
"""

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from tests.run_agent.test_compression_boundary_hook import (
    TestCompressionBoundaryHook as _BoundaryHarness,
)


def _noop_compressor(cleaned_messages):
    compressor = MagicMock()

    def _compress(_messages, **_kwargs):
        compressor.last_compression_status = "noop"
        return list(cleaned_messages)

    compressor.compress.side_effect = _compress
    compressor.compression_count = 0
    compressor.last_prompt_tokens = 0
    compressor.last_completion_tokens = 0
    compressor._last_summary_error = None
    compressor._last_compress_aborted = False
    compressor._last_compression_made_progress = False
    compressor._last_summary_fallback_used = False
    compressor._last_compression_feasibility_reason = None
    compressor.last_compression_status = ""
    return compressor


def test_plugin_noop_adopts_cleanup_without_session_boundary():
    """A reported no-op may change active context without being a split."""
    from hermes_state import SessionDB

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        with SessionDB(db_path=db_path) as db:
            agent = _BoundaryHarness()._make_agent(db)

            messages = [
                {"role": "user", "content": "replayed scaffold"},
                {"role": "assistant", "content": "fresh tail"},
            ]
            cleaned = [{"role": "assistant", "content": "fresh tail"}]
            compressor = _noop_compressor(cleaned)
            agent.context_compressor = compressor
            agent._cached_system_prompt = "cached-system-prompt"

            original_sid = agent.session_id
            compressed, prompt = agent._compress_context(
                messages,
                "sys",
                approx_tokens=10_000,
            )

            # This must exercise the explicit status seam, not #67938's equality
            # guard: cleanup changed the active context.
            assert compressed == cleaned
            assert compressed != messages
            assert prompt == "cached-system-prompt"
            assert agent.session_id == original_sid

            compression_boundary_calls = [
                call
                for call in compressor.on_session_start.call_args_list
                if call.kwargs.get("boundary_reason") == "compression"
            ]
            assert not compression_boundary_calls

            conn = sqlite3.connect(str(db_path))
            try:
                child_count = conn.execute(
                    "SELECT COUNT(*) FROM sessions WHERE parent_session_id = ?",
                    (original_sid,),
                ).fetchone()[0]
            finally:
                conn.close()
            assert child_count == 0


def test_stale_noop_status_does_not_suppress_successful_transition():
    """A later successful result must not inherit a previous no-op status."""
    from hermes_state import SessionDB

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        with SessionDB(db_path=db_path) as db:
            agent = _BoundaryHarness()._make_agent(db)
            compressor = _noop_compressor(
                [{"role": "assistant", "content": "cleaned tail"}]
            )
            calls = 0

            def _compress(_messages, **_kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    compressor.last_compression_status = "noop"
                    return [{"role": "assistant", "content": "cleaned tail"}]
                # Deliberately leave the previous public status untouched.
                return [{"role": "user", "content": "summary"}]

            compressor.compress.side_effect = _compress
            agent.context_compressor = compressor
            original_sid = agent.session_id

            messages = [
                {"role": "user", "content": "replayed scaffold"},
                {"role": "assistant", "content": "fresh tail"},
            ]
            compressed, _ = agent._compress_context(
                messages,
                "sys",
                approx_tokens=10_000,
            )
            assert compressed == [
                {"role": "assistant", "content": "cleaned tail"}
            ]
            assert agent.session_id == original_sid

            compressed, _ = agent._compress_context(
                compressed,
                "sys",
                approx_tokens=10_000,
            )

            # The successful transition is the contract under test, not
            # byte-equality of the payload: rotation + persistence may attach
            # internal message metadata (e.g. ``_row_id``), so compare only the
            # fields that prove the summary was adopted and the session rotated.
            assert compressed[0]["role"] == "user"
            assert compressed[0]["content"] == "summary"
            assert agent.session_id != original_sid
