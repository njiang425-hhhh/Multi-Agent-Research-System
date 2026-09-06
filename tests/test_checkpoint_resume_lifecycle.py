"""Fake-only checkpoint resume and lifecycle-adoption coverage."""

import asyncio
from typing import Any
from typing_extensions import TypedDict
from uuid import UUID

import pytest
from langgraph.graph import END, START, StateGraph

from src import runner as graph_module
from src.runtime_lifecycle import failed_lifecycle_patch
from src.state import ResearchState
from src.runtime_lifecycle import create_new_run_state, start_run


class _LegacyCheckpointState(TypedDict, total=False):
    research_topic: str
    iterations: int
    final_report: str | None


def _single_node_graph(node_name: str, node: Any, checkpointer: Any):
    workflow = StateGraph(ResearchState)
    workflow.add_node(node_name, node)
    workflow.add_edge(START, node_name)
    workflow.add_edge(node_name, END)
    return workflow.compile(checkpointer=checkpointer)


def _legacy_terminal_graph(final_report: str | None, checkpointer: Any):
    async def finish(_state: _LegacyCheckpointState) -> dict[str, Any]:
        return {"final_report": final_report} if final_report else {}

    workflow = StateGraph(_LegacyCheckpointState)
    workflow.add_node("finish", finish)
    workflow.add_edge(START, "finish")
    workflow.add_edge("finish", END)
    return workflow.compile(checkpointer=checkpointer)


@pytest.mark.parametrize(
    ("node_name", "expected_stage"),
    [
        ("plan", "planning"),
        ("search", "searching"),
        ("synthesize", "synthesizing"),
        ("write_report", "reporting"),
    ],
)
def test_p3_pending_resume_preserves_identity_counts_and_maps_stage(
    tmp_path,
    monkeypatch,
    node_name: str,
    expected_stage: str,
) -> None:
    checkpoint_path = tmp_path / f"{node_name}.db"
    thread_id = f"p3-pending-{node_name}"
    config = {"configurable": {"thread_id": thread_id}}
    observed: dict[str, Any] = {}

    async def complete(state: ResearchState) -> dict[str, Any]:
        observed.update(
            run_id=state.run_id,
            status=state.status,
            current_stage=state.current_stage,
            iteration=state.iteration,
            iterations=state.iterations,
        )
        return {
            "final_report": "# resumed report",
            "current_stage": "complete",
            "status": "completed",
        }

    def create_fake_graph(checkpointer=None):
        return _single_node_graph(node_name, complete, checkpointer)

    monkeypatch.setattr(graph_module, "get_checkpoint_path", lambda: checkpoint_path)
    monkeypatch.setattr(graph_module, "create_research_graph", create_fake_graph)

    async def exercise() -> None:
        initial = start_run(create_new_run_state("resume topic")).model_copy(
            update={"iteration": 3, "iterations": 3}
        )
        async with graph_module.create_sqlite_checkpointer() as checkpointer:
            graph = create_fake_graph(checkpointer)
            await graph.ainvoke(initial, config=config, interrupt_before=node_name)
            snapshot = await graph.aget_state(config)
            assert snapshot.next == (node_name,)

        result = await graph_module.resume_research(
            thread_id,
            additional_input={
                "run_id": "caller-run-id",
                "status": "completed",
                "current_stage": "failed",
                "iteration": 999,
            },
        )

        assert observed == {
            "run_id": initial.run_id,
            "status": "running",
            "current_stage": expected_stage,
            "iteration": 3,
            "iterations": 3,
        }
        assert result["run_id"] == initial.run_id
        assert result["iteration"] == 3
        assert result["iterations"] == 3

    asyncio.run(exercise())


def test_failed_pending_checkpoint_reenters_running_before_resume(tmp_path, monkeypatch) -> None:
    checkpoint_path = tmp_path / "failed-pending.db"
    thread_id = "p3-failed-pending"
    config = {"configurable": {"thread_id": thread_id}}
    observed: dict[str, Any] = {}

    async def complete(state: ResearchState) -> dict[str, Any]:
        observed.update(run_id=state.run_id, status=state.status, current_stage=state.current_stage)
        return {
            "final_report": "# recovered report",
            "current_stage": "complete",
            "status": "completed",
        }

    def create_fake_graph(checkpointer=None):
        return _single_node_graph("plan", complete, checkpointer)

    monkeypatch.setattr(graph_module, "get_checkpoint_path", lambda: checkpoint_path)
    monkeypatch.setattr(graph_module, "create_research_graph", create_fake_graph)

    async def exercise() -> None:
        initial = start_run(create_new_run_state("failed resume topic"))
        async with graph_module.create_sqlite_checkpointer() as checkpointer:
            graph = create_fake_graph(checkpointer)
            await graph.ainvoke(initial, config=config, interrupt_before="plan")
            await graph.aupdate_state(config, failed_lifecycle_patch())

        result = await graph_module.resume_research(thread_id)

        assert observed == {
            "run_id": initial.run_id,
            "status": "running",
            "current_stage": "planning",
        }
        assert result["run_id"] == initial.run_id

    asyncio.run(exercise())


def test_terminal_p3_checkpoint_returns_without_reinvoking_graph(tmp_path, monkeypatch) -> None:
    checkpoint_path = tmp_path / "terminal-p3.db"
    thread_id = "p3-terminal"
    config = {"configurable": {"thread_id": thread_id}}
    calls = 0

    async def complete(_state: ResearchState) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {
            "final_report": "# completed report",
            "current_stage": "complete",
            "status": "completed",
        }

    def create_fake_graph(checkpointer=None):
        return _single_node_graph("plan", complete, checkpointer)

    monkeypatch.setattr(graph_module, "get_checkpoint_path", lambda: checkpoint_path)
    monkeypatch.setattr(graph_module, "create_research_graph", create_fake_graph)

    async def exercise() -> None:
        initial = start_run(create_new_run_state("terminal topic"))
        async with graph_module.create_sqlite_checkpointer() as checkpointer:
            graph = create_fake_graph(checkpointer)
            await graph.ainvoke(initial, config=config)
            # Simulate a terminal P3 checkpoint persisted before P4.1 added
            # runtime context and terminal explanation.
            await graph.aupdate_state(
                config,
                {"execution_context": None, "terminal_reason": None},
            )
            assert (await graph.aget_state(config)).next == ()

        result = await graph_module.resume_research(thread_id)

        assert calls == 1
        assert result["run_id"] == initial.run_id
        assert result["iteration"] == initial.iteration
        assert result["iterations"] == initial.iterations
        assert result["terminal_reason"] == "completed"
        assert result["execution_context"].run_id == initial.run_id

    asyncio.run(exercise())


def test_pre_p3_pending_checkpoint_adopts_only_runtime_fields(tmp_path, monkeypatch) -> None:
    checkpoint_path = tmp_path / "legacy-pending.db"
    thread_id = "legacy-pending"
    config = {"configurable": {"thread_id": thread_id}}
    observed: dict[str, Any] = {}
    original_error = RuntimeError("stop after observing adopted state")

    async def legacy_search(_state: _LegacyCheckpointState) -> dict[str, Any]:
        return {}

    def create_legacy_graph(checkpointer):
        workflow = StateGraph(_LegacyCheckpointState)
        workflow.add_node("search", legacy_search)
        workflow.add_edge(START, "search")
        workflow.add_edge("search", END)
        return workflow.compile(checkpointer=checkpointer)

    async def observe_adoption(state: ResearchState) -> dict[str, Any]:
        observed.update(
            run_id=state.run_id,
            status=state.status,
            current_stage=state.current_stage,
            iteration=state.iteration,
            iterations=state.iterations,
        )
        raise original_error

    def create_current_graph(checkpointer=None):
        return _single_node_graph("search", observe_adoption, checkpointer)

    monkeypatch.setattr(graph_module, "get_checkpoint_path", lambda: checkpoint_path)
    monkeypatch.setattr(graph_module, "create_research_graph", create_current_graph)

    async def exercise() -> None:
        async with graph_module.create_sqlite_checkpointer() as checkpointer:
            legacy_graph = create_legacy_graph(checkpointer)
            await legacy_graph.ainvoke(
                {"research_topic": "legacy topic", "iterations": 7},
                config=config,
                interrupt_before="search",
            )
            snapshot = await legacy_graph.aget_state(config)
            assert snapshot.next == ("search",)
            assert "run_id" not in snapshot.values

        with pytest.raises(RuntimeError) as exc_info:
            await graph_module.resume_research(thread_id)
        assert exc_info.value is original_error
        assert UUID(observed["run_id"]).version == 4
        assert observed["status"] == "running"
        assert observed["current_stage"] == "searching"
        assert observed["iteration"] == 7
        assert observed["iterations"] == 7

        async with graph_module.create_sqlite_checkpointer() as checkpointer:
            current_graph = create_current_graph(checkpointer)
            snapshot = await current_graph.aget_state(config)
            assert "query" not in snapshot.values
            assert "documents" not in snapshot.values
            assert "findings" not in snapshot.values
            assert "report" not in snapshot.values
            assert "usage" not in snapshot.values

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("final_report", "expected_stage", "expected_status"),
    [
        ("# legacy completed", "complete", "completed"),
        (None, "failed", "failed"),
    ],
)
def test_terminal_pre_p3_checkpoint_adoption_classifies_without_execution(
    tmp_path,
    monkeypatch,
    final_report: str | None,
    expected_stage: str,
    expected_status: str,
) -> None:
    checkpoint_path = tmp_path / f"legacy-terminal-{expected_status}.db"
    thread_id = f"legacy-terminal-{expected_status}"
    config = {"configurable": {"thread_id": thread_id}}
    current_graph_calls = 0

    async def should_not_run(_state: ResearchState) -> dict[str, Any]:
        nonlocal current_graph_calls
        current_graph_calls += 1
        raise AssertionError("terminal checkpoint must not invoke Graph")

    def create_current_graph(checkpointer=None):
        return _single_node_graph("plan", should_not_run, checkpointer)

    monkeypatch.setattr(graph_module, "get_checkpoint_path", lambda: checkpoint_path)
    monkeypatch.setattr(graph_module, "create_research_graph", create_current_graph)

    async def exercise() -> None:
        async with graph_module.create_sqlite_checkpointer() as checkpointer:
            legacy_graph = _legacy_terminal_graph(final_report, checkpointer)
            await legacy_graph.ainvoke(
                {"research_topic": "legacy terminal", "iterations": 4},
                config=config,
            )
            assert (await legacy_graph.aget_state(config)).next == ()

        result = await graph_module.resume_research(thread_id)

        assert current_graph_calls == 0
        assert UUID(result["run_id"]).version == 4
        assert result["iteration"] == 4
        assert result["iterations"] == 4
        assert result["current_stage"] == expected_stage
        assert result["status"] == expected_status
        assert "query" not in result
        assert "documents" not in result
        assert "findings" not in result
        assert "report" not in result
        assert "usage" not in result

    asyncio.run(exercise())


def test_unknown_pending_node_does_not_guess_a_stage(tmp_path, monkeypatch) -> None:
    checkpoint_path = tmp_path / "unknown-node.db"
    thread_id = "p3-unknown-node"
    config = {"configurable": {"thread_id": thread_id}}
    observed: dict[str, Any] = {}

    async def complete(state: ResearchState) -> dict[str, Any]:
        observed["current_stage"] = state.current_stage
        observed["status"] = state.status
        return {
            "final_report": "# unknown node report",
            "current_stage": "complete",
            "status": "completed",
        }

    def create_fake_graph(checkpointer=None):
        return _single_node_graph("unknown_node", complete, checkpointer)

    monkeypatch.setattr(graph_module, "get_checkpoint_path", lambda: checkpoint_path)
    monkeypatch.setattr(graph_module, "create_research_graph", create_fake_graph)

    async def exercise() -> None:
        initial = start_run(create_new_run_state("unknown node topic")).model_copy(
            update={"current_stage": "synthesizing"}
        )
        async with graph_module.create_sqlite_checkpointer() as checkpointer:
            graph = create_fake_graph(checkpointer)
            await graph.ainvoke(initial, config=config, interrupt_before="unknown_node")

        await graph_module.resume_research(thread_id)

        assert observed == {"current_stage": "synthesizing", "status": "running"}

    asyncio.run(exercise())
