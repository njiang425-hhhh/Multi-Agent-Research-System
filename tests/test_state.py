"""ResearchState V1 contract tests."""

from src.state import (
    Finding,
    ReportSection,
    ResearchPlan,
    ResearchState,
    SearchQuery,
    SearchResult,
    UsageMetrics,
)


def test_research_state_initializes_canonical_defaults() -> None:
    """A canonical entry needs no legacy topic field."""
    state = ResearchState(query="LangGraph development trends")

    assert state.research_topic == ""
    assert state.state_version == 1

    assert state.query == "LangGraph development trends"
    assert state.research_plan is None
    assert state.documents == []
    assert state.findings == []
    assert state.report is None
    assert state.agent_trace == []
    assert state.current_stage == "planning"
    assert state.run_id == ""
    assert state.status == "pending"
    assert state.error is None
    assert state.iteration == 0

    assert state.retrieved_memory == []
    assert state.memory_ids == []
    assert "critic_feedback" not in ResearchState.model_fields
    assert "agent_messages" not in ResearchState.model_fields
    assert "supervisor_decision" not in ResearchState.model_fields
    assert "quality_score" not in ResearchState.model_fields

    assert state.plan is None
    assert state.search_results == []
    assert state.key_findings == []
    assert state.report_sections == []
    assert state.final_report is None
    assert state.iterations == 0
    assert state.credibility_scores == []
    assert state.llm_calls == 0
    assert state.total_input_tokens == 0
    assert state.total_output_tokens == 0
    assert state.llm_call_details == []


def test_research_state_usage_defaults_to_a_fresh_zero_value_object() -> None:
    """Usage is a typed per-run object, not a shared mutable default."""
    first_state = ResearchState(research_topic="first")
    second_state = ResearchState(research_topic="second")

    assert isinstance(first_state.usage, UsageMetrics)
    assert first_state.usage.model_dump() == {
        "llm_calls": 0,
        "tool_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "latency_seconds": 0.0,
        "estimated_cost": None,
    }
    assert first_state.usage is not second_state.usage


def test_research_state_accepts_legacy_fields() -> None:
    """Active V0 fields remain available while V1 migration is incomplete."""
    plan = ResearchPlan(
        topic="LangGraph",
        objectives=["Understand the framework"],
        search_queries=[SearchQuery(query="LangGraph official documentation", purpose="primary source")],
        report_outline=["Overview"],
    )
    result = SearchResult(
        query="LangGraph official documentation",
        title="LangGraph documentation",
        url="https://langchain-ai.github.io/langgraph/",
        snippet="Official documentation",
        content="LangGraph is a framework for agent workflows.",
    )
    section = ReportSection(
        title="Overview",
        content="A concise report section.",
        sources=[result.url],
    )

    state = ResearchState(
        research_topic="LangGraph",
        plan=plan,
        search_results=[result],
        key_findings=["LangGraph supports stateful agent workflows."],
        report_sections=[section],
        final_report="# LangGraph",
        iterations=2,
        credibility_scores=[{"score": 90, "level": "high"}],
        llm_calls=3,
        total_input_tokens=120,
        total_output_tokens=45,
        llm_call_details=[{"agent": "ResearchPlanner"}],
    )

    assert state.plan == plan
    assert state.search_results == [result]
    assert state.key_findings == ["LangGraph supports stateful agent workflows."]
    assert state.report_sections == [section]
    assert state.final_report == "# LangGraph"
    assert state.iterations == 2
    assert state.credibility_scores == [{"score": 90, "level": "high"}]
    assert state.llm_calls == 3
    assert state.total_input_tokens == 120
    assert state.total_output_tokens == 45
    assert state.llm_call_details == [{"agent": "ResearchPlanner"}]


def test_research_state_does_not_automatically_sync_v1_and_legacy_fields() -> None:
    """V1 and legacy fields require explicit migration logic outside the model."""
    legacy_result = SearchResult(
        query="LangGraph",
        title="Source",
        url="https://example.com/source",
        snippet="A source",
    )
    v1_finding = Finding(
        finding_id="finding-1",
        statement="A structured finding",
    )

    state = ResearchState(
        research_topic="legacy topic",
        query="v1 query",
        search_results=[legacy_result],
        findings=[v1_finding],
        key_findings=["legacy finding"],
    )

    assert state.research_topic == "legacy topic"
    assert state.query == "v1 query"
    assert state.search_results == [legacy_result]
    assert state.documents == []
    assert state.key_findings == ["legacy finding"]
    assert state.findings == [v1_finding]

    legacy_only_state = ResearchState(research_topic="legacy-only")
    assert legacy_only_state.query == ""
    assert legacy_only_state.documents == []
    assert legacy_only_state.findings == []


def test_legacy_checkpoint_payload_is_not_hydrated_with_v1_fields() -> None:
    """Legacy checkpoints remain valid without automatically backfilling V1 data."""
    legacy_plan = ResearchPlan(
        topic="legacy topic",
        objectives=["preserve compatibility"],
        search_queries=[],
        report_outline=[],
    )

    restored = ResearchState.model_validate({
        "research_topic": "legacy topic",
        "plan": legacy_plan.model_dump(),
        "key_findings": ["legacy finding"],
        "final_report": "legacy report",
        "llm_calls": 2,
        "total_input_tokens": 10,
        "total_output_tokens": 5,
    })

    assert restored.research_topic == "legacy topic"
    assert restored.plan == legacy_plan
    assert restored.key_findings == ["legacy finding"]
    assert restored.final_report == "legacy report"
    assert restored.query == ""
    assert restored.research_plan is None
    assert restored.documents == []
    assert restored.findings == []
    assert restored.report is None
    assert restored.usage == UsageMetrics()


def test_research_state_accepts_empty_canonical_input() -> None:
    assert ResearchState().query == ""
