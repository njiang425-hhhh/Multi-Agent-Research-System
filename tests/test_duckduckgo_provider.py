"""Fake-only tests for DuckDuckGo's unified provider adapter."""

import asyncio

import pytest
from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException

from src.search.providers.duckduckgo import DuckDuckGoProvider
from src.search.providers.errors import RateLimitError, TimeoutError as ProviderTimeoutError, UnavailableError
from src.search.providers.factory import SearchProviderFactory


def test_duckduckgo_provider_adapts_ddgs_results_without_policy() -> None:
    calls: list[tuple[str, int]] = []

    def fake_search(query: str, limit: int) -> list[dict[str, str]]:
        calls.append((query, limit))
        return [
            {"title": "One", "href": "https://one.example", "body": "First"},
            {"title": "No URL"},
        ]

    provider = DuckDuckGoProvider(search_callable=fake_search)
    results = asyncio.run(provider.search(" topic ", 3))

    assert calls == [("topic", 3)]
    assert [(item.query, item.url, item.provider) for item in results] == [
        ("topic", "https://one.example", "duckduckgo")
    ]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RatelimitException("fake rate limit"), RateLimitError),
        (TimeoutException("fake timeout"), ProviderTimeoutError),
        (DDGSException("fake unavailable"), UnavailableError),
        (RuntimeError("fake transport"), UnavailableError),
    ],
)
def test_duckduckgo_provider_classifies_transport_failures(error: Exception, expected: type[Exception]) -> None:
    def fake_search(_: str, __: int) -> list[dict[str, str]]:
        raise error

    with pytest.raises(expected) as exc_info:
        asyncio.run(DuckDuckGoProvider(search_callable=fake_search).search("topic", 1))

    assert getattr(exc_info.value, "retryable") is True


def test_factory_registers_duckduckgo_on_the_same_contract() -> None:
    provider = SearchProviderFactory().create("duckduckgo")

    assert provider.name == "duckduckgo"
