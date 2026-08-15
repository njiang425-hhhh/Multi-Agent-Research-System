"""Tests for SearchExecutor runtime limits without network access."""

import asyncio
from collections.abc import Callable
from typing import Any

from src.search.config import SearchConfig
from src.search.executor import SearchExecutor


class FakeSearchTool:
    """In-memory search tool that records every invocation."""

    def __init__(self, handler: Callable[[str, int], Any]) -> None:
        self.handler = handler
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, query: str, max_results: int) -> Any:
        self.calls.append({"query": query, "max_results": max_results})
        result = self.handler(query, max_results)
        if asyncio.iscoroutine(result):
            return await result
        return result


class FakeExtractTool:
    """In-memory extraction tool that records every invocation."""

    def __init__(self, handler: Callable[[str], Any]) -> None:
        self.handler = handler
        self.calls: list[str] = []

    async def __call__(self, url: str) -> Any:
        self.calls.append(url)
        result = self.handler(url)
        if asyncio.iscoroutine(result):
            return await result
        return result


def _result(query: str, url: str) -> dict[str, str]:
    return {
        "query": query,
        "title": f"Title for {url}",
        "url": url,
        "snippet": f"Snippet for {url}",
    }


def test_search_executor_enforces_search_budget() -> None:
    search_tool = FakeSearchTool(lambda query, _: [_result(query, f"https://example.com/{query}")])
    extract_tool = FakeExtractTool(lambda url: f"content for {url}")
    executor = SearchExecutor(
        search_config=SearchConfig(
            mode="deterministic_v2",
            max_search_times=2,
            max_extract_times=2,
        ),
        search_tool=search_tool,
        extract_tool=extract_tool,
    )

    execution = asyncio.run(executor.execute(["one", "two", "three"]))

    assert [call["query"] for call in search_tool.calls] == ["one", "two"]
    assert execution.stats.search_calls == 2
    assert [result.query for result in execution.search_results] == ["one", "two"]


def test_search_executor_deduplicates_normalized_queries() -> None:
    search_tool = FakeSearchTool(lambda query, _: [_result(query, f"https://example.com/{query}")])
    extract_tool = FakeExtractTool(lambda url: f"content for {url}")
    executor = SearchExecutor(
        search_config=SearchConfig(
            mode="deterministic_v2",
            max_search_times=3,
            max_extract_times=3,
        ),
        search_tool=search_tool,
        extract_tool=extract_tool,
    )

    execution = asyncio.run(
        executor.execute(["  LangGraph  ", "langgraph", "LANGGRAPH", "other topic"])
    )

    assert [call["query"] for call in search_tool.calls] == ["LangGraph", "other topic"]
    assert execution.stats.search_calls == 2
    assert len(execution.search_results) == 2


def test_search_executor_deduplicates_normalized_urls_before_extraction() -> None:
    search_tool = FakeSearchTool(
        lambda query, _: [
            _result(query, "https://EXAMPLE.com/article/#introduction"),
            _result(query, "https://example.com/article"),
            _result(query, "https://example.com/other"),
        ]
    )
    extract_tool = FakeExtractTool(lambda url: f"content for {url}")
    executor = SearchExecutor(
        search_config=SearchConfig(
            mode="deterministic_v2",
            max_search_times=1,
            max_extract_times=3,
        ),
        search_tool=search_tool,
        extract_tool=extract_tool,
    )

    execution = asyncio.run(executor.execute(["topic"]))

    assert [result.url for result in execution.search_results] == [
        "https://EXAMPLE.com/article/#introduction",
        "https://example.com/other",
    ]
    assert extract_tool.calls == [
        "https://EXAMPLE.com/article/#introduction",
        "https://example.com/other",
    ]
    assert execution.stats.extract_calls == 2


def test_search_executor_enforces_extract_budget_and_returns_partial_results() -> None:
    search_tool = FakeSearchTool(
        lambda query, _: [
            _result(query, "https://example.com/one"),
            _result(query, "https://example.com/two"),
            _result(query, "https://example.com/three"),
        ]
    )
    extract_tool = FakeExtractTool(lambda url: f"content for {url}")
    executor = SearchExecutor(
        search_config=SearchConfig(
            mode="deterministic_v2",
            max_search_times=1,
            max_extract_times=1,
        ),
        search_tool=search_tool,
        extract_tool=extract_tool,
    )

    execution = asyncio.run(executor.execute(["topic"]))

    assert extract_tool.calls == ["https://example.com/one"]
    assert execution.stats.extract_calls == 1
    assert execution.search_results[0].content == "content for https://example.com/one"
    assert [result.content for result in execution.search_results[1:]] == [None, None]
    assert execution.completed is False
    assert execution.partial is True
    assert execution.error is None


def test_search_executor_returns_partial_results_after_timeout_when_allowed() -> None:
    async def slow_extract(_: str) -> str:
        await asyncio.sleep(0.2)
        return "unreachable content"

    search_tool = FakeSearchTool(lambda query, _: [_result(query, "https://example.com/one")])
    extract_tool = FakeExtractTool(slow_extract)
    executor = SearchExecutor(
        search_config=SearchConfig(
            mode="deterministic_v2",
            max_search_times=1,
            max_extract_times=1,
            total_timeout_seconds=0.05,
            allow_partial_results=True,
        ),
        search_tool=search_tool,
        extract_tool=extract_tool,
    )

    execution = asyncio.run(executor.execute(["topic"]))

    assert execution.search_results[0].url == "https://example.com/one"
    assert execution.search_results[0].content is None
    assert execution.completed is False
    assert execution.partial is True
    assert execution.error is None


def test_search_executor_reports_timeout_when_partial_results_are_disabled() -> None:
    async def slow_extract(_: str) -> str:
        await asyncio.sleep(0.2)
        return "unreachable content"

    search_tool = FakeSearchTool(lambda query, _: [_result(query, "https://example.com/one")])
    extract_tool = FakeExtractTool(slow_extract)
    executor = SearchExecutor(
        search_config=SearchConfig(
            mode="deterministic_v2",
            max_search_times=1,
            max_extract_times=1,
            total_timeout_seconds=0.05,
            allow_partial_results=False,
        ),
        search_tool=search_tool,
        extract_tool=extract_tool,
    )

    execution = asyncio.run(executor.execute(["topic"]))

    assert execution.search_results[0].url == "https://example.com/one"
    assert execution.completed is False
    assert execution.partial is False
    assert execution.error == "Search executor timeout (0.05 seconds)"


def test_search_executor_reports_an_error_when_all_searches_fail() -> None:
    def fail_search(_: str, __: int) -> list[dict[str, str]]:
        raise RuntimeError("fake search failure")

    search_tool = FakeSearchTool(fail_search)
    extract_tool = FakeExtractTool(lambda _: "not used")
    executor = SearchExecutor(
        search_config=SearchConfig(
            mode="deterministic_v2",
            max_search_times=2,
            max_extract_times=2,
        ),
        search_tool=search_tool,
        extract_tool=extract_tool,
    )

    execution = asyncio.run(executor.execute(["one", "two"]))

    assert len(search_tool.calls) == 2
    assert extract_tool.calls == []
    assert execution.search_results == []
    assert execution.completed is False
    assert execution.partial is False
    assert execution.stats.failed_calls == 2
    assert execution.error == "web_search failed: fake search failure"


def test_search_executor_records_each_tool_retry_for_trace_projection() -> None:
    attempts = 0

    def retry_once(query: str, _: int) -> list[dict[str, str]]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("fake retry")
        return [_result(query, "https://example.com/retry")]

    executor = SearchExecutor(
        search_config=SearchConfig(
            mode="deterministic_v2",
            max_search_times=2,
            max_extract_times=1,
            search_retry_times=1,
        ),
        search_tool=FakeSearchTool(retry_once),
        extract_tool=FakeExtractTool(lambda _: "fake content"),
    )

    execution = asyncio.run(executor.execute(["retry topic"]))

    search_attempts = [item for item in execution.stats.invocation_records if item["operation"] == "search"]
    assert [item["attempt"] for item in search_attempts] == [1, 2]
    assert [item["success"] for item in search_attempts] == [False, True]
