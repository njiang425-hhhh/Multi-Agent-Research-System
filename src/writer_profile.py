"""Read-only Writer latency profiling from existing call detail / trace data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from src.agent_trace import AgentTraceEvent


@dataclass(frozen=True, slots=True)
class WriterLatencyProfile:
    """A small evidence object; it does not change Writer scheduling."""

    section_attempts: int
    completed_sections: int
    failed_attempts: int
    llm_seconds: float
    wall_seconds: float | None
    serial_fraction: float | None

    @property
    def recommends_concurrency(self) -> bool:
        """P4.3 intentionally never enables concurrency from this signal alone."""
        return False


def profile_writer_latency(
    records: Sequence[Mapping[str, Any]],
    *,
    wall_seconds: float | None = None,
) -> WriterLatencyProfile:
    """Measure section-call cost without inferring business-level parallelism.

    Writer records one entry for every actual attempt. With current sequential
    execution, their sum is the serial LLM critical path; wall time includes
    deterministic composition and callback overhead as well.
    """

    section_records = [
        record
        for record in records
        if str(record.get("agent")) == "ReportWriter"
        and str(record.get("operation", "")).startswith("write_section_")
    ]
    llm_seconds = sum(
        max(0.0, float(record.get("duration") or record.get("duration_seconds") or 0.0))
        for record in section_records
    )
    normalized_wall = max(0.0, wall_seconds) if wall_seconds is not None else None
    serial_fraction = (
        min(1.0, llm_seconds / normalized_wall)
        if normalized_wall is not None and normalized_wall > 0
        else None
    )
    return WriterLatencyProfile(
        section_attempts=len(section_records),
        completed_sections=sum(
            record.get("success") is not False and record.get("status") != "failed"
            for record in section_records
        ),
        failed_attempts=sum(
            record.get("success") is False or record.get("status") == "failed"
            for record in section_records
        ),
        llm_seconds=round(llm_seconds, 6),
        wall_seconds=round(normalized_wall, 6) if normalized_wall is not None else None,
        serial_fraction=round(serial_fraction, 6) if serial_fraction is not None else None,
    )


def profile_writer_trace(
    events: Sequence[AgentTraceEvent | Mapping[str, Any]],
    *,
    wall_seconds: float | None = None,
) -> WriterLatencyProfile:
    """Profile persisted Trace observations without changing their identity."""

    records = [
        event.model_dump() if isinstance(event, AgentTraceEvent) else dict(event)
        for event in events
        if (event.node if isinstance(event, AgentTraceEvent) else event.get("node")) == "write_report"
    ]
    return profile_writer_latency(records, wall_seconds=wall_seconds)
