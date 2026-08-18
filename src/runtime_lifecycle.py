"""Minimal runtime identity and lifecycle helpers for research runs.

This module owns only top-level runtime fields. It may adopt missing runtime
metadata during checkpoint resume, but never hydrates or alters business fields.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Optional, Sequence
from uuid import uuid4

from src.runtime_control import (
    ExecutionContext,
    RunPolicy,
    TerminalReason,
    create_execution_context,
)
from src.state import ResearchState


RUNTIME_OWNED_FIELDS = frozenset(
    {"run_id", "status", "current_stage", "iteration", "execution_context", "terminal_reason"}
)

_RESUME_STAGE_BY_NODE = {
    "plan": "planning",
    "search": "searching",
    "synthesize": "synthesizing",
    "write_report": "reporting",
}


def create_new_run_state(
    topic: str,
    *,
    thread_id: Optional[str] = None,
    run_policy: Optional[RunPolicy] = None,
) -> ResearchState:
    """Create a fresh run with explicit V1/legacy task-field double write."""
    run_id = str(uuid4())
    return ResearchState(
        research_topic=topic,
        query=topic,
        run_id=run_id,
        current_stage="received",
        status="pending",
        execution_context=create_execution_context(
            run_id=run_id,
            thread_id=thread_id,
            policy=run_policy,
        ),
    )


def start_run(state: ResearchState) -> ResearchState:
    """Advance a fresh run into the existing planning entry stage."""
    return state.model_copy(update={"current_stage": "planning", "status": "running"})


def failed_lifecycle_patch() -> dict[str, str]:
    """Return the legacy-compatible lifecycle patch used by an Agent failure.

    Agents may preserve the existing failed stage/status behavior, but terminal
    explanation remains Runtime-owned and is attached only at a runner
    boundary.
    """
    return {"current_stage": "failed", "status": "failed"}


def completed_lifecycle_patch() -> dict[str, str]:
    """Return the legacy-compatible lifecycle patch for Writer success."""
    return {"current_stage": "complete", "status": "completed"}


def _terminal_reason_patch(
    lifecycle_patch: Mapping[str, str],
    reason: TerminalReason,
) -> dict[str, str]:
    return {**lifecycle_patch, "terminal_reason": reason}


def failed_terminal_lifecycle_patch(reason: TerminalReason) -> dict[str, str]:
    """Return a runtime-owned failed terminal patch with an explanation."""
    return _terminal_reason_patch(failed_lifecycle_patch(), reason)


def completed_terminal_lifecycle_patch(reason: TerminalReason = "completed") -> dict[str, str]:
    """Return a runtime-owned completed terminal patch with an explanation."""
    return _terminal_reason_patch(completed_lifecycle_patch(), reason)


def cancelled_lifecycle_patch() -> dict[str, str]:
    """Return the runtime-only patch for cooperative task cancellation."""
    return {
        "current_stage": "failed",
        "status": "cancelled",
        "terminal_reason": "cancelled",
    }


def classify_terminal_lifecycle(state: Mapping[str, Any]) -> dict[str, str]:
    """Classify a returned graph state without changing legacy fields.

    The Graph routers already decide whether execution ends.  This helper only
    assigns a top-level runtime terminal state after that decision has finished.
    """
    has_report = bool(state.get("final_report"))
    has_error = bool(state.get("error"))
    is_failed_with_report = bool(state.get("final_report")) and (
        state.get("status") == "failed" or state.get("current_stage") == "failed"
    )
    execution_context = state.get("execution_context")
    if isinstance(execution_context, Mapping):
        operation_stop_reason = execution_context.get("operation_stop_reason")
    else:
        operation_stop_reason = getattr(execution_context, "operation_stop_reason", None)

    if state.get("status") == "cancelled" or state.get("terminal_reason") == "cancelled":
        desired = cancelled_lifecycle_patch()
    elif not has_report and operation_stop_reason == "budget_exhausted":
        desired = failed_terminal_lifecycle_patch("budget_exhausted")
    elif not has_report and operation_stop_reason == "deadline_exhausted":
        desired = timeout_lifecycle_patch()
    elif has_error or is_failed_with_report:
        desired = failed_terminal_lifecycle_patch("agent_failed")
    elif not has_report:
        # Preserve the legacy no-error router behavior while making the end
        # state distinguishable from an Agent-produced error.
        desired = failed_terminal_lifecycle_patch("router_terminated")
    else:
        desired = completed_terminal_lifecycle_patch()

    return {
        field: value
        for field, value in desired.items()
        if state.get(field) != value
    }


def apply_terminal_lifecycle(state: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy of a graph result with its terminal runtime patch applied."""
    result = dict(state)
    result.update(classify_terminal_lifecycle(state))
    return result


def timeout_lifecycle_patch() -> dict[str, str]:
    """Return the terminal patch for a run-level deadline expiry."""
    return failed_terminal_lifecycle_patch("timeout")


def unhandled_exception_lifecycle_patch() -> dict[str, str]:
    """Return the terminal patch for an exception escaping Graph execution."""
    return failed_terminal_lifecycle_patch("unhandled_exception")


def stage_for_snapshot_next(next_nodes: Sequence[str]) -> str | None:
    """Map one known queued Graph node to its runtime stage without guessing."""
    if len(next_nodes) != 1:
        return None
    return _RESUME_STAGE_BY_NODE.get(next_nodes[0])


def resume_lifecycle_patch(
    state: Mapping[str, Any],
    next_nodes: Sequence[str],
    *,
    thread_id: Optional[str] = None,
) -> dict[str, Any]:
    """Build the minimal runtime patch needed before a queued node resumes."""
    patch: dict[str, Any] = {}

    if not state.get("run_id"):
        patch["run_id"] = str(uuid4())
        patch["iteration"] = state.get("iterations", 0)

    run_id = patch.get("run_id") or str(state.get("run_id") or "")
    if not state.get("execution_context") and run_id:
        patch["execution_context"] = create_execution_context(
            run_id=run_id,
            thread_id=thread_id,
        )

    if state.get("status") != "running":
        patch["status"] = "running"

    stage = stage_for_snapshot_next(next_nodes)
    if stage and state.get("current_stage") != stage:
        patch["current_stage"] = stage

    return patch


def adopt_terminal_lifecycle_patch(
    state: Mapping[str, Any],
    *,
    thread_id: Optional[str] = None,
) -> dict[str, Any]:
    """Adopt missing runtime metadata on a terminal checkpoint only.

    This is intentionally limited to P3/P4 runtime fields.  It neither
    hydrates V1 business fields nor changes the completed checkpoint queue.
    """
    patch: dict[str, Any] = {}
    run_id = str(state.get("run_id") or "")
    if not run_id:
        run_id = str(uuid4())
        patch["run_id"] = run_id
        patch["iteration"] = state.get("iterations", 0)

    if not state.get("terminal_reason"):
        patch.update(classify_terminal_lifecycle(state))
    if not state.get("execution_context"):
        patch["execution_context"] = create_execution_context(
            run_id=run_id,
            thread_id=thread_id,
        )
    return patch


def execution_context_from_state(
    state: Mapping[str, Any],
    *,
    thread_id: Optional[str] = None,
) -> Optional[ExecutionContext]:
    """Read a persisted context without making a business-field migration."""

    value = state.get("execution_context")
    if value:
        return value if isinstance(value, ExecutionContext) else ExecutionContext.model_validate(value)
    run_id = str(state.get("run_id") or "")
    if not run_id:
        return None
    return create_execution_context(run_id=run_id, thread_id=thread_id)


def filter_runtime_owned_input(additional_input: Mapping[str, Any]) -> dict[str, Any]:
    """Keep caller-provided business input while protecting runtime ownership."""
    return {
        field: value
        for field, value in additional_input.items()
        if field not in RUNTIME_OWNED_FIELDS
    }


def is_successful_cache_payload(payload: Mapping[str, Any]) -> bool:
    """Return whether a cached payload represents a completed report result."""
    return (
        bool(payload.get("final_report"))
        and not payload.get("error")
        and payload.get("status") != "failed"
        and payload.get("current_stage") != "failed"
    )


def build_cache_replay_state(
    payload: Mapping[str, Any],
    *,
    run_policy: Optional[RunPolicy] = None,
) -> dict[str, Any] | None:
    """Create an isolated completed replay run from an eligible cache payload."""
    if not is_successful_cache_payload(payload):
        return None

    replay = deepcopy(dict(payload))
    run_id = str(uuid4())
    replay.update(
        run_id=run_id,
        status="completed",
        current_stage="complete",
        iteration=0,
        terminal_reason="cache_replay",
        execution_context=create_execution_context(
            run_id=run_id,
            thread_id=None,
            policy=run_policy,
        ),
        # Trace is run-scoped. A cache replay executes no nodes, so retaining
        # source-run events here would make their trace identity misleading.
        agent_trace=[],
    )
    return replay
