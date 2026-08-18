"""Configuration for deterministic search execution."""

import os
from dataclasses import dataclass
from typing import Literal


SearchMode = Literal["legacy_agent", "deterministic_v2"]


@dataclass(frozen=True, slots=True)
class SearchConfig:
    """Hard runtime limits owned by the Search Executor.

    ``deterministic_v2`` is the supported default: its external operations use
    the shared runtime execution contract. ``legacy_agent`` remains an
    explicit compatibility mode for historical callers; its opaque tool loop
    does not claim the P4 global operation-budget/retry guarantees.
    """

    mode: SearchMode = "deterministic_v2"
    max_search_times: int = 3
    max_extract_times: int = 4
    max_results_per_search: int = 3
    total_timeout_seconds: float = 90.0
    search_retry_times: int = 0
    extract_retry_times: int = 0
    allow_partial_results: bool = True

    def __post_init__(self) -> None:
        if self.mode not in ("legacy_agent", "deterministic_v2"):
            raise ValueError(
                "SearchConfig.mode must be 'legacy_agent' or 'deterministic_v2'"
            )

        integer_limits = {
            "max_search_times": self.max_search_times,
            "max_extract_times": self.max_extract_times,
            "max_results_per_search": self.max_results_per_search,
            "search_retry_times": self.search_retry_times,
            "extract_retry_times": self.extract_retry_times,
        }
        for name, value in integer_limits.items():
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")

        if self.total_timeout_seconds <= 0:
            raise ValueError("total_timeout_seconds must be greater than zero")

    @classmethod
    def from_project_config(cls, project_config: object) -> "SearchConfig":
        """Build a SearchConfig from the existing project configuration.

        The mode is deliberately read from SEARCHER_MODE so the existing
        config.py and Graph do not need to know about the new executor.
        """

        mode = os.getenv("SEARCHER_MODE", "deterministic_v2")
        return cls(
            mode=mode,
            max_search_times=min(int(project_config.max_search_queries), 3),
            max_extract_times=min(int(project_config.max_search_queries) + 1, 4),
            max_results_per_search=min(
                int(project_config.max_search_results_per_query), 3
            ),
            total_timeout_seconds=float(os.getenv("SEARCHER_TOTAL_TIMEOUT_SECONDS", "90")),
            search_retry_times=int(os.getenv("SEARCHER_SEARCH_RETRY_TIMES", "0")),
            extract_retry_times=int(os.getenv("SEARCHER_EXTRACT_RETRY_TIMES", "0")),
            allow_partial_results=os.getenv(
                "SEARCHER_ALLOW_PARTIAL_RESULTS", "true"
            ).lower()
            in {"1", "true", "yes", "on"},
        )
