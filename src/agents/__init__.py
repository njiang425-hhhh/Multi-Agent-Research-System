"""Public API for the four-stage ResearchOS agent pipeline."""

from langchain.agents import create_agent

from src.agents._llm_support import _usage_from_legacy_totals
from src.agents.planner import ResearchPlanner
from src.agents.searcher import SEARCHER_AGENT_RECURSION_LIMIT, ResearchSearcher
from src.agents.synthesizer import ResearchSynthesizer
from src.agents.writer import ReportWriter
from src.config import config

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
