"""Fake-only tests for P8 Research Memory V1 baseline."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from langchain_core.runnables import RunnableLambda

from src import graph as graph_module
from src.agents import ResearchPlanner, ResearchSearcher
from src.evidence.contracts import Evidence
from src.memory import (
    ResearchMemoryStore,
    build_search_memory_hints,
    project_research_memories,
)
from src.memory.contracts import ResearchMemoryRecord
from src.runtime_lifecycle import completed_lifecycle_patch
from src.search.config import SearchConfig
from src.search.models import SearchExecutionResult, SearchExecutionStats
from src.state import (
    Finding,
    MemoryItem,
    ReportSection,
    ResearchPlan,
    ResearchState,
    SearchQuery,
    SearchResult,
)


NOW = datetime(2026, 8, 21, tzinfo=timezone.utc)


def _record(statement: str, source: str, *, created_at: str = "2026-08-21T00:00:00+00:00"):
    state = ResearchState(
        research_topic="AI regulation",
        query="AI regulation",
        run_id="run-memory",
        findings=[Finding(finding_id="finding", statement=statement)],
        search_results=[
            SearchResult(
                query="AI regulation",
                title="Source",
                url=source,
                snippet="Snippet",
            )
        ],
        report_sections=[ReportSection(title="Summary", content="Summary", sources=[source])],
        **completed_lifecycle_patch(),
    )
    return project_research_memories(state, now=NOW)[0].model_copy(
        update={"created_at": created_at, "updated_at": created_at}
    )


def test_memory_projection_prefers_evidence_provenance_and_legacy_fallback() -> None:
    evidence = Evidence(
        evidence_id="ev-1",
        document_id="doc-1",
        source_url="https://example.com/evidence",
        claim="EU AI Act uses risk tiers",
        source_quote="risk tiers",
        text_source="snippet",
        relation="supports",
        status="grounded",
    )
    evidence_state = ResearchState(
        research_topic="AI regulation",
        query="AI regulation",
        run_id="run-evidence",
        findings=[
            Finding(
                finding_id="finding-1",
                statement="EU AI Act uses a risk-based regulatory structure.",
                evidence_refs=["ev-1"],
            )
        ],
        evidence=[evidence],
        **completed_lifecycle_patch(),
    )

    records = project_research_memories(evidence_state, now=NOW)

    assert len(records) == 1
    assert records[0].source_refs == ["https://example.com/evidence"]
    assert records[0].evidence_refs == ["ev-1"]
    assert records[0].document_refs == ["doc-1"]
    assert records[0].grounding_level == "evidence_grounded"
    assert records[0].provenance["run_id"] == "run-evidence"

    legacy = _record("Legacy source-backed finding", "https://example.com/legacy")
    assert legacy.grounding_level == "legacy_source_url"
    assert legacy.source_refs == ["https://example.com/legacy"]


def test_memory_store_deduplicates_retrieves_deletes_and_prunes(tmp_path) -> None:
    store = ResearchMemoryStore(tmp_path / "memory.db", max_records=2)
    first = _record("AI regulation uses risk tiers", "https://eu.example/source")
    duplicate = _record("AI regulation uses risk tiers", "https://eu.example/source")
    second = _record(
        "Semiconductor supply chain resilience depends on supplier diversity",
        "https://chips.example/source",
        created_at="2026-08-20T00:00:00+00:00",
    )
    third = _record(
        "Clinical implementation requires workflow adaptation",
        "https://clinical.example/source",
        created_at="2026-08-22T00:00:00+00:00",
    )

    stored_first = store.upsert(first)
    stored_duplicate = store.upsert(duplicate)

    assert stored_duplicate.memory_id == stored_first.memory_id
    assert store.count() == 1

    store.upsert(second)
    store.upsert(third)

    assert store.count() == 2
    retrieved = store.retrieve("AI regulation risk", limit=2)
    assert retrieved[0][0].statement == "AI regulation uses risk tiers"
    assert retrieved[0][1] > 0
    assert store.delete(retrieved[0][0].memory_id) is True
    assert store.get(retrieved[0][0].memory_id) is None

    expired = _record("Expired memory", "https://old.example/source").model_copy(
        update={"expires_at": (NOW - timedelta(days=1)).isoformat()}
    )
    store.upsert(expired)
    assert store.get(expired.memory_id) is None


def test_planner_injects_memory_as_prior_context_and_projects_state(monkeypatch, tmp_path) -> None:
    from src import agents as agents_module

    monkeypatch.setattr(agents_module.config, "research_memory_enabled", True)
    store = ResearchMemoryStore(tmp_path / "memory.db")
    store.upsert(_record("AI regulation uses risk tiers", "https://eu.example/source"))
    captured_prompt = ""

    async def fake_plan(prompt):
        nonlocal captured_prompt
        captured_prompt = str(prompt)
        return (
            '{"topic":"AI regulation","objectives":["Compare approaches"],'
            '"search_queries":[{"query":"AI regulation comparison","purpose":"compare"}],'
            '"report_outline":["Summary"]}'
        )

    planner = ResearchPlanner(
        llm=RunnableLambda(fake_plan),
        max_retries=1,
        memory_store=store,
    )
    patch = asyncio.run(planner.plan(ResearchState(research_topic="AI regulation risk")))

    assert "Use these source-backed memories only as prior context" in captured_prompt
    assert "AI regulation uses risk tiers" in captured_prompt
    assert patch["retrieved_memory"]
    assert patch["memory_ids"] == [patch["retrieved_memory"][0].memory_id]
    assert patch["research_plan"].search_queries[0].query == "AI regulation comparison"


class _FakeCredibilityScorer:
    def score_search_results(self, results: list[SearchResult]) -> list[dict]:
        return [
            {"result": result, "credibility": {"score": 90, "level": "high"}}
            for result in results
        ]


class _FakeSearchExecutor:
    def __init__(self) -> None:
        self.queries: list[SearchQuery] = []

    async def execute(self, queries, **_kwargs) -> SearchExecutionResult:
        self.queries = list(queries)
        return SearchExecutionResult(
            search_results=[
                SearchResult(
                    query=self.queries[0].query,
                    title="Result",
                    url="https://result.example/source",
                    snippet="Result snippet",
                )
            ],
            stats=SearchExecutionStats(search_calls=len(self.queries), elapsed_seconds=0.1),
        )


def test_searcher_adds_provenance_bearing_memory_hints_without_bypassing_executor() -> None:
    plan = ResearchPlan(
        topic="AI regulation",
        objectives=["Compare"],
        search_queries=[SearchQuery(query="AI regulation comparison", purpose="primary")],
        report_outline=["Summary"],
    )
    memory = MemoryItem(
        memory_id="mem-1",
        memory_type="research_memory",
        content="Prior context",
        metadata={
            "source_refs": ["https://eu.example/path"],
            "grounding_level": "legacy_source_url",
        },
    )
    state = ResearchState(
        research_topic="AI regulation",
        plan=plan,
        retrieved_memory=[memory],
        memory_ids=[memory.memory_id],
    )
    searcher = ResearchSearcher(
        llm=object(),
        credibility_scorer=_FakeCredibilityScorer(),
        search_config=SearchConfig(mode="deterministic_v2", max_results_per_search=1),
        memory_store=None,
    )
    fake_executor = _FakeSearchExecutor()
    searcher.search_executor = fake_executor

    patch = asyncio.run(searcher.search(state))

    assert [query.query for query in fake_executor.queries] == [
        "AI regulation comparison",
        "AI regulation site:eu.example",
    ]
    assert patch["llm_call_details"][-1]["memory_hint_count"] == 1
    assert patch["memory_ids"] == ["mem-1"]
    assert patch["documents"][0].uri == "https://result.example/source"


def test_search_memory_hints_require_source_provenance() -> None:
    assert build_search_memory_hints(
        "topic",
        [MemoryItem(memory_id="no-source", content="No source", metadata={})],
        limit=2,
    ) == []


def test_completed_run_memory_write_is_nonfatal_and_skips_failed_runs(monkeypatch, tmp_path) -> None:
    store_path = tmp_path / "memory.db"
    monkeypatch.setattr(graph_module.config, "research_memory_store_path", str(store_path))
    monkeypatch.setattr(graph_module.config, "research_memory_enabled", True)
    monkeypatch.setattr(graph_module.config, "research_memory_ttl_days", 30)
    monkeypatch.setattr(graph_module.config, "research_memory_max_records", 100)

    completed = {
        "research_topic": "AI regulation",
        "query": "AI regulation",
        "run_id": "run-complete",
        "findings": [{"finding_id": "finding", "statement": "AI memory finding"}],
        "search_results": [
            {
                "query": "AI regulation",
                "title": "Source",
                "url": "https://example.com/source",
                "snippet": "Snippet",
            }
        ],
        "report_sections": [
            {"title": "Summary", "content": "Content", "sources": ["https://example.com/source"]}
        ],
        "status": "completed",
        "current_stage": "complete",
        "terminal_reason": "completed",
    }

    graph_module._persist_completed_research_memory(completed)
    assert ResearchMemoryStore(store_path).count() == 1

    failed = dict(completed, run_id="run-failed", status="failed", terminal_reason="agent_failed")
    graph_module._persist_completed_research_memory(failed)
    assert ResearchMemoryStore(store_path).count() == 1

    class BrokenStore:
        def __init__(self, *_args, **_kwargs):
            pass

        def upsert_many(self, _records):
            raise RuntimeError("fake storage failure")

    monkeypatch.setattr(graph_module, "ResearchMemoryStore", BrokenStore)
    graph_module._persist_completed_research_memory(completed)
