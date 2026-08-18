"""Fake-only P4.3 coverage for Agent LLM execution and Writer profiling."""

import asyncio
import json
from time import perf_counter

from langchain_core.runnables import RunnableLambda

from src import agents
from src.agents import ReportWriter, ResearchPlanner, ResearchSynthesizer
from src.exceptions import LLMError
from src.runtime_control import RunPolicy, create_execution_context
from src.agent_trace import AgentTraceEvent
from src.state import ResearchPlan, ResearchState, SearchQuery, SearchResult
from src.writer_profile import profile_writer_latency, profile_writer_trace


def _context(*, budget: int | None = None):
    return create_execution_context(
        run_id="p43-run",
        thread_id="p43-thread",
        policy=RunPolicy(max_operation_calls=budget),
    )


def _result() -> SearchResult:
    return SearchResult(
        query="topic",
        title="Fake source",
        url="https://example.com/source",
        snippet="Fake snippet",
        content="Fake content",
    )


def _writer_state(*, sections: list[str], context=None) -> ResearchState:
    return ResearchState(
        research_topic="topic",
        plan=ResearchPlan(
            topic="topic",
            objectives=["objective"],
            search_queries=[SearchQuery(query="topic", purpose="fake")],
            report_outline=sections,
        ),
        key_findings=["A sufficiently useful fake finding."],
        search_results=[_result()],
        execution_context=context,
    )


def test_planner_retries_through_shared_contract_and_counts_every_attempt() -> None:
    attempts = 0

    async def flaky_plan(_: object):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("fake transient provider failure")
        return json.dumps({
            "topic": "topic",
            "objectives": ["objective"],
            "search_queries": [{"query": "topic", "purpose": "fake"}],
            "report_outline": ["Summary"],
        })

    state = ResearchState(research_topic="topic", execution_context=_context(budget=3))
    patch = asyncio.run(ResearchPlanner(llm=RunnableLambda(flaky_plan), max_retries=2).plan(state))

    assert attempts == 2
    assert patch["llm_calls"] == 2
    assert [detail["success"] for detail in patch["llm_call_details"]] == [False, True]
    assert patch["usage"].llm_calls == 2
    assert patch["execution_context"].operation_calls == 2


def test_synthesizer_honors_typed_nonretryable_llm_failure(monkeypatch) -> None:
    calls = 0

    class FakeAgent:
        async def ainvoke(self, _: dict):
            nonlocal calls
            calls += 1
            raise LLMError("fake invalid credentials", provider="fake", is_retryable=False)

    monkeypatch.setattr(agents, "create_agent", lambda *_args, **_kwargs: FakeAgent())
    state = _writer_state(sections=["Summary"], context=_context(budget=3))

    patch = asyncio.run(ResearchSynthesizer(llm=object(), max_retries=3).synthesize(state))

    assert calls == 1
    assert patch["llm_calls"] == 1
    assert patch["llm_call_details"][0]["error_code"] == "llm_provider_error"
    assert patch["llm_call_details"][0].get("retryable") is not True


def test_writer_profile_confirms_sequential_section_llm_critical_path() -> None:
    async def slow_writer(_: object) -> str:
        await asyncio.sleep(0.02)
        return "Writer content with citation [1]. " * 30

    state = _writer_state(sections=["One", "Two", "Three"], context=_context(budget=5))
    writer = ReportWriter(llm=RunnableLambda(slow_writer), max_retries=1)
    started = perf_counter()
    patch = asyncio.run(writer.write_report(state))
    wall_seconds = perf_counter() - started
    profile = profile_writer_latency(patch["llm_call_details"], wall_seconds=wall_seconds)

    assert patch["llm_calls"] == 3
    assert profile.section_attempts == 3
    assert profile.completed_sections == 3
    assert profile.llm_seconds >= 0.05
    assert profile.serial_fraction is not None and profile.serial_fraction >= 0.75
    assert profile.recommends_concurrency is False


def test_writer_budget_exhaustion_preserves_completed_attempt_usage_without_partial_report() -> None:
    async def fake_writer(_: object) -> str:
        return "Writer content with citation [1]. " * 30

    state = _writer_state(sections=["One", "Two"], context=_context(budget=1))
    patch = asyncio.run(ReportWriter(llm=RunnableLambda(fake_writer), max_retries=1).write_report(state))

    assert patch["error"] == "报告撰写失败：Run operation budget exhausted"
    assert patch["llm_calls"] == 1
    assert len(patch["llm_call_details"]) == 1
    assert patch["execution_context"].operation_stop_reason == "budget_exhausted"
    assert "report" not in patch


def test_writer_trace_profile_uses_persisted_llm_event_durations() -> None:
    events = [
        AgentTraceEvent(
            node="write_report",
            agent="ReportWriter",
            operation="write_section_Summary",
            event_type="llm",
            duration_seconds=0.3,
        ),
        AgentTraceEvent(
            node="write_report",
            agent="ReportWriter",
            operation="write_section_Details",
            event_type="llm",
            duration_seconds=0.2,
            status="failed",
        ),
    ]

    profile = profile_writer_trace(events, wall_seconds=0.6)

    assert profile.section_attempts == 2
    assert profile.completed_sections == 1
    assert profile.failed_attempts == 1
    assert profile.llm_seconds == 0.5
