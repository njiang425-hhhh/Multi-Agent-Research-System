"""Runtime limits for standalone document analysis."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AnalyzerConfig:
    """Deterministic runtime controls owned by ``ResultAnalyzer``.

    These settings deliberately do not control search or webpage extraction.
    """

    max_documents: int = 8
    max_chars_per_document: int = 5_000
    total_timeout_seconds: float = 90.0
    retry_times: int = 1
    allow_partial_results: bool = True

    def __post_init__(self) -> None:
        integer_limits = {
            "max_documents": self.max_documents,
            "max_chars_per_document": self.max_chars_per_document,
            "retry_times": self.retry_times,
        }
        for name, value in integer_limits.items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")

        if self.max_chars_per_document <= 0:
            raise ValueError("max_chars_per_document must be greater than zero")
        if self.total_timeout_seconds <= 0:
            raise ValueError("total_timeout_seconds must be greater than zero")
