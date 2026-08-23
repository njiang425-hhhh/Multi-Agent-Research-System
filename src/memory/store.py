"""Deterministic local SQLite store for Research Memory V1."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from src.memory.contracts import ResearchMemoryRecord
from src.memory.projection import record_search_text, tokenize_for_memory


class ResearchMemoryStore:
    """Small local persistence boundary; no remote services or embeddings."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_records: int = 500,
    ) -> None:
        self.path = Path(path)
        self.max_records = max_records
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.path))

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS research_memory (
                    memory_id TEXT PRIMARY KEY,
                    content_hash TEXT NOT NULL UNIQUE,
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT,
                    status TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_research_memory_expires "
                "ON research_memory(expires_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_research_memory_updated "
                "ON research_memory(updated_at)"
            )

    def upsert(self, record: ResearchMemoryRecord) -> ResearchMemoryRecord:
        """Persist one record, deduplicating by stable content hash."""

        with self._connect() as conn:
            existing = conn.execute(
                "SELECT record_json FROM research_memory WHERE content_hash = ?",
                (record.content_hash,),
            ).fetchone()
            if existing:
                prior = ResearchMemoryRecord.model_validate_json(existing[0])
                record = record.model_copy(
                    update={
                        "memory_id": prior.memory_id,
                        "created_at": prior.created_at,
                        "provenance": {
                            **record.provenance,
                            "previous_run_id": prior.run_id,
                        },
                    }
                )
            conn.execute(
                """
                INSERT INTO research_memory(
                    memory_id, content_hash, record_json, created_at,
                    updated_at, expires_at, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    record_json = excluded.record_json,
                    updated_at = excluded.updated_at,
                    expires_at = excluded.expires_at,
                    status = excluded.status
                """,
                (
                    record.memory_id,
                    record.content_hash,
                    record.model_dump_json(),
                    record.created_at,
                    record.updated_at,
                    record.expires_at,
                    record.status,
                ),
            )
        self.prune()
        return record

    def upsert_many(self, records: Sequence[ResearchMemoryRecord]) -> list[ResearchMemoryRecord]:
        return [self.upsert(record) for record in records]

    def get(self, memory_id: str) -> ResearchMemoryRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT record_json FROM research_memory WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
        return ResearchMemoryRecord.model_validate_json(row[0]) if row else None

    def delete(self, memory_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM research_memory WHERE memory_id = ?",
                (memory_id,),
            )
            return cursor.rowcount > 0

    def prune(self, *, now: datetime | None = None) -> int:
        """Remove expired records and trim the store to max_records."""

        current = (now or datetime.now(timezone.utc)).isoformat()
        removed = 0
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM research_memory WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (current,),
            )
            removed += cursor.rowcount
            overflow = conn.execute(
                "SELECT COUNT(*) FROM research_memory WHERE status = 'active'"
            ).fetchone()[0] - self.max_records
            if overflow > 0:
                rows = conn.execute(
                    """
                    SELECT memory_id FROM research_memory
                    WHERE status = 'active'
                    ORDER BY updated_at ASC, memory_id ASC
                    LIMIT ?
                    """,
                    (overflow,),
                ).fetchall()
                ids = [row[0] for row in rows]
                if ids:
                    placeholders = ",".join("?" for _ in ids)
                    cursor = conn.execute(
                        f"DELETE FROM research_memory WHERE memory_id IN ({placeholders})",
                        ids,
                    )
                    removed += cursor.rowcount
        return removed

    def list_records(self) -> list[ResearchMemoryRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT record_json FROM research_memory
                WHERE status = 'active'
                ORDER BY created_at DESC, memory_id ASC
                """
            ).fetchall()
        return [ResearchMemoryRecord.model_validate_json(row[0]) for row in rows]

    def retrieve(
        self,
        query: str,
        *,
        limit: int,
        tags: Sequence[str] | None = None,
        now: datetime | None = None,
    ) -> list[tuple[ResearchMemoryRecord, float]]:
        """Return deterministic lexical matches ordered by score."""

        if limit <= 0:
            return []
        self.prune(now=now)
        query_tokens = tokenize_for_memory(query)
        if not query_tokens:
            return []
        tag_filter = {tag.lower() for tag in tags or ()}

        scored: list[tuple[ResearchMemoryRecord, float]] = []
        for record in self.list_records():
            if tag_filter and not tag_filter.intersection({tag.lower() for tag in record.tags}):
                continue
            record_tokens = tokenize_for_memory(record_search_text(record))
            overlap = query_tokens & record_tokens
            if not overlap:
                continue
            score = len(overlap) / max(1, len(query_tokens))
            scored.append((record, round(score, 6)))

        scored.sort(
            key=lambda item: (
                -item[1],
                item[0].created_at,
                item[0].memory_id,
            )
        )
        return scored[:limit]

    def count(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM research_memory").fetchone()[0])

    def dump_json(self) -> list[dict]:
        return [json.loads(record.model_dump_json()) for record in self.list_records()]
