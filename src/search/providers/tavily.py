"""Tavily implementation of the search provider contract."""

import asyncio
from typing import Any, Dict, List, Optional

import httpx
from tavily import AsyncTavilyClient
from tavily.errors import (
    ForbiddenError as TavilyForbiddenError,
    InvalidAPIKeyError,
    KeylessUnsupportedEndpointError,
    MissingAPIKeyError,
    TimeoutError as TavilyTimeoutError,
    UsageLimitExceededError,
)

from src.search.providers.base import SearchProvider
from src.search.providers.errors import (
    AuthError,
    RateLimitError,
    SearchProviderError,
    TimeoutError as ProviderTimeoutError,
    UnavailableError,
)
from src.search.providers.models import ProviderSearchResult


class TavilyProvider(SearchProvider):
    """Search provider backed by Tavily's asynchronous API client."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        client: Optional[Any] = None,
        search_depth: str = "basic",
        timeout_seconds: float = 30.0,
    ) -> None:
        if not api_key and client is None:
            raise AuthError(
                "Tavily API key is required",
                provider="tavily",
            )
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")

        self._client = client or AsyncTavilyClient(api_key=api_key)
        self.search_depth = search_depth
        self.timeout_seconds = timeout_seconds

    @property
    def name(self) -> str:
        return "tavily"

    async def search(
        self,
        query: str,
        max_results: int,
    ) -> List[ProviderSearchResult]:
        normalized_query = query.strip()
        if not normalized_query or max_results <= 0:
            return []

        try:
            response = await self._client.search(
                query=normalized_query,
                max_results=max_results,
                search_depth=self.search_depth,
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            raise self._map_error(exc) from exc

        if not isinstance(response, dict):
            raise UnavailableError(
                "Tavily returned an invalid response",
                provider=self.name,
                details={"response_type": type(response).__name__},
            )

        provider_results: List[ProviderSearchResult] = []
        raw_results = response.get("results", [])
        if not isinstance(raw_results, list):
            raise UnavailableError(
                "Tavily response field 'results' is not a list",
                provider=self.name,
                details={"results_type": type(raw_results).__name__},
            )

        for item in raw_results[:max_results]:
            if not isinstance(item, dict):
                continue

            url = str(item.get("url") or "").strip()
            if not url:
                continue

            provider_results.append(
                ProviderSearchResult(
                    query=normalized_query,
                    title=str(item.get("title") or ""),
                    url=url,
                    snippet=str(item.get("content") or item.get("snippet") or ""),
                    provider=self.name,
                    metadata=self._extract_metadata(item),
                )
            )

        return provider_results

    def _map_error(self, exc: Exception) -> SearchProviderError:
        if isinstance(
            exc,
            (
                InvalidAPIKeyError,
                TavilyForbiddenError,
                MissingAPIKeyError,
                KeylessUnsupportedEndpointError,
            ),
        ):
            return AuthError(
                str(exc) or "Tavily authentication failed",
                provider=self.name,
            )

        if isinstance(exc, UsageLimitExceededError):
            return RateLimitError(
                str(exc) or "Tavily rate limit exceeded",
                provider=self.name,
                retry_after_seconds=getattr(exc, "retry_after_seconds", None),
            )

        if isinstance(
            exc,
            (TavilyTimeoutError, asyncio.TimeoutError, httpx.TimeoutException),
        ):
            return ProviderTimeoutError(
                str(exc) or "Tavily request timed out",
                provider=self.name,
            )

        if isinstance(exc, httpx.HTTPStatusError):
            status_code = exc.response.status_code
            details = {"status_code": status_code}
            if status_code in {401, 403}:
                return AuthError(
                    "Tavily authentication failed",
                    provider=self.name,
                    details=details,
                )
            if status_code == 429:
                return RateLimitError(
                    "Tavily rate limit exceeded",
                    provider=self.name,
                    details=details,
                )
            if status_code >= 500:
                return UnavailableError(
                    "Tavily service is unavailable",
                    provider=self.name,
                    details=details,
                )

            return SearchProviderError(
                "Tavily request failed",
                provider=self.name,
                details=details,
            )

        if isinstance(exc, httpx.RequestError):
            return UnavailableError(
                "Tavily network request failed",
                provider=self.name,
                details={"exception_type": type(exc).__name__},
            )

        return SearchProviderError(
            str(exc) or "Tavily search failed",
            provider=self.name,
            details={"exception_type": type(exc).__name__},
        )

    @staticmethod
    def _extract_metadata(item: Dict[str, Any]) -> Dict[str, Any]:
        metadata_keys = ("score", "published_date", "favicon")
        return {
            key: item[key]
            for key in metadata_keys
            if key in item and item[key] is not None
        }
