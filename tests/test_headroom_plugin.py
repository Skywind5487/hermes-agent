from __future__ import annotations

import importlib.util
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture()
def headroom_plugin(monkeypatch):
    path = Path(__file__).parents[1] / "plugins" / "headroom" / "__init__.py"
    spec = importlib.util.spec_from_file_location("_test_headroom_plugin", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_store_instance", None)
    monkeypatch.setattr(module, "_store_initialized", False)
    monkeypatch.setattr(module, "_router_instance", None)
    monkeypatch.setattr(module, "_router_initialized", False)
    return module


def _settings(module, **overrides):
    values = dict(
        enabled=True,
        kill_switch=False,
        allowlist=frozenset({"search_files", "browser_snapshot"}),
        excluded_tools=frozenset(),
        min_content_chars=100,
        max_items=3,
        max_field_chars=80,
        max_snapshot_chars=200,
        content_router_enabled=False,
        retrieve_default_chars=64,
    )
    values.update(overrides)
    return module._Settings(**values)


def _large_search_result(chars: int = 2000) -> str:
    return json.dumps(
        {
            "total_count": 20,
            "truncated": True,
            "matches": [
                {"path": f"src/file_{i}.py", "line": i + 1, "content": "x" * chars}
                for i in range(8)
            ],
        }
    )


def test_below_threshold_is_unchanged(headroom_plugin, monkeypatch):
    module = headroom_plugin
    monkeypatch.setattr(module, "_settings", lambda: _settings(module, min_content_chars=10_000))
    result = _large_search_result(100)

    assert module._on_transform_tool_result(tool_name="search_files", result=result) is None


def test_non_allowlisted_tool_is_unchanged(headroom_plugin, monkeypatch):
    module = headroom_plugin
    monkeypatch.setattr(module, "_settings", lambda: _settings(module))

    assert module._on_transform_tool_result(tool_name="write_file", result=_large_search_result()) is None


def test_structured_compression_is_bounded_and_retrieval_optional(headroom_plugin, monkeypatch):
    module = headroom_plugin
    monkeypatch.setattr(module, "_settings", lambda: _settings(module))
    monkeypatch.setattr(module, "_get_store", lambda: None)
    original = _large_search_result()

    transformed = module._on_transform_tool_result(tool_name="search_files", result=original)

    assert transformed is not None
    assert len(transformed) < len(original)
    payload = json.loads(transformed)
    assert len(payload["matches"]) == 3
    assert payload["omitted_count"] == 5
    assert payload["_headroom"]["retrieval"] == {
        "available": False,
        "reason": "storage_unavailable",
        "version": "redacted",
    }
    assert "hash" not in payload["_headroom"]["retrieval"]


def test_retrieval_reference_only_emitted_after_successful_store(headroom_plugin, monkeypatch):
    module = headroom_plugin
    monkeypatch.setattr(module, "_settings", lambda: _settings(module))

    class Store:
        def store(self, **kwargs):
            assert kwargs["original"]
            return "abcdef123456abcdef123456"

    monkeypatch.setattr(module, "_get_store", lambda: Store())
    transformed = module._on_transform_tool_result(
        tool_name="search_files", result=_large_search_result()
    )

    assert transformed is not None
    retrieval = json.loads(transformed)["_headroom"]["retrieval"]
    assert retrieval["available"] is True
    assert retrieval["hash"] == "abcdef123456abcdef123456"
    assert retrieval["tool"] == "headroom_retrieve"


def test_store_failure_warns_but_compression_survives(headroom_plugin, monkeypatch, caplog):
    module = headroom_plugin
    monkeypatch.setattr(module, "_settings", lambda: _settings(module))

    class Store:
        def store(self, **kwargs):
            raise OSError("disk unavailable")

    monkeypatch.setattr(module, "_get_store", lambda: Store())
    with caplog.at_level(logging.WARNING):
        transformed = module._on_transform_tool_result(
            tool_name="search_files", result=_large_search_result()
        )

    assert transformed is not None
    assert json.loads(transformed)["_headroom"]["retrieval"]["available"] is False
    assert "failed to persist retrievable content" in caplog.text


def test_retrieve_is_paginated_and_hard_bounded(headroom_plugin, monkeypatch):
    module = headroom_plugin
    content = "0123456789" * 4000

    class Store:
        def retrieve(self, key):
            assert key == "abcdef123456abcdef123456"
            return SimpleNamespace(original_content=content)

    monkeypatch.setattr(module, "_get_store", lambda: Store())
    monkeypatch.setattr(module, "_settings", lambda: _settings(module, retrieve_default_chars=32))

    first = json.loads(
        module._bounded_retrieve(
            {"hash": "abcdef123456abcdef123456", "offset": 10, "max_chars": 50_000}
        )
    )

    assert first["success"] is True
    assert first["offset"] == 10
    assert first["returned_chars"] == module._HARD_MAX_RETRIEVE_CHARS
    assert len(first["content"]) == module._HARD_MAX_RETRIEVE_CHARS
    assert first["truncated"] is True
    assert first["next_offset"] == 10 + module._HARD_MAX_RETRIEVE_CHARS


def test_retrieval_failure_is_bounded_and_warned(headroom_plugin, monkeypatch, caplog):
    module = headroom_plugin

    class Store:
        def retrieve(self, key):
            raise RuntimeError("backend detail that should not reach the model")

    monkeypatch.setattr(module, "_get_store", lambda: Store())
    monkeypatch.setattr(module, "_settings", lambda: _settings(module))
    with caplog.at_level(logging.WARNING):
        result = json.loads(module._bounded_retrieve({"hash": "abcdef"}))

    assert result == {"success": False, "error": "retrieval failed"}
    assert "backend detail" not in json.dumps(result)
    assert "retrieval failed" in caplog.text


def test_registration_failure_does_not_remove_compression(headroom_plugin, caplog):
    module = headroom_plugin

    class Ctx:
        def __init__(self):
            self.hook = None

        def register_hook(self, name, callback, **kwargs):
            self.hook = (name, callback)

        def register_tool(self, **kwargs):
            raise RuntimeError("tool registration unavailable")

    ctx = Ctx()
    with caplog.at_level(logging.WARNING):
        module.register(ctx)

    assert ctx.hook is not None
    assert ctx.hook[0] == "transform_tool_result"
    assert "retrieval tool registration failed" in caplog.text


def test_end_to_end_compress_then_bounded_retrieve(headroom_plugin, monkeypatch):
    module = headroom_plugin
    monkeypatch.setattr(module, "_settings", lambda: _settings(module, retrieve_default_chars=40))
    saved: dict[str, str] = {}

    class Store:
        def store(self, *, original, compressed, tool_name):
            saved["abcdef123456abcdef123456"] = original
            return "abcdef123456abcdef123456"

        def retrieve(self, key):
            value = saved.get(key)
            return None if value is None else SimpleNamespace(original_content=value)

    store = Store()
    monkeypatch.setattr(module, "_get_store", lambda: store)

    class Ctx:
        def __init__(self):
            self.hook = None
            self.tool = None

        def register_hook(self, name, callback, **kwargs):
            self.hook = callback

        def register_tool(self, **kwargs):
            self.tool = kwargs

    ctx = Ctx()
    module.register(ctx)
    assert ctx.hook is not None and ctx.tool is not None

    transformed = ctx.hook(tool_name="search_files", result=_large_search_result())
    assert transformed is not None
    handle = json.loads(transformed)["_headroom"]["retrieval"]["hash"]

    page = json.loads(ctx.tool["handler"]({"hash": handle, "max_chars": 40}))
    assert page["success"] is True
    assert page["returned_chars"] == 40
    assert page["truncated"] is True
    assert page["next_offset"] == 40
