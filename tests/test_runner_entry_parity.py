"""Fake integration coverage for the shared runtime entry boundary."""

import asyncio

import app
import main as cli
import pytest

from src import graph
from src import runner as runner_module
from src.runner import ResearchRunner
from src.state import Document, Finding, Report, ReportSection, ResearchPlan, ResearchState, SearchQuery, UsageMetrics
from src.state_compat import canonical_documents, canonical_findings, canonical_plan, canonical_report


def _completed_payload(topic: str, run_id: str) -> dict:
    first_url = "https://example.com/one"
    second_url = "https://example.com/two"
    state = ResearchState(
        research_topic=topic,
        query=topic,
        run_id=run_id,
        status="completed",
        current_stage="complete",
        research_plan=ResearchPlan(
            topic=topic,
            objectives=["compare sources"],
            search_queries=[SearchQuery(query=topic, purpose="research")],
            report_outline=["Summary"],
        ),
        documents=[
            Document(document_id="doc-1", title="One", uri=first_url, content="first source"),
            Document(document_id="doc-2", title="Two", uri=second_url, content="second source"),
        ],
        findings=[Finding(finding_id="finding-1", statement="Sources agree", source_document_ids=["doc-1", "doc-2"])],
        report=Report(
            title=topic,
            sections=[ReportSection(title="Summary", content="Sources agree [1] [2].", sources=[first_url, second_url])],
            content="# Report\n\n## Summary\n\nSources agree [1] [2].",
            citations=[first_url, second_url],
            status="completed",
        ),
        usage=UsageMetrics(llm_calls=4, tool_calls=5, total_tokens=9),
        agent_trace=[{"event_id": "trace-1", "trace_id": run_id, "node": "write_report", "agent": "ReportWriter", "operation": "write_report", "event_type": "node", "attempt": 1, "status": "completed"}],
    )
    return state.model_dump(mode="json")


class _FakeGraph:
    def __init__(self) -> None:
        self.initial_states = []
        self.update_calls = []

    async def ainvoke(self, state, config=None):
        self.initial_states.append((state, config))
        return _completed_payload(state.query, state.run_id)

    async def aupdate_state(self, config, patch):
        self.update_calls.append((config, patch))


class _FakeCache:
    def __init__(self, payload=None) -> None:
        self.payload = payload
        self.set_calls = []

    def get(self, _topic):
        return self.payload

    def set(self, topic, value):
        self.set_calls.append((topic, value))
        self.payload = value


class _Progress:
    async def update(self, _update):
        pass


def _canonical_projection(state):
    return {
        "research_plan": canonical_plan(state).model_dump(mode="json"),
        "documents": [item.model_dump(mode="json") for item in canonical_documents(state)],
        "findings": [item.model_dump(mode="json") for item in canonical_findings(state)],
        "report": canonical_report(state).model_dump(mode="json"),
        "status": state["status"],
        "usage": state["usage"],
        "trace": [
            {key: value for key, value in event.items() if key != "trace_id"}
            for event in state["agent_trace"]
        ],
    }


def _cache_business_projection(state):
    projection = _canonical_projection(state)
    projection.pop("trace")
    return projection


def test_runner_constructs_one_canonical_workflow_state(monkeypatch) -> None:
    graph = _FakeGraph()
    monkeypatch.setattr(runner_module, "create_research_graph", lambda checkpointer=None: graph)

    result = asyncio.run(ResearchRunner(use_cache=False, use_checkpoints=False).run("runner topic", verbose=False))

    initial, run_config = graph.initial_states[0]
    assert initial.query == initial.research_topic == "runner topic"
    assert initial.status == "running"
    assert initial.current_stage == "planning"
    assert run_config is None
    assert canonical_plan(result).topic == "runner topic"
    assert [item.document_id for item in canonical_documents(result)] == ["doc-1", "doc-2"]
    assert canonical_findings(result)[0].source_document_ids == ["doc-1", "doc-2"]
    assert canonical_report(result).citations == ["https://example.com/one", "https://example.com/two"]


def test_graph_runtime_imports_remain_a_deprecated_runner_facade() -> None:
    with pytest.warns(DeprecationWarning, match="moved to src.runner"):
        legacy_run = graph.run_research

    assert legacy_run is runner_module.run_research


def test_cli_and_web_share_runner_canonical_result_semantics(monkeypatch) -> None:
    cli_graph = _FakeGraph()
    web_graph = _FakeGraph()
    graphs = [cli_graph, web_graph]
    monkeypatch.setattr(runner_module, "create_research_graph", lambda checkpointer=None: graphs.pop(0))

    async def fake_emit_complete(*_args):
        pass

    web_runner_options = {}

    def web_runner_factory(**options):
        web_runner_options.update(options)
        return ResearchRunner(**options)

    monkeypatch.setattr(app, "ResearchRunner", web_runner_factory)
    monkeypatch.setattr(app, "emit_complete", fake_emit_complete)

    cli_result = asyncio.run(cli.run_research("entry parity", verbose=False, use_cache=False, use_checkpoints=False))
    web_result = asyncio.run(app.run_research_with_updates("entry parity", _Progress()))

    assert _canonical_projection(cli_result) == _canonical_projection(web_result)
    assert web_runner_options == {"use_cache": False, "use_checkpoints": False, "persist_memory": False}


def test_runner_cache_replay_returns_the_same_canonical_business_state(monkeypatch) -> None:
    graph = _FakeGraph()
    cache = _FakeCache()
    monkeypatch.setattr(runner_module, "create_research_graph", lambda checkpointer=None: graph)
    monkeypatch.setattr(runner_module, "ResearchCache", lambda: cache)
    runner = ResearchRunner(use_cache=True, use_checkpoints=False)

    first = asyncio.run(runner.run("cache runner topic", verbose=False))
    second = asyncio.run(runner.run("cache runner topic", verbose=False))

    assert len(graph.initial_states) == 1
    assert len(cache.set_calls) == 1
    assert _cache_business_projection(first) == _cache_business_projection(second)
    assert isinstance(second["agent_trace"], list)
    assert second["terminal_reason"] == "cache_replay"
