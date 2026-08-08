"""Data models used only during one deterministic search run."""

from dataclasses import dataclass, field
from typing import List, Optional

from src.state import SearchResult


@dataclass(slots=True)
class SearchExecutionStats:
    """Runtime counters for observability and tests."""

    search_calls: int = 0
    extract_calls: int = 0
    search_retries: int = 0
    extract_retries: int = 0
    failed_calls: int = 0
    elapsed_seconds: float = 0.0


@dataclass(slots=True)
class SearchExecutionResult:
    """Result returned by SearchExecutor before credibility scoring."""

    search_results: List[SearchResult] = field(default_factory=list)
    error: Optional[str] = None
    completed: bool = True
    partial: bool = False
    stats: SearchExecutionStats = field(default_factory=SearchExecutionStats)
