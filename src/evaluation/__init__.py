"""Stable, read-only evaluation APIs for the portfolio workflow.

The default evaluator covers planning quality, research coverage, citation
integrity, optional Evidence grounding, usage/latency, and the fixed showcase.
The package intentionally exposes only the stable portfolio evaluator surface.
"""

from src.evaluation.dataset import FIXED_EVALUATION_DATASET
from src.evaluation.evaluator import EVALUATOR_VERSION, evaluate_run
from src.evaluation.planning_quality import (
    PLANNING_QUALITY_EVALUATOR_VERSION,
    PLANNING_QUALITY_METRICS,
    evaluate_planning_quality,
)
from src.evaluation.research_coverage import (
    RESEARCH_COVERAGE_EVALUATOR_VERSION,
    RESEARCH_COVERAGE_METRICS,
    evaluate_research_coverage,
)
from src.evaluation.runner import run_offline_evaluation
from src.evaluation.showcase import (
    SHOWCASE_CASES,
    SHOWCASE_VERSION,
    archive_showcase,
    manual_deepseek_tavily_showcase_runner,
    run_showcase,
)
from src.evaluation.snapshot import (
    EVALUATION_SNAPSHOT_VERSION,
    build_evaluation_snapshot,
    evaluation_dataset_content_fingerprint,
    validate_evaluation_snapshot,
)

__all__ = [
    "EVALUATION_SNAPSHOT_VERSION",
    "EVALUATOR_VERSION",
    "FIXED_EVALUATION_DATASET",
    "PLANNING_QUALITY_EVALUATOR_VERSION",
    "PLANNING_QUALITY_METRICS",
    "RESEARCH_COVERAGE_EVALUATOR_VERSION",
    "RESEARCH_COVERAGE_METRICS",
    "SHOWCASE_CASES",
    "SHOWCASE_VERSION",
    "archive_showcase",
    "build_evaluation_snapshot",
    "evaluate_planning_quality",
    "evaluate_research_coverage",
    "evaluate_run",
    "evaluation_dataset_content_fingerprint",
    "manual_deepseek_tavily_showcase_runner",
    "run_offline_evaluation",
    "run_showcase",
    "validate_evaluation_snapshot",
]
