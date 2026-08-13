"""Fake-only tests for the LangChain Evidence AnalyzerModel adapter."""

import asyncio

import pytest

from src.evidence.drafts import DocumentAnalysisDraft, EvidenceDraft
from src.evidence.langchain_adapter import LangChainAnalyzerModel
from src.evidence.protocols import DocumentAnalysisRequest
from src.evidence.service import ResultAnalyzer
from src.state import Document


def _request() -> DocumentAnalysisRequest:
    return DocumentAnalysisRequest(
        topic="Research topic",
        objectives=["Establish the documented capability"],
        document_id="doc-1",
        source_url="https://example.com/source",
        title="Source title",
        source_text="The source explicitly supports the claim.",
        text_source="content",
        credibility={"score": 90},
    )


class FakeStructuredRunnable:
    def __init__(self, result: object | list[object]) -> None:
        self.result = result
        self.calls: list[object] = []

    async def ainvoke(self, messages: object) -> object:
        self.calls.append(messages)
        result = self.result.pop(0) if isinstance(self.result, list) else self.result
        if isinstance(result, Exception):
            raise result
        return result


class FakeRawMessage:
    def __init__(self, usage_metadata: dict[str, int]) -> None:
        self.usage_metadata = usage_metadata


class UnprintableResult:
    def __str__(self) -> str:
        raise RuntimeError("output cannot be serialized")


class FakeChatModel:
    def __init__(self, runnable: FakeStructuredRunnable) -> None:
        self.runnable = runnable
        self.schemas: list[object] = []
        self.structured_output_kwargs: list[dict[str, object]] = []

    def with_structured_output(
        self, schema: object, **kwargs: object
    ) -> FakeStructuredRunnable:
        self.schemas.append(schema)
        self.structured_output_kwargs.append(kwargs)
        return self.runnable


def _draft() -> DocumentAnalysisDraft:
    quote = "The source explicitly supports the claim."
    return DocumentAnalysisDraft(
        relevance_score=0.9,
        key_points=["The source is directly relevant."],
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


def test_adapter_uses_document_analysis_draft_structured_output_and_passes_source_text() -> None:
    runnable = FakeStructuredRunnable(_draft())
    model = FakeChatModel(runnable)
    adapter = LangChainAnalyzerModel(model)  # type: ignore[arg-type]

    result = asyncio.run(adapter.analyze_document(_request()))

    assert result == _draft()
    assert model.schemas == [DocumentAnalysisDraft]
    assert model.structured_output_kwargs == [
        {"method": "function_calling", "include_raw": True}
    ]
    assert len(runnable.calls) == 1
    messages = runnable.calls[0]
    assert isinstance(messages, list)
    assert messages[0]["role"] == "system"
    assert "Do not search the web" in messages[0]["content"]
    assert "Exact quote is required" in messages[0]["content"]
    assert "copied verbatim, character-for-character" in messages[0]["content"]
    assert "Offsets are optional" in messages[0]["content"]
    assert "zero-based" in messages[0]["content"]
    assert "end-exclusive" in messages[0]["content"]
    assert "source_start=null and source_end=null" in messages[0]["content"]
    assert "Never guess offsets" in messages[0]["content"]
    assert "Research topic:\nResearch topic" in messages[1]["content"]
    assert "Source text:\nThe source explicitly supports the claim." in messages[1]["content"]


def test_adapter_validates_mapping_structured_output() -> None:
    runnable = FakeStructuredRunnable({"raw": FakeRawMessage({}), "parsed": _draft().model_dump(), "parsing_error": None})
    adapter = LangChainAnalyzerModel(FakeChatModel(runnable))  # type: ignore[arg-type]

    result = asyncio.run(adapter.analyze_document(_request()))

    assert isinstance(result, DocumentAnalysisDraft)
    assert result.relevance_score == 0.9


def test_adapter_exposes_structured_parse_failure() -> None:
    runnable = FakeStructuredRunnable(
        {"raw": FakeRawMessage({}), "parsed": None, "parsing_error": ValueError("fake parsing failure")}
    )
    adapter = LangChainAnalyzerModel(FakeChatModel(runnable))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="fake parsing failure"):
        asyncio.run(adapter.analyze_document(_request()))


@pytest.mark.parametrize(
    "result",
    [
        {"raw": FakeRawMessage({}), "parsed": None, "parsing_error": None},
        {"raw": FakeRawMessage({}), "parsed": {"relevance_score": "not-a-number"}, "parsing_error": None},
    ],
)
def test_adapter_rejects_missing_or_invalid_parsed_structured_output(result: object) -> None:
    adapter = LangChainAnalyzerModel(FakeChatModel(FakeStructuredRunnable(result)))  # type: ignore[arg-type]

    with pytest.raises(Exception):
        asyncio.run(adapter.analyze_document(_request()))

    assert adapter.call_records[0].success is False


def test_adapter_exposes_model_invocation_failure() -> None:
    adapter = LangChainAnalyzerModel(
        FakeChatModel(FakeStructuredRunnable(RuntimeError("fake provider failure")))  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="fake provider failure"):
        asyncio.run(adapter.analyze_document(_request()))


def test_adapter_records_provider_usage_when_structured_output_exposes_raw_metadata() -> None:
    runnable = FakeStructuredRunnable(
        {
            "raw": FakeRawMessage({"input_tokens": 12, "output_tokens": 7}),
            "parsed": _draft(),
            "parsing_error": None,
        }
    )
    adapter = LangChainAnalyzerModel(FakeChatModel(runnable), model_name="fake-model")  # type: ignore[arg-type]

    asyncio.run(adapter.analyze_document(_request()))

    assert adapter.call_records[0].model == "fake-model"
    assert adapter.call_records[0].input_tokens == 12
    assert adapter.call_records[0].output_tokens == 7
    assert adapter.call_records[0].token_source == "provider"
    assert adapter.call_records[0].success is True


def test_adapter_records_estimated_tokens_for_structured_parse_and_model_failures() -> None:
    parse_adapter = LangChainAnalyzerModel(
        FakeChatModel(
            FakeStructuredRunnable(
                {"raw": FakeRawMessage({}), "parsed": {"relevance_score": "not-a-number"}, "parsing_error": None}
            )
        )  # type: ignore[arg-type]
    )
    failure_adapter = LangChainAnalyzerModel(
        FakeChatModel(FakeStructuredRunnable(RuntimeError("fake provider failure")))  # type: ignore[arg-type]
    )

    with pytest.raises(Exception):
        asyncio.run(parse_adapter.analyze_document(_request()))
    with pytest.raises(RuntimeError):
        asyncio.run(failure_adapter.analyze_document(_request()))

    assert parse_adapter.call_records[0].success is False
    assert parse_adapter.call_records[0].token_source == "estimated"
    assert parse_adapter.call_records[0].input_tokens > 0
    assert failure_adapter.call_records[0].success is False
    assert failure_adapter.call_records[0].token_source == "estimated"


def test_adapter_marks_tokens_unavailable_only_when_estimation_is_impossible() -> None:
    adapter = LangChainAnalyzerModel(FakeChatModel(FakeStructuredRunnable(UnprintableResult())))  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        asyncio.run(adapter.analyze_document(_request()))

    assert adapter.call_records[0].success is False
    assert adapter.call_records[0].input_tokens == 0
    assert adapter.call_records[0].output_tokens == 0
    assert adapter.call_records[0].token_source == "unavailable"


def test_result_analyzer_accepts_adapter_and_builds_grounded_evidence() -> None:
    adapter = LangChainAnalyzerModel(
        FakeChatModel(FakeStructuredRunnable({"raw": FakeRawMessage({}), "parsed": _draft(), "parsing_error": None}))
    )  # type: ignore[arg-type]
    analyzer = ResultAnalyzer(adapter)
    document = Document(
        document_id="doc-1",
        title="Source title",
        uri="https://example.com/source",
        content="The source explicitly supports the claim.",
        credibility={"score": 90},
    )

    result = asyncio.run(analyzer.analyze(topic="Research topic", documents=[document]))

    assert result.completed is True
    assert result.partial is False
    assert result.analyses[0].status == "analyzed"
    assert len(result.evidence) == 1
    assert result.evidence[0].source_quote == "The source explicitly supports the claim."


def test_result_analyzer_uses_runtime_span_for_adapter_draft_with_wrong_offsets() -> None:
    draft = _draft()
    draft.evidence[0].source_end = 1
    adapter = LangChainAnalyzerModel(
        FakeChatModel(FakeStructuredRunnable({"raw": FakeRawMessage({}), "parsed": draft, "parsing_error": None}))
    )  # type: ignore[arg-type]
    source_text = "The source explicitly supports the claim."
    document = Document(document_id="doc-1", title="Source title", uri="https://example.com/source", content=source_text)

    result = asyncio.run(ResultAnalyzer(adapter).analyze(topic="Research topic", documents=[document]))

    assert len(result.evidence) == 1
    assert result.evidence[0].source_start == 0
    assert result.evidence[0].source_end == len(source_text)


def test_result_analyzer_retry_records_each_adapter_invocation() -> None:
    adapter = LangChainAnalyzerModel(
        FakeChatModel(
            FakeStructuredRunnable(
                [
                    RuntimeError("first provider failure"),
                    {"raw": FakeRawMessage({}), "parsed": _draft(), "parsing_error": None},
                ]
            )
        )  # type: ignore[arg-type]
    )
    analyzer = ResultAnalyzer(adapter, config=__import__("src.evidence.config", fromlist=["AnalyzerConfig"]).AnalyzerConfig(retry_times=1))
    document = Document(
        document_id="doc-1",
        title="Source title",
        uri="https://example.com/source",
        content="The source explicitly supports the claim.",
    )

    result = asyncio.run(analyzer.analyze(topic="Research topic", documents=[document]))

    assert result.completed is True
    assert [record.success for record in adapter.call_records] == [False, True]
