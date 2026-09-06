"""Injected offline-suite runner; it has no Graph or Agent dependency."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from src.evaluation.contracts import (
    EvaluationCase,
    EvaluationDataset,
    OfflineEvaluationResult,
    OfflineEvaluationSummary,
    RegressionEvaluationSummary,
)
from src.evaluation.dataset import FIXED_EVALUATION_DATASET
from src.evaluation.evaluator import EVALUATOR_VERSION, evaluate_run
from src.evaluation.evaluator import EXPECTED_COMPLETED_NODES
from src.evaluation.snapshot import build_evaluation_snapshot


CaseRunner = Callable[[EvaluationCase], Any | Awaitable[Any]]
_QUALITY_METRIC_NAMES = (
    "source_coverage",
    "citation_integrity",
    "evidence_grounding",
    "report_completeness",
)


def _regression_summary(dataset: EvaluationDataset, results: list) -> RegressionEvaluationSummary:
    """Apply fixed suite thresholds to evaluation output without changing any run."""

    if not results:
        return RegressionEvaluationSummary(status="unavailable")
    expected_matches = sum(
        result.outcome == case.expected_outcome for case, result in zip(dataset.cases, results)
    )
    expected_match_rate = expected_matches / len(results)
    quality_pairs = [
        (case, result)
        for case, result in zip(dataset.cases, results)
        if case.scenario != "failure"
    ]
    rates: dict[str, float] = {}
    for name in _QUALITY_METRIC_NAMES:
        passed = sum(
            1
            for _, result in quality_pairs
            if next(metric.status for metric in result.metrics if metric.name == name) == "passed"
        )
        rates[name] = passed / len(quality_pairs) if quality_pairs else 0.0

    thresholds = dataset.regression_thresholds
    failed_thresholds: list[str] = []
    if expected_match_rate < thresholds.min_expected_outcome_match_rate:
        failed_thresholds.append("min_expected_outcome_match_rate")
    for name, rate in rates.items():
        if rate < thresholds.min_quality_metric_pass_rate:
            failed_thresholds.append(f"min_quality_metric_pass_rate:{name}")
    return RegressionEvaluationSummary(
        status="failed" if failed_thresholds else "passed",
        expected_outcome_match_rate=expected_match_rate,
        quality_metric_pass_rates=rates,
        quality_case_count=len(quality_pairs),
        failed_thresholds=failed_thresholds,
    )


async def run_offline_evaluation(
    run_case: CaseRunner,
    *,
    dataset: EvaluationDataset = FIXED_EVALUATION_DATASET,
    configuration: dict[str, Any] | None = None,
) -> OfflineEvaluationResult:
    """Run an injected existing-result provider over the fixed dataset and summarize it.

    The provider is the only execution boundary. The evaluator itself only reads
    each returned result, so it neither participates in nor modifies the Graph.
    """
    snapshot = build_evaluation_snapshot(
        evaluator_version=EVALUATOR_VERSION,
        dataset=dataset,
        expected_completed_nodes=EXPECTED_COMPLETED_NODES,
        configuration=configuration,
    )
    results = []
    for case in dataset.cases:
        try:
            state = run_case(case)
            if inspect.isawaitable(state):
                state = await state
        except Exception as exc:
            state = {"research_topic": case.query, "error": f"evaluation case runner failed: {exc}"}
        results.append(
            evaluate_run(
                state,
                case=case,
                dataset=dataset,
                configuration=configuration,
                evaluation_snapshot=snapshot,
            )
        )

    counts = {"passed": 0, "failed": 0, "unavailable": 0}
    metric_counts = {"passed": 0, "failed": 0, "unavailable": 0}
    for result in results:
        counts[result.outcome] += 1
        for metric in result.metrics:
            metric_counts[metric.status] += 1
    return OfflineEvaluationResult(
        evaluator_version=EVALUATOR_VERSION,
        evaluation_snapshot=snapshot,
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.version,
        results=results,
        summary=OfflineEvaluationSummary(
            total_cases=len(results),
            passed_cases=counts["passed"],
            failed_cases=counts["failed"],
            unavailable_cases=counts["unavailable"],
            metric_status_counts=metric_counts,
        ),
        regression=_regression_summary(dataset, results),
        configuration=dict(configuration or {}),
    )
