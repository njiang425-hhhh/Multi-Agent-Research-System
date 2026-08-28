"""Fake-only coverage for P3.2a append-only Agent Trace."""

import asyncio

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from src.agent_trace import (
    AgentTraceEvent,
    TraceRetentionPolicy,
    _attempt,
    append_trace_events,
    trace_node_execution,
)
from src import graph as graph_module
from src.runtime_lifecycle import create_new_run_state
from src.state import Document, Finding, Report, ResearchPlan, ResearchState, SearchQuery, SearchResult


def test_trace_node_projects_legacy_llm_and_search_runtime_without_changing_usage() -> None:
    state = create_new_run_state("trace topic")
    prior = AgentTraceEvent(
        event_id="persisted-event",
        trace_id=state.run_id,
        node="plan",
        agent="ResearchPlanner",
        operation="plan",
        event_type="node",
        attempt=1,
        started_at="2026-01-01T00:00:00+00:00",
        ended_at="2026-01-01T00:00:01+00:00",
        duration_seconds=1,
    )
    state = state.model_copy(update={"agent_trace": [prior]})

    async def fake_search(_state):
        return {
            "llm_calls": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "usage": state.usage,
            "llm_call_details": [
                {
                    "agent": "ResearchSearcher",
                    "operation": "deterministic_search",
                    "duration": 0.3,
                    "tool_invocations": [
                        {"operation": "search", "attempt": 1, "success": False, "duration": 0.1, "error": "fake failure"},
                        {"operation": "search", "attempt": 2, "success": True, "duration": 0.2},
                    ],
                }
            ],
        }

    patch = asyncio.run(
        trace_node_execution(
            state,
            node="search",
            agent="ResearchSearcher",
            operation="search",
            execute=fake_search,
        )
    )

    assert patch["usage"] == state.usage
    assert patch["llm_calls"] == 0
    assert [event.event_id for event in patch["agent_trace"]].count("persisted-event") == 1
    assert [event.event_type for event in patch["agent_trace"]] == ["node", "tool", "tool", "node"]
    tool_events = [event for event in patch["agent_trace"] if event.event_type == "tool"]
    assert [event.status for event in tool_events] == ["failed", "completed"]
    assert [event.attempt for event in tool_events] == [1, 2]
    assert all(event.trace_id == state.run_id for event in patch["agent_trace"])


def test_resume_append_preserves_prior_attempts_and_deduplicates_only_same_event_id() -> None:
    state = create_new_run_state("resume trace topic")

    async def failed_attempt(_state):
        return {"error": "fake node failure", "llm_call_details": []}

    first_patch = asyncio.run(
        trace_node_execution(
            state, node="plan", agent="ResearchPlanner", operation="plan", execute=failed_attempt
        )
    )
    resumed = state.model_copy(update={"agent_trace": first_patch["agent_trace"]})

    async def retry_attempt(_state):
        return {"llm_call_details": []}

    second_patch = asyncio.run(
        trace_node_execution(
            resumed, node="plan", agent="ResearchPlanner", operation="plan", execute=retry_attempt
        )
    )

    node_events = [event for event in second_patch["agent_trace"] if event.event_type == "node"]
    assert [event.attempt for event in node_events] == [1, 2]
    assert [event.status for event in node_events] == ["failed", "completed"]
    assert len(append_trace_events(node_events, node_events)) == 2


def test_trace_retention_redacts_payload_and_preserves_future_attempt_identity() -> None:
    policy = TraceRetentionPolicy(max_events=8, max_metadata_string_chars=32)
    nodes = [
        AgentTraceEvent(
            event_id=f"node-{node}", trace_id="trace-run", node=node, agent="Fake",
            operation=node, event_type="node", attempt=1,
            started_at="2026-01-01T00:00:00+00:00", ended_at="2026-01-01T00:00:01+00:00",
        )
        for node in ("plan", "search", "synthesize", "write_report")
    ]
    tools = [
        AgentTraceEvent(
            event_id=f"tool-{attempt}", trace_id="trace-run", node="search", agent="ResearchSearcher",
            operation="search", event_type="tool", attempt=attempt,
            started_at="2026-01-01T00:00:00+00:00", ended_at="2026-01-01T00:00:01+00:00",
            metadata={
                "payload": {"authorization": "Bearer fake-secret", "query": "private query"},
                "note": "x" * 80,
            },
        )
        for attempt in range(1, 13)
    ]

    retained = append_trace_events(nodes, tools, retention_policy=policy)

    assert len(retained) == policy.max_events
    marker = retained[0]
    assert marker.event_type == "retention"
    assert marker.metadata["attempt_offsets"]["search\x1ftool\x1fsearch"] == 12
    assert {event.node for event in retained if event.event_type == "node"} == {
        "plan", "search", "synthesize", "write_report"
    }
    retained_tool = next(event for event in retained if event.event_type == "tool")
    assert retained_tool.metadata["payload"] == "[REDACTED]"
    assert retained_tool.metadata["note"].endswith("…[TRUNCATED]")
    assert _attempt(retained, node="search", event_type="tool", operation="search") == 13
    assert [event.event_id for event in append_trace_events(retained, (), retention_policy=policy)] == [
        event.event_id for event in retained
    ]


def test_graph_wraps_all_four_existing_nodes_with_trace_without_changing_routes(monkeypatch) -> None:
    plan = ResearchPlan(
        topic="trace graph topic",
        objectives=["fake objective"],
        search_queries=[SearchQuery(query="fake query", purpose="fake purpose")],
        report_outline=["fake outline"],
    )
    documents = [
        Document(document_id="one", title="one", uri="https://example.com/one", snippet="one"),
        Document(document_id="two", title="two", uri="https://example.com/two", snippet="two"),
    ]

    class FakePlanner:
        async def plan(self, state):
            return {
                "research_plan": plan,
                "llm_call_details": state.llm_call_details + [
                    {"agent": "ResearchPlanner", "operation": "plan", "duration": 0.1}
                ],
            }

    class FakeSearcher:
        async def search(self, state):
            return {"documents": documents, "llm_call_details": state.llm_call_details}

    class FakeSynthesizer:
        async def synthesize(self, state):
            return {"findings": [Finding(finding_id="finding", statement="fake finding", source_document_ids=["one"])], "llm_call_details": state.llm_call_details}

    class FakeWriter:
        def __init__(self, **_kwargs):
            pass

        async def write_report(self, state):
            return {"report": Report(title="fake", content="# fake report", citations=["https://example.com/one"], status="completed"), "llm_call_details": state.llm_call_details}

    monkeypatch.setattr(graph_module, "ResearchPlanner", FakePlanner)
    monkeypatch.setattr(graph_module, "ResearchSearcher", FakeSearcher)
    monkeypatch.setattr(graph_module, "ResearchSynthesizer", FakeSynthesizer)
    monkeypatch.setattr(graph_module, "ReportWriter", FakeWriter)

    result = asyncio.run(graph_module.create_research_graph().ainvoke(create_new_run_state("trace graph topic")))

    node_events = [event for event in result["agent_trace"] if event.event_type == "node"]
    assert [event.node for event in node_events] == ["plan", "search", "synthesize", "write_report"]
    assert all(event.status == "completed" for event in node_events)
    assert result["final_report"] == "# fake report"


def test_async_sqlite_resume_appends_trace_without_replacing_persisted_attempts(tmp_path) -> None:
    checkpoint_path = tmp_path / "trace-resume.db"
    config = {"configurable": {"thread_id": "fake-trace-resume"}}

    async def plan(state):
        async def execute(_state):
            return {
                "llm_call_details": state.llm_call_details
                + [{"agent": "ResearchPlanner", "operation": "plan", "duration": 0.1}]
            }

        return await trace_node_execution(
            state, node="plan", agent="ResearchPlanner", operation="plan", execute=execute
        )

    async def search(state):
        async def execute(_state):
            return {"llm_call_details": state.llm_call_details}

        return await trace_node_execution(
            state, node="search", agent="ResearchSearcher", operation="search", execute=execute
        )

    def create_graph(checkpointer):
        workflow = StateGraph(ResearchState)
        workflow.add_node("plan", plan)
        workflow.add_node("search", search)
        workflow.add_edge(START, "plan")
        workflow.add_edge("plan", "search")
        workflow.add_edge("search", END)
        return workflow.compile(checkpointer=checkpointer)

    async def exercise() -> None:
        initial = create_new_run_state("trace persistence topic")
        async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
            graph = create_graph(checkpointer)
            await graph.ainvoke(initial, config=config, interrupt_before="search")
            interrupted = await graph.aget_state(config)
            persisted_events = interrupted.values["agent_trace"]
            assert [event.node for event in persisted_events] == ["plan", "plan"]
            persisted_ids = [event.event_id for event in persisted_events]

        async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
            graph = create_graph(checkpointer)
            resumed = await graph.ainvoke(None, config=config)

        events = resumed["agent_trace"]
        assert [event.node for event in events] == ["plan", "plan", "search"]
        assert [event.event_id for event in events[:2]] == persisted_ids
        assert all(event.trace_id == initial.run_id for event in events)

    asyncio.run(exercise())
