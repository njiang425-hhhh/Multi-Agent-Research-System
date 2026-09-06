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
from src.state_compat import canonical_report_text


EXPECTED_COMPLETED_NODES = ("plan", "search", "synthesize", "write_report")
EVALUATOR_VERSION = "p5.2.v1"
_MISSING = object()
_URL_PATTERN = re.compile(r"https?://[^\s<>\]\)]+")
_CITATION_PATTERN = re.compile(r"\[(\d+)\]")


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
    report = _read(state, "report", _MISSING)
    final_report = canonical_report_text(state)
    sections = _list(report, "sections") if report is not _MISSING else None
    if sections is None:
        sections = _list(state, "report_sections")
    if final_report is None:
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
    sections = _list(report, "sections") if report is not _MISSING else None
    if sections is None:
        sections = _list(state, "report_sections")
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


def _citation_numbers(text: Any) -> list[int]:
    return [int(value) for value in _CITATION_PATTERN.findall(str(text or ""))]


def _report_sections(state: Any, report: Any) -> list[Any]:
    sections = _list(report, "sections") if report is not _MISSING else None
    if sections is None:
        sections = _list(state, "report_sections")
    return list(sections or ())


def _citation_integrity_summary(
    state: Any,
    *,
    report: Any,
    final_report: Any,
    documents: list[Any],
) -> tuple[ResearchQualitySummary, EvaluationMetric]:
    """Check the report's citation map without making a factuality claim."""

    document_urls = [str(_read(document, "uri", "") or "").strip() for document in documents]
    marker_numbers = _citation_numbers(final_report)
    invalid_numbers = [number for number in marker_numbers if number < 1 or number > len(document_urls)]
    report_citations = _list(report, "citations") if report is not _MISSING else None
    if report_citations is None:
        report_citations = []
    citation_map_mismatches = sum(
        1
        for index, url in enumerate(document_urls)
        if index >= len(report_citations) or str(report_citations[index] or "").strip() != url
    ) + max(0, len(report_citations) - len(document_urls))

    section_mismatches = 0
    for section in _report_sections(state, report):
        expected_sources: list[str] = []
        for number in _citation_numbers(_read(section, "content", "")):
            if 1 <= number <= len(document_urls):
                url = document_urls[number - 1]
                if url not in expected_sources:
                    expected_sources.append(url)
        actual_sources = [str(url or "").strip() for url in (_list(section, "sources") or ())]
        if actual_sources != expected_sources:
            section_mismatches += 1

    # A bibliography map with no in-text citations is not an actual citation
    # map.  This catches Writer fallback prose that only appends references.
    if document_urls and not marker_numbers:
        citation_map_mismatches += 1

    summary = ResearchQualitySummary(
        distinct_source_count=len(set(document_urls)),
        cited_source_count=len({document_urls[number - 1] for number in marker_numbers if 1 <= number <= len(document_urls)}),
        citation_marker_count=len(marker_numbers),
        invalid_citation_count=len(invalid_numbers),
        citation_map_mismatch_count=citation_map_mismatches,
        section_citation_mismatch_count=section_mismatches,
        report_character_count=len(str(final_report or "")) if final_report is not None else 0,
    )
    if final_report is None:
        return summary, _metric(
            "citation_integrity", "unavailable", value=summary.model_dump(),
            reason="report fields are absent; citation map cannot be established",
        )
    if not documents:
        return summary, _metric(
            "citation_integrity", "failed", value=summary.model_dump(),
            reason="report has no canonical Documents for its citation map",
        )
    if invalid_numbers or citation_map_mismatches or section_mismatches:
        return summary, _metric(
            "citation_integrity", "failed", value=summary.model_dump(),
            reason="citation numbers, bibliography map, or section source URLs do not match canonical Documents",
        )
    return summary, _metric("citation_integrity", "passed", value=summary.model_dump())


def _evidence_grounding_metric(
    state: Any,
    *,
    documents: list[Any],
    findings: list[Any],
    base_summary: ResearchQualitySummary,
    case: EvaluationCase | None,
) -> tuple[ResearchQualitySummary, EvaluationMetric]:
    """Evaluate explicit Evidence-to-Finding links when Evidence actually ran.

    This verifies stored provenance and support/contradiction relationships. It
    is deliberately not a semantic factuality or entailment guarantee.
    """

    diagnostics = _read(state, "evidence_diagnostics", _MISSING)
    status = _read(diagnostics, "status", None) if diagnostics is not _MISSING else None
    if status in {None, "disabled", "not_run"}:
        return base_summary, _metric(
            "evidence_grounding", "unavailable", value=base_summary.model_dump(),
            reason="Evidence is disabled or was not run for this result",
        )
    if status == "failed":
        return base_summary, _metric(
            "evidence_grounding", "unavailable", value=base_summary.model_dump(),
            reason="Evidence execution failed; no complete grounding observation is available",
        )

    document_by_id = {
        str(_read(document, "document_id", "")): document
        for document in documents
        if _read(document, "document_id", "")
    }
    evidence_by_id = {
        str(_read(item, "evidence_id", "")): item
        for item in (_list(state, "evidence") or ())
        if _read(item, "evidence_id", "")
    }

    def valid_evidence(item: Any, source_ids: set[str], relation: str) -> bool:
        document_id = str(_read(item, "document_id", "") or "")
        document = document_by_id.get(document_id)
        if document is None or _read(item, "relation", "") != relation:
            return False
        if source_ids and document_id not in source_ids:
            return False
        if _read(item, "status", "") not in {"grounded", "partial"}:
            return False
        if str(_read(item, "source_url", "") or "") != str(_read(document, "uri", "") or ""):
            return False
        quote = str(_read(item, "source_quote", "") or "")
        source_text = str(_read(document, "content", "") or _read(document, "snippet", "") or "")
        return bool(quote and (not source_text or quote in source_text))

    supported = contradicted = unsupported = 0
    for finding in findings:
        source_ids = {str(value) for value in (_list(finding, "source_document_ids") or ()) if value}
        support_items = [
            evidence_by_id[item_id]
            for item_id in (_list(finding, "evidence_refs") or ())
            if item_id in evidence_by_id
            and valid_evidence(evidence_by_id[item_id], source_ids, "supports")
        ]
        contradiction_items = [
            evidence_by_id[item_id]
            for item_id in (_list(finding, "contradictory_evidence_refs") or ())
            if item_id in evidence_by_id
            and valid_evidence(evidence_by_id[item_id], source_ids, "contradicts")
        ]
        if contradiction_items:
            contradicted += 1
        elif support_items:
            supported += 1
        else:
            unsupported += 1

    summary = base_summary.model_copy(
        update={
            "evidence_supported_finding_count": supported,
            "evidence_contradicted_finding_count": contradicted,
            "evidence_unsupported_finding_count": unsupported,
        }
    )
    if not findings:
        return summary, _metric(
            "evidence_grounding", "unavailable", value=summary.model_dump(),
            reason="Evidence ran but this result has no Findings to evaluate",
        )
    rubric = case.quality_rubric if case is not None else ResearchQualityRubric()
    # ``min_grounded_citations`` belonged to the old URL-provenance metric and
    # is intentionally not reinterpreted as a finding-support threshold.
    minimum = rubric.min_evidence_supported_findings or 0
    if supported < minimum or contradicted or unsupported:
        return summary, _metric(
            "evidence_grounding", "failed", value=summary.model_dump(),
            reason="Findings are unsupported, contradicted, or below the configured Evidence support threshold",
        )
    return summary, _metric("evidence_grounding", "passed", value=summary.model_dump())


def _quality_metrics(
    state: Any,
    case: EvaluationCase | None,
    report_summary: ReportEvaluationSummary,
) -> tuple[ResearchQualitySummary, list[EvaluationMetric]]:
    """Score only observable State/report fields against a case's fixed rubric."""

    rubric = case.quality_rubric if case is not None else ResearchQualityRubric()
    documents = _list(state, "documents")
    report = _read(state, "report", _MISSING)
    final_report = canonical_report_text(state)
    document_values = list(documents or ())
    summary, citation_metric = _citation_integrity_summary(
        state,
        report=report,
        final_report=final_report,
        documents=document_values,
    )
    summary = summary.model_copy(update={"rubric": rubric})
    citation_metric = citation_metric.model_copy(update={"value": summary.model_dump()})
    summary, evidence_metric = _evidence_grounding_metric(
        state,
        documents=document_values,
        findings=list(_list(state, "findings") or ()),
        base_summary=summary,
        case=case,
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

    if final_report is None:
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
    return summary, [source_metric, citation_metric, evidence_metric, completeness_metric]


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
    report_text = canonical_report_text(state)
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
