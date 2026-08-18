"""Offline, deterministic evaluation APIs."""

from src.evaluation.dataset import FIXED_EVALUATION_DATASET
from src.evaluation.evaluator import EVALUATOR_VERSION, evaluate_run
from src.evaluation.runner import run_offline_evaluation
from src.evaluation.snapshot import (
    EVALUATION_SNAPSHOT_VERSION,
    build_evaluation_snapshot,
    evaluation_dataset_content_fingerprint,
    validate_evaluation_snapshot,
)

__all__ = ["EVALUATOR_VERSION", "EVALUATION_SNAPSHOT_VERSION", "FIXED_EVALUATION_DATASET", "build_evaluation_snapshot", "evaluation_dataset_content_fingerprint", "validate_evaluation_snapshot", "evaluate_run", "run_offline_evaluation"]
