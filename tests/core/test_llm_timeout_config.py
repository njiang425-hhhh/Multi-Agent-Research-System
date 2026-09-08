"""Fake-only contracts for the configurable LLM operation timeout."""

import asyncio

import pytest

from src.config import ResearchConfig
from src.execution_policy import OperationDeadlineExceeded
from src.llm_execution import execute_llm_operation
from src.runtime_control import ExecutionContext, RunPolicy, create_execution_context


def test_llm_operation_timeout_defaults_to_180_seconds(monkeypatch) -> None:
    monkeypatch.delenv("LLM_OPERATION_TIMEOUT_SECONDS", raising=False)

    assert ResearchConfig().llm_operation_timeout_seconds == 180


def test_llm_operation_timeout_can_be_overridden_by_environment(monkeypatch) -> None:
    monkeypatch.setenv("LLM_OPERATION_TIMEOUT_SECONDS", "240")

    assert ResearchConfig().llm_operation_timeout_seconds == 240


def test_llm_operation_uses_global_remaining_when_it_is_lower(monkeypatch) -> None:
    observed_timeouts: list[float] = []

    async def fake_wait_for(awaitable, timeout):
        observed_timeouts.append(timeout)
        return await awaitable

    monkeypatch.setattr("src.execution_policy.asyncio.wait_for", fake_wait_for)
    monkeypatch.setattr(ExecutionContext, "remaining_timeout_seconds", lambda _self: 7.0)
    context = create_execution_context(
        run_id="llm-global-deadline",
        thread_id=None,
        policy=RunPolicy(total_timeout_seconds=7),
    )

    async def complete() -> str:
        return "done"

    result = asyncio.run(
        execute_llm_operation(
            complete,
            agent="FakeAgent",
            operation_name="fake_operation",
            model="fake-model",
            input_text="input",
            local_timeout_seconds=180,
            max_retries=0,
            context=context,
        )
    )

    assert result.value == "done"
    assert observed_timeouts == [7.0]


def test_llm_timeout_preserves_operation_deadline_exception_contract() -> None:
    async def slow_operation() -> str:
        await asyncio.sleep(0.05)
        return "late"

    with pytest.raises(OperationDeadlineExceeded, match="Operation deadline exhausted"):
        asyncio.run(
            execute_llm_operation(
                slow_operation,
                agent="FakeAgent",
                operation_name="slow_operation",
                model="fake-model",
                input_text="input",
                local_timeout_seconds=0.01,
                max_retries=0,
                context=None,
            )
        )
