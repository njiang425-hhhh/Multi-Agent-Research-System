"""Append-only observability contracts and orchestration for Agent Trace.

Trace is deliberately a projection of runtime activity.  It never owns usage
totals, alters routing, or participates in any business decision.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


TraceType = Literal["node", "llm", "tool", "retention"]
TraceStatus = Literal["completed", "failed"]
_REDACTED = "[REDACTED]"
_SENSITIVE_METADATA_KEYS = frozenset(
    {"api_key", "authorization", "cookie", "headers", "password", "payload", "secret", "token"}
)


class TraceRetentionPolicy(BaseModel):
    """Bounded checkpoint trace policy, independent of usage accounting."""

    max_events: int = Field(default=200, ge=8)
    max_metadata_string_chars: int = Field(default=2048, ge=32)


DEFAULT_TRACE_RETENTION_POLICY = TraceRetentionPolicy()


class AgentTraceEvent(BaseModel):
    """One immutable execution observation.

    ``trace_id`` identifies the run and ``event_id`` identifies this execution
    attempt.  The former is intentionally the P3.1 ``run_id``; it is never a
    checkpoint ``thread_id``.  Compatibility fields retain readability of the
    unused pre-P3.2a State shape and permit old serialized trace payloads.
    """

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    trace_id: str = ""
    node: str = ""
    agent: str = ""
    operation: str = ""
    event_type: TraceType = "node"
    attempt: int = Field(default=1, ge=1)
    status: TraceStatus = "completed"
    started_at: str = ""
    ended_at: str = ""
    duration_seconds: float = Field(default=0.0, ge=0.0)
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    # P3.0 placeholder compatibility; tracing code writes the canonical fields.
    agent_name: str = ""
    stage: str = ""
    input_refs: list[str] = Field(default_factory=list)
    output_refs: list[str] = Field(default_factory=list)
    tool_name: str | None = None
    latency_seconds: float | None = None
    token_usage: dict[str, int] = Field(default_factory=dict)
    timestamp: str | None = None


def _sanitize_metadata(value: Any, policy: TraceRetentionPolicy, *, key: str = "") -> Any:
    """Remove sensitive payload values while retaining operational shape."""

    if key.lower() in _SENSITIVE_METADATA_KEYS:
        return _REDACTED
    if isinstance(value, Mapping):
        return {
            str(item_key): _sanitize_metadata(item_value, policy, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_metadata(item, policy) for item in value]
    if isinstance(value, str) and len(value) > policy.max_metadata_string_chars:
        return value[: policy.max_metadata_string_chars] + "…[TRUNCATED]"
    return value


def redact_trace_event(event: AgentTraceEvent, policy: TraceRetentionPolicy) -> AgentTraceEvent:
    """Return a metadata-sanitized copy without changing trace identity."""

    return event.model_copy(update={"metadata": _sanitize_metadata(event.metadata, policy)})


def _signature(event: AgentTraceEvent) -> str:
    return "\x1f".join((event.node, event.event_type, event.operation))


def _retention_offsets(events: Sequence[AgentTraceEvent]) -> dict[str, int]:
    offsets: dict[str, int] = {}
    for event in events:
        offsets[_signature(event)] = max(offsets.get(_signature(event), 0), event.attempt)
        if event.event_type != "retention":
            continue
        raw_offsets = event.metadata.get("attempt_offsets")
        if not isinstance(raw_offsets, Mapping):
            continue
        for signature, attempt in raw_offsets.items():
            if isinstance(attempt, int) and attempt > 0:
                offsets[str(signature)] = max(offsets.get(str(signature), 0), attempt)
    return offsets


def _retention_marker(
    dropped: Sequence[AgentTraceEvent],
    offsets: Mapping[str, int],
    prior_marker: AgentTraceEvent | None = None,
) -> AgentTraceEvent:
    reference = prior_marker or (dropped[-1] if dropped else None)
    now = _iso_now()
    prior_dropped = (
        int(prior_marker.metadata.get("dropped_event_count") or 0)
        if prior_marker is not None
        else 0
    )
    metadata = {
        "dropped_event_count": prior_dropped + len(dropped),
        "attempt_offsets": dict(offsets),
    }
    if prior_marker is not None:
        return prior_marker.model_copy(update={"ended_at": now.isoformat(), "metadata": metadata})
    return AgentTraceEvent(
        trace_id=reference.trace_id if reference else "",
        node="trace_retention",
        agent="Runtime",
        operation="compact_trace",
        event_type="retention",
        attempt=1,
        status="completed",
        started_at=reference.started_at if reference and reference.started_at else now.isoformat(),
        ended_at=now.isoformat(),
        metadata=metadata,
    )


def retain_trace_events(
    events: Sequence[AgentTraceEvent],
    policy: TraceRetentionPolicy,
) -> list[AgentTraceEvent]:
    """Bound a checkpoint trace while preserving future retry attempt identity.

    A compacted marker records maximum attempts for discarded signatures. The
    latest node observation per node is retained first so completed-run trace
    evaluation continues to observe Graph V1 node coverage. Usage never reads
    Agent Trace, therefore compaction cannot alter usage totals.
    """

    sanitized = [redact_trace_event(event, policy) for event in events]
    ordinary = [event for event in sanitized if event.event_type != "retention"]
    prior_markers = [event for event in sanitized if event.event_type == "retention"]
    if len(sanitized) <= policy.max_events:
        return sanitized

    offsets = _retention_offsets(sanitized)
    node_latest: list[AgentTraceEvent] = []
    seen_nodes: set[str] = set()
    for event in reversed(ordinary):
        if event.event_type == "node" and event.node not in seen_nodes:
            node_latest.append(event)
            seen_nodes.add(event.node)
    node_latest.reverse()
    retained_ids = {event.event_id for event in node_latest}
    capacity = max(0, policy.max_events - len(node_latest) - 1)
    tail: list[AgentTraceEvent] = []
    for event in reversed(ordinary):
        if event.event_id in retained_ids:
            continue
        if len(tail) >= capacity:
            break
        tail.append(event)
        retained_ids.add(event.event_id)
    tail.reverse()
    retained = [*node_latest, *tail]
    dropped = [event for event in ordinary if event.event_id not in retained_ids]
    # Existing markers are merged into this one so repeated resume/retention
    # stays bounded too.
    marker = _retention_marker(dropped, offsets, prior_markers[0] if prior_markers else None)
    return [marker, *retained]


def append_trace_events(
    existing: Sequence[AgentTraceEvent | Mapping[str, Any]] | None,
    additions: Sequence[AgentTraceEvent],
    *,
    retention_policy: TraceRetentionPolicy = DEFAULT_TRACE_RETENTION_POLICY,
) -> list[AgentTraceEvent]:
    """Append events without replacing checkpoint history.

    A resumed graph receives the persisted list as ``existing``.  Only exact
    event IDs are de-duplicated, making repeated merge/application idempotent
    without collapsing separate retry attempts.
    """
    merged = [
        item if isinstance(item, AgentTraceEvent) else AgentTraceEvent.model_validate(item)
        for item in (existing or ())
    ]
    known_ids = {item.event_id for item in merged}
    for event in additions:
        if event.event_id not in known_ids:
            merged.append(event)
            known_ids.add(event.event_id)
    return retain_trace_events(merged, retention_policy)


def _attempt(existing: Sequence[AgentTraceEvent], *, node: str, event_type: TraceType, operation: str) -> int:
    signature = "\x1f".join((node, event_type, operation))
    return 1 + _retention_offsets(existing).get(signature, 0)


def _iso_now() -> datetime:
    return datetime.now(timezone.utc)


def _base_event(
    *,
    run_id: str,
    node: str,
    agent: str,
    operation: str,
    event_type: TraceType,
    attempt: int,
    status: TraceStatus,
    started: datetime,
    ended: datetime,
    error: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> AgentTraceEvent:
    duration = max(0.0, (ended - started).total_seconds())
    return AgentTraceEvent(
        trace_id=run_id,
        node=node,
        agent=agent,
        operation=operation,
        event_type=event_type,
        attempt=attempt,
        status=status,
        started_at=started.isoformat(),
        ended_at=ended.isoformat(),
        duration_seconds=round(duration, 6),
        error=error,
        metadata=_sanitize_metadata(dict(metadata or {}), DEFAULT_TRACE_RETENTION_POLICY),
        # Keep older readers useful without asking them to understand the new API.
        agent_name=agent,
        stage=node,
        latency_seconds=round(duration, 6),
        timestamp=ended.isoformat(),
    )


def _llm_events(
    records: Sequence[Mapping[str, Any]],
    *,
    existing: Sequence[AgentTraceEvent],
    run_id: str,
    node: str,
) -> list[AgentTraceEvent]:
    events: list[AgentTraceEvent] = []
    all_events = list(existing)
    now = _iso_now()
    for record in records:
        agent = str(record.get("agent") or "LLM")
        operation = str(record.get("operation") or "invoke")
        # deterministic_v2 stores its runtime summary in the legacy detail
        # list for compatibility, but it is not an LLM invocation.
        if operation == "deterministic_search":
            continue
        duration = max(0.0, float(record.get("duration") or 0.0))
        success = record.get("success") is not False
        metadata = {
            key: value
            for key, value in record.items()
            if key not in {"agent", "operation", "duration", "attempt", "success", "error", "tool_invocations"}
        }
        event = _base_event(
            run_id=run_id,
            node=node,
            agent=agent,
            operation=operation,
            event_type="llm",
            attempt=int(record.get("attempt") or _attempt(all_events, node=node, event_type="llm", operation=operation)),
            status="completed" if success else "failed",
            started=now - timedelta(seconds=duration),
            ended=now,
            error=str(record["error"]) if record.get("error") else None,
            metadata=metadata,
        )
        events.append(event)
        all_events.append(event)
    return events


def _tool_events(
    records: Sequence[Mapping[str, Any]],
    *,
    existing: Sequence[AgentTraceEvent],
    run_id: str,
    node: str,
) -> list[AgentTraceEvent]:
    events: list[AgentTraceEvent] = []
    all_events = list(existing)
    now = _iso_now()
    for record in records:
        operation = str(record.get("operation") or "invoke")
        duration = max(0.0, float(record.get("duration") or 0.0))
        success = record.get("success") is not False
        event = _base_event(
            run_id=run_id,
            node=node,
            agent="ResearchSearcher",
            operation=operation,
            event_type="tool",
            attempt=int(record.get("attempt") or _attempt(all_events, node=node, event_type="tool", operation=operation)),
            status="completed" if success else "failed",
            started=now - timedelta(seconds=duration),
            ended=now,
            error=str(record["error"]) if record.get("error") else None,
            metadata={key: value for key, value in record.items() if key not in {"operation", "attempt", "success", "duration", "error"}},
            
        )
        event.tool_name = operation
        events.append(event)
        all_events.append(event)
    return events


async def trace_node_execution(
    state: Any,
    *,
    node: str,
    agent: str,
    operation: str,
    execute: Callable[[Any], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Run one Graph node and append its node/LLM/tool trace projection."""
    existing = append_trace_events(getattr(state, "agent_trace", ()), ())
    started_at = _iso_now()
    started_tick = perf_counter()
    try:
        patch = await execute(state)
    except Exception as exc:
        ended_at = _iso_now()
        node_event = _base_event(
            run_id=getattr(state, "run_id", ""), node=node, agent=agent,
            operation=operation, event_type="node",
            attempt=_attempt(existing, node=node, event_type="node", operation=operation),
            status="failed", started=started_at, ended=ended_at, error=str(exc),
        )
        # Keep the original exception and P3.1 re-raise semantics.  The runner
        # consumes this private payload to persist the failed attempt alongside
        # its existing terminal lifecycle patch.
        setattr(exc, "_agent_trace_events", append_trace_events(existing, [node_event]))
        raise

    ended_at = _iso_now()
    records = list(patch.get("llm_call_details") or ())[len(getattr(state, "llm_call_details", ())):]
    llm_events = _llm_events(records, existing=existing, run_id=getattr(state, "run_id", ""), node=node)
    tool_records = [
        invocation
        for record in records
        for invocation in (record.get("tool_invocations") or ())
        if isinstance(invocation, Mapping)
    ]
    tool_events = _tool_events(tool_records, existing=[*existing, *llm_events], run_id=getattr(state, "run_id", ""), node=node)
    node_error = patch.get("error")
    node_event = _base_event(
        run_id=getattr(state, "run_id", ""), node=node, agent=agent, operation=operation,
        event_type="node", attempt=_attempt(existing, node=node, event_type="node", operation=operation),
        status="failed" if node_error else "completed", started=started_at, ended=ended_at,
        error=str(node_error) if node_error else None,
        metadata={"execution_duration_seconds": round(perf_counter() - started_tick, 6)},
    )
    patch = dict(patch)
    patch["agent_trace"] = append_trace_events(existing, [*llm_events, *tool_events, node_event])
    return patch
