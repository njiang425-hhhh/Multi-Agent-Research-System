"""Production composition and failure isolation for the optional Evidence sidecar."""

from collections.abc import Sequence
from typing import Optional, Protocol

from pydantic import BaseModel, Field

from src.evidence.adapters import search_result_to_document
from src.evidence.config import EvidenceRuntimeConfig
from src.evidence.contracts import DocumentAnalysis, Evidence
from src.evidence.langchain_adapter import LangChainAnalyzerModel
from src.evidence.pipeline import EvidencePipeline, EvidencePipelineResult
from src.evidence.protocols import AnalyzerModel
from src.config import config as project_config
from src.llm.factory import get_llm
from src.state import Document, EvidenceDiagnostics, Finding, SearchResult


class PipelineRuntime(Protocol):
    async def run(
        self,
        *,
        topic: str,
        documents: Sequence[Document],
        objectives: Sequence[str] = (),
    ) -> EvidencePipelineResult:
        """Run the standalone evidence runtime."""


class EvidenceSidecarResult(BaseModel):
    """Serializable sidecar output that callers may choose to write into State."""

    documents: list[Document] = Field(default_factory=list)
    document_analyses: list[DocumentAnalysis] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    diagnostics: EvidenceDiagnostics = Field(default_factory=EvidenceDiagnostics)
    llm_calls_delta: int = 0
    input_tokens_delta: int = 0
    output_tokens_delta: int = 0
    llm_call_details: list[dict[str, object]] = Field(default_factory=list)


class EvidenceSidecarService:
    """Run EvidencePipeline without knowledge of Graph or ResearchState updates."""

    def __init__(
        self,
        *,
        config: Optional[EvidenceRuntimeConfig] = None,
        model: Optional[AnalyzerModel] = None,
        pipeline: Optional[PipelineRuntime] = None,
        initialization_error: Optional[str] = None,
    ) -> None:
        self.config = config or EvidenceRuntimeConfig.from_environment()
        self._initialization_error = initialization_error
        self._tracking_model = model
        if pipeline is not None:
            self._pipeline: Optional[PipelineRuntime] = pipeline
        elif model is not None:
            self._pipeline = EvidencePipeline(model, config=self.config.analyzer)
        else:
            self._pipeline = None

    @classmethod
    def create_production(
        cls,
        *,
        config: Optional[EvidenceRuntimeConfig] = None,
    ) -> "EvidenceSidecarService":
        """Create the optional production runtime without provider-specific branches."""
        runtime_config = config or EvidenceRuntimeConfig.from_environment()
        if not runtime_config.enabled:
            return cls(config=runtime_config)
        try:
            model = LangChainAnalyzerModel(
                get_llm(
                    temperature=0.0,
                    model_override=project_config.summarization_model,
                    extra_body={"thinking": {"type": "disabled"}},
                )
            )
            return cls(config=runtime_config, model=model)
        except Exception as exc:
            return cls(config=runtime_config, initialization_error=str(exc))

    async def run(
        self,
        *,
        topic: str,
        documents: Sequence[Document],
        search_results: Sequence[SearchResult] = (),
        objectives: Sequence[str] = (),
    ) -> EvidenceSidecarResult:
        """Return isolated sidecar output without updating any ResearchState."""
        effective_documents, source = self._effective_documents(documents, search_results)
        tracking_start = self._tracking_record_count()
        if not self.config.enabled:
            return self._with_tracking(EvidenceSidecarResult(
                documents=effective_documents,
                diagnostics=EvidenceDiagnostics(status="disabled", source=source),
            ), tracking_start)
        if not effective_documents:
            return self._with_tracking(EvidenceSidecarResult(
                diagnostics=EvidenceDiagnostics(status="not_run", source="none"),
            ), tracking_start)
        if self._initialization_error is not None:
            return self._with_tracking(EvidenceSidecarResult(
                documents=effective_documents,
                diagnostics=EvidenceDiagnostics(
                    status="failed",
                    source=source,
                    sidecar_errors=[self._initialization_error],
                ),
            ), tracking_start)
        if self._pipeline is None:
            return self._with_tracking(EvidenceSidecarResult(
                documents=effective_documents,
                diagnostics=EvidenceDiagnostics(
                    status="failed",
                    source=source,
                    sidecar_errors=["Evidence sidecar has no configured pipeline"],
                ),
            ), tracking_start)

        try:
            result = await self._pipeline.run(
                topic=topic,
                documents=effective_documents,
                objectives=objectives,
            )
        except Exception as exc:
            return self._with_tracking(EvidenceSidecarResult(
                documents=effective_documents,
                diagnostics=EvidenceDiagnostics(
                    status="failed",
                    source=source,
                    sidecar_errors=[f"Evidence sidecar runtime failed: {exc}"],
                ),
            ), tracking_start)

        diagnostics = self._diagnostics(result, source)
        return self._with_tracking(EvidenceSidecarResult(
            documents=effective_documents,
            document_analyses=result.document_analyses,
            evidence=result.evidence,
            findings=result.findings,
            diagnostics=diagnostics,
        ), tracking_start)

    def _tracking_record_count(self) -> int:
        records = getattr(self._tracking_model, "call_records", ())
        return len(records) if isinstance(records, list) else 0

    def _with_tracking(
        self,
        result: EvidenceSidecarResult,
        start: int,
    ) -> EvidenceSidecarResult:
        records = getattr(self._tracking_model, "call_records", ())
        if not isinstance(records, list):
            return result
        details = [
            record.model_dump() if isinstance(record, BaseModel) else dict(record)
            for record in records[start:]
        ]
        result.llm_call_details = details
        result.llm_calls_delta = len(details)
        result.input_tokens_delta = sum(
            detail.get("input_tokens", 0)
            for detail in details
            if isinstance(detail.get("input_tokens", 0), int)
        )
        result.output_tokens_delta = sum(
            detail.get("output_tokens", 0)
            for detail in details
            if isinstance(detail.get("output_tokens", 0), int)
        )
        return result

    @staticmethod
    def _effective_documents(
        documents: Sequence[Document],
        search_results: Sequence[SearchResult],
    ) -> tuple[list[Document], str]:
        if documents:
            return list(documents), "p2_documents"
        if search_results:
            return (
                [search_result_to_document(result, credibility=None) for result in search_results],
                "legacy_search_results_backfill",
            )
        return [], "none"

    @staticmethod
    def _diagnostics(
        result: EvidencePipelineResult,
        source: str,
    ) -> EvidenceDiagnostics:
        analyzer_errors = list(result.analysis_result.errors)
        aggregation_errors: list[str] = []
        if result.aggregation_result is not None:
            aggregation_errors.extend(result.aggregation_result.errors)
        aggregation_errors.extend(
            error for error in result.errors if error.startswith("Finding aggregation")
        )
        if result.aggregation_attempted and result.aggregation_result is None:
            status = "failed"
        elif result.analyzer_completed and not result.analyzer_partial and not result.aggregation_partial:
            status = "completed"
        else:
            status = "partial"
        return EvidenceDiagnostics(
            status=status,
            source=source,
            analyzer_completed=result.analyzer_completed,
            analyzer_partial=result.analyzer_partial,
            aggregation_attempted=result.aggregation_attempted,
            aggregation_completed=result.aggregation_completed,
            aggregation_partial=result.aggregation_partial,
            analyzer_errors=analyzer_errors,
            aggregation_errors=aggregation_errors,
        )
