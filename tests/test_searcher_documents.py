"""P2.1 tests for explicit SearchResult -> Document double writes."""

import asyncio
import json
from dataclasses import dataclass

from src import agents
from src.agents import ResearchSearcher
from src.search.config import SearchConfig
from src.search.models import SearchExecutionResult, SearchExecutionStats
from src.state import ResearchPlan, ResearchState, SearchQuery, SearchResult


def _result(url: str, *, title: str = "Source", content: str | None = "page text") -> SearchResult:
    return SearchResult(
        query="research topic",
        title=title,
        url=url,
        snippet=f"Snippet for {title}",
        content=content,
    )


def _state() -> ResearchState:
    return ResearchState(
        research_topic="research topic",
        plan=ResearchPlan(
            topic="research topic",
            objectives=["understand the topic"],
            search_queries=[SearchQuery(query="research topic", purpose="research")],
            report_outline=["Summary"],
        ),
    )


class FakeCredibilityScorer:
    def __init__(self, pairs: list[dict]) -> None:
        self.pairs = pairs
        self.calls: list[list[SearchResult]] = []

    def score_search_results(self, results: list[SearchResult]) -> list[dict]:
        self.calls.append(results)
        return self.pairs


@dataclass
class FakeMessage:
    name: str
    content: str


class FakeLegacyAgent:
    def __init__(self, results: list[SearchResult]) -> None:
        self.results = results

    async def ainvoke(self, _input: dict, config: dict) -> dict:
        assert config["recursion_limit"] == agents.SEARCHER_AGENT_RECURSION_LIMIT
        return {
            "messages": [
                FakeMessage(
                    name="web_search",
                    content=json.dumps([result.model_dump() for result in self.results]),
                )
            ]
        }


class FakeSearchExecutor:
    def __init__(self, execution: SearchExecutionResult) -> None:
        self.execution = execution

    async def execute(self, _queries: list[SearchQuery], *, max_results_per_search: int) -> SearchExecutionResult:
        assert max_results_per_search == 3
        return self.execution


def _pair(result: SearchResult, score: int) -> dict:
    return {
        "result": result,
        "credibility": {"score": score, "level": "high", "source": result.title},
    }


def test_legacy_searcher_double_writes_documents_with_bound_credibility_and_deduplicates_urls(
    monkeypatch,
) -> None:
    first = _result("HTTPS://EXAMPLE.COM/guide/#overview", title="First")
    duplicate = _result("https://example.com/guide", title="Duplicate")
    invalid = _result("not a URL", title="Invalid")
    scorer = FakeCredibilityScorer([_pair(first, 91), _pair(duplicate, 82), _pair(invalid, 70)])
    searcher = ResearchSearcher(
        llm=object(),
        credibility_scorer=scorer,
        search_config=SearchConfig(mode="legacy_agent"),
    )
    monkeypatch.setattr(agents, "create_agent", lambda *_args, **_kwargs: FakeLegacyAgent([first, duplicate, invalid]))

    patch = asyncio.run(searcher.search(_state()))

    assert patch["search_results"] == [first, duplicate, invalid]
    assert patch["credibility_scores"] == [
        {"score": 91, "level": "high", "source": "First"},
        {"score": 82, "level": "high", "source": "Duplicate"},
        {"score": 70, "level": "high", "source": "Invalid"},
    ]
    assert len(patch["documents"]) == 2
    assert patch["documents"][0].uri == "https://example.com/guide"
    assert patch["documents"][0].credibility["source"] == "First"
    assert patch["documents"][1].status == "invalid_source"
    assert patch["documents"][1].credibility["source"] == "Invalid"


def test_deterministic_searcher_double_writes_without_changing_partial_legacy_outputs() -> None:
    first = _result("https://one.example/path", title="One", content="full text")
    second = _result("https://two.example/path", title="Two", content=None)
    scorer = FakeCredibilityScorer([_pair(second, 85), _pair(first, 75)])
    searcher = ResearchSearcher(
        llm=object(),
        credibility_scorer=scorer,
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

    assert patch["search_results"] == [second, first]
    assert patch["credibility_scores"] == [
        {"score": 85, "level": "high", "source": "Two"},
        {"score": 75, "level": "high", "source": "One"},
    ]
    assert [document.title for document in patch["documents"]] == ["Two", "One"]
    assert patch["documents"][0].credibility["source"] == "Two"
    assert patch["documents"][0].status == "content_unavailable"
    assert patch["error"] is None
    assert patch["llm_call_details"][-1]["partial"] is True


def test_deterministic_searcher_keeps_existing_empty_result_error_behavior() -> None:
    searcher = ResearchSearcher(
        llm=object(),
        credibility_scorer=FakeCredibilityScorer([]),
        search_config=SearchConfig(mode="deterministic_v2"),
    )
    searcher.search_executor = FakeSearchExecutor(
        SearchExecutionResult(search_results=[], error="fake search failure", completed=False)
    )

    patch = asyncio.run(searcher.search(_state()))

    assert patch == {
        "search_results": [],
        "credibility_scores": [],
        "error": "搜索失败：fake search failure",
        "iterations": 1,
        "iteration": 1,
        "current_stage": "failed",
        "status": "failed",
    }


def test_deterministic_searcher_keeps_single_result_in_both_legacy_and_document_fields() -> None:
    only = _result("https://one.example/path", title="Only")
    searcher = ResearchSearcher(
        llm=object(),
        credibility_scorer=FakeCredibilityScorer([_pair(only, 88)]),
        search_config=SearchConfig(mode="deterministic_v2"),
    )
    searcher.search_executor = FakeSearchExecutor(
        SearchExecutionResult(search_results=[only], stats=SearchExecutionStats())
    )

    patch = asyncio.run(searcher.search(_state()))

    assert patch["search_results"] == [only]
    assert len(patch["documents"]) == 1
    assert patch["documents"][0].credibility["score"] == 88
