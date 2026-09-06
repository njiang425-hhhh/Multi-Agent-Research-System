"""Serializable, deterministic contracts for offline run evaluation."""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


MetricStatus = Literal["passed", "failed", "unavailable"]
EvaluationOutcome = Literal["passed", "failed", "unavailable"]
EvaluationScenario = Literal["success", "partial", "failure"]


class ResearchQualityRubric(BaseModel):
    """Case-level, deterministic minimums for observable research quality."""

    min_distinct_sources: int = Field(default=1, ge=0)
    min_grounded_citations: int = Field(
        default=1,
        ge=0,
        description="Deprecated P5.1 URL-provenance threshold; not used by Phase 3A metrics.",
    )
    min_evidence_supported_findings: int | None = Field(
        default=None,
        ge=0,
        description="Optional Evidence-grounding threshold for supported Findings.",
    )
    min_report_sections: int = Field(default=1, ge=0)
    min_report_characters: int = Field(default=1, ge=0)
    require_top_level_heading: bool = True


class RegressionThresholds(BaseModel):
    """Suite thresholds evaluated from already-produced offline results only."""

    min_expected_outcome_match_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    min_quality_metric_pass_rate: float = Field(default=1.0, ge=0.0, le=1.0)


class EvaluationSnapshot(BaseModel):
    """Versioned, deterministic basis for one comparable evaluation result."""

    snapshot_version: str = "p5.2.v1"
    evaluator_version: str = "p5.2.v1"
    dataset_id: str | None = None
    dataset_version: str | None = None
    dataset_content_fingerprint: str | None = None
    expected_completed_nodes: list[str] = Field(default_factory=list)
    metric_names: list[str] = Field(default_factory=list)
    configuration: dict[str, Any] = Field(default_factory=dict)
    fingerprint: str = Field(min_length=1)


class EvaluationCase(BaseModel):
    """One fixed, versioned research input for an offline suite."""

    case_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    scenario: EvaluationScenario = "success"
    expected_outcome: EvaluationOutcome = "passed"
    quality_rubric: ResearchQualityRubric = Field(default_factory=ResearchQualityRubric)


class EvaluationDataset(BaseModel):
    """A stable collection of cases with observable quality expectations."""

    dataset_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    cases: list[EvaluationCase] = Field(default_factory=list)
    regression_thresholds: RegressionThresholds = Field(default_factory=RegressionThresholds)


class EvaluationMetric(BaseModel):
    """A deterministic assertion or an explicit unavailable observation."""

    name: str = Field(min_length=1)
    status: MetricStatus
    deterministic: bool = True
    value: Any = None
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class TraceEvaluationSummary(BaseModel):
    available: bool = False
    event_count: int = 0
    node_count: int = 0
    llm_count: int = 0
    tool_count: int = 0
    failed_event_count: int = 0
    missing_completed_nodes: list[str] = Field(default_factory=list)
    malformed_event_count: int = 0
    identity_mismatch_count: int = 0


class UsageEvaluationSummary(BaseModel):
    available: bool = False
    llm_calls: int | None = None
    tool_calls: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    latency_seconds: float | None = None


class CoverageEvaluationSummary(BaseModel):
    documents: int = 0
    evidence: int = 0
    findings: int = 0
    evidence_status: MetricStatus = "unavailable"


class ReportEvaluationSummary(BaseModel):
    available: bool = False
    generated: bool = False
    has_heading: bool = False
    section_count: int = 0


class ResearchQualitySummary(BaseModel):
    """Read-only citation-integrity and Evidence-grounding observations."""

    rubric: ResearchQualityRubric = Field(default_factory=ResearchQualityRubric)
    distinct_source_count: int = 0
    cited_source_count: int = 0
    citation_marker_count: int = 0
    invalid_citation_count: int = 0
    citation_map_mismatch_count: int = 0
    section_citation_mismatch_count: int = 0
    evidence_supported_finding_count: int = 0
    evidence_contradicted_finding_count: int = 0
    evidence_unsupported_finding_count: int = 0
    report_character_count: int = 0

class RunEvaluationResult(BaseModel):
    """Evaluation of one already-produced run state; never a State patch."""

    evaluation_id: str = Field(default_factory=lambda: str(uuid4()))
    evaluator_version: str = "p5.2.v1"
    evaluation_snapshot: EvaluationSnapshot | None = None
    dataset_id: str | None = None
    dataset_version: str | None = None
    case_id: str | None = None
    query: str = ""
    run_id: str | None = None
    outcome: EvaluationOutcome
    metrics: list[EvaluationMetric] = Field(default_factory=list)
    trace: TraceEvaluationSummary = Field(default_factory=TraceEvaluationSummary)
    usage: UsageEvaluationSummary = Field(default_factory=UsageEvaluationSummary)
    coverage: CoverageEvaluationSummary = Field(default_factory=CoverageEvaluationSummary)
    report: ReportEvaluationSummary = Field(default_factory=ReportEvaluationSummary)
    quality: ResearchQualitySummary = Field(default_factory=ResearchQualitySummary)
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class OfflineEvaluationSummary(BaseModel):
    total_cases: int = 0
    passed_cases: int = 0
    failed_cases: int = 0
    unavailable_cases: int = 0
    metric_status_counts: dict[str, int] = Field(default_factory=dict)


class RegressionEvaluationSummary(BaseModel):
    """Deterministic regression-gate result; never a production control signal."""

    status: MetricStatus = "unavailable"
    expected_outcome_match_rate: float = 0.0
    quality_metric_pass_rates: dict[str, float] = Field(default_factory=dict)
    quality_case_count: int = 0
    failed_thresholds: list[str] = Field(default_factory=list)


class OfflineEvaluationResult(BaseModel):
    """Serializable suite-level result suitable for later comparison."""

    evaluator_version: str = "p5.2.v1"
    evaluation_snapshot: EvaluationSnapshot | None = None
    dataset_id: str
    dataset_version: str
    results: list[RunEvaluationResult] = Field(default_factory=list)
    summary: OfflineEvaluationSummary = Field(default_factory=OfflineEvaluationSummary)
    regression: RegressionEvaluationSummary = Field(default_factory=RegressionEvaluationSummary)
    configuration: dict[str, Any] = Field(default_factory=dict)
