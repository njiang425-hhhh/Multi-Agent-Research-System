"""Writer entry point for the ResearchOS pipeline.

Serial section generation is the default portfolio path.  The bounded writer
mode is retained for compatibility and recorded under experiments.
"""

from src.agents._implementation import ReportWriter

__all__ = ["ReportWriter"]
