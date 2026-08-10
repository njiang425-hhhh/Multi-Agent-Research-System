"""Contract tests for TavilyProvider without external API access."""

from typing import Any

import httpx
import pytest

from src.search.providers.errors import (
    AuthError,
    RateLimitError,
    TimeoutError as ProviderTimeoutError,
    UnavailableError,
)
from src.search.providers.tavily import TavilyProvider


class FakeAsyncTavilyClient:
    """A deterministic async Tavily client substitute."""

    def __init__(self, response_or_error: Any) -> None:
        self.response_or_error = response_or_error
        self.calls: list[dict[str, Any]] = []

    async def search(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.response_or_error, Exception):
            raise self.response_or_error
        return self.response_or_error


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.tavily.com/search")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("fake Tavily HTTP error", request=request, response=response)


def test_tavily_provider_converts_a_response_and_preserves_metadata() -> None:
    client = FakeAsyncTavilyClient(
        {
            "results": [
                {
                    "title": "Primary source",
                    "url": "https://example.com/primary",
                    "content": "Full provider excerpt",
                    "snippet": "Fallback that should not be used",
                    "score": 0.98,
                    "published_date": "2026-08-01",
                    "favicon": "https://example.com/favicon.ico",
                    "unrelated_field": "not part of metadata contract",
                }
            ]
        }
    )
    provider = TavilyProvider(client=client)

    results = __import__("asyncio").run(provider.search("  LangGraph release notes  ", 5))

    assert client.calls == [
        {
            "query": "LangGraph release notes",
            "max_results": 5,
            "search_depth": "basic",
            "timeout": 30.0,
        }
    ]
    assert len(results) == 1
    result = results[0]
    assert result.query == "LangGraph release notes"
    assert result.title == "Primary source"
    assert result.url == "https://example.com/primary"
    assert result.snippet == "Full provider excerpt"
    assert result.provider == "tavily"
    assert result.metadata == {
        "score": 0.98,
        "published_date": "2026-08-01",
        "favicon": "https://example.com/favicon.ico",
    }


def test_tavily_provider_falls_back_to_snippet_and_filters_results_without_urls() -> None:
    client = FakeAsyncTavilyClient(
        {
            "results": [
                {"title": "No URL", "content": "discarded"},
                {
                    "title": "Snippet source",
                    "url": "https://example.com/snippet",
                    "snippet": "Snippet fallback",
                },
            ]
        }
    )
    provider = TavilyProvider(client=client)

    results = __import__("asyncio").run(provider.search("topic", 2))

    assert len(results) == 1
    assert results[0].url == "https://example.com/snippet"
    assert results[0].snippet == "Snippet fallback"


def test_tavily_provider_limits_raw_results_to_max_results() -> None:
    client = FakeAsyncTavilyClient(
        {
            "results": [
                {"url": "https://example.com/one"},
                {"url": "https://example.com/two"},
                {"url": "https://example.com/three"},
            ]
        }
    )
    provider = TavilyProvider(client=client)

    results = __import__("asyncio").run(provider.search("topic", 2))

    assert [result.url for result in results] == [
        "https://example.com/one",
        "https://example.com/two",
    ]
    assert client.calls[0]["max_results"] == 2


def test_tavily_provider_returns_empty_results_without_calling_client_for_invalid_query() -> None:
    client = FakeAsyncTavilyClient({"results": [{"url": "https://example.com"}]})
    provider = TavilyProvider(client=client)

    assert __import__("asyncio").run(provider.search("   ", 5)) == []
    assert __import__("asyncio").run(provider.search("topic", 0)) == []
    assert client.calls == []


def test_tavily_provider_maps_timeouts_to_provider_timeout_error() -> None:
    provider = TavilyProvider(client=FakeAsyncTavilyClient(httpx.ReadTimeout("timed out")))

    with pytest.raises(ProviderTimeoutError) as exc_info:
        __import__("asyncio").run(provider.search("topic", 1))

    assert exc_info.value.provider == "tavily"


@pytest.mark.parametrize("status_code", [401, 403])
def test_tavily_provider_maps_auth_statuses_to_auth_error(status_code: int) -> None:
    provider = TavilyProvider(client=FakeAsyncTavilyClient(_http_status_error(status_code)))

    with pytest.raises(AuthError) as exc_info:
        __import__("asyncio").run(provider.search("topic", 1))

    assert exc_info.value.provider == "tavily"
    assert exc_info.value.details == {"status_code": status_code}


def test_tavily_provider_maps_rate_limits_to_rate_limit_error() -> None:
    provider = TavilyProvider(client=FakeAsyncTavilyClient(_http_status_error(429)))

    with pytest.raises(RateLimitError) as exc_info:
        __import__("asyncio").run(provider.search("topic", 1))

    assert exc_info.value.provider == "tavily"
    assert exc_info.value.details == {"status_code": 429}


@pytest.mark.parametrize("status_code", [500, 502, 503])
def test_tavily_provider_maps_server_errors_to_unavailable_error(status_code: int) -> None:
    provider = TavilyProvider(client=FakeAsyncTavilyClient(_http_status_error(status_code)))

    with pytest.raises(UnavailableError) as exc_info:
        __import__("asyncio").run(provider.search("topic", 1))

    assert exc_info.value.provider == "tavily"
    assert exc_info.value.details == {"status_code": status_code}


@pytest.mark.parametrize(
    "response",
    [
        ["not a response object"],
        {"results": "not a list"},
    ],
)
def test_tavily_provider_rejects_invalid_response_shapes(response: Any) -> None:
    provider = TavilyProvider(client=FakeAsyncTavilyClient(response))

    with pytest.raises(UnavailableError) as exc_info:
        __import__("asyncio").run(provider.search("topic", 1))

    assert exc_info.value.provider == "tavily"
