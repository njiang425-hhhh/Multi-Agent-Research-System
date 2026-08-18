"""Compatibility tests for the web_search LangChain Tool adapter."""

import asyncio
from typing import Any

import pytest

import src.utils.tools as tools_module
from src.search.providers.errors import AuthError, UnavailableError
from src.search.providers.models import ProviderSearchResult


class FakeProvider:
    """Provider fake used to verify the adapter without network calls."""

    def __init__(self, results_or_error: Any) -> None:
        self.results_or_error = results_or_error
        self.calls: list[tuple[str, int]] = []

    async def search(self, query: str, max_results: int) -> list[ProviderSearchResult]:
        self.calls.append((query, max_results))
        if isinstance(self.results_or_error, Exception):
            raise self.results_or_error
        return self.results_or_error


def test_web_search_adapts_tavily_results_to_legacy_dicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeProvider(
        [
            ProviderSearchResult(
                query="LangGraph",
                title="Official documentation",
                url="https://example.com/docs",
                snippet="Provider result excerpt",
                provider="tavily",
                metadata={"score": 0.98, "published_date": "2026-08-01"},
            )
        ]
    )
    monkeypatch.setattr(tools_module.config, "search_provider", "tavily")
    monkeypatch.setattr(tools_module, "_get_search_provider", lambda _: provider)

    results = asyncio.run(
        tools_module.web_search.ainvoke({"query": "LangGraph", "max_results": 2})
    )

    assert provider.calls == [("LangGraph", 2)]
    assert results == [
        {
            "query": "LangGraph",
            "title": "Official documentation",
            "url": "https://example.com/docs",
            "snippet": "Provider result excerpt",
        }
    ]
    assert "provider" not in results[0]
    assert "metadata" not in results[0]


def test_web_search_uses_configured_default_max_results_for_tavily(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeProvider([])
    monkeypatch.setattr(tools_module.config, "search_provider", "tavily")
    monkeypatch.setattr(tools_module.config, "max_search_results_per_query", 4)
    monkeypatch.setattr(tools_module, "_get_search_provider", lambda _: provider)

    results = asyncio.run(tools_module.web_search.ainvoke({"query": "LangGraph"}))

    assert results == []
    assert provider.calls == [("LangGraph", 4)]


@pytest.mark.parametrize(
    "provider_error",
    [
        AuthError("missing fake key", provider="tavily"),
        UnavailableError("fake service outage", provider="tavily"),
    ],
)
def test_web_search_propagates_tavily_provider_errors(
    monkeypatch: pytest.MonkeyPatch,
    provider_error: Exception,
) -> None:
    provider = FakeProvider(provider_error)
    monkeypatch.setattr(tools_module.config, "search_provider", "tavily")
    monkeypatch.setattr(tools_module, "_get_search_provider", lambda _: provider)

    with pytest.raises(type(provider_error)) as exc_info:
        asyncio.run(tools_module.web_search.ainvoke({"query": "LangGraph"}))

    assert exc_info.value is provider_error


def test_web_search_adapts_duckduckgo_provider_to_legacy_output_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeProvider(
        [
            ProviderSearchResult(
                query="LangGraph",
                title="DuckDuckGo result",
                url="https://example.com/duck",
                snippet="Provider excerpt",
                provider="duckduckgo",
            )
        ]
    )
    monkeypatch.setattr(tools_module.config, "search_provider", "duckduckgo")
    monkeypatch.setattr(tools_module, "_get_search_provider", lambda _: provider)

    results = asyncio.run(
        tools_module.web_search.ainvoke({"query": "LangGraph", "max_results": 3})
    )

    assert provider.calls == [("LangGraph", 3)]
    assert results == [
        {
            "query": "LangGraph",
            "title": "DuckDuckGo result",
            "url": "https://example.com/duck",
            "snippet": "Provider excerpt",
        }
    ]
