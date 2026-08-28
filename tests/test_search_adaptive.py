"""Fake-only P14 bounded adaptive search coverage."""

import asyncio

import pytest

from src import agents as agents_module
from src.agents import ResearchSearcher
from src.search.config import SearchConfig
from src.search.coverage import measure_search_coverage
from src.search.executor import SearchExecutor
from src.state import Document, ResearchPlan, ResearchState, SearchQuery, SearchResult
from src.runtime_control import RunPolicy, create_execution_context


class FakeSearchTool:
    def __init__(self, handler):
        self.handler = handler
        self.calls: list[str] = []

    async def __call__(self, query: str, max_results: int):
        self.calls.append(query)
        result = self.handler(query, max_results)
        if asyncio.iscoroutine(result):
            return await result
        return result


class FakeExtractTool:
    def __init__(self, handler):
        self.handler = handler
        self.calls: list[str] = []

    async def __call__(self, url: str):
        self.calls.append(url)
        result = self.handler(url)
        if asyncio.iscoroutine(result):
            return await result
        return result


class PassingCredibilityScorer:
    def score_search_results(self, results: list[SearchResult]) -> list[dict]:
        return [
            {"result": result, "credibility": {"score": 90, "level": "high"}}
            for result in results
        ]


def _payload(query: str, url: str) -> list[dict[str, str]]:
    return [{"query": query, "title": f"Title {url}", "url": url, "snippet": "snippet"}]


def _state(*queries: str, execution_context=None) -> ResearchState:
    return ResearchState(
        research_topic="adaptive topic",
        execution_context=execution_context,
        plan=ResearchPlan(
            topic="adaptive topic",
            objectives=["cover planned queries"],
            search_queries=[SearchQuery(query=query, purpose=f"purpose for {query}") for query in queries],
            report_outline=["Summary"],
        ),
    )


def _searcher(search_tool: FakeSearchTool, extract_tool: FakeExtractTool) -> ResearchSearcher:
    search_config = SearchConfig(
        mode="deterministic_v2",
        max_search_times=1,
        max_extract_times=1,
        max_results_per_search=2,
        total_timeout_seconds=90,
        search_retry_times=0,
        extract_retry_times=0,
    )
    searcher = ResearchSearcher(
        llm=object(),
        credibility_scorer=PassingCredibilityScorer(),
        search_config=search_config,
    )
    searcher.search_executor = SearchExecutor(
        search_config=search_config,
        search_tool=search_tool,
        extract_tool=extract_tool,
    )
    return searcher


@pytest.fixture(autouse=True)
def _enable_one_adaptive_round(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agents_module.config, "searcher_adaptive_enabled", True)
    monkeypatch.setattr(agents_module.config, "searcher_adaptive_max_rounds", 1)


def test_query_coverage_uses_plan_queries_and_projected_document_content() -> None:
    plan = _state("alpha", "beta").plan
    assert plan is not None

    coverage = measure_search_coverage(
        plan,
        [
            SearchResult(
                query="alpha",
                title="Alpha",
                url="https://example.com/alpha",
                snippet="",
            )
        ],
        [
            Document(
                document_id="beta",
                uri="https://example.com/beta",
                content="beta extracted text",
                metadata={"search_query": "beta"},
            )
        ],
    )

    assert coverage.result_query_coverage == 0.5
    assert coverage.extracted_query_coverage == 0.5
    assert [query.query for query in coverage.missing_result_queries] == ["beta"]
    assert [query.query for query in coverage.missing_extracted_queries] == ["alpha"]


def test_all_covered_does_not_run_supplementary_search() -> None:
    search_tool = FakeSearchTool(lambda query, _: _payload(query, "https://example.com/one"))
    extract_tool = FakeExtractTool(lambda _: "primary content")

    patch = asyncio.run(_searcher(search_tool, extract_tool).search(_state("alpha")))

    assert search_tool.calls == ["alpha"]
    assert patch["search_diagnostics"][-1]["outcome"] == "not_needed"
    assert patch["search_diagnostics"][-1]["rounds_attempted"] == 0


def test_missing_result_runs_exactly_one_supplementary_search() -> None:
    search_tool = FakeSearchTool(
        lambda query, _: _payload(query, f"https://example.com/{query}")
    )
    extract_tool = FakeExtractTool(lambda url: f"content for {url}")

    patch = asyncio.run(_searcher(search_tool, extract_tool).search(_state("alpha", "beta")))

    assert search_tool.calls == ["alpha", "beta"]
    diagnostic = patch["search_diagnostics"][-1]
    assert diagnostic["supplementary_query"] == "beta"
    assert diagnostic["rounds_attempted"] == 1
    assert diagnostic["supplementary_search_calls"] == 1
    assert diagnostic["supplementary_extract_calls"] == 1
    assert diagnostic["outcome"] == "completed"


def test_missing_extraction_runs_exactly_one_supplementary_search() -> None:
    urls = iter(["https://example.com/primary", "https://example.com/supplement"])
    contents = iter([None, "supplementary content"])
    search_tool = FakeSearchTool(lambda query, _: _payload(query, next(urls)))
    extract_tool = FakeExtractTool(lambda _: next(contents))

    patch = asyncio.run(_searcher(search_tool, extract_tool).search(_state("alpha")))

    assert search_tool.calls == ["alpha", "alpha"]
    assert [document.uri for document in patch["documents"]] == [
        "https://example.com/primary",
        "https://example.com/supplement",
    ]
    assert patch["search_diagnostics"][-1]["outcome"] == "completed"
    assert patch["search_diagnostics"][-1]["rounds_attempted"] == 1


def test_still_missing_after_supplement_does_not_start_a_second_round() -> None:
    search_tool = FakeSearchTool(
        lambda query, _: _payload(query, f"https://example.com/{query}")
    )
    extract_tool = FakeExtractTool(lambda url: f"content for {url}")

    patch = asyncio.run(_searcher(search_tool, extract_tool).search(_state("alpha", "beta", "gamma")))

    assert search_tool.calls == ["alpha", "beta"]
    diagnostic = patch["search_diagnostics"][-1]
    assert diagnostic["rounds_attempted"] == 1
    assert diagnostic["post_coverage"]["missing_result_queries"] == ["gamma"]


def test_primary_first_merge_only_upgrades_missing_content() -> None:
    primary = SearchResult(
        query="alpha",
        title="Primary title",
        url="https://example.com/source/#fragment",
        snippet="primary snippet",
        content=None,
    )
    duplicate = SearchResult(
        query="alpha",
        title="Supplement title",
        url="https://example.com/source",
        snippet="supplement snippet",
        content="supplement content",
    )
    new = SearchResult(
        query="beta",
        title="New title",
        url="https://example.com/new",
        snippet="new snippet",
        content="new content",
    )

    merged, appended, upgrades = ResearchSearcher._merge_primary_search_results(
        [primary], [duplicate, new]
    )

    assert [result.url for result in merged] == [primary.url, new.url]
    assert merged[0].title == "Primary title"
    assert merged[0].snippet == "primary snippet"
    assert merged[0].content == "supplement content"
    assert appended == [new]
    assert upgrades == 1


def test_duplicate_only_supplement_is_no_progress_without_error() -> None:
    search_tool = FakeSearchTool(
        lambda query, _: _payload(query, "https://example.com/primary")
    )
    extract_tool = FakeExtractTool(lambda url: f"content for {url}")

    patch = asyncio.run(_searcher(search_tool, extract_tool).search(_state("alpha", "beta")))

    assert search_tool.calls == ["alpha", "beta"]
    assert len(patch["documents"]) == 1
    assert extract_tool.calls == ["https://example.com/primary"]
    assert patch["error"] is None
    assert patch["search_diagnostics"][-1]["outcome"] == "no_progress"


def test_supplement_without_content_is_no_progress_without_error() -> None:
    search_tool = FakeSearchTool(
        lambda query, _: _payload(query, f"https://example.com/{query}")
    )
    contents = iter(["primary content", None])
    extract_tool = FakeExtractTool(lambda _: next(contents))

    patch = asyncio.run(_searcher(search_tool, extract_tool).search(_state("alpha", "beta")))

    diagnostic = patch["search_diagnostics"][-1]
    assert diagnostic["coverage_improved"] is True
    assert diagnostic["new_content_bearing_results"] == 0
    assert diagnostic["outcome"] == "no_progress"
    assert patch["error"] is None


def test_supplementary_failure_keeps_primary_output_and_no_top_level_error() -> None:
    def search(query: str, _: int):
        if query == "beta":
            raise RuntimeError("supplementary failure")
        return _payload(query, "https://example.com/primary")

    search_tool = FakeSearchTool(search)
    extract_tool = FakeExtractTool(lambda _: "primary content")

    patch = asyncio.run(_searcher(search_tool, extract_tool).search(_state("alpha", "beta")))

    assert search_tool.calls == ["alpha", "beta"]
    assert [document.uri for document in patch["documents"]] == ["https://example.com/primary"]
    assert patch["error"] is None
    assert patch["search_diagnostics"][-1]["outcome"] == "supplementary_failed"


def test_runtime_budget_exhaustion_skips_supplementary_search() -> None:
    search_tool = FakeSearchTool(
        lambda query, _: _payload(query, f"https://example.com/{query}")
    )
    extract_tool = FakeExtractTool(lambda url: f"content for {url}")
    context = create_execution_context(
        run_id="adaptive-budget",
        thread_id="adaptive-budget-thread",
        policy=RunPolicy(max_operation_calls=2),
    )

    patch = asyncio.run(
        _searcher(search_tool, extract_tool).search(_state("alpha", "beta", execution_context=context))
    )

    assert search_tool.calls == ["alpha"]
    assert patch["error"] is None
    assert patch["search_diagnostics"][-1]["outcome"] == "skipped_runtime_unavailable"
    assert patch["search_diagnostics"][-1]["skip_reason"] == "runtime_budget_exhausted"
    assert patch["execution_context"].operation_calls == 2
