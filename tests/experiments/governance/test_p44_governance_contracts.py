"""Executable P4.4 ownership boundaries; all dependencies are fake/local."""

from src.evaluation.evaluator import evaluate_run
from src.runtime_lifecycle import RUNTIME_OWNED_FIELDS, filter_runtime_owned_input
from src.state import ResearchState


def test_state_keeps_legacy_and_v1_fields_explicit_while_runtime_input_is_protected() -> None:
    state = ResearchState(research_topic="governance topic")

    assert state.query == ""
    assert state.research_plan is None
    assert state.plan is None
    assert {"run_id", "execution_context", "terminal_reason"}.issubset(RUNTIME_OWNED_FIELDS)
    assert filter_runtime_owned_input(
        {"query": "explicit V1 input", "run_id": "forbidden", "terminal_reason": "completed"}
    ) == {"query": "explicit V1 input"}


def test_evaluation_is_read_only_and_carries_its_own_governance_snapshot() -> None:
    state = {"research_topic": "governance topic", "status": "failed", "current_stage": "failed"}
    before = dict(state)

    result = evaluate_run(state)

    assert state == before
    assert result.evaluation_snapshot is not None
    assert result.evaluation_snapshot.expected_completed_nodes == [
        "plan", "search", "synthesize", "write_report"
    ]
