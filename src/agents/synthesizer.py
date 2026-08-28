"""Synthesizer entry point for the ResearchOS pipeline."""

from src.agents import _implementation


class ResearchSynthesizer(_implementation.ResearchSynthesizer):
    """Compatibility-preserving Synthesizer facade.

    Existing tests replace ``src.agents.create_agent`` with a fake.  Forward
    that explicit injection seam to the relocated implementation.
    """

    async def synthesize(self, *args, **kwargs):
        from src import agents as public_agents

        original = _implementation.create_agent
        _implementation.create_agent = public_agents.create_agent
        try:
            return await super().synthesize(*args, **kwargs)
        finally:
            _implementation.create_agent = original

__all__ = ["ResearchSynthesizer"]
