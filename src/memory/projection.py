"""Projection and prompt helpers for Research Memory V1."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
import hashlib
import re
from typing import Any
from urllib.parse import urlsplit

from src.memory.contracts import MemoryGroundingLevel, ResearchMemoryRecord
from src.state import MemoryItem, SearchQuery


_TOKEN_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]+")


def _read(value: Any, field: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(field, default)
    return getattr(value, field, default)


def _records(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _dedupe(values: Sequence[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _text(value)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _summary(statement: str, *, limit: int = 280) -> str:
    text = _text(statement)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "..."


def _content_hash(statement: str, source_refs: Sequence[str]) -> str:
    payload = {
        "statement": _text(statement).lower(),
        "source_refs": sorted(_dedupe(source_refs)),
    }
    encoded = repr(payload).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _memory_id(content_hash: str) -> str:
    return f"mem_{content_hash[:24]}"


def _source_host(url: str) -> str:
    try:
        return urlsplit(url).netloc.lower()
    except ValueError:
        return ""


def _evidence_lookup(state: Any) -> dict[str, Any]:
    return {
        str(_read(item, "evidence_id", "")): item
        for item in _records(_read(state, "evidence", ()))
        if _read(item, "evidence_id", "")
    }


def _legacy_source_refs(state: Any) -> list[str]:
    refs: list[str] = []
    for section in _records(_read(state, "report_sections", ())):
        refs.extend(_records(_read(section, "sources", ())))
    for result in _records(_read(state, "search_results", ())):
        refs.append(_read(result, "url", ""))
    return _dedupe(refs)


def _evidence_provenance(
    evidence_refs: Sequence[str],
    evidence_by_id: Mapping[str, Any],
) -> tuple[list[str], list[str], MemoryGroundingLevel | None]:
    matched = [evidence_by_id[item] for item in evidence_refs if item in evidence_by_id]
    if not matched:
        return [], [], None

    source_refs = _dedupe(_read(item, "source_url", "") for item in matched)
    document_refs = _dedupe(_read(item, "document_id", "") for item in matched)
    statuses = {str(_read(item, "status", "")) for item in matched}
    grounding: MemoryGroundingLevel = (
        "evidence_grounded" if statuses == {"grounded"} else "evidence_partial"
    )
    return source_refs, document_refs, grounding


def _record(
    *,
    run_id: str,
    topic: str,
    statement: str,
    source_refs: Sequence[str],
    evidence_refs: Sequence[str],
    document_refs: Sequence[str],
    grounding_level: MemoryGroundingLevel,
    now: datetime,
    ttl_days: int,
) -> ResearchMemoryRecord | None:
    clean_sources = _dedupe(source_refs)
    if not _text(statement) or not clean_sources:
        return None

    content_hash = _content_hash(statement, clean_sources)
    created_at = now.isoformat()
    expires_at = (now + timedelta(days=ttl_days)).isoformat()
    return ResearchMemoryRecord(
        memory_id=_memory_id(content_hash),
        content_hash=content_hash,
        run_id=run_id,
        topic=topic,
        statement=_text(statement),
        summary=_summary(statement),
        source_refs=clean_sources,
        evidence_refs=_dedupe(evidence_refs),
        document_refs=_dedupe(document_refs),
        grounding_level=grounding_level,
        provenance={
            "run_id": run_id,
            "topic": topic,
            "grounding_level": grounding_level,
            "source_refs": clean_sources,
            "evidence_refs": _dedupe(evidence_refs),
            "document_refs": _dedupe(document_refs),
        },
        created_at=created_at,
        updated_at=created_at,
        expires_at=expires_at,
    )


def project_research_memories(
    state: Any,
    *,
    ttl_days: int = 30,
    now: datetime | None = None,
    max_records: int = 20,
) -> list[ResearchMemoryRecord]:
    """Project source-backed memories from an already-completed run state."""

    status = _read(state, "status", "")
    stage = _read(state, "current_stage", "")
    if status != "completed" or stage not in {"complete", "completed"}:
        return []

    current = now or datetime.now(timezone.utc)
    run_id = str(_read(state, "run_id", "") or "")
    topic = str(_read(state, "query", "") or _read(state, "research_topic", "") or "")
    evidence_by_id = _evidence_lookup(state)
    legacy_sources = _legacy_source_refs(state)

    records: list[ResearchMemoryRecord] = []
    for finding in _records(_read(state, "findings", ())):
        statement = _read(finding, "statement", "")
        refs = [
            *_records(_read(finding, "evidence_refs", ())),
            *_records(_read(finding, "contradictory_evidence_refs", ())),
        ]
        source_refs, document_refs, grounding = _evidence_provenance(refs, evidence_by_id)
        if not source_refs:
            source_refs = legacy_sources
            grounding = "legacy_source_url"
        record = _record(
            run_id=run_id,
            topic=topic,
            statement=statement,
            source_refs=source_refs,
            evidence_refs=refs,
            document_refs=document_refs,
            grounding_level=grounding or "legacy_source_url",
            now=current,
            ttl_days=ttl_days,
        )
        if record is not None:
            records.append(record)

    if not records:
        for statement in _records(_read(state, "key_findings", ())):
            record = _record(
                run_id=run_id,
                topic=topic,
                statement=str(statement),
                source_refs=legacy_sources,
                evidence_refs=[],
                document_refs=[],
                grounding_level="legacy_source_url",
                now=current,
                ttl_days=ttl_days,
            )
            if record is not None:
                records.append(record)

    deduped: list[ResearchMemoryRecord] = []
    seen: set[str] = set()
    for item in records:
        if item.memory_id not in seen:
            seen.add(item.memory_id)
            deduped.append(item)
        if len(deduped) >= max_records:
            break
    return deduped


def memory_record_to_item(record: ResearchMemoryRecord, *, score: float | None = None) -> MemoryItem:
    """Project a persisted record into the existing V1 State memory item."""

    return MemoryItem(
        memory_id=record.memory_id,
        memory_type="research_memory",
        content=record.summary or record.statement,
        relevance_score=score,
        source=record.source_refs[0] if record.source_refs else None,
        metadata={
            "topic": record.topic,
            "run_id": record.run_id,
            "source_refs": list(record.source_refs),
            "evidence_refs": list(record.evidence_refs),
            "document_refs": list(record.document_refs),
            "grounding_level": record.grounding_level,
            "expires_at": record.expires_at,
        },
        created_at=record.created_at,
    )


def _memory_source_refs(item: MemoryItem | Mapping[str, Any]) -> list[str]:
    metadata = _read(item, "metadata", {}) or {}
    return _dedupe(_read(metadata, "source_refs", ()) if isinstance(metadata, Mapping) else ())


def format_planner_memory_context(items: Sequence[MemoryItem]) -> str:
    """Render bounded prior context for the Planner prompt."""

    lines = []
    for index, item in enumerate(items, 1):
        grounding = item.metadata.get("grounding_level", "unknown")
        sources = _memory_source_refs(item)
        source_text = ", ".join(sources[:2]) if sources else "no source"
        lines.append(
            f"{index}. {item.content} (memory_id={item.memory_id}; grounding={grounding}; sources={source_text})"
        )
    if not lines:
        return "No prior research memory retrieved."
    return (
        "Use these source-backed memories only as prior context, not as proof or final citations:\n"
        + "\n".join(lines)
    )


def build_search_memory_hints(
    topic: str,
    items: Sequence[MemoryItem],
    *,
    limit: int,
) -> list[SearchQuery]:
    """Convert provenance-bearing memories into bounded SearchExecutor hints."""

    hints: list[SearchQuery] = []
    seen_hosts: set[str] = set()
    for item in items:
        for source in _memory_source_refs(item):
            host = _source_host(source)
            if not host or host in seen_hosts:
                continue
            seen_hosts.add(host)
            hints.append(
                SearchQuery(
                    query=f"{topic} site:{host}",
                    purpose=(
                        "Research memory provenance hint; must still be validated "
                        "through SearchExecutor and source scoring"
                    ),
                )
            )
            if len(hints) >= limit:
                return hints
    return hints


def tokenize_for_memory(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(text or "") if len(token) > 1}


def record_search_text(record: ResearchMemoryRecord) -> str:
    hosts = " ".join(_source_host(url) for url in record.source_refs)
    return " ".join([record.topic, record.statement, record.summary, " ".join(record.tags), hosts])
