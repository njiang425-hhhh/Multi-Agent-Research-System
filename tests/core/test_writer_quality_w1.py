"""Writer Quality Phase W1 contracts: scope, carry-forward context, and formatting."""

import asyncio
import re
from types import SimpleNamespace

from langchain_core.runnables import RunnableLambda

from src.agents import ReportWriter
from src.agents.writer import _section_scopes
from src.state import Document, Finding, ResearchPlan, ReportSection, ResearchState, SearchQuery


DOCUMENTS = [
    Document(document_id="doc-impact", title="Impact source", uri="https://example.com/impact"),
    Document(document_id="doc-policy", title="Policy source", uri="https://example.com/policy"),
]


def _state() -> ResearchState:
    return ResearchState(
        query="AI policy and labour impact",
        research_plan=ResearchPlan(
            topic="AI policy and labour impact",
            objectives=["assess labour market impact", "explain policy response"],
            search_queries=[SearchQuery(query="AI", purpose="research")],
            report_outline=["Labour market impact", "Policy response"],
        ),
        documents=DOCUMENTS,
        findings=[
            Finding(
                finding_id="finding-impact",
                statement="AI has a measurable labour market impact.",
                source_document_ids=["doc-impact"],
            ),
            Finding(
                finding_id="finding-policy",
                statement="Policy response requires worker safeguards.",
                source_document_ids=["doc-policy"],
            ),
        ],
    )


def test_section_finding_assignment_is_unique_and_stable() -> None:
    state = _state()
    scopes = _section_scopes(state.research_plan, state.findings)

    assigned_ids = [finding.finding_id for scope in scopes for finding in scope.primary_findings]

    assert assigned_ids.count("finding-impact") == 1
    assert assigned_ids.count("finding-policy") == 1
    assert [finding.finding_id for finding in scopes[0].primary_findings] == ["finding-impact"]
    assert [finding.finding_id for finding in scopes[1].primary_findings] == ["finding-policy"]
    assert [scope.identifier for scope in scopes] == ["section:1", "section:2"]


def test_writer_section_scope_and_used_findings_are_carried_forward(monkeypatch) -> None:
    writer = ReportWriter(llm=RunnableLambda(lambda _input: "unused"), max_retries=1)
    received = []

    async def fake_section(_topic, title, findings, documents, **kwargs):
        received.append(
            {
                "title": title,
                "finding_ids": [finding.finding_id for finding in findings],
                "document_ids": [document.document_id for document in documents],
                "used_finding_ids": kwargs["used_finding_ids"],
                "previous_section_summary": kwargs["previous_section_summary"],
            }
        )
        return ReportSection(title=title, content=(f"{title} body [1]. " * 30)), [], None

    monkeypatch.setattr(writer, "_write_section", fake_section)
    asyncio.run(writer.write_report(_state()))

    assert received[0]["finding_ids"] == ["finding-impact"]
    assert received[0]["document_ids"] == ["doc-impact"]
    assert received[0]["used_finding_ids"] == []
    assert received[1]["finding_ids"] == ["finding-policy"]
    assert received[1]["document_ids"] == ["doc-policy"]
    assert received[1]["used_finding_ids"] == ["finding-impact"]
    assert received[1]["previous_section_summary"].startswith("Labour market impact body")


def test_deterministic_headings_and_scoped_citations_preserve_global_mapping(monkeypatch) -> None:
    writer = ReportWriter(llm=RunnableLambda(lambda _input: "unused"), max_retries=1)

    async def fake_execute(*_args, **_kwargs):
        return SimpleNamespace(value="# Model heading\n3.1 Wrong number\nScoped evidence [2]. " * 3, call_details=[], context=None)

    monkeypatch.setattr("src.agents.writer.execute_llm_operation", fake_execute)
    section, _details, _context = asyncio.run(
        writer._write_section(
            "topic",
            "Policy response",
            [_state().findings[1]],
            [DOCUMENTS[1]],
            citation_documents=DOCUMENTS,
        )
    )
    report = writer._compile_report(
        "topic",
        _state().research_plan,
        [
            ReportSection(title="Executive Summary", content="should not render"),
            ReportSection(title="Policy response", content=section.content),
        ],
        DOCUMENTS,
    )

    assert section.sources == ["https://example.com/policy"]
    assert "[2]" in section.content
    assert not re.search(r"(?m)^\s*(?:#|\d+\.\d+)", section.content)
    assert report.count("## 执行摘要") == 1
    assert report.count("## 研究目标") == 1
    assert report.count("## 参考文献") == 1
    assert "## 1. Policy response" in report
    assert "## Executive Summary" not in report
