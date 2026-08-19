"""Fake-only coverage for the P3.2b offline evaluation baseline."""

import asyncio
import json

import pytest

from src.agent_trace import AgentTraceEvent
from src.evaluation.contracts import EvaluationDataset, EvaluationCase, ResearchQualityRubric
from src.evaluation.dataset import FIXED_EVALUATION_DATASET
from src.evaluation.evaluator import evaluate_run
from src.evaluation.runner import run_offline_evaluation
from src.evaluation.snapshot import build_evaluation_snapshot
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
    first_url = "https://example.com/source-one"
    second_url = "https://example.com/source-two"
    sections = [
        ReportSection(title="Summary", content="A fake first section", sources=[first_url]),
        ReportSection(title="Evidence", content="A fake second section", sources=[second_url]),
    ]
    report_text = "# Offline evaluation topic\n\n## Summary\n\n" + ("A fake report. " * 12) + "\n\n## Evidence\n\n" + ("Grounded evidence. " * 8)
    return state.model_copy(
        update={
            "status": "completed",
            "current_stage": "complete",
            "documents": [
                Document(document_id="doc-1", title="Fake source one", uri=first_url),
                Document(document_id="doc-2", title="Fake source two", uri=second_url),
            ],
            "evidence": [
                Evidence(
                    evidence_id="evidence-1",
                    document_id="doc-1",
                    source_url=first_url,
                    claim="Fake claim",
                    source_quote="Fake quote",
                    text_source="snippet",
                    relation="supports",
                ),
                Evidence(
                    evidence_id="evidence-2",
                    document_id="doc-2",
                    source_url=second_url,
                    claim="Second fake claim",
                    source_quote="Second fake quote",
                    text_source="snippet",
                    relation="supports",
                ),
            ],
            "evidence_diagnostics": EvidenceDiagnostics(status="completed", source="p2_documents"),
            "findings": [Finding(finding_id="finding-1", statement="Fake finding", evidence_refs=["evidence-1", "evidence-2"], status="supported")],
            "report_sections": sections,
            "final_report": report_text,
            "report": Report(title="Offline evaluation topic", sections=sections, content=report_text, citations=[first_url, second_url], status="completed"),
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
        "source_coverage": "passed",
        "grounded_citation": "passed",
        "report_completeness": "passed",
        "failure_signals": "passed",
    }
    assert result.trace.node_count == 4
    assert result.trace.llm_count == 1
    assert result.trace.tool_count == 1
    assert result.coverage.evidence_status == "passed"
    assert result.metadata == {"llm_as_judge": False, "reads_existing_state_only": True}
    assert result.evaluation_snapshot is not None
    assert result.evaluation_snapshot.snapshot_version == "p5.1.v1"
    assert result.quality.distinct_source_count == 2
    assert result.quality.grounded_citation_count == 2
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
        if case.case_id == "search-provider-failure":
            return {"research_topic": case.query, "status": "failed", "current_stage": "failed", "error": "fake case failure"}
        return _completed_state()

    suite = asyncio.run(run_offline_evaluation(fake_case_runner, configuration={"mode": "fake"}))

    assert received == [case.case_id for case in FIXED_EVALUATION_DATASET.cases]
    assert suite.dataset_id == "researchos_offline_baseline"
    assert suite.summary.total_cases == 6
    assert suite.summary.passed_cases == 5
    assert suite.summary.failed_cases == 1
    assert suite.summary.unavailable_cases == 0
    assert suite.configuration == {"mode": "fake"}
    assert suite.evaluation_snapshot is not None
    assert all(result.evaluation_snapshot == suite.evaluation_snapshot for result in suite.results)
    assert all(result.case_id for result in suite.results)
    assert suite.regression.status == "passed"
    assert suite.regression.expected_outcome_match_rate == 1.0
    assert suite.regression.quality_metric_pass_rates == {
        "source_coverage": 1.0,
        "grounded_citation": 1.0,
        "report_completeness": 1.0,
    }
    json.dumps(suite.model_dump(mode="json"))


def test_quality_rubric_reports_deterministic_source_citation_and_completeness_failures() -> None:
    state = _completed_state().model_copy(
        update={
            "documents": [Document(document_id="doc-1", title="Only source", uri="https://example.com/source-one")],
            "report_sections": [ReportSection(title="Summary", content="Short", sources=["https://example.com/unverified"])],
            "final_report": "# Short\n\n## Summary\n\nShort",
            "report": Report(
                title="Short",
                sections=[ReportSection(title="Summary", content="Short", sources=["https://example.com/unverified"])],
                content="# Short\n\n## Summary\n\nShort",
                citations=["https://example.com/unverified"],
                status="completed",
            ),
        }
    )
    case = EvaluationCase(
        case_id="strict-quality",
        query="strict quality",
        quality_rubric=ResearchQualityRubric(
            min_distinct_sources=2,
            min_grounded_citations=1,
            min_report_sections=2,
            min_report_characters=100,
        ),
    )

    result = evaluate_run(state, case=case)

    assert _statuses(result)["source_coverage"] == "failed"
    assert _statuses(result)["grounded_citation"] == "failed"
    assert _statuses(result)["report_completeness"] == "failed"
    assert result.quality.ungrounded_citation_count == 1


def test_partial_case_accepts_validated_partial_evidence_at_its_fixed_lower_threshold() -> None:
    case = next(case for case in FIXED_EVALUATION_DATASET.cases if case.scenario == "partial")
    source_url = "https://example.com/source-one"
    section = ReportSection(title="Available evidence", content="Partial evidence", sources=[source_url])
    report_text = "# Partial report\n\n## Available evidence\n\n" + ("Validated partial evidence. " * 4)
    state = _completed_state().model_copy(
        update={
            "evidence": [
                Evidence(
                    evidence_id="evidence-partial",
                    document_id="doc-1",
                    source_url=source_url,
                    claim="Partial fake claim",
                    source_quote="Partial fake quote",
                    text_source="snippet",
                    relation="supports",
                    status="partial",
                )
            ],
            "evidence_diagnostics": EvidenceDiagnostics(status="partial", source="p2_documents"),
            "report_sections": [section],
            "final_report": report_text,
            "report": Report(
                title="Partial report",
                sections=[section],
                content=report_text,
                citations=[source_url],
                status="completed",
            ),
        }
    )
    before = state.model_dump(mode="json")

    result = evaluate_run(state, case=case)

    assert _statuses(result)["source_coverage"] == "passed"
    assert _statuses(result)["grounded_citation"] == "passed"
    assert _statuses(result)["report_completeness"] == "passed"
    assert state.model_dump(mode="json") == before


def test_regression_gate_fails_a_quality_threshold_without_touching_case_results() -> None:
    async def fake_case_runner(case):
        if case.scenario == "failure":
            return {
                "research_topic": case.query,
                "status": "failed",
                "current_stage": "failed",
                "error": "fake expected failure",
            }
        if case.case_id == "ai-regulation-overview":
            return _completed_state().model_copy(update={"documents": []})
        return _completed_state()

    suite = asyncio.run(run_offline_evaluation(fake_case_runner, configuration={"mode": "fake"}))

    assert suite.regression.status == "failed"
    assert suite.regression.expected_outcome_match_rate == pytest.approx(5 / 6)
    assert suite.regression.quality_metric_pass_rates["source_coverage"] == pytest.approx(4 / 5)
    assert "min_expected_outcome_match_rate" in suite.regression.failed_thresholds
    assert "min_quality_metric_pass_rate:source_coverage" in suite.regression.failed_thresholds


def test_evaluation_snapshot_is_stable_for_same_config_and_changes_with_config() -> None:
    async def fake_case_runner(_case):
        return _completed_state()

    first = asyncio.run(run_offline_evaluation(fake_case_runner, configuration={"mode": "fake", "threshold": 1}))
    same = asyncio.run(run_offline_evaluation(fake_case_runner, configuration={"threshold": 1, "mode": "fake"}))
    changed = asyncio.run(run_offline_evaluation(fake_case_runner, configuration={"mode": "fake", "threshold": 2}))

    assert first.evaluation_snapshot is not None
    assert first.evaluation_snapshot.fingerprint == same.evaluation_snapshot.fingerprint
    assert first.evaluation_snapshot.fingerprint != changed.evaluation_snapshot.fingerprint


def test_evaluation_snapshot_redacts_sensitive_configuration_values() -> None:
    async def fake_case_runner(_case):
        return _completed_state()

    suite = asyncio.run(
        run_offline_evaluation(
            fake_case_runner,
            configuration={"mode": "fake", "provider": {"api_key": "not-for-results"}},
        )
    )

    assert suite.evaluation_snapshot is not None
    assert suite.evaluation_snapshot.configuration["provider"]["api_key"] == "[REDACTED]"


def test_snapshot_fingerprints_actual_dataset_case_content_not_only_declared_version() -> None:
    async def fake_case_runner(_case):
        return _completed_state()

    changed_case = FIXED_EVALUATION_DATASET.cases[0].model_copy(
        update={"query": "Changed content under the same declared dataset version."}
    )
    changed_dataset = EvaluationDataset(
        dataset_id=FIXED_EVALUATION_DATASET.dataset_id,
        version=FIXED_EVALUATION_DATASET.version,
        cases=[changed_case, *FIXED_EVALUATION_DATASET.cases[1:]],
    )

    original = asyncio.run(run_offline_evaluation(fake_case_runner))
    changed = asyncio.run(run_offline_evaluation(fake_case_runner, dataset=changed_dataset))

    assert original.evaluation_snapshot is not None
    assert changed.evaluation_snapshot is not None
    assert original.evaluation_snapshot.dataset_content_fingerprint != changed.evaluation_snapshot.dataset_content_fingerprint
    assert original.evaluation_snapshot.fingerprint != changed.evaluation_snapshot.fingerprint


def test_evaluator_rejects_snapshot_not_bound_to_actual_dataset_or_configuration() -> None:
    snapshot = build_evaluation_snapshot(
        evaluator_version="p4.5.v1",
        dataset=FIXED_EVALUATION_DATASET,
        expected_completed_nodes=("plan", "search", "synthesize", "write_report"),
        configuration={"mode": "fake"},
    )

    with pytest.raises(ValueError, match="does not match"):
        evaluate_run(
            _completed_state(),
            case=FIXED_EVALUATION_DATASET.cases[0],
            dataset=FIXED_EVALUATION_DATASET,
            configuration={"mode": "changed"},
            evaluation_snapshot=snapshot,
        )

    changed_case = FIXED_EVALUATION_DATASET.cases[0].model_copy(update={"description": "changed"})
    changed_dataset = EvaluationDataset(
        dataset_id=FIXED_EVALUATION_DATASET.dataset_id,
        version=FIXED_EVALUATION_DATASET.version,
        cases=[changed_case, *FIXED_EVALUATION_DATASET.cases[1:]],
    )
    with pytest.raises(ValueError, match="does not match"):
        evaluate_run(
            _completed_state(),
            case=changed_case,
            dataset=changed_dataset,
            configuration={"mode": "fake"},
            evaluation_snapshot=snapshot,
        )
