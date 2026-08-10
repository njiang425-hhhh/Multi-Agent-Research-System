"""Contracts for a future Finding Aggregator without orchestration integration."""

from typing import Annotated, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from src.state import Finding


EvidenceId = Annotated[str, Field(min_length=1)]
FindingStatus = Literal["supported", "contested", "insufficient", "failed"]


class FindingDraft(BaseModel):
    """Structured aggregation proposal before creation of an existing Finding.

    The draft names only Evidence IDs.  A future service will validate these IDs
    against its eligible evidence set, calculate confidence, and then create the
    existing ``src.state.Finding`` model.
    """

    statement: str = Field(min_length=1)
    supporting_evidence_ids: List[EvidenceId] = Field(default_factory=list)
    contradictory_evidence_ids: List[EvidenceId] = Field(default_factory=list)
    reasoning_summary: Optional[str] = Field(default=None)
    status: FindingStatus = Field(default="supported")

    @model_validator(mode="after")
    def validate_conflict_references(self) -> "FindingDraft":
        support_ids = self.supporting_evidence_ids
        contradiction_ids = self.contradictory_evidence_ids
        if len(support_ids) != len(set(support_ids)):
            raise ValueError("supporting_evidence_ids must not contain duplicates")
        if len(contradiction_ids) != len(set(contradiction_ids)):
            raise ValueError("contradictory_evidence_ids must not contain duplicates")
        if set(support_ids).intersection(contradiction_ids):
            raise ValueError("an Evidence ID cannot both support and contradict a Finding")
        return self


class FindingAggregationResult(BaseModel):
    """Validated aggregation output using the existing ``Finding`` type.

    ``available_evidence_ids`` is validation context, not a replacement for the
    Evidence records.  Every reference in a Finding must belong to this explicit
    set, preventing document IDs, URLs, list indices, or arbitrary text from
    entering ``Finding.evidence_refs``.
    """

    available_evidence_ids: List[EvidenceId] = Field(default_factory=list)
    findings: List[Finding] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    completed: bool = Field(default=True)
    partial: bool = Field(default=False)

    @model_validator(mode="after")
    def validate_finding_references(self) -> "FindingAggregationResult":
        available_ids = self.available_evidence_ids
        if len(available_ids) != len(set(available_ids)):
            raise ValueError("available_evidence_ids must not contain duplicates")

        allowed_ids = set(available_ids)
        for finding in self.findings:
            if finding.status not in {"supported", "contested", "insufficient", "failed"}:
                raise ValueError(f"Finding '{finding.finding_id}' has an unsupported status")
            if finding.confidence is not None and not 0.0 <= finding.confidence <= 1.0:
                raise ValueError(f"Finding '{finding.finding_id}' confidence must be between 0 and 1")

            support_ids = finding.evidence_refs
            contradiction_ids = finding.contradictory_evidence_refs
            if len(support_ids) != len(set(support_ids)):
                raise ValueError(f"Finding '{finding.finding_id}' has duplicate evidence_refs")
            if len(contradiction_ids) != len(set(contradiction_ids)):
                raise ValueError(
                    f"Finding '{finding.finding_id}' has duplicate contradictory_evidence_refs"
                )
            if set(support_ids).intersection(contradiction_ids):
                raise ValueError(
                    f"Finding '{finding.finding_id}' cannot reference the same Evidence as support and contradiction"
                )

            unknown_ids = (set(support_ids) | set(contradiction_ids)) - allowed_ids
            if unknown_ids:
                unknown = ", ".join(sorted(unknown_ids))
                raise ValueError(
                    f"Finding '{finding.finding_id}' references unknown Evidence IDs: {unknown}"
                )

        return self
