"""Fake-only coverage for P3.1a runtime identity and lifecycle basics."""

import asyncio
from uuid import UUID

from src import graph as graph_module
from src.runtime_lifecycle import (
    completed_lifecycle_patch,
    create_new_run_state,
    failed_lifecycle_patch,
    start_run,
)


def test_new_run_has_an_independent_uuid_and_pending_received_state() -> None:
    state = create_new_run_state("Runtime lifecycle topic")

    assert state.research_topic == "Runtime lifecycle topic"
    assert state.query == "Runtime lifecycle topic"
    assert UUID(state.run_id).version == 4
    assert state.current_stage == "received"
    assert state.status == "pending"
    assert state.iteration == 0
    assert state.iterations == 0


def test_start_run_preserves_identity_and_enters_planning() -> None:
    pending = create_new_run_state("Runtime lifecycle topic")

    running = start_run(pending)

    assert running.run_id == pending.run_id
    assert running.current_stage == "planning"
    assert running.status == "running"
    assert pending.current_stage == "received"
    assert pending.status == "pending"


def test_runtime_terminal_patches_are_owned_separately_from_error_text() -> None:
    assert failed_lifecycle_patch() == {"current_stage": "failed", "status": "failed"}
    assert completed_lifecycle_patch() == {"current_stage": "complete", "status": "completed"}


class _FakeGraph:
    def __init__(self) -> None:
        self.initial_state = None
        self.config = None
        self.lifecycle_patches = []

    async def ainvoke(self, state, config=None):
        self.initial_state = state
        self.config = config
        return {"final_report": "fake report"}

    async def aupdate_state(self, _config, patch):
        self.lifecycle_patches.append(patch)


def test_graph_runner_keeps_thread_id_separate_from_new_run_id(monkeypatch) -> None:
    fake_graph = _FakeGraph()
    monkeypatch.setattr(graph_module, "create_memory_checkpointer", lambda: object())
    monkeypatch.setattr(graph_module, "create_research_graph", lambda checkpointer=None: fake_graph)

    result = asyncio.run(
        graph_module.run_research(
            "Runtime lifecycle topic",
            use_cache=False,
            verbose=False,
            thread_id="checkpoint-thread-123",
        )
    )

    assert result == {
        "final_report": "fake report",
        "current_stage": "complete",
        "status": "completed",
    }
    assert fake_graph.lifecycle_patches == [{"current_stage": "complete", "status": "completed"}]
    assert fake_graph.initial_state is not None
    assert fake_graph.initial_state.run_id != "checkpoint-thread-123"
    assert UUID(fake_graph.initial_state.run_id).version == 4
    assert fake_graph.initial_state.current_stage == "planning"
    assert fake_graph.initial_state.status == "running"
    assert fake_graph.config == {"configurable": {"thread_id": "checkpoint-thread-123"}}
