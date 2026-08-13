"""Dependency-free Pydantic contracts shared by State and the Evidence runtime."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


AnalysisStatus = Literal["analyzed", "partial", "failed", "skipped"]
EvidenceStatus = Literal["grounded", "partial", "invalid"]
EvidenceRelation = Literal["supports", "contradicts", "context"]
AnalyzedTextSource = Literal["content", "snippet", "none"]
EvidenceTextSource = Literal["content", "snippet"]


class DocumentAnalysis(BaseModel):
    """Document-level analysis linked only through stable document/evidence IDs."""

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
    """A source-grounded statement extracted from one document."""

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
