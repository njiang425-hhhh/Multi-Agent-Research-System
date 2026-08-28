"""Optional memory retrieval helpers used by Planner and Searcher."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src.config import config
from src.memory.projection import memory_retrieval_items
from src.memory.store import ResearchMemoryStore
from src.state import MemoryItem


logger = logging.getLogger(__name__)


def default_memory_store() -> ResearchMemoryStore:
    return ResearchMemoryStore(
        Path(config.research_memory_store_path),
        max_records=config.research_memory_max_records,
    )


def retrieve_memory_items_with_diagnostics(
    store: ResearchMemoryStore | None,
    query: str,
) -> tuple[list[MemoryItem], dict[str, Any]]:
    if (
        not config.research_memory_enabled
        or config.research_memory_retrieval_limit <= 0
        or store is None
    ):
        return [], {
            "enabled": bool(config.research_memory_enabled),
            "retrieval_outcome": "disabled_or_unavailable",
            "retrieved_count": 0,
            "retrieved_memory_ids": [],
            "retrieval": [],
        }
    try:
        items, observations = memory_retrieval_items(
            query,
            store.retrieve(query, limit=config.research_memory_retrieval_limit),
        )
        return items, {
            "enabled": True,
            "retrieval_outcome": "completed",
            "retrieved_count": len(items),
            "retrieved_memory_ids": [item.memory_id for item in items],
            "retrieval": observations,
        }
    except Exception as exc:
        logger.warning("Research memory retrieval failed: %s", exc)
        return [], {
            "enabled": True,
            "retrieval_outcome": "failed",
            "retrieval_error": str(exc),
            "retrieved_count": 0,
            "retrieved_memory_ids": [],
            "retrieval": [],
        }
