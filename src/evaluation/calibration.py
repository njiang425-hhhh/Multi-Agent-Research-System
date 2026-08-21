"""Reference-backed, deterministic calibration for the read-only quality rubric."""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.evaluation.contracts import EvaluationCase, EvaluationDataset, RunEvaluationResult
from src.evaluation.evaluator import EVALUATOR_VERSION, EXPECTED_COMPLETED_NODES, evaluate_run
from src.evaluation.snapshot import build_evaluation_snapshot


CALIBRATION_VERSION = "p6.calibration.v1"
_QUALITY_METRICS = ("source_coverage", "grounded_citation", "report_completeness")


class ReferenceSource(BaseModel):
    """Stable source identity explaining a hand-authored calibration oracle."""

    reference_id: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class ReferenceQualityExpectation(BaseModel):
    """Known structural/provenance signals expected from one reference fixture.

    ``grounded_citation`` intentionally means URL/provenance grounding only. It
    does not assert factual correctness or semantic entailment of a claim.
    """

    metric_statuses: dict[str, Literal["passed", "failed", "unavailable"]]
    distinct_source_count: int = Field(ge=0)
    grounded_citation_count: int = Field(ge=0)
    report_section_count: int = Field(ge=0)
    rationale: str = Field(min_length=1)


class CalibrationCase(BaseModel):
    """A P6 calibration case, kept separate from the P5 workload dataset."""

    calibration_id: str = Field(min_length=1)
    case: EvaluationCase
    reference_sources: list[ReferenceSource] = Field(min_length=1)
    expected: ReferenceQualityExpectation


class CalibrationDataset(BaseModel):
    dataset_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    cases: list[CalibrationCase] = Field(min_length=1)


class CalibrationSignalResult(BaseModel):
    name: str
    expected: Any
    observed: Any
    status: Literal["passed", "failed"]


class CalibrationCaseResult(BaseModel):
    """Derived agreement between a reference oracle and existing evaluator output."""

    data_kind: Literal["derived"] = "derived"
    calibration_id: str
    case_id: str
    reference_ids: list[str]
    evaluation: RunEvaluationResult
    signals: list[CalibrationSignalResult] = Field(default_factory=list)
    alignment_status: Literal["passed", "failed"]


class CalibrationSummary(BaseModel):
    total_cases: int = 0
    aligned_cases: int = 0
    misaligned_cases: int = 0
    signal_count: int = 0
    matched_signal_count: int = 0


class CalibrationResult(BaseModel):
    """Read-only calibration output, not a production quality gate."""

    calibration_version: str = CALIBRATION_VERSION
    evaluation_snapshot_fingerprint: str
    calibration_content_fingerprint: str
    dataset_id: str
    dataset_version: str
    cases: list[CalibrationCaseResult] = Field(default_factory=list)
    summary: CalibrationSummary = Field(default_factory=CalibrationSummary)
    configuration: dict[str, Any] = Field(default_factory=dict)


CalibrationRunner = Callable[[CalibrationCase], Any | Awaitable[Any]]


def calibration_content_fingerprint(dataset: CalibrationDataset) -> str:
    """Fingerprint references, expectations, rationales, and ordered case content."""

    encoded = json.dumps(dataset.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _metric_status(evaluation: RunEvaluationResult, name: str) -> str:
    return next(metric.status for metric in evaluation.metrics if metric.name == name)


def _signals(case: CalibrationCase, evaluation: RunEvaluationResult) -> list[CalibrationSignalResult]:
    signals: list[CalibrationSignalResult] = []
    for name in _QUALITY_METRICS:
        if name not in case.expected.metric_statuses:
            continue
        observed = _metric_status(evaluation, name)
        expected = case.expected.metric_statuses[name]
        signals.append(
            CalibrationSignalResult(
                name=f"metric:{name}", expected=expected, observed=observed,
                status="passed" if expected == observed else "failed",
            )
        )
    for name, observed in {
        "distinct_source_count": evaluation.quality.distinct_source_count,
        "grounded_citation_count": evaluation.quality.grounded_citation_count,
        "report_section_count": evaluation.report.section_count,
    }.items():
        expected = getattr(case.expected, name)
        signals.append(
            CalibrationSignalResult(
                name=name, expected=expected, observed=observed,
                status="passed" if expected == observed else "failed",
            )
        )
    return signals


async def run_rubric_calibration(
    run_case: CalibrationRunner,
    *,
    dataset: CalibrationDataset,
    configuration: Mapping[str, Any] | None = None,
) -> CalibrationResult:
    """Compare injected reference fixtures against existing metrics without mutation.

    This function does not construct Graph, providers, LLMs, or Evidence
    services. The caller owns fixture construction and any execution boundary.
    """

    evaluation_dataset = EvaluationDataset(
        dataset_id=dataset.dataset_id,
        version=dataset.version,
        cases=[item.case for item in dataset.cases],
    )
    fingerprint = calibration_content_fingerprint(dataset)
    resolved_configuration = {
        **dict(configuration or {}),
        "calibration_version": CALIBRATION_VERSION,
        "calibration_content_fingerprint": fingerprint,
    }
    snapshot = build_evaluation_snapshot(
        evaluator_version=EVALUATOR_VERSION,
        dataset=evaluation_dataset,
        expected_completed_nodes=EXPECTED_COMPLETED_NODES,
        configuration=resolved_configuration,
    )
    results: list[CalibrationCaseResult] = []
    for calibration_case in dataset.cases:
        try:
            state = run_case(calibration_case)
            if inspect.isawaitable(state):
                state = await state
        except Exception as exc:
            state = {
                "research_topic": calibration_case.case.query,
                "error": f"calibration runner failed: {exc}",
            }
        evaluation = evaluate_run(
            state,
            case=calibration_case.case,
            dataset=evaluation_dataset,
            configuration=resolved_configuration,
            evaluation_snapshot=snapshot,
        )
        signals = _signals(calibration_case, evaluation)
        results.append(
            CalibrationCaseResult(
                calibration_id=calibration_case.calibration_id,
                case_id=calibration_case.case.case_id,
                reference_ids=[item.reference_id for item in calibration_case.reference_sources],
                evaluation=evaluation,
                signals=signals,
                alignment_status="passed" if all(item.status == "passed" for item in signals) else "failed",
            )
        )
    signal_count = sum(len(item.signals) for item in results)
    matched = sum(item.status == "passed" for result in results for item in result.signals)
    aligned = sum(item.alignment_status == "passed" for item in results)
    return CalibrationResult(
        evaluation_snapshot_fingerprint=snapshot.fingerprint,
        calibration_content_fingerprint=fingerprint,
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.version,
        cases=results,
        summary=CalibrationSummary(
            total_cases=len(results), aligned_cases=aligned,
            misaligned_cases=len(results) - aligned, signal_count=signal_count,
            matched_signal_count=matched,
        ),
        configuration=resolved_configuration,
    )
