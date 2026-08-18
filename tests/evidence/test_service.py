"""Tests for ResultAnalyzer using an injected fake analyzer model."""

import asyncio
import inspect
from collections import defaultdict
from collections.abc import Callable
from typing import Any

import pytest

from src.evidence.config import AnalyzerConfig
from src.evidence.drafts import DocumentAnalysisDraft, EvidenceDraft
from src.evidence.protocols import DocumentAnalysisRequest
from src.evidence.service import ResultAnalyzer
from src.runtime_control import RunPolicy, create_execution_context
from src.state import Document


class FakeAnalyzerModel:
    """Configurable analyzer-model fake with no client or network dependency."""

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


def _document(document_id: str, **overrides: object) -> Document:
    values: dict[str, object] = {
        "document_id": document_id,
        "title": f"Title for {document_id}",
        "uri": f"https://example.com/{document_id}",
        "snippet": "Snippet evidence.",
        "content": "The primary source supports the claim.",
        "status": "retrieved",
    }
    values.update(overrides)
    return Document(**values)


def _draft_with_evidence(
    source_quote: str,
    start: int | None = None,
    end: int | None = None,
) -> DocumentAnalysisDraft:
    return DocumentAnalysisDraft(
        relevance_score=0.9,
        key_points=["The source is directly relevant."],
        evidence=[
            EvidenceDraft(
                claim="The source supports the claim.",
                source_quote=source_quote,
                source_start=start,
                source_end=end,
                relation="supports",
                strength=0.8,
            )
        ],
    )


def test_analyzer_config_rejects_invalid_runtime_limits() -> None:
    with pytest.raises(ValueError, match="max_chars_per_document"):
        AnalyzerConfig(max_chars_per_document=0)

    with pytest.raises(ValueError, match="retry_times"):
        AnalyzerConfig(retry_times=-1)


def test_result_analyzer_builds_grounded_evidence_from_content() -> None:
    source_text = "The primary source supports the claim."
    model = FakeAnalyzerModel(
        lambda _: _draft_with_evidence(source_text, 0, len(source_text))
    )
    analyzer = ResultAnalyzer(model)
    document = _document("doc-1", content=source_text, credibility={"score": 88})

    result = asyncio.run(analyzer.analyze(topic="Research topic", documents=[document]))

    assert model.calls[0].text_source == "content"
    assert model.calls[0].source_text == source_text
    assert result.completed is True
    assert result.partial is False
    assert result.analyses[0].credibility_score == 88.0
    assert result.analyses[0].status == "analyzed"
    assert len(result.evidence) == 1
    assert result.evidence[0].evidence_id.startswith("evidence:")
    assert result.evidence[0].status == "grounded"
    assert result.analyses[0].evidence_ids == [result.evidence[0].evidence_id]


def test_result_analyzer_grounds_unique_quote_with_runtime_offsets_regardless_of_draft_offsets() -> None:
    source_text = "Before. The primary source supports the claim. After."
    quote = "The primary source supports the claim."
    expected_start = source_text.index(quote)
    expected_end = expected_start + len(quote)
    document = _document("doc-1", content=source_text)
    drafts = [
        _draft_with_evidence(quote, expected_start, expected_end),
        _draft_with_evidence(quote, 0, 1),
        _draft_with_evidence(quote),
    ]

    evidence = [
        asyncio.run(
            ResultAnalyzer(FakeAnalyzerModel(lambda _, draft=draft: draft)).analyze(
                topic="Research topic", documents=[document]
            )
        ).evidence[0]
        for draft in drafts
    ]

    assert [(item.source_start, item.source_end) for item in evidence] == [
        (expected_start, expected_end),
        (expected_start, expected_end),
        (expected_start, expected_end),
    ]
    assert len({item.evidence_id for item in evidence}) == 1


def test_result_analyzer_uses_snippet_when_content_is_missing() -> None:
    snippet = "Snippet evidence."
    model = FakeAnalyzerModel(lambda _: _draft_with_evidence(snippet, 0, len(snippet)))
    analyzer = ResultAnalyzer(model)
    document = _document("doc-1", content=None, snippet=snippet)

    result = asyncio.run(analyzer.analyze(topic="Research topic", documents=[document]))

    assert model.calls[0].text_source == "snippet"
    assert model.calls[0].source_text == snippet
    assert result.completed is False
    assert result.partial is True
    assert result.analyses[0].status == "partial"
    assert result.evidence[0].status == "partial"


def test_result_analyzer_truncates_content_before_fake_model_invocation() -> None:
    content = "1234567890abcdef"
    model = FakeAnalyzerModel(lambda _: DocumentAnalysisDraft(relevance_score=0.4))
    analyzer = ResultAnalyzer(model, AnalyzerConfig(max_chars_per_document=10))

    asyncio.run(analyzer.analyze(topic="Research topic", documents=[_document("doc-1", content=content)]))

    assert model.calls[0].source_text == "1234567890"


def test_result_analyzer_skips_invalid_or_textless_documents_without_model_calls() -> None:
    model = FakeAnalyzerModel(lambda _: pytest.fail("model must not be called"))
    analyzer = ResultAnalyzer(model)
    invalid_document = _document("doc-invalid", status="invalid_source")
    textless_document = _document("doc-empty", content=None, snippet="")

    result = asyncio.run(
        analyzer.analyze(
            topic="Research topic",
            documents=[invalid_document, textless_document],
        )
    )

    assert model.calls == []
    assert [analysis.status for analysis in result.analyses] == ["skipped", "skipped"]
    assert result.completed is False
    assert result.partial is True
    assert len(result.errors) == 2


def test_result_analyzer_marks_unverifiable_quotes_as_partial() -> None:
    model = FakeAnalyzerModel(lambda _: _draft_with_evidence("not in the source", 0, 17))
    analyzer = ResultAnalyzer(model)

    result = asyncio.run(analyzer.analyze(topic="Research topic", documents=[_document("doc-1")]))

    assert result.evidence == []
    assert result.analyses[0].evidence_ids == []
    assert result.analyses[0].status == "partial"
    assert result.partial is True
    assert "could not be uniquely validated" in result.errors[0]


@pytest.mark.parametrize(
    "quote, source_text",
    [
        ("The source supports the claim.", "The source supports the claim. The source supports the claim."),
        ("The source  supports the claim.", "The source supports the claim."),
        ("The source supports the claim.", "the source supports the claim."),
        ("Café supports the claim.", "Cafe\u0301 supports the claim."),
    ],
)
def test_result_analyzer_rejects_non_unique_or_non_exact_quotes_even_with_draft_offsets(
    quote: str,
    source_text: str,
) -> None:
    model = FakeAnalyzerModel(lambda _: _draft_with_evidence(quote, 0, len(quote)))

    result = asyncio.run(
        ResultAnalyzer(model).analyze(
            topic="Research topic", documents=[_document("doc-1", content=source_text)]
        )
    )

    assert result.evidence == []
    assert result.analyses[0].status == "partial"
    assert "could not be uniquely validated" in result.errors[0]


def test_result_analyzer_retries_a_failed_document_and_continues_to_the_next() -> None:
    attempts: dict[str, int] = defaultdict(int)

    def handler(request: DocumentAnalysisRequest) -> DocumentAnalysisDraft | Exception:
        attempts[request.document_id] += 1
        if request.document_id == "doc-1" and attempts[request.document_id] == 1:
            return RuntimeError("transient fake failure")
        return DocumentAnalysisDraft(relevance_score=0.5)

    model = FakeAnalyzerModel(handler)
    analyzer = ResultAnalyzer(model, AnalyzerConfig(retry_times=1))

    result = asyncio.run(
        analyzer.analyze(
            topic="Research topic",
            documents=[_document("doc-1"), _document("doc-2")],
        )
    )

    assert attempts == {"doc-1": 2, "doc-2": 1}
    assert [analysis.status for analysis in result.analyses] == ["analyzed", "analyzed"]
    assert result.completed is True
    assert result.partial is False


def test_result_analyzer_keeps_later_document_output_after_unrecoverable_failure() -> None:
    def handler(request: DocumentAnalysisRequest) -> DocumentAnalysisDraft | Exception:
        if request.document_id == "doc-1":
            return RuntimeError("permanent fake failure")
        return DocumentAnalysisDraft(relevance_score=0.5)

    model = FakeAnalyzerModel(handler)
    analyzer = ResultAnalyzer(model, AnalyzerConfig(retry_times=1))

    result = asyncio.run(
        analyzer.analyze(
            topic="Research topic",
            documents=[_document("doc-1"), _document("doc-2")],
        )
    )

    assert [call.document_id for call in model.calls] == ["doc-1", "doc-1", "doc-2"]
    assert [analysis.status for analysis in result.analyses] == ["failed", "analyzed"]
    assert result.completed is False
    assert result.partial is True
    assert "doc-1: Document analysis failed after retries" in result.errors[0]


def test_result_analyzer_returns_partial_output_when_document_limit_is_reached() -> None:
    model = FakeAnalyzerModel(lambda _: DocumentAnalysisDraft(relevance_score=0.5))
    analyzer = ResultAnalyzer(model, AnalyzerConfig(max_documents=1))

    result = asyncio.run(
        analyzer.analyze(
            topic="Research topic",
            documents=[_document("doc-1"), _document("doc-2")],
        )
    )

    assert [document.document_id for document in result.documents] == ["doc-1"]
    assert [call.document_id for call in model.calls] == ["doc-1"]
    assert result.completed is False
    assert result.partial is True
    assert "Document limit reached" in result.errors[0]


def test_result_analyzer_stops_after_total_timeout_and_respects_partial_policy() -> None:
    async def slow_handler(_: DocumentAnalysisRequest) -> DocumentAnalysisDraft:
        await asyncio.sleep(0.2)
        return DocumentAnalysisDraft(relevance_score=0.5)

    model = FakeAnalyzerModel(slow_handler)
    analyzer = ResultAnalyzer(
        model,
        AnalyzerConfig(total_timeout_seconds=0.05, allow_partial_results=False),
    )

    result = asyncio.run(
        analyzer.analyze(
            topic="Research topic",
            documents=[_document("doc-1"), _document("doc-2")],
        )
    )

    assert [call.document_id for call in model.calls] == ["doc-1"]
    assert result.analyses == []
    assert result.completed is False
    assert result.partial is False
    assert any("timeout" in error.lower() for error in result.errors)


def test_result_analyzer_preserves_evidence_isolation_when_runtime_budget_is_exhausted() -> None:
    model = FakeAnalyzerModel(lambda _: DocumentAnalysisDraft(relevance_score=0.5))
    context = create_execution_context(
        run_id="evidence-budget",
        thread_id="thread-evidence-budget",
        policy=RunPolicy(max_operation_calls=1),
    )

    result = asyncio.run(
        ResultAnalyzer(model).analyze(
            topic="Research topic",
            documents=[_document("doc-1"), _document("doc-2")],
            execution_context=context,
        )
    )

    assert [call.document_id for call in model.calls] == ["doc-1"]
    assert result.completed is False
    assert result.partial is True
    assert "run operation budget exhausted" in result.errors[-1]
    assert result.execution_context is not None
    assert result.execution_context.operation_stop_reason == "budget_exhausted"
