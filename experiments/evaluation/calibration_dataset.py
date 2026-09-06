"""Versioned P6 calibration fixtures, deliberately separate from P5 workloads."""

from experiments.evaluation.calibration import (
    CalibrationCase,
    CalibrationDataset,
    ReferenceQualityExpectation,
    ReferenceSource,
)
from src.evaluation.contracts import EvaluationCase, ResearchQualityRubric


_ONE = ReferenceSource(
    reference_id="reference-one",
    source_url="https://reference.example.org/one",
    rationale="A stable first source used by the deterministic fixture.",
)
_TWO = ReferenceSource(
    reference_id="reference-two",
    source_url="https://reference.example.org/two",
    rationale="A stable second source used by the deterministic fixture.",
)


REFERENCE_CALIBRATION_DATASET = CalibrationDataset(
    dataset_id="researchos_quality_calibration",
    version="1",
    cases=[
        CalibrationCase(
            calibration_id="grounded-complete",
            case=EvaluationCase(
                case_id="calibration-grounded-complete", query="Reference-backed complete report.",
                quality_rubric=ResearchQualityRubric(
                    min_distinct_sources=2, min_grounded_citations=2, min_report_sections=2,
                    min_report_characters=80,
                ),
            ),
            reference_sources=[_ONE, _TWO],
            expected=ReferenceQualityExpectation(
                metric_statuses={name: "passed" for name in ("source_coverage", "citation_integrity", "report_completeness")},
                distinct_source_count=2, citation_marker_count=2, report_section_count=2,
                rationale="Two distinct reference URLs are cited and both are URL-grounded; the report meets structural minimums.",
            ),
        ),
        CalibrationCase(
            calibration_id="insufficient-source-coverage",
            case=EvaluationCase(
                case_id="calibration-insufficient-source-coverage", query="One retained reference source.",
                quality_rubric=ResearchQualityRubric(
                    min_distinct_sources=2, min_grounded_citations=1, min_report_sections=1,
                    min_report_characters=40,
                ),
            ),
            reference_sources=[_ONE],
            expected=ReferenceQualityExpectation(
                metric_statuses={"source_coverage": "failed", "citation_integrity": "passed", "report_completeness": "passed"},
                distinct_source_count=1, citation_marker_count=1, report_section_count=1,
                rationale="One valid provenance-grounded source cannot satisfy a two-source coverage requirement.",
            ),
        ),
        CalibrationCase(
            calibration_id="ungrounded-report-citation",
            case=EvaluationCase(
                case_id="calibration-ungrounded-report-citation", query="Unsupported report citation.",
                quality_rubric=ResearchQualityRubric(
                    min_distinct_sources=1, min_grounded_citations=1, min_report_sections=1,
                    min_report_characters=40,
                ),
            ),
            reference_sources=[_ONE],
            expected=ReferenceQualityExpectation(
                metric_statuses={"source_coverage": "passed", "citation_integrity": "failed", "report_completeness": "passed"},
                distinct_source_count=1, citation_marker_count=1, report_section_count=1,
                rationale="The fixture includes one valid URL grounding and one report URL without matching Evidence provenance.",
            ),
        ),
        CalibrationCase(
            calibration_id="incomplete-report",
            case=EvaluationCase(
                case_id="calibration-incomplete-report", query="Report below structural completeness threshold.",
                quality_rubric=ResearchQualityRubric(
                    min_distinct_sources=1, min_grounded_citations=1, min_report_sections=2,
                    min_report_characters=120,
                ),
            ),
            reference_sources=[_ONE],
            expected=ReferenceQualityExpectation(
                metric_statuses={"source_coverage": "passed", "citation_integrity": "passed", "report_completeness": "failed"},
                distinct_source_count=1, citation_marker_count=1, report_section_count=1,
                rationale="The report maintains URL provenance but has only one short section, so completeness must fail.",
            ),
        ),
    ],
)
