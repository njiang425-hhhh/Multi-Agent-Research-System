"""Tests for Finding Aggregation contracts using the existing Finding model."""

import pytest
from pydantic import ValidationError

from src.evidence.finding_models import FindingAggregationResult, FindingDraft
from src.state import Finding


def _finding(**overrides: object) -> Finding:
    values: dict[str, object] = {
        "finding_id": "finding-1",
        "statement": "A supported research conclusion.",
        "evidence_refs": ["evidence-1"],
        "contradictory_evidence_refs": [],
        "confidence": 0.8,
        "reasoning_summary": "Supported by a grounded source.",
        "status": "supported",
    }
    values.update(overrides)
    return Finding(**values)


def test_finding_draft_accepts_distinct_evidence_id_references() -> None:
    draft = FindingDraft(
        statement="Sources disagree about the conclusion.",
        supporting_evidence_ids=["evidence-1"],
        contradictory_evidence_ids=["evidence-2"],
        status="contested",
    )

    assert draft.supporting_evidence_ids == ["evidence-1"]
    assert draft.contradictory_evidence_ids == ["evidence-2"]
    assert draft.status == "contested"


@pytest.mark.parametrize("status", ["unknown", "complete", "grounded"])
def test_finding_draft_rejects_invalid_status_values(status: str) -> None:
    with pytest.raises(ValidationError):
        FindingDraft(statement="A finding", status=status)


def test_finding_draft_rejects_duplicate_and_conflicting_evidence_ids() -> None:
    with pytest.raises(ValidationError, match="duplicates"):
        FindingDraft(statement="A finding", supporting_evidence_ids=["evidence-1", "evidence-1"])

    with pytest.raises(ValidationError, match="both support and contradict"):
        FindingDraft(
            statement="A finding",
            supporting_evidence_ids=["evidence-1"],
            contradictory_evidence_ids=["evidence-1"],
        )


def test_finding_aggregation_result_accepts_existing_finding_with_evidence_ids() -> None:
    result = FindingAggregationResult(
        available_evidence_ids=["evidence-1", "evidence-2"],
        findings=[
            _finding(
                evidence_refs=["evidence-1"],
                contradictory_evidence_refs=["evidence-2"],
                status="contested",
                confidence=0.45,
            )
        ],
    )

    finding = result.findings[0]
    assert finding.evidence_refs == ["evidence-1"]
    assert finding.contradictory_evidence_refs == ["evidence-2"]
    assert finding.status == "contested"


def test_finding_aggregation_result_rejects_non_evidence_references() -> None:
    with pytest.raises(ValidationError, match="unknown Evidence IDs"):
        FindingAggregationResult(
            available_evidence_ids=["evidence-1"],
            findings=[_finding(evidence_refs=["doc-1"])],
        )


def test_finding_aggregation_result_rejects_conflicting_existing_finding_references() -> None:
    with pytest.raises(ValidationError, match="support and contradiction"):
        FindingAggregationResult(
            available_evidence_ids=["evidence-1"],
            findings=[
                _finding(
                    evidence_refs=["evidence-1"],
                    contradictory_evidence_refs=["evidence-1"],
                    status="contested",
                )
            ],
        )


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_finding_aggregation_result_rejects_out_of_range_confidence(confidence: float) -> None:
    with pytest.raises(ValidationError, match="confidence must be between 0 and 1"):
        FindingAggregationResult(
            available_evidence_ids=["evidence-1"],
            findings=[_finding(confidence=confidence)],
        )


def test_finding_aggregation_result_rejects_invalid_existing_finding_status() -> None:
    with pytest.raises(ValidationError, match="unsupported status"):
        FindingAggregationResult(
            available_evidence_ids=["evidence-1"],
            findings=[_finding(status="unverified")],
        )


def test_finding_aggregation_result_rejects_duplicate_available_evidence_ids() -> None:
    with pytest.raises(ValidationError, match="available_evidence_ids must not contain duplicates"):
        FindingAggregationResult(available_evidence_ids=["evidence-1", "evidence-1"])
