"""Fake-only tests for P6 reference-backed rubric calibration."""

import asyncio

from src.evaluation.calibration import calibration_content_fingerprint, run_rubric_calibration
from src.evaluation.calibration_dataset import REFERENCE_CALIBRATION_DATASET


def _state(calibration_id: str):
    case = next(item for item in REFERENCE_CALIBRATION_DATASET.cases if item.calibration_id == calibration_id)
    source_urls = [item.source_url for item in case.reference_sources]
    document_urls = source_urls if calibration_id == "grounded-complete" else source_urls[:1]
    report_urls = list(document_urls)
    if calibration_id == "ungrounded-report-citation":
        report_urls.append("https://reference.example.org/unsupported")
    sections = [{"title": "Summary", "sources": report_urls[:1]}]
    if calibration_id == "grounded-complete":
        sections.append({"title": "Details", "sources": report_urls[1:]})
    for index, section in enumerate(sections, 1):
        section["content"] = f"Reference-backed content [{index}]."
    report_text = "# Calibration report\n\n" + "\n\n".join(
        f"## {section['title']}\n\n{section['content']} " + ("Reference-backed content. " * 8)
        for section in sections
    )
    return {
        "run_id": f"calibration-{calibration_id}",
        "research_topic": case.case.query,
        "status": "completed",
        "current_stage": "complete",
        "documents": [{"document_id": f"doc-{index}", "uri": url} for index, url in enumerate(document_urls)],
        "evidence": [
            {"evidence_id": f"evidence-{index}", "document_id": f"doc-{index}", "source_url": url,
             "source_quote": "Reference quote", "status": "grounded"}
            for index, url in enumerate(document_urls)
        ],
        "findings": [{"finding_id": "finding-1"}],
        "evidence_diagnostics": {"status": "completed"},
        "report_sections": sections,
        "report": {"citations": report_urls, "sections": sections},
        "final_report": report_text,
        "usage": {"llm_calls": 1, "tool_calls": 1, "input_tokens": 1, "output_tokens": 1, "total_tokens": 2, "latency_seconds": 1.0},
        "agent_trace": [],
    }


def test_reference_calibration_aligns_expected_provenance_and_structure_signals_read_only() -> None:
    states = {item.calibration_id: _state(item.calibration_id) for item in REFERENCE_CALIBRATION_DATASET.cases}
    before = {key: repr(value) for key, value in states.items()}

    async def runner(case):
        return states[case.calibration_id]

    result = asyncio.run(run_rubric_calibration(runner, dataset=REFERENCE_CALIBRATION_DATASET, configuration={"execution": "fake"}))

    assert result.summary.aligned_cases == 4
    assert result.summary.misaligned_cases == 0
    assert result.summary.signal_count == 24
    assert result.configuration["calibration_content_fingerprint"] == result.calibration_content_fingerprint
    assert all(item.alignment_status == "passed" for item in result.cases)
    assert {key: repr(value) for key, value in states.items()} == before


def test_calibration_fingerprint_changes_when_reference_rationale_changes() -> None:
    original = calibration_content_fingerprint(REFERENCE_CALIBRATION_DATASET)
    changed_source = REFERENCE_CALIBRATION_DATASET.cases[0].reference_sources[0].model_copy(
        update={"rationale": "Changed reference rationale."}
    )
    changed_case = REFERENCE_CALIBRATION_DATASET.cases[0].model_copy(update={"reference_sources": [changed_source, *REFERENCE_CALIBRATION_DATASET.cases[0].reference_sources[1:]]})
    changed = REFERENCE_CALIBRATION_DATASET.model_copy(update={"cases": [changed_case, *REFERENCE_CALIBRATION_DATASET.cases[1:]]})

    assert calibration_content_fingerprint(changed) != original
