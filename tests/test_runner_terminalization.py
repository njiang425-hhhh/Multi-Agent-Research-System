"""Fake-only coverage for runner terminal lifecycle behavior."""

import asyncio

import pytest
from langgraph.graph import END, START, StateGraph

from src import graph as graph_module
from src.runtime_lifecycle import create_new_run_state, start_run
from src.state import ResearchState


class _FakeGraph:
    def __init__(
        self,
        result: dict | None = None,
        error: Exception | None = None,
        update_error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.update_error = update_error
        self.update_calls: list[tuple[dict, dict]] = []

    async def ainvoke(self, _state, config=None):
        if self.error is not None:
            raise self.error
        return dict(self.result or {})

    async def aupdate_state(self, config, patch):
        self.update_calls.append((config, patch))
        if self.update_error is not None:
            raise self.update_error


def _run_with_fake_graph(monkeypatch, graph: _FakeGraph) -> dict:
    monkeypatch.setattr(graph_module, "create_memory_checkpointer", lambda: object())
    monkeypatch.setattr(graph_module, "create_research_graph", lambda checkpointer=None: graph)
    return asyncio.run(
        graph_module.run_research(
            "terminal lifecycle topic",
            use_cache=False,
            verbose=False,
            thread_id="fake-terminal-thread",
        )
    )


def test_runner_keeps_a_normal_completed_result_unchanged(monkeypatch) -> None:
    graph = _FakeGraph(
        {
            "final_report": "# Completed report",
            "status": "completed",
            "current_stage": "complete",
            "error": None,
        }
    )

    result = _run_with_fake_graph(monkeypatch, graph)

    assert result["status"] == "completed"
    assert result["current_stage"] == "complete"
    assert result["error"] is None
    assert graph.update_calls == [
        (
            {"configurable": {"thread_id": "fake-terminal-thread"}},
            {"terminal_reason": "completed"},
        )
    ]


def test_runner_terminalizes_router_early_end_without_creating_legacy_error(monkeypatch) -> None:
    graph = _FakeGraph(
        {
            "final_report": None,
            "status": "running",
            "current_stage": "synthesizing",
            "error": None,
        }
    )

    result = _run_with_fake_graph(monkeypatch, graph)

    assert result["status"] == "failed"
    assert result["current_stage"] == "failed"
    assert result["error"] is None
    assert graph.update_calls == [
        (
            {"configurable": {"thread_id": "fake-terminal-thread"}},
            {
                "current_stage": "failed",
                "status": "failed",
                "terminal_reason": "router_terminated",
            },
        )
    ]


def test_runner_keeps_existing_agent_failure_idempotent(monkeypatch) -> None:
    graph = _FakeGraph(
        {
            "final_report": None,
            "status": "failed",
            "current_stage": "failed",
            "error": "existing agent error",
        }
    )

    result = _run_with_fake_graph(monkeypatch, graph)

    assert result["status"] == "failed"
    assert result["current_stage"] == "failed"
    assert result["error"] == "existing agent error"
    assert graph.update_calls == [
        (
            {"configurable": {"thread_id": "fake-terminal-thread"}},
            {"terminal_reason": "agent_failed"},
        )
    ]


def test_runner_reraises_unhandled_error_after_persisting_failure_patch(monkeypatch) -> None:
    original_error = RuntimeError("fake unhandled runner error")
    graph = _FakeGraph(error=original_error)
    monkeypatch.setattr(graph_module, "create_memory_checkpointer", lambda: object())
    monkeypatch.setattr(graph_module, "create_research_graph", lambda checkpointer=None: graph)

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(
            graph_module.run_research(
                "terminal lifecycle topic",
                use_cache=False,
                verbose=False,
                thread_id="fake-terminal-thread",
            )
        )

    assert exc_info.value is original_error
    assert graph.update_calls == [
        (
            {"configurable": {"thread_id": "fake-terminal-thread"}},
            {
                "current_stage": "failed",
                "status": "failed",
                "terminal_reason": "unhandled_exception",
            },
        )
    ]


def test_runner_does_not_mask_unhandled_error_when_checkpoint_update_fails(monkeypatch) -> None:
    original_error = RuntimeError("fake original runner error")
    graph = _FakeGraph(error=original_error, update_error=RuntimeError("fake checkpoint error"))
    monkeypatch.setattr(graph_module, "create_memory_checkpointer", lambda: object())
    monkeypatch.setattr(graph_module, "create_research_graph", lambda checkpointer=None: graph)

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(
            graph_module.run_research(
                "terminal lifecycle topic",
                use_cache=False,
                verbose=False,
                thread_id="fake-terminal-thread",
            )
        )

    assert exc_info.value is original_error
    assert graph.update_calls


def test_persistent_runner_records_unhandled_failure_without_clearing_pending_node(tmp_path, monkeypatch) -> None:
    checkpoint_path = tmp_path / "terminalization.db"
    thread_id = "fake-persistent-terminal-thread"
    config = {"configurable": {"thread_id": thread_id}}
    original_error = RuntimeError("fake persisted node failure")

    async def boom(_state: ResearchState) -> dict:
        raise original_error

    def create_fake_graph(checkpointer=None):
        workflow = StateGraph(ResearchState)
        workflow.add_node("boom", boom)
        workflow.add_edge(START, "boom")
        workflow.add_edge("boom", END)
        return workflow.compile(checkpointer=checkpointer)

    monkeypatch.setattr(graph_module, "get_checkpoint_path", lambda: checkpoint_path)
    monkeypatch.setattr(graph_module, "create_research_graph", create_fake_graph)

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(
            graph_module.run_research_with_persistence(
                "terminal lifecycle topic",
                use_cache=False,
                verbose=False,
                thread_id=thread_id,
            )
        )

    assert exc_info.value is original_error

    async def read_snapshot() -> None:
        async with graph_module.create_sqlite_checkpointer() as checkpointer:
            restored_graph = create_fake_graph(checkpointer)
            snapshot = await restored_graph.aget_state(config)

            assert snapshot.next == ("boom",)
            assert snapshot.values["status"] == "failed"
            assert snapshot.values["current_stage"] == "failed"
            assert snapshot.values.get("error") is None

    asyncio.run(read_snapshot())
