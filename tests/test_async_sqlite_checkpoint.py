"""Fake-only coverage for the async SQLite checkpoint baseline."""

import asyncio

from langgraph.graph import END, START, StateGraph

from src import graph as graph_module
from src.runtime_lifecycle import create_new_run_state, start_run
from src.state import ResearchState


def _checkpoint_graph(checkpointer):
    async def finish(_state: ResearchState) -> dict[str, str]:
        return {"current_stage": "complete", "status": "completed"}

    workflow = StateGraph(ResearchState)
    workflow.add_node("finish", finish)
    workflow.add_edge(START, "finish")
    workflow.add_edge("finish", END)
    return workflow.compile(checkpointer=checkpointer)


def test_async_sqlite_checkpoint_persists_and_reads_same_thread(tmp_path, monkeypatch) -> None:
    checkpoint_path = tmp_path / "research_checkpoints.db"
    monkeypatch.setattr(graph_module, "get_checkpoint_path", lambda: checkpoint_path)

    async def exercise_checkpoint() -> None:
        thread_id = "fake-async-sqlite-thread"
        config = {"configurable": {"thread_id": thread_id}}
        initial_state = start_run(create_new_run_state("async sqlite checkpoint topic"))

        async with graph_module.create_sqlite_checkpointer() as checkpointer:
            graph = _checkpoint_graph(checkpointer)
            result = await graph.ainvoke(initial_state, config=config)
            snapshot = await graph.aget_state(config)

            assert result["run_id"] == initial_state.run_id
            assert result["run_id"] != thread_id
            assert snapshot.next == ()
            assert snapshot.values["run_id"] == initial_state.run_id
            assert snapshot.values["status"] == "completed"
            assert snapshot.values["current_stage"] == "complete"

        async with graph_module.create_sqlite_checkpointer() as checkpointer:
            restored_graph = _checkpoint_graph(checkpointer)
            restored_snapshot = await restored_graph.aget_state(config)

            assert restored_snapshot.next == ()
            assert restored_snapshot.values["run_id"] == initial_state.run_id
            assert restored_snapshot.values["status"] == "completed"
            assert restored_snapshot.values["current_stage"] == "complete"

    asyncio.run(exercise_checkpoint())
