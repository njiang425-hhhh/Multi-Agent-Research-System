"""Pydantic contracts for the standalone Evidence Layer."""

from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from src.evidence.contracts import DocumentAnalysis, Evidence
from src.runtime_control import ExecutionContext
from src.state import Document


class AnalysisResult(BaseModel):
    """Standalone output of document analysis before any Finding aggregation.

    Existing ``Document`` objects remain the sole source-document model.  This
    result intentionally does not create or store ``Finding`` instances; a
    later aggregation stage can consume the grounded ``Evidence`` records.
    """

    documents: List[Document] = Field(default_factory=list, description="Analyzed source documents")
    analyses: List[DocumentAnalysis] = Field(
        default_factory=list,
        description="One or more document-level analyses",
    )
    evidence: List[Evidence] = Field(default_factory=list, description="Extracted evidence records")
    errors: List[str] = Field(default_factory=list, description="Non-fatal analysis errors")
    completed: bool = Field(default=True, description="Whether all requested work completed")
    partial: bool = Field(default=False, description="Whether usable output is incomplete")
    execution_context: Optional[ExecutionContext] = Field(
        default=None,
        description="Runtime control context forwarded without Evidence ownership",
    )

    @model_validator(mode="after")
    def validate_references(self) -> "AnalysisResult":
        """Ensure analyses and evidence refer to known, unambiguous documents."""
        document_ids = [document.document_id for document in self.documents]
        evidence_ids = [item.evidence_id for item in self.evidence]

        if any(not document_id for document_id in document_ids):
            raise ValueError("documents in AnalysisResult must have document_id values")
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("AnalysisResult contains duplicate document_id values")
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("AnalysisResult contains duplicate evidence_id values")

        known_document_ids = set(document_ids)
        known_evidence_ids = set(evidence_ids)

        for item in self.evidence:
            if item.document_id not in known_document_ids:
                raise ValueError(
                    f"Evidence '{item.evidence_id}' references an unknown document_id"
                )

        for analysis in self.analyses:
            if analysis.document_id not in known_document_ids:
                raise ValueError(
                    f"DocumentAnalysis references an unknown document_id '{analysis.document_id}'"
                )
            for evidence_id in analysis.evidence_ids:
                if evidence_id not in known_evidence_ids:
                    raise ValueError(
                        f"DocumentAnalysis references an unknown evidence_id '{evidence_id}'"
                    )
                matching_evidence = next(
                    item for item in self.evidence if item.evidence_id == evidence_id
                )
                if matching_evidence.document_id != analysis.document_id:
                    raise ValueError(
                        "DocumentAnalysis may only reference evidence from the same document"
                    )

        return self
