"""Fake-only tests for the manual P5.3 workload harness and archive format."""

import asyncio
import json

import pytest

from src.evaluation.dataset import FIXED_EVALUATION_DATASET
from src.evaluation.real_workload import (
    archive_real_workload_benchmark,
    render_benchmark_report,
    run_real_workload_benchmark,
)


def _trace(run_id: str):
    return [
        {
            "trace_id": run_id,
            "node": node,
            "agent": f"Fake{node}",
            "operation": node,
            "event_type": "node",
            "attempt": 1,
            "status": "completed",
            "started_at": "2026-08-19T00:00:00+00:00",
            "ended_at": "2026-08-19T00:00:01+00:00",
            "duration_seconds": duration,
        }
        for node, duration in (("plan", 0.1), ("search", 0.2), ("synthesize", 0.3), ("write_report", 0.4))
    ]


def _state(case, mode):
    run_id = f"fake-{mode}-{case.case_id}"
    if case.scenario == "failure":
        return {
            "research_topic": case.query,
            "run_id": run_id,
            "status": "failed",
            "current_stage": "failed",
            "terminal_reason": "agent_failed",
            "error": "fake provider failure",
            "agent_trace": _trace(run_id),
            "usage": {
                "llm_calls": 1,
                "tool_calls": 1,
                "input_tokens": 2,
                "output_tokens": 0,
                "total_tokens": 2,
                "latency_seconds": 1.0,
            },
            "llm_call_details": [
                {"agent": "ResearchPlanner", "operation": "plan", "attempt": 1, "error": "fake provider failure"}
            ],
        }
    first_url = "https://example.com/one"
    second_url = "https://example.com/two"
    evidence = []
    diagnostics = {"status": "disabled"}
    details = []
    if mode != "disabled":
        evidence = [
            {
                "evidence_id": "evidence-one",
                "document_id": "doc-one",
                "source_url": first_url,
                "source_quote": "first quote",
                "status": "partial" if mode == "partial" else "grounded",
            },
            {
                "evidence_id": "evidence-two",
                "document_id": "doc-two",
                "source_url": second_url,
                "source_quote": "second quote",
                "status": "partial" if mode == "partial" else "grounded",
            },
        ]
        diagnostics = {"status": "partial" if mode == "partial" else "completed"}
        details = [
            {"agent": "EvidenceAnalyzer", "operation": "analyze_document", "attempt": 1, "duration": 0.15, "success": True}
        ]
    return {
        "research_topic": case.query,
        "run_id": run_id,
        "status": "completed",
        "current_stage": "complete",
        "terminal_reason": "completed",
        "documents": [
            {"document_id": "doc-one", "uri": first_url},
            {"document_id": "doc-two", "uri": second_url},
        ],
        "evidence": evidence,
        "evidence_diagnostics": diagnostics,
        "findings": [{"finding_id": "finding-one"}],
        "report_sections": [
            {"title": "One", "sources": [first_url]},
            {"title": "Two", "sources": [second_url]},
        ],
        "report": {"citations": [first_url, second_url]},
        "final_report": "# Report\n\n## One\n\n" + ("Evidence. " * 12) + "\n\n## Two\n\n" + ("Evidence. " * 12),
        "usage": {
            "llm_calls": 3 if mode == "disabled" else 5,
            "tool_calls": 2,
            "input_tokens": 11 if mode == "disabled" else 15,
            "output_tokens": 7 if mode == "disabled" else 10,
            "total_tokens": 18 if mode == "disabled" else 25,
            "latency_seconds": 1.0 if mode == "disabled" else 1.4,
        },
        "agent_trace": _trace(run_id),
        "llm_call_details": details,
    }


def test_real_workload_harness_separates_observed_and_derived_data_and_archives(tmp_path) -> None:
    states = {
        (mode, case.case_id): _state(case, mode)
        for mode in ("disabled", "enabled", "partial")
        for case in FIXED_EVALUATION_DATASET.cases
    }

    async def fake_runner(case, mode):
        return states[(mode, case.case_id)]

    result = asyncio.run(
        run_real_workload_benchmark(fake_runner, configuration={"execution": "fake"})
    )
    observations = {(item.evidence_mode, item.case_id): item for item in result.observed_cases}
    slo = {item.evidence_mode: item for item in result.derived_metrics.slo_by_mode}

    assert result.benchmark_version == "p5.3.v1"
    assert len(result.observed_cases) == 18
    assert len(result.derived_metrics.case_quality) == 18
    assert observations[("enabled", "ai-regulation-overview")].data_kind == "observed"
    assert observations[("enabled", "ai-regulation-overview")].performance.node_latency_seconds == {
        "plan": 0.1,
        "search": 0.2,
        "synthesize": 0.3,
        "write_report": 0.4,
    }
    assert observations[("enabled", "ai-regulation-overview")].performance.evidence_sidecar_latency_seconds == 0.15
    assert observations[("partial", "ai-regulation-overview")].reliability.partial_observed is True
    assert observations[("disabled", "search-provider-failure")].reliability.provider_errors[0].source == "llm"
    assert slo["disabled"].success_rate == pytest.approx(5 / 6, abs=1e-6)
    assert slo["enabled"].quality_pass_rate == pytest.approx(5 / 6, abs=1e-6)
    assert slo["enabled"].provider_failure_rate == pytest.approx(1 / 6, abs=1e-6)
    assert slo["enabled"].writer_latency_fraction is not None
    assert result.derived_metrics.evidence_value_comparisons[0]["mode"] == "enabled"
    assert "observed_total_wall_latency_seconds_delta" in result.derived_metrics.evidence_value_comparisons[0]
    assert result.derived_metrics.evidence_value_comparisons[0]["comparison_status"] == "comparable"

    json_path, report_path = archive_real_workload_benchmark(result, tmp_path / "archive")

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["observed_cases"][0]["data_kind"] == "observed"
    assert payload["derived_metrics"]["data_kind"] == "derived"
    assert "Observed case/mode runs" in report_path.read_text(encoding="utf-8")
    assert "Derived SLO summary" in render_benchmark_report(result)
