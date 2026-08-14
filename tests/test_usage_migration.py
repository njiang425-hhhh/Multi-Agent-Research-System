"""Fake-only tests for explicit legacy tracking -> V1 usage double writes."""

import asyncio
import json

from langchain_core.runnables import RunnableLambda

from src import agents as agents_module
from src.agents import (
    ReportWriter,
    ResearchPlanner,
    ResearchSearcher,
    ResearchSynthesizer,
    _usage_from_legacy_totals,
)
from src.evidence.config import EvidenceRuntimeConfig
from src.evidence.sidecar import EvidenceSidecarResult
from src.search.config import SearchConfig
from src.search.models import SearchExecutionResult, SearchExecutionStats
from src.state import (
    ReportSection,
    ResearchPlan,
    ResearchState,
    SearchQuery,
    SearchResult,
    UsageMetrics,
)


def _plan() -> ResearchPlan:
    return ResearchPlan(
        topic="legacy topic",
        objectives=["Test usage migration"],
        search_queries=[SearchQuery(query="legacy topic", purpose="fake research")],
        report_outline=["Summary"],
    )


def _search_result() -> SearchResult:
    return SearchResult(
        query="legacy topic",
        title="Fake source",
        url="https://example.com/source",
        snippet="Fake source snippet",
        content="Fake source content",
    )


def _state(**overrides: object) -> ResearchState:
    values: dict[str, object] = {
        "research_topic": "legacy topic",
        "plan": _plan(),
        "search_results": [_search_result()],
        "key_findings": ["Legacy finding"],
        "llm_calls": 3,
        "total_input_tokens": 30,
        "total_output_tokens": 15,
        "llm_call_details": [{"agent": "Prior"}],
        "usage": UsageMetrics(
            tool_calls=4,
            latency_seconds=1.5,
            estimated_cost=0.25,
        ),
    }
    values.update(overrides)
    return ResearchState(**values)


def _assert_usage_matches_legacy(patch: dict, state: ResearchState) -> None:
    usage = patch["usage"]
    assert usage.llm_calls == patch["llm_calls"]
    assert usage.input_tokens == patch["total_input_tokens"]
    assert usage.output_tokens == patch["total_output_tokens"]
    assert usage.total_tokens == usage.input_tokens + usage.output_tokens
    assert usage.tool_calls == state.usage.tool_calls
    assert usage.latency_seconds == state.usage.latency_seconds
    assert usage.estimated_cost == state.usage.estimated_cost


def test_usage_projection_mirrors_legacy_totals_and_preserves_v1_only_fields() -> None:
    state = _state()

    usage = _usage_from_legacy_totals(
        state,
        llm_calls=8,
        total_input_tokens=80,
        total_output_tokens=21,
    )

    assert usage.model_dump() == {
        "llm_calls": 8,
        "tool_calls": 4,
        "input_tokens": 80,
        "output_tokens": 21,
        "total_tokens": 101,
        "latency_seconds": 1.5,
        "estimated_cost": 0.25,
    }


def test_planner_double_writes_usage_for_a_legacy_only_checkpoint() -> None:
    state = ResearchState.model_validate(
        {
            "research_topic": "legacy topic",
            "llm_calls": 3,
            "total_input_tokens": 30,
            "total_output_tokens": 15,
        }
    )
    response = json.dumps(
        {
            "topic": "legacy topic",
            "objectives": ["Test usage migration"],
            "search_queries": [{"query": "legacy topic", "purpose": "fake research"}],
            "report_outline": ["Summary"],
        }
    )

    patch = asyncio.run(
        ResearchPlanner(llm=RunnableLambda(lambda _prompt: response), max_retries=1).plan(state)
    )

    _assert_usage_matches_legacy(patch, state)
    assert patch["usage"].tool_calls == 0
    assert patch["usage"].latency_seconds == 0.0
    assert patch["usage"].estimated_cost is None


class _FakeCredibilityScorer:
    def score_search_results(self, results: list[SearchResult]) -> list[dict]:
        return [
            {"result": result, "credibility": {"score": 90, "level": "high"}}
            for result in results
        ]


class _FakeLegacySearchAgent:
    async def ainvoke(self, _input: dict, config: dict) -> dict:
        assert config["recursion_limit"] == agents_module.SEARCHER_AGENT_RECURSION_LIMIT
        return {
            "messages": [
                type(
                    "Message",
                    (),
                    {
                        "name": "web_search",
                        "content": json.dumps([_search_result().model_dump()]),
                    },
                )()
            ]
        }


class _FakeSearchExecutor:
    async def execute(
        self,
        _queries: list[SearchQuery],
        *,
        max_results_per_search: int,
    ) -> SearchExecutionResult:
        assert max_results_per_search == 3
        return SearchExecutionResult(
            search_results=[_search_result()],
            stats=SearchExecutionStats(search_calls=1, extract_calls=1, elapsed_seconds=0.2),
        )


def test_both_searcher_success_paths_double_write_usage(monkeypatch) -> None:
    legacy_state = _state()
    monkeypatch.setattr(
        agents_module,
        "create_agent",
        lambda *_args, **_kwargs: _FakeLegacySearchAgent(),
    )
    legacy_patch = asyncio.run(
        ResearchSearcher(
            llm=object(),
            credibility_scorer=_FakeCredibilityScorer(),
            search_config=SearchConfig(mode="legacy_agent"),
        ).search(legacy_state)
    )

    _assert_usage_matches_legacy(legacy_patch, legacy_state)
    assert legacy_patch["llm_calls"] == legacy_state.llm_calls + 1

    deterministic_state = _state()
    deterministic_searcher = ResearchSearcher(
        llm=object(),
        credibility_scorer=_FakeCredibilityScorer(),
        search_config=SearchConfig(mode="deterministic_v2", max_results_per_search=3),
    )
    deterministic_searcher.search_executor = _FakeSearchExecutor()
    deterministic_patch = asyncio.run(deterministic_searcher.search(deterministic_state))

    _assert_usage_matches_legacy(deterministic_patch, deterministic_state)
    assert deterministic_patch["llm_calls"] == deterministic_state.llm_calls
    assert len(deterministic_patch["llm_call_details"]) == len(deterministic_state.llm_call_details) + 1


class _FakeSynthesisAgent:
    async def ainvoke(self, _input: dict) -> dict:
        return {"messages": [type("Message", (), {"content": '["Synthesized finding"]'})()]}


class _FakeSidecar:
    async def run(self, **_kwargs: object) -> EvidenceSidecarResult:
        return EvidenceSidecarResult(
            llm_calls_delta=2,
            input_tokens_delta=20,
            output_tokens_delta=8,
            llm_call_details=[
                {"agent": "EvidenceAnalyzer", "input_tokens": 10, "output_tokens": 4},
                {"agent": "EvidenceAnalyzer", "input_tokens": 10, "output_tokens": 4},
            ],
        )


def test_synthesizer_rebuilds_usage_after_evidence_delta(monkeypatch) -> None:
    state = _state()
    monkeypatch.setattr(
        agents_module,
        "create_agent",
        lambda *_args, **_kwargs: _FakeSynthesisAgent(),
    )
    synthesizer = ResearchSynthesizer(
        llm=object(),
        max_retries=1,
        evidence_config=EvidenceRuntimeConfig(enabled=True),
        sidecar_factory=_FakeSidecar,
    )

    patch = asyncio.run(synthesizer.synthesize(state))

    _assert_usage_matches_legacy(patch, state)
    assert patch["llm_calls"] == state.llm_calls + 3
    assert patch["total_input_tokens"] >= state.total_input_tokens + 20
    assert patch["total_output_tokens"] >= state.total_output_tokens + 8


def test_writer_double_writes_usage_and_failure_keeps_existing_patch(monkeypatch) -> None:
    state = _state()
    writer = ReportWriter(llm=object(), max_retries=1)

    async def fake_write_section(*_args, **_kwargs) -> tuple[ReportSection, dict]:
        return (
            ReportSection(title="Summary", content="Writer content [1]. " * 40),
            {"input_tokens": 9, "output_tokens": 6},
        )

    monkeypatch.setattr(writer, "_write_section", fake_write_section)
    patch = asyncio.run(writer.write_report(state))

    _assert_usage_matches_legacy(patch, state)
    assert patch["llm_calls"] == state.llm_calls + 1

    failure_patch = asyncio.run(ReportWriter(llm=object(), max_retries=1).write_report(ResearchState(research_topic="topic")))
    assert failure_patch == {"error": "报告生成所需的数据不足"}
    assert "usage" not in failure_patch
