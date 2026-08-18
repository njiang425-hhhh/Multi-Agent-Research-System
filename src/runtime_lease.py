"""Cross-process SQLite leases for persistent research runners.

The lease is intentionally scoped to one checkpoint ``thread_id``.  It is not
a distributed job scheduler: callers get a deterministic conflict instead of
two runners resuming or starting the same checkpoint concurrently.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4


class PersistentRunLeaseConflictError(RuntimeError):
    """Raised when another active runner owns the same checkpoint thread."""


class PersistentRunLeaseLostError(RuntimeError):
    """Raised when a runner can no longer prove it owns its persisted lease."""


class PersistentRunLease:
    """An expiring SQLite lease refreshed while a persistent runner is active."""

    _TABLE = "research_runtime_leases"

    def __init__(
        self,
        checkpoint_path: Path,
        thread_id: str,
        *,
        ttl_seconds: float,
    ) -> None:
        if not thread_id.strip():
            raise ValueError("thread_id must not be empty")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")

        self.checkpoint_path = Path(checkpoint_path)
        self.thread_id = thread_id
        self.ttl_seconds = ttl_seconds
        self.owner_id = str(uuid4())
        self._held = False
        self._lost = False
        self._heartbeat_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "PersistentRunLease":
        await asyncio.to_thread(self._acquire)
        self._held = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat())
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        if self._held:
            await asyncio.to_thread(self._release)
        self._held = False

    def assert_held(self) -> None:
        if self._lost:
            raise PersistentRunLeaseLostError(
                f"Persistent run lease was lost for thread_id '{self.thread_id}'"
            )

    async def _heartbeat(self) -> None:
        # Refresh often enough to tolerate a scheduling delay but never create
        # an aggressive write loop for the shared checkpoint database.
        interval = max(0.1, self.ttl_seconds / 3)
        try:
            while True:
                await asyncio.sleep(interval)
                refreshed = await asyncio.to_thread(self._refresh)
                if not refreshed:
                    self._lost = True
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            # A later runner-boundary assertion turns this into an explicit
            # error instead of silently continuing without a lease.
            self._lost = True

    def _acquire(self) -> None:
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=self.ttl_seconds)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._TABLE} (
                    thread_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                f"DELETE FROM {self._TABLE} WHERE expires_at <= ?",
                (now.isoformat(),),
            )
            try:
                connection.execute(
                    f"INSERT INTO {self._TABLE} (thread_id, owner_id, expires_at) VALUES (?, ?, ?)",
                    (self.thread_id, self.owner_id, expires_at.isoformat()),
                )
            except sqlite3.IntegrityError as exc:
                raise PersistentRunLeaseConflictError(
                    f"Another persistent runner is active for thread_id '{self.thread_id}'"
                ) from exc
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _refresh(self) -> bool:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=self.ttl_seconds)
        connection = self._connect()
        try:
            cursor = connection.execute(
                f"UPDATE {self._TABLE} SET expires_at = ? WHERE thread_id = ? AND owner_id = ?",
                (expires_at.isoformat(), self.thread_id, self.owner_id),
            )
            connection.commit()
            return cursor.rowcount == 1
        finally:
            connection.close()

    def _release(self) -> None:
        connection = self._connect()
        try:
            connection.execute(
                f"DELETE FROM {self._TABLE} WHERE thread_id = ? AND owner_id = ?",
                (self.thread_id, self.owner_id),
            )
            connection.commit()
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.checkpoint_path), timeout=5.0)
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection
