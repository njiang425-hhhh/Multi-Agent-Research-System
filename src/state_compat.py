"""Explicit compatibility helpers for the incremental ResearchState migration.

This module is the only place that translates between legacy State fields and
the canonical contract.  It intentionally uses ordinary functions rather than
Pydantic aliases or validators: callers choose when migration occurs and the
stored legacy payload remains inspectable.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.evidence.adapters import scored_search_results_to_documents
from src.evidence.compat import key_findings_to_findings
from src.state import (
    Document,
    Finding,
    Report,
    ReportSection,
    ResearchPlan,
    ResearchState,
    SearchResult,
    UsageMetrics,
)


def _read(value: ResearchState | Mapping[str, Any], field: str, default: Any = None) -> Any:
    return value.get(field, default) if isinstance(value, Mapping) else getattr(value, field, default)


def _field_was_supplied(state: ResearchState, field: str) -> bool:
    return field in state.model_fields_set


def canonical_query(state: ResearchState | Mapping[str, Any]) -> str:
    """Return ``query`` when present, otherwise the legacy topic."""

    return str(_read(state, "query", "") or _read(state, "research_topic", "") or "")


def canonical_plan(state: ResearchState | Mapping[str, Any]) -> ResearchPlan | None:
    """Return the canonical plan with an explicit legacy fallback."""

    value = _read(state, "research_plan", None) or _read(state, "plan", None)
    if value is None or isinstance(value, ResearchPlan):
        return value
    try:
        return ResearchPlan.model_validate(value)
    except Exception:
        return None


def canonical_iteration(state: ResearchState | Mapping[str, Any]) -> int:
    """Prefer an explicitly supplied canonical iteration value."""

    if isinstance(state, ResearchState) and _field_was_supplied(state, "iteration"):
        return int(state.iteration)
    value = _read(state, "iteration", None)
    if value not in (None, 0):
        return int(value)
    return int(_read(state, "iterations", 0) or 0)


def canonical_usage(state: ResearchState | Mapping[str, Any]) -> UsageMetrics:
    """Read canonical usage, hydrating legacy totals only when it was omitted."""

    usage = _read(state, "usage", None)
    supplied = not isinstance(state, ResearchState) or _field_was_supplied(state, "usage")
    if isinstance(usage, Mapping):
        usage = UsageMetrics.model_validate(usage)
    if isinstance(usage, UsageMetrics) and supplied:
        # Preserve V1-only metrics while filling resource totals that were not
        # supplied in a mixed legacy fixture.  Explicit zero is canonical.
        usage_fields = usage.model_fields_set
        legacy_calls = int(_read(state, "llm_calls", 0) or 0)
        legacy_input = int(_read(state, "total_input_tokens", 0) or 0)
        legacy_output = int(_read(state, "total_output_tokens", 0) or 0)
        calls = usage.llm_calls if "llm_calls" in usage_fields else legacy_calls
        input_tokens = usage.input_tokens if "input_tokens" in usage_fields else legacy_input
        output_tokens = usage.output_tokens if "output_tokens" in usage_fields else legacy_output
        total_tokens = (
            usage.total_tokens
            if "total_tokens" in usage_fields
            else input_tokens + output_tokens
        )
        return usage.model_copy(
            update={
                "llm_calls": calls,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
            }
        )
    calls = int(_read(state, "llm_calls", 0) or 0)
    input_tokens = int(_read(state, "total_input_tokens", 0) or 0)
    output_tokens = int(_read(state, "total_output_tokens", 0) or 0)
    if calls or input_tokens or output_tokens:
        return UsageMetrics(
            llm_calls=calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        )
    if isinstance(usage, UsageMetrics):
        return usage
    return UsageMetrics()


def canonical_report(state: ResearchState | Mapping[str, Any]) -> Report | None:
    """Return the canonical report, or build a read-only view of legacy output."""

    report = _read(state, "report", None)
    if report is not None:
        if isinstance(report, Report):
            return report
        try:
            return Report.model_validate(report)
        except Exception:
            # Evaluation/archive payloads may hold a partial report summary.
            # Keep the compatibility boundary read-only and fall back below.
            pass
    final_report = _read(state, "final_report", None)
    if not final_report:
        return None
    sections = list(_read(state, "report_sections", ()) or ())
    valid_sections: list[ReportSection] = []
    for section in sections:
        try:
            valid_sections.append(
                section if isinstance(section, ReportSection) else ReportSection.model_validate(section)
            )
        except Exception:
            # Archive summaries are intentionally allowed to omit prose.
            continue
    citations: list[str] = []
    for section in sections:
        sources = _read(section, "sources", ()) or ()
        for source in sources:
            if source and source not in citations:
                citations.append(str(source))
    return Report(
        title=canonical_query(state),
        sections=valid_sections,
        content=str(final_report),
        citations=citations,
        status="completed",
    )


def canonical_report_text(state: ResearchState | Mapping[str, Any]) -> str | None:
    raw_report = _read(state, "report", None)
    raw_content = _read(raw_report, "content", None) if raw_report is not None else None
    if raw_content:
        return str(raw_content)
    report = canonical_report(state)
    if report and report.content:
        return report.content
    value = _read(state, "final_report", None)
    return str(value) if value else None


def canonical_documents(state: ResearchState | Mapping[str, Any]) -> list[Document]:
    """Return validated canonical Documents without reading legacy results."""

    documents: list[Document] = []
    for value in _read(state, "documents", ()) or ():
        try:
            documents.append(value if isinstance(value, Document) else Document.model_validate(value))
        except Exception:
            continue
    return documents


def canonical_findings(state: ResearchState | Mapping[str, Any]) -> list[Finding]:
    """Return validated canonical Findings without reading legacy strings."""

    findings: list[Finding] = []
    for value in _read(state, "findings", ()) or ():
        try:
            findings.append(value if isinstance(value, Finding) else Finding.model_validate(value))
        except Exception:
            continue
    return findings


def legacy_projection_patch(state: ResearchState, patch: Mapping[str, Any]) -> dict[str, Any]:
    """Project canonical business outputs to legacy fields at a graph boundary.

    Agent bodies return canonical task data only. This explicit boundary keeps
    historical callers, cached payloads, and UI views readable without hidden
    model-level synchronization.
    """

    projected = dict(patch)
    updated = state.model_copy(update=projected)
    if "research_plan" in projected:
        projected["plan"] = updated.research_plan
    if "documents" in projected:
        search_results: list[SearchResult] = []
        credibility_scores: list[dict[str, Any]] = []
        for document in canonical_documents(updated):
            search_results.append(
                SearchResult(
                    query=str(document.metadata.get("search_query") or canonical_query(updated)),
                    title=document.title,
                    url=document.uri,
                    snippet=document.snippet,
                    content=document.content,
                )
            )
            credibility_scores.append(dict(document.credibility or {}))
        projected["search_results"] = search_results
        projected["credibility_scores"] = credibility_scores
    if "findings" in projected:
        projected["key_findings"] = [
            finding.statement for finding in canonical_findings(updated) if finding.statement
        ]
    if "report" in projected and updated.report is not None:
        projected["report_sections"] = list(updated.report.sections)
        projected["final_report"] = updated.report.content
    return projected


def canonical_patch_from_legacy(
    state: ResearchState,
    *,
    include_semantic_projections: bool = True,
) -> dict[str, Any]:
    """Build missing canonical values without overwriting supplied canonical data."""

    patch: dict[str, Any] = {}
    if not state.query:
        patch["query"] = state.research_topic
    if state.research_plan is None and state.plan is not None:
        patch["research_plan"] = state.plan
    if include_semantic_projections and not state.documents and state.search_results:
        scores = list(state.credibility_scores)
        patch["documents"] = scored_search_results_to_documents(
            (
                result,
                scores[index] if index < len(scores) else {},
            )
            for index, result in enumerate(state.search_results)
        )
    if include_semantic_projections and not state.findings and state.key_findings:
        patch["findings"] = key_findings_to_findings(state.key_findings)
    if include_semantic_projections and state.report is None and state.final_report:
        patch["report"] = canonical_report(state)
    if not _field_was_supplied(state, "iteration") and state.iterations:
        patch["iteration"] = state.iterations
    if not _field_was_supplied(state, "usage") and (
        state.llm_calls or state.total_input_tokens or state.total_output_tokens
    ):
        patch["usage"] = canonical_usage(state)
    return patch


def hydrate_canonical_state(
    payload: ResearchState | Mapping[str, Any],
    *,
    include_semantic_projections: bool = True,
) -> ResearchState:
    """Return a canonical-ready copy of a legacy or mixed State payload.

    Existing canonical values win.  The source payload is never mutated, so
    checkpoint and cache migration remains an explicit boundary operation.
    """

    if isinstance(payload, ResearchState):
        state = payload
    else:
        raw = dict(payload)
        raw.setdefault("research_topic", str(raw.get("query") or ""))
        state = ResearchState.model_validate(raw)
    return state.model_copy(
        update=canonical_patch_from_legacy(
            state,
            include_semantic_projections=include_semantic_projections,
        )
    )
