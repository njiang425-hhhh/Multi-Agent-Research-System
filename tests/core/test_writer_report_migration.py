"""Canonical Writer and stable citation-number regression coverage."""

import asyncio
from types import SimpleNamespace

from langchain_core.runnables import RunnableLambda
from src.agents import ReportWriter
from src.state import Document, Finding, ResearchPlan, ResearchState, ReportSection, SearchQuery


FIRST_URL = "https://example.com/first"
SECOND_URL = "https://example.com/second"


def _state() -> ResearchState:
    documents = [
        Document(document_id="doc-1", title="First", uri=FIRST_URL, snippet="first", credibility={"level": "high"}),
        Document(document_id="doc-2", title="Second", uri=SECOND_URL, snippet="second"),
    ]
    return ResearchState(
        research_topic="topic", query="topic",
        research_plan=ResearchPlan(topic="topic", objectives=["test"], search_queries=[SearchQuery(query="topic", purpose="test")], report_outline=["Summary", "Details"]),
        documents=documents,
        findings=[Finding(finding_id="finding-1", statement="Source-linked finding", source_document_ids=["doc-1"])],
        key_findings=["stale legacy finding"],
    )


def test_writer_uses_only_canonical_inputs_and_returns_only_report(monkeypatch) -> None:
    writer = ReportWriter(llm=RunnableLambda(lambda _input: "unused"), max_retries=1)
    received = []

    async def fake_section(_topic, section_title, findings, documents, **_kwargs):
        received.append((findings, documents))
        return ReportSection(title=section_title, content=(f"{section_title} content [1]. " * 30), sources=[FIRST_URL]), [], None

    monkeypatch.setattr(writer, "_write_section", fake_section)
    patch = asyncio.run(writer.write_report(_state()))

    assert len(received) == 2
    assert received[0][0][0].statement == "Source-linked finding"
    assert received[0][1][0].document_id == "doc-1"
    assert "final_report" not in patch and "report_sections" not in patch
    assert patch["report"].citations == [FIRST_URL, SECOND_URL]
    assert patch["report"].sections[0].sources == [FIRST_URL]
    assert "[1]" in patch["report"].content


def test_writer_drops_invalid_citation_numbers_and_keeps_section_sources_mapped(monkeypatch) -> None:
    async def fake_execute(*_args, **_kwargs):
        return SimpleNamespace(value="Grounded [2], invalid [3], and also [1]. " * 3, call_details=[], context=None)

    monkeypatch.setattr("src.agents.writer.execute_llm_operation", fake_execute)
    writer = ReportWriter(llm=RunnableLambda(lambda _input: "unused"), max_retries=1)
    state = _state()
    section, _details, _context = asyncio.run(writer._write_section("topic", "Summary", state.findings, state.documents))

    assert "[3]" not in section.content
    assert section.sources == [SECOND_URL, FIRST_URL]
