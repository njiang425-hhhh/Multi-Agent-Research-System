"""Fake-only coverage for the P3.2b offline evaluation baseline."""

import asyncio
import json

from src.agent_trace import AgentTraceEvent
from src.evaluation.dataset import FIXED_EVALUATION_DATASET
from src.evaluation.evaluator import evaluate_run
from src.evaluation.runner import run_offline_evaluation
from src.evidence.contracts import Evidence
from src.runtime_lifecycle import create_new_run_state
from src.state import (
    Document,
    EvidenceDiagnostics,
    Finding,
    Report,
    ReportSection,
    UsageMetrics,
)


def _event(
    node: str,
    event_type: str = "node",
    *,
    status: str = "completed",
    trace_id: str = "fake-run",
) -> AgentTraceEvent:
    return AgentTraceEvent(
        trace_id=trace_id,
        node=node,
        agent=f"Fake{node}",
        operation=node,
        event_type=event_type,
        attempt=1,
        status=status,
        started_at="2026-08-15T00:00:00+00:00",
        ended_at="2026-08-15T00:00:01+00:00",
        duration_seconds=1.0,
        error="fake error" if status == "failed" else None,
    )


def _completed_state():
    state = create_new_run_state("offline evaluation topic")
    section = ReportSection(title="Summary", content="A fake section", sources=["https://example.com/source"])
    report_text = "# Offline evaluation topic\n\n## Summary\n\nA fake report."
    return state.model_copy(
        update={
            "status": "completed",
            "current_stage": "complete",
            "documents": [Document(document_id="doc-1", title="Fake source", uri="https://example.com/source")],
            "evidence": [
                Evidence(
                    evidence_id="evidence-1",
                    document_id="doc-1",
                    source_url="https://example.com/source",
                    claim="Fake claim",
                    source_quote="Fake quote",
                    text_source="snippet",
                    relation="supports",
                )
            ],
            "evidence_diagnostics": EvidenceDiagnostics(status="completed", source="p2_documents"),
            "findings": [Finding(finding_id="finding-1", statement="Fake finding", evidence_refs=["evidence-1"], status="supported")],
            "report_sections": [section],
            "final_report": report_text,
            "report": Report(title="Offline evaluation topic", sections=[section], content=report_text, status="completed"),
            "usage": UsageMetrics(llm_calls=3, tool_calls=2, input_tokens=11, output_tokens=7, total_tokens=18, latency_seconds=2.5),
            "agent_trace": [
                _event("plan", trace_id=state.run_id),
                _event("search", trace_id=state.run_id),
                _event("synthesize", trace_id=state.run_id),
                _event("write_report", trace_id=state.run_id),
                _event("plan", "llm", trace_id=state.run_id),
                _event("search", "tool", trace_id=state.run_id),
            ],
        }
    )


def _statuses(result) -> dict[str, str]:
    return {metric.name: metric.status for metric in result.metrics}


def test_evaluator_reads_a_complete_state_and_reports_deterministic_metrics() -> None:
    state = _completed_state()
    before = state.model_dump(mode="json")

    result = evaluate_run(state, case=FIXED_EVALUATION_DATASET.cases[0], dataset_id="fixed", dataset_version="1")

    assert result.outcome == "passed"
    assert _statuses(result) == {
        "run_completion": "passed",
        "trace_completeness": "passed",
        "usage_integrity": "passed",
        "research_coverage": "passed",
        "report_structure": "passed",
        "failure_signals": "passed",
    }
    assert result.trace.node_count == 4
    assert result.trace.llm_count == 1
    assert result.trace.tool_count == 1
    assert result.coverage.evidence_status == "passed"
    assert result.metadata == {"llm_as_judge": False, "reads_existing_state_only": True}
    assert state.model_dump(mode="json") == before
    json.dumps(result.model_dump(mode="json"))


def test_evaluator_marks_missing_data_unavailable_and_detects_failed_trace() -> None:
    result = evaluate_run(
        {
            "research_topic": "legacy result",
            "status": "failed",
            "current_stage": "failed",
            "error": "fake failure",
            "agent_trace": [_event("search", status="failed").model_dump()],
        }
    )

    statuses = _statuses(result)
    assert result.outcome == "failed"
    assert statuses["trace_completeness"] == "passed"
    assert statuses["usage_integrity"] == "unavailable"
    assert statuses["research_coverage"] == "unavailable"
    assert statuses["report_structure"] == "unavailable"
    assert statuses["failure_signals"] == "failed"
    assert result.trace.failed_event_count == 1


def test_completed_lifecycle_does_not_mask_a_failed_trace_metric() -> None:
    state = _completed_state().model_copy(update={"agent_trace": []})

    result = evaluate_run(state)

    assert _statuses(result)["run_completion"] == "passed"
    assert _statuses(result)["trace_completeness"] == "failed"
    assert result.outcome == "failed"


def test_fixed_runner_evaluates_every_case_and_aggregates_without_graph_dependency() -> None:
    received = []

    async def fake_case_runner(case):
        received.append(case.case_id)
        if case.case_id == "semiconductor-supply-chain":
            return {"research_topic": case.query, "status": "failed", "current_stage": "failed", "error": "fake case failure"}
        return _completed_state()

    suite = asyncio.run(run_offline_evaluation(fake_case_runner, configuration={"mode": "fake"}))

    assert received == [case.case_id for case in FIXED_EVALUATION_DATASET.cases]
    assert suite.dataset_id == "researchos_offline_baseline"
    assert suite.summary.total_cases == 3
    assert suite.summary.passed_cases == 2
    assert suite.summary.failed_cases == 1
    assert suite.summary.unavailable_cases == 0
    assert suite.configuration == {"mode": "fake"}
    assert all(result.case_id for result in suite.results)
    json.dumps(suite.model_dump(mode="json"))
