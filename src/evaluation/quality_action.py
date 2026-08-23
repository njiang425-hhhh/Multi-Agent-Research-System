"""Deterministic, read-only quality-to-action advisory contracts.

This module composes existing evaluation observations into structured
recommendations.  It never mutates State, invokes an Agent, or grants an
action execution permission.
"""

from __future__ import annotations

from hashlib import sha256
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.evaluation.contracts import EvaluationMetric, RunEvaluationResult
from src.evaluation.planning_quality import PlanningQualityResult
from src.evaluation.research_coverage import ResearchCoverageResult
from src.evaluation.snapshot import build_evaluation_snapshot
from src.evaluation.contracts import EvaluationSnapshot


QUALITY_ACTION_EVALUATOR_VERSION = "p11.1.quality_action.v1"
QUALITY_ACTION_POLICY_VERSION = "p11.1.advisory_policy.v1"

SignalStatus = Literal["passed", "failed", "unavailable", "inconclusive"]
SignalKind = Literal["observed", "derived"]
SignalScope = Literal["run", "node", "facet", "document", "claim", "report"]
ActionKind = Literal[
    "continue",
    "accept_partial",
    "retry_research",
    "replan",
    "stop_fail",
    "human_review",
]
RecommendationStatus = Literal["candidate", "blocked", "unavailable"]


class ThresholdSpec(BaseModel):
    """Versioned comparison rule attached to one signal."""

    operator: Literal["eq", "gte", "gt", "lte", "lt", "in", "exists"]
    expected: Any
    unit: str | None = None
    policy_version: str = QUALITY_ACTION_POLICY_VERSION


class SignalProvenance(BaseModel):
    """Where one signal came from; it is descriptive, never executable."""

    evaluator_version: str
    snapshot_fingerprint: str
    dataset_id: str | None = None
    dataset_version: str | None = None
    run_id: str | None = None
    source_fields: list[str] = Field(default_factory=list)
    observed_at: str | None = None


class QualitySignal(BaseModel):
    """One observed or derived quality fact with explicit missing semantics."""

    signal_id: str
    metric: str
    status: SignalStatus
    kind: SignalKind
    scope: SignalScope
    deterministic: bool
    value: Any = None
    threshold: ThresholdSpec | None = None
    reason: str
    provenance: SignalProvenance
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActionRecommendation(BaseModel):
    """An advisory recommendation, deliberately not a routing command."""

    advisory: Literal[True] = True
    action: ActionKind
    status: RecommendationStatus
    metric: str | None = None
    threshold: ThresholdSpec | None = None
    reason: str
    triggering_signal_ids: list[str] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    unmet_preconditions: list[str] = Field(default_factory=list)
    provenance: SignalProvenance


class QualityActionEvaluation(BaseModel):
    """Read-only P11.1 output for one selected case or run."""

    evaluator_version: str = QUALITY_ACTION_EVALUATOR_VERSION
    policy_version: str = QUALITY_ACTION_POLICY_VERSION
    evaluation_snapshot: EvaluationSnapshot
    signals: list[QualitySignal] = Field(default_factory=list)
    recommendation: ActionRecommendation
    configuration: dict[str, Any] = Field(default_factory=dict)


_PLANNING_METRICS = {
    "objectives_coverage",
    "query_purpose_diversity",
    "report_outline_alignment",
}
_RESEARCH_METRICS = {
    "planned_query_facet_coverage",
    "executed_query_facet_coverage",
    "result_facet_coverage",
    "extracted_facet_coverage",
    "source_domain_balance",
}
_REPORT_METRICS = {"report_structure", "report_completeness"}
_RESEARCH_ACTION_METRICS = {"result_facet_coverage", "extracted_facet_coverage"}
_PLANNING_ACTION_METRICS = _PLANNING_METRICS
_HARD_STOP_REASONS = {
    "timeout",
    "budget_exhausted",
    "cancelled",
    "unhandled_exception",
    "agent_failed",
    "router_terminated",
}


def quality_action_content_fingerprint(value: Any) -> str:
    """Fingerprint adapter inputs and policy content deterministically."""

    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif isinstance(value, Mapping):
        value = {str(key): quality_action_content_fingerprint_value(item) for key, item in value.items()}
    elif isinstance(value, (list, tuple)):
        value = [quality_action_content_fingerprint_value(item) for item in value]
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def quality_action_content_fingerprint_value(value: Any) -> Any:
    """Convert nested Pydantic and collection values to JSON-safe content."""

    if hasattr(value, "model_dump"):
        return quality_action_content_fingerprint_value(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return {str(key): quality_action_content_fingerprint_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [quality_action_content_fingerprint_value(item) for item in value]
    return value


def _read(value: Any, field: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(field, default)
    return getattr(value, field, default)


def _metric_threshold(metric: EvaluationMetric) -> ThresholdSpec:
    if metric.name == "query_purpose_diversity":
        return ThresholdSpec(
            operator="gte",
            expected=metric.metadata.get("minimum", 3),
            unit="purpose_categories",
        )
    if metric.name in _PLANNING_METRICS or metric.name in _RESEARCH_METRICS:
        return ThresholdSpec(operator="gte", expected=1.0, unit="ratio")
    return ThresholdSpec(operator="eq", expected="passed", unit="metric_status")


def _metric_scope(name: str) -> SignalScope:
    if name in _PLANNING_METRICS:
        return "node"
    if name in _RESEARCH_METRICS:
        return "facet"
    if name in {"source_coverage"}:
        return "document"
    if name in {"grounded_citation"}:
        return "claim"
    if name in _REPORT_METRICS:
        return "report"
    return "run"


def _metric_signal(
    metric: EvaluationMetric,
    *,
    kind: SignalKind,
    provenance: SignalProvenance,
    signal_id_prefix: str,
    scope: SignalScope | None = None,
) -> QualitySignal:
    return QualitySignal(
        signal_id=f"{signal_id_prefix}:{metric.name}",
        metric=metric.name,
        status=metric.status,
        kind=kind,
        scope=scope or _metric_scope(metric.name),
        deterministic=metric.deterministic,
        value=metric.value,
        threshold=_metric_threshold(metric),
        reason=metric.reason or f"evaluation metric status is {metric.status}",
        provenance=provenance,
        metadata=dict(metric.metadata),
    )


def _case_metrics(result: Any, case_id: str | None) -> tuple[str, Sequence[EvaluationMetric]]:
    items = list(result.results)
    if case_id:
        selected = [item for item in items if item.case_id == case_id]
        if not selected:
            raise ValueError(f"case_id is not present in evaluation result: {case_id}")
        item = selected[0]
    elif len(items) == 1:
        item = items[0]
    else:
        raise ValueError("case_id is required when adapting a multi-case evaluation result")
    return item.case_id, item.metrics


def _derived_provenance(
    *,
    result: Any,
    source_fields: Sequence[str],
) -> SignalProvenance:
    snapshot = result.evaluation_snapshot
    return SignalProvenance(
        evaluator_version=str(result.evaluator_version),
        snapshot_fingerprint=str(snapshot.fingerprint),
        dataset_id=_read(result, "dataset_id"),
        dataset_version=_read(result, "dataset_version"),
        source_fields=list(source_fields),
    )


def planning_quality_signals(
    result: PlanningQualityResult,
    *,
    case_id: str | None = None,
) -> list[QualitySignal]:
    """Adapt one P9 case result without rerunning planning or an LLM."""

    selected_case, metrics = _case_metrics(result, case_id)
    provenance = _derived_provenance(
        result=result,
        source_fields=[f"planning.results[{selected_case}].metrics"],
    )
    return [
        _metric_signal(
            metric,
            kind="derived",
            provenance=provenance,
            signal_id_prefix=f"planning:{selected_case}",
        )
        for metric in metrics
    ]


def research_coverage_signals(
    result: ResearchCoverageResult,
    *,
    case_id: str | None = None,
) -> list[QualitySignal]:
    """Adapt one P10 case result without rerunning search or extraction."""

    selected_case, metrics = _case_metrics(result, case_id)
    provenance = _derived_provenance(
        result=result,
        source_fields=[f"research_coverage.results[{selected_case}].metrics"],
    )
    return [
        _metric_signal(
            metric,
            kind="derived",
            provenance=provenance,
            signal_id_prefix=f"research:{selected_case}",
        )
        for metric in metrics
    ]


def run_evaluation_signals(
    result: RunEvaluationResult,
) -> list[QualitySignal]:
    """Adapt P5 run-evaluation metrics without changing their semantics."""

    provenance = _derived_provenance(
        result=result,
        source_fields=["run_evaluation.metrics"],
    )
    prefix = f"run:{result.case_id or result.run_id or 'unknown'}"
    return [
        _metric_signal(
            metric,
            kind="derived",
            provenance=provenance,
            signal_id_prefix=prefix,
        )
        for metric in result.metrics
    ]


def _observed_signal(
    *,
    signal_id: str,
    metric: str,
    status: SignalStatus,
    value: Any,
    reason: str,
    source_fields: Sequence[str],
    run_id: str | None,
    metadata: Mapping[str, Any] | None = None,
    threshold: ThresholdSpec | None = None,
) -> QualitySignal:
    return QualitySignal(
        signal_id=signal_id,
        metric=metric,
        status=status,
        kind="observed",
        scope="run",
        deterministic=True,
        value=value,
        threshold=threshold,
        reason=reason,
        provenance=SignalProvenance(
            evaluator_version=QUALITY_ACTION_EVALUATOR_VERSION,
            snapshot_fingerprint="pending",
            run_id=run_id,
            source_fields=list(source_fields),
        ),
        metadata=dict(metadata or {}),
    )


def runtime_observation_signals(state: Any) -> list[QualitySignal]:
    """Read lifecycle, terminal, trace, usage and error observations only."""

    run_id = str(_read(state, "run_id", "") or "") or None
    status = _read(state, "status", None)
    stage = _read(state, "current_stage", None)
    terminal_reason = _read(state, "terminal_reason", None)
    error = _read(state, "error", None)
    trace = _read(state, "agent_trace", None)
    usage = _read(state, "usage", None)
    evidence_diagnostics = _read(state, "evidence_diagnostics", None)
    evidence_status = _read(evidence_diagnostics, "status", None)
    lifecycle_passed = status == "completed" and stage == "complete" and not error
    hard_stop = terminal_reason in _HARD_STOP_REASONS or status in {"failed", "cancelled"}

    signals = [
        _observed_signal(
            signal_id="runtime:lifecycle",
            metric="runtime_lifecycle",
            status="passed" if lifecycle_passed else "failed",
            value=lifecycle_passed,
            reason="runtime lifecycle is completed" if lifecycle_passed else "runtime lifecycle is not completed",
            source_fields=["status", "current_stage", "error", "terminal_reason"],
            run_id=run_id,
            threshold=ThresholdSpec(operator="eq", expected=True, unit="completed"),
        ),
        _observed_signal(
            signal_id="runtime:hard_stop",
            metric="runtime_hard_stop",
            status="failed" if hard_stop else "passed",
            value=terminal_reason,
            reason=(f"runtime terminal reason is {terminal_reason}" if hard_stop else "no runtime hard-stop fact observed"),
            source_fields=["status", "terminal_reason"],
            run_id=run_id,
            threshold=ThresholdSpec(operator="eq", expected=None, unit="terminal_reason"),
        ),
        _observed_signal(
            signal_id="runtime:error",
            metric="runtime_error",
            status="failed" if error else "passed",
            value=error,
            reason="runtime error is present" if error else "runtime error is absent",
            source_fields=["error"],
            run_id=run_id,
            threshold=ThresholdSpec(operator="exists", expected=False, unit="error"),
        ),
    ]

    if evidence_status == "partial":
        signals.append(
            _observed_signal(
                signal_id="runtime:partial_output",
                metric="partial_output",
                status="passed",
                value=True,
                reason="evidence diagnostics report a partial result",
                source_fields=["evidence_diagnostics.status"],
                run_id=run_id,
                threshold=ThresholdSpec(operator="eq", expected=True, unit="partial_output"),
            )
        )

    if trace is None:
        signals.append(
            _observed_signal(
                signal_id="runtime:trace",
                metric="runtime_trace",
                status="unavailable",
                value=None,
                reason="agent_trace is absent",
                source_fields=["agent_trace"],
                run_id=run_id,
            )
        )
    else:
        failed_events = sum(_read(event, "status", "") == "failed" for event in trace)
        signals.append(
            _observed_signal(
                signal_id="runtime:trace",
                metric="runtime_trace",
                status="failed" if failed_events else "passed",
                value={"event_count": len(trace), "failed_event_count": failed_events},
                reason="trace contains failed events" if failed_events else "trace contains no failed events",
                source_fields=["agent_trace"],
                run_id=run_id,
                metadata={"failed_event_count": failed_events},
            )
        )

    if usage is None:
        signals.append(
            _observed_signal(
                signal_id="runtime:usage",
                metric="runtime_usage",
                status="unavailable",
                value=None,
                reason="usage is absent",
                source_fields=["usage"],
                run_id=run_id,
            )
        )
    else:
        fields = ("llm_calls", "tool_calls", "input_tokens", "output_tokens", "total_tokens", "latency_seconds")
        values = {field: _read(usage, field, None) for field in fields}
        complete = all(value is not None for value in values.values())
        nonnegative = complete and all(isinstance(value, (int, float)) and value >= 0 for value in values.values())
        consistent = nonnegative and values["total_tokens"] == values["input_tokens"] + values["output_tokens"]
        valid = nonnegative and consistent
        signals.append(
            _observed_signal(
                signal_id="runtime:usage",
                metric="runtime_usage",
                status="passed" if valid else ("inconclusive" if not complete else "failed"),
                value=values,
                reason="usage is complete and consistent" if valid else "usage is incomplete or inconsistent",
                source_fields=["usage"],
                run_id=run_id,
            )
        )

    return signals


def _threshold_satisfied(signal: QualitySignal) -> bool | None:
    threshold = signal.threshold
    if threshold is None:
        return None
    value = signal.value
    if threshold.unit == "metric_status":
        return signal.status == threshold.expected
    if threshold.operator == "exists":
        return (value is not None) == bool(threshold.expected)
    if threshold.operator == "eq":
        return value == threshold.expected
    if threshold.operator == "in":
        return value in threshold.expected
    if value is None:
        return None
    try:
        if threshold.operator == "gte":
            return value >= threshold.expected
        if threshold.operator == "gt":
            return value > threshold.expected
        if threshold.operator == "lte":
            return value <= threshold.expected
        if threshold.operator == "lt":
            return value < threshold.expected
    except (TypeError, ValueError):
        return None
    return None


def _effective_status(signal: QualitySignal) -> SignalStatus:
    if signal.status in {"unavailable", "inconclusive", "failed"}:
        return signal.status
    satisfied = _threshold_satisfied(signal)
    return "failed" if satisfied is False else signal.status


def _provenance_for(signals: Sequence[QualitySignal]) -> SignalProvenance:
    if signals:
        return signals[0].provenance
    return SignalProvenance(
        evaluator_version=QUALITY_ACTION_EVALUATOR_VERSION,
        snapshot_fingerprint="empty",
        source_fields=[],
    )


def recommend_action(signals: Sequence[QualitySignal]) -> ActionRecommendation:
    """Resolve one deterministic advisory recommendation from signals."""

    provenance = _provenance_for(signals)
    if not signals:
        return ActionRecommendation(
            action="human_review",
            status="blocked",
            reason="no quality signals are available",
            unmet_preconditions=["at least one quality signal is required"],
            provenance=provenance,
        )

    effective = {signal.signal_id: _effective_status(signal) for signal in signals}
    by_metric: dict[str, list[QualitySignal]] = defaultdict(list)
    for signal in signals:
        by_metric[signal.metric].append(signal)

    hard_stop = [
        signal
        for signal in signals
        if signal.metric == "runtime_hard_stop" and effective[signal.signal_id] == "failed"
    ]
    if hard_stop:
        signal = hard_stop[0]
        return ActionRecommendation(
            action="stop_fail",
            status="candidate",
            metric=signal.metric,
            threshold=signal.threshold,
            reason="runtime hard-stop facts take precedence over derived quality recommendations",
            triggering_signal_ids=[item.signal_id for item in hard_stop],
            preconditions=["Runtime/runner remains the owner of terminalization"],
            unmet_preconditions=[],
            provenance=provenance,
        )

    conflicts = [
        metric
        for metric, items in by_metric.items()
        if {effective[item.signal_id] for item in items} & {"passed"}
        and {effective[item.signal_id] for item in items} & {"failed"}
    ]
    if conflicts:
        conflict_signals = [item for item in signals if item.metric in conflicts]
        return ActionRecommendation(
            action="human_review",
            status="blocked",
            reason=f"conflicting statuses were observed for metrics: {', '.join(sorted(conflicts))}",
            triggering_signal_ids=[item.signal_id for item in conflict_signals],
            unmet_preconditions=["resolve conflicting observations before any action is considered"],
            provenance=provenance,
        )

    unavailable = [
        signal
        for signal in signals
        if effective[signal.signal_id] in {"unavailable", "inconclusive"}
    ]
    if unavailable:
        return ActionRecommendation(
            action="human_review",
            status="blocked",
            reason="one or more required signals are unavailable or inconclusive",
            triggering_signal_ids=[item.signal_id for item in unavailable],
            unmet_preconditions=["required signals must be available and calibrated"],
            provenance=provenance,
        )

    planning_failures = [
        signal
        for signal in signals
        if signal.metric in _PLANNING_ACTION_METRICS and effective[signal.signal_id] == "failed"
    ]
    if planning_failures:
        signal = planning_failures[0]
        return ActionRecommendation(
            action="replan",
            status="blocked",
            metric=signal.metric,
            threshold=signal.threshold,
            reason="planning quality is below its threshold; replan is only an advisory candidate",
            triggering_signal_ids=[item.signal_id for item in planning_failures],
            unmet_preconditions=[
                "bounded Planner budget",
                "replan state/checkpoint contract",
                "explicit authorization to change the plan",
            ],
            provenance=provenance,
        )

    research_failures = [
        signal
        for signal in signals
        if signal.metric in _RESEARCH_ACTION_METRICS and effective[signal.signal_id] == "failed"
    ]
    if research_failures:
        signal = research_failures[0]
        return ActionRecommendation(
            action="retry_research",
            status="blocked",
            metric=signal.metric,
            threshold=signal.threshold,
            reason="research coverage is below its threshold; re-search remains advisory only",
            triggering_signal_ids=[item.signal_id for item in research_failures],
            unmet_preconditions=[
                "recoverable, targetable research deficit",
                "remaining Runtime deadline and operation budget",
                "idempotent research execution contract",
            ],
            provenance=provenance,
        )

    partial = [
        signal
        for signal in signals
        if signal.metric == "partial_output" and effective[signal.signal_id] == "passed"
    ]
    if partial:
        return ActionRecommendation(
            action="accept_partial",
            status="blocked",
            metric="partial_output",
            threshold=partial[0].threshold,
            reason="partial output was observed, but no partial-delivery policy is currently authorized",
            triggering_signal_ids=[item.signal_id for item in partial],
            unmet_preconditions=[
                "explicit partial-report product contract",
                "non-critical missing facets identified",
                "no provenance or safety violation",
            ],
            provenance=provenance,
        )

    failures = [signal for signal in signals if effective[signal.signal_id] == "failed"]
    if failures:
        return ActionRecommendation(
            action="human_review",
            status="blocked",
            reason="a failed signal is present without an approved action mapping",
            triggering_signal_ids=[item.signal_id for item in failures],
            unmet_preconditions=["failed signal requires an explicit, calibrated action policy"],
            provenance=provenance,
        )

    return ActionRecommendation(
        action="continue",
        status="candidate",
        reason="all supplied signals passed their thresholds",
        triggering_signal_ids=[signal.signal_id for signal in signals],
        preconditions=["continue remains advisory and does not alter Graph execution"],
        provenance=provenance,
    )


def _update_signal_provenance(
    signals: Sequence[QualitySignal],
    *,
    snapshot_fingerprint: str,
) -> list[QualitySignal]:
    return [
        signal.model_copy(
            update={
                "provenance": signal.provenance.model_copy(
                    update={"snapshot_fingerprint": snapshot_fingerprint}
                )
            }
        )
        for signal in signals
    ]


def build_quality_action_advisory(
    *,
    planning: PlanningQualityResult | None = None,
    research_coverage: ResearchCoverageResult | None = None,
    run_evaluation: RunEvaluationResult | None = None,
    state: Any | None = None,
    case_id: str | None = None,
    additional_signals: Sequence[QualitySignal] = (),
    threshold_policy: Mapping[str, ThresholdSpec] | None = None,
    configuration: Mapping[str, Any] | None = None,
) -> QualityActionEvaluation:
    """Compose existing observations into one deterministic advisory result."""

    signals: list[QualitySignal] = []
    if planning is not None:
        signals.extend(planning_quality_signals(planning, case_id=case_id))
    if research_coverage is not None:
        signals.extend(research_coverage_signals(research_coverage, case_id=case_id))
    if run_evaluation is not None:
        signals.extend(run_evaluation_signals(run_evaluation))
    if state is not None:
        signals.extend(runtime_observation_signals(state))
    signals.extend(additional_signals)

    if threshold_policy:
        signals = [
            signal.model_copy(
                update={"threshold": threshold_policy.get(signal.metric, signal.threshold)}
            )
            for signal in signals
        ]

    resolved_thresholds = {
        key: value.model_dump(mode="json")
        for key, value in sorted(
            {
                signal.metric: signal.threshold
                for signal in signals
                if signal.threshold is not None
            }.items()
        )
    }
    input_fingerprint = quality_action_content_fingerprint(
        {
            "planning": planning,
            "research_coverage": research_coverage,
            "run_evaluation": run_evaluation,
            "state": state,
            "case_id": case_id,
            "additional_signals": list(additional_signals),
        }
    )
    resolved_configuration = {
        **dict(configuration or {}),
        "quality_action_policy_version": QUALITY_ACTION_POLICY_VERSION,
        "thresholds": resolved_thresholds,
        "input_fingerprint": input_fingerprint,
    }
    metric_names = sorted({signal.metric for signal in signals})
    snapshot = build_evaluation_snapshot(
        evaluator_version=QUALITY_ACTION_EVALUATOR_VERSION,
        expected_completed_nodes=(),
        metric_names=metric_names,
        configuration=resolved_configuration,
    )
    signals = _update_signal_provenance(signals, snapshot_fingerprint=snapshot.fingerprint)
    recommendation = recommend_action(signals)
    recommendation = recommendation.model_copy(
        update={
            "provenance": recommendation.provenance.model_copy(
                update={"snapshot_fingerprint": snapshot.fingerprint}
            )
        }
    )
    return QualityActionEvaluation(
        evaluation_snapshot=snapshot,
        signals=signals,
        recommendation=recommendation,
        configuration=resolved_configuration,
    )


__all__ = [
    "ActionKind",
    "ActionRecommendation",
    "QUALITY_ACTION_EVALUATOR_VERSION",
    "QUALITY_ACTION_POLICY_VERSION",
    "QualityActionEvaluation",
    "QualitySignal",
    "SignalKind",
    "SignalProvenance",
    "SignalScope",
    "SignalStatus",
    "ThresholdSpec",
    "build_quality_action_advisory",
    "planning_quality_signals",
    "quality_action_content_fingerprint",
    "recommend_action",
    "research_coverage_signals",
    "run_evaluation_signals",
    "runtime_observation_signals",
]
