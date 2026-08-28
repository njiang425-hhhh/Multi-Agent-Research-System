"""Fake-only P4.3 coverage for Agent LLM execution and Writer profiling."""

import asyncio
import json
from time import perf_counter

import pytest
from langchain_core.runnables import RunnableLambda

from src import agents
from src.agents import ReportWriter, ResearchPlanner, ResearchSynthesizer
from src.exceptions import LLMError
from src.runtime_control import RunPolicy, create_execution_context
from src.agent_trace import AgentTraceEvent
from src.state import Document, Finding, ReportSection, ResearchPlan, ResearchState, SearchQuery, SearchResult
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


def _search_result(title: str, url: str) -> SearchResult:
    return SearchResult(
        query="topic",
        title=title,
        url=url,
        snippet=f"Snippet for {title}",
        content=f"Content for {title}",
    )


def _writer_state(*, sections: list[str], context=None) -> ResearchState:
    return ResearchState(
        research_topic="topic",
        research_plan=ResearchPlan(
            topic="topic",
            objectives=["objective"],
            search_queries=[SearchQuery(query="topic", purpose="fake")],
            report_outline=sections,
        ),
        documents=[Document(document_id="doc-1", title="Fake source", uri="https://example.com/source", snippet="Fake snippet", content="Fake content")],
        findings=[Finding(finding_id="finding-1", statement="A sufficiently useful fake finding.", source_document_ids=["doc-1"])],
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


def test_writer_bounded_sections_respect_concurrency_and_assemble_by_outline(monkeypatch) -> None:
    state = _writer_state(sections=["One", "Two", "Three"])
    writer = ReportWriter(
        llm=object(),
        max_retries=1,
        section_execution_mode="bounded",
        section_concurrency=2,
    )
    active = 0
    max_active = 0
    completed: list[str] = []
    delays = {"One": 0.03, "Two": 0.01, "Three": 0.02}

    async def fake_write_section(
        _topic: str,
        section_title: str,
        _findings: list[Finding],
        _documents: list[Document],
        **_kwargs,
    ) -> tuple[ReportSection, dict]:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        try:
            await asyncio.sleep(delays[section_title])
            completed.append(section_title)
            return (
                ReportSection(
                    title=section_title,
                    content=(f"{section_title} bounded writer content [1]. " * 30),
                    sources=[f"https://example.com/{section_title.lower()}"],
                ),
                {
                    "agent": "ReportWriter",
                    "operation": f"write_section_{section_title}",
                    "input_tokens": 3,
                    "output_tokens": 2,
                    "duration": delays[section_title],
                },
            )
        finally:
            active -= 1

    monkeypatch.setattr(writer, "_write_section", fake_write_section)
    patch = asyncio.run(writer.write_report(state))

    assert max_active == 2
    assert completed[:2] == ["Two", "One"]
    assert [section.title for section in patch["report"].sections] == ["One", "Two", "Three"]
    assert patch["report"].content.index("## One") < patch["report"].content.index("## Two")
    assert patch["report"].content.index("## Two") < patch["report"].content.index("## Three")
    assert [detail["section_index"] for detail in patch["llm_call_details"]] == [0, 1, 2]
    assert {detail["writer_execution_mode"] for detail in patch["llm_call_details"]} == {"bounded"}


def test_writer_bounded_shared_budget_fails_all_or_nothing_without_lost_update() -> None:
    async def slow_writer(_: object) -> str:
        await asyncio.sleep(0.02)
        return "Writer content with citation [1]. " * 30

    state = _writer_state(sections=["One", "Two"], context=_context(budget=1))
    writer = ReportWriter(
        llm=RunnableLambda(slow_writer),
        max_retries=1,
        section_execution_mode="bounded",
        section_concurrency=2,
    )

    patch = asyncio.run(writer.write_report(state))

    assert patch["error"] == "报告撰写失败：Run operation budget exhausted"
    assert patch["llm_calls"] == 1
    assert len(patch["llm_call_details"]) == 1
    assert patch["execution_context"].operation_calls == 1
    assert patch["execution_context"].operation_stop_reason == "budget_exhausted"
    assert "report" not in patch


def test_writer_bounded_cancel_propagates_without_failure_patch(monkeypatch) -> None:
    state = _writer_state(sections=["One", "Two"])
    writer = ReportWriter(
        llm=object(),
        max_retries=1,
        section_execution_mode="bounded",
        section_concurrency=2,
    )

    async def blocking_section(*_args, **_kwargs):
        await asyncio.sleep(10)

    monkeypatch.setattr(writer, "_write_section", blocking_section)

    async def exercise() -> None:
        task = asyncio.create_task(writer.write_report(state))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())


def test_writer_section_citations_preserve_first_seen_order() -> None:
    async def cited_writer(_: object) -> str:
        return "Citation order [2], then [1], then duplicate [2]. " * 10

    writer = ReportWriter(llm=RunnableLambda(cited_writer), max_retries=1)
    section, _details, _context = asyncio.run(
        writer._write_section(
            "topic",
            "Summary",
            [Finding(finding_id="finding", statement="Finding", source_document_ids=["doc-1"])],
            [
                Document(document_id="doc-1", title="First", uri="https://example.com/first"),
                Document(document_id="doc-2", title="Second", uri="https://example.com/second"),
            ],
        )
    )

    assert section.sources == ["https://example.com/second", "https://example.com/first"]


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
