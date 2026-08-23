"""Deterministic, local-only showcase for Research Memory V1."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from src.memory.projection import (
    build_search_memory_hints,
    format_planner_memory_context,
    memory_retrieval_items,
    project_research_memories,
)
from src.memory.store import ResearchMemoryStore
from src.search.coverage import measure_search_coverage
from src.state import Finding, ResearchPlan, ResearchState, SearchQuery, SearchResult


def _completed_run_a() -> ResearchState:
    return ResearchState(
        research_topic="AI regulation",
        query="AI regulation",
        run_id="memory-demo-run-a",
        findings=[
            Finding(
                finding_id="memory-demo-finding",
                statement="EU AI Act enforcement uses risk tiers and provider obligations.",
            )
        ],
        search_results=[
            SearchResult(
                query="EU AI Act risk tiers",
                title="EU source",
                url="https://eu.example/ai-act",
                snippet="Risk tiers and enforcement obligations",
                content="Source-backed demo content.",
            )
        ],
        status="completed",
        current_stage="complete",
    )


def _run_demo(store_path: Path, *, store_label: str) -> dict[str, Any]:
    store = ResearchMemoryStore(store_path, max_records=20)
    records = project_research_memories(_completed_run_a())
    persisted = store.upsert_many(records)

    run_b_query = "AI regulation enforcement risk tiers"
    plan = ResearchPlan(
        topic=run_b_query,
        objectives=["Compare enforcement obligations"],
        search_queries=[
            SearchQuery(query="EU AI Act enforcement obligations", purpose="primary"),
            SearchQuery(query="EU AI Act risk tiers", purpose="primary"),
        ],
        report_outline=["Summary"],
    )
    run_b_results = [
        SearchResult(
            query=query.query,
            title="Deterministic demo source",
            url=f"https://demo.example/{index}",
            snippet="deterministic coverage",
            content="deterministic extracted content",
        )
        for index, query in enumerate(plan.search_queries, 1)
    ]
    coverage = measure_search_coverage(plan, run_b_results)
    matches = store.retrieve(run_b_query, limit=2)
    retrieved_items, retrieval = memory_retrieval_items(run_b_query, matches)
    hints = build_search_memory_hints(run_b_query, retrieved_items, limit=2)
    hint_hosts = [hint.query.rsplit("site:", 1)[-1] for hint in hints if "site:" in hint.query]
    repeated_hint_hosts = sorted(
        {host for host in hint_hosts if hint_hosts.count(host) > 1}
    )

    unrelated_query = "coastal flood adaptation planning"
    unrelated_matches = store.retrieve(unrelated_query, limit=2)
    _, unrelated_retrieval = memory_retrieval_items(unrelated_query, unrelated_matches)

    planned_queries = [query.query for query in plan.search_queries]
    return {
        "demo_mode": "explicit_local_fake",
        "store_path": store_label,
        "run_a": {
            "outcome": "completed_run_written",
            "memory_write_count": len(persisted),
            "memory_ids": [record.memory_id for record in persisted],
        },
        "memory_off": {
            "retrieved_memory_count": 0,
            "retrieved_memory_ids": [],
            "planner_context_memory_ids": [],
            "searcher_site_hints": [],
        },
        "memory_on_run_b": {
            "query": run_b_query,
            "retrieved_memory_count": len(retrieved_items),
            "retrieved_memory_ids": [item.memory_id for item in retrieved_items],
            "retrieval": retrieval,
            "planner_context_memory_ids": [item.memory_id for item in retrieved_items],
            "planner_context_present": bool(format_planner_memory_context(retrieved_items)),
            "searcher_site_hints": [hint.query for hint in hints],
            "planned_queries": planned_queries,
            "result_query_coverage": coverage.result_query_coverage,
            "extracted_query_coverage": coverage.extracted_query_coverage,
        },
        "unrelated_run_c": {
            "query": unrelated_query,
            "retrieved_memory_count": len(unrelated_retrieval),
            "retrieval": unrelated_retrieval,
        },
        "comparison": {
            "planned_query_overlap": 1.0,
            "planned_query_novelty": [],
            "repeated_source_hint_hosts": repeated_hint_hosts,
            "coverage_delta": {
                "result_query_coverage": 0.0,
                "extracted_query_coverage": 0.0,
            },
            "interpretation": (
                "The demo observes lexical memory injection and provenance only; "
                "it does not claim provider or report-quality improvement."
            ),
        },
    }


def run_memory_demo(store_path: str | Path | None = None) -> dict[str, Any]:
    """Run the P15 A/B/C demo without enabling Memory for normal research runs."""

    if store_path is not None:
        path = Path(store_path)
        return _run_demo(path, store_label=str(path))
    cache_dir = Path(".cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(
        prefix="researchos-memory-demo-",
        dir=cache_dir,
    ) as temporary_directory:
        return _run_demo(Path(temporary_directory) / "memory.db", store_label="temporary")
