"""Deterministic, read-only Evidence-value comparison over offline case results."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.evaluation.contracts import (
    EvaluationCase,
    EvaluationDataset,
    EvaluationSnapshot,
    OfflineEvaluationResult,
    RunEvaluationResult,
)
from src.evaluation.dataset import FIXED_EVALUATION_DATASET
from src.evaluation.evaluator import EXPECTED_COMPLETED_NODES
from src.evaluation.runner import run_offline_evaluation
from src.evaluation.snapshot import build_evaluation_snapshot


EvidenceBenchmarkMode = Literal["disabled", "enabled", "partial"]
BENCHMARK_VERSION = "p5.2.v1"
DEFAULT_EVIDENCE_BENCHMARK_MODES: tuple[EvidenceBenchmarkMode, ...] = (
    "disabled",
    "enabled",
    "partial",
)
_QUALITY_METRIC_NAMES = ("source_coverage", "grounded_citation", "report_completeness")
_ADOPTED_DIAGNOSTIC_STATUSES = frozenset({"completed", "partial"})
_MISSING = object()


class EvidenceAdoptionSummary(BaseModel):
    """Observed sidecar adoption facts; this does not change finding ownership."""

    eligible_cases: int = 0
    adopted_cases: int = 0
    adoption_rate: float = 0.0
    evidence_record_count: int = 0
    evidence_backed_report_cases: int = 0
    diagnostics_status_counts: dict[str, int] = Field(default_factory=dict)


class BenchmarkResourceSummary(BaseModel):
    """Existing persisted usage/latency totals, not benchmark wall-clock measurements."""

    observed_usage_cases: int = 0
    total_latency_seconds: float = 0.0
    average_latency_seconds: float | None = None
    total_llm_calls: int = 0
    total_tool_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0


class EvidenceBenchmarkModeResult(BaseModel):
    """One mode's P5.1 evaluation plus P5.2 read-only observations."""

    mode: EvidenceBenchmarkMode
    evaluation: OfflineEvaluationResult
    quality_metric_pass_rates: dict[str, float] = Field(default_factory=dict)
    adoption: EvidenceAdoptionSummary = Field(default_factory=EvidenceAdoptionSummary)
    resources: BenchmarkResourceSummary = Field(default_factory=BenchmarkResourceSummary)


class EvidenceValueCaseDelta(BaseModel):
    """A target mode's deterministic delta from the disabled result for one case."""

    case_id: str
    scenario: str
    tags: list[str] = Field(default_factory=list)
    quality_pass_delta: int
    grounded_citation_delta: int
    evidence_record_delta: int
    latency_seconds_delta: float | None = None
    total_tokens_delta: int | None = None
    value_observed: bool = False


class EvidenceValueComparison(BaseModel):
    """Target-mode deltas from disabled, including task tags with observable value."""

    mode: EvidenceBenchmarkMode
    quality_metric_pass_rate_deltas: dict[str, float] = Field(default_factory=dict)
    adoption_rate_delta: float = 0.0
    total_latency_seconds_delta: float = 0.0
    total_tokens_delta: int = 0
    cases: list[EvidenceValueCaseDelta] = Field(default_factory=list)
    benefited_case_ids: list[str] = Field(default_factory=list)
    benefited_task_tags: list[str] = Field(default_factory=list)


class EvidenceValueBenchmarkResult(BaseModel):
    """Comparable P5.2 output. It is an offline observation, never a State patch."""

    benchmark_version: str = BENCHMARK_VERSION
    benchmark_snapshot: EvaluationSnapshot
    dataset_id: str
    dataset_version: str
    modes: list[EvidenceBenchmarkModeResult] = Field(default_factory=list)
    comparisons: list[EvidenceValueComparison] = Field(default_factory=list)
    configuration: dict[str, Any] = Field(default_factory=dict)


EvidenceBenchmarkRunner = Callable[[EvaluationCase, EvidenceBenchmarkMode], Any | Awaitable[Any]]


def _read(value: Any, field: str, default: Any = _MISSING) -> Any:
    if isinstance(value, Mapping):
        return value.get(field, default)
    return getattr(value, field, default)


def _metric_status(result: RunEvaluationResult, name: str) -> str:
    return next(metric.status for metric in result.metrics if metric.name == name)


def _quality_pass_count(result: RunEvaluationResult) -> int:
    return sum(_metric_status(result, name) == "passed" for name in _QUALITY_METRIC_NAMES)


def _quality_metric_pass_rates(
    dataset: EvaluationDataset,
    results: Sequence[RunEvaluationResult],
) -> dict[str, float]:
    quality_results = [
        result
        for case, result in zip(dataset.cases, results)
        if case.scenario != "failure"
    ]
    return {
        name: round(
            sum(_metric_status(result, name) == "passed" for result in quality_results)
            / len(quality_results),
            6,
        )
        if quality_results
        else 0.0
        for name in _QUALITY_METRIC_NAMES
    }


def _resource_summary(states: Sequence[Any]) -> BenchmarkResourceSummary:
    observed: list[Any] = []
    for state in states:
        usage = _read(state, "usage", _MISSING)
        if usage is not _MISSING and usage is not None:
            observed.append(usage)

    def integer_total(field: str) -> int:
        return sum(
            value
            for usage in observed
            if isinstance((value := _read(usage, field, None)), int) and not isinstance(value, bool)
        )

    latencies = [
        float(value)
        for usage in observed
        if isinstance((value := _read(usage, "latency_seconds", None)), (int, float))
        and not isinstance(value, bool)
        and value >= 0
    ]
    return BenchmarkResourceSummary(
        observed_usage_cases=len(observed),
        total_latency_seconds=round(sum(latencies), 6),
        average_latency_seconds=round(sum(latencies) / len(latencies), 6) if latencies else None,
        total_llm_calls=integer_total("llm_calls"),
        total_tool_calls=integer_total("tool_calls"),
        total_input_tokens=integer_total("input_tokens"),
        total_output_tokens=integer_total("output_tokens"),
        total_tokens=integer_total("total_tokens"),
    )


def _adoption_summary(
    dataset: EvaluationDataset,
    states: Sequence[Any],
    results: Sequence[RunEvaluationResult],
) -> EvidenceAdoptionSummary:
    eligible_cases = 0
    adopted_cases = 0
    evidence_records = 0
    evidence_backed_reports = 0
    statuses: dict[str, int] = {}
    for case, state, result in zip(dataset.cases, states, results):
        diagnostics = _read(state, "evidence_diagnostics", _MISSING)
        status = str(_read(diagnostics, "status", "absent")) if diagnostics is not _MISSING else "absent"
        statuses[status] = statuses.get(status, 0) + 1
        evidence_count = result.coverage.evidence
        evidence_records += evidence_count
        if case.scenario == "failure":
            continue
        eligible_cases += 1
        if status in _ADOPTED_DIAGNOSTIC_STATUSES and evidence_count > 0:
            adopted_cases += 1
        if result.quality.grounded_citation_count > 0:
            evidence_backed_reports += 1
    return EvidenceAdoptionSummary(
        eligible_cases=eligible_cases,
        adopted_cases=adopted_cases,
        adoption_rate=round(adopted_cases / eligible_cases, 6) if eligible_cases else 0.0,
        evidence_record_count=evidence_records,
        evidence_backed_report_cases=evidence_backed_reports,
        diagnostics_status_counts=dict(sorted(statuses.items())),
    )


async def _run_mode(
    run_case: EvidenceBenchmarkRunner,
    *,
    mode: EvidenceBenchmarkMode,
    dataset: EvaluationDataset,
    configuration: Mapping[str, Any] | None,
) -> tuple[EvidenceBenchmarkModeResult, list[Any]]:
    states: list[Any] = []

    async def capture(case: EvaluationCase) -> Any:
        try:
            state = run_case(case, mode)
            if inspect.isawaitable(state):
                state = await state
        except Exception as exc:
            state = {
                "research_topic": case.query,
                "error": f"evidence benchmark case runner failed: {exc}",
            }
        states.append(state)
        return state

    mode_configuration = {
        **dict(configuration or {}),
        "benchmark_version": BENCHMARK_VERSION,
        "evidence_mode": mode,
    }
    evaluation = await run_offline_evaluation(
        capture,
        dataset=dataset,
        configuration=mode_configuration,
    )
    return (
        EvidenceBenchmarkModeResult(
            mode=mode,
            evaluation=evaluation,
            quality_metric_pass_rates=_quality_metric_pass_rates(dataset, evaluation.results),
            adoption=_adoption_summary(dataset, states, evaluation.results),
            resources=_resource_summary(states),
        ),
        states,
    )


def _case_delta(
    case: EvaluationCase,
    baseline: RunEvaluationResult,
    target: RunEvaluationResult,
    baseline_state: Any,
    target_state: Any,
) -> EvidenceValueCaseDelta:
    baseline_usage = _read(baseline_state, "usage", None)
    target_usage = _read(target_state, "usage", None)
    baseline_latency = _read(baseline_usage, "latency_seconds", None)
    target_latency = _read(target_usage, "latency_seconds", None)
    baseline_tokens = _read(baseline_usage, "total_tokens", None)
    target_tokens = _read(target_usage, "total_tokens", None)
    quality_pass_delta = _quality_pass_count(target) - _quality_pass_count(baseline)
    return EvidenceValueCaseDelta(
        case_id=case.case_id,
        scenario=case.scenario,
        tags=list(case.tags),
        quality_pass_delta=quality_pass_delta,
        grounded_citation_delta=(
            target.quality.grounded_citation_count - baseline.quality.grounded_citation_count
        ),
        evidence_record_delta=target.coverage.evidence - baseline.coverage.evidence,
        latency_seconds_delta=(
            round(float(target_latency) - float(baseline_latency), 6)
            if isinstance(target_latency, (int, float))
            and not isinstance(target_latency, bool)
            and isinstance(baseline_latency, (int, float))
            and not isinstance(baseline_latency, bool)
            else None
        ),
        total_tokens_delta=(
            int(target_tokens) - int(baseline_tokens)
            if isinstance(target_tokens, int)
            and not isinstance(target_tokens, bool)
            and isinstance(baseline_tokens, int)
            and not isinstance(baseline_tokens, bool)
            else None
        ),
        value_observed=quality_pass_delta > 0,
    )


def _comparison(
    dataset: EvaluationDataset,
    baseline: EvidenceBenchmarkModeResult,
    target: EvidenceBenchmarkModeResult,
    baseline_states: Sequence[Any],
    target_states: Sequence[Any],
) -> EvidenceValueComparison:
    cases = [
        _case_delta(case, base, result, base_state, state)
        for case, base, result, base_state, state in zip(
            dataset.cases,
            baseline.evaluation.results,
            target.evaluation.results,
            baseline_states,
            target_states,
        )
        if case.scenario != "failure"
    ]
    benefited_case_ids = [item.case_id for item in cases if item.value_observed]
    benefited_tags = sorted({tag for item in cases if item.value_observed for tag in item.tags})
    return EvidenceValueComparison(
        mode=target.mode,
        quality_metric_pass_rate_deltas={
            name: round(
                target.quality_metric_pass_rates[name] - baseline.quality_metric_pass_rates[name],
                6,
            )
            for name in _QUALITY_METRIC_NAMES
        },
        adoption_rate_delta=round(target.adoption.adoption_rate - baseline.adoption.adoption_rate, 6),
        total_latency_seconds_delta=round(
            target.resources.total_latency_seconds - baseline.resources.total_latency_seconds,
            6,
        ),
        total_tokens_delta=target.resources.total_tokens - baseline.resources.total_tokens,
        cases=cases,
        benefited_case_ids=benefited_case_ids,
        benefited_task_tags=benefited_tags,
    )


async def run_evidence_value_benchmark(
    run_case: EvidenceBenchmarkRunner,
    *,
    dataset: EvaluationDataset = FIXED_EVALUATION_DATASET,
    modes: Sequence[EvidenceBenchmarkMode] = DEFAULT_EVIDENCE_BENCHMARK_MODES,
    configuration: Mapping[str, Any] | None = None,
) -> EvidenceValueBenchmarkResult:
    """Compare injected disabled/enabled/partial results without invoking production Graph code.

    ``run_case`` may be a deterministic fake for CI or an explicitly invoked
    manual integration harness. This function does not construct providers or
    clients itself, and never writes to a State, checkpoint, or runtime context.
    """

    resolved_modes = tuple(modes)
    invalid_modes = sorted(set(resolved_modes) - set(DEFAULT_EVIDENCE_BENCHMARK_MODES))
    if invalid_modes:
        raise ValueError(f"unknown evidence benchmark modes: {invalid_modes}")
    if len(set(resolved_modes)) != len(resolved_modes):
        raise ValueError("evidence benchmark modes must be unique")
    if "disabled" not in resolved_modes:
        raise ValueError("evidence benchmark requires the disabled baseline mode")
    if not resolved_modes:
        raise ValueError("evidence benchmark requires at least one mode")

    benchmark_configuration = {
        **dict(configuration or {}),
        "benchmark_version": BENCHMARK_VERSION,
        "evidence_modes": list(resolved_modes),
    }
    benchmark_snapshot = build_evaluation_snapshot(
        evaluator_version=BENCHMARK_VERSION,
        dataset=dataset,
        expected_completed_nodes=EXPECTED_COMPLETED_NODES,
        configuration=benchmark_configuration,
    )
    mode_results: list[EvidenceBenchmarkModeResult] = []
    mode_states: dict[EvidenceBenchmarkMode, list[Any]] = {}
    for mode in resolved_modes:
        mode_result, states = await _run_mode(
            run_case,
            mode=mode,
            dataset=dataset,
            configuration=configuration,
        )
        mode_results.append(mode_result)
        mode_states[mode] = states

    baseline = next(result for result in mode_results if result.mode == "disabled")
    comparisons = [
        _comparison(dataset, baseline, result, mode_states["disabled"], mode_states[result.mode])
        for result in mode_results
        if result.mode != "disabled"
    ]
    return EvidenceValueBenchmarkResult(
        benchmark_snapshot=benchmark_snapshot,
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.version,
        modes=mode_results,
        comparisons=comparisons,
        configuration=dict(configuration or {}),
    )
