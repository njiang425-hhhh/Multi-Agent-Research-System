"""Pure, deterministic evaluation of existing ResearchState-compatible results."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from src.evaluation.contracts import (
    CoverageEvaluationSummary,
    EvaluationCase,
    EvaluationDataset,
    EvaluationMetric,
    EvaluationSnapshot,
    ResearchQualityRubric,
    ResearchQualitySummary,
    ReportEvaluationSummary,
    RunEvaluationResult,
    TraceEvaluationSummary,
    UsageEvaluationSummary,
)
from src.evaluation.snapshot import build_evaluation_snapshot, validate_evaluation_snapshot


EXPECTED_COMPLETED_NODES = ("plan", "search", "synthesize", "write_report")
EVALUATOR_VERSION = "p5.1.v1"
_MISSING = object()
_URL_PATTERN = re.compile(r"https?://[^\s<>\]\)]+")


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


def _source_urls(values: list[Any] | tuple[Any, ...] | None) -> set[str]:
    """Return a stable, exact URL set without fetching or canonicalizing sources."""

    urls: set[str] = set()
    for value in values or ():
        text = str(value or "").strip()
        if text.startswith(("http://", "https://")):
            urls.add(text.rstrip(".,;"))
        urls.update(match.rstrip(".,;") for match in _URL_PATTERN.findall(text))
    return urls


def _report_cited_urls(state: Any, report: Any, final_report: Any) -> set[str]:
    sections = _list(state, "report_sections")
    if sections is None and report is not _MISSING:
        sections = _list(report, "sections")
    values: list[Any] = []
    for section in sections or ():
        section_sources = _list(section, "sources")
        if section_sources is not None:
            values.extend(section_sources)
    if report is not _MISSING:
        report_citations = _list(report, "citations")
        if report_citations is not None:
            values.extend(report_citations)
    if final_report is not _MISSING:
        values.append(final_report)
    return _source_urls(values)


def _quality_metrics(
    state: Any,
    case: EvaluationCase | None,
    report_summary: ReportEvaluationSummary,
) -> tuple[ResearchQualitySummary, list[EvaluationMetric]]:
    """Score only observable State/report fields against a case's fixed rubric."""

    rubric = case.quality_rubric if case is not None else ResearchQualityRubric()
    documents = _list(state, "documents")
    evidence = _list(state, "evidence")
    report = _read(state, "report", _MISSING)
    final_report = _read(state, "final_report", _MISSING)
    document_urls = _source_urls([_read(document, "uri", "") for document in documents or ()])
    document_urls_by_id = {
        str(_read(document, "document_id", "")): str(_read(document, "uri", "")).strip()
        for document in documents or ()
        if _read(document, "document_id", "") and _read(document, "uri", "")
    }
    cited_urls = _report_cited_urls(state, report, final_report)
    grounded_urls = _source_urls(
        [
            _read(item, "source_url", "")
            for item in evidence or ()
            if _read(item, "status", "grounded") in {"grounded", "partial"}
            and bool(_read(item, "evidence_id", ""))
            and bool(_read(item, "source_quote", ""))
            and _read(item, "source_url", "")
            == document_urls_by_id.get(str(_read(item, "document_id", "")))
        ]
    )
    grounded_citations = cited_urls & grounded_urls
    ungrounded_citations = cited_urls - grounded_urls
    summary = ResearchQualitySummary(
        rubric=rubric,
        distinct_source_count=len(document_urls),
        cited_source_count=len(cited_urls),
        grounded_citation_count=len(grounded_citations),
        ungrounded_citation_count=len(ungrounded_citations),
        report_character_count=len(str(final_report or "")) if final_report is not _MISSING else 0,
    )

    if documents is None:
        source_metric = _metric(
            "source_coverage", "unavailable", value=summary.model_dump(),
            reason="documents are absent from this result",
        )
    elif summary.distinct_source_count < rubric.min_distinct_sources:
        source_metric = _metric(
            "source_coverage", "failed", value=summary.model_dump(),
            reason="distinct document sources are below the case minimum",
        )
    else:
        source_metric = _metric("source_coverage", "passed", value=summary.model_dump())

    if evidence is None:
        citation_metric = _metric(
            "grounded_citation", "unavailable", value=summary.model_dump(),
            reason="evidence is absent; grounded citations cannot be established",
        )
    elif final_report is _MISSING:
        citation_metric = _metric(
            "grounded_citation", "unavailable", value=summary.model_dump(),
            reason="report fields are absent; report citations cannot be established",
        )
    elif (
        summary.grounded_citation_count < rubric.min_grounded_citations
        or summary.ungrounded_citation_count > 0
    ):
        citation_metric = _metric(
            "grounded_citation", "failed", value=summary.model_dump(),
            reason="report citations are ungrounded or below the case minimum",
        )
    else:
        citation_metric = _metric("grounded_citation", "passed", value=summary.model_dump())

    if final_report is _MISSING:
        completeness_metric = _metric(
            "report_completeness", "unavailable", value=summary.model_dump(),
            reason="report fields are absent from this result",
        )
    elif (
        not report_summary.generated
        or (rubric.require_top_level_heading and not report_summary.has_heading)
        or report_summary.section_count < rubric.min_report_sections
        or summary.report_character_count < rubric.min_report_characters
    ):
        completeness_metric = _metric(
            "report_completeness", "failed", value=summary.model_dump(),
            reason="report content, heading, sections, or length are below the case minimum",
        )
    else:
        completeness_metric = _metric("report_completeness", "passed", value=summary.model_dump())
    return summary, [source_metric, citation_metric, completeness_metric]


def evaluate_run(
    state: Any,
    *,
    case: EvaluationCase | None = None,
    dataset_id: str | None = None,
    dataset_version: str | None = None,
    dataset: EvaluationDataset | None = None,
    configuration: Mapping[str, Any] | None = None,
    evaluation_snapshot: EvaluationSnapshot | None = None,
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
    quality, quality_metrics = _quality_metrics(state, case, report)
    failure_metric = _metric(
        "failure_signals",
        "failed" if error or trace.failed_event_count else "passed",
        value={"error": error, "failed_trace_events": trace.failed_event_count},
    )
    metrics = [
        lifecycle_metric,
        trace_metric,
        usage_metric,
        coverage_metric,
        report_metric,
        *quality_metrics,
        failure_metric,
    ]
    if lifecycle_metric.status == "unavailable":
        outcome = "unavailable"
    elif any(metric.status == "failed" for metric in metrics):
        outcome = "failed"
    else:
        outcome = "passed"
    snapshot_args = {
        "evaluator_version": EVALUATOR_VERSION,
        "dataset_id": dataset_id,
        "dataset_version": dataset_version,
        "dataset": dataset,
        "case": case,
        "expected_completed_nodes": EXPECTED_COMPLETED_NODES,
        "configuration": configuration,
    }
    snapshot = (
        validate_evaluation_snapshot(evaluation_snapshot, **snapshot_args)
        if evaluation_snapshot is not None
        else build_evaluation_snapshot(**snapshot_args)
    )
    resolved_dataset_id = dataset.dataset_id if dataset is not None else dataset_id
    resolved_dataset_version = dataset.version if dataset is not None else dataset_version
    return RunEvaluationResult(
        evaluator_version=EVALUATOR_VERSION,
        evaluation_snapshot=snapshot,
        dataset_id=resolved_dataset_id,
        dataset_version=resolved_dataset_version,
        case_id=case.case_id if case else None,
        query=case.query if case else str(_read(state, "query", _read(state, "research_topic", "")) or ""),
        run_id=_read(state, "run_id", None) or None,
        outcome=outcome,
        metrics=metrics,
        trace=trace,
        usage=usage,
        coverage=coverage,
        report=report,
        quality=quality,
        error=str(error) if error else None,
        metadata={"llm_as_judge": False, "reads_existing_state_only": True},
    )
