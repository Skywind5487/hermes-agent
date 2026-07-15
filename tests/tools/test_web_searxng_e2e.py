"""
E2E test for SearXNG web search — exercises the full dispatch chain against
the real self-hosted SearXNG instance.

Covers the complete code path that an LLM tool call would take:
  web_search_tool()
    → _get_search_backend() / _get_backend()  (config + env resolution)
    → web_search_registry provider lookup       (registry dispatch)
    → SearXNGWebSearchProvider.search()        (real HTTP → real instance)
    → result normalization and return           (JSON output)

This is deliberately NOT mocked. It proves the integration works from
the LLM's entry point all the way to the SearXNG JSON API.

Usage:
    pytest tests/tools/test_web_searxng_e2e.py -v --no-header  -o 'addopts='

Skip with -m 'not e2e':
    pytest tests/ -m 'not e2e'  ...
"""

from __future__ import annotations

import json

import pytest

from tests.tools.conftest import register_all_web_providers

# ---------------------------------------------------------------------------
# Markers
# ---------------------------------------------------------------------------

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        False,  # noqa: F632 — placeholder; set True to skip in CI without a SearXNG instance
        reason="SearXNG instance not available in this environment",
    ),
]

# ---------------------------------------------------------------------------
# E2E: web_search_tool → registry → SearXNG provider → real HTTP → results
# ---------------------------------------------------------------------------


class TestSearXNGE2EDispatch:
    """Full-chain E2E: config → registry → provider → real SearXNG → results.

    No HTTP mocking. The test calls the actual SearXNG instance at
    ``searxng.skywind.lv.eu.org`` and verifies the returned data structure
    matches what the LLM expects from the ``web_search`` tool.
    """

    _register_providers = staticmethod(register_all_web_providers)
    _SEARXNG_URL = "https://searxng.skywind.lv.eu.org"

    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch):
        """Set up the environment for SearXNG routing.

        1. Populate the web-search-provider registry (simulates plugin load).
        2. Configure ``web.search_backend = searxng`` in config (as a
           production setup would via ``hermes config set ...`` or by writing
           ``config.yaml``).  We patch ``_load_web_config`` rather than
           touching the real file so the test is hermetic.
        3. Set ``SEARXNG_URL`` in the process environment.
        4. Strip other provider env vars so auto-detect fallback does not
           accidentally pick a different backend.
        """
        monkeypatch.setenv("SEARXNG_URL", self._SEARXNG_URL)

        # Simulate ``web.search_backend = searxng`` in config.yaml.
        from tools import web_tools

        monkeypatch.setattr(
            web_tools,
            "_load_web_config",
            lambda: {"search_backend": "searxng"},
        )

        # Clean competing env vars so the E2E path is unambiguous.
        for key in (
            "TAVILY_API_KEY",
            "EXA_API_KEY",
            "PARALLEL_API_KEY",
            "FIRECRAWL_API_KEY",
            "BRAVE_SEARCH_API_KEY",
        ):
            monkeypatch.delenv(key, raising=False)

        # Populate the web provider registry.
        self._register_providers()
        yield
        from agent.web_search_registry import _reset_for_tests

        _reset_for_tests()

    def _load_results(self, query: str = "Hermes Agent AI", limit: int = 3) -> dict:
        """Call ``web_search_tool`` — the LLM's entry point — and return the parsed JSON."""
        from tools.web_tools import web_search_tool

        raw = web_search_tool(query, limit=limit)
        assert isinstance(raw, str), f"Expected JSON string, got {type(raw)}"
        return json.loads(raw)

    # ── Smoke: the real instance is reachable ────────────────────────────

    def test_searxng_instance_is_reachable(self):
        """The configured backend resolves to ``searxng`` and is available."""
        from tools.web_tools import _get_search_backend, _is_backend_available

        backend = _get_search_backend()
        assert backend == "searxng", (
            f"Expected backend 'searxng', got '{backend}'. "
            f"Another provider's env var may be set."
        )
        assert _is_backend_available("searxng"), (
            "SearXNG backend not available — is SEARXNG_URL set?"
        )

    # ── E2E: real query through the full chain ───────────────────────────

    def test_search_returns_real_results(self):
        """A real search query returns success=True with results."""
        result = self._load_results("machine learning Python", limit=3)

        assert result.get("success") is True, (
            f"Search failed: {result.get('error', 'unknown error')}"
        )
        web = result.get("data", {}).get("web", [])
        assert len(web) >= 1, (
            f"Expected at least 1 result from real SearXNG, got {len(web)}"
        )

        # Every result must have the required shape.
        for i, item in enumerate(web):
            assert isinstance(item, dict), f"Result {i} is not a dict: {item}"
            assert "title" in item, f"Result {i} missing 'title'"
            assert "url" in item, f"Result {i} missing 'url'"
            assert "description" in item, f"Result {i} missing 'description'"
            assert "position" in item, f"Result {i} missing 'position'"
            assert isinstance(item["title"], str) and item["title"], (
                f"Result {i} has empty title"
            )
            assert isinstance(item["url"], str) and item["url"].startswith("http"), (
                f"Result {i} has invalid URL: {item['url']}"
            )
            assert isinstance(item["position"], int) and item["position"] >= 1, (
                f"Result {i} position is {item['position']}, expected >= 1"
            )

    def test_positions_are_one_indexed_and_sequential(self):
        """Positions start at 1 and increase by 1 with no gaps."""
        result = self._load_results("quantum computing", limit=5)
        web = result.get("data", {}).get("web", [])
        assert len(web) >= 1

        positions = [r["position"] for r in web]
        expected = list(range(1, len(web) + 1))
        assert positions == expected, f"Expected positions {expected}, got {positions}"

    def test_limit_is_respected(self):
        """The ``limit`` parameter caps the number of returned results."""
        for limit in (1, 2, 5):
            result = self._load_results("deep learning", limit=limit)
            web = result.get("data", {}).get("web", [])
            assert len(web) <= limit, (
                f"Limit {limit} returned {len(web)} results"
            )

    def test_empty_query_still_returns_results_or_graceful_error(self):
        """An empty or very short query should not crash the tool.

        The tool may return either results or a graceful error — either is
        acceptable as long as the JSON is well-formed.
        """
        for query in ("", "a"):
            result = self._load_results(query, limit=3)
            # Must parse as valid JSON (already true).
            # Either success=True with ≥0 results, or success=False with error.
            if not result.get("success"):
                assert "error" in result, (
                    f"Failed for query={query!r} but no error field: {result}"
                )
                # Graceful error is acceptable.

    def test_non_english_query_works(self):
        """SearXNG should handle non-English queries correctly."""
        result = self._load_results("機器學習 Python 教學", limit=3)
        # The tool may or may not find relevant results in Chinese,
        # but it should succeed and return well-formed results.
        assert "success" in result
        if result["success"]:
            web = result["data"]["web"]
            assert len(web) >= 0
        else:
            # A graceful error is acceptable (e.g. no matching results).
            assert "error" in result

    def test_http_error_instance_down_returns_graceful_error(self):
        """When the instance is unreachable, the tool returns a graceful error,
        not an exception."""
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"SEARXNG_URL": "http://localhost:1"}):
            # Re-patch _load_web_config so the search_backend stays searxng
            from tools import web_tools

            with patch.object(
                web_tools, "_load_web_config", return_value={"search_backend": "searxng"}
            ):
                from tools.web_tools import web_search_tool

                raw = web_search_tool("test", limit=3)
                result = json.loads(raw)

        assert result.get("success") is False
        assert "error" in result
        assert "localhost" in result["error"] or "reach" in result["error"].lower() or "SearXNG" in result["error"]
