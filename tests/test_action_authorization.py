"""Deterministic, fake-only tests for the P12.1 authorization contract."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

from src.evaluation.action_authorization import (
    ActionAuthorization,
    ActionBudget,
    ActionDeadlinePolicy,
    ActionPartialPolicy,
    ActionProvenance,
    ActionTarget,
    action_authorization_content_fingerprint,
    evaluate_action_eligibility,
)
from src.evaluation.quality_action import ActionRecommendation, SignalProvenance
from src.runtime_control import RunPolicy, create_execution_context
from src.runtime_lifecycle import create_new_run_state


_NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def _recommendation(action: str = "retry_research") -> ActionRecommendation:
    return ActionRecommendation(
        action=action,
        status="blocked",
        metric="extracted_facet_coverage",
        reason="fake advisory",
        triggering_signal_ids=["fake:coverage"],
        provenance=SignalProvenance(
            evaluator_version="fake.p11.v1",
            snapshot_fingerprint="fake-snapshot",
            source_fields=["fake.coverage"],
        ),
    )


def _context(*, operation_calls: int = 0, timeout_seconds: float = 60.0):
    return create_execution_context(
        run_id="fake-run",
        thread_id="fake-thread",
        policy=RunPolicy(total_timeout_seconds=timeout_seconds, max_operation_calls=5),
        now=_NOW,
    ).model_copy(update={"operation_calls": operation_calls})


def _authorization(
    action: str = "retry_research",
    *,
    target: ActionTarget | None = None,
    budget: ActionBudget | None = None,
    readiness_status: str = "ready",
    preconditions: dict[str, bool] | None = None,
    partial_policy: ActionPartialPolicy | None = None,
) -> ActionAuthorization:
    defaults = {
        "retry_research": ActionTarget(
            stage="researching",
            node="search",
            scope="facet",
            selectors={"facet": "coverage-gap"},
            targetable_deficit=True,
            deficit_metric="extracted_facet_coverage",
            triggering_signal_ids=["fake:coverage"],
        ),
        "replan": ActionTarget(
            stage="planning",
            node="plan",
            scope="facet",
            selectors={"objective": "coverage-gap"},
            targetable_deficit=True,
            deficit_metric="objectives_coverage",
            triggering_signal_ids=["fake:planning"],
        ),
        "accept_partial": ActionTarget(
            stage="writing",
            node="write_report",
            scope="report",
            selectors={"report": "final"},
            targetable_deficit=True,
            deficit_metric="partial_output",
            triggering_signal_ids=["fake:partial"],
        ),
        "human_review": ActionTarget(
            stage="failed",
            node="human_review",
            scope="approval",
            selectors={"review": "quality-conflict"},
            deficit_metric="grounded_citation",
            triggering_signal_ids=["fake:review"],
        ),
    }
    default_preconditions = {
        "retry_research": {"business_retry_not_operation_retry": True},
        "replan": {
            "downstream_invalidation_contract": True,
            "plan_replacement_contract": True,
        },
        "accept_partial": {},
        "human_review": {"approval_workflow_contract": True},
    }
    recommendation = _recommendation(action)
    provenance = ActionProvenance(
        authorization_id="auth-fake-001",
        action_id="action-fake-001",
        grantor="fake-test",
        recommendation_fingerprint=action_authorization_content_fingerprint(recommendation),
        source_signal_ids=recommendation.triggering_signal_ids,
    )
    return ActionAuthorization(
        allowed_action=action,
        target=target or defaults[action],
        action_budget=budget or ActionBudget(max_operation_calls=2, max_search_calls=1),
        deadline_policy=ActionDeadlinePolicy(local_action_timeout_seconds=30.0),
        provenance=provenance,
        readiness_status=readiness_status,
        preconditions=preconditions if preconditions is not None else default_preconditions[action],
        partial_policy=partial_policy or ActionPartialPolicy(
            allow_partial=action == "accept_partial",
            product_partial_contract=action == "accept_partial",
            required_output_fields=["final_report"] if action == "accept_partial" else [],
        ),
    )


def test_recommendation_without_authorization_is_blocked() -> None:
    decision = evaluate_action_eligibility(_recommendation(), now=_NOW)

    assert decision.status == "blocked"
    assert decision.unmet_preconditions == ["missing authorization"]


def test_authorization_action_mismatch_is_blocked() -> None:
    decision = evaluate_action_eligibility(
        _recommendation("retry_research"),
        _authorization("replan"),
        execution_context=_context(),
        now=_NOW,
    )

    assert decision.status == "blocked"
    assert "authorization action mismatch" in decision.unmet_preconditions


def test_authorization_provenance_must_bind_to_the_advisory() -> None:
    authorization = _authorization().model_copy(
        update={
            "provenance": _authorization().provenance.model_copy(
                update={"recommendation_fingerprint": "stale-advisory"}
            )
        }
    )

    decision = evaluate_action_eligibility(
        _recommendation(), authorization, execution_context=_context(), now=_NOW
    )

    assert decision.status == "blocked"
    assert "authorization recommendation provenance mismatch" in decision.unmet_preconditions


def test_incomplete_target_is_blocked() -> None:
    authorization = _authorization(
        target=ActionTarget(
            stage=None,
            node="search",
            targetable_deficit=False,
            deficit_metric="extracted_facet_coverage",
        )
    )

    decision = evaluate_action_eligibility(
        _recommendation(), authorization, execution_context=_context(), now=_NOW
    )

    assert decision.status == "blocked"
    assert "target stage and node are required" in decision.unmet_preconditions
    assert "targetable deficit is required" in decision.unmet_preconditions


def test_runtime_deadline_exhaustion_is_blocked() -> None:
    decision = evaluate_action_eligibility(
        _recommendation(),
        _authorization(),
        execution_context=_context(timeout_seconds=5.0),
        now=_NOW + timedelta(seconds=6),
    )

    assert decision.status == "blocked"
    assert "runtime deadline exhausted" in decision.unmet_preconditions


def test_runtime_operation_budget_exhaustion_is_blocked() -> None:
    decision = evaluate_action_eligibility(
        _recommendation(),
        _authorization(),
        execution_context=_context(operation_calls=5),
        now=_NOW,
    )

    assert decision.status == "blocked"
    assert "runtime operation budget exhausted" in decision.unmet_preconditions


def test_action_budget_is_narrowed_by_runtime_remaining_budget() -> None:
    authorization = _authorization(budget=ActionBudget(max_operation_calls=10, max_search_calls=4))
    decision = evaluate_action_eligibility(
        _recommendation(),
        authorization,
        execution_context=_context(operation_calls=3),
        now=_NOW,
    )

    assert decision.status == "eligible"
    assert decision.effective_budget.max_operation_calls == 2
    assert "narrowed" in decision.warnings[0]
    assert authorization.action_budget.max_operation_calls == 10


def test_insufficient_calibration_is_blocked() -> None:
    decision = evaluate_action_eligibility(
        _recommendation(),
        _authorization(readiness_status="insufficient"),
        execution_context=_context(),
        now=_NOW,
    )

    assert decision.status == "blocked"
    assert "real-workload calibration/readiness is insufficient" in decision.unmet_preconditions


def test_business_retry_is_not_operation_level_retry() -> None:
    decision = evaluate_action_eligibility(
        _recommendation(),
        _authorization(preconditions={"business_retry_not_operation_retry": False}),
        execution_context=_context(),
        now=_NOW,
    )

    assert decision.status == "blocked"
    assert "business retry must be explicitly distinct from operation-level retry" in decision.unmet_preconditions
    assert "identical provider/tool operation" in decision.operation_retry_boundary


def test_replan_requires_downstream_invalidation_contract() -> None:
    decision = evaluate_action_eligibility(
        _recommendation("replan"),
        _authorization(
            "replan",
            preconditions={"plan_replacement_contract": True},
        ),
        execution_context=_context(),
        now=_NOW,
    )

    assert decision.status == "blocked"
    assert "replan requires an explicit downstream invalidation contract" in decision.unmet_preconditions
    assert any(rule.mode == "invalidate" for rule in decision.state_transition_rules)


def test_accept_partial_requires_product_partial_contract() -> None:
    decision = evaluate_action_eligibility(
        _recommendation("accept_partial"),
        _authorization(
            "accept_partial",
            partial_policy=ActionPartialPolicy(allow_partial=True, product_partial_contract=False),
        ),
        execution_context=_context(),
        now=_NOW,
    )

    assert decision.status == "blocked"
    assert "accept_partial requires a product partial-output contract" in decision.unmet_preconditions


def test_human_review_requires_approval_workflow_contract() -> None:
    decision = evaluate_action_eligibility(
        _recommendation("human_review"),
        _authorization("human_review", preconditions={}),
        execution_context=_context(),
        now=_NOW,
    )

    assert decision.status == "blocked"
    assert "human_review requires an approval workflow contract" in decision.unmet_preconditions


def test_decision_is_deterministic_and_inputs_are_not_mutated() -> None:
    recommendation = _recommendation()
    authorization = _authorization()
    state = create_new_run_state("fake authorization state")
    before_recommendation = copy.deepcopy(recommendation.model_dump(mode="json"))
    before_authorization = copy.deepcopy(authorization.model_dump(mode="json"))
    before_state = copy.deepcopy(state.model_dump(mode="json"))

    first = evaluate_action_eligibility(recommendation, authorization, state=state, now=_NOW)
    second = evaluate_action_eligibility(recommendation, authorization, state=state, now=_NOW)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert recommendation.model_dump(mode="json") == before_recommendation
    assert authorization.model_dump(mode="json") == before_authorization
    assert state.model_dump(mode="json") == before_state


def test_contract_evaluation_requires_no_agent_graph_or_provider_calls() -> None:
    decision = evaluate_action_eligibility(
        _recommendation(),
        _authorization(),
        execution_context=_context(),
        now=_NOW,
    )

    assert decision.status == "eligible"
    assert decision.provenance is not None
