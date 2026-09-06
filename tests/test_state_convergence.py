"""Phase 3C canonical-only state-flow contracts."""

import asyncio

from src import graph as graph_module
from src.runtime_lifecycle import create_new_run_state
from src.state import Document, Finding, Report, ReportSection, ResearchPlan, ResearchState, SearchQuery, SearchResult
from src.state_compat import canonical_documents, canonical_findings, canonical_plan, canonical_query, canonical_report, hydrate_canonical_state


def _plan(topic: str) -> ResearchPlan:
    return ResearchPlan(
        topic=topic,
        objectives=["objective"],
        search_queries=[SearchQuery(query=topic, purpose="research")],
        report_outline=["Summary"],
    )


def test_canonical_only_input_needs_no_legacy_task_field() -> None:
    state = ResearchState(query="canonical topic", research_plan=_plan("canonical topic"))

    hydrated = hydrate_canonical_state(state)

    assert hydrated.research_topic == ""
    assert canonical_query(hydrated) == "canonical topic"
    assert canonical_plan(hydrated) == state.research_plan


def test_legacy_only_input_hydrates_into_canonical_business_fields() -> None:
    source = SearchResult(
        query="legacy topic",
        title="Legacy source",
        url="https://example.com/legacy",
        snippet="legacy snippet",
        content="legacy content",
    )
    legacy = ResearchState(
        research_topic="legacy topic",
        plan=_plan("legacy topic"),
        search_results=[source],
        key_findings=["legacy finding"],
        report_sections=[ReportSection(title="Summary", content="legacy report", sources=[source.url])],
        final_report="# legacy report",
    )

    hydrated = hydrate_canonical_state(legacy)

    assert canonical_query(hydrated) == "legacy topic"
    assert canonical_plan(hydrated).topic == "legacy topic"
    assert [item.uri for item in canonical_documents(hydrated)] == [source.url]
    assert [item.statement for item in canonical_findings(hydrated)] == ["legacy finding"]
    assert canonical_report(hydrated).content == "# legacy report"


def test_explicit_canonical_values_win_even_when_empty_against_legacy_values() -> None:
    legacy_source = SearchResult(
        query="legacy topic",
        title="Legacy source",
        url="https://example.com/legacy",
        snippet="legacy snippet",
    )
    state = ResearchState(
        query="canonical topic",
        research_plan=None,
        documents=[],
        findings=[],
        report=None,
        research_topic="legacy topic",
        plan=_plan("legacy topic"),
        search_results=[legacy_source],
        key_findings=["legacy finding"],
        final_report="# legacy report",
    )

    hydrated = hydrate_canonical_state(state)

    assert canonical_query(hydrated) == "canonical topic"
    assert canonical_plan(hydrated) is None
    assert canonical_documents(hydrated) == []
    assert canonical_findings(hydrated) == []
    assert canonical_report(hydrated) is None


def test_new_graph_flow_emits_canonical_business_values_only(monkeypatch) -> None:
    topic = "canonical graph topic"
    plan = _plan(topic)
    documents = [
        Document(document_id="doc-1", title="One", uri="https://example.com/one"),
        Document(document_id="doc-2", title="Two", uri="https://example.com/two"),
    ]

    class Planner:
        async def plan(self, _state):
            return {"research_plan": plan}

    class Searcher:
        async def search(self, _state):
            return {"documents": documents}

    class Synthesizer:
        async def synthesize(self, _state):
            return {"findings": [Finding(finding_id="finding", statement="claim", source_document_ids=["doc-1"])]}

    class Writer:
        def __init__(self, **_kwargs):
            pass

        async def write_report(self, _state):
            return {
                "report": Report(
                    title=topic,
                    sections=[ReportSection(title="Summary", content="claim [1]", sources=[documents[0].uri])],
                    content="# canonical report",
                    citations=[item.uri for item in documents],
                    status="completed",
                )
            }

    monkeypatch.setattr(graph_module, "ResearchPlanner", Planner)
    monkeypatch.setattr(graph_module, "ResearchSearcher", Searcher)
    monkeypatch.setattr(graph_module, "ResearchSynthesizer", Synthesizer)
    monkeypatch.setattr(graph_module, "ReportWriter", Writer)

    result = asyncio.run(graph_module.create_research_graph().ainvoke(create_new_run_state(topic)))

    assert result["query"] == topic
    assert result["research_topic"] == ""
    assert result["research_plan"] == plan
    assert result["documents"] == documents
    assert result["findings"][0].statement == "claim"
    assert result["report"].content == "# canonical report"
    for legacy_field, default in {
        "plan": None,
        "search_results": [],
        "key_findings": [],
        "report_sections": [],
        "final_report": None,
    }.items():
        assert result.get(legacy_field, default) == default
