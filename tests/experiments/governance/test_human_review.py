"""Fake-only P13.2 checkpointed human-review tests."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest

from src.action_execution import (
    ActionExecutionBlockedError,
    ActionExecutionControlPlane,
    ActionExecutionDeadlineSnapshot,
    ActionExecutionRequest,
    ActionExecutionRuntimeView,
    ActionLedgerRepository,
    ActionTerminalError,
)
from src.evaluation.action_authorization import (
    ActionBudget,
    ActionEligibilityDecision,
    ActionProvenance,
    ActionTarget,
)
from src.human_review import (
    ApprovalAuthorizationError,
    ApprovalConflictError,
    ApprovalPayload,
    ApprovalReceiptRepository,
    ApprovalRequest,
    ApprovalResumeError,
    CheckpointResumeRequest,
    CheckpointResumeResult,
    CheckpointedHumanReviewHandler,
)
from src.runtime_control import RunPolicy, create_execution_context
from src.runtime_lease import PersistentRunLease, PersistentRunLeaseConflictError, PersistentRunLeaseLostError


_NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


class _FakeResumeAdapter:
    def __init__(self) -> None:
        self.requests: list[CheckpointResumeRequest] = []

    async def resume(self, request: CheckpointResumeRequest) -> CheckpointResumeResult:
        self.requests.append(request)
        return CheckpointResumeResult(
            output_fingerprint=f"resume-output:{request.action_id}",
            state_transition_fingerprint=f"resume-transition:{request.action_id}",
        )


def _action_request(*, scope: str = "approval") -> ActionExecutionRequest:
    return ActionExecutionRequest(
        action_id="human-action-001",
        request_id="human-request-001",
        run_id="human-run-001",
        thread_id="human-thread-001",
        action="human_review",
        target=ActionTarget(
            stage="synthesizing",
            node="synthesize",
            scope=scope,
            selectors={"review": "quality-conflict"},
            targetable_deficit=False,
            deficit_metric="citation_integrity",
            triggering_signal_ids=["signal-review"],
        ),
        authorization_id="human-auth-001",
        recommendation_fingerprint="human-recommendation-fingerprint",
        authorization_fingerprint="human-authorization-fingerprint",
        state_fingerprint="human-state-fingerprint",
        expected_checkpoint_revision="human-checkpoint-001",
        effective_budget=ActionBudget(max_operation_calls=1),
        deadline=ActionExecutionDeadlineSnapshot(
            effective_deadline_seconds=30.0,
            effective_deadline_at=(_NOW + timedelta(seconds=30)).isoformat(),
        ),
        idempotency_key="human-idempotency-001",
        created_at=_NOW.isoformat(),
    )


def _runtime(
    request: ActionExecutionRequest,
    *,
    now: datetime = _NOW,
    timeout_seconds: float = 60.0,
    run_status: str = "running",
    authorization_fingerprint: str | None = None,
    checkpoint_revision: str | None = None,
) -> ActionExecutionRuntimeView:
    context = create_execution_context(
        run_id=request.run_id,
        thread_id=request.thread_id,
        policy=RunPolicy(total_timeout_seconds=timeout_seconds, max_operation_calls=5),
        now=_NOW,
    )
    return ActionExecutionRuntimeView(
        run_id=request.run_id,
        thread_id=request.thread_id,
        checkpoint_revision=checkpoint_revision or request.expected_checkpoint_revision,
        state_fingerprint=request.state_fingerprint,
        authorization_id=request.authorization_id,
        authorization_fingerprint=authorization_fingerprint or request.authorization_fingerprint,
        recommendation_fingerprint=request.recommendation_fingerprint,
        run_status=run_status,
        terminal_reason="cancelled" if run_status == "cancelled" else None,
        execution_context=context,
    )


def _approval_request(request: ActionExecutionRequest) -> ApprovalRequest:
    return ApprovalRequest(
        action_id=request.action_id,
        request_id=request.request_id,
        authorization_id=request.authorization_id,
        run_id=request.run_id,
        thread_id=request.thread_id,
        checkpoint_revision=request.expected_checkpoint_revision,
        state_fingerprint=request.state_fingerprint,
        allowed_approver_roles=["research_reviewer"],
        created_at=_NOW.isoformat(),
    )


def _payload(request: ActionExecutionRequest, *, decision: str = "approve", role: str = "research_reviewer") -> ApprovalPayload:
    return ApprovalPayload(
        action_id=request.action_id,
        authorization_id=request.authorization_id,
        approver_identity="reviewer-001",
        approver_role=role,
        decision=decision,
        comment="fake review",
        reference="ticket-001",
        submitted_at=_NOW.isoformat(),
        idempotency_key=f"approval-idempotency-{decision}",
    )


def _eligibility(request: ActionExecutionRequest) -> ActionEligibilityDecision:
    return ActionEligibilityDecision(
        status="eligible",
        action="human_review",
        reason="fake explicit authorization",
        provenance=ActionProvenance(
            authorization_id=request.authorization_id,
            action_id=request.action_id,
            grantor="fake-test",
            recommendation_fingerprint=request.recommendation_fingerprint,
            source_signal_ids=["signal-review"],
        ),
    )


def _handler(tmp_path):
    ledger = ActionLedgerRepository(tmp_path / "actions.db")
    control = ActionExecutionControlPlane(ledger, checkpoint_path=ledger.path)
    adapter = _FakeResumeAdapter()
    approvals = ApprovalReceiptRepository(ledger.path)
    return ledger, control, adapter, CheckpointedHumanReviewHandler(control, approvals, adapter)


async def _enter_waiting(handler, request, runtime) -> None:
    await handler.enter_waiting(
        _approval_request(request),
        _eligibility(request),
        runtime=runtime,
        worker_id="wait-worker",
        now=_NOW,
    )


def test_approve_resumes_existing_checkpoint_once(tmp_path) -> None:
    ledger, _, adapter, handler = _handler(tmp_path)
    request = _action_request()
    runtime = _runtime(request)
    ledger.submit(request)

    async def exercise() -> None:
        await _enter_waiting(handler, request, runtime)
        receipt = await handler.submit_approval(
            _approval_request(request), _payload(request), runtime=runtime, now=_NOW
        )
        assert receipt.resume_status == "pending"
        assert adapter.requests == []
        resumed = await handler.resume_approved(
            request.action_id, runtime=runtime, worker_id="resume-worker", now=_NOW
        )
        assert resumed.resume_status == "resumed"

    asyncio.run(exercise())
    assert len(adapter.requests) == 1
    assert adapter.requests[0].checkpoint_revision == request.expected_checkpoint_revision
    assert ledger.get(request.action_id).status == "succeeded"


def test_reject_does_not_resume_research_flow(tmp_path) -> None:
    ledger, _, adapter, handler = _handler(tmp_path)
    request = _action_request()
    runtime = _runtime(request)
    ledger.submit(request)

    async def exercise() -> None:
        await _enter_waiting(handler, request, runtime)
        receipt = await handler.submit_approval(
            _approval_request(request), _payload(request, decision="reject"), runtime=runtime, now=_NOW
        )
        assert receipt.resume_status == "suppressed"
        with pytest.raises(ApprovalResumeError):
            await handler.resume_approved(
                request.action_id, runtime=runtime, worker_id="resume-worker", now=_NOW
            )

    asyncio.run(exercise())
    assert adapter.requests == []
    assert ledger.get(request.action_id).status == "rejected"


def test_identical_duplicate_approval_returns_same_receipt(tmp_path) -> None:
    ledger, _, adapter, handler = _handler(tmp_path)
    request = _action_request()
    runtime = _runtime(request)
    ledger.submit(request)

    async def exercise() -> None:
        await _enter_waiting(handler, request, runtime)
        payload = _payload(request)
        first = await handler.submit_approval(_approval_request(request), payload, runtime=runtime, now=_NOW)
        duplicate = await handler.submit_approval(_approval_request(request), payload, runtime=runtime, now=_NOW)
        assert duplicate.model_dump(mode="json") == first.model_dump(mode="json")

    asyncio.run(exercise())
    assert adapter.requests == []


def test_conflicting_approval_is_rejected_after_first_wins(tmp_path) -> None:
    ledger, _, _, handler = _handler(tmp_path)
    request = _action_request()
    runtime = _runtime(request)
    ledger.submit(request)

    async def exercise() -> None:
        await _enter_waiting(handler, request, runtime)
        await handler.submit_approval(_approval_request(request), _payload(request), runtime=runtime, now=_NOW)
        with pytest.raises(ApprovalConflictError):
            await handler.submit_approval(
                _approval_request(request),
                _payload(request, decision="reject"),
                runtime=runtime,
                now=_NOW,
            )

    asyncio.run(exercise())


def test_stale_authorization_and_checkpoint_are_blocked(tmp_path) -> None:
    ledger, _, _, handler = _handler(tmp_path)
    request = _action_request()
    ledger.submit(request)

    async def exercise() -> None:
        await _enter_waiting(handler, request, _runtime(request))
        with pytest.raises(ActionExecutionBlockedError, match="authorization fingerprint is stale"):
            await handler.submit_approval(
                _approval_request(request),
                _payload(request),
                runtime=_runtime(request, authorization_fingerprint="stale-auth"),
                now=_NOW,
            )

    asyncio.run(exercise())
    assert ledger.get(request.action_id).status == "failed"

    checkpoint_request = _action_request()
    ledger2, _, _, handler2 = _handler(tmp_path / "checkpoint")
    ledger2.submit(checkpoint_request)

    async def checkpoint_exercise() -> None:
        await _enter_waiting(handler2, checkpoint_request, _runtime(checkpoint_request))
        with pytest.raises(ApprovalAuthorizationError, match="checkpoint or state binding is stale"):
            await handler2.submit_approval(
                _approval_request(checkpoint_request),
                _payload(checkpoint_request),
                runtime=_runtime(checkpoint_request, checkpoint_revision="checkpoint-stale"),
                now=_NOW,
            )

    asyncio.run(checkpoint_exercise())


def test_approval_after_timeout_or_cancel_is_terminally_rejected(tmp_path) -> None:
    ledger, _, _, handler = _handler(tmp_path)
    request = _action_request()
    ledger.submit(request)

    async def timeout_exercise() -> None:
        await _enter_waiting(handler, request, _runtime(request))
        with pytest.raises(ActionExecutionBlockedError, match="runtime deadline is exhausted"):
            await handler.submit_approval(
                _approval_request(request),
                _payload(request),
                runtime=_runtime(request, timeout_seconds=5.0),
                now=_NOW + timedelta(seconds=6),
            )

    asyncio.run(timeout_exercise())
    assert ledger.get(request.action_id).status == "expired"

    cancelled_request = _action_request().model_copy(
        update={
            "action_id": "human-action-cancelled",
            "request_id": "human-request-cancelled",
            "idempotency_key": "human-idempotency-cancelled",
        }
    )
    ledger2, _, _, handler2 = _handler(tmp_path / "cancelled")
    ledger2.submit(cancelled_request)

    async def cancel_exercise() -> None:
        await _enter_waiting(handler2, cancelled_request, _runtime(cancelled_request))
        with pytest.raises(ActionExecutionBlockedError, match="runtime run is cancelled"):
            await handler2.submit_approval(
                _approval_request(cancelled_request),
                _payload(cancelled_request),
                runtime=_runtime(cancelled_request, run_status="cancelled"),
                now=_NOW,
            )

    asyncio.run(cancel_exercise())
    assert ledger2.get(cancelled_request.action_id).status == "cancelled"


def test_crash_after_waiting_and_after_approval_before_resume_are_recoverable(tmp_path) -> None:
    ledger, _, adapter, handler = _handler(tmp_path)
    request = _action_request()
    runtime = _runtime(request)
    ledger.submit(request)

    async def exercise() -> None:
        await _enter_waiting(handler, request, runtime)
        assert handler.recover(request.action_id) is None
        assert adapter.requests == []
        receipt = await handler.submit_approval(
            _approval_request(request), _payload(request), runtime=runtime, now=_NOW
        )
        recovered = handler.recover(request.action_id)
        assert recovered.resume_status == "pending"
        assert receipt.resume_status == "pending"
        assert adapter.requests == []
        await handler.resume_approved(
            request.action_id, runtime=runtime, worker_id="recovery-worker", now=_NOW
        )

    asyncio.run(exercise())
    assert len(adapter.requests) == 1


def test_duplicate_resume_and_terminal_action_reopen_are_suppressed(tmp_path) -> None:
    ledger, _, adapter, handler = _handler(tmp_path)
    request = _action_request()
    runtime = _runtime(request)
    ledger.submit(request)

    async def exercise() -> None:
        await _enter_waiting(handler, request, runtime)
        await handler.submit_approval(_approval_request(request), _payload(request), runtime=runtime, now=_NOW)
        first = await handler.resume_approved(
            request.action_id, runtime=runtime, worker_id="resume-worker", now=_NOW
        )
        duplicate = await handler.resume_approved(
            request.action_id, runtime=runtime, worker_id="resume-worker", now=_NOW
        )
        assert first.model_dump(mode="json") == duplicate.model_dump(mode="json")
        with pytest.raises(ActionTerminalError):
            await handler.enter_waiting(
                _approval_request(request),
                _eligibility(request),
                runtime=runtime,
                worker_id="new-wait-worker",
                now=_NOW,
            )

    asyncio.run(exercise())
    assert len(adapter.requests) == 1


def test_unauthorized_approver_wrong_scope_and_lease_loss_do_not_approve(tmp_path, monkeypatch) -> None:
    ledger, control, adapter, handler = _handler(tmp_path)
    request = _action_request()
    runtime = _runtime(request)
    ledger.submit(request)

    async def exercise() -> None:
        await _enter_waiting(handler, request, runtime)
        with pytest.raises(ApprovalAuthorizationError, match="approver role"):
            await handler.submit_approval(
                _approval_request(request),
                _payload(request, role="viewer"),
                runtime=runtime,
                now=_NOW,
            )

        class _LostLease:
            owner_id = "lost"

            def assert_held(self) -> None:
                raise PersistentRunLeaseLostError("lost")

        @asynccontextmanager
        async def lost_lease(_runtime):
            yield _LostLease()

        monkeypatch.setattr(control, "lease_for_runtime", lost_lease)
        with pytest.raises(PersistentRunLeaseLostError):
            await handler.submit_approval(
                _approval_request(request), _payload(request), runtime=runtime, now=_NOW
            )

    asyncio.run(exercise())
    assert handler.approvals.get(request.action_id) is None
    assert adapter.requests == []

    wrong_scope = _action_request(scope="run").model_copy(
        update={
            "action_id": "human-action-wrong-scope",
            "request_id": "human-request-wrong-scope",
            "idempotency_key": "human-idempotency-wrong-scope",
        }
    )
    ledger2, _, _, handler2 = _handler(tmp_path / "wrong-scope")
    ledger2.submit(wrong_scope)

    async def wrong_scope_exercise() -> None:
        with pytest.raises(ApprovalAuthorizationError, match="target scope"):
            await _enter_waiting(handler2, wrong_scope, _runtime(wrong_scope))

    asyncio.run(wrong_scope_exercise())


def test_lease_conflict_and_no_external_calls_before_explicit_resume(tmp_path) -> None:
    ledger, control, adapter, handler = _handler(tmp_path)
    request = _action_request()
    runtime = _runtime(request)
    ledger.submit(request)

    async def exercise() -> None:
        async with PersistentRunLease(ledger.path, request.thread_id, ttl_seconds=30):
            with pytest.raises(PersistentRunLeaseConflictError):
                await handler.enter_waiting(
                    _approval_request(request),
                    _eligibility(request),
                    runtime=runtime,
                    worker_id="wait-worker",
                    now=_NOW,
                )
        await _enter_waiting(handler, request, runtime)
        await handler.submit_approval(_approval_request(request), _payload(request), runtime=runtime, now=_NOW)
        assert adapter.requests == []
        assert control.ledger.get(request.action_id).status == "waiting_approval"

    asyncio.run(exercise())
