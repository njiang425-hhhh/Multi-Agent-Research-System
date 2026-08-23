"""Checkpointed human-review handler built on the P13.1 control plane.

The handler records an explicit approval decision and can ask an injected
runner-boundary adapter to resume an already queued checkpoint.  It never
creates a Graph route, changes ResearchState business fields, or decides an
approval automatically.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from src.action_execution import (
    ActionExecutionBlockedError,
    ActionExecutionClaim,
    ActionExecutionControlPlane,
    ActionExecutionOutcome,
    ActionExecutionRequest,
    ActionExecutionRuntimeView,
    ActionLedgerEntry,
    ActionTerminalError,
)
from src.evaluation.action_authorization import ActionEligibilityDecision


HUMAN_REVIEW_POLICY_VERSION = "p13.2.human_review.v1"

ApprovalDecision = Literal["approve", "reject"]
ApprovalResumeStatus = Literal["pending", "executing", "resumed", "suppressed", "unknown"]


class ApprovalError(RuntimeError):
    """Base error for durable human-review approval handling."""


class ApprovalConflictError(ApprovalError):
    """A different approval attempted to replace the first terminal decision."""


class ApprovalAuthorizationError(ApprovalError):
    """The approver or policy scope does not authorize this decision."""


class ApprovalResumeError(ApprovalError):
    """The receipt cannot safely cause another logical checkpoint resume."""


class ApprovalRequest(BaseModel):
    """Explicit approval workflow binding for one waiting human-review action."""

    action_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    authorization_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    checkpoint_revision: str = Field(min_length=1)
    state_fingerprint: str = Field(min_length=1)
    allowed_approver_roles: list[str] = Field(min_length=1)
    created_at: str = Field(min_length=1)
    policy_version: str = HUMAN_REVIEW_POLICY_VERSION


class ApprovalPayload(BaseModel):
    """One human decision; no policy in this module manufactures a payload."""

    action_id: str = Field(min_length=1)
    authorization_id: str = Field(min_length=1)
    approver_identity: str = Field(min_length=1)
    approver_role: str = Field(min_length=1)
    decision: ApprovalDecision
    comment: str | None = None
    reference: str | None = None
    submitted_at: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    policy_version: str = HUMAN_REVIEW_POLICY_VERSION


class ApprovalReceipt(BaseModel):
    """Durable first-wins approval receipt and independent resume progress."""

    approval_id: str = Field(min_length=1)
    action_id: str
    request_id: str
    authorization_id: str
    run_id: str
    thread_id: str
    checkpoint_revision: str
    state_fingerprint: str
    approver_identity: str
    approver_role: str
    decision: ApprovalDecision
    payload_fingerprint: str
    idempotency_key: str
    policy_version: str
    ledger_attempt: int = Field(ge=0)
    ledger_revision: int = Field(ge=0)
    resume_status: ApprovalResumeStatus
    resume_attempt: int = Field(default=0, ge=0)
    resume_revision: int = Field(default=0, ge=0)
    resume_error: str | None = None
    created_at: str
    updated_at: str


class CheckpointResumeRequest(BaseModel):
    """Runner-boundary input for resuming one already queued Graph checkpoint."""

    action_id: str
    request_id: str
    authorization_id: str
    run_id: str
    thread_id: str
    checkpoint_revision: str
    ledger_attempt: int
    resume_attempt: int


class CheckpointResumeResult(BaseModel):
    """Attribution-only result returned by a runner-boundary resume adapter."""

    output_fingerprint: str | None = None
    state_transition_fingerprint: str = Field(min_length=1)


class CheckpointResumeAdapter(Protocol):
    """A runner-boundary adapter; implementations own Graph invocation details."""

    async def resume(self, request: CheckpointResumeRequest) -> CheckpointResumeResult:
        """Resume only the queued checkpoint using the Runtime lease/CAS guard."""


def approval_content_fingerprint(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _now_iso(now: datetime | None) -> str:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.isoformat()


class ApprovalReceiptRepository:
    """First-wins approval store colocated with the P13.1 action ledger DB."""

    _TABLE = "human_review_approval_receipts"

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._initialize()

    def submit(
        self,
        approval_request: ApprovalRequest,
        payload: ApprovalPayload,
        entry: ActionLedgerEntry,
        *,
        now: datetime | None = None,
    ) -> ApprovalReceipt:
        payload_fingerprint = approval_content_fingerprint(payload)
        with self._transaction() as connection:
            existing = self._get(connection, payload.action_id)
            if existing is None:
                existing = self._get_by_idempotency(connection, payload.idempotency_key)
            if existing is not None:
                if existing.payload_fingerprint == payload_fingerprint:
                    return existing
                raise ApprovalConflictError("first terminal approval already exists with different payload")
            timestamp = _now_iso(now)
            receipt = ApprovalReceipt(
                approval_id=f"approval:{payload.action_id}",
                action_id=payload.action_id,
                request_id=approval_request.request_id,
                authorization_id=payload.authorization_id,
                run_id=approval_request.run_id,
                thread_id=approval_request.thread_id,
                checkpoint_revision=approval_request.checkpoint_revision,
                state_fingerprint=approval_request.state_fingerprint,
                approver_identity=payload.approver_identity,
                approver_role=payload.approver_role,
                decision=payload.decision,
                payload_fingerprint=payload_fingerprint,
                idempotency_key=payload.idempotency_key,
                policy_version=payload.policy_version,
                ledger_attempt=entry.attempt,
                ledger_revision=entry.revision,
                resume_status="pending" if payload.decision == "approve" else "suppressed",
                created_at=timestamp,
                updated_at=timestamp,
            )
            connection.execute(
                f"""
                INSERT INTO {self._TABLE} (action_id, idempotency_key, receipt_json, resume_revision)
                VALUES (?, ?, ?, 0)
                """,
                (
                    receipt.action_id,
                    receipt.idempotency_key,
                    receipt.model_dump_json(),
                ),
            )
            return receipt

    def get(self, action_id: str) -> ApprovalReceipt | None:
        with self._connect() as connection:
            return self._get(connection, action_id)

    def begin_resume(
        self, action_id: str, *, expected_resume_revision: int, now: datetime | None = None
    ) -> ApprovalReceipt:
        with self._transaction() as connection:
            receipt = self._get(connection, action_id, required=True)
            if receipt.decision != "approve":
                raise ApprovalResumeError("rejected approval cannot resume a checkpoint")
            if receipt.resume_status == "resumed":
                return receipt
            if receipt.resume_status != "pending":
                raise ApprovalResumeError(f"logical resume is already {receipt.resume_status}")
            if receipt.resume_revision != expected_resume_revision:
                raise ApprovalResumeError("resume receipt revision is stale")
            return self._update(
                connection,
                receipt,
                resume_status="executing",
                resume_attempt=receipt.resume_attempt + 1,
                updated_at=_now_iso(now),
            )

    def finish_resume(
        self,
        action_id: str,
        *,
        expected_resume_revision: int,
        success: bool,
        error: str | None = None,
        now: datetime | None = None,
    ) -> ApprovalReceipt:
        with self._transaction() as connection:
            receipt = self._get(connection, action_id, required=True)
            if receipt.resume_status == "resumed" and success:
                return receipt
            if receipt.resume_status != "executing" or receipt.resume_revision != expected_resume_revision:
                raise ApprovalResumeError("resume receipt is not owned by this attempt")
            return self._update(
                connection,
                receipt,
                resume_status="resumed" if success else "unknown",
                resume_error=error,
                updated_at=_now_iso(now),
            )

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._TABLE} (
                    action_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    receipt_json TEXT NOT NULL,
                    resume_revision INTEGER NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _transaction(self):
        repository = self

        class _Transaction:
            def __enter__(self):
                self.connection = repository._connect()
                self.connection.execute("BEGIN IMMEDIATE")
                return self.connection

            def __exit__(self, exc_type, exc, traceback):
                if exc_type is None:
                    self.connection.commit()
                else:
                    self.connection.rollback()
                self.connection.close()
                return False

        return _Transaction()

    def _get(
        self, connection: sqlite3.Connection, action_id: str, *, required: bool = False
    ) -> ApprovalReceipt | None:
        row = connection.execute(
            f"SELECT receipt_json FROM {self._TABLE} WHERE action_id = ?", (action_id,)
        ).fetchone()
        if row is None:
            if required:
                raise KeyError(f"unknown approval action_id '{action_id}'")
            return None
        return ApprovalReceipt.model_validate_json(row["receipt_json"])

    def _get_by_idempotency(
        self, connection: sqlite3.Connection, idempotency_key: str
    ) -> ApprovalReceipt | None:
        row = connection.execute(
            f"SELECT receipt_json FROM {self._TABLE} WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
        return ApprovalReceipt.model_validate_json(row["receipt_json"]) if row else None

    def _update(
        self,
        connection: sqlite3.Connection,
        receipt: ApprovalReceipt,
        **changes: Any,
    ) -> ApprovalReceipt:
        updated = receipt.model_copy(
            update={**changes, "resume_revision": receipt.resume_revision + 1}
        )
        cursor = connection.execute(
            f"""
            UPDATE {self._TABLE} SET receipt_json = ?, resume_revision = ?
            WHERE action_id = ? AND resume_revision = ?
            """,
            (
                updated.model_dump_json(),
                updated.resume_revision,
                receipt.action_id,
                receipt.resume_revision,
            ),
        )
        if cursor.rowcount != 1:
            raise ApprovalResumeError("resume receipt changed during compare-and-swap update")
        return updated


class CheckpointedHumanReviewHandler:
    """Explicit human-review handler; callers choose when to enter or resume it."""

    def __init__(
        self,
        control_plane: ActionExecutionControlPlane,
        approvals: ApprovalReceiptRepository,
        resume_adapter: CheckpointResumeAdapter,
    ) -> None:
        self.control_plane = control_plane
        self.approvals = approvals
        self.resume_adapter = resume_adapter

    async def enter_waiting(
        self,
        approval_request: ApprovalRequest,
        eligibility: ActionEligibilityDecision,
        *,
        runtime: ActionExecutionRuntimeView,
        worker_id: str,
        now: datetime | None = None,
    ) -> ActionLedgerEntry:
        entry = self._entry(approval_request.action_id)
        self._validate_enter(approval_request, eligibility, entry, runtime)
        claim = await self.control_plane.claim(
            entry.request.action_id,
            worker_id=worker_id,
            runtime=runtime,
            expected_ledger_revision=entry.revision,
            now=now,
        )
        return await self.control_plane.wait_for_approval(claim, runtime=runtime, now=now)

    async def submit_approval(
        self,
        approval_request: ApprovalRequest,
        payload: ApprovalPayload,
        *,
        runtime: ActionExecutionRuntimeView,
        now: datetime | None = None,
    ) -> ApprovalReceipt:
        existing = self.approvals.get(payload.action_id)
        if existing is not None:
            # First-wins receipts make identical delivery idempotent even after
            # the run has resumed or terminalized.
            return self.approvals.submit(approval_request, payload, self._entry(payload.action_id), now=now)

        entry = self._entry(payload.action_id)
        self._validate_payload(approval_request, payload, entry, runtime)
        async with self.control_plane.lease_for_runtime(runtime) as lease:
            try:
                self.control_plane.revalidate_runtime(entry.request, runtime, now=now)
            except ActionExecutionBlockedError as error:
                lease.assert_held()
                self.control_plane.ledger.terminalize(
                    entry.request.action_id,
                    expected_revision=entry.revision,
                    status=error.terminal_status,
                    error=str(error),
                    recovery_marker="blocked_at_approval",
                    now=now,
                )
                raise

            lease.assert_held()
            receipt = self.approvals.submit(approval_request, payload, entry, now=now)
            if receipt.decision == "reject":
                lease.assert_held()
                self.control_plane.ledger.terminalize(
                    entry.request.action_id,
                    expected_revision=entry.revision,
                    status="rejected",
                    error="human review rejected",
                    recovery_marker="approval_rejected",
                    now=now,
                )
            lease.assert_held()
            return receipt

    async def resume_approved(
        self,
        action_id: str,
        *,
        runtime: ActionExecutionRuntimeView,
        worker_id: str,
        now: datetime | None = None,
    ) -> ApprovalReceipt:
        receipt = self.approvals.get(action_id)
        if receipt is None:
            raise ApprovalResumeError("approval receipt is required before resume")
        if receipt.decision != "approve":
            raise ApprovalResumeError("rejected approval cannot resume a checkpoint")
        if receipt.resume_status == "resumed":
            return receipt
        if receipt.resume_status != "pending":
            raise ApprovalResumeError(f"logical resume is already {receipt.resume_status}")

        entry = self._entry(action_id)
        try:
            self.control_plane.revalidate_runtime(entry.request, runtime, now=now)
        except ActionExecutionBlockedError as error:
            async with self.control_plane.lease_for_runtime(runtime) as lease:
                lease.assert_held()
                self.control_plane.ledger.terminalize(
                    action_id,
                    expected_revision=entry.revision,
                    status=error.terminal_status,
                    error=str(error),
                    recovery_marker="blocked_before_approved_resume",
                    now=now,
                )
            raise

        claim = await self.control_plane.claim_waiting(
            action_id,
            worker_id=worker_id,
            runtime=runtime,
            expected_ledger_revision=entry.revision,
            now=now,
        )
        async with self.control_plane.lease_for_runtime(runtime) as lease:
            lease.assert_held()
            resuming = self.approvals.begin_resume(
                action_id,
                expected_resume_revision=receipt.resume_revision,
                now=now,
            )
            lease.assert_held()
        executing = await self.control_plane.mark_executing(claim, runtime=runtime, now=now)
        adapter_request = CheckpointResumeRequest(
            action_id=entry.request.action_id,
            request_id=entry.request.request_id,
            authorization_id=entry.request.authorization_id,
            run_id=entry.request.run_id,
            thread_id=entry.request.thread_id,
            checkpoint_revision=entry.request.expected_checkpoint_revision,
            ledger_attempt=executing.attempt,
            resume_attempt=resuming.resume_attempt,
        )
        try:
            result = await self.resume_adapter.resume(adapter_request)
        except Exception as error:
            self.approvals.finish_resume(
                action_id,
                expected_resume_revision=resuming.resume_revision,
                success=False,
                error=str(error),
                now=now,
            )
            raise

        await self.control_plane.commit(
            executing,
            runtime=runtime,
            outcome=ActionExecutionOutcome(
                status="succeeded",
                input_fingerprint=entry.request.state_fingerprint,
                output_fingerprint=result.output_fingerprint,
                state_transition_fingerprint=result.state_transition_fingerprint,
            ),
            now=now,
        )
        return self.approvals.finish_resume(
            action_id,
            expected_resume_revision=resuming.resume_revision,
            success=True,
            now=now,
        )

    def recover(self, action_id: str) -> ApprovalReceipt | None:
        """Expose durable wait/approval state; never auto-resume a checkpoint."""

        entry = self._entry(action_id)
        receipt = self.approvals.get(action_id)
        if entry.status == "succeeded" and receipt and receipt.resume_status == "executing":
            return self.approvals.finish_resume(
                action_id,
                expected_resume_revision=receipt.resume_revision,
                success=True,
            )
        if entry.status == "executing" and receipt and receipt.resume_status == "executing":
            # The adapter boundary may have been entered. Suppress duplicate
            # resume until an operator or an idempotent adapter reconciles it.
            return self.approvals.finish_resume(
                action_id,
                expected_resume_revision=receipt.resume_revision,
                success=False,
                error="resume outcome unknown during recovery",
            )
        return receipt

    def _entry(self, action_id: str) -> ActionLedgerEntry:
        entry = self.control_plane.ledger.get(action_id)
        if entry is None:
            raise KeyError(f"unknown human-review action_id '{action_id}'")
        return entry

    @staticmethod
    def _validate_enter(
        approval_request: ApprovalRequest,
        eligibility: ActionEligibilityDecision,
        entry: ActionLedgerEntry,
        runtime: ActionExecutionRuntimeView,
    ) -> None:
        if entry.request.action != "human_review":
            raise ApprovalAuthorizationError("only human_review may enter an approval wait")
        if eligibility.status != "eligible" or eligibility.action != "human_review":
            raise ApprovalAuthorizationError("human_review requires an eligible decision")
        if not eligibility.provenance or eligibility.provenance.authorization_id != entry.request.authorization_id:
            raise ApprovalAuthorizationError("eligibility authorization does not bind to action request")
        CheckpointedHumanReviewHandler._validate_request_binding(approval_request, entry, runtime)
        if entry.request.target.scope != "approval":
            raise ApprovalAuthorizationError("human_review target scope must be approval")

    @staticmethod
    def _validate_payload(
        approval_request: ApprovalRequest,
        payload: ApprovalPayload,
        entry: ActionLedgerEntry,
        runtime: ActionExecutionRuntimeView,
    ) -> None:
        CheckpointedHumanReviewHandler._validate_request_binding(approval_request, entry, runtime)
        if entry.status != "waiting_approval":
            raise ApprovalResumeError("approval may only be submitted while action is waiting")
        if payload.action_id != entry.request.action_id:
            raise ApprovalAuthorizationError("approval action_id does not bind to waiting action")
        if payload.authorization_id != entry.request.authorization_id:
            raise ApprovalAuthorizationError("approval authorization id does not bind to waiting action")
        if payload.approver_role not in approval_request.allowed_approver_roles:
            raise ApprovalAuthorizationError("approver role is not authorized for this review")
        if payload.policy_version != approval_request.policy_version:
            raise ApprovalAuthorizationError("approval policy version does not match request")

    @staticmethod
    def _validate_request_binding(
        approval_request: ApprovalRequest,
        entry: ActionLedgerEntry,
        runtime: ActionExecutionRuntimeView,
    ) -> None:
        request = entry.request
        if (
            approval_request.action_id != request.action_id
            or approval_request.request_id != request.request_id
            or approval_request.authorization_id != request.authorization_id
            or approval_request.run_id != request.run_id
            or approval_request.thread_id != request.thread_id
        ):
            raise ApprovalAuthorizationError("approval request does not bind to action request")
        if (
            approval_request.checkpoint_revision != request.expected_checkpoint_revision
            or approval_request.state_fingerprint != request.state_fingerprint
            or runtime.checkpoint_revision != request.expected_checkpoint_revision
            or runtime.state_fingerprint != request.state_fingerprint
        ):
            raise ApprovalAuthorizationError("approval checkpoint or state binding is stale")
        if runtime.run_id != request.run_id or runtime.thread_id != request.thread_id:
            raise ApprovalAuthorizationError("approval runtime identity is stale")


__all__ = [
    "HUMAN_REVIEW_POLICY_VERSION",
    "ApprovalAuthorizationError",
    "ApprovalConflictError",
    "ApprovalDecision",
    "ApprovalError",
    "ApprovalPayload",
    "ApprovalReceipt",
    "ApprovalReceiptRepository",
    "ApprovalRequest",
    "ApprovalResumeError",
    "CheckpointResumeAdapter",
    "CheckpointResumeRequest",
    "CheckpointResumeResult",
    "CheckpointedHumanReviewHandler",
    "approval_content_fingerprint",
]
