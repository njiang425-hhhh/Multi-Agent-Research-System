"""Tests for the standalone Evidence Layer data contracts."""

import pytest
from pydantic import ValidationError

from src.evidence.models import AnalysisResult, DocumentAnalysis, Evidence
from src.state import Document


def _document(document_id: str = "doc-1") -> Document:
    return Document(
        document_id=document_id,
        title="Primary source",
        uri=f"https://example.com/{document_id}",
        content="The source text supports the claim.",
    )


def _evidence(
    evidence_id: str = "evidence-1",
    document_id: str = "doc-1",
    **overrides: object,
) -> Evidence:
    values: dict[str, object] = {
        "evidence_id": evidence_id,
        "document_id": document_id,
        "source_url": f"https://example.com/{document_id}",
        "claim": "The source supports the claim.",
        "source_quote": "The source text supports the claim.",
        "text_source": "content",
        "relation": "supports",
    }
    values.update(overrides)
    return Evidence(**values)


def test_document_analysis_requires_document_identifier_and_relevance_score() -> None:
    with pytest.raises(ValidationError):
        DocumentAnalysis(relevance_score=0.5)

    with pytest.raises(ValidationError):
        DocumentAnalysis(document_id="doc-1")


def test_evidence_requires_its_source_and_relationship_fields() -> None:
    with pytest.raises(ValidationError):
        Evidence(
            evidence_id="evidence-1",
            document_id="doc-1",
            source_url="https://example.com/doc-1",
            claim="A claim",
            source_quote="A quote",
        )

    with pytest.raises(ValidationError):
        _evidence(source_quote="")


@pytest.mark.parametrize("status", ["unknown", "complete", "grounded"])
def test_document_analysis_rejects_invalid_status_values(status: str) -> None:
    with pytest.raises(ValidationError):
        DocumentAnalysis(document_id="doc-1", relevance_score=0.5, status=status)


@pytest.mark.parametrize("relation", ["unknown", "agrees", "neutral"])
def test_evidence_rejects_invalid_relation_values(relation: str) -> None:
    with pytest.raises(ValidationError):
        _evidence(relation=relation)


@pytest.mark.parametrize("score", [-0.01, 1.01])
def test_document_analysis_rejects_out_of_range_relevance_scores(score: float) -> None:
    with pytest.raises(ValidationError):
        DocumentAnalysis(document_id="doc-1", relevance_score=score)


@pytest.mark.parametrize("score", [-0.01, 100.01])
def test_document_analysis_rejects_out_of_range_credibility_scores(score: float) -> None:
    with pytest.raises(ValidationError):
        DocumentAnalysis(
            document_id="doc-1",
            relevance_score=0.5,
            credibility_score=score,
        )


@pytest.mark.parametrize("strength", [-0.01, 1.01])
def test_evidence_rejects_out_of_range_strength(strength: float) -> None:
    with pytest.raises(ValidationError):
        _evidence(strength=strength)


def test_evidence_rejects_invalid_source_offsets() -> None:
    with pytest.raises(ValidationError, match="provided together"):
        _evidence(source_start=0)

    with pytest.raises(ValidationError, match="greater than source_start"):
        _evidence(source_start=8, source_end=4)


def test_analysis_result_accepts_references_to_the_same_document() -> None:
    analysis = DocumentAnalysis(
        document_id="doc-1",
        relevance_score=0.9,
        credibility_score=85.0,
        key_points=["The source contains a relevant fact."],
        evidence_ids=["evidence-1"],
    )
    evidence = _evidence(source_start=0, source_end=36, strength=0.8)

    result = AnalysisResult(
        documents=[_document()],
        analyses=[analysis],
        evidence=[evidence],
    )

    assert result.documents[0].document_id == "doc-1"
    assert result.analyses[0].evidence_ids == ["evidence-1"]
    assert result.evidence[0].document_id == "doc-1"
    assert result.completed is True
    assert result.partial is False


def test_analysis_result_rejects_unknown_document_references() -> None:
    with pytest.raises(ValidationError, match="unknown document_id"):
        AnalysisResult(documents=[_document()], evidence=[_evidence(document_id="missing")])

    with pytest.raises(ValidationError, match="DocumentAnalysis references an unknown document_id"):
        AnalysisResult(
            documents=[_document()],
            analyses=[DocumentAnalysis(document_id="missing", relevance_score=0.5)],
        )


def test_analysis_result_rejects_invalid_evidence_relationships() -> None:
    with pytest.raises(ValidationError, match="unknown evidence_id"):
        AnalysisResult(
            documents=[_document()],
            analyses=[
                DocumentAnalysis(
                    document_id="doc-1",
                    relevance_score=0.5,
                    evidence_ids=["missing-evidence"],
                )
            ],
        )

    with pytest.raises(ValidationError, match="same document"):
        AnalysisResult(
            documents=[_document("doc-1"), _document("doc-2")],
            analyses=[
                DocumentAnalysis(
                    document_id="doc-1",
                    relevance_score=0.5,
                    evidence_ids=["evidence-2"],
                )
            ],
            evidence=[_evidence(evidence_id="evidence-2", document_id="doc-2")],
        )


def test_analysis_result_rejects_duplicate_identifiers() -> None:
    with pytest.raises(ValidationError, match="duplicate document_id"):
        AnalysisResult(documents=[_document(), _document()])

    with pytest.raises(ValidationError, match="duplicate evidence_id"):
        AnalysisResult(
            documents=[_document()],
            evidence=[_evidence(), _evidence()],
        )
