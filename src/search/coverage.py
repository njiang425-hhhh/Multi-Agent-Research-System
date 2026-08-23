"""Pure query-level coverage helpers for bounded adaptive search."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from src.state import Document, ResearchPlan, SearchQuery, SearchResult


def _normalize_query(query: str) -> str:
    return " ".join((query or "").lower().split())


def _has_content(value: str | None) -> bool:
    return bool(value and value.strip())


def _document_query(document: Document) -> str:
    metadata = document.metadata if isinstance(document.metadata, dict) else {}
    return str(metadata.get("search_query") or "")


@dataclass(frozen=True, slots=True)
class SearchCoverage:
    """Observed plan-query coverage without evaluation or runtime dependencies."""

    result_query_coverage: float
    extracted_query_coverage: float
    missing_result_queries: tuple[SearchQuery, ...]
    missing_extracted_queries: tuple[SearchQuery, ...]


def measure_search_coverage(
    plan: ResearchPlan,
    search_results: Sequence[SearchResult],
    documents: Sequence[Document] = (),
) -> SearchCoverage:
    """Measure result and content coverage for the plan's existing queries.

    A query is result-covered when a returned result carries a URL. It is
    extraction-covered when a matching result or projected document has
    non-empty content. Query matching is normalized only for case and spacing.
    """

    planned_queries = [
        query for query in plan.search_queries if _normalize_query(query.query)
    ]
    if not planned_queries:
        return SearchCoverage(1.0, 1.0, (), ())

    result_queries = {
        _normalize_query(result.query)
        for result in search_results
        if _normalize_query(result.query) and bool(result.url and result.url.strip())
    }
    extracted_queries = {
        _normalize_query(result.query)
        for result in search_results
        if _normalize_query(result.query) and _has_content(result.content)
    }
    extracted_queries.update(
        _normalize_query(_document_query(document))
        for document in documents
        if _normalize_query(_document_query(document)) and _has_content(document.content)
    )

    missing_result = tuple(
        query
        for query in planned_queries
        if _normalize_query(query.query) not in result_queries
    )
    missing_extracted = tuple(
        query
        for query in planned_queries
        if _normalize_query(query.query) not in extracted_queries
    )
    total = len(planned_queries)
    return SearchCoverage(
        result_query_coverage=(total - len(missing_result)) / total,
        extracted_query_coverage=(total - len(missing_extracted)) / total,
        missing_result_queries=missing_result,
        missing_extracted_queries=missing_extracted,
    )
