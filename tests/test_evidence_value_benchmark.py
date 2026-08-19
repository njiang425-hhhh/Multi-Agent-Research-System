"""Fake-only deterministic coverage for the P5.2 Evidence Value Benchmark."""

import asyncio

import pytest

from src.agent_trace import AgentTraceEvent
from src.evaluation.dataset import FIXED_EVALUATION_DATASET
from src.evaluation.evidence_benchmark import run_evidence_value_benchmark
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


def _events(run_id: str) -> list[AgentTraceEvent]:
    return [
        AgentTraceEvent(
            trace_id=run_id,
            node=node,
            agent=f"Fake{node}",
            operation=node,
            event_type="node",
            attempt=1,
            status="completed",
            started_at="2026-08-19T00:00:00+00:00",
            ended_at="2026-08-19T00:00:01+00:00",
            duration_seconds=1.0,
        )
        for node in ("plan", "search", "synthesize", "write_report")
    ]


def _state(case, mode):
    if case.scenario == "failure":
        return {
            "research_topic": case.query,
            "status": "failed",
            "current_stage": "failed",
            "error": f"fake {mode} provider failure",
        }

    state = create_new_run_state(case.query)
    first_url = "https://example.com/source-one"
    second_url = "https://example.com/source-two"
    is_partial_case = mode == "partial" and case.scenario == "partial"
    documents = [Document(document_id="doc-1", title="Source one", uri=first_url)]
    sections = [ReportSection(title="Summary", content="Evidence report", sources=[first_url])]
    if not is_partial_case:
        documents.append(Document(document_id="doc-2", title="Source two", uri=second_url))
        sections.append(ReportSection(title="Details", content="More evidence", sources=[second_url]))
    report_text = "# Evidence benchmark\n\n" + "\n\n".join(
        f"## {section.title}\n\n" + ("Validated evidence. " * 8) for section in sections
    )
    evidence = []
    diagnostics = EvidenceDiagnostics(status="disabled", source="p2_documents")
    if mode != "disabled":
        evidence = [
            Evidence(
                evidence_id="evidence-1",
                document_id="doc-1",
                source_url=first_url,
                claim="First fake claim",
                source_quote="First fake quote",
                text_source="snippet",
                relation="supports",
                status="partial" if mode == "partial" else "grounded",
            )
        ]
        if not is_partial_case:
            evidence.append(
                Evidence(
                    evidence_id="evidence-2",
                    document_id="doc-2",
                    source_url=second_url,
                    claim="Second fake claim",
                    source_quote="Second fake quote",
                    text_source="snippet",
                    relation="supports",
                    status="partial" if mode == "partial" else "grounded",
                )
            )
        diagnostics = EvidenceDiagnostics(
            status="partial" if mode == "partial" else "completed",
            source="p2_documents",
            analyzer_completed=True,
            analyzer_partial=mode == "partial",
        )
    usage = UsageMetrics(
        llm_calls=3 if mode == "disabled" else 5,
        tool_calls=2,
        input_tokens=11 if mode == "disabled" else 16,
        output_tokens=7 if mode == "disabled" else 10,
        total_tokens=18 if mode == "disabled" else 26,
        latency_seconds=1.0 if mode == "disabled" else 1.5,
    )
    return state.model_copy(
        update={
            "status": "completed",
            "current_stage": "complete",
            "documents": documents,
            "evidence": evidence,
            "evidence_diagnostics": diagnostics,
            "findings": [Finding(finding_id="finding-1", statement="Fake finding", evidence_refs=["evidence-1"], status="supported")],
            "report_sections": sections,
            "final_report": report_text,
            "report": Report(
                title="Evidence benchmark",
                sections=sections,
                content=report_text,
                citations=[source for section in sections for source in section.sources],
                status="completed",
            ),
            "usage": usage,
            "agent_trace": _events(state.run_id),
        }
    )


def test_benchmark_compares_modes_with_same_cases_rubric_and_read_only_inputs() -> None:
    states = {
        (mode, case.case_id): _state(case, mode)
        for mode in ("disabled", "enabled", "partial")
        for case in FIXED_EVALUATION_DATASET.cases
    }
    before = {
        key: value.model_dump(mode="json") if hasattr(value, "model_dump") else dict(value)
        for key, value in states.items()
    }

    async def fake_runner(case, mode):
        return states[(mode, case.case_id)]

    result = asyncio.run(
        run_evidence_value_benchmark(fake_runner, configuration={"mode": "fake"})
    )
    by_mode = {item.mode: item for item in result.modes}
    by_comparison = {item.mode: item for item in result.comparisons}

    assert result.dataset_id == FIXED_EVALUATION_DATASET.dataset_id
    assert result.modes and [item.mode for item in result.modes] == ["disabled", "enabled", "partial"]
    assert result.benchmark_snapshot.configuration["evidence_modes"] == ["disabled", "enabled", "partial"]
    assert by_mode["disabled"].quality_metric_pass_rates == {
        "source_coverage": 1.0,
        "grounded_citation": 0.0,
        "report_completeness": 1.0,
    }
    assert by_mode["disabled"].adoption.adoption_rate == 0.0
    assert by_mode["enabled"].adoption.adoption_rate == 1.0
    assert by_mode["enabled"].adoption.evidence_backed_report_cases == 5
    assert by_mode["partial"].adoption.diagnostics_status_counts["partial"] == 5
    assert by_mode["partial"].quality_metric_pass_rates["grounded_citation"] == 1.0
    assert by_comparison["enabled"].quality_metric_pass_rate_deltas["grounded_citation"] == 1.0
    assert by_comparison["enabled"].adoption_rate_delta == 1.0
    assert by_comparison["enabled"].total_latency_seconds_delta == 2.5
    assert by_comparison["enabled"].total_tokens_delta == 40
    assert by_comparison["partial"].benefited_case_ids == [
        case.case_id for case in FIXED_EVALUATION_DATASET.cases if case.scenario != "failure"
    ]
    assert "climate" in by_comparison["partial"].benefited_task_tags
    assert any(item.scenario == "partial" and item.value_observed for item in by_comparison["partial"].cases)
    assert {
        key: value.model_dump(mode="json") if hasattr(value, "model_dump") else dict(value)
        for key, value in states.items()
    } == before


def test_benchmark_snapshot_is_stable_and_binds_mode_order_and_configuration() -> None:
    async def fake_runner(case, mode):
        return _state(case, mode)

    first = asyncio.run(
        run_evidence_value_benchmark(fake_runner, configuration={"mode": "fake", "seed": 1})
    )
    same = asyncio.run(
        run_evidence_value_benchmark(fake_runner, configuration={"seed": 1, "mode": "fake"})
    )
    changed = asyncio.run(
        run_evidence_value_benchmark(
            fake_runner,
            modes=("disabled", "partial", "enabled"),
            configuration={"mode": "fake", "seed": 1},
        )
    )

    assert first.benchmark_snapshot.fingerprint == same.benchmark_snapshot.fingerprint
    assert first.benchmark_snapshot.fingerprint != changed.benchmark_snapshot.fingerprint


def test_benchmark_requires_unique_modes_and_disabled_baseline() -> None:
    async def fake_runner(case, mode):
        return _state(case, mode)

    with pytest.raises(ValueError, match="disabled baseline"):
        asyncio.run(run_evidence_value_benchmark(fake_runner, modes=("enabled",)))
    with pytest.raises(ValueError, match="unique"):
        asyncio.run(
            run_evidence_value_benchmark(fake_runner, modes=("disabled", "disabled"))
        )
    with pytest.raises(ValueError, match="unknown"):
        asyncio.run(run_evidence_value_benchmark(fake_runner, modes=("disabled", "unknown")))
