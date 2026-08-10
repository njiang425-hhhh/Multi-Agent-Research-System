"""Pydantic contracts for the standalone Evidence Layer."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from src.state import Document


AnalysisStatus = Literal["analyzed", "partial", "failed", "skipped"]
EvidenceStatus = Literal["grounded", "partial", "invalid"]
EvidenceRelation = Literal["supports", "contradicts", "context"]
AnalyzedTextSource = Literal["content", "snippet", "none"]
EvidenceTextSource = Literal["content", "snippet"]


class DocumentAnalysis(BaseModel):
    """Document-level relevance assessment and references to extracted evidence.

    ``evidence_ids`` deliberately refers only to :class:`Evidence` identifiers.
    This keeps the model compatible with ``Finding.evidence_refs`` without
    reusing document identifiers as evidence identifiers.
    """

    document_id: str = Field(min_length=1, description="Referenced Document identifier")
    relevance_score: float = Field(ge=0.0, le=1.0, description="Topic relevance score")
    credibility_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Normalized score derived from existing document credibility",
    )
    key_points: List[str] = Field(default_factory=list, description="Document-level key points")
    evidence_ids: List[str] = Field(
        default_factory=list,
        description="Evidence identifiers extracted from this document",
    )
    analyzed_text_source: AnalyzedTextSource = Field(
        default="content",
        description="Document text used by the analyzer",
    )
    status: AnalysisStatus = Field(default="analyzed", description="Analysis outcome")
    error: Optional[str] = Field(default=None, description="Failure detail when not analyzed")


class Evidence(BaseModel):
    """A source-grounded statement extracted from one Document.

    ``source_quote`` must be copied from the document text by a future analyzer
    implementation.  The model records offsets when content is available but
    does not itself inspect source text, keeping it independent from retrieval.
    """

    evidence_id: str = Field(min_length=1, description="Stable evidence identifier")
    document_id: str = Field(min_length=1, description="Source Document identifier")
    source_url: str = Field(min_length=1, description="Source URL for citation lookup")
    claim: str = Field(min_length=1, description="Claim supported or contextualized by this evidence")
    source_quote: str = Field(min_length=1, description="Verbatim source text")
    source_start: Optional[int] = Field(default=None, ge=0, description="Quote start offset")
    source_end: Optional[int] = Field(default=None, ge=0, description="Quote end offset")
    text_source: EvidenceTextSource = Field(description="Whether the quote came from content or snippet")
    relation: EvidenceRelation = Field(description="Relationship to a candidate finding")
    strength: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Strength of the evidence for its relationship",
    )
    status: EvidenceStatus = Field(default="grounded", description="Evidence validation outcome")

    @model_validator(mode="after")
    def validate_source_offsets(self) -> "Evidence":
        """Require offsets to be supplied together and in source order."""
        if (self.source_start is None) != (self.source_end is None):
            raise ValueError("source_start and source_end must be provided together")
        if self.source_start is not None and self.source_end is not None:
            if self.source_end <= self.source_start:
                raise ValueError("source_end must be greater than source_start")
        return self


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
