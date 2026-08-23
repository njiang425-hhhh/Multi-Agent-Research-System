"""Deterministic, fake-only tests for the P11.1 advisory contract."""

from __future__ import annotations

import copy

from src.evaluation.contracts import EvaluationMetric
from src.evaluation.planning_quality import (
    PlanningQualityCaseResult,
    PlanningQualityResult,
)
from src.evaluation.quality_action import (
    QUALITY_ACTION_POLICY_VERSION,
    QualitySignal,
    SignalProvenance,
    ThresholdSpec,
    build_quality_action_advisory,
    planning_quality_signals,
    recommend_action,
    runtime_observation_signals,
)
from src.evaluation.snapshot import build_evaluation_snapshot
from src.evidence.contracts import Evidence
from src.runtime_lifecycle import create_new_run_state
from src.state import EvidenceDiagnostics, UsageMetrics


def _provenance() -> SignalProvenance:
    return SignalProvenance(
        evaluator_version="fake.evaluator.v1",
        snapshot_fingerprint="fake-snapshot",
        source_fields=["fake.metric"],
    )


def _signal(
    metric: str,
    *,
    status: str = "passed",
    value=1.0,
    threshold: ThresholdSpec | None = None,
    signal_id: str | None = None,
) -> QualitySignal:
    return QualitySignal(
        signal_id=signal_id or f"fake:{metric}",
        metric=metric,
        status=status,
        kind="derived",
        scope="facet" if "coverage" in metric else "run",
        deterministic=True,
        value=value,
        threshold=threshold
        or ThresholdSpec(operator="gte", expected=1.0, unit="ratio"),
        reason=f"fake {metric} is {status}",
        provenance=_provenance(),
    )


def test_all_pass_is_a_candidate_continue_and_never_eligible() -> None:
    result = build_quality_action_advisory(
        additional_signals=[
            _signal("objectives_coverage"),
            _signal("extracted_facet_coverage"),
            _signal("report_completeness"),
        ]
    )

    assert result.recommendation.action == "continue"
    assert result.recommendation.status == "candidate"
    assert result.recommendation.advisory is True
    assert result.recommendation.provenance.snapshot_fingerprint == result.evaluation_snapshot.fingerprint


def test_extraction_failure_is_blocked_retry_research() -> None:
    recommendation = recommend_action([_signal("extracted_facet_coverage", status="failed", value=0.5)])

    assert recommendation.action == "retry_research"
    assert recommendation.status == "blocked"
    assert recommendation.unmet_preconditions


def test_planning_failure_is_blocked_replan() -> None:
    recommendation = recommend_action([_signal("objectives_coverage", status="failed", value=0.5)])

    assert recommendation.action == "replan"
    assert recommendation.status == "blocked"
    assert "bounded Planner budget" in recommendation.unmet_preconditions


def test_runtime_budget_stop_has_priority_over_derived_quality_failure() -> None:
    state = create_new_run_state("fake runtime stop")
    state = state.model_copy(
        update={
            "status": "failed",
            "current_stage": "failed",
            "terminal_reason": "budget_exhausted",
            "error": "runtime budget exhausted",
            "agent_trace": [],
            "usage": UsageMetrics(),
        }
    )
    result = build_quality_action_advisory(
        state=state,
        additional_signals=[_signal("extracted_facet_coverage", status="failed", value=0.0)],
    )

    assert result.recommendation.action == "stop_fail"
    assert result.recommendation.status == "candidate"
    assert result.recommendation.metric == "runtime_hard_stop"


def test_unavailable_signal_is_not_pass_and_requests_blocked_human_review() -> None:
    recommendation = recommend_action([_signal("grounded_citation", status="unavailable", value=None)])

    assert recommendation.action == "human_review"
    assert recommendation.status == "blocked"


def test_partial_output_is_blocked_accept_partial() -> None:
    partial = _signal(
        "partial_output",
        value=True,
        threshold=ThresholdSpec(operator="eq", expected=True, unit="partial_output"),
    )

    recommendation = recommend_action([partial])

    assert recommendation.action == "accept_partial"
    assert recommendation.status == "blocked"


def test_conflicting_same_metric_signals_request_human_review() -> None:
    recommendation = recommend_action(
        [
            _signal("extracted_facet_coverage", signal_id="coverage:one"),
            _signal("extracted_facet_coverage", signal_id="coverage:two", status="failed", value=0.0),
        ]
    )

    assert recommendation.action == "human_review"
    assert recommendation.status == "blocked"
    assert "extracted_facet_coverage" in recommendation.reason


def test_threshold_boundary_is_inclusive_and_below_boundary_is_actionable() -> None:
    threshold = ThresholdSpec(operator="gte", expected=1.0, unit="ratio")
    passing = recommend_action([_signal("extracted_facet_coverage", value=1.0, threshold=threshold)])
    failing = recommend_action([_signal("extracted_facet_coverage", value=0.999, threshold=threshold)])

    assert passing.action == "continue"
    assert failing.action == "retry_research"


def test_runtime_adapter_reads_only_state_and_marks_missing_observations() -> None:
    state = {"status": "pending", "current_stage": "planning", "run_id": "fake-run"}
    before = copy.deepcopy(state)

    signals = runtime_observation_signals(state)

    assert {signal.metric for signal in signals} >= {
        "runtime_lifecycle",
        "runtime_hard_stop",
        "runtime_error",
        "runtime_trace",
        "runtime_usage",
    }
    assert next(signal for signal in signals if signal.metric == "runtime_trace").status == "unavailable"
    assert state == before


def test_p9_adapter_preserves_metric_status_and_provenance() -> None:
    snapshot = build_evaluation_snapshot(
        evaluator_version="p9.planning_quality.v1",
        expected_completed_nodes=("plan",),
        dataset_id="fake-planning",
        dataset_version="1",
        metric_names=("objectives_coverage",),
    )
    result = PlanningQualityResult(
        evaluation_snapshot=snapshot,
        dataset_id="fake-planning",
        dataset_version="1",
        dataset_content_fingerprint="dataset",
        results=[
            PlanningQualityCaseResult(
                case_id="case-1",
                metrics=[
                    EvaluationMetric(
                        name="objectives_coverage",
                        status="passed",
                        value=1.0,
                    )
                ],
            )
        ],
        summary={"total_cases": 1, "fully_passing_cases": 1},
    )

    signals = planning_quality_signals(result, case_id="case-1")

    assert signals[0].status == "passed"
    assert signals[0].kind == "derived"
    assert signals[0].scope == "node"
    assert signals[0].provenance.dataset_id == "fake-planning"


def test_snapshot_contains_policy_thresholds_and_changes_when_policy_changes() -> None:
    signal = _signal("extracted_facet_coverage")
    first = build_quality_action_advisory(
        additional_signals=[signal],
        threshold_policy={
            "extracted_facet_coverage": ThresholdSpec(operator="gte", expected=1.0, unit="ratio")
        },
    )
    changed = build_quality_action_advisory(
        additional_signals=[signal],
        threshold_policy={
            "extracted_facet_coverage": ThresholdSpec(operator="gte", expected=0.8, unit="ratio")
        },
    )

    assert first.policy_version == QUALITY_ACTION_POLICY_VERSION
    assert first.evaluation_snapshot.configuration["quality_action_policy_version"] == QUALITY_ACTION_POLICY_VERSION
    assert first.evaluation_snapshot.configuration["thresholds"]["extracted_facet_coverage"]["expected"] == 1.0
    assert first.evaluation_snapshot.fingerprint != changed.evaluation_snapshot.fingerprint


def test_partial_evidence_state_produces_blocked_accept_partial_without_mutation() -> None:
    state = create_new_run_state("fake partial")
    state = state.model_copy(
        update={
            "evidence_diagnostics": EvidenceDiagnostics(status="partial", source="p2_documents"),
            "evidence": [
                Evidence(
                    evidence_id="evidence-1",
                    document_id="doc-1",
                    source_url="https://example.com/source",
                    claim="fake claim",
                    source_quote="fake quote",
                    text_source="snippet",
                    relation="supports",
                )
            ],
        }
    )
    before = copy.deepcopy(state.model_dump(mode="json"))

    result = build_quality_action_advisory(state=state)

    assert result.recommendation.action == "accept_partial"
    assert result.recommendation.status == "blocked"
    assert state.model_dump(mode="json") == before


def test_contract_composition_is_repeatable_and_does_not_call_external_services() -> None:
    signal = _signal("extracted_facet_coverage")

    first = build_quality_action_advisory(additional_signals=[signal], configuration={"mode": "fake"})
    second = build_quality_action_advisory(additional_signals=[signal], configuration={"mode": "fake"})

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.recommendation.advisory is True
