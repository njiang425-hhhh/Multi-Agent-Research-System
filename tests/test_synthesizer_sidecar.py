"""Evidence is optional enrichment and cannot replace Writer inputs."""

import asyncio
from dataclasses import dataclass

from src import agents
from src.agents import ResearchSynthesizer
from src.evidence.config import EvidenceRuntimeConfig
from src.evidence.contracts import Evidence
from src.evidence.sidecar import EvidenceSidecarResult
from src.state import Document, EvidenceDiagnostics, Finding, ResearchPlan, ResearchState, SearchQuery


@dataclass
class _Message:
    content: str


class _Agent:
    async def ainvoke(self, _input):
        return {"messages": [_Message('[{"claim": "Canonical claim", "source_numbers": [1]}]')]}


class _Sidecar:
    def __init__(self):
        self.calls = []

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        return EvidenceSidecarResult(
            findings=[Finding(finding_id="evidence", statement="Different evidence claim", evidence_refs=["e-1"], status="supported")],
            evidence=[Evidence(evidence_id="e-1", document_id="doc-1", source_url="https://example.com/source", claim="Different evidence claim", source_quote="quote", text_source="content", relation="supports")],
            diagnostics=EvidenceDiagnostics(status="completed", source="p2_documents", analyzer_completed=True, aggregation_attempted=True, aggregation_completed=True),
        )


def _state() -> ResearchState:
    return ResearchState(
        research_topic="topic", query="topic",
        research_plan=ResearchPlan(topic="topic", objectives=["test"], search_queries=[SearchQuery(query="topic", purpose="test")], report_outline=["Summary"]),
        documents=[Document(document_id="doc-1", title="Source", uri="https://example.com/source", snippet="snippet")],
    )


def test_enabled_sidecar_enriches_without_replacing_canonical_findings(monkeypatch) -> None:
    monkeypatch.setattr(agents, "create_agent", lambda *_args, **_kwargs: _Agent())
    sidecar = _Sidecar()
    patch = asyncio.run(ResearchSynthesizer(llm=object(), max_retries=1, evidence_config=EvidenceRuntimeConfig(enabled=True), sidecar_factory=lambda: sidecar).synthesize(_state()))

    assert [finding.statement for finding in patch["findings"]] == ["Canonical claim"]
    assert patch["findings"][0].source_document_ids == ["doc-1"]
    assert patch["evidence"][0].evidence_id == "e-1"
    assert "search_results" not in sidecar.calls[0]
    assert sidecar.calls[0]["documents"] == _state().documents
