"""Fake-only coverage for the manual P16 showcase archive runner."""

import asyncio
import copy
import json

from src.evaluation.showcase import (
    SHOWCASE_CASES,
    archive_showcase,
    render_showcase_summary,
    run_showcase,
)


def _trace(run_id: str) -> list[dict[str, object]]:
    return [
        {
            "trace_id": run_id,
            "node": node,
            "agent": f"Fake{node}",
            "operation": node,
            "event_type": "node",
            "attempt": 1,
            "status": "completed",
            "started_at": "2026-08-23T00:00:00+00:00",
            "ended_at": "2026-08-23T00:00:01+00:00",
            "duration_seconds": 0.1,
        }
        for node in ("plan", "search", "synthesize", "write_report")
    ]


def _state(case, *, memory: bool = False) -> dict[str, object]:
    run_id = f"fake-{case.case_id}"
    facets = " ".join(case.coverage_facets)
    first_url = f"https://one.example/{case.case_id}"
    second_url = f"https://two.example/{case.case_id}"
    plan = {
        "topic": case.query,
        "objectives": list(case.planning_objective_facets),
        "search_queries": [
            {"query": f"{facets} comparison", "purpose": "comparison: compare facets"},
            {"query": f"{facets} evidence", "purpose": "authority: find evidence"},
            {"query": f"{facets} implementation", "purpose": "implementation: describe implementation"},
        ],
        "report_outline": list(case.planning_outline_facets),
    }
    memory_diagnostics = {}
    if memory:
        memory_diagnostics = {
            "enabled": True,
            "retrieved_count": 1,
            "retrieved_memory_ids": ["memory-1"],
            "planner_context_memory_ids": ["memory-1"],
            "searcher_site_hints": ["site:one.example prior research"],
            "write_count": 0,
        }
    return {
        "query": case.query,
        "research_topic": case.query,
        "run_id": run_id,
        "status": "completed",
        "current_stage": "complete",
        "terminal_reason": "completed",
        "research_plan": plan,
        "plan": plan,
        "search_results": [
            {"query": item["query"], "title": facets, "url": url, "snippet": facets, "content": facets}
            for item, url in zip(plan["search_queries"], (first_url, second_url, first_url))
        ],
        "documents": [
            {"document_id": "doc-one", "title": facets, "uri": first_url, "content": facets, "metadata": {"search_query": plan["search_queries"][0]["query"]}},
            {"document_id": "doc-two", "title": facets, "uri": second_url, "content": facets, "metadata": {"search_query": plan["search_queries"][1]["query"]}},
        ],
        "findings": [{"finding_id": "finding-1", "statement": "Observed finding"}],
        "key_findings": ["Observed finding"],
        "evidence": [
            {"evidence_id": "evidence-one", "document_id": "doc-one", "source_url": first_url, "source_quote": "quoted support", "status": "grounded"},
            {"evidence_id": "evidence-two", "document_id": "doc-two", "source_url": second_url, "source_quote": "quoted support", "status": "grounded"},
        ],
        "report_sections": [{"title": "Evidence", "content": "Observed support [1] [2]", "sources": [first_url, second_url]}],
        "report": {"citations": [first_url, second_url]},
        "final_report": "# Showcase report\n\n## Evidence\n\nObserved support [1] [2]. " + ("Observed support. " * 12),
        "agent_trace": _trace(run_id),
        "usage": {"llm_calls": 3, "tool_calls": 2, "input_tokens": 10, "output_tokens": 8, "total_tokens": 18, "latency_seconds": 0.5},
        "search_diagnostics": [{"kind": "adaptive_search", "rounds_attempted": 1, "outcome": "completed", "supplementary_query": "extra source", "primary_coverage": {"result_query_coverage": 0.5}, "post_coverage": {"result_query_coverage": 1.0}}],
        "memory_diagnostics": memory_diagnostics,
    }


def test_showcase_archives_cases_and_read_only_evaluations(tmp_path) -> None:
    states = {case.case_id: _state(case, memory=case.case_id == SHOWCASE_CASES[0].case_id) for case in SHOWCASE_CASES}
    original = copy.deepcopy(states)

    async def fake_runner(case):
        return states[case.case_id]

    result = asyncio.run(
        run_showcase(
            fake_runner,
            memory_case_id=SHOWCASE_CASES[0].case_id,
            memory_store_path=tmp_path / "memory.db",
        )
    )

    assert result["summary"] == {
        "total_cases": 4,
        "completed_cases": 4,
        "failed_cases": 0,
        "adaptive_triggered_cases": 4,
        "memory_enabled_cases": 1,
    }
    assert states == original
    first = result["cases"][0]
    assert first["adaptive"]["supplementary_query"] == "extra source"
    assert first["memory"]["retrieved_memory_ids"] == ["memory-1"]
    assert first["core_metrics"]["source_coverage"] == "passed"
    assert first["core_metrics"]["citation_integrity"] == "passed"
    assert first["core_metrics"]["evidence_grounding"] == "unavailable"
    assert "planning" in first["evaluations"]
    assert "research_coverage" in first["evaluations"]

    summary_json, summary_markdown = archive_showcase(result, tmp_path / "archive")
    archive = json.loads(summary_json.read_text(encoding="utf-8"))
    assert archive["dataset"]["showcase_cases"][0]["category"] == "comparison"
    assert "Boundaries" in summary_markdown.read_text(encoding="utf-8")
    for case in SHOWCASE_CASES:
        assert (tmp_path / "archive" / "cases" / f"{case.case_id}.json").exists()
        assert (tmp_path / "archive" / "cases" / f"{case.case_id}.report.md").exists()


def test_showcase_continues_after_one_runner_failure_and_keeps_default_memory_off() -> None:
    selected = SHOWCASE_CASES[:2]

    async def mixed_runner(case):
        if case.case_id == selected[0].case_id:
            raise RuntimeError("provider token=should-not-appear")
        return _state(case)

    result = asyncio.run(run_showcase(mixed_runner, cases=selected))

    failed, completed = result["cases"]
    assert result["summary"]["failed_cases"] == 1
    assert failed["status"] == "failed"
    assert "should-not-appear" not in failed["error"]
    assert completed["status"] == "completed"
    assert completed["memory"]["enabled_for_case"] is False
    assert completed["memory"]["retrieved_count"] == 0
    assert "failed" in render_showcase_summary(result)


def test_memory_case_must_be_selected_and_only_one_case_is_possible() -> None:
    async def fake_runner(case):
        return _state(case)

    try:
        asyncio.run(run_showcase(fake_runner, cases=SHOWCASE_CASES[:1], memory_case_id=SHOWCASE_CASES[1].case_id))
    except ValueError as exc:
        assert "selected" in str(exc)
    else:
        raise AssertionError("unselected memory case should be rejected")
