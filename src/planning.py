"""Deterministic normalization for the existing ResearchPlan contract."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any

from src.state import ResearchPlan, SearchQuery


PLANNING_PURPOSE_CATEGORIES = (
    "background",
    "mechanism",
    "comparison",
    "authority",
    "implementation",
    "risk_limitations",
    "trends",
)

_PURPOSE_PREFIX = re.compile(
    r"^\s*\[?(?:background|mechanism|comparison|authority|implementation|risk_limitations|trends)\]?\s*[:\-\u2014\uff1a]\s*",
    re.IGNORECASE,
)
_WHITESPACE = re.compile(r"\s+")
_CATEGORY_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("comparison", (" compare", "comparison", " vs ", "versus", "alternative", "\u6bd4\u8f83", "\u5bf9\u6bd4", "\u66ff\u4ee3")),
    ("authority", ("official", "policy", "regulation", "guideline", "standard", "paper", "study", "evidence", "\u5b98\u65b9", "\u653f\u7b56", "\u6cd5\u89c4", "\u6307\u5357", "\u6807\u51c6", "\u8bba\u6587", "\u8bc1\u636e")),
    ("mechanism", ("how ", "mechanism", "architecture", "workflow", "process", "\u539f\u7406", "\u673a\u5236", "\u67b6\u6784", "\u6d41\u7a0b")),
    ("implementation", ("implement", "implementation", "adoption", "practice", "case study", "deployment", "\u5b9e\u65bd", "\u843d\u5730", "\u5b9e\u8df5", "\u6848\u4f8b", "\u90e8\u7f72")),
    ("risk_limitations", ("risk", "challenge", "limitation", "safety", "failure", "barrier", "\u98ce\u9669", "\u6311\u6218", "\u5c40\u9650", "\u5b89\u5168", "\u969c\u788d")),
    ("trends", ("latest", "trend", "future", "2024", "2025", "2026", "\u8d8b\u52bf", "\u672a\u6765", "\u6700\u65b0", "\u8fdb\u5c55")),
)


def _text(value: Any) -> str:
    return _WHITESPACE.sub(" ", str(value or "")).strip()


def _dedupe_text(values: Sequence[Any], *, limit: int) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _text(value)
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
        if len(cleaned) >= limit:
            break
    return cleaned


def _query_category(query: str, purpose: str) -> str:
    # Prefer the Planner's explicit rationale. Topic words such as
    # "regulation" are common across all queries and should only be a fallback.
    for candidate in (purpose, query):
        text = f" {_text(candidate).casefold()} "
        if "case study" in text or "\u6848\u4f8b" in text:
            return "implementation"
        for category, hints in _CATEGORY_HINTS:
            if any(hint.casefold() in text for hint in hints):
                return category
    return "background"


def normalize_query_purpose(query: str, purpose: Any) -> str:
    """Preserve the LLM rationale while adding one deterministic intent label."""

    clean_purpose = _PURPOSE_PREFIX.sub("", _text(purpose))
    category = _query_category(query, clean_purpose)
    detail = clean_purpose or "Investigate this distinct research dimension"
    return f"{category}: {detail}"


def purpose_category(purpose: str) -> str:
    """Read the normalized label, returning ``background`` for legacy text."""

    match = re.match(
        r"^\s*(background|mechanism|comparison|authority|implementation|risk_limitations|trends)\s*:",
        _text(purpose),
        flags=re.IGNORECASE,
    )
    return match.group(1).casefold() if match else "background"


def normalize_research_plan(
    payload: Mapping[str, Any],
    *,
    max_queries: int,
    max_sections: int,
) -> ResearchPlan:
    """Normalize duplicates and query intent without adding planner work or fields.

    The function deliberately never invents objectives, searches, or report
    sections. It only removes blank/duplicate entries and annotates the purpose
    of a query already supplied by the Planner LLM.
    """

    topic = _text(payload.get("topic"))
    objectives = _dedupe_text(payload.get("objectives") or (), limit=5)
    outline = _dedupe_text(payload.get("report_outline") or (), limit=max_sections)

    queries: list[SearchQuery] = []
    seen_queries: set[str] = set()
    raw_queries = payload.get("search_queries") or ()
    for item in raw_queries:
        if not isinstance(item, Mapping):
            continue
        query = _text(item.get("query"))
        key = query.casefold()
        if not query or key in seen_queries:
            continue
        seen_queries.add(key)
        queries.append(
            SearchQuery(
                query=query,
                purpose=normalize_query_purpose(query, item.get("purpose")),
            )
        )
        if len(queries) >= max_queries:
            break

    return ResearchPlan(
        topic=topic,
        objectives=objectives,
        search_queries=queries,
        report_outline=outline,
    )
