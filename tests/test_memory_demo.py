"""Fake-only P15 Memory Demo Activation coverage."""

from __future__ import annotations

import asyncio
import json

from langchain_core.runnables import RunnableLambda

from src import agents as agents_module
from src import runner as graph_module
from src.agents import ResearchPlanner, ResearchSearcher
from src.memory import ResearchMemoryStore, run_memory_demo
from src.memory.projection import project_research_memories
from src.search.config import SearchConfig
from src.search.models import SearchExecutionResult, SearchExecutionStats
from src.state import Finding, ResearchPlan, ResearchState, SearchQuery, SearchResult


def _completed_state() -> ResearchState:
    return ResearchState(
        research_topic="AI regulation",
        query="AI regulation",
        run_id="p15-run-a",
        findings=[Finding(finding_id="finding", statement="AI regulation uses risk tiers")],
        search_results=[
            SearchResult(
                query="AI regulation",
                title="EU source",
                url="https://eu.example/ai-act",
                snippet="risk tiers",
                content="source content",
            )
        ],
        status="completed",
        current_stage="complete",
        terminal_reason="completed",
    )


def _plan_response(_prompt) -> str:
    return json.dumps({
        "topic": "AI regulation",
        "objectives": ["Compare approaches"],
        "search_queries": [{"query": "AI regulation risk tiers", "purpose": "compare"}],
        "report_outline": ["Summary"],
    })


def _enable_memory(monkeypatch, store_path) -> None:
    monkeypatch.setattr(agents_module.config, "research_memory_enabled", True)
    monkeypatch.setattr(agents_module.config, "research_memory_store_path", str(store_path))
    monkeypatch.setattr(agents_module.config, "research_memory_retrieval_limit", 2)
    monkeypatch.setattr(agents_module.config, "research_memory_search_hint_limit", 2)


def test_completed_run_write_diagnostics_and_incomplete_skip(monkeypatch, tmp_path) -> None:
    store_path = tmp_path / "memory.db"
    _enable_memory(monkeypatch, store_path)
    monkeypatch.setattr(graph_module.config, "research_memory_enabled", True)
    monkeypatch.setattr(graph_module.config, "research_memory_store_path", str(store_path))

    graph_module._persist_completed_research_memory(_completed_state().model_dump())
    graph_module._persist_completed_research_memory(
        _completed_state().model_copy(update={"status": "failed", "terminal_reason": "agent_failed"}).model_dump()
    )

    assert ResearchMemoryStore(store_path).count() == 1


def test_enabled_planner_records_retrieval_score_terms_provenance_and_context(monkeypatch, tmp_path) -> None:
    store = ResearchMemoryStore(tmp_path / "memory.db")
    store.upsert_many(project_research_memories(_completed_state()))
    _enable_memory(monkeypatch, store.path)

    patch = asyncio.run(
        ResearchPlanner(
            llm=RunnableLambda(_plan_response),
            max_retries=1,
            memory_store=store,
        ).plan(ResearchState(research_topic="AI regulation risk tiers"))
    )

    diagnostics = patch["memory_diagnostics"]
    assert diagnostics["retrieved_count"] == 1
    assert diagnostics["retrieved_memory_ids"] == patch["memory_ids"]
    assert diagnostics["planner_context_memory_ids"] == patch["memory_ids"]
    assert diagnostics["retrieval"][0]["retrieval_score"] > 0
    assert diagnostics["retrieval"][0]["matched_terms"] == ["ai", "regulation", "risk", "tiers"]
    assert diagnostics["retrieval"][0]["provenance"]["run_id"] == "p15-run-a"


def test_unrelated_query_and_retrieval_limit_do_not_overmatch(monkeypatch, tmp_path) -> None:
    store = ResearchMemoryStore(tmp_path / "memory.db")
    records = project_research_memories(_completed_state())
    store.upsert_many(records)
    store.upsert_many(
        project_research_memories(
            _completed_state().model_copy(
                update={
                    "run_id": "p15-run-second",
                    "findings": [Finding(finding_id="two", statement="AI regulation enforcement")],
                }
            )
        )
    )
    _enable_memory(monkeypatch, store.path)
    monkeypatch.setattr(agents_module.config, "research_memory_retrieval_limit", 1)

    related = asyncio.run(
        ResearchPlanner(
            llm=RunnableLambda(_plan_response), max_retries=1, memory_store=store
        ).plan(ResearchState(research_topic="AI regulation enforcement risk tiers"))
    )
    unrelated = asyncio.run(
        ResearchPlanner(
            llm=RunnableLambda(_plan_response), max_retries=1, memory_store=store
        ).plan(ResearchState(research_topic="coastal flood adaptation"))
    )

    assert related["memory_diagnostics"]["retrieved_count"] == 1
    assert unrelated["memory_diagnostics"]["retrieved_count"] == 0


class _PassingScorer:
    def score_search_results(self, results: list[SearchResult]) -> list[dict]:
        return [{"result": result, "credibility": {"score": 90}} for result in results]


class _CapturingExecutor:
    def __init__(self) -> None:
        self.queries: list[SearchQuery] = []

    async def execute(self, queries, **_kwargs) -> SearchExecutionResult:
        self.queries = list(queries)
        return SearchExecutionResult(
            search_results=[
                SearchResult(
                    query=query.query,
                    title="source",
                    url=f"https://result.example/{index}",
                    snippet="snippet",
                    content="content",
                )
                for index, query in enumerate(self.queries, 1)
            ],
            stats=SearchExecutionStats(search_calls=len(self.queries), extract_calls=len(self.queries)),
        )


def test_enabled_searcher_records_site_hints_that_stay_inside_executor(monkeypatch, tmp_path) -> None:
    store = ResearchMemoryStore(tmp_path / "memory.db")
    store.upsert_many(project_research_memories(_completed_state()))
    _enable_memory(monkeypatch, store.path)
    plan = ResearchPlan(
        topic="AI regulation",
        objectives=["Compare"],
        search_queries=[SearchQuery(query="AI regulation risk tiers", purpose="primary")],
        report_outline=["Summary"],
    )
    state = ResearchState(research_topic="AI regulation", plan=plan)
    executor = _CapturingExecutor()
    searcher = ResearchSearcher(
        llm=object(),
        credibility_scorer=_PassingScorer(),
        search_config=SearchConfig(mode="deterministic_v2", max_results_per_search=2),
        memory_store=store,
    )
    searcher.search_executor = executor

    patch = asyncio.run(searcher.search(state))

    assert [query.query for query in executor.queries] == [
        "AI regulation risk tiers",
        "AI regulation site:eu.example",
    ]
    assert patch["memory_diagnostics"]["searcher_site_hints"] == [
        "AI regulation site:eu.example"
    ]
    assert patch["memory_diagnostics"]["retrieved_memory_ids"] == patch["memory_ids"]


def test_retrieval_failure_is_nonfatal_and_memory_off_does_not_inject(monkeypatch, tmp_path) -> None:
    class BrokenStore:
        def retrieve(self, *_args, **_kwargs):
            raise RuntimeError("fake retrieval failure")

    _enable_memory(monkeypatch, tmp_path / "memory.db")
    enabled_patch = asyncio.run(
        ResearchPlanner(
            llm=RunnableLambda(_plan_response), max_retries=1, memory_store=BrokenStore()
        ).plan(ResearchState(research_topic="AI regulation"))
    )

    monkeypatch.setattr(agents_module.config, "research_memory_enabled", False)
    off_store = ResearchMemoryStore(tmp_path / "off.db")
    off_store.upsert_many(project_research_memories(_completed_state()))
    off_patch = asyncio.run(
        ResearchPlanner(
            llm=RunnableLambda(_plan_response), max_retries=1, memory_store=off_store
        ).plan(ResearchState(research_topic="AI regulation risk tiers"))
    )

    assert enabled_patch.get("error") is None
    assert enabled_patch["memory_diagnostics"]["retrieval_outcome"] == "failed"
    assert enabled_patch["memory_ids"] == []
    assert off_patch["memory_ids"] == []
    assert "memory_diagnostics" not in off_patch


def test_memory_demo_reports_off_on_and_unrelated_observations() -> None:
    result = run_memory_demo()

    assert result["run_a"]["memory_write_count"] == 1
    assert result["memory_off"]["retrieved_memory_count"] == 0
    assert result["memory_on_run_b"]["retrieved_memory_count"] == 1
    assert result["memory_on_run_b"]["searcher_site_hints"] == [
        "AI regulation enforcement risk tiers site:eu.example"
    ]
    assert result["unrelated_run_c"]["retrieved_memory_count"] == 0
    assert result["comparison"]["planned_query_overlap"] == 1.0
    assert result["comparison"]["coverage_delta"] == {
        "result_query_coverage": 0.0,
        "extracted_query_coverage": 0.0,
    }
