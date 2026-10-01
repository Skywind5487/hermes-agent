"""x-opencode-session rides on every OpenCode request (port of upstream #101864).

These exercise the real code paths — ``build_api_kwargs`` for the main turn and
``auxiliary_client._build_call_kwargs`` for compression/title/vision calls —
plus the target-detection, key-stability and merge-precedence contracts.
"""

from __future__ import annotations

import pytest

from agent import auxiliary_client as aux
from agent import portal_tags
from agent.opencode_affinity import (
    OPENCODE_SESSION_HEADER,
    is_opencode_target,
    merge_opencode_session_headers,
    opencode_session_headers,
)

_MSGS = [{"role": "user", "content": "hi"}]


# --- target detection -------------------------------------------------------


@pytest.mark.parametrize(
    "provider, base_url, expected",
    [
        ("opencode-go", "https://opencode.ai/zen/go/v1", True),
        ("opencode-zen", "https://opencode.ai/zen/v1", True),
        ("opencode-free", "https://opencode.ai/zen/v1", True),
        ("custom", "https://opencode.ai/zen/go/v1", True),  # URL-only detection
        ("openrouter", "https://openrouter.ai/api/v1", False),
        ("deepseek", "https://api.deepseek.com/v1", False),
    ],
)
def test_is_opencode_target(provider, base_url, expected):
    assert is_opencode_target(provider, base_url) is expected


# --- key derivation ---------------------------------------------------------


def test_key_prefers_conversation_root_and_is_stable():
    token = portal_tags.set_conversation_context("conv-root-1")
    try:
        first = opencode_session_headers("opencode-go", None, "sess-1")
        second = opencode_session_headers("opencode-go", None, "sess-1")
        assert first == second == {OPENCODE_SESSION_HEADER: "conv-root-1"}
    finally:
        portal_tags.reset_conversation_context(token)


def test_cron_per_fire_timestamp_collapses_to_one_scope():
    a = opencode_session_headers("opencode-go", None, "cron_abc_20260901_070000")
    b = opencode_session_headers("opencode-go", None, "cron_abc_20260902_070000")
    assert a == b == {OPENCODE_SESSION_HEADER: "cron_abc"}


def test_no_header_for_non_opencode_target():
    assert opencode_session_headers("openrouter", "https://openrouter.ai/api/v1", "sess-1") == {}


# --- merge semantics --------------------------------------------------------


def test_caller_pinned_header_wins():
    kwargs = {"extra_headers": {OPENCODE_SESSION_HEADER: "pinned"}}
    out = merge_opencode_session_headers(kwargs, "opencode-go", None, "sess-1")
    assert out["extra_headers"][OPENCODE_SESSION_HEADER] == "pinned"


def test_merge_leaves_other_providers_untouched():
    kwargs = {"extra_headers": {"X-Other": "1"}}
    out = merge_opencode_session_headers(
        kwargs, "deepseek", "https://api.deepseek.com/v1", "sess-1"
    )
    assert out["extra_headers"] == {"X-Other": "1"}


# --- integration: auxiliary calls share the conversation key ----------------


def test_auxiliary_build_call_kwargs_carries_header():
    token = portal_tags.set_conversation_context("conv-root-1")
    try:
        kwargs = aux._build_call_kwargs(
            "opencode-go",
            "mimo-v2.5",
            _MSGS,
            base_url="https://opencode.ai/zen/go/v1",
        )
        assert kwargs["extra_headers"][OPENCODE_SESSION_HEADER] == "conv-root-1"

        other = aux._build_call_kwargs(
            "openrouter",
            "x",
            _MSGS,
            base_url="https://openrouter.ai/api/v1",
        )
        assert OPENCODE_SESSION_HEADER not in (other.get("extra_headers") or {})
    finally:
        portal_tags.reset_conversation_context(token)


# --- integration: main turn -------------------------------------------------


def _agent(provider, model, base_url):
    from run_agent import AIAgent

    return AIAgent(
        api_key="test-key",
        base_url=base_url,
        model=model,
        provider=provider,
        quiet_mode=True,
        skip_context_files=True,
        skip_memory=True,
        session_id="sess-affinity-1",
    )


def test_main_turn_build_api_kwargs_carries_header():
    from agent.chat_completion_helpers import build_api_kwargs

    agent = _agent("opencode-go", "mimo-v2.5", "https://opencode.ai/zen/go/v1")
    kwargs = build_api_kwargs(agent, _MSGS)
    assert kwargs["extra_headers"][OPENCODE_SESSION_HEADER] == "sess-affinity-1"

    other = _agent("openrouter", "anthropic/claude-sonnet-4.6", "https://openrouter.ai/api/v1")
    assert OPENCODE_SESSION_HEADER not in (
        build_api_kwargs(other, _MSGS).get("extra_headers") or {}
    )
