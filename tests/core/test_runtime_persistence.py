"""Minimal fake-only contracts for runtime deadline, SQLite, and resume."""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from langgraph.graph import END, START, StateGraph

from src import runner as runner_module
from src.runtime_control import RunPolicy, create_execution_context
from src.runtime_lifecycle import create_new_run_state, start_run
from src.state import ResearchState


def _checkpoint_graph(checkpointer: Any):
    async def finish(_state: ResearchState) -> dict[str, str]:
        return {"current_stage": "complete", "status": "completed"}

    workflow = StateGraph(ResearchState)
    workflow.add_node("finish", finish)
    workflow.add_edge(START, "finish")
    workflow.add_edge("finish", END)
    return workflow.compile(checkpointer=checkpointer)


def test_runtime_context_uses_one_absolute_deadline() -> None:
    now = datetime(2026, 8, 17, tzinfo=timezone.utc)
    context = create_execution_context(
        run_id="run-1", thread_id="thread-1", policy=RunPolicy(total_timeout_seconds=10), now=now
    )

    assert context.remaining_timeout_seconds(now=now + timedelta(seconds=3)) == 7
    assert context.remaining_timeout_seconds(now=now + timedelta(seconds=20)) == 0


def test_async_sqlite_checkpoint_persists_and_reads_same_thread(tmp_path, monkeypatch) -> None:
    checkpoint_path = tmp_path / "research_checkpoints.db"
    monkeypatch.setattr(runner_module, "get_checkpoint_path", lambda: checkpoint_path)

    async def exercise_checkpoint() -> None:
        config = {"configurable": {"thread_id": "fake-async-sqlite-thread"}}
        initial_state = start_run(create_new_run_state("async sqlite checkpoint topic"))

        async with runner_module.create_sqlite_checkpointer() as checkpointer:
            graph = _checkpoint_graph(checkpointer)
            result = await graph.ainvoke(initial_state, config=config)
            snapshot = await graph.aget_state(config)
            assert result["run_id"] == initial_state.run_id
            assert snapshot.next == ()
            assert snapshot.values["status"] == "completed"

        async with runner_module.create_sqlite_checkpointer() as checkpointer:
            restored = await _checkpoint_graph(checkpointer).aget_state(config)
            assert restored.next == ()
            assert restored.values["run_id"] == initial_state.run_id

    asyncio.run(exercise_checkpoint())


def test_terminal_sqlite_resume_returns_persisted_result_without_reinvoking_graph(tmp_path, monkeypatch) -> None:
    checkpoint_path = tmp_path / "terminal.db"
    config = {"configurable": {"thread_id": "terminal-thread"}}
    calls = 0

    async def complete(_state: ResearchState) -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {"report": {"title": "done", "content": "# done", "status": "completed"}, "status": "completed", "current_stage": "complete"}

    def create_fake_graph(checkpointer=None):
        workflow = StateGraph(ResearchState)
        workflow.add_node("plan", complete)
        workflow.add_edge(START, "plan")
        workflow.add_edge("plan", END)
        return workflow.compile(checkpointer=checkpointer)

    monkeypatch.setattr(runner_module, "get_checkpoint_path", lambda: checkpoint_path)
    monkeypatch.setattr(runner_module, "create_research_graph", create_fake_graph)

    async def exercise_resume() -> None:
        initial = start_run(create_new_run_state("terminal topic"))
        async with runner_module.create_sqlite_checkpointer() as checkpointer:
            await create_fake_graph(checkpointer).ainvoke(initial, config=config)

        result = await runner_module.resume_research("terminal-thread")
        assert calls == 1
        assert result["run_id"] == initial.run_id
        assert result["terminal_reason"] == "completed"

    asyncio.run(exercise_resume())
