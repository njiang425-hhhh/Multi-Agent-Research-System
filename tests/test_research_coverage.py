"""Fake-only P10.1 research coverage baseline tests."""

from __future__ import annotations

import asyncio
import json

import pytest

from src.evaluation.research_coverage import (
    RESEARCH_COVERAGE_METRICS,
    ResearchCoverageObservation,
    evaluate_research_coverage,
    research_coverage_content_fingerprint,
)
from src.evaluation.research_coverage_dataset import (
    P10_RESEARCH_COVERAGE_FAKE_PAYLOADS,
    P10_RESEARCH_COVERAGE_EXTRACT_PAYLOADS,
    P10_RESEARCH_COVERAGE_PLAN_PAYLOADS,
    P10_RESEARCH_COVERAGE_SEARCH_PAYLOADS,
    RESEARCH_COVERAGE_DATASET,
)
from src.evidence.adapters import scored_search_results_to_documents
from src.planning import normalize_research_plan
from src.search.config import SearchConfig
from src.search.executor import SearchExecutor
from src.state import ResearchPlan, SearchResult


class FakeSearchTool:
    def __init__(self, payloads: dict[str, list[dict[str, str]]]) -> None:
        self.payloads = payloads
        self.calls: list[str] = []

    async def __call__(self, query: str, max_results: int) -> list[dict[str, str]]:
        self.calls.append(query)
        return [
            {"query": query, **item}
            for item in self.payloads.get(query, [])[:max_results]
        ]


class FakeExtractTool:
    def __init__(self, payloads: dict[str, str]) -> None:
        self.payloads = payloads
        self.calls: list[str] = []

    async def __call__(self, url: str) -> str:
        self.calls.append(url)
        return self.payloads[url]


def _plan(case_id: str) -> ResearchPlan:
    return normalize_research_plan(
        P10_RESEARCH_COVERAGE_PLAN_PAYLOADS[case_id],
        max_queries=3,
        max_sections=8,
    )


async def _observation(case_id: str):
    plan = _plan(case_id)
    search_tool = FakeSearchTool(P10_RESEARCH_COVERAGE_SEARCH_PAYLOADS[case_id])
    extract_tool = FakeExtractTool(P10_RESEARCH_COVERAGE_EXTRACT_PAYLOADS)
    executor = SearchExecutor(
        search_config=SearchConfig(
            mode="deterministic_v2",
            max_search_times=3,
            max_extract_times=4,
            max_results_per_search=3,
            total_timeout_seconds=90.0,
            allow_partial_results=True,
        ),
        search_tool=search_tool,
        extract_tool=extract_tool,
    )

    execution = await executor.execute(
        plan.search_queries,
        max_results_per_search=3,
    )
    documents = scored_search_results_to_documents(
        (result, {"score": 80, "level": "high", "source": result.title})
        for result in execution.search_results
    )
    return ResearchCoverageObservation(
        plan=plan,
        executed_queries=list(search_tool.calls),
        search_results=execution.search_results,
        documents=documents,
    )


async def _observations():
    return {
        case.case_id: await _observation(case.case_id)
        for case in RESEARCH_COVERAGE_DATASET.cases
    }


def _metric(result, name: str):
    return next(metric for metric in result.metrics if metric.name == name)


def _previous_sequential_observations(
    observations: dict[str, ResearchCoverageObservation],
) -> dict[str, ResearchCoverageObservation]:
    before: dict[str, ResearchCoverageObservation] = {}
    for case_id, observation in observations.items():
        search_results: list[SearchResult] = []
        for index, result in enumerate(observation.search_results):
            content = (
                P10_RESEARCH_COVERAGE_EXTRACT_PAYLOADS[result.url]
                if index < 4
                else None
            )
            search_results.append(result.model_copy(update={"content": content}))
        documents = scored_search_results_to_documents(
            (result, {"score": 80, "level": "high", "source": result.title})
            for result in search_results
        )
        before[case_id] = ResearchCoverageObservation(
            plan=observation.plan,
            executed_queries=list(observation.executed_queries),
            search_results=search_results,
            documents=documents,
        )
    return before


def _evaluate(observations: dict[str, ResearchCoverageObservation]):
    return evaluate_research_coverage(
        observations,
        dataset=RESEARCH_COVERAGE_DATASET,
        fake_payloads=P10_RESEARCH_COVERAGE_FAKE_PAYLOADS,
        configuration={
            "execution": "fake",
            "searcher_mode": "deterministic_v2",
            "max_search_times": 3,
            "max_extract_times": 4,
            "max_results_per_search": 3,
            "min_credibility_score": 40,
            "research_memory_enabled": False,
            "evidence_analyzer_enabled": False,
        },
    )


def test_research_coverage_baseline_is_deterministic_and_read_only() -> None:
    observations = asyncio.run(_observations())
    before = {
        case_id: observation.model_dump(mode="json")
        for case_id, observation in observations.items()
    }

    result = _evaluate(observations)
    same = evaluate_research_coverage(
        observations,
        dataset=RESEARCH_COVERAGE_DATASET,
        fake_payloads=P10_RESEARCH_COVERAGE_FAKE_PAYLOADS,
        configuration={
            "max_results_per_search": 3,
            "max_extract_times": 4,
            "max_search_times": 3,
            "execution": "fake",
            "searcher_mode": "deterministic_v2",
            "min_credibility_score": 40,
            "research_memory_enabled": False,
            "evidence_analyzer_enabled": False,
        },
    )

    assert result.evaluation_snapshot.metric_names == list(RESEARCH_COVERAGE_METRICS)
    assert result.evaluation_snapshot.expected_completed_nodes == ["plan", "search"]
    assert result.evaluation_snapshot.fingerprint == same.evaluation_snapshot.fingerprint
    assert result.dataset_content_fingerprint == research_coverage_content_fingerprint(
        RESEARCH_COVERAGE_DATASET
    )
    assert result.fake_payload_fingerprint == research_coverage_content_fingerprint(
        P10_RESEARCH_COVERAGE_FAKE_PAYLOADS
    )
    assert result.summary.total_cases == 3
    assert result.summary.fully_passing_cases == 3
    assert result.summary.planned_query_facet_coverage_mean == 1.0
    assert result.summary.executed_query_facet_coverage_mean == 1.0
    assert result.summary.result_facet_coverage_mean == 1.0
    assert result.summary.source_domain_balance_mean == 1.0
    assert result.summary.extracted_facet_coverage_mean == 1.0
    assert result.summary.p10_2_candidate is False
    assert result.summary.result_or_extracted_failure_case_ids == []
    assert {
        case_id: observation.model_dump(mode="json")
        for case_id, observation in observations.items()
    } == before
    json.dumps(result.model_dump(mode="json"))


def test_research_coverage_before_after_closes_sequential_extraction_gap() -> None:
    observations = asyncio.run(_observations())
    before = _evaluate(_previous_sequential_observations(observations))
    after = _evaluate(observations)

    assert before.summary.planned_query_facet_coverage_mean == 1.0
    assert before.summary.executed_query_facet_coverage_mean == 1.0
    assert before.summary.result_facet_coverage_mean == 1.0
    assert before.summary.source_domain_balance_mean == 1.0
    assert before.summary.extracted_facet_coverage_mean == pytest.approx(
        ((4 / 5) + (2 / 3) + (2 / 3)) / 3
    )
    assert before.summary.fully_passing_cases == 0
    assert before.summary.p10_2_candidate is True

    assert after.summary.planned_query_facet_coverage_mean == 1.0
    assert after.summary.executed_query_facet_coverage_mean == 1.0
    assert after.summary.result_facet_coverage_mean == 1.0
    assert after.summary.source_domain_balance_mean == 1.0
    assert after.summary.extracted_facet_coverage_mean == 1.0
    assert after.summary.fully_passing_cases == 3
    assert after.summary.p10_2_candidate is False

    by_case = {item.case_id: item for item in before.results}

    ai = by_case["ai-regulation-jurisdiction-balance"]
    assert _metric(ai, "planned_query_facet_coverage").status == "passed"
    assert _metric(ai, "executed_query_facet_coverage").status == "passed"
    assert _metric(ai, "result_facet_coverage").status == "passed"
    assert _metric(ai, "source_domain_balance").status == "passed"
    assert _metric(ai, "extracted_facet_coverage").status == "failed"
    assert _metric(ai, "extracted_facet_coverage").metadata["missing_facets"] == [
        "china"
    ]

    clinical = by_case["clinical-evidence-practice-translation"]
    assert _metric(clinical, "extracted_facet_coverage").metadata["missing_facets"] == [
        "outcome"
    ]

    enterprise = by_case["enterprise-ai-evaluation-coverage"]
    assert _metric(enterprise, "extracted_facet_coverage").metadata["missing_facets"] == [
        "procurement"
    ]


def test_research_coverage_snapshot_changes_with_fake_payload_content() -> None:
    observations = asyncio.run(_observations())
    base = evaluate_research_coverage(
        observations,
        dataset=RESEARCH_COVERAGE_DATASET,
        fake_payloads=P10_RESEARCH_COVERAGE_FAKE_PAYLOADS,
        configuration={"execution": "fake"},
    )
    changed_payloads = {
        **P10_RESEARCH_COVERAGE_FAKE_PAYLOADS,
        "extract": {
            **P10_RESEARCH_COVERAGE_EXTRACT_PAYLOADS,
            "https://procurement.example.org/evaluation": "changed procurement text",
        },
    }
    changed = evaluate_research_coverage(
        observations,
        dataset=RESEARCH_COVERAGE_DATASET,
        fake_payloads=changed_payloads,
        configuration={"execution": "fake"},
    )

    assert base.fake_payload_fingerprint != changed.fake_payload_fingerprint
    assert base.evaluation_snapshot.fingerprint != changed.evaluation_snapshot.fingerprint
