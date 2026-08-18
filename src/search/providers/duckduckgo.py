"""DuckDuckGo implementation of the provider-independent search contract."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Optional

from ddgs import DDGS
from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException

from src.search.providers.base import SearchProvider
from src.search.providers.errors import (
    RateLimitError,
    TimeoutError as ProviderTimeoutError,
    UnavailableError,
)
from src.search.providers.models import ProviderSearchResult


class DuckDuckGoProvider(SearchProvider):
    """Thin DDGS transport adapter with no retry, fallback, or circuit policy."""

    def __init__(
        self,
        *,
        search_callable: Optional[Callable[[str, int], list[dict[str, Any]]]] = None,
    ) -> None:
        self._search_callable = search_callable or self._search_sync

    @property
    def name(self) -> str:
        return "duckduckgo"

    async def search(self, query: str, max_results: int) -> list[ProviderSearchResult]:
        normalized_query = query.strip()
        if not normalized_query or max_results <= 0:
            return []

        try:
            raw_results = await asyncio.to_thread(
                self._search_callable, normalized_query, max_results
            )
        except Exception as exc:
            raise self._map_error(exc) from exc

        if not isinstance(raw_results, list):
            raise UnavailableError(
                "DuckDuckGo returned an invalid response",
                provider=self.name,
                details={"response_type": type(raw_results).__name__},
            )

        results: list[ProviderSearchResult] = []
        for item in raw_results[:max_results]:
            if not isinstance(item, dict):
                continue
            url = str(item.get("href") or item.get("url") or "").strip()
            if not url:
                continue
            results.append(
                ProviderSearchResult(
                    query=normalized_query,
                    title=str(item.get("title") or ""),
                    url=url,
                    snippet=str(item.get("body") or item.get("snippet") or ""),
                    provider=self.name,
                )
            )
        return results

    @staticmethod
    def _search_sync(query: str, max_results: int) -> list[dict[str, Any]]:
        return list(DDGS().text(query, max_results=max_results))

    def _map_error(self, exc: Exception):
        if isinstance(exc, RatelimitException):
            return RateLimitError(
                str(exc) or "DuckDuckGo rate limit exceeded",
                provider=self.name,
            )
        if isinstance(exc, (TimeoutException, asyncio.TimeoutError)):
            return ProviderTimeoutError(
                str(exc) or "DuckDuckGo request timed out",
                provider=self.name,
            )
        if isinstance(exc, DDGSException):
            return UnavailableError(
                str(exc) or "DuckDuckGo search failed",
                provider=self.name,
                details={"exception_type": type(exc).__name__},
            )
        return UnavailableError(
            str(exc) or "DuckDuckGo search failed",
            provider=self.name,
            details={"exception_type": type(exc).__name__},
        )
