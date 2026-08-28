"""Canonical Documents -> Findings regression coverage."""

import asyncio
from dataclasses import dataclass

from src import agents
from src.agents import ResearchSynthesizer
from src.evidence.adapters import scored_search_results_to_documents
from src.evidence.compat import LEGACY_PROJECTION_SUMMARY, key_findings_to_findings
from src.evidence.config import EvidenceRuntimeConfig
from src.state import ResearchPlan, ResearchState, SearchQuery, SearchResult


def _documents():
    results = [
        SearchResult(query="topic", title="One", url="https://example.com/one", snippet="one", content="one body"),
        SearchResult(query="topic", title="Two", url="https://example.com/two", snippet="two", content="two body"),
    ]
    return scored_search_results_to_documents((result, {"score": 90, "level": "high"}) for result in results)


def _state() -> ResearchState:
    return ResearchState(
        research_topic="topic",
        query="topic",
        research_plan=ResearchPlan(topic="topic", objectives=["test"], search_queries=[SearchQuery(query="topic", purpose="test")], report_outline=["Summary"]),
        documents=_documents(),
    )


@dataclass
class _Message:
    content: str


class _Agent:
    async def ainvoke(self, _input):
        return {"messages": [_Message('[{"claim": "First finding", "source_numbers": [2, 1, 9]}, {"claim": "Missing source", "source_numbers": [0]}]')]}


def test_legacy_projection_remains_deterministic_and_unverified() -> None:
    projected = key_findings_to_findings(["First", "Repeated", "Repeated"])
    assert [item.statement for item in projected] == ["First", "Repeated", "Repeated"]
    assert all(item.source_document_ids == [] for item in projected)
    assert all(item.reasoning_summary == LEGACY_PROJECTION_SUMMARY for item in projected)


def test_synthesizer_maps_source_numbers_to_ordered_document_ids(monkeypatch) -> None:
    monkeypatch.setattr(agents, "create_agent", lambda *_args, **_kwargs: _Agent())
    patch = asyncio.run(ResearchSynthesizer(llm=object(), max_retries=1, evidence_config=EvidenceRuntimeConfig(enabled=False)).synthesize(_state()))

    assert "key_findings" not in patch
    assert [item.statement for item in patch["findings"]] == ["First finding"]
    assert patch["findings"][0].source_document_ids == [
        _state().documents[1].document_id,
        _state().documents[0].document_id,
    ]
    assert patch["findings"][0].confidence is None
    assert patch["evidence_diagnostics"].status == "disabled"
