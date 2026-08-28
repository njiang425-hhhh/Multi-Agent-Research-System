"""Searcher entry point for the ResearchOS pipeline.

The default execution mode is the deterministic, budgeted SearchExecutor.
The legacy autonomous search path remains available only for compatibility and
is documented as an experiment rather than part of the portfolio narrative.
"""

from src.agents import _implementation


class ResearchSearcher(_implementation.ResearchSearcher):
    """Compatibility-preserving Searcher facade.

    The temporary facade keeps the long-standing ``src.agents.create_agent``
    test injection seam while the implementation is being relocated.
    """

    async def search(self, *args, **kwargs):
        from src import agents as public_agents

        original = _implementation.create_agent
        _implementation.create_agent = public_agents.create_agent
        try:
            return await super().search(*args, **kwargs)
        finally:
            _implementation.create_agent = original

__all__ = ["ResearchSearcher"]
