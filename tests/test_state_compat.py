"""Regression coverage for the explicit canonical/legacy State boundary."""

from src.state import ResearchPlan, ResearchState, SearchQuery, UsageMetrics
from src.state_compat import (
    canonical_iteration,
    canonical_plan,
    canonical_query,
    canonical_usage,
    hydrate_canonical_state,
)


def _plan(topic: str) -> ResearchPlan:
    return ResearchPlan(
        topic=topic,
        objectives=[topic],
        search_queries=[SearchQuery(query=topic, purpose="test")],
        report_outline=["Summary"],
    )


def test_hydration_projects_legacy_only_core_fields_without_mutating_source() -> None:
    legacy = ResearchState(
        research_topic="legacy query",
        plan=_plan("legacy query"),
        iterations=4,
        llm_calls=2,
        total_input_tokens=20,
        total_output_tokens=8,
    )

    hydrated = hydrate_canonical_state(legacy, include_semantic_projections=False)

    assert hydrated is not legacy
    assert canonical_query(hydrated) == "legacy query"
    assert canonical_plan(hydrated) == legacy.plan
    assert canonical_iteration(hydrated) == 4
    assert canonical_usage(hydrated).model_dump(include={"llm_calls", "input_tokens", "output_tokens"}) == {
        "llm_calls": 2,
        "input_tokens": 20,
        "output_tokens": 8,
    }
    assert legacy.query == ""
    assert legacy.research_plan is None
    assert legacy.iteration == 0


def test_hydration_prefers_explicit_canonical_values_on_conflict() -> None:
    canonical = _plan("canonical query")
    legacy = ResearchState(
        research_topic="legacy query",
        query="canonical query",
        plan=_plan("legacy query"),
        research_plan=canonical,
        iterations=2,
        iteration=7,
        llm_calls=2,
        total_input_tokens=20,
        total_output_tokens=8,
        usage=UsageMetrics(llm_calls=9, input_tokens=90, output_tokens=30, total_tokens=120),
    )

    hydrated = hydrate_canonical_state(legacy, include_semantic_projections=False)

    assert canonical_query(hydrated) == "canonical query"
    assert canonical_plan(hydrated) == canonical
    assert canonical_iteration(hydrated) == 7
    assert canonical_usage(hydrated).llm_calls == 9
    assert canonical_usage(hydrated).input_tokens == 90
    assert canonical_usage(hydrated).output_tokens == 30
