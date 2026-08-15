"""Pure, deterministic evaluation of existing ResearchState-compatible results."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.evaluation.contracts import (
    CoverageEvaluationSummary,
    EvaluationCase,
    EvaluationMetric,
    ReportEvaluationSummary,
    RunEvaluationResult,
    TraceEvaluationSummary,
    UsageEvaluationSummary,
)


EXPECTED_COMPLETED_NODES = ("plan", "search", "synthesize", "write_report")
EVALUATOR_VERSION = "p3.2b.v1"
_MISSING = object()


def _read(value: Any, field: str, default: Any = _MISSING) -> Any:
    if isinstance(value, Mapping):
        return value.get(field, default)
    return getattr(value, field, default)


def _list(value: Any, field: str) -> list[Any] | None:
    item = _read(value, field, _MISSING)
    return item if isinstance(item, (list, tuple)) else None


def _metric(name: str, status: str, *, value: Any = None, reason: str | None = None, **metadata: Any) -> EvaluationMetric:
    return EvaluationMetric(name=name, status=status, value=value, reason=reason, metadata=metadata)


def _trace_summary(state: Any, completed: bool) -> tuple[TraceEvaluationSummary, EvaluationMetric]:
    trace = _list(state, "agent_trace")
    if trace is None:
        return TraceEvaluationSummary(), _metric(
            "trace_completeness", "unavailable", reason="agent_trace is absent from this result"
        )

    events = list(trace)
    malformed = 0
    identity_mismatches = 0
    nodes: set[str] = set()
    event_types: list[str] = []
    failed = 0
    expected_run_id = _read(state, "run_id", None) or None
    observed_trace_ids: set[str] = set()
    for event in events:
        event_type = _read(event, "event_type", "")
        node = _read(event, "node", "")
        attempt = _read(event, "attempt", None)
        started = _read(event, "started_at", "")
        ended = _read(event, "ended_at", "")
        duration = _read(event, "duration_seconds", None)
        trace_id = _read(event, "trace_id", "")
        if not all((event_type, node, trace_id, started, ended)) or not isinstance(attempt, int) or attempt < 1 or not isinstance(duration, (int, float)) or duration < 0:
            malformed += 1
        if trace_id:
            observed_trace_ids.add(str(trace_id))
        if expected_run_id and trace_id != expected_run_id:
            identity_mismatches += 1
        if event_type == "node" and node:
            nodes.add(str(node))
        event_types.append(str(event_type))
        if _read(event, "status", "") == "failed":
            failed += 1

    missing = [node for node in EXPECTED_COMPLETED_NODES if node not in nodes] if completed else []
    summary = TraceEvaluationSummary(
        available=True,
        event_count=len(events),
        node_count=event_types.count("node"),
        llm_count=event_types.count("llm"),
        tool_count=event_types.count("tool"),
        failed_event_count=failed,
        missing_completed_nodes=missing,
        malformed_event_count=malformed,
        identity_mismatch_count=identity_mismatches + max(0, len(observed_trace_ids) - 1),
    )
    if completed and (missing or malformed or summary.identity_mismatch_count):
        return summary, _metric(
            "trace_completeness", "failed", value=summary.model_dump(),
            reason="completed run has missing nodes, malformed events, or inconsistent trace identity",
        )
    return summary, _metric("trace_completeness", "passed", value=summary.model_dump())


def _usage_summary(state: Any) -> tuple[UsageEvaluationSummary, EvaluationMetric]:
    usage = _read(state, "usage", _MISSING)
    if usage is _MISSING or usage is None:
        return UsageEvaluationSummary(), _metric(
            "usage_integrity", "unavailable", reason="usage is absent; legacy totals are not recomputed"
        )
    fields = ("llm_calls", "tool_calls", "input_tokens", "output_tokens", "total_tokens", "latency_seconds")
    values = {field: _read(usage, field, _MISSING) for field in fields}
    if any(value is _MISSING for value in values.values()):
        return UsageEvaluationSummary(), _metric(
            "usage_integrity", "unavailable", reason="usage is incomplete; no fallback calculation is performed"
        )
    summary = UsageEvaluationSummary(available=True, **values)
    nonnegative = all(isinstance(value, (int, float)) and value >= 0 for value in values.values())
    total_consistent = values["total_tokens"] == values["input_tokens"] + values["output_tokens"]
    if not nonnegative or not total_consistent:
        return summary, _metric(
            "usage_integrity", "failed", value=summary.model_dump(),
            reason="usage has a negative value or inconsistent total_tokens",
        )
    return summary, _metric("usage_integrity", "passed", value=summary.model_dump())


def _coverage_summary(state: Any, completed: bool) -> tuple[CoverageEvaluationSummary, EvaluationMetric]:
    documents = _list(state, "documents")
    evidence = _list(state, "evidence")
    findings = _list(state, "findings")
    diagnostics = _read(state, "evidence_diagnostics", _MISSING)
    diagnostics_status = _read(diagnostics, "status", None) if diagnostics is not _MISSING else None
    evidence_status = "unavailable" if diagnostics_status in {None, "disabled", "not_run"} else "passed"
    summary = CoverageEvaluationSummary(
        documents=len(documents or ()), evidence=len(evidence or ()), findings=len(findings or ()), evidence_status=evidence_status
    )
    if documents is None or findings is None:
        return summary, _metric(
            "research_coverage", "unavailable", value=summary.model_dump(), reason="documents or findings are absent from this result"
        )
    if completed and (not documents or not findings):
        return summary, _metric(
            "research_coverage", "failed", value=summary.model_dump(),
            reason="completed run has no documents or no findings",
        )
    return summary, _metric("research_coverage", "passed", value=summary.model_dump())


def _report_summary(state: Any, completed: bool) -> tuple[ReportEvaluationSummary, EvaluationMetric]:
    final_report = _read(state, "final_report", _MISSING)
    report = _read(state, "report", _MISSING)
    sections = _list(state, "report_sections")
    if sections is None and report is not _MISSING:
        sections = _list(report, "sections")
    if final_report is _MISSING and report is not _MISSING:
        final_report = _read(report, "content", _MISSING)
    if final_report is _MISSING:
        return ReportEvaluationSummary(), _metric(
            "report_structure", "unavailable", reason="report fields are absent from this result"
        )
    text = str(final_report or "")
    inferred_sections = sum(1 for line in text.splitlines() if line.startswith("## "))
    summary = ReportEvaluationSummary(
        available=True,
        generated=bool(text.strip()),
        has_heading=any(line.startswith("# ") for line in text.splitlines()),
        section_count=len(sections) if sections is not None else inferred_sections,
    )
    if completed and (not summary.generated or not summary.has_heading or summary.section_count < 1):
        return summary, _metric(
            "report_structure", "failed", value=summary.model_dump(),
            reason="completed run lacks report content, a top-level heading, or sections",
        )
    return summary, _metric("report_structure", "passed", value=summary.model_dump())


def evaluate_run(
    state: Any,
    *,
    case: EvaluationCase | None = None,
    dataset_id: str | None = None,
    dataset_version: str | None = None,
) -> RunEvaluationResult:
    """Evaluate an existing state without writing, invoking agents, or estimating usage."""
    status = _read(state, "status", _MISSING)
    stage = _read(state, "current_stage", _MISSING)
    error = _read(state, "error", None)
    report_text = _read(state, "final_report", None)
    completed = status == "completed" and stage == "complete" and bool(report_text) and not error
    lifecycle_known = status is not _MISSING and stage is not _MISSING
    lifecycle_metric = (
        _metric("run_completion", "passed", value={"status": status, "current_stage": stage})
        if completed
        else _metric(
            "run_completion", "failed" if lifecycle_known else "unavailable",
            value={"status": None if status is _MISSING else status, "current_stage": None if stage is _MISSING else stage},
            reason=None if lifecycle_known else "lifecycle fields are absent from this result",
        )
    )
    trace, trace_metric = _trace_summary(state, completed)
    usage, usage_metric = _usage_summary(state)
    coverage, coverage_metric = _coverage_summary(state, completed)
    report, report_metric = _report_summary(state, completed)
    failure_metric = _metric(
        "failure_signals",
        "failed" if error or trace.failed_event_count else "passed",
        value={"error": error, "failed_trace_events": trace.failed_event_count},
    )
    metrics = [lifecycle_metric, trace_metric, usage_metric, coverage_metric, report_metric, failure_metric]
    if lifecycle_metric.status == "unavailable":
        outcome = "unavailable"
    elif any(metric.status == "failed" for metric in metrics):
        outcome = "failed"
    else:
        outcome = "passed"
    return RunEvaluationResult(
        evaluator_version=EVALUATOR_VERSION,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        case_id=case.case_id if case else None,
        query=case.query if case else str(_read(state, "query", _read(state, "research_topic", "")) or ""),
        run_id=_read(state, "run_id", None) or None,
        outcome=outcome,
        metrics=metrics,
        trace=trace,
        usage=usage,
        coverage=coverage,
        report=report,
        error=str(error) if error else None,
        metadata={"llm_as_judge": False, "reads_existing_state_only": True},
    )
