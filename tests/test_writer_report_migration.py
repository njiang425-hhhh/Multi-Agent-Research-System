"""Fake-only tests for the explicit legacy Writer -> V1 Report projection."""

import asyncio

from src.agents import ReportWriter
from src.state import Report, ReportSection, ResearchPlan, ResearchState, SearchQuery, SearchResult


FIRST_URL = "https://example.com/first"
SECOND_URL = "https://example.com/second"
EXTRA_URL = "https://example.com/extra"
FOURTH_URL = "https://example.com/fourth"


def _plan() -> ResearchPlan:
    return ResearchPlan(
        topic="legacy topic",
        objectives=["Preserve legacy report output"],
        search_queries=[SearchQuery(query="legacy topic", purpose="fake research")],
        report_outline=["Summary", "Details"],
    )


def _search_result(url: str, title: str) -> SearchResult:
    return SearchResult(
        query="legacy topic",
        title=title,
        url=url,
        snippet=f"Snippet for {title}",
        content=f"Content for {title}",
    )


def _legacy_only_state() -> ResearchState:
    return ResearchState.model_validate(
        {
            "research_topic": "legacy topic",
            "plan": _plan().model_dump(),
            "key_findings": ["Legacy finding"],
            "search_results": [
                _search_result(FIRST_URL, "First").model_dump(),
                _search_result(SECOND_URL, "Second").model_dump(),
                _search_result(FIRST_URL, "Duplicate first").model_dump(),
            ],
        }
    )


def test_writer_double_writes_a_v1_report_from_legacy_only_state(monkeypatch) -> None:
    state = _legacy_only_state()
    writer = ReportWriter(llm=object(), max_retries=1)
    received: list[tuple[str, str, list[str]]] = []

    async def fake_write_section(
        topic: str,
        section_title: str,
        findings: list[str],
        _search_results: list[SearchResult],
    ) -> tuple[ReportSection, None]:
        received.append((topic, section_title, findings))
        sources = {
            "Summary": [SECOND_URL, EXTRA_URL, FIRST_URL],
            "Details": [EXTRA_URL, FOURTH_URL],
        }[section_title]
        return ReportSection(
            title=section_title,
            content=(f"{section_title} legacy content [1]. " * 30),
            sources=sources,
        ), None

    monkeypatch.setattr(writer, "_write_section", fake_write_section)

    patch = asyncio.run(writer.write_report(state))

    assert state.report is None
    assert received == [
        ("legacy topic", "Summary", ["Legacy finding"]),
        ("legacy topic", "Details", ["Legacy finding"]),
    ]
    assert patch["report_sections"]
    assert patch["final_report"]
    assert patch["report"].sections == patch["report_sections"]
    assert patch["report"].content == patch["final_report"]
    assert patch["report"].title == "legacy topic"
    assert patch["report"].citations == [FIRST_URL, SECOND_URL, EXTRA_URL, FOURTH_URL]
    assert patch["report"].report_id == ""
    assert patch["report"].version == 1
    assert patch["report"].status == "completed"
    assert patch["iteration"] == state.iteration + 1
    assert patch["current_stage"] == "complete"
    assert patch["status"] == "completed"


def test_writer_keeps_legacy_inputs_when_a_stale_v1_report_is_present(monkeypatch) -> None:
    state = _legacy_only_state()
    state.report = Report(title="stale V1 report", content="stale content")
    writer = ReportWriter(llm=object(), max_retries=1)
    received: list[tuple[str, list[str]]] = []

    async def fake_write_section(
        topic: str,
        section_title: str,
        findings: list[str],
        _search_results: list[SearchResult],
    ) -> tuple[ReportSection, None]:
        received.append((topic, findings))
        return ReportSection(
            title=section_title,
            content=("Fresh legacy writer content [1]. " * 30),
            sources=[FIRST_URL],
        ), None

    monkeypatch.setattr(writer, "_write_section", fake_write_section)

    patch = asyncio.run(writer.write_report(state))

    assert received == [
        ("legacy topic", ["Legacy finding"]),
        ("legacy topic", ["Legacy finding"]),
    ]
    assert patch["report"].title == "legacy topic"
    assert patch["report"].content == patch["final_report"]
    assert patch["report"].content != "stale content"


def test_writer_failure_paths_do_not_create_a_partial_v1_report(monkeypatch) -> None:
    insufficient = ResearchState(research_topic="legacy topic")
    insufficient_patch = asyncio.run(ReportWriter(llm=object(), max_retries=1).write_report(insufficient))

    assert insufficient_patch == {
        "error": "报告生成所需的数据不足",
        "current_stage": "failed",
        "status": "failed",
    }
    assert "report" not in insufficient_patch

    writer = ReportWriter(llm=object(), max_retries=1)

    async def fake_failed_section(*_args, **_kwargs) -> tuple[None, None]:
        return None, None

    monkeypatch.setattr(writer, "_write_section", fake_failed_section)
    failed_patch = asyncio.run(writer.write_report(_legacy_only_state()))

    assert failed_patch == {
        "error": "报告撰写失败：未生成报告章节",
        "iterations": 1,
        "iteration": 1,
        "current_stage": "failed",
        "status": "failed",
    }
    assert "report" not in failed_patch
