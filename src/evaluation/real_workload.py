"""Manual-only real-workload benchmark and archival built on the P5.2 runner."""

from __future__ import annotations

import inspect
import json
import math
import os
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.evaluation.contracts import EvaluationCase, EvaluationDataset, RunEvaluationResult
from src.evaluation.dataset import FIXED_EVALUATION_DATASET
from src.evaluation.evidence_benchmark import (
    DEFAULT_EVIDENCE_BENCHMARK_MODES,
    EvidenceBenchmarkMode,
    EvidenceValueBenchmarkResult,
    run_evidence_value_benchmark,
)


REAL_WORKLOAD_BENCHMARK_VERSION = "p5.3.v1"
_QUALITY_METRIC_NAMES = (
    "source_coverage",
    "citation_integrity",
    "evidence_grounding",
    "report_completeness",
)
_NODE_NAMES = ("plan", "search", "synthesize", "write_report")
_SENSITIVE_ERROR_PATTERN = re.compile(r"(?i)(api[_-]?key|authorization|token|secret)\s*[:=]\s*\S+")
_MISSING = object()


class ProviderErrorObservation(BaseModel):
    """An observed failed provider/tool attempt with redacted message text."""

    source: Literal["llm", "tool", "run"]
    operation: str
    attempt: int | None = None
    error: str


class CaseModePerformanceObservation(BaseModel):
    """Direct timing and usage observations for one completed harness invocation."""

    total_wall_latency_seconds: float
    node_latency_seconds: dict[str, float | None] = Field(default_factory=dict)
    evidence_sidecar_latency_seconds: float = 0.0
    llm_calls: int | None = None
    tool_calls: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class CaseModeReliabilityObservation(BaseModel):
    """Attempt/error counts read from persisted details and trace only."""

    provider_errors: list[ProviderErrorObservation] = Field(default_factory=list)
    llm_attempt_count: int = 0
    tool_attempt_count: int = 0
    retry_attempt_count: int = 0
    timeout_observed: bool = False
    partial_observed: bool = False
    failure_observed: bool = False


class RealWorkloadCaseObservation(BaseModel):
    """Observed data, not a recommendation or a production policy decision."""

    data_kind: Literal["observed"] = "observed"
    case_id: str
    task_tags: list[str] = Field(default_factory=list)
    evidence_mode: EvidenceBenchmarkMode
    run_id: str | None = None
    status: str = "unknown"
    terminal_reason: str | None = None
    error: str | None = None
    evidence_diagnostics_status: str = "absent"
    evidence_record_count: int = 0
    grounded_record_count: int = 0
    performance: CaseModePerformanceObservation
    reliability: CaseModeReliabilityObservation


class RealWorkloadCaseQuality(BaseModel):
    """Derived P5.1 quality facts for one observed case/mode pair."""

    data_kind: Literal["derived"] = "derived"
    case_id: str
    evidence_mode: EvidenceBenchmarkMode
    source_coverage: str
    citation_integrity: str
    evidence_grounding: str
    report_completeness: str
    overall_quality_pass: bool
    evidence_adopted: bool
    evidence_supported_finding_count: int = 0


class ModeSLOSummary(BaseModel):
    """Derived mode-level SLO summary. Percentiles use deterministic nearest rank."""

    evidence_mode: EvidenceBenchmarkMode
    observed_case_count: int = 0
    success_rate: float = 0.0
    quality_pass_rate: float = 0.0
    latency_p50_seconds: float | None = None
    latency_p95_seconds: float | None = None
    provider_failure_rate: float = 0.0
    writer_latency_fraction: float | None = None
    evidence_adoption_rate: float = 0.0
    evidence_supported_finding_total: int = 0
    partial_case_count: int = 0
    failure_case_count: int = 0


class RealWorkloadDerivedMetrics(BaseModel):
    """Calculated evaluation/comparison data, explicitly separated from observations."""

    data_kind: Literal["derived"] = "derived"
    case_quality: list[RealWorkloadCaseQuality] = Field(default_factory=list)
    slo_by_mode: list[ModeSLOSummary] = Field(default_factory=list)
    evidence_value_comparisons: list[dict[str, Any]] = Field(default_factory=list)


class RealWorkloadBenchmarkResult(BaseModel):
    """Archivable P5.3 result. It never changes the evaluated production runs."""

    benchmark_version: str = REAL_WORKLOAD_BENCHMARK_VERSION
    generated_at: str
    dataset_id: str
    dataset_version: str
    benchmark: EvidenceValueBenchmarkResult
    observed_cases: list[RealWorkloadCaseObservation] = Field(default_factory=list)
    derived_metrics: RealWorkloadDerivedMetrics
    configuration: dict[str, Any] = Field(default_factory=dict)


RealWorkloadRunner = Callable[[EvaluationCase, EvidenceBenchmarkMode], Any | Awaitable[Any]]


def _read(value: Any, field: str, default: Any = _MISSING) -> Any:
    if isinstance(value, Mapping):
        return value.get(field, default)
    return getattr(value, field, default)


def _as_records(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [
        item.model_dump() if isinstance(item, BaseModel) else item
        for item in value
        if isinstance(item, Mapping) or isinstance(item, BaseModel)
    ]


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return float(value)
    return None


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _redact_error(value: Any) -> str:
    return _SENSITIVE_ERROR_PATTERN.sub("[REDACTED]", str(value or ""))[:1024]


def _metric_status(result: RunEvaluationResult, name: str) -> str:
    return next(metric.status for metric in result.metrics if metric.name == name)


def _node_latencies(trace: Sequence[Mapping[str, Any]]) -> dict[str, float | None]:
    return {
        node: round(
            sum(
                _number(event.get("duration_seconds")) or 0.0
                for event in trace
                if event.get("event_type") == "node" and event.get("node") == node
            ),
            6,
        )
        if any(event.get("event_type") == "node" and event.get("node") == node for event in trace)
        else None
        for node in _NODE_NAMES
    }


def _reliability(state: Any, trace: Sequence[Mapping[str, Any]]) -> CaseModeReliabilityObservation:
    details = _as_records(_read(state, "llm_call_details", ()))
    errors: list[ProviderErrorObservation] = []
    for detail in details:
        if detail.get("error"):
            errors.append(
                ProviderErrorObservation(
                    source="llm",
                    operation=str(detail.get("operation") or "llm"),
                    attempt=_integer(detail.get("attempt")),
                    error=_redact_error(detail["error"]),
                )
            )
    for event in trace:
        if event.get("event_type") == "tool" and event.get("status") == "failed":
            errors.append(
                ProviderErrorObservation(
                    source="tool",
                    operation=str(event.get("operation") or "tool"),
                    attempt=_integer(event.get("attempt")),
                    error=_redact_error(event.get("error") or "tool attempt failed"),
                )
            )
    run_error = _read(state, "error", None)
    if run_error:
        errors.append(
            ProviderErrorObservation(
                source="run",
                operation="run",
                error=_redact_error(run_error),
            )
        )
    diagnostics = _read(state, "evidence_diagnostics", None)
    diagnostic_status = str(_read(diagnostics, "status", ""))
    terminal_reason = str(_read(state, "terminal_reason", ""))
    timeout_observed = terminal_reason == "timeout" or any(
        "timeout" in item.error.lower() or "timed out" in item.error.lower() for item in errors
    )
    return CaseModeReliabilityObservation(
        provider_errors=errors,
        llm_attempt_count=len(details),
        tool_attempt_count=sum(event.get("event_type") == "tool" for event in trace),
        retry_attempt_count=sum((_integer(detail.get("attempt")) or 1) > 1 for detail in details)
        + sum((_integer(event.get("attempt")) or 1) > 1 for event in trace if event.get("event_type") == "tool"),
        timeout_observed=timeout_observed,
        partial_observed=diagnostic_status == "partial",
        failure_observed=str(_read(state, "status", "")) in {"failed", "cancelled"},
    )


def _observation(
    case: EvaluationCase,
    mode: EvidenceBenchmarkMode,
    state: Any,
    wall_latency_seconds: float,
) -> RealWorkloadCaseObservation:
    trace = _as_records(_read(state, "agent_trace", ()))
    details = _as_records(_read(state, "llm_call_details", ()))
    usage = _read(state, "usage", None)
    diagnostics = _read(state, "evidence_diagnostics", None)
    evidence = _read(state, "evidence", ())
    evidence_records = _as_records(evidence)
    return RealWorkloadCaseObservation(
        case_id=case.case_id,
        task_tags=list(case.tags),
        evidence_mode=mode,
        run_id=str(_read(state, "run_id", "") or "") or None,
        status=str(_read(state, "status", "unknown")),
        terminal_reason=str(_read(state, "terminal_reason", "") or "") or None,
        error=_redact_error(_read(state, "error", None)) or None,
        evidence_diagnostics_status=str(_read(diagnostics, "status", "absent")),
        evidence_record_count=len(evidence_records),
        grounded_record_count=sum(
            item.get("status") in {"grounded", "partial"} for item in evidence_records
        ),
        performance=CaseModePerformanceObservation(
            total_wall_latency_seconds=round(max(0.0, wall_latency_seconds), 6),
            node_latency_seconds=_node_latencies(trace),
            evidence_sidecar_latency_seconds=round(
                sum(
                    _number(detail.get("duration")) or 0.0
                    for detail in details
                    if detail.get("agent") == "EvidenceAnalyzer"
                ),
                6,
            ),
            llm_calls=_integer(_read(usage, "llm_calls", None)),
            tool_calls=_integer(_read(usage, "tool_calls", None)),
            input_tokens=_integer(_read(usage, "input_tokens", None)),
            output_tokens=_integer(_read(usage, "output_tokens", None)),
            total_tokens=_integer(_read(usage, "total_tokens", None)),
        ),
        reliability=_reliability(state, trace),
    )


def _nearest_rank(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(percentile * len(ordered)) - 1)], 6)


def _quality_records(
    benchmark: EvidenceValueBenchmarkResult,
    observations: Mapping[tuple[EvidenceBenchmarkMode, str], RealWorkloadCaseObservation],
) -> list[RealWorkloadCaseQuality]:
    records: list[RealWorkloadCaseQuality] = []
    for mode_result in benchmark.modes:
        for evaluation in mode_result.evaluation.results:
            observation = observations[(mode_result.mode, str(evaluation.case_id))]
            statuses = {name: _metric_status(evaluation, name) for name in _QUALITY_METRIC_NAMES}
            records.append(
                RealWorkloadCaseQuality(
                    case_id=str(evaluation.case_id),
                    evidence_mode=mode_result.mode,
                    source_coverage=statuses["source_coverage"],
                    citation_integrity=statuses["citation_integrity"],
                    evidence_grounding=statuses["evidence_grounding"],
                    report_completeness=statuses["report_completeness"],
                    overall_quality_pass=all(status == "passed" for status in statuses.values()),
                    evidence_adopted=(
                        observation.evidence_diagnostics_status in {"completed", "partial"}
                        and observation.evidence_record_count > 0
                    ),
                    evidence_supported_finding_count=evaluation.quality.evidence_supported_finding_count,
                )
            )
    return records


def _slo_summaries(
    benchmark: EvidenceValueBenchmarkResult,
    observations: Sequence[RealWorkloadCaseObservation],
    quality: Sequence[RealWorkloadCaseQuality],
) -> list[ModeSLOSummary]:
    summaries: list[ModeSLOSummary] = []
    for mode_result in benchmark.modes:
        mode = mode_result.mode
        mode_observations = [item for item in observations if item.evidence_mode == mode]
        mode_quality = [item for item in quality if item.evidence_mode == mode]
        latencies = [item.performance.total_wall_latency_seconds for item in mode_observations]
        writer_seconds = sum(
            item.performance.node_latency_seconds.get("write_report") or 0.0
            for item in mode_observations
        )
        total_seconds = sum(latencies)
        summaries.append(
            ModeSLOSummary(
                evidence_mode=mode,
                observed_case_count=len(mode_observations),
                success_rate=round(
                    sum(item.status == "completed" for item in mode_observations)
                    / len(mode_observations),
                    6,
                )
                if mode_observations
                else 0.0,
                quality_pass_rate=round(
                    sum(item.overall_quality_pass for item in mode_quality) / len(mode_quality),
                    6,
                )
                if mode_quality
                else 0.0,
                latency_p50_seconds=_nearest_rank(latencies, 0.5),
                latency_p95_seconds=_nearest_rank(latencies, 0.95),
                provider_failure_rate=round(
                    sum(bool(item.reliability.provider_errors) for item in mode_observations)
                    / len(mode_observations),
                    6,
                )
                if mode_observations
                else 0.0,
                writer_latency_fraction=round(min(1.0, writer_seconds / total_seconds), 6)
                if total_seconds > 0
                else None,
                evidence_adoption_rate=mode_result.adoption.adoption_rate,
                evidence_supported_finding_total=sum(item.evidence_supported_finding_count for item in mode_quality),
                partial_case_count=sum(item.reliability.partial_observed for item in mode_observations),
                failure_case_count=sum(item.reliability.failure_observed for item in mode_observations),
            )
        )
    return summaries


def _comparisons_with_observed_overhead(
    benchmark: EvidenceValueBenchmarkResult,
    observations: Sequence[RealWorkloadCaseObservation],
    dataset: EvaluationDataset,
) -> list[dict[str, Any]]:
    """Add P5.3 wall-time/call deltas to P5.2 quality comparisons.

    P5.2 deliberately reads persisted ``UsageMetrics``. P5.3 additionally
    observes harness wall time because existing production usage does not own
    an end-to-end latency accumulator.
    """

    by_mode_case = {(item.evidence_mode, item.case_id): item for item in observations}
    comparisons: list[dict[str, Any]] = []
    for comparison in benchmark.comparisons:
        target_mode = comparison.mode
        baseline = [
            by_mode_case[("disabled", case.case_id)] for case in dataset.cases
            if ("disabled", case.case_id) in by_mode_case
        ]
        target = [
            by_mode_case[(target_mode, case.case_id)] for case in dataset.cases
            if (target_mode, case.case_id) in by_mode_case
        ]
        baseline_by_case = {item.case_id: item for item in baseline}
        target_by_case = {item.case_id: item for item in target}
        matched_success_case_ids = [
            case.case_id
            for case in dataset.cases
            if case.scenario != "failure"
            and case.case_id in baseline_by_case
            and case.case_id in target_by_case
            and baseline_by_case[case.case_id].status == "completed"
            and target_by_case[case.case_id].status == "completed"
        ]
        observed_wall_delta = sum(
            target_by_case[case_id].performance.total_wall_latency_seconds
            - baseline_by_case[case_id].performance.total_wall_latency_seconds
            for case_id in matched_success_case_ids
        )
        llm_calls_delta = sum(
            (target_by_case[case_id].performance.llm_calls or 0)
            - (baseline_by_case[case_id].performance.llm_calls or 0)
            for case_id in matched_success_case_ids
        )
        tool_calls_delta = sum(
            (target_by_case[case_id].performance.tool_calls or 0)
            - (baseline_by_case[case_id].performance.tool_calls or 0)
            for case_id in matched_success_case_ids
        )
        payload = comparison.model_dump(mode="json")
        payload["matched_success_case_ids"] = matched_success_case_ids
        payload["comparison_status"] = "comparable" if matched_success_case_ids else "inconclusive"
        payload["comparison_reason"] = (
            None
            if matched_success_case_ids
            else "no non-failure case completed in both disabled and target modes"
        )
        payload["observed_total_wall_latency_seconds_delta"] = (
            round(observed_wall_delta, 6) if matched_success_case_ids else None
        )
        payload["observed_llm_calls_delta"] = llm_calls_delta if matched_success_case_ids else None
        payload["observed_tool_calls_delta"] = tool_calls_delta if matched_success_case_ids else None
        comparisons.append(payload)
    return comparisons


async def run_real_workload_benchmark(
    run_case: RealWorkloadRunner,
    *,
    dataset: EvaluationDataset = FIXED_EVALUATION_DATASET,
    modes: Sequence[EvidenceBenchmarkMode] = DEFAULT_EVIDENCE_BENCHMARK_MODES,
    configuration: Mapping[str, Any] | None = None,
) -> RealWorkloadBenchmarkResult:
    """Observe an injected real/manual harness, then derive P5.1/P5.2 metrics.

    The function intentionally does not construct Graph, provider, LLM, or
    Evidence services. It is suitable for fake tests and manual integrations;
    callers alone own external execution and credentials.
    """

    observations: dict[tuple[EvidenceBenchmarkMode, str], RealWorkloadCaseObservation] = {}

    async def observed_runner(case: EvaluationCase, mode: EvidenceBenchmarkMode) -> Any:
        started = perf_counter()
        try:
            state = run_case(case, mode)
            if inspect.isawaitable(state):
                state = await state
        except Exception as exc:
            state = {
                "research_topic": case.query,
                "status": "failed",
                "current_stage": "failed",
                "terminal_reason": "unhandled_exception",
                "error": f"manual benchmark runner failed: {exc}",
            }
        observations[(mode, case.case_id)] = _observation(
            case, mode, state, perf_counter() - started
        )
        return state

    resolved_configuration = {
        **dict(configuration or {}),
        "real_workload_benchmark_version": REAL_WORKLOAD_BENCHMARK_VERSION,
    }
    benchmark = await run_evidence_value_benchmark(
        observed_runner,
        dataset=dataset,
        modes=modes,
        configuration=resolved_configuration,
    )
    ordered_observations = [
        observations[(mode, case.case_id)] for mode in modes for case in dataset.cases
    ]
    quality = _quality_records(benchmark, observations)
    return RealWorkloadBenchmarkResult(
        generated_at=datetime.now(timezone.utc).isoformat(),
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.version,
        benchmark=benchmark,
        observed_cases=ordered_observations,
        derived_metrics=RealWorkloadDerivedMetrics(
            case_quality=quality,
            slo_by_mode=_slo_summaries(benchmark, ordered_observations, quality),
            evidence_value_comparisons=_comparisons_with_observed_overhead(
                benchmark,
                ordered_observations,
                dataset,
            ),
        ),
        configuration=resolved_configuration,
    )


def refresh_real_workload_derived_metrics(
    result: RealWorkloadBenchmarkResult,
) -> RealWorkloadBenchmarkResult:
    """Recalculate derived fields from an archived observed result without rerunning APIs."""

    observations = {(item.evidence_mode, item.case_id): item for item in result.observed_cases}
    quality = _quality_records(result.benchmark, observations)
    refreshed = RealWorkloadDerivedMetrics(
        case_quality=quality,
        slo_by_mode=_slo_summaries(result.benchmark, result.observed_cases, quality),
        evidence_value_comparisons=_comparisons_with_observed_overhead(
            result.benchmark,
            result.observed_cases,
            FIXED_EVALUATION_DATASET,
        ),
    )
    return result.model_copy(update={"derived_metrics": refreshed})


@contextmanager
def _temporary_evidence_mode(mode: EvidenceBenchmarkMode):
    """Temporarily scope only Evidence environment switches to one manual run."""

    previous = {
        name: os.environ.get(name)
        for name in ("EVIDENCE_ANALYZER_ENABLED", "EVIDENCE_ALLOW_PARTIAL_RESULTS")
    }
    os.environ["EVIDENCE_ANALYZER_ENABLED"] = "false" if mode == "disabled" else "true"
    os.environ["EVIDENCE_ALLOW_PARTIAL_RESULTS"] = "true" if mode == "partial" else "false"
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def manual_deepseek_tavily_runner() -> RealWorkloadRunner:
    """Return the explicit, sequential real runner used only by the manual CLI."""

    from src.config import config

    if config.model_provider != "deepseek" or config.search_provider != "tavily":
        raise RuntimeError("manual workload benchmark requires MODEL_PROVIDER=deepseek and SEARCH_PROVIDER=tavily")
    if not config.deepseek_api_key or not config.tavily_api_key:
        raise RuntimeError("manual workload benchmark requires DEEPSEEK_API_KEY and TAVILY_API_KEY")

    async def run_case(case: EvaluationCase, mode: EvidenceBenchmarkMode) -> Any:
        # ``run_evidence_value_benchmark`` is intentionally sequential. The
        # process-scoped environment is therefore restored before the next run.
        with _temporary_evidence_mode(mode):
            from src.graph import run_research

            return await run_research(
                case.query,
                verbose=False,
                use_cache=False,
                use_checkpoints=True,
            )

    return run_case


def render_benchmark_report(result: RealWorkloadBenchmarkResult) -> str:
    """Render a compact human report while preserving JSON as the source of record."""

    lines = [
        "# P5.3 Real Workload & SLO Benchmark Report",
        "",
        "This report separates **observed data** from **derived metrics**. It is not a production-policy recommendation.",
        "",
        "## Benchmark identity",
        "",
        f"- Generated at: `{result.generated_at}`",
        f"- Dataset: `{result.dataset_id}` v`{result.dataset_version}`",
        f"- Modes: `{', '.join(item.mode for item in result.benchmark.modes)}`",
        "",
        "## Observed case/mode runs",
        "",
        "| Case | Tags | Mode | Status | Terminal reason | Total wall s | Evidence records | Sidecar s | Provider errors |",
        "|---|---|---|---|---|---:|---:|---:|---:|",
    ]
    for item in result.observed_cases:
        lines.append(
            "| {case} | {tags} | {mode} | {status} | {reason} | {total:.3f} | {records} | {sidecar:.3f} | {errors} |".format(
                case=item.case_id,
                tags=", ".join(item.task_tags) or "-",
                mode=item.evidence_mode,
                status=item.status,
                reason=item.terminal_reason or "-",
                total=item.performance.total_wall_latency_seconds,
                records=item.evidence_record_count,
                sidecar=item.performance.evidence_sidecar_latency_seconds,
                errors=len(item.reliability.provider_errors),
            )
        )
    lines.extend(
        [
            "",
            "## Derived SLO summary",
            "",
            "| Mode | Success rate | Quality pass rate | p50 s | p95 s | Provider failure rate | Writer/total | Evidence adoption | Grounded citations |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for item in result.derived_metrics.slo_by_mode:
        lines.append(
            "| {mode} | {success:.3f} | {quality:.3f} | {p50} | {p95} | {failure:.3f} | {writer} | {adoption:.3f} | {grounded} |".format(
                mode=item.evidence_mode,
                success=item.success_rate,
                quality=item.quality_pass_rate,
                p50=f"{item.latency_p50_seconds:.3f}" if item.latency_p50_seconds is not None else "-",
                p95=f"{item.latency_p95_seconds:.3f}" if item.latency_p95_seconds is not None else "-",
                failure=item.provider_failure_rate,
                writer=f"{item.writer_latency_fraction:.3f}" if item.writer_latency_fraction is not None else "-",
                adoption=item.evidence_adoption_rate,
                grounded=item.evidence_supported_finding_total,
            )
        )
    lines.extend(["", "## Derived Evidence comparison", ""])
    for item in result.derived_metrics.evidence_value_comparisons:
        lines.extend(
            [
                f"### {item['mode']} relative to disabled",
                "",
                f"- Comparison status: `{item['comparison_status']}`",
                f"- Matched successful cases: `{', '.join(item['matched_success_case_ids']) or '-'}`",
                f"- Comparison note: `{item['comparison_reason'] or '-'}`",
                f"- Quality pass-rate deltas: `{item['quality_metric_pass_rate_deltas']}`",
                f"- Observed wall-latency overhead (s): `{item['observed_total_wall_latency_seconds_delta'] if item['observed_total_wall_latency_seconds_delta'] is not None else '-'}`",
                f"- Token overhead: `{item['total_tokens_delta']}`",
                f"- LLM/tool-call overhead: `{item['observed_llm_calls_delta'] if item['observed_llm_calls_delta'] is not None else '-'}` / `{item['observed_tool_calls_delta'] if item['observed_tool_calls_delta'] is not None else '-'}`",
                f"- Benefited cases: `{', '.join(item['benefited_case_ids']) or '-'}`",
                f"- Benefited task tags: `{', '.join(item['benefited_task_tags']) or '-'}`",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def archive_real_workload_benchmark(
    result: RealWorkloadBenchmarkResult,
    output_directory: str | Path,
) -> tuple[Path, Path]:
    """Write the machine-readable JSON and human-readable report for one manual run."""

    directory = Path(output_directory)
    directory.mkdir(parents=True, exist_ok=False)
    raw_path = directory / "benchmark.json"
    report_path = directory / "benchmark.md"
    raw_path.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(render_benchmark_report(result), encoding="utf-8")
    return raw_path, report_path
