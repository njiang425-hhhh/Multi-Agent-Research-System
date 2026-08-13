"""Standalone runtime that composes Evidence analysis and deterministic aggregation."""

from collections.abc import Sequence
from typing import Optional, Protocol

from pydantic import BaseModel, Field

from src.evidence.aggregation import RuleBasedFindingAggregator
from src.evidence.config import AnalyzerConfig
from src.evidence.finding_models import FindingAggregationResult
from src.evidence.models import AnalysisResult, DocumentAnalysis, Evidence
from src.evidence.protocols import AnalyzerModel
from src.evidence.service import ResultAnalyzer
from src.state import Document, Finding


class FindingAggregator(Protocol):
    """Minimal deterministic aggregation boundary used for dependency injection."""

    def aggregate(
        self,
        *,
        documents: Sequence[Document],
        analyses: Sequence[DocumentAnalysis],
        evidence: Sequence[Evidence],
    ) -> FindingAggregationResult:
        """Aggregate validated Evidence into Findings."""


class EvidencePipelineResult(BaseModel):
    """Combined output retaining the existing analysis and aggregation contracts."""

    analysis_result: AnalysisResult
    aggregation_result: Optional[FindingAggregationResult] = None
    aggregation_attempted: bool = False
    aggregation_completed: bool = False
    aggregation_partial: bool = False
    errors: list[str] = Field(default_factory=list)

    @property
    def document_analyses(self) -> list[DocumentAnalysis]:
        """Document analyses produced by the existing ``AnalysisResult`` contract."""
        return self.analysis_result.analyses

    @property
    def evidence(self) -> list[Evidence]:
        """Grounded or partial Evidence produced by the existing ``AnalysisResult`` contract."""
        return self.analysis_result.evidence

    @property
    def findings(self) -> list[Finding]:
        """Deterministic Findings, or an empty list when aggregation did not run."""
        if self.aggregation_result is None:
            return []
        return self.aggregation_result.findings

    @property
    def analyzer_completed(self) -> bool:
        return self.analysis_result.completed

    @property
    def analyzer_partial(self) -> bool:
        return self.analysis_result.partial


class EvidencePipeline:
    """Run bounded document analysis followed by deterministic Finding aggregation.

    This runtime has no knowledge of ResearchState, LangGraph, search, or LLM
    provider construction.  ``ResultAnalyzer`` remains the sole owner of
    document budgets, retries, timeouts, and partial-analysis policy.
    """

    def __init__(
        self,
        model: AnalyzerModel,
        *,
        config: Optional[AnalyzerConfig] = None,
        aggregator: Optional[FindingAggregator] = None,
    ) -> None:
        self.config = config or AnalyzerConfig()
        self.analyzer = ResultAnalyzer(model, config=self.config)
        self.aggregator = aggregator or RuleBasedFindingAggregator()

    async def run(
        self,
        *,
        topic: str,
        documents: Sequence[Document],
        objectives: Sequence[str] = (),
    ) -> EvidencePipelineResult:
        """Analyze documents and aggregate only eligible Evidence records."""
        analysis_result = await self.analyzer.analyze(
            topic=topic,
            documents=documents,
            objectives=objectives,
        )
        errors = list(analysis_result.errors)

        if not analysis_result.completed and not self.config.allow_partial_results:
            errors.append(
                "Finding aggregation skipped because document analysis was incomplete "
                "and partial results are disabled"
            )
            return EvidencePipelineResult(
                analysis_result=analysis_result,
                errors=errors,
            )

        if not analysis_result.evidence:
            return EvidencePipelineResult(
                analysis_result=analysis_result,
                errors=errors,
            )

        try:
            aggregation_result = self.aggregator.aggregate(
                documents=analysis_result.documents,
                analyses=analysis_result.analyses,
                evidence=analysis_result.evidence,
            )
        except Exception as exc:
            errors.append(f"Finding aggregation failed: {exc}")
            return EvidencePipelineResult(
                analysis_result=analysis_result,
                aggregation_attempted=True,
                errors=errors,
            )

        errors.extend(aggregation_result.errors)
        return EvidencePipelineResult(
            analysis_result=analysis_result,
            aggregation_result=aggregation_result,
            aggregation_attempted=True,
            aggregation_completed=aggregation_result.completed,
            aggregation_partial=aggregation_result.partial,
            errors=errors,
        )
