"""Minimal runtime identity and lifecycle helpers for research runs.

This module owns only top-level runtime fields. It may adopt missing runtime
metadata during checkpoint resume, but never hydrates or alters business fields.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence
from uuid import uuid4

from src.state import ResearchState


RUNTIME_OWNED_FIELDS = frozenset({"run_id", "status", "current_stage", "iteration"})

_RESUME_STAGE_BY_NODE = {
    "plan": "planning",
    "search": "searching",
    "synthesize": "synthesizing",
    "write_report": "reporting",
}


def create_new_run_state(topic: str) -> ResearchState:
    """Create a fresh run with explicit V1/legacy task-field double write."""
    return ResearchState(
        research_topic=topic,
        query=topic,
        run_id=str(uuid4()),
        current_stage="received",
        status="pending",
    )


def start_run(state: ResearchState) -> ResearchState:
    """Advance a fresh run into the existing planning entry stage."""
    return state.model_copy(update={"current_stage": "planning", "status": "running"})


def failed_lifecycle_patch() -> dict[str, str]:
    """Return the runtime-only patch for an explicit Agent failure."""
    return {"current_stage": "failed", "status": "failed"}


def completed_lifecycle_patch() -> dict[str, str]:
    """Return the runtime-only patch for successful Writer completion."""
    return {"current_stage": "complete", "status": "completed"}


def classify_terminal_lifecycle(state: Mapping[str, Any]) -> dict[str, str]:
    """Classify a returned graph state without changing legacy fields.

    The Graph routers already decide whether execution ends.  This helper only
    assigns a top-level runtime terminal state after that decision has finished.
    """
    has_report = bool(state.get("final_report"))
    is_failed = (
        bool(state.get("error"))
        or state.get("status") == "failed"
        or state.get("current_stage") == "failed"
    )

    if is_failed or not has_report:
        desired = failed_lifecycle_patch()
    else:
        desired = completed_lifecycle_patch()

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


def stage_for_snapshot_next(next_nodes: Sequence[str]) -> str | None:
    """Map one known queued Graph node to its runtime stage without guessing."""
    if len(next_nodes) != 1:
        return None
    return _RESUME_STAGE_BY_NODE.get(next_nodes[0])


def resume_lifecycle_patch(
    state: Mapping[str, Any],
    next_nodes: Sequence[str],
) -> dict[str, Any]:
    """Build the minimal runtime patch needed before a queued node resumes."""
    patch: dict[str, Any] = {}

    if not state.get("run_id"):
        patch["run_id"] = str(uuid4())
        patch["iteration"] = state.get("iterations", 0)

    if state.get("status") != "running":
        patch["status"] = "running"

    stage = stage_for_snapshot_next(next_nodes)
    if stage and state.get("current_stage") != stage:
        patch["current_stage"] = stage

    return patch


def adopt_terminal_lifecycle_patch(state: Mapping[str, Any]) -> dict[str, Any]:
    """Adopt only a legacy checkpoint that has no persisted runtime identity."""
    if state.get("run_id"):
        return {}

    patch: dict[str, Any] = {
        "run_id": str(uuid4()),
        "iteration": state.get("iterations", 0),
    }
    if state.get("final_report"):
        patch.update(completed_lifecycle_patch())
    else:
        patch.update(failed_lifecycle_patch())
    return patch


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


def build_cache_replay_state(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """Create an isolated completed replay run from an eligible cache payload."""
    if not is_successful_cache_payload(payload):
        return None

    replay = deepcopy(dict(payload))
    replay.update(
        run_id=str(uuid4()),
        status="completed",
        current_stage="complete",
        iteration=0,
    )
    return replay
