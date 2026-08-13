"""Fake-only tests for Evidence State contracts and the isolated sidecar service."""

import asyncio
import inspect
from collections.abc import Callable
from typing import Any

from src.evidence.config import AnalyzerConfig, EvidenceRuntimeConfig
from src.evidence.contracts import DocumentAnalysis, Evidence
from src.evidence.drafts import DocumentAnalysisDraft, EvidenceDraft
from src.evidence.finding_models import FindingAggregationResult
from src.evidence.models import DocumentAnalysis as ReExportedDocumentAnalysis
from src.evidence.models import Evidence as ReExportedEvidence
from src.evidence.pipeline import EvidencePipeline, EvidencePipelineResult
from src.evidence.protocols import DocumentAnalysisRequest
from src.evidence.sidecar import EvidenceSidecarService
import src.evidence.sidecar as sidecar_module
from src.evidence.langchain_adapter import LangChainAnalyzerModel
from src.state import Document, EvidenceDiagnostics, ResearchState, SearchResult


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


class ExplodingPipeline:
    async def run(self, **_kwargs: object) -> EvidencePipelineResult:
        raise RuntimeError("fake pipeline runtime failure")


class FakeStructuredRunnable:
    def __init__(self, handler: Callable[[object], object]) -> None:
        self.handler = handler

    async def ainvoke(self, messages: object) -> object:
        result = self.handler(messages)
        if isinstance(result, Exception):
            raise result
        return {"raw": FakeRawMessage(), "parsed": result, "parsing_error": None}


class FakeRawMessage:
    usage_metadata: dict[str, int] = {}


class ProductionFakeChatModel:
    def with_structured_output(self, _schema: object, **_kwargs: object) -> object:
        return object()


class FakeChatModel:
    def __init__(self, runnable: FakeStructuredRunnable) -> None:
        self.runnable = runnable
        self.model_name = "fake-evidence-model"

    def with_structured_output(
        self, _schema: object, **_kwargs: object
    ) -> FakeStructuredRunnable:
        return self.runnable


def _document(document_id: str = "doc-1", **overrides: object) -> Document:
    values: dict[str, object] = {
        "document_id": document_id,
        "title": f"Title {document_id}",
        "uri": f"https://example.com/{document_id}",
        "content": "The source supports the claim.",
        "snippet": "The source supports the claim.",
        "credibility": {"score": 85},
        "status": "retrieved",
    }
    values.update(overrides)
    return Document(**values)


def _search_result() -> SearchResult:
    return SearchResult(
        query="research topic",
        title="Legacy source",
        url="https://example.com/legacy",
        snippet="The source supports the claim.",
        content="The source supports the claim.",
    )


def _draft(request: DocumentAnalysisRequest) -> DocumentAnalysisDraft:
    quote = "The source supports the claim."
    return DocumentAnalysisDraft(
        relevance_score=0.8,
        key_points=[f"Point for {request.document_id}"],
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


def _service(
    model: FakeAnalyzerModel | None = None,
    *,
    allow_partial: bool = True,
    aggregator: object | None = None,
    pipeline: object | None = None,
    initialization_error: str | None = None,
) -> EvidenceSidecarService:
    runtime = EvidenceRuntimeConfig(
        enabled=True,
        analyzer=AnalyzerConfig(retry_times=0, allow_partial_results=allow_partial),
    )
    if pipeline is not None:
        return EvidenceSidecarService(
            config=runtime,
            pipeline=pipeline,  # type: ignore[arg-type]
            initialization_error=initialization_error,
        )
    if aggregator is not None:
        return EvidenceSidecarService(
            config=runtime,
            model=model,
            pipeline=EvidencePipeline(model, config=runtime.analyzer, aggregator=aggregator),  # type: ignore[arg-type]
            initialization_error=initialization_error,
        )
    return EvidenceSidecarService(config=runtime, model=model, initialization_error=initialization_error)


def _run(service: EvidenceSidecarService, **kwargs: object):
    return asyncio.run(
        service.run(
            topic="Research topic",
            documents=kwargs.get("documents", []),  # type: ignore[arg-type]
            search_results=kwargs.get("search_results", []),  # type: ignore[arg-type]
            objectives=["Test objective"],
        )
    )


def test_contracts_are_reexported_from_models_for_existing_imports() -> None:
    assert Evidence is ReExportedEvidence
    assert DocumentAnalysis is ReExportedDocumentAnalysis


def test_research_state_has_minimal_evidence_defaults_and_old_payload_loads() -> None:
    state = ResearchState(research_topic="topic")
    assert state.document_analyses == []
    assert state.evidence == []
    assert state.evidence_diagnostics == EvidenceDiagnostics()

    old_payload = {"research_topic": "legacy topic", "key_findings": ["legacy finding"]}
    restored = ResearchState.model_validate(old_payload)
    assert restored.document_analyses == []
    assert restored.evidence == []
    assert restored.evidence_diagnostics.status == "not_run"


def test_evidence_flag_defaults_to_disabled(monkeypatch) -> None:
    for name in (
        "EVIDENCE_ANALYZER_ENABLED",
        "EVIDENCE_MAX_DOCUMENTS",
        "EVIDENCE_MAX_CHARS_PER_DOCUMENT",
        "EVIDENCE_TOTAL_TIMEOUT_SECONDS",
        "EVIDENCE_RETRY_TIMES",
        "EVIDENCE_ALLOW_PARTIAL_RESULTS",
    ):
        monkeypatch.delenv(name, raising=False)

    config = EvidenceRuntimeConfig.from_environment()

    assert config.enabled is False
    assert config.analyzer == AnalyzerConfig()


def test_disabled_sidecar_returns_diagnostics_without_initializing_or_running_pipeline() -> None:
    service = EvidenceSidecarService(config=EvidenceRuntimeConfig(enabled=False))
    document = _document()

    result = _run(service, documents=[document])

    assert result.documents == [document]
    assert result.document_analyses == []
    assert result.evidence == []
    assert result.findings == []
    assert result.diagnostics.status == "disabled"
    assert result.diagnostics.source == "p2_documents"
    assert result.llm_calls_delta == 0
    assert result.input_tokens_delta == 0
    assert result.output_tokens_delta == 0
    assert result.llm_call_details == []


def test_production_sidecar_creates_only_evidence_llm_with_non_thinking_extra_body(
    monkeypatch,
) -> None:
    captured: list[dict[str, object]] = []

    def fake_get_llm(**kwargs: object) -> ProductionFakeChatModel:
        captured.append(dict(kwargs))
        return ProductionFakeChatModel()

    monkeypatch.setattr(sidecar_module, "get_llm", fake_get_llm)
    runtime = EvidenceRuntimeConfig(enabled=True)

    service = EvidenceSidecarService.create_production(config=runtime)

    assert service._initialization_error is None
    assert captured == [
        {
            "temperature": 0.0,
            "model_override": sidecar_module.project_config.summarization_model,
            "extra_body": {"thinking": {"type": "disabled"}},
        }
    ]


def test_sidecar_returns_complete_analysis_evidence_and_evidence_backed_findings() -> None:
    model = FakeAnalyzerModel(_draft)
    result = _run(_service(model), documents=[_document("doc-1"), _document("doc-2")])

    assert result.diagnostics.status == "completed"
    assert result.diagnostics.analyzer_completed is True
    assert result.diagnostics.aggregation_completed is True
    assert len(result.document_analyses) == 2
    assert len(result.evidence) == 2
    assert len(result.findings) == 1
    assert result.findings[0].evidence_refs == [item.evidence_id for item in result.evidence]


def test_sidecar_records_partial_analyzer_diagnostics_and_keeps_usable_output() -> None:
    def handler(request: DocumentAnalysisRequest) -> DocumentAnalysisDraft | Exception:
        if request.document_id == "doc-1":
            return RuntimeError("fake analyzer failure")
        return _draft(request)

    result = _run(_service(FakeAnalyzerModel(handler)), documents=[_document("doc-1"), _document("doc-2")])

    assert result.diagnostics.status == "partial"
    assert result.diagnostics.analyzer_partial is True
    assert result.diagnostics.aggregation_completed is True
    assert len(result.evidence) == 1
    assert len(result.findings) == 1
    assert any("fake analyzer failure" in error for error in result.diagnostics.analyzer_errors)
    assert result.diagnostics.aggregation_errors == []


def test_sidecar_classifies_aggregator_failures_without_losing_analysis() -> None:
    result = _run(
        _service(FakeAnalyzerModel(_draft), aggregator=ExplodingAggregator()),
        documents=[_document()],
    )

    assert result.diagnostics.status == "failed"
    assert result.diagnostics.aggregation_attempted is True
    assert len(result.document_analyses) == 1
    assert len(result.evidence) == 1
    assert result.findings == []
    assert any("fake aggregation failure" in error for error in result.diagnostics.aggregation_errors)
    assert result.diagnostics.analyzer_errors == []


def test_sidecar_isolates_initialization_and_runtime_failures() -> None:
    initialization_failure = _run(
        _service(initialization_error="fake construction failure"),
        documents=[_document()],
    )
    runtime_failure = _run(
        _service(pipeline=ExplodingPipeline()),
        documents=[_document()],
    )

    assert initialization_failure.diagnostics.status == "failed"
    assert initialization_failure.diagnostics.sidecar_errors == ["fake construction failure"]
    assert runtime_failure.diagnostics.status == "failed"
    assert any("fake pipeline runtime failure" in error for error in runtime_failure.diagnostics.sidecar_errors)


def test_sidecar_backfills_legacy_search_results_without_credibility_binding() -> None:
    model = FakeAnalyzerModel(_draft)
    result = _run(_service(model), documents=[], search_results=[_search_result()])

    assert result.diagnostics.source == "legacy_search_results_backfill"
    assert len(result.documents) == 1
    assert result.documents[0].credibility is None
    assert result.documents[0].uri == "https://example.com/legacy"
    assert len(result.findings) == 1


def test_sidecar_safely_skips_when_no_documents_or_legacy_search_results_exist() -> None:
    result = _run(_service(FakeAnalyzerModel(_draft)), documents=[], search_results=[])

    assert result.documents == []
    assert result.document_analyses == []
    assert result.evidence == []
    assert result.findings == []
    assert result.diagnostics.status == "not_run"
    assert result.diagnostics.source == "none"


def test_sidecar_does_not_modify_a_research_state_instance() -> None:
    state = ResearchState(
        research_topic="topic",
        documents=[_document()],
        search_results=[_search_result()],
    )
    before = state.model_dump()

    _run(_service(FakeAnalyzerModel(_draft)), documents=state.documents, search_results=state.search_results)

    assert state.model_dump() == before


def test_sidecar_sums_adapter_tracking_without_counting_aggregator() -> None:
    adapter = LangChainAnalyzerModel(
        FakeChatModel(FakeStructuredRunnable(lambda _messages: _draft_from_messages())),  # type: ignore[arg-type]
        model_name="fake-evidence-model",
    )
    result = _run(_service(adapter), documents=[_document("doc-1"), _document("doc-2")])

    assert result.llm_calls_delta == 2
    assert len(result.llm_call_details) == 2
    assert result.input_tokens_delta == sum(
        detail["input_tokens"] for detail in result.llm_call_details
    )
    assert result.output_tokens_delta == sum(
        detail["output_tokens"] for detail in result.llm_call_details
    )
    assert all(detail["agent"] == "EvidenceAnalyzer" for detail in result.llm_call_details)
    assert all(detail["operation"] == "analyze_document" for detail in result.llm_call_details)
    assert all(detail["token_source"] == "estimated" for detail in result.llm_call_details)


def test_sidecar_keeps_retry_and_partial_adapter_tracking_after_fallback_paths() -> None:
    calls = {"count": 0}

    def flaky_handler(_messages: object) -> object:
        calls["count"] += 1
        if calls["count"] == 1:
            return RuntimeError("fake first attempt failure")
        return _draft_from_messages()

    adapter = LangChainAnalyzerModel(FakeChatModel(FakeStructuredRunnable(flaky_handler)))  # type: ignore[arg-type]
    retry_config = EvidenceRuntimeConfig(
        enabled=True,
        analyzer=AnalyzerConfig(retry_times=1, allow_partial_results=True),
    )
    result = _run(
        EvidenceSidecarService(config=retry_config, model=adapter),
        documents=[_document()],
    )

    assert result.diagnostics.status == "completed"
    assert result.llm_calls_delta == 2
    assert [detail["success"] for detail in result.llm_call_details] == [False, True]
    assert "fake first attempt failure" in result.llm_call_details[0]["error"]


def test_sidecar_preserves_analyzer_tracking_when_aggregator_fails() -> None:
    adapter = LangChainAnalyzerModel(
        FakeChatModel(FakeStructuredRunnable(lambda _messages: _draft_from_messages()))  # type: ignore[arg-type]
    )
    result = _run(
        _service(adapter, aggregator=ExplodingAggregator()),
        documents=[_document()],
    )

    assert result.diagnostics.status == "failed"
    assert result.llm_calls_delta == 1
    assert result.llm_call_details[0]["success"] is True


def _draft_from_messages() -> DocumentAnalysisDraft:
    quote = "The source supports the claim."
    return DocumentAnalysisDraft(
        relevance_score=0.8,
        evidence=[
            EvidenceDraft(
                claim="The source supports the claim.",
                source_quote=quote,
                source_start=0,
                source_end=len(quote),
                relation="supports",
            )
        ],
    )
