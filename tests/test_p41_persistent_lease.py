"""Fake-only P4.1 persistent runner lease and cancellation coverage."""

import asyncio

import pytest
from langgraph.graph import END, START, StateGraph

from src import runner as graph_module
from src.runtime_lease import PersistentRunLeaseConflictError
from src.runtime_lifecycle import create_new_run_state, start_run
from src.state import ResearchState


class _BlockingPersistentGraph:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def ainvoke(self, _state, config=None):
        self.started.set()
        await self.release.wait()
        return {"final_report": "# completed"}

    async def aupdate_state(self, _config, patch):
        return None


def test_persistent_runner_rejects_concurrent_start_for_same_thread(tmp_path, monkeypatch) -> None:
    checkpoint_path = tmp_path / "lease.db"
    graph = _BlockingPersistentGraph()
    monkeypatch.setattr(graph_module, "get_checkpoint_path", lambda: checkpoint_path)
    monkeypatch.setattr(graph_module, "create_research_graph", lambda checkpointer=None: graph)

    async def exercise() -> None:
        first = asyncio.create_task(
            graph_module.run_research_with_persistence(
                "first", use_cache=False, verbose=False, thread_id="shared-thread"
            )
        )
        await graph.started.wait()

        with pytest.raises(PersistentRunLeaseConflictError):
            await graph_module.run_research_with_persistence(
                "second", use_cache=False, verbose=False, thread_id="shared-thread"
            )

        with pytest.raises(PersistentRunLeaseConflictError):
            await graph_module.resume_research("shared-thread")

        with pytest.raises(PersistentRunLeaseConflictError):
            await graph_module.cancel_research("shared-thread")

        graph.release.set()
        result = await first
        assert result["terminal_reason"] == "completed"

    asyncio.run(exercise())


def test_cache_replay_does_not_contend_for_a_checkpoint_thread_lease(tmp_path, monkeypatch) -> None:
    checkpoint_path = tmp_path / "cache-replay-lease.db"

    class _Cache:
        def get(self, _topic):
            return {
                "run_id": "source-run",
                "status": "completed",
                "current_stage": "complete",
                "final_report": "# cached",
                "error": None,
            }

        def set(self, _topic, _state):
            raise AssertionError("cache replay must not write a new cache entry")

    monkeypatch.setattr(graph_module, "get_checkpoint_path", lambda: checkpoint_path)
    monkeypatch.setattr(graph_module, "ResearchCache", _Cache)

    async def exercise() -> None:
        async with graph_module.PersistentRunLease(
            checkpoint_path, "shared-thread", ttl_seconds=30
        ):
            replay = await graph_module.run_research_with_persistence(
                "cached", verbose=False, thread_id="shared-thread"
            )
        assert replay["terminal_reason"] == "cache_replay"
        assert replay["execution_context"].thread_id is None

    asyncio.run(exercise())


def test_cancel_paused_checkpoint_marks_runtime_terminal_without_invoking_graph(tmp_path, monkeypatch) -> None:
    checkpoint_path = tmp_path / "cancel.db"
    thread_id = "cancelled-checkpoint"
    run_config = {"configurable": {"thread_id": thread_id}}
    calls = 0

    async def node(_state: ResearchState) -> dict:
        nonlocal calls
        calls += 1
        return {"final_report": "# should not run"}

    def create_fake_graph(checkpointer=None):
        workflow = StateGraph(ResearchState)
        workflow.add_node("plan", node)
        workflow.add_edge(START, "plan")
        workflow.add_edge("plan", END)
        return workflow.compile(checkpointer=checkpointer)

    monkeypatch.setattr(graph_module, "get_checkpoint_path", lambda: checkpoint_path)
    monkeypatch.setattr(graph_module, "create_research_graph", create_fake_graph)

    async def exercise() -> None:
        initial = start_run(create_new_run_state("cancel pending", thread_id=thread_id))
        async with graph_module.create_sqlite_checkpointer() as checkpointer:
            graph = create_fake_graph(checkpointer)
            await graph.ainvoke(initial, config=run_config, interrupt_before="plan")

        cancelled = await graph_module.cancel_research(thread_id)
        assert cancelled["status"] == "cancelled"
        assert cancelled["terminal_reason"] == "cancelled"
        assert cancelled.get("error") is None

        resumed = await graph_module.resume_research(thread_id)
        assert resumed["status"] == "cancelled"
        assert resumed["terminal_reason"] == "cancelled"
        assert calls == 0

    asyncio.run(exercise())
