"""Regression tests for the outbound LLM request transform boundary."""

from __future__ import annotations

import inspect
import logging
import types

from agent import conversation_loop
from hermes_cli.middleware import apply_llm_request_middleware


class _UncopyableProviderObject:
    def __deepcopy__(self, memo):
        raise TypeError("opaque provider object cannot be deep-copied")


def _install(monkeypatch, *callbacks):
    manager = types.SimpleNamespace(_middleware={"llm_request": list(callbacks)})
    monkeypatch.setattr("hermes_cli.plugins.get_plugin_manager", lambda: manager)


def test_missing_middleware_preserves_request_identity(monkeypatch):
    _install(monkeypatch)
    request = {"messages": [{"role": "user", "content": "hello"}]}

    result = apply_llm_request_middleware(request)

    assert result.payload is request
    assert result.original_payload is request
    assert result.changed is False
    assert result.trace == []


def test_request_replacements_chain_with_stable_original(monkeypatch):
    seen = []

    def first(**kwargs):
        kwargs["original_request"]["tampered"] = True
        return {"request": {**kwargs["request"], "first": True}, "source": "first"}

    def second(**kwargs):
        seen.append((kwargs["request"], kwargs["original_request"]))
        return {"request": {**kwargs["request"], "second": True}, "source": "second"}

    _install(monkeypatch, first, second)
    request = {
        "model": "test-model",
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [{"type": "function", "function": {"name": "demo"}}],
        "stream": True,
    }

    result = apply_llm_request_middleware(request)

    assert seen == [({**request, "first": True}, request)]
    assert result.payload == {**request, "first": True, "second": True}
    assert result.original_payload == request
    assert result.trace == [{"source": "first"}, {"source": "second"}]


def test_callback_failure_discards_nested_mutation(monkeypatch, caplog):
    seen = []

    def first(**kwargs):
        return {"request": {**kwargs["request"], "first": True}, "source": "first"}

    def failing(**kwargs):
        kwargs["request"]["messages"][0]["content"] = "corrupted"
        kwargs["request"]["partial"] = True
        raise RuntimeError("broken transformer")

    def last(**kwargs):
        seen.append(kwargs["request"])
        return {"request": {**kwargs["request"], "last": True}, "source": "last"}

    _install(monkeypatch, first, failing, last)
    request = {"messages": [{"role": "user", "content": "original"}]}

    with caplog.at_level(logging.WARNING):
        result = apply_llm_request_middleware(request)

    committed = {"messages": [{"role": "user", "content": "original"}], "first": True}
    assert seen == [committed]
    assert result.payload == {**committed, "last": True}
    assert request == {"messages": [{"role": "user", "content": "original"}]}
    assert "broken transformer" in caplog.text


def test_uncopyable_original_skips_callbacks_without_shared_state(monkeypatch, caplog):
    called = False

    def callback(**kwargs):
        nonlocal called
        called = True
        kwargs["request"]["messages"][0]["content"] = "changed"
        raise RuntimeError("must not run")

    _install(monkeypatch, callback)
    request = {
        "messages": [{"role": "user", "content": "original"}],
        "provider_state": _UncopyableProviderObject(),
    }

    with caplog.at_level(logging.WARNING):
        result = apply_llm_request_middleware(request)

    assert called is False
    assert result.payload is request
    assert result.original_payload is request
    assert request["messages"][0]["content"] == "original"
    assert "could not be isolated transactionally" in caplog.text


def test_uncopyable_replacement_is_not_committed(monkeypatch, caplog):
    def callback(**kwargs):
        kwargs["request"]["messages"][0]["content"] = "candidate-only"
        return {
            "request": {
                **kwargs["request"],
                "provider_state": _UncopyableProviderObject(),
            },
            "source": "uncopyable",
        }

    _install(monkeypatch, callback)
    request = {"messages": [{"role": "user", "content": "original"}]}

    with caplog.at_level(logging.WARNING):
        result = apply_llm_request_middleware(request)

    assert result.payload == request
    assert result.changed is False
    assert result.trace == []
    assert request["messages"][0]["content"] == "original"
    assert "returned a request that could not be isolated transactionally" in caplog.text


def test_transform_runs_before_common_provider_dispatch():
    source = inspect.getsource(conversation_loop.run_conversation)

    transform_at = source.index("apply_llm_request_middleware(")
    observer_at = source.index('"pre_api_request"', transform_at)
    dispatch_at = source.index("def _perform_api_call", transform_at)
    execution_at = source.index("run_llm_execution_middleware(", dispatch_at)

    assert transform_at < observer_at < dispatch_at < execution_at

    dispatch_block = source[dispatch_at:execution_at]
    assert 'agent.api_mode == "codex_responses"' in dispatch_block
    assert "agent._interruptible_streaming_api_call(" in dispatch_block
    assert "relay_llm.execute(" in dispatch_block
    assert "agent._interruptible_api_call" in dispatch_block
