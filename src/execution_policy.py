"""Shared bounded execution contract for runtime-owned external operations."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Generic, Optional, TypeVar

from pydantic import BaseModel, Field

from src.runtime_control import ExecutionContext
from src.search.providers.errors import SearchProviderError


_Result = TypeVar("_Result")


class OperationExecutionPolicy(BaseModel):
    """Local limits supplied by a service for one operation kind.

    ``max_retries`` always means retries *after* the first attempt, so the
    upper bound for an operation is ``max_retries + 1`` attempts. The runtime
    may lower that bound through its global operation budget or deadline.
    """

    max_retries: int = Field(default=0, ge=0)
    retry_unknown_errors: bool = False
    honor_retry_after: bool = True


class OperationExecutionError(RuntimeError):
    """Typed failure signal understood by the shared execution contract.

    Providers and LLM adapters may expose this small, transport-neutral
    surface.  Selecting another provider or opening a circuit remains outside
    this contract.
    """

    code = "operation_error"
    default_retryable = False

    def __init__(
        self,
        message: str,
        *,
        retryable: Optional[bool] = None,
        retry_after_seconds: Optional[float] = None,
    ) -> None:
        self.retryable = self.default_retryable if retryable is None else retryable
        self.retry_after_seconds = retry_after_seconds
        super().__init__(message)


class OperationBudgetExhausted(RuntimeError):
    """The RunPolicy global external-operation budget has been consumed."""

    def __init__(self, context: Optional[ExecutionContext]) -> None:
        self.context = context
        super().__init__("Run operation budget exhausted")


class OperationDeadlineExceeded(asyncio.TimeoutError):
    """The effective local/global deadline expired before an attempt finished."""

    def __init__(self, context: Optional[ExecutionContext]) -> None:
        self.context = context
        super().__init__("Operation deadline exhausted")


@dataclass(frozen=True, slots=True)
class OperationAttempt:
    attempt: int
    success: bool
    duration_seconds: float
    error: Optional[str] = None
    retryable: bool = False
    retry_after_seconds: Optional[float] = None


@dataclass(frozen=True, slots=True)
class OperationExecutionResult(Generic[_Result]):
    value: _Result
    context: Optional[ExecutionContext]
    attempts: list[OperationAttempt]


def is_retryable_error(error: BaseException, *, retry_unknown_errors: bool) -> bool:
    """Use a normalized typed signal; unknown errors need explicit opt-in."""

    if isinstance(error, asyncio.TimeoutError):
        return True
    if isinstance(error, (SearchProviderError, OperationExecutionError)):
        return error.retryable
    return retry_unknown_errors


def retry_after_seconds(error: BaseException) -> Optional[float]:
    """Read the normalized typed Retry-After signal without provider policy."""

    if isinstance(error, (SearchProviderError, OperationExecutionError)):
        value = error.retry_after_seconds
        return value if value is not None and value >= 0 else None
    return None


def _effective_timeout(
    *,
    local_deadline: float,
    context: Optional[ExecutionContext],
) -> float:
    local_remaining = local_deadline - perf_counter()
    if local_remaining <= 0:
        raise OperationDeadlineExceeded(context.stopped("deadline_exhausted") if context else None)

    if context is None:
        return local_remaining
    global_remaining = context.remaining_timeout_seconds()
    if global_remaining is None:
        return local_remaining
    if global_remaining <= 0:
        raise OperationDeadlineExceeded(context.stopped("deadline_exhausted"))
    return min(local_remaining, global_remaining)


def _consume_operation_budget(context: Optional[ExecutionContext]) -> Optional[ExecutionContext]:
    if context is None:
        return None
    remaining = context.remaining_operation_calls()
    if remaining is not None and remaining <= 0:
        raise OperationBudgetExhausted(context.stopped("budget_exhausted"))
    return context.after_operation_call()


async def execute_operation(
    operation: Callable[[], Awaitable[_Result]],
    *,
    policy: OperationExecutionPolicy,
    local_deadline: float,
    context: Optional[ExecutionContext],
    on_attempt: Optional[Callable[[OperationAttempt], None]] = None,
) -> OperationExecutionResult[_Result]:
    """Execute one operation under the intersection of local and run policy.

    Services own ``local_deadline`` and retry count. Runtime owns the global
    deadline/budget embedded in ``context``. A provider merely classifies its
    error and exposes retry signals; it cannot choose fallback behavior here.
    """

    current_context = context
    attempts: list[OperationAttempt] = []
    last_error: BaseException | None = None

    for attempt in range(1, policy.max_retries + 2):
        timeout = _effective_timeout(local_deadline=local_deadline, context=current_context)
        current_context = _consume_operation_budget(current_context)
        started = perf_counter()
        try:
            value = await asyncio.wait_for(operation(), timeout=timeout)
            record = OperationAttempt(
                attempt=attempt, success=True, duration_seconds=perf_counter() - started
            )
            attempts.append(record)
            if on_attempt is not None:
                on_attempt(record)
            return OperationExecutionResult(value=value, context=current_context, attempts=attempts)
        except asyncio.TimeoutError as error:
            stopped_context = current_context.stopped("deadline_exhausted") if current_context else None
            record = OperationAttempt(
                attempt=attempt,
                success=False,
                duration_seconds=perf_counter() - started,
                error="timeout",
                retryable=True,
            )
            attempts.append(record)
            if on_attempt is not None:
                on_attempt(record)
            # A timeout consumes the effective deadline. Retrying cannot make
            # progress without a fresh deadline, which runtime deliberately
            # does not create.
            raise OperationDeadlineExceeded(stopped_context) from error
        except Exception as error:
            last_error = error
            retryable = is_retryable_error(
                error, retry_unknown_errors=policy.retry_unknown_errors
            )
            retry_after = retry_after_seconds(error)
            record = OperationAttempt(
                attempt=attempt,
                success=False,
                duration_seconds=perf_counter() - started,
                error=str(error),
                retryable=retryable,
                retry_after_seconds=retry_after,
            )
            attempts.append(record)
            if on_attempt is not None:
                on_attempt(record)
            if not retryable or attempt > policy.max_retries:
                # Failure callers need the advanced context to persist a
                # budget/deadline-consistent terminal patch and trace.
                try:
                    setattr(error, "execution_context", current_context)
                except (AttributeError, TypeError):
                    pass
                raise
            if policy.honor_retry_after and retry_after:
                remaining = _effective_timeout(
                    local_deadline=local_deadline, context=current_context
                )
                if retry_after >= remaining:
                    try:
                        setattr(error, "execution_context", current_context)
                    except (AttributeError, TypeError):
                        pass
                    raise
                await asyncio.sleep(retry_after)

    if last_error is not None:
        raise last_error
    raise RuntimeError("Operation failed without an exception")
