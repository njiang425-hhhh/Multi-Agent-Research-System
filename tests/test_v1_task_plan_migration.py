"""Fake-only coverage for the P2 task and plan field migration."""

import asyncio
import json

from langchain_core.runnables import RunnableLambda

from src import agents as agents_module
from src.agents import ResearchPlanner, ResearchSearcher
from src.evidence.config import EvidenceRuntimeConfig
from src.graph import _create_initial_state
from src.search.config import SearchConfig
from src.search.models import SearchExecutionResult, SearchExecutionStats
from src.state import ResearchPlan, ResearchState, SearchQuery, SearchResult
from src.state_compat import hydrate_canonical_state, legacy_projection_patch


def _plan(query: str) -> ResearchPlan:
    return ResearchPlan(
        topic=query,
        objectives=[f"research {query}"],
        search_queries=[SearchQuery(query=query, purpose="fake research")],
        report_outline=["Summary"],
    )


def test_graph_entry_explicitly_double_writes_task_fields() -> None:
    state = _create_initial_state("canonical research task")

    assert state.research_topic == "canonical research task"
    assert state.query == "canonical research task"


def test_planner_uses_canonical_query_and_returns_canonical_plan() -> None:
    observed_prompts: list[str] = []

    def fake_llm(prompt: object) -> str:
        observed_prompts.append(str(prompt))
        return json.dumps(
            {
                "topic": "canonical research task",
                "objectives": ["research it"],
                "search_queries": [{"query": "canonical research task", "purpose": "fake"}],
                "report_outline": ["Summary"],
            }
        )

    state = ResearchState(research_topic="legacy task", query="canonical research task")
    patch = asyncio.run(ResearchPlanner(llm=RunnableLambda(fake_llm), max_retries=1).plan(state))

    assert "canonical research task" in observed_prompts[0]
    assert "legacy task" not in observed_prompts[0]
    assert "plan" not in patch
    assert patch["research_plan"].search_queries[0].query == "canonical research task"
    assert patch["iteration"] == state.iteration + 1


class _FakeCredibilityScorer:
    def score_search_results(self, results: list[SearchResult]) -> list[dict]:
        return [
            {"result": result, "credibility": {"score": 90, "level": "high"}}
            for result in results
        ]


class _CapturingExecutor:
    def __init__(self) -> None:
        self.queries: list[SearchQuery] | None = None

    async def execute(
        self,
        queries: list[SearchQuery],
        *,
        max_results_per_search: int,
    ) -> SearchExecutionResult:
        assert max_results_per_search == 3
        self.queries = queries
        return SearchExecutionResult(
            search_results=[
                SearchResult(
                    query=queries[0].query,
                    title="Fake source",
                    url="https://example.com/source",
                    snippet="Fake source snippet",
                    content="Fake source content",
                )
            ],
            stats=SearchExecutionStats(),
        )


def test_searcher_prefers_v1_plan_without_mutating_legacy_plan() -> None:
    legacy_plan = _plan("legacy query")
    v1_plan = _plan("canonical query")
    state = ResearchState(
        research_topic="legacy task",
        query="canonical research task",
        plan=legacy_plan,
        research_plan=v1_plan,
    )
    executor = _CapturingExecutor()
    searcher = ResearchSearcher(
        llm=object(),
        credibility_scorer=_FakeCredibilityScorer(),
        search_config=SearchConfig(mode="deterministic_v2", max_results_per_search=3),
    )
    searcher.search_executor = executor

    patch = asyncio.run(searcher.search(state))

    assert executor.queries == v1_plan.search_queries
    assert v1_plan.search_queries[0].completed is True
    assert legacy_plan.search_queries[0].completed is False
    assert "search_results" not in patch
    assert patch["documents"][0].metadata["search_query"] == "canonical query"
    assert patch["iteration"] == state.iteration + 1


class _FakeSynthesisAgent:
    def __init__(self) -> None:
        self.input: dict | None = None

    async def ainvoke(self, input_value: dict) -> dict:
        self.input = input_value
        return {"messages": [type("Message", (), {"content": '[{"claim": "A canonical finding", "source_numbers": [1]}]'})()]}


def test_synthesizer_uses_canonical_query_and_hydrated_documents(monkeypatch) -> None:
    fake_agent = _FakeSynthesisAgent()
    monkeypatch.setattr(agents_module, "create_agent", lambda *_args, **_kwargs: fake_agent)
    state = ResearchState(
        research_topic="legacy task",
        query="canonical research task",
        plan=_plan("legacy query"),
        research_plan=_plan("canonical query"),
        search_results=[
            SearchResult(
                query="canonical query",
                title="Fake source",
                url="https://example.com/source",
                snippet="Fake source snippet",
                content="Fake source content",
            )
        ],
    )
    from src.agents import ResearchSynthesizer

    patch = asyncio.run(
        ResearchSynthesizer(
            llm=object(),
            max_retries=1,
            evidence_config=EvidenceRuntimeConfig(enabled=False),
        ).synthesize(hydrate_canonical_state(state))
    )

    message = fake_agent.input["messages"][0]["content"]
    assert "canonical research task" in message
    assert "legacy task" not in message
    assert patch["findings"][0].statement == "A canonical finding"
    assert patch["iteration"] == state.iteration + 1
