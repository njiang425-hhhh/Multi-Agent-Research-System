"""Durable, handler-free control-plane contracts for authorized actions.

P13.1 deliberately stops before business execution.  It persists action
requests and their lifecycle, but never invokes an Agent, Graph, provider, or
State transition.  Future handlers must use this protocol before they can
perform an external effect or commit a business-state change.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any, AsyncIterator, Literal, Protocol

from pydantic import BaseModel, Field

from experiments.governance.action_authorization import ActionBudget, ActionTarget
from experiments.governance.quality_action import ActionKind
from src.runtime_control import ExecutionContext
from src.runtime_lease import PersistentRunLease


ACTION_EXECUTION_VERSION = "p13.1.action_execution.v1"

ActionLifecycleStatus = Literal[
    "queued",
    "claimed",
    "executing",
    "waiting_approval",
    "succeeded",
    "rejected",
    "no_progress",
    "expired",
    "failed",
    "cancelled",
]
ActionTerminalStatus = Literal[
    "succeeded",
    "rejected",
    "no_progress",
    "expired",
    "failed",
    "cancelled",
]

_TERMINAL_STATUSES = frozenset(
    {"succeeded", "rejected", "no_progress", "expired", "failed", "cancelled"}
)


class ActionExecutionError(RuntimeError):
    """Base error for action control-plane operations."""


class ActionRequestConflictError(ActionExecutionError):
    """The same action or idempotency key was reused with different content."""


class ActionLedgerRevisionError(ActionExecutionError):
    """A compare-and-swap ledger revision did not match."""


class ActionAlreadyClaimedError(ActionExecutionError):
    """Another worker owns the logical action attempt."""


class ActionTerminalError(ActionExecutionError):
    """A terminal action cannot be executed or committed again."""


class ActionExecutionBlockedError(ActionExecutionError):
    """Current runtime, authorization, or checkpoint facts block execution."""

    def __init__(self, message: str, *, terminal_status: ActionTerminalStatus = "failed") -> None:
        self.terminal_status = terminal_status
        super().__init__(message)


class ActionExecutionDeadlineSnapshot(BaseModel):
    """An absolute, non-resettable deadline derived by P12 eligibility."""

    effective_deadline_seconds: float = Field(gt=0)
    effective_deadline_at: str = Field(min_length=1)


class ActionExecutionRequest(BaseModel):
    """Immutable request for one logical action; it is not a dispatch command."""

    action_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    action: ActionKind
    target: ActionTarget
    authorization_id: str = Field(min_length=1)
    recommendation_fingerprint: str = Field(min_length=1)
    authorization_fingerprint: str = Field(min_length=1)
    state_fingerprint: str = Field(min_length=1)
    expected_checkpoint_revision: str = Field(min_length=1)
    effective_budget: ActionBudget
    deadline: ActionExecutionDeadlineSnapshot
    idempotency_key: str = Field(min_length=1)
    created_at: str = Field(min_length=1)
    version: str = ACTION_EXECUTION_VERSION


class ActionExecutionRuntimeView(BaseModel):
    """Current runtime facts supplied by the Runtime-owned boundary."""

    run_id: str
    thread_id: str
    checkpoint_revision: str
    state_fingerprint: str
    authorization_id: str
    authorization_fingerprint: str
    recommendation_fingerprint: str
    run_status: str
    terminal_reason: str | None = None
    execution_context: ExecutionContext | None = None


class ActionExecutionConstraints(BaseModel):
    """Claim-time limits after intersecting action and current runtime limits."""

    effective_budget: ActionBudget
    effective_deadline_at: str


class ActionExecutionClaim(BaseModel):
    """Lease-scoped ownership returned to a future handler, never auto-dispatched."""

    action_id: str
    request_id: str
    ledger_revision: int
    attempt: int
    worker_id: str
    lease_owner_id: str
    constraints: ActionExecutionConstraints


class ActionLifecycleTraceMetadata(BaseModel):
    """Action attribution metadata for a future append-only Trace adapter."""

    action_id: str
    request_id: str
    authorization_id: str
    attempt: int = Field(ge=0)
    lifecycle_transition: str
    worker_id: str | None = None
    lease_owner_id: str | None = None
    error: str | None = None
    recovery_reason: str | None = None


class ActionLedgerEntry(BaseModel):
    """Durable action ledger projection; request payload remains immutable."""

    request: ActionExecutionRequest
    status: ActionLifecycleStatus
    revision: int = Field(ge=0)
    attempt: int = Field(ge=0)
    worker_id: str | None = None
    lease_owner_id: str | None = None
    created_at: str
    claimed_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    input_fingerprint: str | None = None
    output_fingerprint: str | None = None
    state_transition_fingerprint: str | None = None
    error: str | None = None
    recovery_marker: str | None = None
    claim_constraints: ActionExecutionConstraints | None = None

    @property
    def terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES


class ActionExecutionOutcome(BaseModel):
    """Future handler output that may be ledger-committed, never state-applied here."""

    status: ActionTerminalStatus
    input_fingerprint: str
    output_fingerprint: str | None = None
    state_transition_fingerprint: str | None = None
    error: str | None = None


class ActionExecutor(Protocol):
    """Handler interface only; P13.1 ships no implementation or dispatcher."""

    async def execute(
        self,
        request: ActionExecutionRequest,
        claim: ActionExecutionClaim,
    ) -> ActionExecutionOutcome:
        """Perform one specifically registered future business action."""


def action_execution_content_fingerprint(value: Any) -> str:
    """Stable fingerprint helper used for request and transition binding."""

    encoded = json.dumps(_json_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _json_value(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _parse_timestamp(value: str) -> datetime:
    return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _now_iso(now: datetime | None) -> str:
    return _as_utc(now or datetime.now(timezone.utc)).isoformat()


def _entry_request_json(request: ActionExecutionRequest) -> str:
    return json.dumps(request.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ActionLedgerRepository:
    """SQLite ledger with immutable requests and compare-and-swap transitions."""

    _TABLE = "action_execution_ledger"

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._initialize()

    def submit(self, request: ActionExecutionRequest) -> ActionLedgerEntry:
        """Insert one request or return its exact existing logical action."""

        request_json = _entry_request_json(request)
        request_fingerprint = action_execution_content_fingerprint(request)
        with self._transaction() as connection:
            existing = self._select(connection, request.action_id)
            if existing is None:
                existing = self._select_by_idempotency(connection, request.idempotency_key)
            if existing is not None:
                if action_execution_content_fingerprint(existing.request) != request_fingerprint:
                    raise ActionRequestConflictError(
                        "action_id or idempotency_key is already bound to different request content"
                    )
                return existing
            connection.execute(
                f"""
                INSERT INTO {self._TABLE} (
                    action_id, request_id, run_id, thread_id, idempotency_key,
                    request_json, status, revision, attempt, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'queued', 0, 0, ?)
                """,
                (
                    request.action_id,
                    request.request_id,
                    request.run_id,
                    request.thread_id,
                    request.idempotency_key,
                    request_json,
                    request.created_at,
                ),
            )
            return self._select(connection, request.action_id, required=True)

    def get(self, action_id: str) -> ActionLedgerEntry | None:
        with self._connect() as connection:
            return self._select(connection, action_id)

    def claim(
        self,
        action_id: str,
        *,
        expected_revision: int,
        worker_id: str,
        lease_owner_id: str,
        constraints: ActionExecutionConstraints,
        now: datetime | None = None,
    ) -> ActionLedgerEntry:
        with self._transaction() as connection:
            entry = self._select(connection, action_id, required=True)
            if entry.terminal:
                raise ActionTerminalError(f"action '{action_id}' is already terminal: {entry.status}")
            if entry.revision != expected_revision:
                raise ActionLedgerRevisionError(f"action '{action_id}' ledger revision is stale")
            if entry.status != "queued":
                raise ActionAlreadyClaimedError(f"action '{action_id}' is already {entry.status}")
            return self._update(
                connection,
                entry,
                status="claimed",
                worker_id=worker_id,
                lease_owner_id=lease_owner_id,
                claimed_at=_now_iso(now),
                claim_constraints=constraints,
            )

    def claim_waiting(
        self,
        action_id: str,
        *,
        expected_revision: int,
        worker_id: str,
        lease_owner_id: str,
        constraints: ActionExecutionConstraints,
        now: datetime | None = None,
    ) -> ActionLedgerEntry:
        """Claim an explicitly approved waiting action without dispatching it."""

        with self._transaction() as connection:
            entry = self._select(connection, action_id, required=True)
            if entry.terminal:
                raise ActionTerminalError(f"action '{action_id}' is already terminal: {entry.status}")
            if entry.revision != expected_revision:
                raise ActionLedgerRevisionError(f"action '{action_id}' ledger revision is stale")
            if entry.status != "waiting_approval":
                raise ActionAlreadyClaimedError(
                    f"action '{action_id}' is {entry.status}, not waiting_approval"
                )
            return self._update(
                connection,
                entry,
                status="claimed",
                worker_id=worker_id,
                lease_owner_id=lease_owner_id,
                claimed_at=_now_iso(now),
                recovery_marker="approved_resume_pending",
                claim_constraints=constraints,
            )

    def mark_executing(
        self,
        action_id: str,
        *,
        expected_revision: int,
        worker_id: str,
        lease_owner_id: str,
        now: datetime | None = None,
    ) -> ActionLedgerEntry:
        with self._transaction() as connection:
            entry = self._select(connection, action_id, required=True)
            self._assert_owned(entry, expected_revision, worker_id, lease_owner_id, "claimed")
            return self._update(
                connection,
                entry,
                status="executing",
                started_at=_now_iso(now),
                attempt=entry.attempt + 1,
                lease_owner_id=lease_owner_id,
            )

    def mark_waiting_approval(
        self,
        action_id: str,
        *,
        expected_revision: int,
        worker_id: str,
        lease_owner_id: str,
        now: datetime | None = None,
    ) -> ActionLedgerEntry:
        with self._transaction() as connection:
            entry = self._select(connection, action_id, required=True)
            self._assert_owned(entry, expected_revision, worker_id, lease_owner_id, "claimed")
            return self._update(
                connection,
                entry,
                status="waiting_approval",
                recovery_marker="lease_released_while_waiting",
                finished_at=_now_iso(now),
                lease_owner_id=lease_owner_id,
            )

    def commit(
        self,
        action_id: str,
        *,
        expected_revision: int,
        worker_id: str,
        lease_owner_id: str,
        outcome: ActionExecutionOutcome,
        now: datetime | None = None,
    ) -> ActionLedgerEntry:
        """Record one logical transition commit; no ResearchState is written."""

        with self._transaction() as connection:
            entry = self._select(connection, action_id, required=True)
            if entry.terminal:
                if self._same_terminal_outcome(entry, outcome):
                    return entry
                raise ActionTerminalError(f"action '{action_id}' already committed as {entry.status}")
            self._assert_owned(entry, expected_revision, worker_id, lease_owner_id, "executing")
            return self._update(
                connection,
                entry,
                status=outcome.status,
                finished_at=_now_iso(now),
                lease_owner_id=lease_owner_id,
                input_fingerprint=outcome.input_fingerprint,
                output_fingerprint=outcome.output_fingerprint,
                state_transition_fingerprint=outcome.state_transition_fingerprint,
                error=outcome.error,
            )

    def terminalize(
        self,
        action_id: str,
        *,
        expected_revision: int,
        status: ActionTerminalStatus,
        error: str,
        recovery_marker: str | None = None,
        now: datetime | None = None,
    ) -> ActionLedgerEntry:
        with self._transaction() as connection:
            entry = self._select(connection, action_id, required=True)
            if entry.terminal:
                return entry
            if entry.revision != expected_revision:
                raise ActionLedgerRevisionError(f"action '{action_id}' ledger revision is stale")
            return self._update(
                connection,
                entry,
                status=status,
                finished_at=_now_iso(now),
                error=error,
                recovery_marker=recovery_marker,
            )

    def recover(self, action_id: str, *, now: datetime | None = None) -> ActionLedgerEntry:
        """Recover deterministically without repeating an uncertain external effect."""

        with self._transaction() as connection:
            entry = self._select(connection, action_id, required=True)
            if entry.status == "claimed":
                return self._update(
                    connection,
                    entry,
                    status="queued",
                    worker_id=None,
                    lease_owner_id=None,
                    recovery_marker="recovered_before_external_execution",
                )
            if entry.status == "executing":
                return self._update(
                    connection,
                    entry,
                    status="failed",
                    finished_at=_now_iso(now),
                    error="external execution outcome unknown after recovery",
                    recovery_marker="external_execution_uncommitted",
                )
            return entry

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._TABLE} (
                    action_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    attempt INTEGER NOT NULL,
                    worker_id TEXT,
                    lease_owner_id TEXT,
                    created_at TEXT NOT NULL,
                    claimed_at TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    input_fingerprint TEXT,
                    output_fingerprint TEXT,
                    state_transition_fingerprint TEXT,
                    error TEXT,
                    recovery_marker TEXT,
                    claim_constraints_json TEXT
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

    def _select(
        self,
        connection: sqlite3.Connection,
        action_id: str,
        *,
        required: bool = False,
    ) -> ActionLedgerEntry | None:
        row = connection.execute(
            f"SELECT * FROM {self._TABLE} WHERE action_id = ?", (action_id,)
        ).fetchone()
        if row is None:
            if required:
                raise KeyError(f"unknown action_id '{action_id}'")
            return None
        return self._entry_from_row(row)

    def _select_by_idempotency(
        self, connection: sqlite3.Connection, idempotency_key: str
    ) -> ActionLedgerEntry | None:
        row = connection.execute(
            f"SELECT * FROM {self._TABLE} WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
        return self._entry_from_row(row) if row is not None else None

    @staticmethod
    def _entry_from_row(row: sqlite3.Row) -> ActionLedgerEntry:
        constraints_json = row["claim_constraints_json"]
        return ActionLedgerEntry(
            request=ActionExecutionRequest.model_validate_json(row["request_json"]),
            status=row["status"],
            revision=row["revision"],
            attempt=row["attempt"],
            worker_id=row["worker_id"],
            lease_owner_id=row["lease_owner_id"],
            created_at=row["created_at"],
            claimed_at=row["claimed_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            input_fingerprint=row["input_fingerprint"],
            output_fingerprint=row["output_fingerprint"],
            state_transition_fingerprint=row["state_transition_fingerprint"],
            error=row["error"],
            recovery_marker=row["recovery_marker"],
            claim_constraints=(
                ActionExecutionConstraints.model_validate_json(constraints_json)
                if constraints_json
                else None
            ),
        )

    def _update(
        self,
        connection: sqlite3.Connection,
        entry: ActionLedgerEntry,
        **changes: Any,
    ) -> ActionLedgerEntry:
        revision = entry.revision + 1
        values = {
            "status": entry.status,
            "attempt": entry.attempt,
            "worker_id": entry.worker_id,
            "lease_owner_id": entry.lease_owner_id,
            "claimed_at": entry.claimed_at,
            "started_at": entry.started_at,
            "finished_at": entry.finished_at,
            "input_fingerprint": entry.input_fingerprint,
            "output_fingerprint": entry.output_fingerprint,
            "state_transition_fingerprint": entry.state_transition_fingerprint,
            "error": entry.error,
            "recovery_marker": entry.recovery_marker,
            "claim_constraints_json": (
                entry.claim_constraints.model_dump_json() if entry.claim_constraints else None
            ),
        }
        values.update(changes)
        if "claim_constraints" in values:
            constraints = values.pop("claim_constraints")
            values["claim_constraints_json"] = constraints.model_dump_json() if constraints else None
        assignments = ", ".join([f"{name} = ?" for name in values] + ["revision = ?"])
        cursor = connection.execute(
            f"UPDATE {self._TABLE} SET {assignments} WHERE action_id = ? AND revision = ?",
            (*values.values(), revision, entry.request.action_id, entry.revision),
        )
        if cursor.rowcount != 1:
            raise ActionLedgerRevisionError(
                f"action '{entry.request.action_id}' changed during ledger transition"
            )
        return self._select(connection, entry.request.action_id, required=True)

    @staticmethod
    def _assert_owned(
        entry: ActionLedgerEntry,
        expected_revision: int,
        worker_id: str,
        lease_owner_id: str,
        expected_status: ActionLifecycleStatus,
    ) -> None:
        if entry.terminal:
            raise ActionTerminalError(
                f"action '{entry.request.action_id}' is already terminal: {entry.status}"
            )
        if entry.revision != expected_revision:
            raise ActionLedgerRevisionError(
                f"action '{entry.request.action_id}' ledger revision is stale"
            )
        if entry.status != expected_status:
            raise ActionAlreadyClaimedError(
                f"action '{entry.request.action_id}' is {entry.status}, not {expected_status}"
            )
        if entry.worker_id != worker_id:
            raise ActionAlreadyClaimedError(
                f"action '{entry.request.action_id}' is not owned by this worker"
            )

    @staticmethod
    def _same_terminal_outcome(entry: ActionLedgerEntry, outcome: ActionExecutionOutcome) -> bool:
        return (
            entry.status == outcome.status
            and entry.input_fingerprint == outcome.input_fingerprint
            and entry.output_fingerprint == outcome.output_fingerprint
            and entry.state_transition_fingerprint == outcome.state_transition_fingerprint
            and entry.error == outcome.error
        )


class ActionExecutionControlPlane:
    """Lease-aware facade that validates Runtime facts before ledger mutation."""

    def __init__(
        self,
        ledger: ActionLedgerRepository,
        *,
        checkpoint_path: Path,
        lease_ttl_seconds: float = 30.0,
    ) -> None:
        self.ledger = ledger
        self.checkpoint_path = Path(checkpoint_path)
        self.lease_ttl_seconds = lease_ttl_seconds

    def submit_request(self, request: ActionExecutionRequest) -> ActionLedgerEntry:
        return self.ledger.submit(request)

    def revalidate_runtime(
        self,
        request: ActionExecutionRequest,
        runtime: ActionExecutionRuntimeView,
        *,
        now: datetime | None = None,
    ) -> ActionExecutionConstraints:
        """Read current Runtime facts without changing the ledger or State."""

        return self._validate_runtime(request, runtime, now=now)

    @asynccontextmanager
    async def lease_for_runtime(
        self, runtime: ActionExecutionRuntimeView
    ) -> AsyncIterator[PersistentRunLease]:
        """Expose one short Runtime checkpoint lease to a sibling control-plane store."""

        async with self._lease(runtime.thread_id) as lease:
            lease.assert_held()
            yield lease

    async def claim(
        self,
        action_id: str,
        *,
        worker_id: str,
        runtime: ActionExecutionRuntimeView,
        expected_ledger_revision: int,
        now: datetime | None = None,
    ) -> ActionExecutionClaim:
        async with self._lease(runtime.thread_id) as lease:
            entry = self._entry_for_runtime(action_id, runtime)
            try:
                constraints = self._validate_runtime(entry.request, runtime, now=now)
            except ActionExecutionBlockedError as error:
                self.ledger.terminalize(
                    action_id,
                    expected_revision=entry.revision,
                    status=error.terminal_status,
                    error=str(error),
                    recovery_marker="blocked_at_claim",
                    now=now,
                )
                raise
            lease.assert_held()
            claimed = self.ledger.claim(
                action_id,
                expected_revision=expected_ledger_revision,
                worker_id=worker_id,
                lease_owner_id=lease.owner_id,
                constraints=constraints,
                now=now,
            )
            lease.assert_held()
            return ActionExecutionClaim(
                action_id=claimed.request.action_id,
                request_id=claimed.request.request_id,
                ledger_revision=claimed.revision,
                attempt=claimed.attempt,
                worker_id=worker_id,
                lease_owner_id=lease.owner_id,
                constraints=constraints,
            )

    async def mark_executing(
        self,
        claim: ActionExecutionClaim,
        *,
        runtime: ActionExecutionRuntimeView,
        now: datetime | None = None,
    ) -> ActionExecutionClaim:
        async with self._lease(runtime.thread_id) as lease:
            entry = self._entry_for_runtime(claim.action_id, runtime)
            self._validate_runtime(entry.request, runtime, now=now)
            lease.assert_held()
            executing = self.ledger.mark_executing(
                claim.action_id,
                expected_revision=claim.ledger_revision,
                worker_id=claim.worker_id,
                lease_owner_id=claim.lease_owner_id,
                now=now,
            )
            lease.assert_held()
            return claim.model_copy(
                update={
                    "ledger_revision": executing.revision,
                    "attempt": executing.attempt,
                    "lease_owner_id": lease.owner_id,
                }
            )

    async def claim_waiting(
        self,
        action_id: str,
        *,
        worker_id: str,
        runtime: ActionExecutionRuntimeView,
        expected_ledger_revision: int,
        now: datetime | None = None,
    ) -> ActionExecutionClaim:
        """Claim an approved waiting action at a runner boundary."""

        async with self._lease(runtime.thread_id) as lease:
            entry = self._entry_for_runtime(action_id, runtime)
            try:
                constraints = self._validate_runtime(entry.request, runtime, now=now)
            except ActionExecutionBlockedError as error:
                self.ledger.terminalize(
                    action_id,
                    expected_revision=entry.revision,
                    status=error.terminal_status,
                    error=str(error),
                    recovery_marker="blocked_before_resume",
                    now=now,
                )
                raise
            lease.assert_held()
            claimed = self.ledger.claim_waiting(
                action_id,
                expected_revision=expected_ledger_revision,
                worker_id=worker_id,
                lease_owner_id=lease.owner_id,
                constraints=constraints,
                now=now,
            )
            lease.assert_held()
            return ActionExecutionClaim(
                action_id=claimed.request.action_id,
                request_id=claimed.request.request_id,
                ledger_revision=claimed.revision,
                attempt=claimed.attempt,
                worker_id=worker_id,
                lease_owner_id=lease.owner_id,
                constraints=constraints,
            )

    async def wait_for_approval(
        self,
        claim: ActionExecutionClaim,
        *,
        runtime: ActionExecutionRuntimeView,
        now: datetime | None = None,
    ) -> ActionLedgerEntry:
        async with self._lease(runtime.thread_id) as lease:
            entry = self._entry_for_runtime(claim.action_id, runtime)
            self._validate_runtime(entry.request, runtime, now=now)
            lease.assert_held()
            waiting = self.ledger.mark_waiting_approval(
                claim.action_id,
                expected_revision=claim.ledger_revision,
                worker_id=claim.worker_id,
                lease_owner_id=claim.lease_owner_id,
                now=now,
            )
            lease.assert_held()
            return waiting

    async def commit(
        self,
        claim: ActionExecutionClaim,
        *,
        runtime: ActionExecutionRuntimeView,
        outcome: ActionExecutionOutcome,
        now: datetime | None = None,
    ) -> ActionLedgerEntry:
        """Revalidate before recording one logical State-transition commit intent."""

        async with self._lease(runtime.thread_id) as lease:
            entry = self._entry_for_runtime(claim.action_id, runtime)
            try:
                self._validate_runtime(entry.request, runtime, now=now)
            except ActionExecutionBlockedError as error:
                if entry.revision == claim.ledger_revision and entry.worker_id == claim.worker_id:
                    lease.assert_held()
                    self.ledger.terminalize(
                        claim.action_id,
                        expected_revision=entry.revision,
                        status=error.terminal_status,
                        error=str(error),
                        recovery_marker="blocked_at_commit",
                        now=now,
                    )
                raise
            lease.assert_held()
            committed = self.ledger.commit(
                claim.action_id,
                expected_revision=claim.ledger_revision,
                worker_id=claim.worker_id,
                lease_owner_id=claim.lease_owner_id,
                outcome=outcome,
                now=now,
            )
            lease.assert_held()
            return committed

    async def recover(self, action_id: str, *, now: datetime | None = None) -> ActionLedgerEntry:
        entry = self.ledger.get(action_id)
        if entry is None:
            raise KeyError(f"unknown action_id '{action_id}'")
        async with self._lease(entry.request.thread_id) as lease:
            lease.assert_held()
            recovered = self.ledger.recover(action_id, now=now)
            lease.assert_held()
            return recovered

    def trace_metadata(
        self,
        entry: ActionLedgerEntry,
        *,
        lifecycle_transition: str,
    ) -> ActionLifecycleTraceMetadata:
        """Return attribution only; existing Trace/Usage stores are untouched."""

        return ActionLifecycleTraceMetadata(
            action_id=entry.request.action_id,
            request_id=entry.request.request_id,
            authorization_id=entry.request.authorization_id,
            attempt=entry.attempt,
            lifecycle_transition=lifecycle_transition,
            worker_id=entry.worker_id,
            lease_owner_id=entry.lease_owner_id,
            error=entry.error,
            recovery_reason=entry.recovery_marker,
        )

    @asynccontextmanager
    async def _lease(self, thread_id: str) -> AsyncIterator[PersistentRunLease]:
        """Acquire the Runtime's checkpoint lease only for a short mutation."""

        async with PersistentRunLease(
            self.checkpoint_path,
            thread_id,
            ttl_seconds=self.lease_ttl_seconds,
        ) as lease:
            lease.assert_held()
            yield lease

    def _entry_for_runtime(
        self, action_id: str, runtime: ActionExecutionRuntimeView
    ) -> ActionLedgerEntry:
        entry = self.ledger.get(action_id)
        if entry is None:
            raise KeyError(f"unknown action_id '{action_id}'")
        if entry.request.thread_id != runtime.thread_id:
            raise ActionExecutionBlockedError("runtime thread_id does not bind to action request")
        return entry

    @staticmethod
    def _validate_runtime(
        request: ActionExecutionRequest,
        runtime: ActionExecutionRuntimeView,
        *,
        now: datetime | None,
    ) -> ActionExecutionConstraints:
        if request.run_id != runtime.run_id:
            raise ActionExecutionBlockedError("runtime run_id does not bind to action request")
        if request.thread_id != runtime.thread_id:
            raise ActionExecutionBlockedError("runtime thread_id does not bind to action request")
        if request.authorization_id != runtime.authorization_id:
            raise ActionExecutionBlockedError("authorization id is stale")
        if request.authorization_fingerprint != runtime.authorization_fingerprint:
            raise ActionExecutionBlockedError("authorization fingerprint is stale")
        if request.recommendation_fingerprint != runtime.recommendation_fingerprint:
            raise ActionExecutionBlockedError("recommendation fingerprint is stale")
        if request.expected_checkpoint_revision != runtime.checkpoint_revision:
            raise ActionExecutionBlockedError("checkpoint revision is stale")
        if request.state_fingerprint != runtime.state_fingerprint:
            raise ActionExecutionBlockedError("state fingerprint is stale")
        if runtime.run_status == "cancelled" or runtime.terminal_reason == "cancelled":
            raise ActionExecutionBlockedError("runtime run is cancelled", terminal_status="cancelled")
        if runtime.run_status in {"completed", "failed"}:
            raise ActionExecutionBlockedError("runtime run is terminal")
        if runtime.execution_context is None:
            raise ActionExecutionBlockedError("runtime execution context is required")
        if not request.effective_budget.has_limit():
            raise ActionExecutionBlockedError("action effective budget is required")

        current = _as_utc(now or datetime.now(timezone.utc))
        action_deadline = _parse_timestamp(request.deadline.effective_deadline_at)
        if action_deadline <= current:
            raise ActionExecutionBlockedError("action deadline is exhausted", terminal_status="expired")
        runtime_remaining = runtime.execution_context.remaining_timeout_seconds(now=current)
        if runtime_remaining is not None and runtime_remaining <= 0:
            raise ActionExecutionBlockedError("runtime deadline is exhausted", terminal_status="expired")

        deadline = action_deadline
        if runtime.execution_context.deadline_at:
            runtime_deadline = _parse_timestamp(runtime.execution_context.deadline_at)
            deadline = min(deadline, runtime_deadline)
        if deadline <= current:
            raise ActionExecutionBlockedError("effective action deadline is exhausted", terminal_status="expired")

        effective_budget = request.effective_budget.model_copy(deep=True)
        remaining_operations = runtime.execution_context.remaining_operation_calls()
        if remaining_operations is not None:
            if remaining_operations <= 0:
                raise ActionExecutionBlockedError("runtime operation budget is exhausted")
            if effective_budget.max_operation_calls is None:
                effective_budget = effective_budget.model_copy(
                    update={"max_operation_calls": remaining_operations}
                )
            elif effective_budget.max_operation_calls > remaining_operations:
                effective_budget = effective_budget.model_copy(
                    update={"max_operation_calls": remaining_operations}
                )
        return ActionExecutionConstraints(
            effective_budget=effective_budget,
            effective_deadline_at=deadline.isoformat(),
        )


__all__ = [
    "ACTION_EXECUTION_VERSION",
    "ActionAlreadyClaimedError",
    "ActionExecutionClaim",
    "ActionExecutionConstraints",
    "ActionExecutionControlPlane",
    "ActionExecutionDeadlineSnapshot",
    "ActionExecutionError",
    "ActionExecutionBlockedError",
    "ActionExecutionOutcome",
    "ActionExecutionRequest",
    "ActionExecutionRuntimeView",
    "ActionExecutor",
    "ActionLedgerEntry",
    "ActionLedgerRepository",
    "ActionLedgerRevisionError",
    "ActionLifecycleStatus",
    "ActionLifecycleTraceMetadata",
    "ActionRequestConflictError",
    "ActionTerminalError",
    "action_execution_content_fingerprint",
]
