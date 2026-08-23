"""Read-only P12.1 action authorization and eligibility contracts.

The P11 recommendation remains an advisory observation.  This module can
describe whether a separately authorized future action would be eligible, but
it never schedules, invokes, retries, or merges an action.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.evaluation.quality_action import ActionKind, ActionRecommendation
from src.runtime_control import ExecutionContext


ACTION_AUTHORIZATION_VERSION = "p12.1.action_authorization.v1"
ACTION_ELIGIBILITY_POLICY_VERSION = "p12.1.eligibility_policy.v1"

EligibilityStatus = Literal["eligible", "blocked", "unavailable"]
ReadinessStatus = Literal["ready", "insufficient", "unavailable"]
StateMergeMode = Literal[
    "preserve",
    "append_dedupe",
    "replace",
    "invalidate",
    "approval_only",
    "no_content_merge",
]

_SUPPORTED_ACTIONS = {"retry_research", "replan", "accept_partial", "human_review"}


class ActionTarget(BaseModel):
    """A bounded stage/node and deficit scope for one possible action."""

    stage: str | None = None
    node: str | None = None
    scope: Literal["run", "facet", "query", "document", "report", "approval"] = "run"
    selectors: dict[str, Any] = Field(default_factory=dict)
    targetable_deficit: bool = False
    deficit_metric: str | None = None
    triggering_signal_ids: list[str] = Field(default_factory=list)


class ActionBudget(BaseModel):
    """Action-local limits; every populated field only narrows execution."""

    max_operation_calls: int | None = Field(default=None, ge=0)
    max_llm_calls: int | None = Field(default=None, ge=0)
    max_tool_calls: int | None = Field(default=None, ge=0)
    max_search_calls: int | None = Field(default=None, ge=0)
    max_extract_calls: int | None = Field(default=None, ge=0)
    max_wall_time_seconds: float | None = Field(default=None, gt=0)

    def has_limit(self) -> bool:
        return any(value is not None for value in self.model_dump().values())


class ActionDeadlinePolicy(BaseModel):
    """Deadline inputs whose minimum becomes the action's effective limit."""

    inherit_run_deadline: bool = True
    authorization_deadline_at: str | None = None
    local_action_timeout_seconds: float | None = Field(default=None, gt=0)
    exhaustion_outcome: Literal["blocked"] = "blocked"


class NoProgressPolicy(BaseModel):
    """Future executor termination rule, represented but not enforced here."""

    metric: str | None = None
    minimum_delta: float | None = Field(default=None, ge=0)
    max_consecutive_no_progress: int = Field(default=1, ge=0)
    terminate_on_no_progress: bool = True


class ActionFailurePolicy(BaseModel):
    """Future action failure semantics; State remains untouched in P12.1."""

    outcome: Literal["preserve_original", "human_review", "stop_fail"] = "preserve_original"
    preserve_original_state: bool = True


class ActionPartialPolicy(BaseModel):
    """Product-level partial delivery contract for a future action."""

    allow_partial: bool = False
    product_partial_contract: bool = False
    required_output_fields: list[str] = Field(default_factory=list)


class ActionProvenance(BaseModel):
    """Durable identity and attribution fields for a future execution ledger."""

    authorization_id: str = Field(min_length=1)
    action_id: str = Field(min_length=1)
    grantor: str = Field(min_length=1)
    recommendation_fingerprint: str = Field(min_length=1)
    authorization_version: str = ACTION_AUTHORIZATION_VERSION
    policy_version: str = ACTION_ELIGIBILITY_POLICY_VERSION
    source_signal_ids: list[str] = Field(default_factory=list)
    state_fingerprint: str | None = None
    created_at: str | None = None


class StateTransitionRule(BaseModel):
    """A declared future state effect, never applied by this module."""

    field: str
    mode: StateMergeMode
    requires_attribution: bool = True
    requires_downstream_invalidation: bool = False
    description: str


class ActionReadinessCheck(BaseModel):
    """Per-action precondition result with declared future transition rules."""

    action: ActionKind
    ready: bool
    unmet_preconditions: list[str] = Field(default_factory=list)
    state_transition_rules: list[StateTransitionRule] = Field(default_factory=list)
    semantic_boundary: str | None = None


class ActionAuthorization(BaseModel):
    """Explicit, single-action authority; it is not an execution request."""

    explicit: Literal[True] = True
    allowed_action: ActionKind
    target: ActionTarget
    max_executions: int = Field(default=1, ge=0)
    action_budget: ActionBudget = Field(default_factory=ActionBudget)
    deadline_policy: ActionDeadlinePolicy = Field(default_factory=ActionDeadlinePolicy)
    no_progress_policy: NoProgressPolicy = Field(default_factory=NoProgressPolicy)
    failure_policy: ActionFailurePolicy = Field(default_factory=ActionFailurePolicy)
    partial_policy: ActionPartialPolicy = Field(default_factory=ActionPartialPolicy)
    provenance: ActionProvenance
    readiness_status: ReadinessStatus = "insufficient"
    preconditions: dict[str, bool] = Field(default_factory=dict)
    evaluation_as_production_gate: Literal[False] = False


class ActionEligibilityDecision(BaseModel):
    """Pure, deterministic decision about a future action's readiness."""

    status: EligibilityStatus
    action: ActionKind | None = None
    reason: str
    unmet_preconditions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    effective_deadline_seconds: float | None = None
    effective_budget: ActionBudget = Field(default_factory=ActionBudget)
    readiness_check: ActionReadinessCheck | None = None
    state_transition_rules: list[StateTransitionRule] = Field(default_factory=list)
    provenance: ActionProvenance | None = None
    operation_retry_boundary: str = (
        "Operation retry repeats one identical provider/tool operation under runtime ownership; "
        "retry_research is a separately authorized business action with its own target and budget."
    )
    authorization_version: str = ACTION_AUTHORIZATION_VERSION
    policy_version: str = ACTION_ELIGIBILITY_POLICY_VERSION


def action_authorization_content_fingerprint(value: Any) -> str:
    """Return a stable content fingerprint without changing the supplied input."""

    encoded = json.dumps(_json_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _json_value(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _read(value: Any, field: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(field, default)
    return getattr(value, field, default)


def _resolve_context(state: Any, context: ExecutionContext | None) -> ExecutionContext | None:
    if context is not None:
        return context
    raw_context = _read(state, "execution_context")
    if isinstance(raw_context, ExecutionContext):
        return raw_context
    if isinstance(raw_context, Mapping):
        return ExecutionContext.model_validate(raw_context)
    return None


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _parse_deadline(value: str) -> datetime:
    return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _effective_deadline(
    policy: ActionDeadlinePolicy,
    context: ExecutionContext | None,
    now: datetime,
) -> tuple[float | None, list[str]]:
    limits: list[float] = []
    unmet: list[str] = []
    if policy.inherit_run_deadline and context is not None:
        remaining = context.remaining_timeout_seconds(now=now)
        if remaining is not None:
            limits.append(remaining)
            if remaining <= 0:
                unmet.append("runtime deadline exhausted")
    if policy.authorization_deadline_at:
        remaining = (_parse_deadline(policy.authorization_deadline_at) - now).total_seconds()
        limits.append(remaining)
        if remaining <= 0:
            unmet.append("authorization deadline exhausted")
    if policy.local_action_timeout_seconds is None:
        unmet.append("local action deadline is required")
    else:
        limits.append(policy.local_action_timeout_seconds)
    if not limits:
        return None, unmet
    return max(0.0, min(limits)), unmet


def _effective_budget(
    budget: ActionBudget,
    context: ExecutionContext | None,
) -> tuple[ActionBudget, list[str], list[str]]:
    effective = budget.model_copy(deep=True)
    unmet: list[str] = []
    warnings: list[str] = []
    if not budget.has_limit():
        unmet.append("action-specific budget is required")
    if context is None:
        return effective, unmet, warnings

    remaining = context.remaining_operation_calls()
    if remaining is None:
        return effective, unmet, warnings
    if remaining <= 0:
        unmet.append("runtime operation budget exhausted")
        return effective, unmet, warnings
    if effective.max_operation_calls is None:
        effective = effective.model_copy(update={"max_operation_calls": remaining})
        warnings.append("runtime remaining operation budget bounds the action budget")
    elif effective.max_operation_calls > remaining:
        effective = effective.model_copy(update={"max_operation_calls": remaining})
        warnings.append("action operation budget was narrowed to runtime remaining budget")
    return effective, unmet, warnings


def _target_preconditions(action: ActionKind, target: ActionTarget) -> list[str]:
    unmet: list[str] = []
    if not target.stage or not target.node:
        unmet.append("target stage and node are required")
    if action in {"retry_research", "replan", "accept_partial"} and not target.targetable_deficit:
        unmet.append("targetable deficit is required")
    if action in {"retry_research", "replan"} and not target.deficit_metric:
        unmet.append("target deficit metric is required")
    if action == "retry_research" and not target.selectors:
        unmet.append("retry_research target selectors are required")
    return unmet


def _retry_research_rules() -> list[StateTransitionRule]:
    return [
        StateTransitionRule(
            field="search_results",
            mode="append_dedupe",
            description="Append new target-scoped results with deterministic deduplication.",
        ),
        StateTransitionRule(
            field="documents",
            mode="append_dedupe",
            description="Append or replace only documents attributable to the authorized target.",
        ),
        StateTransitionRule(
            field="key_findings/final_report",
            mode="invalidate",
            requires_downstream_invalidation=True,
            description="Invalidate downstream derived output when target-scoped research changes.",
        ),
    ]


def _replan_rules() -> list[StateTransitionRule]:
    return [
        StateTransitionRule(
            field="research_plan",
            mode="replace",
            description="Replace the plan only with the authorized plan revision and attribution.",
        ),
        StateTransitionRule(
            field="search_results/documents/key_findings/final_report",
            mode="invalidate",
            requires_downstream_invalidation=True,
            description="Invalidate all downstream plan-dependent outputs before any rerun.",
        ),
    ]


def _accept_partial_rules() -> list[StateTransitionRule]:
    return [
        StateTransitionRule(
            field="final_report",
            mode="no_content_merge",
            description="Preserve the produced partial artifact and attach delivery semantics only.",
        )
    ]


def _human_review_rules() -> list[StateTransitionRule]:
    return [
        StateTransitionRule(
            field="approval_record",
            mode="approval_only",
            description="Record an external approval decision without directly changing research state.",
        )
    ]


def validate_retry_research_readiness(authorization: ActionAuthorization) -> ActionReadinessCheck:
    unmet: list[str] = []
    if authorization.target.node != "search":
        unmet.append("retry_research target node must be search")
    if authorization.target.deficit_metric not in {"result_facet_coverage", "extracted_facet_coverage"}:
        unmet.append("retry_research requires a research coverage deficit")
    if not authorization.preconditions.get("business_retry_not_operation_retry", False):
        unmet.append("business retry must be explicitly distinct from operation-level retry")
    if not any(
        value is not None
        for value in (
            authorization.action_budget.max_operation_calls,
            authorization.action_budget.max_search_calls,
            authorization.action_budget.max_extract_calls,
        )
    ):
        unmet.append("retry_research needs a bounded search, extract, or operation budget")
    return ActionReadinessCheck(
        action="retry_research",
        ready=not unmet,
        unmet_preconditions=unmet,
        state_transition_rules=_retry_research_rules(),
        semantic_boundary=(
            "This is a business-level research action, not an operation-level retry of one provider call."
        ),
    )


def validate_replan_readiness(authorization: ActionAuthorization) -> ActionReadinessCheck:
    unmet: list[str] = []
    if authorization.target.node != "plan":
        unmet.append("replan target node must be plan")
    if not authorization.preconditions.get("downstream_invalidation_contract", False):
        unmet.append("replan requires an explicit downstream invalidation contract")
    if not authorization.preconditions.get("plan_replacement_contract", False):
        unmet.append("replan requires an explicit plan replacement contract")
    return ActionReadinessCheck(
        action="replan",
        ready=not unmet,
        unmet_preconditions=unmet,
        state_transition_rules=_replan_rules(),
    )


def validate_accept_partial_readiness(authorization: ActionAuthorization) -> ActionReadinessCheck:
    unmet: list[str] = []
    if not authorization.partial_policy.allow_partial:
        unmet.append("accept_partial requires explicit partial delivery permission")
    if not authorization.partial_policy.product_partial_contract:
        unmet.append("accept_partial requires a product partial-output contract")
    return ActionReadinessCheck(
        action="accept_partial",
        ready=not unmet,
        unmet_preconditions=unmet,
        state_transition_rules=_accept_partial_rules(),
    )


def validate_human_review_readiness(authorization: ActionAuthorization) -> ActionReadinessCheck:
    unmet: list[str] = []
    if authorization.target.scope != "approval":
        unmet.append("human_review target scope must be approval")
    if not authorization.preconditions.get("approval_workflow_contract", False):
        unmet.append("human_review requires an approval workflow contract")
    return ActionReadinessCheck(
        action="human_review",
        ready=not unmet,
        unmet_preconditions=unmet,
        state_transition_rules=_human_review_rules(),
    )


def validate_action_readiness(authorization: ActionAuthorization) -> ActionReadinessCheck:
    """Dispatch the four P12.1 validators without creating an action executor."""

    validators = {
        "retry_research": validate_retry_research_readiness,
        "replan": validate_replan_readiness,
        "accept_partial": validate_accept_partial_readiness,
        "human_review": validate_human_review_readiness,
    }
    validator = validators.get(authorization.allowed_action)
    if validator is None:
        return ActionReadinessCheck(
            action=authorization.allowed_action,
            ready=False,
            unmet_preconditions=["action has no P12.1 readiness validator"],
        )
    return validator(authorization)


def evaluate_action_eligibility(
    recommendation: ActionRecommendation,
    authorization: ActionAuthorization | None = None,
    *,
    state: Any = None,
    execution_context: ExecutionContext | None = None,
    now: datetime | None = None,
) -> ActionEligibilityDecision:
    """Evaluate future-action eligibility without executing or mutating anything."""

    if recommendation.advisory is not True:
        return ActionEligibilityDecision(
            status="unavailable",
            action=recommendation.action,
            reason="recommendation is not a valid advisory contract",
        )
    if recommendation.status == "unavailable":
        return ActionEligibilityDecision(
            status="unavailable",
            action=recommendation.action,
            reason="advisory recommendation is unavailable",
        )
    if recommendation.action not in _SUPPORTED_ACTIONS:
        return ActionEligibilityDecision(
            status="unavailable",
            action=recommendation.action,
            reason="action has no P12.1 eligibility contract",
        )
    if authorization is None:
        return ActionEligibilityDecision(
            status="blocked",
            action=recommendation.action,
            reason="explicit action authorization is required",
            unmet_preconditions=["missing authorization"],
        )
    if authorization.allowed_action != recommendation.action:
        return ActionEligibilityDecision(
            status="blocked",
            action=recommendation.action,
            reason="authorization action does not match advisory recommendation",
            unmet_preconditions=["authorization action mismatch"],
            provenance=authorization.provenance,
        )
    if authorization.provenance.recommendation_fingerprint != action_authorization_content_fingerprint(
        recommendation
    ):
        return ActionEligibilityDecision(
            status="blocked",
            action=recommendation.action,
            reason="authorization provenance does not bind to this advisory recommendation",
            unmet_preconditions=["authorization recommendation provenance mismatch"],
            provenance=authorization.provenance,
        )

    current = _as_utc(now or datetime.now(timezone.utc))
    context = _resolve_context(state, execution_context)
    unmet = _target_preconditions(recommendation.action, authorization.target)
    if authorization.max_executions <= 0:
        unmet.append("authorization max executions must be positive")
    if authorization.readiness_status == "insufficient":
        unmet.append("real-workload calibration/readiness is insufficient")
    elif authorization.readiness_status == "unavailable":
        unmet.append("real-workload calibration/readiness is unavailable")

    effective_deadline, deadline_unmet = _effective_deadline(
        authorization.deadline_policy,
        context,
        current,
    )
    effective_budget, budget_unmet, warnings = _effective_budget(authorization.action_budget, context)
    readiness_check = validate_action_readiness(authorization)
    unmet.extend(deadline_unmet)
    unmet.extend(budget_unmet)
    unmet.extend(readiness_check.unmet_preconditions)
    unmet = list(dict.fromkeys(unmet))

    return ActionEligibilityDecision(
        status="eligible" if not unmet else "blocked",
        action=recommendation.action,
        reason=(
            "authorization and readiness contracts are satisfied"
            if not unmet
            else "action execution contract has unmet preconditions"
        ),
        unmet_preconditions=unmet,
        warnings=warnings,
        effective_deadline_seconds=effective_deadline,
        effective_budget=effective_budget,
        readiness_check=readiness_check,
        state_transition_rules=readiness_check.state_transition_rules,
        provenance=authorization.provenance,
    )


__all__ = [
    "ACTION_AUTHORIZATION_VERSION",
    "ACTION_ELIGIBILITY_POLICY_VERSION",
    "ActionAuthorization",
    "ActionBudget",
    "ActionDeadlinePolicy",
    "ActionEligibilityDecision",
    "ActionFailurePolicy",
    "ActionPartialPolicy",
    "ActionProvenance",
    "ActionReadinessCheck",
    "ActionTarget",
    "NoProgressPolicy",
    "StateTransitionRule",
    "action_authorization_content_fingerprint",
    "evaluate_action_eligibility",
    "validate_accept_partial_readiness",
    "validate_action_readiness",
    "validate_human_review_readiness",
    "validate_replan_readiness",
    "validate_retry_research_readiness",
]
