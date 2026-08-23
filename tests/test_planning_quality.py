"""Fake-only P9 Planning Enhancement baseline and Planner contract tests."""

from __future__ import annotations

import asyncio
import json

from langchain_core.runnables import RunnableLambda
import pytest

from src.agents import ResearchPlanner
from src.evaluation.planning_dataset import (
    P9_BASELINE_PLAN_PAYLOADS,
    P9_IMPROVED_PLAN_PAYLOADS,
    PLANNING_QUALITY_DATASET,
)
from src.evaluation.planning_quality import evaluate_planning_quality
from src.planning import normalize_research_plan, purpose_category
from src.state import ResearchPlan, ResearchState, SearchQuery


def _legacy_plan(payload: dict) -> ResearchPlan:
    return ResearchPlan(
        topic=payload["topic"],
        objectives=payload["objectives"],
        search_queries=[SearchQuery(**item) for item in payload["search_queries"]],
        report_outline=payload["report_outline"],
    )


async def _planner_output(case_id: str) -> ResearchPlan:
    payload = P9_IMPROVED_PLAN_PAYLOADS[case_id]
    planner = ResearchPlanner(
        llm=RunnableLambda(lambda _prompt: json.dumps(payload)),
        max_retries=1,
    )
    patch = await planner.plan(ResearchState(research_topic=payload["topic"]))
    assert patch["llm_calls"] == 1
    assert len(patch["llm_call_details"]) == 1
    return patch["plan"]


def _metric(result, name: str):
    return next(metric for metric in result.metrics if metric.name == name)


def test_planning_quality_before_after_is_deterministic_and_uses_actual_planner() -> None:
    before = evaluate_planning_quality(
        {case_id: _legacy_plan(payload) for case_id, payload in P9_BASELINE_PLAN_PAYLOADS.items()},
        dataset=PLANNING_QUALITY_DATASET,
        configuration={"phase": "before", "execution": "fake"},
    )
    after_plans = asyncio.run(
        _collect_after_plans([case.case_id for case in PLANNING_QUALITY_DATASET.cases])
    )
    after = evaluate_planning_quality(
        after_plans,
        dataset=PLANNING_QUALITY_DATASET,
        configuration={"phase": "after", "execution": "fake"},
    )

    assert before.summary.fully_passing_cases == 1
    assert before.summary.objectives_coverage_mean == pytest.approx(10 / 18)
    assert before.summary.query_purpose_diversity_mean == pytest.approx(8 / 6)
    assert before.summary.report_outline_alignment_mean == pytest.approx(3.25 / 6)
    assert after.summary.fully_passing_cases == 6
    assert after.summary.objectives_coverage_mean == 1.0
    assert after.summary.query_purpose_diversity_mean == 3.0
    assert after.summary.report_outline_alignment_mean == 1.0
    assert after.evaluation_snapshot.metric_names == [
        "objectives_coverage",
        "query_purpose_diversity",
        "report_outline_alignment",
    ]
    assert before.dataset_content_fingerprint == after.dataset_content_fingerprint

    before_control = next(item for item in before.results if item.case_id == "semiconductor-supply-chain")
    after_control = next(item for item in after.results if item.case_id == "semiconductor-supply-chain")
    assert [metric.value for metric in before_control.metrics] == [metric.value for metric in after_control.metrics]
    assert all(metric.status == "passed" for metric in after_control.metrics)

    before_ai = next(item for item in before.results if item.case_id == "ai-regulation-comparison")
    after_ai = next(item for item in after.results if item.case_id == "ai-regulation-comparison")
    assert _metric(before_ai, "objectives_coverage").status == "failed"
    assert _metric(after_ai, "objectives_coverage").status == "passed"
    assert _metric(before_ai, "query_purpose_diversity").value == 1
    assert _metric(after_ai, "query_purpose_diversity").value == 3


async def _collect_after_plans(case_ids: list[str]) -> dict[str, ResearchPlan]:
    return {case_id: await _planner_output(case_id) for case_id in case_ids}


def test_planner_normalization_deduplicates_and_derives_bounded_purpose_labels() -> None:
    plan = normalize_research_plan(
        {
            "topic": "  enterprise AI  ",
            "objectives": ["Assess capability", "Assess capability", "  Review safety  "],
            "search_queries": [
                {"query": "enterprise AI comparison", "purpose": "compare options"},
                {"query": " Enterprise AI comparison ", "purpose": "duplicate"},
                {"query": "enterprise AI official safety standard", "purpose": "review rules"},
                {"query": "enterprise AI implementation guide", "purpose": "delivery"},
            ],
            "report_outline": ["Context", "Context", "Safety controls"],
        },
        max_queries=3,
        max_sections=8,
    )

    assert plan.topic == "enterprise AI"
    assert plan.objectives == ["Assess capability", "Review safety"]
    assert [item.query for item in plan.search_queries] == [
        "enterprise AI comparison",
        "enterprise AI official safety standard",
        "enterprise AI implementation guide",
    ]
    assert [purpose_category(item.purpose) for item in plan.search_queries] == [
        "comparison",
        "authority",
        "implementation",
    ]
    assert plan.report_outline == ["Context", "Safety controls"]


def test_planner_prompt_requires_aligned_outline_and_labeled_diverse_purposes() -> None:
    captured = ""

    async def fake_plan(prompt):
        nonlocal captured
        captured = str(prompt)
        return json.dumps(P9_IMPROVED_PLAN_PAYLOADS["ai-regulation-comparison"])

    patch = asyncio.run(
        ResearchPlanner(llm=RunnableLambda(fake_plan), max_retries=1).plan(
            ResearchState(research_topic="Compare AI regulation across jurisdictions")
        )
    )

    assert "purpose" in captured
    assert "risk_limitations:" in captured
    assert "\u81f3\u5c11\u4e00\u4e2a\u5bf9\u5e94\u7ae0\u8282" in captured
    assert patch["plan"].search_queries[0].purpose.startswith("comparison:")
