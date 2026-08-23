"""Contracts for the local Research Memory V1 baseline."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


MemoryGroundingLevel = Literal[
    "evidence_grounded",
    "evidence_partial",
    "legacy_source_url",
]


class ResearchMemoryRecord(BaseModel):
    """One source-backed memory projected from a completed research run."""

    memory_id: str = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    run_id: str = ""
    topic: str = ""
    statement: str = Field(min_length=1)
    summary: str = ""
    source_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    document_refs: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    grounding_level: MemoryGroundingLevel = "legacy_source_url"
    provenance: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    expires_at: str | None = None
    status: str = "active"
