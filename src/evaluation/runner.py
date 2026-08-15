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
)
from src.evaluation.dataset import FIXED_EVALUATION_DATASET
from src.evaluation.evaluator import EVALUATOR_VERSION, evaluate_run


CaseRunner = Callable[[EvaluationCase], Any | Awaitable[Any]]


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
                dataset_id=dataset.dataset_id,
                dataset_version=dataset.version,
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
        configuration=dict(configuration or {}),
    )
