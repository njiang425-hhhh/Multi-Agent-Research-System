"""Small, deterministic P9 planning-quality baseline and comparison helpers."""

from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, Field

from src.evaluation.contracts import EvaluationMetric, EvaluationSnapshot
from src.evaluation.snapshot import build_evaluation_snapshot
from src.planning import purpose_category
from src.state import ResearchPlan


PLANNING_QUALITY_EVALUATOR_VERSION = "p9.planning_quality.v1"
PLANNING_QUALITY_METRICS = (
    "objectives_coverage",
    "query_purpose_diversity",
    "report_outline_alignment",
)


class PlanningQualityCase(BaseModel):
    """A fixed fake planning case with lexical coverage expectations."""

    case_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    objective_facets: list[str] = Field(min_length=1)
    outline_facets: list[str] = Field(min_length=1)
    min_purpose_categories: int = Field(default=3, ge=1)


class PlanningQualityDataset(BaseModel):
    dataset_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    cases: list[PlanningQualityCase] = Field(min_length=1)


class PlanningQualityCaseResult(BaseModel):
    case_id: str
    metrics: list[EvaluationMetric]


class PlanningQualitySummary(BaseModel):
    total_cases: int = 0
    fully_passing_cases: int = 0
    objectives_coverage_mean: float = 0.0
    query_purpose_diversity_mean: float = 0.0
    report_outline_alignment_mean: float = 0.0


class PlanningQualityResult(BaseModel):
    evaluator_version: str = PLANNING_QUALITY_EVALUATOR_VERSION
    evaluation_snapshot: EvaluationSnapshot
    dataset_id: str
    dataset_version: str
    dataset_content_fingerprint: str
    results: list[PlanningQualityCaseResult]
    summary: PlanningQualitySummary
    configuration: dict[str, Any] = Field(default_factory=dict)


def planning_dataset_content_fingerprint(dataset: PlanningQualityDataset) -> str:
    encoded = json.dumps(
        dataset.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256(encoded.encode("utf-8")).hexdigest()


def _text(plan_values: Sequence[str]) -> str:
    return " ".join(str(value or "").casefold() for value in plan_values)


def _facet_coverage(values: Sequence[str], facets: Sequence[str]) -> tuple[float, list[str]]:
    text = _text(values)
    matched = [facet for facet in facets if facet.casefold() in text]
    return len(matched) / len(facets), matched


def _metric(name: str, *, value: Any, passed: bool, **metadata: Any) -> EvaluationMetric:
    return EvaluationMetric(
        name=name,
        status="passed" if passed else "failed",
        value=value,
        metadata=metadata,
    )


def evaluate_planning_quality(
    plans: Mapping[str, ResearchPlan],
    *,
    dataset: PlanningQualityDataset,
    configuration: Mapping[str, Any] | None = None,
) -> PlanningQualityResult:
    """Score existing plans only; no Graph, Agent, provider, or LLM is created."""

    dataset_fingerprint = planning_dataset_content_fingerprint(dataset)
    resolved_configuration = {
        **dict(configuration or {}),
        "planning_dataset_content_fingerprint": dataset_fingerprint,
    }
    snapshot = build_evaluation_snapshot(
        evaluator_version=PLANNING_QUALITY_EVALUATOR_VERSION,
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.version,
        expected_completed_nodes=("plan",),
        metric_names=PLANNING_QUALITY_METRICS,
        configuration=resolved_configuration,
    )
    results: list[PlanningQualityCaseResult] = []
    objective_scores: list[float] = []
    diversity_scores: list[int] = []
    outline_scores: list[float] = []
    fully_passing = 0

    for case in dataset.cases:
        plan = plans.get(case.case_id)
        if plan is None:
            metrics = [
                EvaluationMetric(name=name, status="unavailable", reason="plan is absent")
                for name in PLANNING_QUALITY_METRICS
            ]
            results.append(PlanningQualityCaseResult(case_id=case.case_id, metrics=metrics))
            continue

        objective_score, objective_hits = _facet_coverage(plan.objectives, case.objective_facets)
        outline_score, outline_hits = _facet_coverage(plan.report_outline, case.outline_facets)
        categories = sorted({purpose_category(item.purpose) for item in plan.search_queries})
        metrics = [
            _metric(
                "objectives_coverage",
                value=objective_score,
                passed=objective_score == 1.0,
                matched_facets=objective_hits,
                expected_facets=case.objective_facets,
            ),
            _metric(
                "query_purpose_diversity",
                value=len(categories),
                passed=len(categories) >= case.min_purpose_categories,
                categories=categories,
                minimum=case.min_purpose_categories,
            ),
            _metric(
                "report_outline_alignment",
                value=outline_score,
                passed=outline_score == 1.0,
                matched_facets=outline_hits,
                expected_facets=case.outline_facets,
            ),
        ]
        objective_scores.append(objective_score)
        diversity_scores.append(len(categories))
        outline_scores.append(outline_score)
        fully_passing += int(all(metric.status == "passed" for metric in metrics))
        results.append(PlanningQualityCaseResult(case_id=case.case_id, metrics=metrics))

    scored_cases = len(objective_scores)
    return PlanningQualityResult(
        evaluation_snapshot=snapshot,
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.version,
        dataset_content_fingerprint=dataset_fingerprint,
        results=results,
        summary=PlanningQualitySummary(
            total_cases=len(dataset.cases),
            fully_passing_cases=fully_passing,
            objectives_coverage_mean=sum(objective_scores) / scored_cases if scored_cases else 0.0,
            query_purpose_diversity_mean=sum(diversity_scores) / scored_cases if scored_cases else 0.0,
            report_outline_alignment_mean=sum(outline_scores) / scored_cases if scored_cases else 0.0,
        ),
        configuration=resolved_configuration,
    )
