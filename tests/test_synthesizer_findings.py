"""P2.2a tests for deterministic legacy Finding compatibility projection."""

import asyncio
from dataclasses import dataclass

from src import agents
from src.agents import ReportWriter, ResearchSynthesizer
from src.evidence.compat import LEGACY_PROJECTION_SUMMARY, key_findings_to_findings
from src.state import ResearchPlan, ResearchState, ReportSection, SearchQuery, SearchResult


def _search_result() -> SearchResult:
    return SearchResult(
        query="research topic",
        title="Source",
        url="https://example.com/source",
        snippet="A source summary with enough text to form a fallback finding.",
        content="Source content.",
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
        search_results=[_search_result()],
        credibility_scores=[{"score": 90, "level": "high", "factors": ["fake"]}],
    )


@dataclass
class FakeMessage:
    content: str


class FakeSynthesisAgent:
    def __init__(self, output: str | Exception) -> None:
        self.output = output

    async def ainvoke(self, _input: dict) -> dict:
        if isinstance(self.output, Exception):
            raise self.output
        return {"messages": [FakeMessage(self.output)]}


def test_compat_projection_preserves_order_and_builds_unverified_findings() -> None:
    key_findings = ["First finding", "Repeated finding", "Repeated finding"]

    projected = key_findings_to_findings(key_findings)

    assert [finding.statement for finding in projected] == key_findings
    assert len({finding.finding_id for finding in projected}) == 3
    assert all(finding.finding_id.startswith("finding:legacy:") for finding in projected)
    assert all(finding.evidence_refs == [] for finding in projected)
    assert all(finding.contradictory_evidence_refs == [] for finding in projected)
    assert all(finding.confidence is None for finding in projected)
    assert all(finding.status == "unverified" for finding in projected)
    assert all(finding.reasoning_summary == LEGACY_PROJECTION_SUMMARY for finding in projected)


def test_compat_projection_is_stable_for_identical_ordered_input() -> None:
    key_findings = ["First finding", "Repeated finding", "Repeated finding"]

    first = key_findings_to_findings(key_findings)
    second = key_findings_to_findings(key_findings)

    assert [finding.finding_id for finding in first] == [finding.finding_id for finding in second]
    assert key_findings_to_findings([]) == []


def test_synthesizer_double_writes_findings_without_changing_legacy_patch_fields(monkeypatch) -> None:
    output = '["First synthesized finding", "Second synthesized finding"]'
    synthesizer = ResearchSynthesizer(llm=object(), max_retries=1)
    monkeypatch.setattr(
        agents,
        "create_agent",
        lambda *_args, **_kwargs: FakeSynthesisAgent(output),
    )
    state = _state()

    patch = asyncio.run(synthesizer.synthesize(state))

    assert patch["key_findings"] == ["First synthesized finding", "Second synthesized finding"]
    assert [finding.statement for finding in patch["findings"]] == patch["key_findings"]
    assert patch["current_stage"] == "reporting"
    assert patch["iterations"] == state.iterations + 1
    assert patch["iteration"] == state.iteration + 1
    assert patch["llm_calls"] == state.llm_calls + 1
    assert patch["total_input_tokens"] >= state.total_input_tokens
    assert patch["total_output_tokens"] >= state.total_output_tokens
    assert len(patch["llm_call_details"]) == len(state.llm_call_details) + 1
    assert "documents" not in patch


def test_synthesizer_error_patch_keeps_existing_behavior_without_findings(monkeypatch) -> None:
    synthesizer = ResearchSynthesizer(llm=object(), max_retries=1)
    monkeypatch.setattr(
        agents,
        "create_agent",
        lambda *_args, **_kwargs: FakeSynthesisAgent(RuntimeError("fake synthesis failure")),
    )

    patch = asyncio.run(synthesizer.synthesize(_state()))

    assert patch == {
        "error": "综合失败：fake synthesis failure",
        "iterations": 1,
        "iteration": 1,
        "current_stage": "failed",
        "status": "failed",
    }


def test_writer_continues_to_use_legacy_key_findings_not_structured_findings(monkeypatch) -> None:
    writer = ReportWriter(llm=object(), max_retries=1)
    state = _state()
    state.key_findings = ["Legacy finding for writer"]
    state.findings = key_findings_to_findings(["Different structured finding"])
    received: list[list[str]] = []

    async def fake_write_section(
        _topic: str,
        section_title: str,
        findings: list[str],
        _search_results: list[SearchResult],
    ) -> tuple[ReportSection, int]:
        received.append(findings)
        return ReportSection(title=section_title, content="Writer content. " * 50), 0

    monkeypatch.setattr(writer, "_write_section", fake_write_section)

    patch = asyncio.run(writer.write_report(state))

    assert received == [["Legacy finding for writer"]]
    assert patch["error"] if "error" in patch else None is None
    assert patch["final_report"]
