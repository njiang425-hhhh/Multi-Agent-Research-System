"""Public exports for deterministic search components.

Exports are loaded lazily so importing ``src.search.providers`` does not
eagerly import SearchExecutor and, through it, ``src.utils.tools``.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.search.config import SearchConfig
    from src.search.executor import SearchExecutor
    from src.search.models import SearchExecutionResult, SearchExecutionStats

__all__ = [
    "SearchConfig",
    "SearchExecutor",
    "SearchExecutionResult",
    "SearchExecutionStats",
]


def __getattr__(name: str) -> Any:
    """Load public objects on first access without package import side effects."""
    if name == "SearchConfig":
        from src.search.config import SearchConfig

        return SearchConfig
    if name == "SearchExecutor":
        from src.search.executor import SearchExecutor

        return SearchExecutor
    if name in {"SearchExecutionResult", "SearchExecutionStats"}:
        from src.search.models import SearchExecutionResult, SearchExecutionStats

        return {
            "SearchExecutionResult": SearchExecutionResult,
            "SearchExecutionStats": SearchExecutionStats,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
