"""Deterministic search executor.

The executor owns tool calls, budgets, retries, de-duplication and deadlines.
An LLM may later analyze its output, but it does not control this loop.
"""

import asyncio
import inspect
import json
import time
from typing import Any, Iterable, Optional, Sequence, Union
from urllib.parse import urldefrag, urlsplit, urlunsplit

from src.search.config import SearchConfig
from src.search.models import SearchExecutionResult, SearchExecutionStats
from src.state import SearchQuery, SearchResult
from src.utils.tools import extract_webpage_content, web_search


QueryInput = Union[SearchQuery, str]
ToolLike = Any


class _BudgetExhausted(Exception):
    """Internal signal that a hard tool-call budget has been consumed."""


class SearchExecutor:
    """Execute a bounded, sequential search and extraction plan."""

    def __init__(
        self,
        search_config: Optional[SearchConfig] = None,
        search_tool: Optional[ToolLike] = None,
        extract_tool: Optional[ToolLike] = None,
    ) -> None:
        self.search_config = search_config or SearchConfig(mode="deterministic_v2")
        self.search_tool = search_tool or web_search
        self.extract_tool = extract_tool or extract_webpage_content

    async def execute(
        self,
        queries: Sequence[QueryInput],
        max_results_per_search: Optional[int] = None,
    ) -> SearchExecutionResult:
        """Run bounded searches followed by bounded content extraction."""

        stats = SearchExecutionStats()
        results: list[SearchResult] = []
        seen_queries: set[str] = set()
        seen_urls: set[str] = set()
        started = time.perf_counter()
        last_error: Optional[str] = None
        timed_out = False

        search_limit = self.search_config.max_search_times
        result_limit = (
            max_results_per_search
            if max_results_per_search is not None
            else self.search_config.max_results_per_search
        )

        if search_limit <= 0:
            return self._finish(
                results,
                "Search budget is zero",
                False,
                False,
                stats,
                started,
            )

        try:
            deadline = started + self.search_config.total_timeout_seconds

            for query_input in queries:
                if stats.search_calls >= search_limit:
                    break

                query = self._query_text(query_input)
                query_key = self._normalize_query(query)
                if not query_key or query_key in seen_queries:
                    continue
                seen_queries.add(query_key)

                try:
                    payload = await self._invoke_with_retries(
                        self.search_tool,
                        {"query": query, "max_results": result_limit},
                        self.search_config.search_retry_times,
                        deadline,
                        stats,
                        operation="search",
                    )
                except _BudgetExhausted:
                    break
                except asyncio.TimeoutError:
                    timed_out = True
                    break
                except Exception as exc:
                    stats.failed_calls += 1
                    last_error = f"web_search failed: {exc}"
                    continue

                for item in self._coerce_items(payload):
                    result = self._to_search_result(item, fallback_query=query)
                    if not result or not result.url:
                        continue

                    url_key = self._normalize_url(result.url)
                    if not url_key or url_key in seen_urls:
                        continue
                    seen_urls.add(url_key)
                    results.append(result)

            if not timed_out:
                for result in results[: self.search_config.max_extract_times]:
                    if self._deadline_expired(deadline):
                        timed_out = True
                        break

                    try:
                        content = await self._invoke_with_retries(
                            self.extract_tool,
                            {"url": result.url},
                            self.search_config.extract_retry_times,
                            deadline,
                            stats,
                            operation="extract",
                        )
                    except _BudgetExhausted:
                        break
                    except asyncio.TimeoutError:
                        timed_out = True
                        break
                    except Exception:
                        stats.failed_calls += 1
                        continue

                    if isinstance(content, str) and content.strip():
                        result.content = content

        except asyncio.TimeoutError:
            timed_out = True
        finally:
            stats.elapsed_seconds = round(time.perf_counter() - started, 2)

        if timed_out:
            timeout_message = (
                "Search executor timeout "
                f"({self.search_config.total_timeout_seconds:g} seconds)"
            )
            if not results or not self.search_config.allow_partial_results:
                return self._finish(
                    results,
                    timeout_message,
                    False,
                    False,
                    stats,
                    started,
                )
            return self._finish(
                results,
                None,
                False,
                True,
                stats,
                started,
            )

        if not results:
            return self._finish(
                results,
                last_error or "No search results",
                False,
                False,
                stats,
                started,
            )

        extraction_limited = len(results) > self.search_config.max_extract_times
        return self._finish(
            results,
            None,
            not extraction_limited,
            extraction_limited,
            stats,
            started,
        )

    async def _invoke_with_retries(
        self,
        tool: ToolLike,
        payload: dict[str, Any],
        retry_times: int,
        deadline: float,
        stats: SearchExecutionStats,
        operation: str,
    ) -> Any:
        attempts = retry_times + 1
        last_exception: Optional[Exception] = None

        for attempt in range(attempts):
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                raise asyncio.TimeoutError

            if operation == "search":
                if stats.search_calls >= self.search_config.max_search_times:
                    raise _BudgetExhausted
                stats.search_calls += 1
            else:
                if stats.extract_calls >= self.search_config.max_extract_times:
                    raise _BudgetExhausted
                stats.extract_calls += 1

            started = time.perf_counter()
            try:
                result = tool.ainvoke(payload) if hasattr(tool, "ainvoke") else tool(**payload)
                if inspect.isawaitable(result):
                    result = await asyncio.wait_for(result, timeout=remaining)
                stats.invocation_records.append({
                    "operation": operation,
                    "attempt": attempt + 1,
                    "success": True,
                    "duration": round(time.perf_counter() - started, 6),
                    "payload": dict(payload),
                })
                return result
            except asyncio.TimeoutError:
                stats.invocation_records.append({
                    "operation": operation,
                    "attempt": attempt + 1,
                    "success": False,
                    "duration": round(time.perf_counter() - started, 6),
                    "payload": dict(payload),
                    "error": "timeout",
                })
                raise
            except Exception as exc:
                last_exception = exc
                stats.invocation_records.append({
                    "operation": operation,
                    "attempt": attempt + 1,
                    "success": False,
                    "duration": round(time.perf_counter() - started, 6),
                    "payload": dict(payload),
                    "error": str(exc),
                })
                if attempt < attempts - 1:
                    if operation == "search":
                        stats.search_retries += 1
                    else:
                        stats.extract_retries += 1

        if last_exception:
            raise last_exception
        raise RuntimeError(f"{operation} failed without an exception")

    @staticmethod
    def _query_text(query_input: QueryInput) -> str:
        if isinstance(query_input, SearchQuery):
            return query_input.query.strip()
        return str(query_input).strip()

    @staticmethod
    def _normalize_query(query: str) -> str:
        return " ".join(query.lower().split())

    @staticmethod
    def _normalize_url(url: str) -> str:
        try:
            clean_url, _ = urldefrag(url.strip())
            parts = urlsplit(clean_url)
            if parts.scheme not in {"http", "https"} or not parts.netloc:
                return ""
            path = parts.path.rstrip("/") or "/"
            return urlunsplit(
                (parts.scheme.lower(), parts.netloc.lower(), path, parts.query, "")
            )
        except ValueError:
            return ""

    @staticmethod
    def _coerce_items(payload: Any) -> Iterable[Any]:
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                return []
        if isinstance(payload, list):
            return payload
        return []

    @staticmethod
    def _to_search_result(item: Any, fallback_query: str) -> Optional[SearchResult]:
        if isinstance(item, SearchResult):
            return item
        if not isinstance(item, dict):
            return None

        return SearchResult(
            query=str(item.get("query") or fallback_query),
            title=str(item.get("title") or ""),
            url=str(item.get("url") or ""),
            snippet=str(item.get("snippet") or ""),
            content=item.get("content"),
        )

    @staticmethod
    def _deadline_expired(deadline: float) -> bool:
        return time.perf_counter() >= deadline

    @staticmethod
    def _finish(
        results: list[SearchResult],
        error: Optional[str],
        completed: bool,
        partial: bool,
        stats: SearchExecutionStats,
        started: float,
    ) -> SearchExecutionResult:
        stats.elapsed_seconds = round(time.perf_counter() - started, 2)
        return SearchExecutionResult(
            search_results=results,
            error=error,
            completed=completed,
            partial=partial,
            stats=stats,
        )
