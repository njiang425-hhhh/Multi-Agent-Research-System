"""Fake-only deterministic coverage for P6 repeatability aggregation."""

import asyncio
import json

from src.evaluation.dataset import FIXED_EVALUATION_DATASET
from src.evaluation.repeatability import (
    RepeatabilityDecisionCriteria,
    archive_repeatability_benchmark,
    render_repeatability_report,
    run_repeatability_benchmark,
)


def _trace(run_id: str):
    return [
        {"trace_id": run_id, "node": node, "agent": "Fake", "operation": node, "event_type": "node", "attempt": 1,
         "status": "completed", "started_at": "2026-08-20T00:00:00+00:00", "ended_at": "2026-08-20T00:00:01+00:00", "duration_seconds": duration}
        for node, duration in (("plan", 0.1), ("search", 0.2), ("synthesize", 0.3), ("write_report", 0.4))
    ]


def _state(case, mode, call_number):
    run_id = f"repeat-{call_number}-{mode}-{case.case_id}"
    if case.scenario == "failure":
        return {"research_topic": case.query, "run_id": run_id, "status": "failed", "current_stage": "failed", "error": "fake provider failure", "agent_trace": _trace(run_id), "usage": {"llm_calls": 1, "tool_calls": 1, "input_tokens": 1, "output_tokens": 0, "total_tokens": 1, "latency_seconds": 1.0}, "llm_call_details": [{"agent": "Search", "operation": "search", "attempt": 1, "error": "fake provider failure"}]}
    urls = ["https://example.com/one", "https://example.com/two"]
    evidence = [] if mode == "disabled" else [
        {"evidence_id": f"evidence-{index}", "document_id": f"doc-{index}", "source_url": url, "source_quote": "quote", "status": "grounded"}
        for index, url in enumerate(urls)
    ]
    details = [{"agent": "EvidenceAnalyzer", "operation": "analyze_document", "attempt": 1, "error": "fake transient provider error"}] if mode == "enabled" and case.case_id == "ai-regulation-overview" else []
    sections = [{"title": "One", "sources": [urls[0]]}, {"title": "Two", "sources": [urls[1]]}]
    report_text = "# Report\n\n## One\n\n" + ("Evidence. " * 12) + "\n\n## Two\n\n" + ("Evidence. " * 12)
    return {"research_topic": case.query, "run_id": run_id, "status": "completed", "current_stage": "complete", "terminal_reason": "completed", "documents": [{"document_id": f"doc-{index}", "uri": url} for index, url in enumerate(urls)], "evidence": evidence, "evidence_diagnostics": {"status": "disabled" if mode == "disabled" else "completed"}, "findings": [{"finding_id": "finding"}], "report_sections": sections, "report": {"citations": urls, "sections": sections}, "final_report": report_text, "usage": {"llm_calls": 3 if mode == "disabled" else 5, "tool_calls": 2, "input_tokens": 10, "output_tokens": 10, "total_tokens": 20, "latency_seconds": 1.0 if mode == "disabled" else 1.2}, "agent_trace": _trace(run_id), "llm_call_details": details}


def test_repeatability_outputs_observed_runs_derived_dispersion_matched_pairs_and_assessments(tmp_path) -> None:
    calls = 0

    async def runner(case, mode):
        nonlocal calls
        calls += 1
        return _state(case, mode, calls)

    result = asyncio.run(run_repeatability_benchmark(runner, rounds=3, configuration={"execution": "fake", "ci": True}))
    aggregate = {item.evidence_mode: item for item in result.derived_metrics.mode_aggregates}
    matched = {(item.evidence_mode, item.case_id): item for item in result.derived_metrics.matched_cross_round_comparisons}
    assessment = {item.initiative: item for item in result.derived_metrics.initiative_assessments}

    assert len(result.observed_runs) == 3
    assert all(item.data_kind == "observed" for item in result.observed_runs)
    assert result.derived_metrics.data_kind == "derived"
    assert aggregate["enabled"].quality_pass_rate.sample_count == 3
    assert aggregate["enabled"].quality_metric_pass_rates["evidence_grounding"].sample_count == 3
    assert aggregate["enabled"].provider_error_rate.sample_standard_deviation == 0.0
    assert matched[("enabled", "ai-regulation-overview")].matched_success_round_count == 3
    assert matched[("enabled", "ai-regulation-overview")].repeated_benefit_rate == 0.0
    assert assessment["evidence_selector"].status == "insufficient"
    assert assessment["writer_optimization"].status == "not_assessable"
    assert assessment["provider_resilience"].status == "not_assessable"

    json_path, report_path = archive_repeatability_benchmark(result, tmp_path / "repeatability")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["observed_runs"][0]["data_kind"] == "observed"
    assert payload["derived_metrics"]["data_kind"] == "derived"
    assert "Derived cross-round matched comparison" in report_path.read_text(encoding="utf-8")
    assert "initiative assessments" in render_repeatability_report(result)


def test_repeatability_requires_disabled_baseline_and_assesses_explicit_slos_only() -> None:
    async def runner(case, mode):
        return _state(case, mode, 1)

    criteria = RepeatabilityDecisionCriteria(writer_latency_slo_seconds=0.1, provider_error_rate_slo=0.1)
    result = asyncio.run(run_repeatability_benchmark(runner, rounds=2, criteria=criteria, configuration={"execution": "fake"}))
    assessment = {item.initiative: item for item in result.derived_metrics.initiative_assessments}

    assert assessment["writer_optimization"].status == "insufficient"
    assert assessment["provider_resilience"].status == "insufficient"
