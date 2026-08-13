"""Fake-only tests for the standalone EvidencePipeline runtime."""

import asyncio
import inspect
from collections.abc import Callable
from typing import Any

from src.evidence.config import AnalyzerConfig
from src.evidence.drafts import DocumentAnalysisDraft, EvidenceDraft
from src.evidence.finding_models import FindingAggregationResult
from src.evidence.pipeline import EvidencePipeline
from src.evidence.protocols import DocumentAnalysisRequest
from src.state import Document


class FakeAnalyzerModel:
    def __init__(self, handler: Callable[[DocumentAnalysisRequest], Any]) -> None:
        self.handler = handler
        self.calls: list[DocumentAnalysisRequest] = []

    async def analyze_document(self, request: DocumentAnalysisRequest) -> DocumentAnalysisDraft:
        self.calls.append(request)
        result = self.handler(request)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, Exception):
            raise result
        return result


class ExplodingAggregator:
    def aggregate(self, **_kwargs: object) -> FindingAggregationResult:
        raise RuntimeError("fake aggregation failure")


def _document(document_id: str, **overrides: object) -> Document:
    values: dict[str, object] = {
        "document_id": document_id,
        "title": f"Title {document_id}",
        "uri": f"https://example.com/{document_id}",
        "content": "The source supports the claim.",
        "snippet": "The source supports the claim.",
        "credibility": {"score": 80},
        "status": "retrieved",
    }
    values.update(overrides)
    return Document(**values)


def _draft_for(request: DocumentAnalysisRequest) -> DocumentAnalysisDraft:
    quote = "The source supports the claim."
    return DocumentAnalysisDraft(
        relevance_score=0.8,
        key_points=[f"Key point for {request.document_id}"],
        evidence=[
            EvidenceDraft(
                claim="The source supports the claim.",
                source_quote=quote,
                source_start=0,
                source_end=len(quote),
                relation="supports",
                strength=0.8,
            )
        ],
    )


def _run(
    model: FakeAnalyzerModel,
    documents: list[Document],
    *,
    config: AnalyzerConfig | None = None,
    aggregator: object | None = None,
):
    pipeline = EvidencePipeline(
        model,
        config=config,
        aggregator=aggregator,  # type: ignore[arg-type]
    )
    return asyncio.run(
        pipeline.run(
            topic="Research topic",
            documents=documents,
            objectives=["Test objective"],
        )
    )


def test_pipeline_aggregates_complete_analysis_and_retains_existing_results() -> None:
    model = FakeAnalyzerModel(_draft_for)

    result = _run(model, [_document("doc-1"), _document("doc-2")])

    assert result.analyzer_completed is True
    assert result.analyzer_partial is False
    assert result.aggregation_attempted is True
    assert result.aggregation_completed is True
    assert result.aggregation_partial is False
    assert len(result.document_analyses) == 2
    assert len(result.evidence) == 2
    assert len(result.findings) == 1
    assert result.findings[0].evidence_refs == [
        item.evidence_id for item in result.evidence
    ]
    assert result.analysis_result.analyses == result.document_analyses


def test_pipeline_aggregates_partial_analysis_when_runtime_allows_partial_results() -> None:
    model = FakeAnalyzerModel(_draft_for)

    result = _run(
        model,
        [_document("doc-1"), _document("doc-2")],
        config=AnalyzerConfig(max_documents=1, allow_partial_results=True),
    )

    assert result.analyzer_completed is False
    assert result.analyzer_partial is True
    assert result.aggregation_attempted is True
    assert len(result.evidence) == 1
    assert len(result.findings) == 1
    assert any("Document limit reached" in error for error in result.errors)


def test_pipeline_skips_aggregation_when_incomplete_analysis_disallows_partial_results() -> None:
    model = FakeAnalyzerModel(_draft_for)

    result = _run(
        model,
        [_document("doc-1"), _document("doc-2")],
        config=AnalyzerConfig(max_documents=1, allow_partial_results=False),
    )

    assert result.analyzer_completed is False
    assert result.analyzer_partial is False
    assert result.aggregation_attempted is False
    assert result.aggregation_completed is False
    assert result.findings == []
    assert len(result.evidence) == 1
    assert any("aggregation skipped" in error.lower() for error in result.errors)


def test_pipeline_returns_empty_findings_normally_when_no_evidence_is_valid() -> None:
    invalid_quote = DocumentAnalysisDraft(
        relevance_score=0.8,
        evidence=[
            EvidenceDraft(
                claim="Unsupported claim.",
                source_quote="not in the source",
                source_start=0,
                source_end=17,
                relation="supports",
            )
        ],
    )
    result = _run(FakeAnalyzerModel(lambda _: invalid_quote), [_document("doc-1")])

    assert result.aggregation_attempted is False
    assert result.findings == []
    assert result.evidence == []
    assert result.analysis_result.analyses[0].status == "partial"
    assert any("could not be uniquely validated" in error for error in result.errors)


def test_pipeline_uses_runtime_offsets_when_a_unique_quote_has_wrong_draft_offsets() -> None:
    quote = "The source supports the claim."
    document = _document("doc-1", content=f"Before. {quote} After.")
    draft = DocumentAnalysisDraft(
        relevance_score=0.8,
        evidence=[
            EvidenceDraft(
                claim="The source supports the claim.",
                source_quote=quote,
                source_start=0,
                source_end=1,
                relation="supports",
            )
        ],
    )

    result = _run(FakeAnalyzerModel(lambda _: draft), [document])

    assert len(result.evidence) == 1
    assert result.evidence[0].source_start == document.content.index(quote)
    assert result.evidence[0].source_end == result.evidence[0].source_start + len(quote)


def test_pipeline_captures_aggregation_failure_without_losing_analysis_output() -> None:
    result = _run(
        FakeAnalyzerModel(_draft_for),
        [_document("doc-1")],
        aggregator=ExplodingAggregator(),
    )

    assert result.aggregation_attempted is True
    assert result.aggregation_completed is False
    assert result.findings == []
    assert len(result.document_analyses) == 1
    assert len(result.evidence) == 1
    assert any("fake aggregation failure" in error for error in result.errors)


def test_pipeline_keeps_usable_evidence_after_one_document_analysis_failure() -> None:
    def handler(request: DocumentAnalysisRequest) -> DocumentAnalysisDraft | Exception:
        if request.document_id == "doc-1":
            return RuntimeError("fake document failure")
        return _draft_for(request)

    result = _run(
        FakeAnalyzerModel(handler),
        [_document("doc-1"), _document("doc-2")],
        config=AnalyzerConfig(retry_times=0, allow_partial_results=True),
    )

    assert [analysis.status for analysis in result.document_analyses] == ["failed", "analyzed"]
    assert result.analyzer_partial is True
    assert len(result.evidence) == 1
    assert len(result.findings) == 1
    assert any("fake document failure" in error for error in result.errors)


def test_pipeline_skips_invalid_documents_without_model_or_api_calls() -> None:
    model = FakeAnalyzerModel(lambda _: AssertionError("model must not be called"))

    result = _run(model, [_document("doc-invalid", status="invalid_source")])

    assert model.calls == []
    assert result.document_analyses[0].status == "skipped"
    assert result.evidence == []
    assert result.findings == []
    assert result.aggregation_attempted is False
