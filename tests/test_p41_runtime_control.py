"""Fake-only P4.1 Runtime Ownership contracts."""

import asyncio
from datetime import datetime, timedelta, timezone
from time import perf_counter

import pytest

from src import runner as graph_module
from src.execution_policy import (
    ExecutionContextCoordinator,
    OperationBudgetExhausted,
    OperationExecutionPolicy,
    execute_operation,
)
from src.runtime_control import RunPolicy, create_execution_context
from src.runtime_lifecycle import (
    apply_terminal_lifecycle,
    build_cache_replay_state,
    create_new_run_state,
    filter_runtime_owned_input,
)


def test_fresh_run_captures_serializable_runtime_context_without_touching_legacy_fields() -> None:
    policy = RunPolicy(total_timeout_seconds=15)
    state = create_new_run_state("P4.1 topic", thread_id="persistent-thread", run_policy=policy)

    assert state.execution_context is not None
    assert state.execution_context.run_id == state.run_id
    assert state.execution_context.thread_id == "persistent-thread"
    assert state.execution_context.policy == policy
    assert state.execution_context.delivery_semantics == "at_least_once"
    assert state.terminal_reason is None
    assert state.query == "P4.1 topic"
    assert state.research_topic == ""


def test_execution_context_uses_an_absolute_deadline_across_resume_boundaries() -> None:
    now = datetime(2026, 8, 17, tzinfo=timezone.utc)
    context = create_execution_context(
        run_id="run-1",
        thread_id="thread-1",
        policy=RunPolicy(total_timeout_seconds=10),
        now=now,
    )

    assert context.remaining_timeout_seconds(now=now + timedelta(seconds=3)) == 7
    assert context.remaining_timeout_seconds(now=now + timedelta(seconds=20)) == 0


def test_terminal_reason_distinguishes_router_end_from_agent_error_without_changing_error() -> None:
    router_end = apply_terminal_lifecycle(
        {"final_report": None, "status": "running", "current_stage": "synthesizing", "error": None}
    )
    agent_error = apply_terminal_lifecycle(
        {"final_report": None, "status": "failed", "current_stage": "failed", "error": "agent failed"}
    )

    assert router_end["status"] == "failed"
    assert router_end["terminal_reason"] == "router_terminated"
    assert router_end["error"] is None
    assert agent_error["terminal_reason"] == "agent_failed"
    assert agent_error["error"] == "agent failed"


def test_cache_replay_is_a_new_unleased_at_least_once_context() -> None:
    replay = build_cache_replay_state(
        {
            "run_id": "source-run",
            "status": "completed",
            "current_stage": "complete",
            "final_report": "# cached",
            "error": None,
            "agent_trace": [{"event_id": "source-event"}],
        }
    )

    assert replay is not None
    assert replay["run_id"] != "source-run"
    assert replay["terminal_reason"] == "cache_replay"
    assert replay["execution_context"].thread_id is None
    assert replay["execution_context"].delivery_semantics == "at_least_once"
    assert replay["agent_trace"] == []


def test_runtime_owned_fields_cannot_be_supplied_as_resume_business_input() -> None:
    filtered = filter_runtime_owned_input(
        {
            "query": "allowed business input",
            "run_id": "forbidden",
            "execution_context": {"run_id": "forbidden"},
            "terminal_reason": "completed",
        }
    )

    assert filtered == {"query": "allowed business input"}


def test_concurrent_operation_budget_requires_a_shared_context_coordinator() -> None:
    async def operation() -> str:
        await asyncio.sleep(0.01)
        return "ok"

    async def exercise() -> None:
        raw_context = create_execution_context(
            run_id="raw-risk",
            thread_id="raw-risk",
            policy=RunPolicy(max_operation_calls=1),
        )
        raw_results = await asyncio.gather(
            *(
                execute_operation(
                    operation,
                    policy=OperationExecutionPolicy(),
                    local_deadline=perf_counter() + 5,
                    context=raw_context,
                )
                for _ in range(2)
            ),
            return_exceptions=True,
        )
        assert sum(not isinstance(result, Exception) for result in raw_results) == 2

        coordinated_context = ExecutionContextCoordinator(
            create_execution_context(
                run_id="coordinated",
                thread_id="coordinated",
                policy=RunPolicy(max_operation_calls=1),
            )
        )
        coordinated_results = await asyncio.gather(
            *(
                execute_operation(
                    operation,
                    policy=OperationExecutionPolicy(),
                    local_deadline=perf_counter() + 5,
                    context=coordinated_context,
                )
                for _ in range(2)
            ),
            return_exceptions=True,
        )

        assert sum(not isinstance(result, Exception) for result in coordinated_results) == 1
        assert sum(isinstance(result, OperationBudgetExhausted) for result in coordinated_results) == 1
        assert coordinated_context.context is not None
        assert coordinated_context.context.operation_calls == 1

    asyncio.run(exercise())


class _BlockingGraph:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.patches: list[dict] = []

    async def ainvoke(self, _state, config=None):
        self.started.set()
        await self.release.wait()
        return {"final_report": "# completed"}

    async def aupdate_state(self, _config, patch):
        self.patches.append(patch)


def test_cancelling_a_running_task_persists_cancelled_reason_and_reraises(monkeypatch) -> None:
    graph = _BlockingGraph()
    monkeypatch.setattr(graph_module, "create_memory_checkpointer", lambda: object())
    monkeypatch.setattr(graph_module, "create_research_graph", lambda checkpointer=None: graph)

    async def exercise() -> None:
        task = asyncio.create_task(
            graph_module.run_research(
                "cancel task",
                use_cache=False,
                verbose=False,
                thread_id="cancel-thread",
            )
        )
        await graph.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())

    assert graph.patches == [
        {"current_stage": "failed", "status": "cancelled", "terminal_reason": "cancelled"}
    ]


def test_run_deadline_persists_timeout_reason_and_reraises(monkeypatch) -> None:
    class _SlowGraph:
        def __init__(self) -> None:
            self.patches: list[dict] = []

        async def ainvoke(self, _state, config=None):
            await asyncio.sleep(0.2)
            return {"final_report": "# too late"}

        async def aupdate_state(self, _config, patch):
            self.patches.append(patch)

    graph = _SlowGraph()
    monkeypatch.setattr(graph_module, "create_memory_checkpointer", lambda: object())
    monkeypatch.setattr(graph_module, "create_research_graph", lambda checkpointer=None: graph)

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(
            graph_module.run_research(
                "timeout task",
                use_cache=False,
                verbose=False,
                thread_id="timeout-thread",
                run_policy=RunPolicy(total_timeout_seconds=0.01),
            )
        )

    assert graph.patches == [
        {"current_stage": "failed", "status": "failed", "terminal_reason": "timeout"}
    ]
