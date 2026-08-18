"""Fake-only checks for the P4.2 external-operation execution contract."""

import asyncio
import time

import pytest

from src.execution_policy import (
    OperationBudgetExhausted,
    OperationDeadlineExceeded,
    OperationExecutionError,
    OperationExecutionPolicy,
    execute_operation,
    is_retryable_error,
    retry_after_seconds,
)
from src.llm_execution import LLMOperationError
from src.runtime_control import RunPolicy, create_execution_context
from src.search.providers.errors import AuthError, RateLimitError


def test_operation_contract_uses_the_shorter_local_or_global_deadline() -> None:
    async def slow_operation() -> str:
        await asyncio.sleep(0.05)
        return "unreachable"

    context = create_execution_context(
        run_id="run-deadline",
        thread_id="thread-deadline",
        policy=RunPolicy(total_timeout_seconds=0.01),
    )

    with pytest.raises(OperationDeadlineExceeded) as exc_info:
        asyncio.run(
            execute_operation(
                slow_operation,
                policy=OperationExecutionPolicy(),
                local_deadline=time.perf_counter() + 1,
                context=context,
            )
        )

    assert exc_info.value.context is not None
    assert exc_info.value.context.operation_stop_reason == "deadline_exhausted"


def test_operation_contract_consumes_global_budget_across_calls() -> None:
    async def operation() -> str:
        return "ok"

    context = create_execution_context(
        run_id="run-budget",
        thread_id="thread-budget",
        policy=RunPolicy(max_operation_calls=1),
    )
    first = asyncio.run(
        execute_operation(
            operation,
            policy=OperationExecutionPolicy(),
            local_deadline=time.perf_counter() + 1,
            context=context,
        )
    )

    assert first.context is not None
    assert first.context.operation_calls == 1
    with pytest.raises(OperationBudgetExhausted) as exc_info:
        asyncio.run(
            execute_operation(
                operation,
                policy=OperationExecutionPolicy(),
                local_deadline=time.perf_counter() + 1,
                context=first.context,
            )
        )
    assert exc_info.value.context is not None
    assert exc_info.value.context.operation_stop_reason == "budget_exhausted"


def test_provider_retry_classification_controls_attempt_boundaries() -> None:
    auth_calls = 0

    async def auth_failure() -> str:
        nonlocal auth_calls
        auth_calls += 1
        raise AuthError("fake credentials", provider="fake")

    with pytest.raises(AuthError):
        asyncio.run(
            execute_operation(
                auth_failure,
                policy=OperationExecutionPolicy(max_retries=3),
                local_deadline=time.perf_counter() + 1,
                context=None,
            )
        )
    assert auth_calls == 1

    rate_limit_calls = 0

    async def rate_limit_once() -> str:
        nonlocal rate_limit_calls
        rate_limit_calls += 1
        if rate_limit_calls == 1:
            raise RateLimitError("fake limit", provider="fake")
        return "ok"

    result = asyncio.run(
        execute_operation(
            rate_limit_once,
            policy=OperationExecutionPolicy(max_retries=1),
            local_deadline=time.perf_counter() + 1,
            context=None,
        )
    )
    assert rate_limit_calls == 2
    assert [attempt.success for attempt in result.attempts] == [False, True]
    assert result.attempts[0].retryable is True


@pytest.mark.parametrize(
    "error",
    [
        OperationExecutionError("generic transient", retryable=True, retry_after_seconds=0.25),
        LLMOperationError("llm transient", retryable=True, retry_after_seconds=0.25),
        RateLimitError("provider transient", provider="fake", retry_after_seconds=0.25),
    ],
)
def test_typed_operation_and_provider_errors_share_retry_and_retry_after_contract(error: Exception) -> None:
    assert is_retryable_error(error, retry_unknown_errors=False) is True
    assert retry_after_seconds(error) == 0.25


def test_generic_operation_error_retry_after_is_observed_per_attempt() -> None:
    calls = 0

    async def transient_once() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OperationExecutionError("temporary", retryable=True, retry_after_seconds=0.25)
        return "ok"

    result = asyncio.run(
        execute_operation(
            transient_once,
            policy=OperationExecutionPolicy(max_retries=1, honor_retry_after=False),
            local_deadline=time.perf_counter() + 1,
            context=None,
        )
    )

    assert calls == 2
    assert result.attempts[0].retry_after_seconds == 0.25
