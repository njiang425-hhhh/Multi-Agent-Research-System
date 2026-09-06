"""Manual P16 showcase orchestration and archival for existing ResearchOS V1 runs.

The module observes an injected runner and evaluates its returned state after the
run.  It does not construct a Graph, modify a plan, or turn evaluation results
into runtime decisions.
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from pydantic import BaseModel, Field

from src.config import config
from src.evaluation.contracts import EvaluationCase, EvaluationDataset, ResearchQualityRubric
from src.evaluation.evaluator import evaluate_run
from src.evaluation.planning_quality import (
    PlanningQualityCase,
    PlanningQualityDataset,
    evaluate_planning_quality,
)
from src.evaluation.research_coverage import (
    ResearchCoverageCase,
    ResearchCoverageDataset,
    ResearchCoverageObservation,
    evaluate_research_coverage,
)
from src.state import Document, ResearchPlan, SearchResult
from src.state_compat import canonical_report, canonical_report_text


SHOWCASE_VERSION = "p16.showcase.v1"
_ERROR_PATTERN = re.compile(r"(?i)(api[_-]?key|authorization|token|secret)\s*[:=]\s*\S+")
_MISSING = object()


class ShowcaseCase(BaseModel):
    """One stable manual task and lexical facets for read-only evaluators."""

    case_id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    query: str = Field(min_length=1)
    description: str = ""
    planning_objective_facets: list[str] = Field(min_length=1)
    planning_outline_facets: list[str] = Field(min_length=1)
    coverage_facets: list[str] = Field(min_length=1)
    quality_rubric: ResearchQualityRubric = Field(default_factory=ResearchQualityRubric)

    def evaluation_case(self) -> EvaluationCase:
        return EvaluationCase(
            case_id=self.case_id,
            query=self.query,
            description=self.description,
            tags=["p16", self.category],
            quality_rubric=self.quality_rubric,
        )


SHOWCASE_CASES: tuple[ShowcaseCase, ...] = (
    ShowcaseCase(
        case_id="comparison-ai-governance",
        category="comparison",
        query="Compare the EU AI Act and current United States federal AI governance approach for enterprise deployment.",
        description="Cross-jurisdiction comparison with primary policy and implementation sources.",
        planning_objective_facets=["EU", "United States", "enterprise"],
        planning_outline_facets=["EU", "United States", "comparison"],
        coverage_facets=["eu", "united", "states", "enterprise"],
        quality_rubric=ResearchQualityRubric(
            min_distinct_sources=2,
            min_grounded_citations=1,
            min_report_sections=2,
            min_report_characters=120,
        ),
    ),
    ShowcaseCase(
        case_id="evidence-clinical-ai-screening",
        category="evidence_research",
        query="What current clinical evidence supports and limits AI-assisted mammography screening implementation?",
        description="Evidence-focused clinical research with benefits, limitations, and implementation constraints.",
        planning_objective_facets=["clinical", "evidence", "implementation"],
        planning_outline_facets=["evidence", "limitations", "implementation"],
        coverage_facets=["clinical", "evidence", "implementation"],
        quality_rubric=ResearchQualityRubric(
            min_distinct_sources=2,
            min_grounded_citations=1,
            min_report_sections=2,
            min_report_characters=120,
        ),
    ),
    ShowcaseCase(
        case_id="risk-regulated-ai-agents",
        category="risk_implementation",
        query="What are the main risks and implementation controls for deploying customer-support AI agents in a regulated enterprise?",
        description="Operational risk and control design research without any action execution.",
        planning_objective_facets=["risks", "controls", "regulated"],
        planning_outline_facets=["risks", "controls", "implementation"],
        coverage_facets=["risks", "controls", "regulated", "enterprise"],
        quality_rubric=ResearchQualityRubric(
            min_distinct_sources=2,
            min_grounded_citations=1,
            min_report_sections=2,
            min_report_characters=120,
        ),
    ),
    ShowcaseCase(
        case_id="trend-ai-semiconductor-supply",
        category="trend_industry",
        query="What trends will shape AI data-center semiconductor supply chains through 2026?",
        description="Industry trend research spanning demand, supply, and resilience constraints.",
        planning_objective_facets=["AI", "supply", "2026"],
        planning_outline_facets=["trends", "supply", "risks"],
        coverage_facets=["ai", "data", "semiconductor", "supply"],
        quality_rubric=ResearchQualityRubric(
            min_distinct_sources=2,
            min_grounded_citations=1,
            min_report_sections=2,
            min_report_characters=120,
        ),
    ),
)

SHOWCASE_EVALUATION_DATASET = EvaluationDataset(
    dataset_id="researchos_p16_showcase",
    version=SHOWCASE_VERSION,
    cases=[item.evaluation_case() for item in SHOWCASE_CASES],
)
SHOWCASE_PLANNING_DATASET = PlanningQualityDataset(
    dataset_id="researchos_p16_showcase_planning",
    version=SHOWCASE_VERSION,
    cases=[
        PlanningQualityCase(
            case_id=item.case_id,
            query=item.query,
            objective_facets=item.planning_objective_facets,
            outline_facets=item.planning_outline_facets,
        )
        for item in SHOWCASE_CASES
    ],
)
SHOWCASE_COVERAGE_DATASET = ResearchCoverageDataset(
    dataset_id="researchos_p16_showcase_coverage",
    version=SHOWCASE_VERSION,
    cases=[
        ResearchCoverageCase(
            case_id=item.case_id,
            query=item.query,
            required_facets=item.coverage_facets,
        )
        for item in SHOWCASE_CASES
    ],
)

ShowcaseRunner = Callable[[ShowcaseCase], Any | Awaitable[Any]]


def _read(value: Any, field: str, default: Any = _MISSING) -> Any:
    return value.get(field, default) if isinstance(value, Mapping) else getattr(value, field, default)


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _redact_error(error: Any) -> str | None:
    if not error:
        return None
    return _ERROR_PATTERN.sub("[REDACTED]", str(error))[:1024]


def _coerce_plan(state: Any) -> ResearchPlan | None:
    raw_plan = _read(state, "research_plan", None) or _read(state, "plan", None)
    if raw_plan is None:
        return None
    if isinstance(raw_plan, ResearchPlan):
        return raw_plan
    try:
        return ResearchPlan.model_validate(raw_plan)
    except Exception:
        return None


def _models(values: Any, model: type[SearchResult] | type[Document]) -> list[Any]:
    items: list[Any] = []
    for value in values or ():
        try:
            items.append(value if isinstance(value, model) else model.model_validate(value))
        except Exception:
            continue
    return items


def _executed_queries(
    state: Any,
    results: Sequence[SearchResult],
    documents: Sequence[Document],
) -> list[str]:
    observed = [result.query for result in results if result.query.strip()]
    if not observed:
        observed.extend(
            str(document.metadata.get("search_query") or "").strip()
            for document in documents
        )
    if observed:
        return list(dict.fromkeys(observed))
    diagnostics = _read(state, "search_diagnostics", ())
    for diagnostic in diagnostics if isinstance(diagnostics, (list, tuple)) else ():
        if isinstance(diagnostic, Mapping):
            query = diagnostic.get("supplementary_query")
            if isinstance(query, str) and query.strip():
                observed.append(query)
    return list(dict.fromkeys(observed))


def _search_statistics(state: Any) -> dict[str, int | bool]:
    """Read SearchExecutor's existing authoritative count projection."""

    diagnostics = _read(state, "search_diagnostics", ())
    diagnostics = diagnostics if isinstance(diagnostics, (list, tuple)) else ()
    entries = [
        item
        for item in diagnostics
        if isinstance(item, Mapping) and item.get("kind") == "search_execution"
    ]
    return {
        "available": bool(entries),
        "search_calls": sum(int(item.get("search_calls", 0) or 0) for item in entries),
        "extract_calls": sum(int(item.get("extract_calls", 0) or 0) for item in entries),
    }


def _metric_status(result: Any, name: str) -> str:
    for metric in _read(result, "metrics", ()) or ():
        if _read(metric, "name", "") == name:
            return str(_read(metric, "status", "unavailable"))
    return "unavailable"


def _adaptive_summary(state: Any) -> dict[str, Any]:
    diagnostics = _read(state, "search_diagnostics", ())
    adaptive = next(
        (
            item for item in reversed(diagnostics or ())
            if isinstance(item, Mapping) and item.get("kind") == "adaptive_search"
        ),
        None,
    )
    if adaptive is None:
        return {"available": False, "triggered": False, "outcome": "absent"}
    before = adaptive.get("primary_coverage") if isinstance(adaptive.get("primary_coverage"), Mapping) else {}
    after = adaptive.get("post_coverage") if isinstance(adaptive.get("post_coverage"), Mapping) else before
    return {
        "available": True,
        "triggered": bool(adaptive.get("rounds_attempted", 0)),
        "outcome": adaptive.get("outcome", "unknown"),
        "trigger_reason": adaptive.get("skip_reason") or (
            "incomplete_primary_coverage" if adaptive.get("rounds_attempted", 0) else None
        ),
        "supplementary_query": adaptive.get("supplementary_query"),
        "before_coverage": before,
        "after_coverage": after,
        "no_progress": adaptive.get("outcome") == "no_progress",
        "error": _redact_error(adaptive.get("supplementary_error")),
    }


def _memory_summary(state: Any, enabled: bool) -> dict[str, Any]:
    diagnostics = _read(state, "memory_diagnostics", {})
    diagnostics = diagnostics if isinstance(diagnostics, Mapping) else {}
    return {
        "enabled_for_case": enabled,
        "available": bool(diagnostics),
        "retrieved_count": int(diagnostics.get("retrieved_count", 0) or 0),
        "retrieved_memory_ids": list(diagnostics.get("retrieved_memory_ids", []) or []),
        "planner_context_memory_ids": list(diagnostics.get("planner_context_memory_ids", []) or []),
        "searcher_site_hints": list(diagnostics.get("searcher_site_hints", []) or []),
        "write_count": int(diagnostics.get("write_count", 0) or 0),
        "diagnostics": _jsonable(diagnostics),
    }


def _case_evaluations(case: ShowcaseCase, state: Any) -> dict[str, Any]:
    """Run existing pure evaluators over a state copy, preserving read-only scope."""

    observed_state = deepcopy(_jsonable(state))
    plan = _coerce_plan(observed_state)
    run_evaluation = evaluate_run(
        observed_state,
        case=case.evaluation_case(),
        dataset=SHOWCASE_EVALUATION_DATASET,
        configuration={"execution": "p16_showcase_archive", "read_only": True},
    )
    if plan is None:
        return {
            "run": run_evaluation.model_dump(mode="json"),
            "planning": {"status": "unavailable", "reason": "research plan is absent"},
            "research_coverage": {"status": "unavailable", "reason": "research plan is absent"},
        }

    results = _models(_read(observed_state, "search_results", ()), SearchResult)
    documents = _models(_read(observed_state, "documents", ()), Document)
    planning = evaluate_planning_quality(
        {case.case_id: plan},
        dataset=PlanningQualityDataset(
            dataset_id=SHOWCASE_PLANNING_DATASET.dataset_id,
            version=SHOWCASE_PLANNING_DATASET.version,
            cases=[item for item in SHOWCASE_PLANNING_DATASET.cases if item.case_id == case.case_id],
        ),
        configuration={"execution": "p16_showcase_archive", "read_only": True},
    )
    coverage = evaluate_research_coverage(
        {
            case.case_id: ResearchCoverageObservation(
                plan=plan,
                executed_queries=_executed_queries(observed_state, results, documents),
                search_results=results,
                documents=documents,
            )
        },
        dataset=ResearchCoverageDataset(
            dataset_id=SHOWCASE_COVERAGE_DATASET.dataset_id,
            version=SHOWCASE_COVERAGE_DATASET.version,
            cases=[item for item in SHOWCASE_COVERAGE_DATASET.cases if item.case_id == case.case_id],
        ),
        fake_payloads={"source": "observed_showcase_state", "case_id": case.case_id},
        configuration={"execution": "p16_showcase_archive", "read_only": True},
    )
    return {
        "run": run_evaluation.model_dump(mode="json"),
        "planning": planning.model_dump(mode="json"),
        "research_coverage": coverage.model_dump(mode="json"),
    }


def _case_record(case: ShowcaseCase, state: Any, wall_seconds: float, memory_enabled: bool) -> dict[str, Any]:
    state_payload = _jsonable(state)
    results = _models(_read(state_payload, "search_results", ()), SearchResult)
    documents = _models(_read(state_payload, "documents", ()), Document)
    evaluations = _case_evaluations(case, state_payload)
    run_evaluation = evaluations["run"]
    return {
        "archive_version": SHOWCASE_VERSION,
        "data_kind": "observed_with_read_only_evaluations",
        "case": _jsonable(case),
        "input_query": case.query,
        "status": _read(state_payload, "status", "unknown"),
        "current_stage": _read(state_payload, "current_stage", "unknown"),
        "terminal_reason": _read(state_payload, "terminal_reason", None),
        "error": _redact_error(_read(state_payload, "error", None)),
        "wall_time_seconds": round(max(wall_seconds, 0.0), 6),
        "research_plan": _jsonable(_coerce_plan(state_payload)),
        "executed_search_queries": _executed_queries(state_payload, results, documents),
        "search_results": _jsonable(results),
        "documents": _jsonable(documents),
        "adaptive": _adaptive_summary(state_payload),
        "search_statistics": _search_statistics(state_payload),
        "memory": _memory_summary(state_payload, memory_enabled),
        "findings": _jsonable(_read(state_payload, "findings", ())),
        "key_findings": _jsonable(_read(state_payload, "key_findings", ())),
        "final_report": canonical_report_text(state_payload) or "",
        "citations": _jsonable((canonical_report(state_payload).citations if canonical_report(state_payload) else ())),
        "trace": _jsonable(_read(state_payload, "agent_trace", ())),
        "usage": _jsonable(_read(state_payload, "usage", {})),
        "evaluations": evaluations,
        "core_metrics": {
            "planning_quality": _jsonable(_read(evaluations.get("planning"), "summary", {})),
            "research_coverage": _jsonable(_read(evaluations.get("research_coverage"), "summary", {})),
            "source_coverage": _metric_status(run_evaluation, "source_coverage"),
            "citation_integrity": _metric_status(run_evaluation, "citation_integrity"),
            "evidence_grounding": _metric_status(run_evaluation, "evidence_grounding"),
            "report_completeness": _metric_status(run_evaluation, "report_completeness"),
        },
    }


@contextmanager
def _temporary_memory_case(enabled: bool, store_path: Path | None):
    """Scope the existing P8 toggle to one explicitly selected manual run."""

    original_enabled = config.research_memory_enabled
    original_path = config.research_memory_store_path
    if enabled:
        config.research_memory_enabled = True
        if store_path is not None:
            config.research_memory_store_path = str(store_path)
    try:
        yield
    finally:
        config.research_memory_enabled = original_enabled
        config.research_memory_store_path = original_path


async def run_showcase(
    run_case: ShowcaseRunner,
    *,
    cases: Sequence[ShowcaseCase] = SHOWCASE_CASES,
    memory_case_id: str | None = None,
    memory_store_path: Path | None = None,
) -> dict[str, Any]:
    """Run selected cases sequentially and retain failure observations per case."""

    selected = list(cases)
    available = {item.case_id for item in selected}
    if memory_case_id is not None and memory_case_id not in available:
        raise ValueError("memory_case_id must name one selected showcase case")

    records: list[dict[str, Any]] = []
    for case in selected:
        memory_enabled = case.case_id == memory_case_id
        started = perf_counter()
        try:
            with _temporary_memory_case(memory_enabled, memory_store_path):
                state = run_case(case)
                if inspect.isawaitable(state):
                    state = await state
        except Exception as exc:
            state = {
                "query": case.query,
                "research_topic": case.query,
                "status": "failed",
                "current_stage": "failed",
                "terminal_reason": "unhandled_exception",
                "error": _redact_error(exc),
                "agent_trace": [],
                "usage": {},
            }
        records.append(_case_record(case, state, perf_counter() - started, memory_enabled))

    return {
        "showcase_version": SHOWCASE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_kind": "observed_with_read_only_evaluations",
        "dataset": {
            "showcase_cases": [item.model_dump(mode="json") for item in selected],
            "evaluation_dataset": SHOWCASE_EVALUATION_DATASET.model_dump(mode="json"),
            "planning_dataset": SHOWCASE_PLANNING_DATASET.model_dump(mode="json"),
            "coverage_dataset": SHOWCASE_COVERAGE_DATASET.model_dump(mode="json"),
        },
        "configuration": {
            "execution": "manual_or_injected_showcase_runner",
            "memory_default": "off",
            "memory_case_id": memory_case_id,
            "adaptive_max_rounds": 1,
            "evaluations_read_only": True,
        },
        "cases": records,
        "summary": {
            "total_cases": len(records),
            "completed_cases": sum(item["status"] == "completed" for item in records),
            "failed_cases": sum(item["status"] == "failed" for item in records),
            "adaptive_triggered_cases": sum(item["adaptive"]["triggered"] for item in records),
            "memory_enabled_cases": sum(item["memory"]["enabled_for_case"] for item in records),
        },
    }


def render_showcase_summary(result: Mapping[str, Any]) -> str:
    """Render a small human index; per-case JSON is the source of record."""

    summary = result["summary"]
    lines = [
        "# P16 End-to-End Showcase",
        "",
        "Observed DeepSeek/Tavily (or injected fake) runs with post-run, read-only P5/P9/P10 evaluation. These metrics are descriptive and do not control runtime behavior or claim provider-quality improvement.",
        "",
        f"- Generated at: `{result['generated_at']}`",
        f"- Cases: `{summary['total_cases']}`; completed: `{summary['completed_cases']}`; failed: `{summary['failed_cases']}`",
        f"- Adaptive triggered: `{summary['adaptive_triggered_cases']}`; memory enabled cases: `{summary['memory_enabled_cases']}`",
        "",
        "| Case | Status | Search | Extract | LLM | Wall s | Citation integrity | Evidence grounding |",
        "|---|---|---:|---:|---:|---:|---|---|",
    ]
    for item in result["cases"]:
        metrics = item["core_metrics"]
        lines.append(
            "| {case} | {status} | {search} | {extract} | {llm} | {wall:.3f} | {citation} | {grounding} |".format(
                case=item["case"]["case_id"],
                status=item["status"],
                search=item["search_statistics"]["search_calls"] if item["search_statistics"]["available"] else "n/a",
                extract=item["search_statistics"]["extract_calls"] if item["search_statistics"]["available"] else "n/a",
                llm=item["usage"].get("llm_calls", "n/a"),
                wall=item["wall_time_seconds"],
                citation=metrics["citation_integrity"],
                grounding=metrics["evidence_grounding"],
            )
        )
    lines.extend(
        [
            "",
            "## Boundaries",
            "",
            "- Graph V1, Planner/Searcher/Synthesizer/Writer contracts, Runtime ownership, and Writer input are unchanged.",
            "- Adaptive diagnostics only observe the existing single P14 supplementary-search bound.",
            "- Memory is off unless one explicit case is selected; it remains bounded local lexical memory and never reaches Writer.",
            "- P9/P10 lexical metrics and P5 report metrics are read-only archive observations, not quality gates or provider claims.",
            "",
        ]
    )
    return "\n".join(lines)


def _render_case_report(record: Mapping[str, Any]) -> str:
    report = str(record.get("final_report") or "")
    if report.strip():
        return report.rstrip() + "\n"
    return "# {case}\n\nNo final report was produced.\n".format(case=record["case"]["case_id"])


def archive_showcase(result: Mapping[str, Any], output_directory: str | Path) -> tuple[Path, Path]:
    """Write one reproducible P16 archive directory without rerunning anything."""

    directory = Path(output_directory)
    directory.mkdir(parents=True, exist_ok=False)
    cases_directory = directory / "cases"
    cases_directory.mkdir()
    summary_json = directory / "summary.json"
    summary_markdown = directory / "summary.md"
    summary_json.write_text(json.dumps(_jsonable(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary_markdown.write_text(render_showcase_summary(result), encoding="utf-8")
    for record in result["cases"]:
        case_id = record["case"]["case_id"]
        (cases_directory / f"{case_id}.json").write_text(
            json.dumps(_jsonable(record), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (cases_directory / f"{case_id}.report.md").write_text(
            _render_case_report(record), encoding="utf-8"
        )
    return summary_json, summary_markdown


def manual_deepseek_tavily_showcase_runner() -> ShowcaseRunner:
    """Return the explicit real runner; CI must inject fakes instead."""

    if config.model_provider != "deepseek" or config.search_provider != "tavily":
        raise RuntimeError("manual showcase requires MODEL_PROVIDER=deepseek and SEARCH_PROVIDER=tavily")
    if not config.deepseek_api_key or not config.tavily_api_key:
        raise RuntimeError("manual showcase requires DEEPSEEK_API_KEY and TAVILY_API_KEY")

    async def run_case(case: ShowcaseCase) -> Any:
        from src.runner import run_research

        return await run_research(
            case.query,
            verbose=False,
            use_cache=False,
            use_checkpoints=True,
        )

    return run_case
