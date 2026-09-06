"""Manual-only, repeatable aggregation of P5.3 workload observations."""

from __future__ import annotations

import json
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.evaluation.contracts import EvaluationCase, EvaluationDataset
from src.evaluation.dataset import FIXED_EVALUATION_DATASET
from experiments.evaluation.evidence_benchmark import DEFAULT_EVIDENCE_BENCHMARK_MODES, EvidenceBenchmarkMode
from experiments.evaluation.real_workload import (
    REAL_WORKLOAD_BENCHMARK_VERSION,
    RealWorkloadBenchmarkResult,
    run_real_workload_benchmark,
)


REPEATABILITY_BENCHMARK_VERSION = "p6.repeatability.v1"
_QUALITY_NAMES = (
    "source_coverage",
    "citation_integrity",
    "evidence_grounding",
    "report_completeness",
)


class DispersionSummary(BaseModel):
    """Deterministic descriptive statistics; sample standard deviation needs n >= 2."""

    data_kind: Literal["derived"] = "derived"
    sample_count: int = 0
    mean: float | None = None
    minimum: float | None = None
    maximum: float | None = None
    sample_standard_deviation: float | None = None
    coefficient_of_variation: float | None = None


class RepeatabilityDecisionCriteria(BaseModel):
    """Offline assessment thresholds, never production settings or controls."""

    min_rounds: int = Field(default=3, ge=2)
    min_matched_success_rate: float = Field(default=0.8, ge=0.0, le=1.0)
    min_repeated_benefit_rate: float = Field(default=0.8, ge=0.0, le=1.0)
    max_quality_pass_rate_standard_deviation: float = Field(default=0.1, ge=0.0)
    max_latency_coefficient_of_variation: float = Field(default=0.3, ge=0.0)
    max_provider_error_rate_regression: float = Field(default=0.1, ge=0.0, le=1.0)
    min_writer_latency_fraction: float = Field(default=0.5, ge=0.0, le=1.0)
    writer_latency_slo_seconds: float | None = Field(default=None, gt=0.0)
    provider_error_rate_slo: float | None = Field(default=None, ge=0.0, le=1.0)


class RepeatabilityRun(BaseModel):
    """One actual harness round. Its nested P5.3 payload labels observed/derived fields."""

    data_kind: Literal["observed"] = "observed"
    round_index: int = Field(ge=1)
    workload: RealWorkloadBenchmarkResult


class ModeRepeatabilityAggregate(BaseModel):
    data_kind: Literal["derived"] = "derived"
    evidence_mode: EvidenceBenchmarkMode
    run_count: int = 0
    quality_pass_rate: DispersionSummary = Field(default_factory=DispersionSummary)
    quality_metric_pass_rates: dict[str, DispersionSummary] = Field(default_factory=dict)
    total_wall_latency_seconds: DispersionSummary = Field(default_factory=DispersionSummary)
    latency_p95_seconds: DispersionSummary = Field(default_factory=DispersionSummary)
    writer_latency_fraction: DispersionSummary = Field(default_factory=DispersionSummary)
    writer_node_latency_seconds: DispersionSummary = Field(default_factory=DispersionSummary)
    provider_error_rate: DispersionSummary = Field(default_factory=DispersionSummary)
    evidence_adoption_rate: DispersionSummary = Field(default_factory=DispersionSummary)


class EvidenceValueRepeatabilityAggregate(BaseModel):
    data_kind: Literal["derived"] = "derived"
    evidence_mode: EvidenceBenchmarkMode
    total_rounds: int = 0
    comparable_rounds: int = 0
    quality_metric_pass_rate_deltas: dict[str, DispersionSummary] = Field(default_factory=dict)
    observed_wall_latency_delta_seconds: DispersionSummary = Field(default_factory=DispersionSummary)


class MatchedCrossRoundComparison(BaseModel):
    """Only same-round, same-case successful pairs may contribute a delta."""

    data_kind: Literal["derived"] = "derived"
    evidence_mode: EvidenceBenchmarkMode
    case_id: str
    task_tags: list[str] = Field(default_factory=list)
    eligible_round_count: int = 0
    matched_success_round_count: int = 0
    matched_success_rate: float = 0.0
    quality_pass_delta: DispersionSummary = Field(default_factory=DispersionSummary)
    evidence_grounding_pass_delta: DispersionSummary = Field(default_factory=DispersionSummary)
    observed_wall_latency_delta_seconds: DispersionSummary = Field(default_factory=DispersionSummary)
    value_observed_round_count: int = 0
    repeated_benefit_rate: float | None = None


class ProviderErrorPatternAggregate(BaseModel):
    """Error grouping is descriptive only; no fallback or health policy is inferred."""

    data_kind: Literal["derived"] = "derived"
    evidence_mode: EvidenceBenchmarkMode
    source: str
    operation: str
    observed_error_count: int = 0
    affected_round_count: int = 0


class InitiativeEvidenceAssessment(BaseModel):
    """A decision-input assessment, explicitly not an action or recommendation."""

    data_kind: Literal["derived"] = "derived"
    initiative: Literal["evidence_selector", "writer_optimization", "provider_resilience"]
    status: Literal["sufficient", "insufficient", "not_assessable"]
    satisfied_criteria: list[str] = Field(default_factory=list)
    unmet_criteria: list[str] = Field(default_factory=list)
    observed_basis: dict[str, Any] = Field(default_factory=dict)
    disclaimer: str = "Assessment only; it does not alter production policy or initiate implementation."


class RepeatabilityDerivedMetrics(BaseModel):
    data_kind: Literal["derived"] = "derived"
    mode_aggregates: list[ModeRepeatabilityAggregate] = Field(default_factory=list)
    evidence_value_aggregates: list[EvidenceValueRepeatabilityAggregate] = Field(default_factory=list)
    matched_cross_round_comparisons: list[MatchedCrossRoundComparison] = Field(default_factory=list)
    provider_error_patterns: list[ProviderErrorPatternAggregate] = Field(default_factory=list)
    initiative_assessments: list[InitiativeEvidenceAssessment] = Field(default_factory=list)


class RepeatabilityBenchmarkResult(BaseModel):
    """P6 archive: source observations and derived inference remain separated."""

    benchmark_version: str = REPEATABILITY_BENCHMARK_VERSION
    generated_at: str
    dataset_id: str
    dataset_version: str
    repeatability_snapshot_fingerprint: str
    observed_runs: list[RepeatabilityRun] = Field(default_factory=list)
    derived_metrics: RepeatabilityDerivedMetrics
    decision_criteria: RepeatabilityDecisionCriteria
    configuration: dict[str, Any] = Field(default_factory=dict)


RepeatabilityRunner = Callable[[EvaluationCase, EvidenceBenchmarkMode], Any | Awaitable[Any]]


def _round(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def _dispersion(values: Sequence[float | int | None]) -> DispersionSummary:
    numbers = [float(value) for value in values if isinstance(value, (int, float)) and not isinstance(value, bool)]
    if not numbers:
        return DispersionSummary()
    mean = sum(numbers) / len(numbers)
    variance = sum((value - mean) ** 2 for value in numbers) / (len(numbers) - 1) if len(numbers) > 1 else None
    deviation = math.sqrt(variance) if variance is not None else None
    return DispersionSummary(
        sample_count=len(numbers), mean=_round(mean), minimum=_round(min(numbers)), maximum=_round(max(numbers)),
        sample_standard_deviation=_round(deviation),
        coefficient_of_variation=_round(deviation / abs(mean)) if deviation is not None and mean != 0 else None,
    )


def _slo(workload: RealWorkloadBenchmarkResult, mode: EvidenceBenchmarkMode):
    return next(item for item in workload.derived_metrics.slo_by_mode if item.evidence_mode == mode)


def _quality(workload: RealWorkloadBenchmarkResult, mode: EvidenceBenchmarkMode, case_id: str):
    return next(
        item for item in workload.derived_metrics.case_quality
        if item.evidence_mode == mode and item.case_id == case_id
    )


def _observation(workload: RealWorkloadBenchmarkResult, mode: EvidenceBenchmarkMode, case_id: str):
    return next(
        item for item in workload.observed_cases
        if item.evidence_mode == mode and item.case_id == case_id
    )


def _snapshot_fingerprint(
    dataset: EvaluationDataset,
    modes: Sequence[EvidenceBenchmarkMode],
    criteria: RepeatabilityDecisionCriteria,
    configuration: Mapping[str, Any] | None,
) -> str:
    payload = {
        "benchmark_version": REPEATABILITY_BENCHMARK_VERSION,
        "dataset_id": dataset.dataset_id,
        "dataset_version": dataset.version,
        "dataset_content": [item.model_dump(mode="json") for item in dataset.cases],
        "modes": list(modes),
        "criteria": criteria.model_dump(mode="json"),
        "configuration": dict(configuration or {}),
        "p5_real_workload_version": REAL_WORKLOAD_BENCHMARK_VERSION,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _mode_aggregates(runs: Sequence[RepeatabilityRun], modes: Sequence[EvidenceBenchmarkMode]) -> list[ModeRepeatabilityAggregate]:
    aggregates: list[ModeRepeatabilityAggregate] = []
    for mode in modes:
        slos = [_slo(item.workload, mode) for item in runs]
        mode_benchmarks = [
            next(item for item in run.workload.benchmark.modes if item.mode == mode)
            for run in runs
        ]
        observations = [
            observation for item in runs for observation in item.workload.observed_cases
            if observation.evidence_mode == mode
        ]
        aggregates.append(
            ModeRepeatabilityAggregate(
                evidence_mode=mode,
                run_count=len(runs),
                quality_pass_rate=_dispersion([item.quality_pass_rate for item in slos]),
                quality_metric_pass_rates={
                    name: _dispersion(
                        [item.quality_metric_pass_rates.get(name) for item in mode_benchmarks]
                    )
                    for name in _QUALITY_NAMES
                },
                total_wall_latency_seconds=_dispersion([
                    sum(observation.performance.total_wall_latency_seconds for observation in item.workload.observed_cases if observation.evidence_mode == mode)
                    for item in runs
                ]),
                latency_p95_seconds=_dispersion([item.latency_p95_seconds for item in slos]),
                writer_latency_fraction=_dispersion([item.writer_latency_fraction for item in slos]),
                writer_node_latency_seconds=_dispersion([
                    observation.performance.node_latency_seconds.get("write_report")
                    for observation in observations
                ]),
                provider_error_rate=_dispersion([item.provider_failure_rate for item in slos]),
                evidence_adoption_rate=_dispersion([item.evidence_adoption_rate for item in slos]),
            )
        )
    return aggregates


def _value_aggregates(runs: Sequence[RepeatabilityRun], modes: Sequence[EvidenceBenchmarkMode]) -> list[EvidenceValueRepeatabilityAggregate]:
    aggregates: list[EvidenceValueRepeatabilityAggregate] = []
    for mode in modes:
        if mode == "disabled":
            continue
        comparisons = [
            next(item for item in run.workload.derived_metrics.evidence_value_comparisons if item["mode"] == mode)
            for run in runs
        ]
        aggregates.append(
            EvidenceValueRepeatabilityAggregate(
                evidence_mode=mode,
                total_rounds=len(runs),
                comparable_rounds=sum(item["comparison_status"] == "comparable" for item in comparisons),
                quality_metric_pass_rate_deltas={
                    name: _dispersion([item["quality_metric_pass_rate_deltas"].get(name) for item in comparisons])
                    for name in _QUALITY_NAMES
                },
                observed_wall_latency_delta_seconds=_dispersion([
                    item.get("observed_total_wall_latency_seconds_delta")
                    for item in comparisons if item["comparison_status"] == "comparable"
                ]),
            )
        )
    return aggregates


def _matched_comparisons(
    runs: Sequence[RepeatabilityRun], dataset: EvaluationDataset, modes: Sequence[EvidenceBenchmarkMode]
) -> list[MatchedCrossRoundComparison]:
    results: list[MatchedCrossRoundComparison] = []
    for mode in modes:
        if mode == "disabled":
            continue
        for case in dataset.cases:
            if case.scenario == "failure":
                continue
            quality_deltas: list[int] = []
            citation_deltas: list[int] = []
            wall_deltas: list[float] = []
            benefit_count = 0
            for run in runs:
                baseline_observation = _observation(run.workload, "disabled", case.case_id)
                target_observation = _observation(run.workload, mode, case.case_id)
                if baseline_observation.status != "completed" or target_observation.status != "completed":
                    continue
                baseline_quality = _quality(run.workload, "disabled", case.case_id)
                target_quality = _quality(run.workload, mode, case.case_id)
                baseline_count = sum(getattr(baseline_quality, name) == "passed" for name in _QUALITY_NAMES)
                target_count = sum(getattr(target_quality, name) == "passed" for name in _QUALITY_NAMES)
                delta = target_count - baseline_count
                quality_deltas.append(delta)
                citation_deltas.append(
                    int(target_quality.evidence_grounding == "passed") - int(baseline_quality.evidence_grounding == "passed")
                )
                wall_deltas.append(
                    target_observation.performance.total_wall_latency_seconds
                    - baseline_observation.performance.total_wall_latency_seconds
                )
                benefit_count += delta > 0
            matched = len(quality_deltas)
            results.append(
                MatchedCrossRoundComparison(
                    evidence_mode=mode,
                    case_id=case.case_id,
                    task_tags=list(case.tags),
                    eligible_round_count=len(runs),
                    matched_success_round_count=matched,
                    matched_success_rate=_round(matched / len(runs)) if runs else 0.0,
                    quality_pass_delta=_dispersion(quality_deltas),
                    evidence_grounding_pass_delta=_dispersion(citation_deltas),
                    observed_wall_latency_delta_seconds=_dispersion(wall_deltas),
                    value_observed_round_count=benefit_count,
                    repeated_benefit_rate=_round(benefit_count / matched) if matched else None,
                )
            )
    return results


def _provider_patterns(runs: Sequence[RepeatabilityRun], modes: Sequence[EvidenceBenchmarkMode]) -> list[ProviderErrorPatternAggregate]:
    records: dict[tuple[str, str, str], tuple[int, set[int]]] = {}
    for run in runs:
        for observation in run.workload.observed_cases:
            if observation.evidence_mode not in modes:
                continue
            for error in observation.reliability.provider_errors:
                key = (observation.evidence_mode, error.source, error.operation)
                count, affected = records.get(key, (0, set()))
                records[key] = (count + 1, {*affected, run.round_index})
    return [
        ProviderErrorPatternAggregate(
            evidence_mode=mode, source=source, operation=operation,
            observed_error_count=count, affected_round_count=len(rounds),
        )
        for (mode, source, operation), (count, rounds) in sorted(records.items())
    ]


def _assessments(
    runs: Sequence[RepeatabilityRun],
    modes: Sequence[EvidenceBenchmarkMode],
    aggregates: Sequence[ModeRepeatabilityAggregate],
    matched: Sequence[MatchedCrossRoundComparison],
    patterns: Sequence[ProviderErrorPatternAggregate],
    criteria: RepeatabilityDecisionCriteria,
) -> list[InitiativeEvidenceAssessment]:
    by_mode = {item.evidence_mode: item for item in aggregates}
    baseline_error = by_mode["disabled"].provider_error_rate.mean if "disabled" in by_mode else None
    evidence_candidates = [
        item for item in matched
        if item.matched_success_rate >= criteria.min_matched_success_rate
        and item.repeated_benefit_rate is not None
        and item.repeated_benefit_rate >= criteria.min_repeated_benefit_rate
        and (item.evidence_grounding_pass_delta.mean or 0.0) > 0
    ]
    evidence_stable = any(
        (by_mode[item.evidence_mode].quality_pass_rate.sample_standard_deviation or 0.0)
        <= criteria.max_quality_pass_rate_standard_deviation
        and (baseline_error is None or by_mode[item.evidence_mode].provider_error_rate.mean is None
             or by_mode[item.evidence_mode].provider_error_rate.mean <= baseline_error + criteria.max_provider_error_rate_regression)
        for item in evidence_candidates
    )
    evidence_unmet: list[str] = []
    if len(runs) < criteria.min_rounds:
        evidence_unmet.append(f"requires at least {criteria.min_rounds} rounds")
    if not evidence_candidates:
        evidence_unmet.append("requires a same-case repeated positive provenance-grounding delta")
    if evidence_candidates and not evidence_stable:
        evidence_unmet.append("requires stable quality and no excessive provider-error regression")

    writer_unmet: list[str] = []
    if criteria.writer_latency_slo_seconds is None:
        writer_status: Literal["sufficient", "insufficient", "not_assessable"] = "not_assessable"
        writer_unmet.append("writer_latency_slo_seconds is required before a performance initiative can be assessed")
    else:
        writer_candidates = [
            item for item in aggregates
            if item.writer_node_latency_seconds.maximum is not None
            and item.writer_node_latency_seconds.maximum > criteria.writer_latency_slo_seconds
            and (item.writer_latency_fraction.mean or 0.0) >= criteria.min_writer_latency_fraction
            and (item.total_wall_latency_seconds.coefficient_of_variation or 0.0) <= criteria.max_latency_coefficient_of_variation
        ]
        if len(runs) < criteria.min_rounds:
            writer_unmet.append(f"requires at least {criteria.min_rounds} rounds")
        if not writer_candidates:
            writer_unmet.append("requires repeated SLO breach, material writer share, and stable total latency")
        writer_status = "sufficient" if not writer_unmet else "insufficient"

    resilience_unmet: list[str] = []
    if criteria.provider_error_rate_slo is None:
        resilience_status: Literal["sufficient", "insufficient", "not_assessable"] = "not_assessable"
        resilience_unmet.append("provider_error_rate_slo is required before resilience can be assessed")
    else:
        repeated = [
            item for item in patterns
            if item.affected_round_count >= criteria.min_rounds
            and (by_mode[item.evidence_mode].provider_error_rate.mean or 0.0) > criteria.provider_error_rate_slo
        ]
        if len(runs) < criteria.min_rounds:
            resilience_unmet.append(f"requires at least {criteria.min_rounds} rounds")
        if not repeated:
            resilience_unmet.append("requires a repeated source/operation error pattern above the explicit SLO")
        resilience_status = "sufficient" if not resilience_unmet else "insufficient"

    return [
        InitiativeEvidenceAssessment(
            initiative="evidence_selector", status="sufficient" if not evidence_unmet else "insufficient",
            satisfied_criteria=["assessment is based on same-round matched successful pairs"] if evidence_candidates else [],
            unmet_criteria=evidence_unmet,
            observed_basis={"candidate_case_ids": [item.case_id for item in evidence_candidates], "round_count": len(runs)},
        ),
        InitiativeEvidenceAssessment(
            initiative="writer_optimization", status=writer_status,
            satisfied_criteria=["explicit writer SLO was supplied"] if criteria.writer_latency_slo_seconds is not None else [],
            unmet_criteria=writer_unmet,
            observed_basis={"round_count": len(runs), "writer_latency_slo_seconds": criteria.writer_latency_slo_seconds},
        ),
        InitiativeEvidenceAssessment(
            initiative="provider_resilience", status=resilience_status,
            satisfied_criteria=["explicit provider error-rate SLO was supplied"] if criteria.provider_error_rate_slo is not None else [],
            unmet_criteria=resilience_unmet,
            observed_basis={"round_count": len(runs), "provider_error_rate_slo": criteria.provider_error_rate_slo},
        ),
    ]


def refresh_repeatability_derived_metrics(
    result: RepeatabilityBenchmarkResult,
    *,
    dataset: EvaluationDataset = FIXED_EVALUATION_DATASET,
) -> RepeatabilityBenchmarkResult:
    """Recalculate P6 inference from archived P5.3 records without rerunning a provider."""

    # The resolved mode order is persisted in the P6 configuration. This makes
    # archived recalculation independent of execution and provider state.
    resolved_modes = tuple(result.configuration.get("evidence_modes", DEFAULT_EVIDENCE_BENCHMARK_MODES))
    runs = result.observed_runs
    mode_aggregates = _mode_aggregates(runs, resolved_modes)
    matched = _matched_comparisons(runs, dataset, resolved_modes)
    patterns = _provider_patterns(runs, resolved_modes)
    derived = RepeatabilityDerivedMetrics(
        mode_aggregates=mode_aggregates,
        evidence_value_aggregates=_value_aggregates(runs, resolved_modes),
        matched_cross_round_comparisons=matched,
        provider_error_patterns=patterns,
        initiative_assessments=_assessments(runs, resolved_modes, mode_aggregates, matched, patterns, result.decision_criteria),
    )
    return result.model_copy(update={"derived_metrics": derived})


async def run_repeatability_benchmark(
    run_case: RepeatabilityRunner,
    *,
    rounds: int = 3,
    dataset: EvaluationDataset = FIXED_EVALUATION_DATASET,
    modes: Sequence[EvidenceBenchmarkMode] = DEFAULT_EVIDENCE_BENCHMARK_MODES,
    criteria: RepeatabilityDecisionCriteria | None = None,
    configuration: Mapping[str, Any] | None = None,
) -> RepeatabilityBenchmarkResult:
    """Sequentially collect manual P5.3 rounds; no Graph/provider is constructed here."""

    if rounds < 2:
        raise ValueError("repeatability benchmark requires at least two rounds")
    criteria = criteria or RepeatabilityDecisionCriteria()
    resolved_modes = tuple(modes)
    if len(set(resolved_modes)) != len(resolved_modes) or "disabled" not in resolved_modes:
        raise ValueError("repeatability benchmark requires unique modes including disabled")
    resolved_configuration = {
        **dict(configuration or {}),
        "repeatability_benchmark_version": REPEATABILITY_BENCHMARK_VERSION,
        "evidence_modes": list(resolved_modes),
        "rounds": rounds,
    }
    runs: list[RepeatabilityRun] = []
    for round_index in range(1, rounds + 1):
        workload = await run_real_workload_benchmark(
            run_case, dataset=dataset, modes=resolved_modes,
            configuration={**resolved_configuration, "repeatability_round": round_index},
        )
        runs.append(RepeatabilityRun(round_index=round_index, workload=workload))
    result = RepeatabilityBenchmarkResult(
        generated_at=datetime.now(timezone.utc).isoformat(),
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.version,
        repeatability_snapshot_fingerprint=_snapshot_fingerprint(dataset, resolved_modes, criteria, resolved_configuration),
        observed_runs=runs,
        derived_metrics=RepeatabilityDerivedMetrics(),
        decision_criteria=criteria,
        configuration=resolved_configuration,
    )
    return refresh_repeatability_derived_metrics(result, dataset=dataset)


def render_repeatability_report(result: RepeatabilityBenchmarkResult) -> str:
    """Compact report; JSON remains the source record for observed and derived values."""

    lines = [
        "# P6 Repeatability Benchmark Report", "",
        "Observed runs and derived inference are separated below. This is an assessment only; it changes no production policy.", "",
        "## Observed per-run workload records", "",
        "| Round | P5.3 generated at | Case/mode observations |", "|---:|---|---:|",
    ]
    for run in result.observed_runs:
        lines.append(f"| {run.round_index} | {run.workload.generated_at} | {len(run.workload.observed_cases)} |")
    lines.extend(
        [
            "",
            "## Derived per-run SLO profile",
            "",
            "| Round | Mode | Quality pass | p95 s | Writer/total | Provider-error rate | Evidence adoption |",
            "|---:|---|---:|---:|---:|---:|---:|",
        ]
    )
    for run in result.observed_runs:
        for item in run.workload.derived_metrics.slo_by_mode:
            lines.append(
                "| {round_index} | {mode} | {quality:.3f} | {p95} | {writer} | {errors:.3f} | {adoption:.3f} |".format(
                    round_index=run.round_index,
                    mode=item.evidence_mode,
                    quality=item.quality_pass_rate,
                    p95=f"{item.latency_p95_seconds:.3f}" if item.latency_p95_seconds is not None else "-",
                    writer=f"{item.writer_latency_fraction:.3f}" if item.writer_latency_fraction is not None else "-",
                    errors=item.provider_failure_rate,
                    adoption=item.evidence_adoption_rate,
                )
            )
    lines.extend(["", "## Derived aggregate and dispersion", "", "| Mode | Quality mean ± sd | Wall-time mean ± sd (s) | Provider-error mean ± sd | Writer share mean ± sd |", "|---|---:|---:|---:|---:|"])
    for item in result.derived_metrics.mode_aggregates:
        def present(summary: DispersionSummary) -> str:
            return "-" if summary.mean is None else f"{summary.mean:.3f} ± {summary.sample_standard_deviation:.3f}" if summary.sample_standard_deviation is not None else f"{summary.mean:.3f} (n={summary.sample_count})"
        lines.append(f"| {item.evidence_mode} | {present(item.quality_pass_rate)} | {present(item.total_wall_latency_seconds)} | {present(item.provider_error_rate)} | {present(item.writer_latency_fraction)} |")
    lines.extend(
        [
            "",
            "## Derived Evidence-value dispersion",
            "",
            "| Mode | Comparable rounds | Grounded-citation delta mean ± sd | Observed wall delta mean ± sd (s) |",
            "|---|---:|---:|---:|",
        ]
    )
    for item in result.derived_metrics.evidence_value_aggregates:
        grounded = item.quality_metric_pass_rate_deltas["evidence_grounding"]
        wall = item.observed_wall_latency_delta_seconds
        def present_delta(summary: DispersionSummary) -> str:
            if summary.mean is None:
                return "-"
            if summary.sample_standard_deviation is None:
                return f"{summary.mean:.3f} (n={summary.sample_count})"
            return f"{summary.mean:.3f} ± {summary.sample_standard_deviation:.3f}"
        lines.append(
            f"| {item.evidence_mode} | {item.comparable_rounds}/{item.total_rounds} | "
            f"{present_delta(grounded)} | {present_delta(wall)} |"
        )
    lines.extend(["", "## Derived cross-round matched comparison", "", "| Mode | Case | Matched rounds | Grounded pass delta mean | Wall delta mean (s) | Repeated benefit rate |", "|---|---|---:|---:|---:|---:|"])
    for item in result.derived_metrics.matched_cross_round_comparisons:
        lines.append(f"| {item.evidence_mode} | {item.case_id} | {item.matched_success_round_count}/{item.eligible_round_count} | {item.evidence_grounding_pass_delta.mean if item.evidence_grounding_pass_delta.mean is not None else '-'} | {item.observed_wall_latency_delta_seconds.mean if item.observed_wall_latency_delta_seconds.mean is not None else '-'} | {item.repeated_benefit_rate if item.repeated_benefit_rate is not None else '-'} |")
    lines.extend(["", "## Derived initiative assessments", ""])
    for item in result.derived_metrics.initiative_assessments:
        lines.append(f"- `{item.initiative}`: **{item.status}** — {'; '.join(item.unmet_criteria) or 'all declared criteria met'}")
    return "\n".join(lines) + "\n"


def archive_repeatability_benchmark(result: RepeatabilityBenchmarkResult, output_directory: str | Path) -> tuple[Path, Path]:
    """Archive a manual P6 run; callers choose output location and execution timing."""

    directory = Path(output_directory)
    directory.mkdir(parents=True, exist_ok=False)
    json_path = directory / "benchmark.json"
    report_path = directory / "benchmark.md"
    json_path.write_text(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path.write_text(render_repeatability_report(result), encoding="utf-8")
    return json_path, report_path
