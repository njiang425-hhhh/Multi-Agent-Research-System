"""Provider-neutral protocol used by the Evidence Layer service."""

from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from src.evidence.drafts import DocumentAnalysisDraft


class DocumentAnalysisRequest(BaseModel):
    """The bounded, source-grounded input passed to an analyzer model."""

    topic: str = Field(min_length=1)
    objectives: list[str] = Field(default_factory=list)
    document_id: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    title: str = Field(default="")
    source_text: str = Field(min_length=1)
    text_source: Literal["content", "snippet"]
    credibility: dict[str, Any] | None = None


class AnalyzerModel(Protocol):
    """Minimal model boundary allowing a fake implementation in unit tests."""

    async def analyze_document(
        self,
        request: DocumentAnalysisRequest,
    ) -> DocumentAnalysisDraft:
        """Return a validated document-analysis draft without runtime side effects."""
