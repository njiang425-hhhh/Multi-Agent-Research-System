"""Minimal fake-only Evidence grounding contracts."""

import asyncio

from src.evidence.drafts import DocumentAnalysisDraft, EvidenceDraft
from src.evidence.service import ResultAnalyzer
from src.state import Document


class FakeAnalyzerModel:
    def __init__(self, draft: DocumentAnalysisDraft) -> None:
        self.draft = draft

    async def analyze_document(self, _request: object) -> DocumentAnalysisDraft:
        return self.draft


def _document(content: str) -> Document:
    return Document(
        document_id="doc-1",
        title="Source",
        uri="https://example.com/source",
        snippet="Snippet",
        content=content,
        status="retrieved",
    )


def _draft(quote: str) -> DocumentAnalysisDraft:
    return DocumentAnalysisDraft(
        relevance_score=0.9,
        evidence=[EvidenceDraft(claim="The source supports the claim.", source_quote=quote, relation="supports")],
    )


def test_evidence_analyzer_builds_grounded_evidence_from_source_content() -> None:
    source_text = "The source supports the claim."
    result = asyncio.run(
        ResultAnalyzer(FakeAnalyzerModel(_draft(source_text))).analyze(
            topic="Research topic", documents=[_document(source_text)]
        )
    )

    assert result.completed is True
    assert len(result.evidence) == 1
    assert result.evidence[0].status == "grounded"


def test_evidence_analyzer_marks_an_unsupported_quote_as_partial() -> None:
    result = asyncio.run(
        ResultAnalyzer(FakeAnalyzerModel(_draft("not in the source"))).analyze(
            topic="Research topic", documents=[_document("The source supports the claim.")]
        )
    )

    assert result.evidence == []
    assert result.partial is True
    assert "could not be uniquely validated" in result.errors[0]
