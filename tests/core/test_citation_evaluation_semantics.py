"""Regression coverage for separate citation-integrity and Evidence-grounding semantics."""

from src.evaluation.evaluator import evaluate_run
from src.evidence.contracts import Evidence
from src.state import Document, EvidenceDiagnostics, Finding, Report, ReportSection, ResearchState


def _statuses(state: ResearchState) -> dict[str, str]:
    return {metric.name: metric.status for metric in evaluate_run(state).metrics}


def _state(*, marker: str = "[1]", citation_url: str = "https://example.com/source") -> ResearchState:
    url = "https://example.com/source"
    section = ReportSection(title="Summary", content=f"Supported statement {marker}.", sources=[url])
    content = f"# Citation semantics\n\n## Summary\n\n{section.content}"
    return ResearchState(
        research_topic="citation semantics",
        query="citation semantics",
        status="completed",
        current_stage="complete",
        documents=[Document(document_id="doc-1", title="Source", uri=url, content="quoted support")],
        findings=[Finding(finding_id="finding-1", statement="Supported statement", source_document_ids=["doc-1"])],
        report=Report(title="citation semantics", sections=[section], content=content, citations=[citation_url], status="completed"),
    )


def test_valid_citation_is_evaluated_when_evidence_is_disabled() -> None:
    state = _state().model_copy(
        update={"evidence_diagnostics": EvidenceDiagnostics(status="disabled", source="p2_documents")}
    )

    statuses = _statuses(state)

    assert statuses["citation_integrity"] == "passed"
    assert statuses["evidence_grounding"] == "unavailable"


def test_invalid_citation_number_fails_integrity_without_claiming_evidence_failure() -> None:
    state = _state(marker="[2]").model_copy(
        update={"evidence_diagnostics": EvidenceDiagnostics(status="disabled", source="p2_documents")}
    )

    result = evaluate_run(state)

    assert {metric.name: metric.status for metric in result.metrics}["citation_integrity"] == "failed"
    assert result.quality.invalid_citation_count == 1
    assert {metric.name: metric.status for metric in result.metrics}["evidence_grounding"] == "unavailable"


def test_citation_document_url_mismatch_fails_integrity() -> None:
    result = evaluate_run(_state(citation_url="https://example.com/wrong"))

    assert {metric.name: metric.status for metric in result.metrics}["citation_integrity"] == "failed"
    assert result.quality.citation_map_mismatch_count == 1


def test_evidence_supported_finding_passes_grounding_without_factuality_claim() -> None:
    evidence = Evidence(
        evidence_id="evidence-1",
        document_id="doc-1",
        source_url="https://example.com/source",
        claim="Supported statement",
        source_quote="quoted support",
        text_source="content",
        relation="supports",
    )
    state = _state().model_copy(
        update={
            "findings": [
                Finding(
                    finding_id="finding-1",
                    statement="Supported statement",
                    source_document_ids=["doc-1"],
                    evidence_refs=["evidence-1"],
                )
            ],
            "evidence": [evidence],
            "evidence_diagnostics": EvidenceDiagnostics(status="completed", source="p2_documents"),
        }
    )

    result = evaluate_run(state)

    assert {metric.name: metric.status for metric in result.metrics}["citation_integrity"] == "passed"
    assert {metric.name: metric.status for metric in result.metrics}["evidence_grounding"] == "passed"
    assert result.quality.evidence_supported_finding_count == 1


def test_contradicted_finding_fails_evidence_grounding() -> None:
    evidence = Evidence(
        evidence_id="evidence-contradicts",
        document_id="doc-1",
        source_url="https://example.com/source",
        claim="Supported statement",
        source_quote="quoted support",
        text_source="content",
        relation="contradicts",
    )
    state = _state().model_copy(
        update={
            "findings": [
                Finding(
                    finding_id="finding-1",
                    statement="Supported statement",
                    source_document_ids=["doc-1"],
                    contradictory_evidence_refs=["evidence-contradicts"],
                )
            ],
            "evidence": [evidence],
            "evidence_diagnostics": EvidenceDiagnostics(status="completed", source="p2_documents"),
        }
    )

    result = evaluate_run(state)

    assert {metric.name: metric.status for metric in result.metrics}["citation_integrity"] == "passed"
    assert {metric.name: metric.status for metric in result.metrics}["evidence_grounding"] == "failed"
    assert result.quality.evidence_contradicted_finding_count == 1
