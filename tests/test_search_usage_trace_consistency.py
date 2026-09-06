"""SearchExecutionStats is the single source for search tool observations."""

import asyncio

import pytest

from src import agents as agents_module
from src.agent_trace import trace_node_execution
from src.agents.searcher import ResearchSearcher
from src.search.config import SearchConfig
from src.search.executor import SearchExecutor
from src.state import ResearchPlan, ResearchState, SearchQuery, SearchResult


class _PassingCredibilityScorer:
    def score_search_results(self, results: list[SearchResult]) -> list[dict]:
        return [
            {"result": result, "credibility": {"score": 90, "level": "high"}}
            for result in results
        ]


async def _search(query: str, max_results: int) -> list[dict[str, str]]:
    results = {
        "alpha": [
            {"query": query, "title": "Alpha one", "url": "https://example.com/a1", "snippet": "a1"},
            {"query": query, "title": "Alpha two", "url": "https://example.com/a2", "snippet": "a2"},
        ],
        "beta": [
            {"query": query, "title": "Beta one", "url": "https://example.com/b1", "snippet": "b1"},
        ],
    }
    return results[query][:max_results]


async def _extract(url: str) -> str:
    return f"content for {url}"


def _state() -> ResearchState:
    return ResearchState(
        research_topic="tool accounting",
        plan=ResearchPlan(
            topic="tool accounting",
            objectives=["account for tool calls"],
            search_queries=[
                SearchQuery(query="alpha", purpose="authority"),
                SearchQuery(query="beta", purpose="comparison"),
            ],
            report_outline=["Summary"],
        ),
    )


def test_usage_trace_and_diagnostics_share_search_execution_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agents_module.config, "searcher_adaptive_enabled", False)
    searcher = ResearchSearcher(
        llm=object(),
        credibility_scorer=_PassingCredibilityScorer(),
        search_config=SearchConfig(
            mode="deterministic_v2",
            max_search_times=2,
            max_extract_times=3,
            max_results_per_search=2,
            total_timeout_seconds=90,
        ),
    )
    searcher.search_executor = SearchExecutor(
        search_config=searcher.search_config,
        search_tool=_search,
        extract_tool=_extract,
    )
    state = _state()
    patch = asyncio.run(searcher.search(state))

    stats = next(item for item in patch["search_diagnostics"] if item["kind"] == "search_execution")
    call_detail = patch["llm_call_details"][-1]
    assert stats["search_calls"] == call_detail["search_calls"] == 2
    assert stats["extract_calls"] == call_detail["extract_calls"] == 3
    assert stats["tool_calls"] == patch["usage"].tool_calls == 5
    assert len(call_detail["tool_invocations"]) == 5

    async def execute(_state: ResearchState) -> dict:
        return patch

    traced = asyncio.run(
        trace_node_execution(
            state,
            node="search",
            agent="ResearchSearcher",
            operation="search",
            execute=execute,
        )
    )
    tool_events = [event for event in traced["agent_trace"] if event.event_type == "tool"]
    assert len(tool_events) == stats["tool_calls"]
    assert sum(event.operation == "search" for event in tool_events) == stats["search_calls"]
    assert sum(event.operation == "extract" for event in tool_events) == stats["extract_calls"]
