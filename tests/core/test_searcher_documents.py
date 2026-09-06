"""Canonical SearchResult-to-Document conversion contracts."""

import asyncio

from src.agents import ResearchSearcher
from src.search.config import SearchConfig
from src.search.models import SearchExecutionResult, SearchExecutionStats
from src.state import ResearchPlan, ResearchState, SearchQuery, SearchResult


def _result(url: str, *, title: str = "Source", content: str | None = "page text") -> SearchResult:
    return SearchResult(
        query="research topic", title=title, url=url, snippet=f"Snippet for {title}", content=content
    )


def _state() -> ResearchState:
    return ResearchState(
        query="research topic",
        research_plan=ResearchPlan(
            topic="research topic",
            objectives=["understand the topic"],
            search_queries=[SearchQuery(query="research topic", purpose="research")],
            report_outline=["Summary"],
        ),
    )


class FakeCredibilityScorer:
    def __init__(self, pairs: list[dict]) -> None:
        self.pairs = pairs

    def score_search_results(self, _results: list[SearchResult]) -> list[dict]:
        return self.pairs


class FakeSearchExecutor:
    def __init__(self, execution: SearchExecutionResult) -> None:
        self.execution = execution

    async def execute(self, _queries: list[SearchQuery], *, max_results_per_search: int) -> SearchExecutionResult:
        assert max_results_per_search == 3
        return self.execution


def _pair(result: SearchResult, score: int) -> dict:
    return {"result": result, "credibility": {"score": score, "level": "high", "source": result.title}}


def test_deterministic_searcher_returns_credibility_sorted_documents() -> None:
    first = _result("https://one.example/path", title="One", content="full text")
    second = _result("https://two.example/path", title="Two", content=None)
    searcher = ResearchSearcher(
        llm=object(),
        credibility_scorer=FakeCredibilityScorer([_pair(second, 85), _pair(first, 75)]),
        search_config=SearchConfig(mode="deterministic_v2", max_results_per_search=3),
    )
    searcher.search_executor = FakeSearchExecutor(
        SearchExecutionResult(
            search_results=[first, second],
            completed=False,
            partial=True,
            stats=SearchExecutionStats(search_calls=1, extract_calls=1, elapsed_seconds=0.25),
        )
    )

    patch = asyncio.run(searcher.search(_state()))

    assert [document.title for document in patch["documents"]] == ["Two", "One"]
    assert patch["documents"][0].status == "content_unavailable"
    assert "search_results" not in patch


def test_deterministic_searcher_keeps_empty_result_error_behavior() -> None:
    searcher = ResearchSearcher(
        llm=object(),
        credibility_scorer=FakeCredibilityScorer([]),
        search_config=SearchConfig(mode="deterministic_v2"),
    )
    searcher.search_executor = FakeSearchExecutor(
        SearchExecutionResult(search_results=[], error="fake search failure", completed=False)
    )

    assert asyncio.run(searcher.search(_state())) == {
        "error": "搜索失败：fake search failure",
        "iteration": 1,
        "current_stage": "failed",
        "status": "failed",
    }


def test_duplicate_document_keeps_first_metadata_and_fills_content() -> None:
    first = _result("https://example.com/source", title="First", content=None)
    duplicate = _result("https://example.com/source/#fragment", title="Later", content="later body")
    searcher = ResearchSearcher(
        llm=object(),
        credibility_scorer=FakeCredibilityScorer([_pair(first, 95), _pair(duplicate, 80)]),
        search_config=SearchConfig(mode="deterministic_v2"),
    )
    searcher.search_executor = FakeSearchExecutor(
        SearchExecutionResult(search_results=[first, duplicate], stats=SearchExecutionStats())
    )

    patch = asyncio.run(searcher.search(_state()))

    assert len(patch["documents"]) == 1
    assert patch["documents"][0].title == "First"
    assert patch["documents"][0].content == "later body"
