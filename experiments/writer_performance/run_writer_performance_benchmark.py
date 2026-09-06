"""Manual-only P7 Writer serial vs bounded section scheduling benchmark."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import config
from src.evaluation.dataset import FIXED_EVALUATION_DATASET
from src.evaluation.evaluator import evaluate_run


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a minimal real Writer serial vs bounded=2 A/B benchmark."
    )
    parser.add_argument(
        "--case-id",
        default="ai-regulation-overview",
        help="Evaluation case id to run once per Writer mode.",
    )
    parser.add_argument(
        "--bounded-concurrency",
        type=int,
        default=2,
        help="Bounded Writer section concurrency for the experimental arm.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("experiments/archives/writer-performance-experiments"),
        help="Parent directory for a timestamped JSON and Markdown archive.",
    )
    return parser.parse_args()


@contextmanager
def _temporary_writer_mode(mode: str, concurrency: int):
    previous_mode = config.writer_section_execution_mode
    previous_concurrency = config.writer_section_concurrency
    previous_evidence = {
        name: os.environ.get(name)
        for name in ("EVIDENCE_ANALYZER_ENABLED", "EVIDENCE_ALLOW_PARTIAL_RESULTS")
    }
    config.writer_section_execution_mode = mode
    config.writer_section_concurrency = concurrency
    os.environ["EVIDENCE_ANALYZER_ENABLED"] = "false"
    os.environ["EVIDENCE_ALLOW_PARTIAL_RESULTS"] = "false"
    try:
        yield
    finally:
        config.writer_section_execution_mode = previous_mode
        config.writer_section_concurrency = previous_concurrency
        for name, value in previous_evidence.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _read(value: Any, field: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(field, default)
    return getattr(value, field, default)


def _records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    records: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            records.append(item)
        elif hasattr(item, "model_dump"):
            records.append(item.model_dump(mode="json"))
    return records


def _node_latency(state: Any, node: str) -> float | None:
    trace = _records(_read(state, "agent_trace", ()))
    values = [
        float(event.get("duration_seconds") or 0.0)
        for event in trace
        if event.get("event_type") == "node" and event.get("node") == node
    ]
    return round(sum(values), 6) if values else None


def _writer_llm_seconds(state: Any) -> float:
    details = _records(_read(state, "llm_call_details", ()))
    return round(
        sum(
            float(detail.get("duration") or 0.0)
            for detail in details
            if detail.get("agent") == "ReportWriter"
            and str(detail.get("operation", "")).startswith("write_section_")
        ),
        6,
    )


def _quality_statuses(evaluation: Any) -> dict[str, str]:
    names = {"source_coverage", "citation_integrity", "report_completeness"}
    return {
        metric.name: metric.status
        for metric in evaluation.metrics
        if metric.name in names
    }


async def _run_arm(case: Any, mode: str, concurrency: int) -> dict[str, Any]:
    from src.runner import run_research

    started = perf_counter()
    with _temporary_writer_mode(mode, concurrency):
        state = await run_research(
            case.query,
            verbose=False,
            use_cache=False,
            use_checkpoints=True,
        )
    total_wall = round(perf_counter() - started, 6)
    evaluation = evaluate_run(
        state,
        case=case,
        dataset=FIXED_EVALUATION_DATASET,
        configuration={
            "benchmark": "p7_writer_performance",
            "writer_section_execution_mode": mode,
            "writer_section_concurrency": concurrency,
            "evidence": "disabled",
            "cache": "disabled",
        },
    )
    report = _read(state, "report", None)
    citations = _read(report, "citations", ()) or ()
    return {
        "mode": mode,
        "concurrency": concurrency,
        "status": _read(state, "status", "unknown"),
        "terminal_reason": _read(state, "terminal_reason", None),
        "error": _read(state, "error", None),
        "run_id": _read(state, "run_id", None),
        "total_wall_latency_seconds": total_wall,
        "writer_node_latency_seconds": _node_latency(state, "write_report"),
        "writer_section_llm_seconds": _writer_llm_seconds(state),
        "llm_calls": _read(_read(state, "usage", None), "llm_calls", None),
        "tool_calls": _read(_read(state, "usage", None), "tool_calls", None),
        "total_tokens": _read(_read(state, "usage", None), "total_tokens", None),
        "section_count": len(_read(state, "report_sections", ()) or ()),
        "citation_count": len(citations),
        "quality": _quality_statuses(evaluation),
        "evaluation_outcome": evaluation.outcome,
    }


def _comparison(serial: dict[str, Any], bounded: dict[str, Any]) -> dict[str, Any]:
    regressions: list[str] = []
    if serial["status"] == "completed" and bounded["status"] != "completed":
        regressions.append("bounded failed where serial completed")
    if bounded["section_count"] < serial["section_count"]:
        regressions.append("bounded produced fewer sections")
    if bounded["citation_count"] < serial["citation_count"]:
        regressions.append("bounded produced fewer citations")
    for name, serial_status in serial["quality"].items():
        bounded_status = bounded["quality"].get(name)
        if serial_status == "passed" and bounded_status != "passed":
            regressions.append(f"bounded quality regressed for {name}")
    return {
        "writer_node_latency_delta_seconds": (
            round(bounded["writer_node_latency_seconds"] - serial["writer_node_latency_seconds"], 6)
            if bounded["writer_node_latency_seconds"] is not None
            and serial["writer_node_latency_seconds"] is not None
            else None
        ),
        "total_wall_latency_delta_seconds": round(
            bounded["total_wall_latency_seconds"] - serial["total_wall_latency_seconds"],
            6,
        ),
        "regressions": regressions,
    }


def _render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# P7 Writer Performance A/B Benchmark",
        "",
        "Manual-only observed data. Evidence disabled, cache disabled, no repeatability claim.",
        "",
        f"- Generated at: `{payload['generated_at']}`",
        f"- Case: `{payload['case_id']}`",
        "",
        "| Arm | Status | Total wall s | Writer node s | Writer LLM s | Sections | Citations | Eval |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for item in payload["runs"]:
        lines.append(
            "| {mode}({concurrency}) | {status} | {total:.3f} | {writer} | {llm:.3f} | {sections} | {citations} | {outcome} |".format(
                mode=item["mode"],
                concurrency=item["concurrency"],
                status=item["status"],
                total=item["total_wall_latency_seconds"],
                writer=f"{item['writer_node_latency_seconds']:.3f}" if item["writer_node_latency_seconds"] is not None else "-",
                llm=item["writer_section_llm_seconds"],
                sections=item["section_count"],
                citations=item["citation_count"],
                outcome=item["evaluation_outcome"],
            )
        )
    comparison = payload["comparison"]
    lines.extend(
        [
            "",
            "## Comparison",
            "",
            f"- Writer node latency delta, bounded - serial (s): `{comparison['writer_node_latency_delta_seconds']}`",
            f"- Total wall latency delta, bounded - serial (s): `{comparison['total_wall_latency_delta_seconds']}`",
            f"- Regressions: `{', '.join(comparison['regressions']) or '-'}`",
            "",
        ]
    )
    return "\n".join(lines)


async def _main() -> tuple[Path, Path]:
    args = _arguments()
    if args.bounded_concurrency < 1:
        raise ValueError("--bounded-concurrency must be >= 1")
    cases = {case.case_id: case for case in FIXED_EVALUATION_DATASET.cases}
    if args.case_id not in cases:
        raise ValueError(f"Unknown case id: {args.case_id}")

    case = cases[args.case_id]
    serial = await _run_arm(case, "serial", 1)
    bounded = await _run_arm(case, "bounded", args.bounded_concurrency)
    payload = {
        "benchmark_version": "p7.writer_performance.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "case_id": case.case_id,
        "query": case.query,
        "configuration": {
            "evidence": "disabled",
            "cache": "disabled",
            "production_default": "serial",
            "bounded_concurrency": args.bounded_concurrency,
        },
        "runs": [serial, bounded],
        "comparison": _comparison(serial, bounded),
    }
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output_root / timestamp
    output_dir.mkdir(parents=True, exist_ok=False)
    json_path = output_dir / "benchmark.json"
    report_path = output_dir / "benchmark.md"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(_render_markdown(payload), encoding="utf-8")
    return json_path, report_path


if __name__ == "__main__":
    archived_json, archived_report = asyncio.run(_main())
    print(f"Archived JSON: {archived_json}")
    print(f"Archived report: {archived_report}")
