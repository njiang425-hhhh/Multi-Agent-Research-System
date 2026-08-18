"""Runtime-owned execution contracts for one research run.

These contracts deliberately describe control-plane concerns only.  They do
not contain business inputs, Graph routing decisions, Agent output, or
provider-specific policies.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal, Optional, TypeVar

from pydantic import BaseModel, Field


CancellationMode = Literal["cooperative"]
OperationStopReason = Literal["deadline_exhausted", "budget_exhausted"]
TerminalReason = Literal[
    "completed",
    "cache_replay",
    "router_terminated",
    "agent_failed",
    "unhandled_exception",
    "timeout",
    "budget_exhausted",
    "cancelled",
]

_Result = TypeVar("_Result")


class RunPolicy(BaseModel):
    """Stable, runtime-owned policy captured when a run starts.

    P4.2 extends the policy with an optional global external-operation budget.
    The persistent lease cadence is runner infrastructure, not a user-facing
    business policy. Per-operation budgets, retry classification, and provider
    policy remain out of scope until their owning services are unified.
    """

    total_timeout_seconds: Optional[float] = Field(default=None, gt=0)
    max_operation_calls: Optional[int] = Field(default=None, ge=0)
    cancellation_mode: CancellationMode = "cooperative"


class ExecutionContext(BaseModel):
    """Serializable control-plane context persisted with a run checkpoint.

    ``deadline_at`` is absolute so a resumed persistent run cannot restart a
    previously consumed run-level timeout.  A missing context on an old
    checkpoint is adopted by the runtime only; business fields are untouched.
    """

    run_id: str
    thread_id: Optional[str] = None
    policy: RunPolicy = Field(default_factory=RunPolicy)
    started_at: str
    deadline_at: Optional[str] = None
    delivery_semantics: Literal["at_least_once"] = "at_least_once"
    operation_calls: int = Field(default=0, ge=0)
    operation_stop_reason: Optional[OperationStopReason] = None

    def remaining_timeout_seconds(self, *, now: Optional[datetime] = None) -> Optional[float]:
        """Return the persisted run deadline remaining, when one exists."""

        if not self.deadline_at:
            return None
        deadline = datetime.fromisoformat(self.deadline_at)
        current = now or datetime.now(timezone.utc)
        return max(0.0, (deadline - current).total_seconds())

    def remaining_operation_calls(self) -> Optional[int]:
        """Return the runtime-owned external-operation budget remaining."""

        if self.policy.max_operation_calls is None:
            return None
        return max(0, self.policy.max_operation_calls - self.operation_calls)

    def after_operation_call(self) -> "ExecutionContext":
        """Return a new context after one external operation attempt."""

        return self.model_copy(
            update={"operation_calls": self.operation_calls + 1, "operation_stop_reason": None}
        )

    def stopped(self, reason: OperationStopReason) -> "ExecutionContext":
        """Return a new context carrying a runtime stop reason."""

        return self.model_copy(update={"operation_stop_reason": reason})


def create_execution_context(
    *,
    run_id: str,
    thread_id: Optional[str],
    policy: Optional[RunPolicy] = None,
    now: Optional[datetime] = None,
) -> ExecutionContext:
    """Create a new persisted context without deriving any business state."""

    resolved_policy = policy or RunPolicy()
    current = now or datetime.now(timezone.utc)
    deadline = (
        current + timedelta(seconds=resolved_policy.total_timeout_seconds)
        if resolved_policy.total_timeout_seconds is not None
        else None
    )
    return ExecutionContext(
        run_id=run_id,
        thread_id=thread_id,
        policy=resolved_policy,
        started_at=current.isoformat(),
        deadline_at=deadline.isoformat() if deadline else None,
    )


async def invoke_with_execution_context(
    operation: Callable[[], Awaitable[_Result]],
    context: Optional[ExecutionContext],
) -> _Result:
    """Execute one runner operation under its persisted run deadline.

    Cancellation remains cooperative: cancelling the outer asyncio task is
    propagated to the caller, while the runner persists a ``cancelled``
    lifecycle patch at its boundary. This helper does not invent a polling
    channel inside Agents or alter Graph routing.
    """

    if context is None:
        return await operation()
    remaining = context.remaining_timeout_seconds()
    if remaining is None:
        return await operation()
    if remaining <= 0:
        raise asyncio.TimeoutError("Run deadline has already expired")
    return await asyncio.wait_for(operation(), timeout=remaining)
