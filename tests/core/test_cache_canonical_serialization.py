"""Regression coverage for canonical ResearchState cache round trips."""

from src.runtime_lifecycle import build_cache_replay_state
from src.state import Document, Finding, Report, ReportSection, ResearchPlan, ResearchState, SearchQuery
from src.state_compat import canonical_documents, canonical_findings, canonical_plan, canonical_report
from src.utils.cache import ResearchCache


def _completed_state() -> ResearchState:
    topic = "cache canonical round trip"
    first_url = "https://example.com/first"
    second_url = "https://example.com/second"
    plan = ResearchPlan(
        topic=topic,
        objectives=["compare authoritative sources"],
        search_queries=[SearchQuery(query="first", purpose="authority"), SearchQuery(query="second", purpose="comparison")],
        report_outline=["Summary"],
    )
    documents = [
        Document(document_id="doc-first", title="First", uri=first_url, snippet="first"),
        Document(document_id="doc-second", title="Second", uri=second_url, snippet="second"),
    ]
    findings = [
        Finding(
            finding_id="finding-1",
            statement="First and second sources differ.",
            source_document_ids=["doc-first", "doc-second"],
        )
    ]
    section = ReportSection(
        title="Summary",
        content="The sources differ [1] [2].",
        sources=[first_url, second_url],
    )
    report = Report(
        title=topic,
        sections=[section],
        content="# Cache canonical round trip\n\n## Summary\n\nThe sources differ [1] [2].",
        citations=[first_url, second_url],
        status="completed",
    )
    return ResearchState(
        research_topic=topic,
        query=topic,
        research_plan=plan,
        documents=documents,
        findings=findings,
        report=report,
        status="completed",
        current_stage="complete",
    )


def test_cache_round_trip_preserves_the_complete_canonical_research_contract(tmp_path) -> None:
    original = _completed_state()
    topic = original.query
    cache = ResearchCache(cache_dir=tmp_path / "research-cache")

    cache.set(topic, original)
    restored_payload = ResearchCache(cache_dir=tmp_path / "research-cache").get(topic)

    assert restored_payload is not None
    replay = build_cache_replay_state(restored_payload)
    assert replay is not None
    restored_plan = canonical_plan(replay)
    restored_report = canonical_report(replay)

    assert restored_plan == original.research_plan
    assert [document.document_id for document in canonical_documents(replay)] == [
        "doc-first",
        "doc-second",
    ]
    assert [document.uri for document in canonical_documents(replay)] == [
        "https://example.com/first",
        "https://example.com/second",
    ]
    assert canonical_findings(replay) == original.findings
    assert canonical_findings(replay)[0].source_document_ids == ["doc-first", "doc-second"]
    assert restored_report == original.report
    assert restored_report.citations == [document.uri for document in canonical_documents(replay)]


def test_cache_rejects_pre_schema_string_fallback_payloads(tmp_path) -> None:
    cache = ResearchCache(cache_dir=tmp_path / "research-cache")
    topic = "stale cache"
    cache._cache[cache._get_key(topic)] = {
        "topic": topic,
        "timestamp": "2026-09-06T00:00:00",
        "data": {"final_report": "# stale report", "documents": ["Document(...)"]},
    }
    cache._save_cache()

    assert ResearchCache(cache_dir=tmp_path / "research-cache").get(topic) is None


def test_cache_v2_hydrates_a_legacy_business_payload_without_overwriting_canonical_values(tmp_path) -> None:
    cache = ResearchCache(cache_dir=tmp_path / "research-cache")
    topic = "legacy v2 cache"
    legacy = ResearchState(
        research_topic="legacy topic",
        plan=ResearchPlan(
            topic="legacy topic",
            objectives=["legacy objective"],
            search_queries=[SearchQuery(query="legacy query", purpose="legacy")],
            report_outline=["Summary"],
        ),
    )
    cache._cache[cache._get_key(topic)] = {
        "topic": topic,
        "timestamp": "2026-09-06T00:00:00",
        "data": {
            "cache_schema_version": 2,
            "state": legacy.model_dump(mode="json"),
        },
    }
    cache._save_cache()

    restored = ResearchCache(cache_dir=tmp_path / "research-cache").get(topic)

    assert restored is not None
    assert canonical_plan(restored).topic == "legacy topic"
