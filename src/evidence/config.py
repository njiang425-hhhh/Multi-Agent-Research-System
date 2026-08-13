"""Configuration for standalone Evidence analysis and its optional sidecar."""

import os
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


@dataclass(frozen=True, slots=True)
class EvidenceRuntimeConfig:
    """Feature switch plus analyzer-owned runtime limits.

    The flag controls only sidecar invocation.  It has no authority over the
    Searcher runtime or LangGraph routing.
    """

    enabled: bool = False
    analyzer: AnalyzerConfig = AnalyzerConfig()

    @classmethod
    def from_environment(cls) -> "EvidenceRuntimeConfig":
        return cls(
            enabled=os.getenv("EVIDENCE_ANALYZER_ENABLED", "false").lower()
            in {"1", "true", "yes", "on"},
            analyzer=AnalyzerConfig(
                max_documents=int(os.getenv("EVIDENCE_MAX_DOCUMENTS", "8")),
                max_chars_per_document=int(os.getenv("EVIDENCE_MAX_CHARS_PER_DOCUMENT", "5000")),
                total_timeout_seconds=float(os.getenv("EVIDENCE_TOTAL_TIMEOUT_SECONDS", "90")),
                retry_times=int(os.getenv("EVIDENCE_RETRY_TIMES", "1")),
                allow_partial_results=os.getenv(
                    "EVIDENCE_ALLOW_PARTIAL_RESULTS", "true"
                ).lower()
                in {"1", "true", "yes", "on"},
            ),
        )
