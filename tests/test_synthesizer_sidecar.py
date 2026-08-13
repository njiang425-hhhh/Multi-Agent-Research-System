"""Fake-only orchestration tests for the optional Synthesizer Evidence sidecar."""

import asyncio
from dataclasses import dataclass

from src import agents
from src.agents import ReportWriter, ResearchSynthesizer
from src.evidence.config import AnalyzerConfig, EvidenceRuntimeConfig
from src.evidence.contracts import DocumentAnalysis, Evidence
from src.evidence.sidecar import EvidenceSidecarResult
from src.state import Document, EvidenceDiagnostics, Finding, ResearchPlan, ResearchState, ReportSection, SearchQuery, SearchResult


@dataclass
class FakeMessage:
    content: str


class FakeSynthesisAgent:
    def __init__(self, output: str) -> None:
        self.output = output

    async def ainvoke(self, _input: dict) -> dict:
        return {"messages": [FakeMessage(self.output)]}


class FakeSidecar:
    def __init__(self, result: EvidenceSidecarResult | Exception) -> None:
        self.result = result
        self.calls: list[dict] = []

    async def run(self, **kwargs: object) -> EvidenceSidecarResult:
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _state(*, documents: list[Document] | None = None) -> ResearchState:
    source = SearchResult(
        query="topic",
        title="Source",
        url="https://example.com/source",
        snippet="Source snippet",
        content="Source text.",
    )
    return ResearchState(
        research_topic="topic",
        plan=ResearchPlan(
            topic="topic",
            objectives=["Test objective"],
            search_queries=[SearchQuery(query="topic", purpose="test")],
            report_outline=["Summary"],
        ),
        documents=documents or [],
        search_results=[source],
        credibility_scores=[{"score": 90, "level": "high"}],
        llm_calls=4,
        total_input_tokens=40,
        total_output_tokens=20,
        llm_call_details=[{"agent": "Prior"}],
    )


def _runtime(*, allow_partial: bool = True) -> EvidenceRuntimeConfig:
    return EvidenceRuntimeConfig(
        enabled=True,
        analyzer=AnalyzerConfig(allow_partial_results=allow_partial),
    )


def _evidence_result(
    *,
    findings: list[Finding] | None = None,
    evidence: list[Evidence] | None = None,
    diagnostics: EvidenceDiagnostics | None = None,
    documents: list[Document] | None = None,
    calls: int = 2,
) -> EvidenceSidecarResult:
    evidence = evidence if evidence is not None else [
        Evidence(
            evidence_id="evidence-1",
            document_id="doc-1",
            source_url="https://example.com/source",
            claim="Evidence-backed claim.",
            source_quote="Source text.",
            text_source="content",
            relation="supports",
        )
    ]
    findings = findings if findings is not None else [
        Finding(
            finding_id="finding:evidence",
            statement="Evidence-backed claim.",
            evidence_refs=["evidence-1"],
            status="supported",
        )
    ]
    diagnostics = diagnostics or EvidenceDiagnostics(
        status="completed",
        source="p2_documents",
        analyzer_completed=True,
        aggregation_attempted=True,
        aggregation_completed=True,
    )
    return EvidenceSidecarResult(
        documents=documents or [Document(document_id="doc-1", uri="https://example.com/source")],
        document_analyses=[DocumentAnalysis(document_id="doc-1", relevance_score=0.8)],
        evidence=evidence,
        findings=findings,
        diagnostics=diagnostics,
        llm_calls_delta=calls,
        input_tokens_delta=11,
        output_tokens_delta=7,
        llm_call_details=[
            {
                "agent": "EvidenceAnalyzer",
                "operation": "analyze_document",
                "input_tokens": 11,
                "output_tokens": 7,
                "success": True,
                "token_source": "estimated",
            }
            for _ in range(calls)
        ],
    )


def _synthesizer(monkeypatch, *, runtime: EvidenceRuntimeConfig, factory=None) -> ResearchSynthesizer:
    monkeypatch.setattr(
        agents,
        "create_agent",
        lambda *_args, **_kwargs: FakeSynthesisAgent('["Legacy synthesis finding"]'),
    )
    return ResearchSynthesizer(
        llm=object(),
        max_retries=1,
        evidence_config=runtime,
        sidecar_factory=factory,
    )


def test_disabled_flag_keeps_p22a_patch_and_never_constructs_sidecar(monkeypatch) -> None:
    def forbidden_factory():
        raise AssertionError("sidecar factory must not run when disabled")

    patch = asyncio.run(
        _synthesizer(monkeypatch, runtime=EvidenceRuntimeConfig(enabled=False), factory=forbidden_factory)
        .synthesize(_state())
    )

    assert [finding.statement for finding in patch["findings"]] == patch["key_findings"]
    assert patch["evidence_diagnostics"].status == "disabled"
    assert patch["llm_calls"] == 5
    assert patch["total_input_tokens"] >= 40
    assert len(patch["llm_call_details"]) == 2
    assert "documents" not in patch


def test_successful_sidecar_persists_outputs_and_replaces_only_with_valid_evidence_findings(monkeypatch) -> None:
    sidecar = FakeSidecar(_evidence_result())
    state = _state(documents=[Document(document_id="doc-1", uri="https://example.com/source")])

    patch = asyncio.run(_synthesizer(monkeypatch, runtime=_runtime(), factory=lambda: sidecar).synthesize(state))

    assert patch["key_findings"] == ["Legacy synthesis finding"]
    assert patch["findings"][0].finding_id == "finding:evidence"
    assert patch["document_analyses"][0].document_id == "doc-1"
    assert patch["evidence"][0].evidence_id == "evidence-1"
    assert patch["evidence_diagnostics"].status == "completed"
    assert patch["llm_calls"] == 7
    assert patch["total_input_tokens"] >= 51
    assert patch["total_output_tokens"] >= 27
    assert len(patch["llm_call_details"]) == 4
    assert sidecar.calls[0]["documents"] == state.documents


def test_findings_replacement_requires_nonempty_known_evidence_refs_and_allowed_partial(monkeypatch) -> None:
    invalid_cases = [
        _evidence_result(findings=[], calls=0),
        _evidence_result(findings=[Finding(finding_id="empty", statement="Empty", status="supported")]),
        _evidence_result(findings=[Finding(finding_id="unknown", statement="Unknown", evidence_refs=["missing"], status="supported")]),
        _evidence_result(
            diagnostics=EvidenceDiagnostics(
                status="partial",
                source="p2_documents",
                analyzer_completed=False,
                analyzer_partial=True,
                aggregation_attempted=True,
                aggregation_completed=True,
            )
        ),
    ]
    for result in invalid_cases:
        runtime = _runtime(allow_partial=False) if result.diagnostics.status == "partial" else _runtime()
        patch = asyncio.run(
            _synthesizer(monkeypatch, runtime=runtime, factory=lambda item=result: FakeSidecar(item))
            .synthesize(_state(documents=[Document(document_id="doc-1", uri="https://example.com/source")]))
        )
        assert patch["findings"][0].status == "unverified"
        assert patch["key_findings"] == ["Legacy synthesis finding"]


def test_sidecar_factory_and_runtime_failures_are_isolated_and_writer_can_use_legacy_findings(monkeypatch) -> None:
    factory_failure = asyncio.run(
        _synthesizer(
            monkeypatch,
            runtime=_runtime(),
            factory=lambda: (_ for _ in ()).throw(RuntimeError("fake factory failure")),
        ).synthesize(_state())
    )
    runtime_failure = asyncio.run(
        _synthesizer(monkeypatch, runtime=_runtime(), factory=lambda: FakeSidecar(RuntimeError("fake runtime failure")))
        .synthesize(_state())
    )

    for patch in (factory_failure, runtime_failure):
        assert "error" not in patch
        assert patch["current_stage"] == "reporting"
        assert patch["findings"][0].status == "unverified"
        assert patch["evidence_diagnostics"].status == "failed"

    writer = ReportWriter(llm=object(), max_retries=1)
    received: list[list[str]] = []

    async def fake_write_section(_topic, section_title, findings, _results):
        received.append(findings)
        return ReportSection(title=section_title, content="Writer content. " * 50), 0

    monkeypatch.setattr(writer, "_write_section", fake_write_section)
    writer_state = _state()
    writer_state.key_findings = runtime_failure["key_findings"]
    writer_state.findings = runtime_failure["findings"]
    asyncio.run(writer.write_report(writer_state))
    assert received == [["Legacy synthesis finding"]]


def test_backfill_writes_documents_and_sidecar_tracking_is_appended(monkeypatch) -> None:
    sidecar_result = _evidence_result(
        documents=[Document(document_id="backfill", uri="https://example.com/source", credibility=None)],
        diagnostics=EvidenceDiagnostics(
            status="partial",
            source="legacy_search_results_backfill",
            analyzer_completed=False,
            analyzer_partial=True,
            aggregation_attempted=True,
            aggregation_completed=True,
        ),
        calls=2,
    )
    patch = asyncio.run(_synthesizer(monkeypatch, runtime=_runtime(), factory=lambda: FakeSidecar(sidecar_result)).synthesize(_state()))

    assert patch["documents"][0].document_id == "backfill"
    assert patch["documents"][0].credibility is None
    assert patch["findings"][0].finding_id == "finding:evidence"
    assert patch["llm_calls"] == 7
    assert len(patch["llm_call_details"]) == 4


def test_empty_key_findings_never_constructs_enabled_sidecar(monkeypatch) -> None:
    synth = _synthesizer(monkeypatch, runtime=_runtime(), factory=lambda: (_ for _ in ()).throw(AssertionError("must not run")))
    monkeypatch.setattr(synth, "_extract_findings", lambda *_args: [])

    patch = asyncio.run(synth.synthesize(_state()))

    assert patch["key_findings"] == []
    assert patch["evidence_diagnostics"].status == "not_run"
    assert patch["findings"] == []
