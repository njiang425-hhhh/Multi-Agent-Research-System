"""Structured model output contracts before Evidence Layer enrichment."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class EvidenceDraft(BaseModel):
    """Model-proposed evidence that still requires source validation."""

    claim: str = Field(min_length=1)
    source_quote: str = Field(min_length=1)
    source_start: Optional[int] = Field(default=None, ge=0)
    source_end: Optional[int] = Field(default=None, ge=0)
    relation: Literal["supports", "contradicts", "context"]
    strength: Optional[float] = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_source_offsets(self) -> "EvidenceDraft":
        if (self.source_start is None) != (self.source_end is None):
            raise ValueError("source_start and source_end must be provided together")
        if self.source_start is not None and self.source_end is not None:
            if self.source_end <= self.source_start:
                raise ValueError("source_end must be greater than source_start")
        return self


class DocumentAnalysisDraft(BaseModel):
    """Validated structured output returned by an ``AnalyzerModel``."""

    relevance_score: float = Field(ge=0.0, le=1.0)
    key_points: List[str] = Field(default_factory=list)
    evidence: List[EvidenceDraft] = Field(default_factory=list)
