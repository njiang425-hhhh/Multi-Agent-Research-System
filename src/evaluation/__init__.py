"""Offline, deterministic evaluation APIs."""

from src.evaluation.dataset import FIXED_EVALUATION_DATASET
from src.evaluation.evaluator import EVALUATOR_VERSION, evaluate_run
from src.evaluation.evidence_benchmark import (
    BENCHMARK_VERSION,
    DEFAULT_EVIDENCE_BENCHMARK_MODES,
    run_evidence_value_benchmark,
)
from src.evaluation.real_workload import (
    REAL_WORKLOAD_BENCHMARK_VERSION,
    archive_real_workload_benchmark,
    manual_deepseek_tavily_runner,
    refresh_real_workload_derived_metrics,
    run_real_workload_benchmark,
)
from src.evaluation.calibration import (
    CALIBRATION_VERSION,
    calibration_content_fingerprint,
    run_rubric_calibration,
)
from src.evaluation.calibration_dataset import REFERENCE_CALIBRATION_DATASET
from src.evaluation.repeatability import (
    REPEATABILITY_BENCHMARK_VERSION,
    RepeatabilityDecisionCriteria,
    archive_repeatability_benchmark,
    refresh_repeatability_derived_metrics,
    run_repeatability_benchmark,
)
from src.evaluation.research_coverage import (
    RESEARCH_COVERAGE_EVALUATOR_VERSION,
    RESEARCH_COVERAGE_METRICS,
    evaluate_research_coverage,
    research_coverage_content_fingerprint,
)
from src.evaluation.research_coverage_dataset import RESEARCH_COVERAGE_DATASET
from src.evaluation.quality_action import (
    QUALITY_ACTION_EVALUATOR_VERSION,
    QUALITY_ACTION_POLICY_VERSION,
    ActionRecommendation,
    QualityActionEvaluation,
    QualitySignal,
    SignalProvenance,
    ThresholdSpec,
    build_quality_action_advisory,
    planning_quality_signals,
    quality_action_content_fingerprint,
    recommend_action,
    research_coverage_signals,
    run_evaluation_signals,
    runtime_observation_signals,
)
from src.evaluation.runner import run_offline_evaluation
from src.evaluation.snapshot import (
    EVALUATION_SNAPSHOT_VERSION,
    build_evaluation_snapshot,
    evaluation_dataset_content_fingerprint,
    validate_evaluation_snapshot,
)

__all__ = ["ActionRecommendation", "BENCHMARK_VERSION", "CALIBRATION_VERSION", "DEFAULT_EVIDENCE_BENCHMARK_MODES", "EVALUATOR_VERSION", "EVALUATION_SNAPSHOT_VERSION", "FIXED_EVALUATION_DATASET", "QUALITY_ACTION_EVALUATOR_VERSION", "QUALITY_ACTION_POLICY_VERSION", "QualityActionEvaluation", "QualitySignal", "REAL_WORKLOAD_BENCHMARK_VERSION", "REFERENCE_CALIBRATION_DATASET", "REPEATABILITY_BENCHMARK_VERSION", "RESEARCH_COVERAGE_DATASET", "RESEARCH_COVERAGE_EVALUATOR_VERSION", "RESEARCH_COVERAGE_METRICS", "RepeatabilityDecisionCriteria", "SignalProvenance", "ThresholdSpec", "archive_real_workload_benchmark", "archive_repeatability_benchmark", "build_evaluation_snapshot", "build_quality_action_advisory", "calibration_content_fingerprint", "evaluate_research_coverage", "evaluation_dataset_content_fingerprint", "manual_deepseek_tavily_runner", "planning_quality_signals", "quality_action_content_fingerprint", "recommend_action", "refresh_real_workload_derived_metrics", "refresh_repeatability_derived_metrics", "research_coverage_content_fingerprint", "research_coverage_signals", "run_evaluation_signals", "run_evidence_value_benchmark", "run_offline_evaluation", "run_real_workload_benchmark", "run_repeatability_benchmark", "run_rubric_calibration", "runtime_observation_signals", "evaluate_run"]
