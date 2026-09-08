"""Fake-only contracts for deterministic, bounded search."""

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from src.runtime_control import RunPolicy, create_execution_context
from src.search.config import SearchConfig
from src.search.executor import SearchExecutor


class FakeSearchTool:
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


def test_search_config_rejects_removed_legacy_agent_mode() -> None:
    with pytest.raises(ValueError, match="deterministic_v2"):
        SearchConfig(mode="legacy_agent")  # type: ignore[arg-type]


def test_search_executor_enforces_search_budget() -> None:
    search_tool = FakeSearchTool(lambda query, _: [_result(query, f"https://example.com/{query}")])
    executor = SearchExecutor(
        search_config=SearchConfig(mode="deterministic_v2", max_search_times=2, max_extract_times=2),
        search_tool=search_tool,
        extract_tool=FakeExtractTool(lambda url: f"content for {url}"),
    )

    execution = asyncio.run(executor.execute(["one", "two", "three"]))

    assert [call["query"] for call in search_tool.calls] == ["one", "two"]
    assert execution.stats.search_calls == 2


def test_search_executor_deduplicates_normalized_queries() -> None:
    search_tool = FakeSearchTool(lambda query, _: [_result(query, f"https://example.com/{query}")])
    executor = SearchExecutor(
        search_config=SearchConfig(mode="deterministic_v2", max_search_times=3, max_extract_times=3),
        search_tool=search_tool,
        extract_tool=FakeExtractTool(lambda url: f"content for {url}"),
    )

    execution = asyncio.run(executor.execute(["  LangGraph  ", "langgraph", "LANGGRAPH", "other topic"]))

    assert [call["query"] for call in search_tool.calls] == ["LangGraph", "other topic"]
    assert execution.stats.search_calls == 2


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
        search_config=SearchConfig(mode="deterministic_v2", max_search_times=1, max_extract_times=3),
        search_tool=search_tool,
        extract_tool=extract_tool,
    )

    execution = asyncio.run(executor.execute(["topic"]))

    assert [result.url for result in execution.search_results] == [
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
        search_config=SearchConfig(mode="deterministic_v2", max_search_times=1, max_extract_times=1),
        search_tool=search_tool,
        extract_tool=extract_tool,
    )

    execution = asyncio.run(executor.execute(["topic"]))

    assert extract_tool.calls == ["https://example.com/one"]
    assert execution.stats.extract_calls == 1
    assert execution.partial is True


def test_search_executor_continues_after_partial_extraction_failure() -> None:
    def extract(url: str) -> str:
        if url.endswith("alpha"):
            raise RuntimeError("fake extraction failure")
        return f"content for {url}"

    executor = SearchExecutor(
        search_config=SearchConfig(mode="deterministic_v2", max_search_times=2, max_extract_times=2),
        search_tool=FakeSearchTool(lambda query, _: [_result(query, f"https://example.com/{query}")]),
        extract_tool=FakeExtractTool(extract),
    )

    execution = asyncio.run(executor.execute(["alpha", "beta"]))

    assert [result.content for result in execution.search_results] == [
        None,
        "content for https://example.com/beta",
    ]
    assert execution.stats.failed_calls == 1


def test_search_executor_propagates_runtime_budget_as_partial_output() -> None:
    executor = SearchExecutor(
        search_config=SearchConfig(
            mode="deterministic_v2", max_search_times=1, max_extract_times=1, allow_partial_results=True
        ),
        search_tool=FakeSearchTool(lambda query, _: [_result(query, "https://example.com/one")]),
        extract_tool=FakeExtractTool(lambda _: "unreachable"),
    )
    context = create_execution_context(
        run_id="search-budget",
        thread_id="thread-search-budget",
        policy=RunPolicy(max_operation_calls=1),
    )

    execution = asyncio.run(executor.execute(["topic"], execution_context=context))

    assert execution.partial is True
    assert execution.execution_context is not None
    assert execution.execution_context.operation_stop_reason == "budget_exhausted"
