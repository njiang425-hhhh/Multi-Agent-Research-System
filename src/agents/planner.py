"""Planner entry point for the ResearchOS pipeline.

``ResearchPlanner`` turns a user question into objectives, bounded search
queries, and a report outline.  The Phase 1 wrapper preserves the established
implementation and public constructor exactly.
"""

from src.agents._implementation import ResearchPlanner

__all__ = ["ResearchPlanner"]
