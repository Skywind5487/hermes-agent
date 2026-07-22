"""Behavioral tests for the Holographic prefetch kill switch."""
from __future__ import annotations

from plugins.memory.holographic import HolographicMemoryProvider


class _CountingRetriever:
    def __init__(self, results=None):
        self.calls = 0
        self.results = results or []

    def search(self, *args, **kwargs):
        self.calls += 1
        return self.results


def test_prefetch_disabled_does_not_query_retriever():
    provider = HolographicMemoryProvider(config={"prefetch_enabled": False})
    retriever = _CountingRetriever()
    provider._retriever = retriever

    assert provider.prefetch("query that would normally recall") == ""
    assert retriever.calls == 0


def test_prefetch_defaults_to_enabled():
    provider = HolographicMemoryProvider(config={})
    retriever = _CountingRetriever(
        [{"trust_score": 0.9, "content": "default-enabled fact"}]
    )
    provider._retriever = retriever

    assert "default-enabled fact" in provider.prefetch("query")
    assert retriever.calls == 1
