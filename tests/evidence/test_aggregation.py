"""Tests for the deterministic, no-LLM Finding Aggregator baseline."""

from src.evidence.aggregation import RuleBasedFindingAggregator
from src.evidence.models import DocumentAnalysis, Evidence
from src.state import Document


def _document(document_id: str) -> Document:
    return Document(
        document_id=document_id,
        uri=f"https://example.com/{document_id}",
        credibility={"score": 80},
    )


def _analysis(document_id: str) -> DocumentAnalysis:
    return DocumentAnalysis(
        document_id=document_id,
        relevance_score=0.8,
        credibility_score=80,
    )


def _evidence(
    evidence_id: str,
    document_id: str,
    claim: str,
    **overrides: object,
) -> Evidence:
    values: dict[str, object] = {
        "evidence_id": evidence_id,
        "document_id": document_id,
        "source_url": f"https://example.com/{document_id}",
        "claim": claim,
        "source_quote": "Supporting source quote.",
        "text_source": "content",
        "relation": "supports",
        "strength": 0.8,
        "status": "grounded",
    }
    values.update(overrides)
    return Evidence(**values)


def test_aggregator_groups_evidence_with_the_same_normalized_claim() -> None:
    aggregator = RuleBasedFindingAggregator()
    result = aggregator.aggregate(
        documents=[_document("doc-1"), _document("doc-2")],
        analyses=[_analysis("doc-1"), _analysis("doc-2")],
        evidence=[
            _evidence("evidence-1", "doc-1", "Framework adoption is increasing."),
            _evidence("evidence-2", "doc-2", " framework   adoption is increasing "),
        ],
    )

    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.finding_id.startswith("finding:")
    assert finding.evidence_refs == ["evidence-1", "evidence-2"]
    assert finding.contradictory_evidence_refs == []
    assert finding.status == "supported"


def test_aggregator_keeps_different_claims_in_separate_findings() -> None:
    aggregator = RuleBasedFindingAggregator()
    result = aggregator.aggregate(
        documents=[_document("doc-1"), _document("doc-2")],
        analyses=[_analysis("doc-1"), _analysis("doc-2")],
        evidence=[
            _evidence("evidence-1", "doc-1", "Adoption is increasing."),
            _evidence("evidence-2", "doc-2", "Costs are decreasing."),
        ],
    )

    assert len(result.findings) == 2
    assert {finding.statement for finding in result.findings} == {
        "Adoption is increasing.",
        "Costs are decreasing.",
    }


def test_aggregator_excludes_invalid_and_unknown_document_evidence() -> None:
    aggregator = RuleBasedFindingAggregator()
    result = aggregator.aggregate(
        documents=[_document("doc-1")],
        analyses=[_analysis("doc-1")],
        evidence=[
            _evidence("evidence-valid", "doc-1", "Adoption is increasing."),
            _evidence(
                "evidence-invalid",
                "doc-1",
                "Adoption is increasing.",
                status="invalid",
            ),
            _evidence("evidence-unknown", "missing-doc", "Adoption is increasing."),
        ],
    )

    assert result.available_evidence_ids == ["evidence-valid"]
    assert result.findings[0].evidence_refs == ["evidence-valid"]
    assert result.completed is False
    assert result.partial is True
    assert len(result.errors) == 2


def test_aggregator_uses_only_evidence_ids_in_finding_references() -> None:
    aggregator = RuleBasedFindingAggregator()
    result = aggregator.aggregate(
        documents=[_document("doc-1")],
        analyses=[_analysis("doc-1")],
        evidence=[_evidence("evidence-1", "doc-1", "Adoption is increasing.")],
    )

    finding = result.findings[0]
    assert finding.evidence_refs == ["evidence-1"]
    assert "doc-1" not in finding.evidence_refs
    assert finding.evidence_refs == result.available_evidence_ids


def test_aggregator_confidence_is_deterministic() -> None:
    aggregator = RuleBasedFindingAggregator()
    inputs = {
        "documents": [_document("doc-1"), _document("doc-2")],
        "analyses": [_analysis("doc-1"), _analysis("doc-2")],
        "evidence": [
            _evidence("evidence-1", "doc-1", "Adoption is increasing."),
            _evidence("evidence-2", "doc-2", "Adoption is increasing."),
        ],
    }

    first = aggregator.aggregate(**inputs)
    second = aggregator.aggregate(**inputs)

    assert first.findings[0].confidence == second.findings[0].confidence
    assert 0.0 <= first.findings[0].confidence <= 1.0


def test_aggregator_returns_identical_output_for_identical_input() -> None:
    aggregator = RuleBasedFindingAggregator()
    inputs = {
        "documents": [_document("doc-1"), _document("doc-2")],
        "analyses": [_analysis("doc-1"), _analysis("doc-2")],
        "evidence": [
            _evidence("evidence-2", "doc-2", "Adoption is increasing."),
            _evidence("evidence-1", "doc-1", "Adoption is increasing."),
        ],
    }

    first = aggregator.aggregate(**inputs)
    second = aggregator.aggregate(**inputs)

    assert first.model_dump() == second.model_dump()
