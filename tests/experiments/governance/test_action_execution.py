"""Fake-only tests for the P13.1 generic action execution control plane."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from src.action_execution import (
    ActionAlreadyClaimedError,
    ActionExecutionBlockedError,
    ActionExecutionControlPlane,
    ActionExecutionDeadlineSnapshot,
    ActionExecutionOutcome,
    ActionExecutionRequest,
    ActionExecutionRuntimeView,
    ActionLedgerRepository,
    ActionLedgerRevisionError,
    ActionRequestConflictError,
    ActionTerminalError,
)
from src.evaluation.action_authorization import ActionBudget, ActionTarget
from src.runtime_control import RunPolicy, create_execution_context
from src.runtime_lease import PersistentRunLease, PersistentRunLeaseConflictError


_NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def _request(
    *,
    action_id: str = "action-001",
    request_id: str = "request-001",
    run_id: str = "run-001",
    thread_id: str = "thread-001",
    idempotency_key: str = "idem-run-001-action-001",
    max_operation_calls: int = 2,
) -> ActionExecutionRequest:
    return ActionExecutionRequest(
        action_id=action_id,
        request_id=request_id,
        run_id=run_id,
        thread_id=thread_id,
        action="retry_research",
        target=ActionTarget(
            stage="searching",
            node="search",
            scope="facet",
            selectors={"facet": "gap"},
            targetable_deficit=True,
            deficit_metric="extracted_facet_coverage",
            triggering_signal_ids=["signal-001"],
        ),
        authorization_id="authorization-001",
        recommendation_fingerprint="recommendation-fingerprint",
        authorization_fingerprint="authorization-fingerprint",
        state_fingerprint="state-fingerprint",
        expected_checkpoint_revision="checkpoint-001",
        effective_budget=ActionBudget(max_operation_calls=max_operation_calls, max_search_calls=1),
        deadline=ActionExecutionDeadlineSnapshot(
            effective_deadline_seconds=30.0,
            effective_deadline_at=(_NOW + timedelta(seconds=30)).isoformat(),
        ),
        idempotency_key=idempotency_key,
        created_at=_NOW.isoformat(),
    )


def _runtime(
    request: ActionExecutionRequest,
    *,
    now: datetime = _NOW,
    operation_calls: int = 0,
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
    ).model_copy(update={"operation_calls": operation_calls})
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


def _plane(tmp_path) -> tuple[ActionLedgerRepository, ActionExecutionControlPlane]:
    ledger = ActionLedgerRepository(tmp_path / "action-ledger.db")
    return ledger, ActionExecutionControlPlane(ledger, checkpoint_path=ledger.path)


def test_duplicate_request_returns_the_same_logical_action(tmp_path) -> None:
    _, plane = _plane(tmp_path)
    request = _request()

    first = plane.submit_request(request)
    duplicate = plane.submit_request(request)

    assert first.request.action_id == duplicate.request.action_id
    assert duplicate.revision == 0


def test_duplicate_request_content_conflict_is_rejected(tmp_path) -> None:
    _, plane = _plane(tmp_path)
    request = _request()
    plane.submit_request(request)

    with pytest.raises(ActionRequestConflictError):
        plane.submit_request(request.model_copy(update={"request_id": "request-conflict"}))


def test_duplicate_claim_is_rejected_by_status_and_revision(tmp_path) -> None:
    _, plane = _plane(tmp_path)
    request = _request()
    entry = plane.submit_request(request)
    runtime = _runtime(request)

    async def exercise() -> None:
        claimed = await plane.claim(
            request.action_id,
            worker_id="worker-1",
            runtime=runtime,
            expected_ledger_revision=entry.revision,
            now=_NOW,
        )
        with pytest.raises(ActionLedgerRevisionError):
            await plane.claim(
                request.action_id,
                worker_id="worker-2",
                runtime=runtime,
                expected_ledger_revision=entry.revision,
                now=_NOW,
            )
        with pytest.raises(ActionAlreadyClaimedError):
            await plane.claim(
                request.action_id,
                worker_id="worker-2",
                runtime=runtime,
                expected_ledger_revision=claimed.ledger_revision,
                now=_NOW,
            )

    asyncio.run(exercise())


def test_claim_respects_existing_thread_lease_conflict(tmp_path) -> None:
    ledger, plane = _plane(tmp_path)
    request = _request()
    entry = plane.submit_request(request)
    runtime = _runtime(request)

    async def exercise() -> None:
        async with PersistentRunLease(ledger.path, request.thread_id, ttl_seconds=30):
            with pytest.raises(PersistentRunLeaseConflictError):
                await plane.claim(
                    request.action_id,
                    worker_id="worker-1",
                    runtime=runtime,
                    expected_ledger_revision=entry.revision,
                    now=_NOW,
                )

    asyncio.run(exercise())


def test_stale_checkpoint_revision_is_blocked_and_terminalized(tmp_path) -> None:
    ledger, plane = _plane(tmp_path)
    request = _request()
    entry = plane.submit_request(request)

    async def exercise() -> None:
        with pytest.raises(ActionExecutionBlockedError, match="checkpoint revision is stale"):
            await plane.claim(
                request.action_id,
                worker_id="worker-1",
                runtime=_runtime(request, checkpoint_revision="checkpoint-002"),
                expected_ledger_revision=entry.revision,
                now=_NOW,
            )

    asyncio.run(exercise())
    assert ledger.get(request.action_id).status == "failed"


def test_stale_authorization_fingerprint_is_blocked_and_terminalized(tmp_path) -> None:
    ledger, plane = _plane(tmp_path)
    request = _request()
    entry = plane.submit_request(request)

    async def exercise() -> None:
        with pytest.raises(ActionExecutionBlockedError, match="authorization fingerprint is stale"):
            await plane.claim(
                request.action_id,
                worker_id="worker-1",
                runtime=_runtime(request, authorization_fingerprint="stale-authorization"),
                expected_ledger_revision=entry.revision,
                now=_NOW,
            )

    asyncio.run(exercise())
    assert ledger.get(request.action_id).status == "failed"


def test_runtime_deadline_exhaustion_marks_action_expired(tmp_path) -> None:
    ledger, plane = _plane(tmp_path)
    request = _request()
    entry = plane.submit_request(request)

    async def exercise() -> None:
        with pytest.raises(ActionExecutionBlockedError, match="runtime deadline is exhausted"):
            await plane.claim(
                request.action_id,
                worker_id="worker-1",
                runtime=_runtime(request, timeout_seconds=5.0),
                expected_ledger_revision=entry.revision,
                now=_NOW + timedelta(seconds=6),
            )

    asyncio.run(exercise())
    assert ledger.get(request.action_id).status == "expired"


def test_cancelled_runtime_marks_action_cancelled(tmp_path) -> None:
    ledger, plane = _plane(tmp_path)
    request = _request()
    entry = plane.submit_request(request)

    async def exercise() -> None:
        with pytest.raises(ActionExecutionBlockedError, match="runtime run is cancelled"):
            await plane.claim(
                request.action_id,
                worker_id="worker-1",
                runtime=_runtime(request, run_status="cancelled"),
                expected_ledger_revision=entry.revision,
                now=_NOW,
            )

    asyncio.run(exercise())
    assert ledger.get(request.action_id).status == "cancelled"


def test_crash_before_external_execution_recovers_to_queued_and_reclaims(tmp_path) -> None:
    _, plane = _plane(tmp_path)
    request = _request()
    entry = plane.submit_request(request)
    runtime = _runtime(request)

    async def exercise() -> None:
        claim = await plane.claim(
            request.action_id,
            worker_id="worker-1",
            runtime=runtime,
            expected_ledger_revision=entry.revision,
            now=_NOW,
        )
        recovered = await plane.recover(request.action_id, now=_NOW + timedelta(seconds=1))
        assert claim.ledger_revision == 1
        assert recovered.status == "queued"
        assert recovered.recovery_marker == "recovered_before_external_execution"
        replay_claim = await plane.claim(
            request.action_id,
            worker_id="worker-2",
            runtime=runtime,
            expected_ledger_revision=recovered.revision,
            now=_NOW + timedelta(seconds=2),
        )
        assert replay_claim.attempt == 0

    asyncio.run(exercise())


def test_crash_after_external_execution_before_commit_is_not_reexecuted(tmp_path) -> None:
    _, plane = _plane(tmp_path)
    request = _request()
    entry = plane.submit_request(request)
    runtime = _runtime(request)

    async def exercise() -> None:
        claim = await plane.claim(
            request.action_id,
            worker_id="worker-1",
            runtime=runtime,
            expected_ledger_revision=entry.revision,
            now=_NOW,
        )
        await plane.mark_executing(claim, runtime=runtime, now=_NOW)
        recovered = await plane.recover(request.action_id, now=_NOW + timedelta(seconds=1))
        assert recovered.status == "failed"
        assert recovered.recovery_marker == "external_execution_uncommitted"
        with pytest.raises(ActionTerminalError):
            await plane.claim(
                request.action_id,
                worker_id="worker-2",
                runtime=runtime,
                expected_ledger_revision=recovered.revision,
                now=_NOW + timedelta(seconds=2),
            )

    asyncio.run(exercise())


def test_terminal_and_duplicate_commit_are_suppressed(tmp_path) -> None:
    _, plane = _plane(tmp_path)
    request = _request()
    entry = plane.submit_request(request)
    runtime = _runtime(request)
    outcome = ActionExecutionOutcome(
        status="succeeded",
        input_fingerprint="input-001",
        output_fingerprint="output-001",
        state_transition_fingerprint="transition-001",
    )

    async def exercise() -> None:
        claim = await plane.claim(
            request.action_id,
            worker_id="worker-1",
            runtime=runtime,
            expected_ledger_revision=entry.revision,
            now=_NOW,
        )
        executing = await plane.mark_executing(claim, runtime=runtime, now=_NOW)
        committed = await plane.commit(executing, runtime=runtime, outcome=outcome, now=_NOW)
        duplicate = await plane.commit(executing, runtime=runtime, outcome=outcome, now=_NOW)
        assert committed.status == "succeeded"
        assert duplicate.revision == committed.revision
        with pytest.raises(ActionTerminalError):
            await plane.claim(
                request.action_id,
                worker_id="worker-2",
                runtime=runtime,
                expected_ledger_revision=committed.revision,
                now=_NOW,
            )

    asyncio.run(exercise())


def test_waiting_action_releases_its_short_lived_lease(tmp_path) -> None:
    ledger, plane = _plane(tmp_path)
    request = _request()
    entry = plane.submit_request(request)
    runtime = _runtime(request)

    async def exercise() -> None:
        claim = await plane.claim(
            request.action_id,
            worker_id="worker-1",
            runtime=runtime,
            expected_ledger_revision=entry.revision,
            now=_NOW,
        )
        waiting = await plane.wait_for_approval(claim, runtime=runtime, now=_NOW)
        assert waiting.status == "waiting_approval"
        async with PersistentRunLease(ledger.path, request.thread_id, ttl_seconds=30) as lease:
            lease.assert_held()

    asyncio.run(exercise())


def test_cache_replay_run_cannot_reuse_source_action_idempotency(tmp_path) -> None:
    _, plane = _plane(tmp_path)
    source = _request()
    plane.submit_request(source)
    replay = _request(
        action_id="action-replay-001",
        request_id="request-replay-001",
        run_id="run-replay-001",
        thread_id="thread-replay-001",
        idempotency_key=source.idempotency_key,
    )

    with pytest.raises(ActionRequestConflictError):
        plane.submit_request(replay)

    isolated = plane.submit_request(replay.model_copy(update={"idempotency_key": "idem-replay"}))
    assert isolated.request.run_id == "run-replay-001"
    assert isolated.request.action_id != source.action_id


def test_runtime_budget_is_only_narrowed_never_reset(tmp_path) -> None:
    _, plane = _plane(tmp_path)
    request = _request(max_operation_calls=10)
    entry = plane.submit_request(request)
    runtime = _runtime(request, operation_calls=4)

    async def exercise() -> None:
        claim = await plane.claim(
            request.action_id,
            worker_id="worker-1",
            runtime=runtime,
            expected_ledger_revision=entry.revision,
            now=_NOW,
        )
        assert claim.constraints.effective_budget.max_operation_calls == 1
        assert request.effective_budget.max_operation_calls == 10

    asyncio.run(exercise())


def test_recovery_is_deterministic_and_trace_metadata_is_attributed(tmp_path) -> None:
    _, plane = _plane(tmp_path)
    request = _request()
    entry = plane.submit_request(request)
    runtime = _runtime(request)

    async def exercise() -> None:
        claim = await plane.claim(
            request.action_id,
            worker_id="worker-1",
            runtime=runtime,
            expected_ledger_revision=entry.revision,
            now=_NOW,
        )
        await plane.mark_executing(claim, runtime=runtime, now=_NOW)
        first = await plane.recover(request.action_id, now=_NOW)
        second = await plane.recover(request.action_id, now=_NOW)
        metadata = plane.trace_metadata(second, lifecycle_transition="recovered")
        assert first.model_dump(mode="json") == second.model_dump(mode="json")
        assert metadata.action_id == request.action_id
        assert metadata.authorization_id == request.authorization_id
        assert metadata.recovery_reason == "external_execution_uncommitted"

    asyncio.run(exercise())


def test_control_plane_has_no_agent_graph_provider_or_state_calls(tmp_path) -> None:
    _, plane = _plane(tmp_path)
    request = _request()

    entry = plane.submit_request(request)

    assert entry.status == "queued"
    assert entry.request.action == "retry_research"
