"""LLM adapter for the shared runtime operation-execution contract."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Optional, TypeVar

from src.exceptions import LLMError
from src.execution_policy import (
    ExecutionContextLike,
    OperationAttempt,
    OperationExecutionError,
    OperationExecutionPolicy,
    execute_operation,
)
from src.llm_tracker import estimate_tokens
from src.runtime_control import ExecutionContext


_Result = TypeVar("_Result")


class LLMOperationError(OperationExecutionError):
    """Normalized LLM transport or provider failure for retry decisions."""

    code = "llm_transport_error"
    default_retryable = True

    def __init__(
        self,
        message: str,
        *,
        code: str = "llm_transport_error",
        retryable: bool = True,
        retry_after_seconds: Optional[float] = None,
    ) -> None:
        self.code = code
        super().__init__(
            message,
            retryable=retryable,
            retry_after_seconds=retry_after_seconds,
        )


@dataclass(frozen=True, slots=True)
class LLMOperationResult:
    """One logical LLM operation with an observation per real attempt."""

    value: Any
    context: Optional[ExecutionContext]
    call_details: list[dict[str, Any]]


def _normalize_llm_error(error: Exception) -> LLMOperationError:
    if isinstance(error, LLMOperationError):
        return error
    if isinstance(error, LLMError):
        return LLMOperationError(
            str(error),
            code="llm_provider_error",
            retryable=error.is_retryable,
            retry_after_seconds=getattr(error, "retry_after_seconds", None),
        )
    # LangChain and provider SDKs do not yet share a stable exception hierarchy
    # in this repository. Preserve the historic retry behavior at this adapter
    # boundary, but make that decision explicit and observable.
    return LLMOperationError(str(error), retryable=True)


def _failure_context(error: Exception) -> Optional[ExecutionContext]:
    value = getattr(error, "context", None) or getattr(error, "execution_context", None)
    return value if isinstance(value, ExecutionContext) else None


async def execute_llm_operation(
    operation: Callable[[], Awaitable[_Result]],
    *,
    agent: str,
    operation_name: str,
    model: str,
    input_text: str,
    local_timeout_seconds: float,
    max_retries: int,
    context: ExecutionContextLike,
    output_text: Callable[[_Result], str] = str,
) -> LLMOperationResult:
    """Run an LLM operation once-or-retry with complete attempt accounting.

    ``max_retries`` follows ``OperationExecutionPolicy`` exactly: it excludes
    the first attempt. Failed attempts count as LLM calls and retain their
    input estimate, while only successful attempts contribute output tokens.
    """

    call_details: list[dict[str, Any]] = []
    latest_output = ""
    latest_error: Optional[LLMOperationError] = None
    input_tokens = estimate_tokens(input_text)

    async def invoke() -> _Result:
        nonlocal latest_output, latest_error
        latest_output = ""
        latest_error = None
        try:
            value = await operation()
            latest_output = output_text(value)
            return value
        except Exception as error:
            latest_error = _normalize_llm_error(error)
            raise latest_error from error

    def record_attempt(attempt: OperationAttempt) -> None:
        detail: dict[str, Any] = {
            "agent": agent,
            "operation": operation_name,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": estimate_tokens(latest_output) if attempt.success else 0,
            "duration": round(attempt.duration_seconds, 6),
            "attempt": attempt.attempt,
            "success": attempt.success,
        }
        if attempt.error:
            detail["error"] = attempt.error
        if latest_error is not None and not attempt.success:
            detail["error_code"] = latest_error.code
        if attempt.retryable:
            detail["retryable"] = True
        call_details.append(detail)

    try:
        execution = await execute_operation(
            invoke,
            policy=OperationExecutionPolicy(max_retries=max_retries),
            local_deadline=perf_counter() + local_timeout_seconds,
            context=context,
            on_attempt=record_attempt,
        )
    except Exception as error:
        # The caller owns the State patch. Attach observations and the most
        # recent persisted context without changing Graph routing semantics.
        setattr(error, "llm_call_details", call_details)
        if _failure_context(error) is None and context is not None:
            setattr(error, "execution_context", context)
        raise

    return LLMOperationResult(
        value=execution.value,
        context=execution.context,
        call_details=call_details,
    )
