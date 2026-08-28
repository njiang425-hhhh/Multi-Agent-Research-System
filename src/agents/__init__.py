"""Public modules for the four-stage ResearchOS agent pipeline.

The implementation remains intentionally compatibility-preserving during
Portfolio Refactor Phase 1.  Existing callers can keep importing from
``src.agents`` while new readers can enter through a role-specific module.
"""

from langchain.agents import create_agent

from src.agents import _implementation
from src.agents._implementation import (
    SEARCHER_AGENT_RECURSION_LIMIT,
    _usage_from_legacy_totals,
    config,
)
from src.agents.planner import ResearchPlanner
from src.agents.searcher import ResearchSearcher
from src.agents.synthesizer import ResearchSynthesizer
from src.agents.writer import ReportWriter

__all__ = [
    "ResearchPlanner",
    "ResearchSearcher",
    "ResearchSynthesizer",
    "ReportWriter",
    "SEARCHER_AGENT_RECURSION_LIMIT",
    "_usage_from_legacy_totals",
    "config",
    "create_agent",
]
