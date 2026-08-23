"""Deterministic versioning and fingerprints for offline evaluation inputs."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Mapping, Sequence

from src.evaluation.contracts import EvaluationCase, EvaluationDataset, EvaluationSnapshot


EVALUATION_SNAPSHOT_VERSION = "p5.1.v1"
EVALUATION_METRIC_NAMES = (
    "run_completion",
    "trace_completeness",
    "usage_integrity",
    "research_coverage",
    "report_structure",
    "source_coverage",
    "grounded_citation",
    "report_completeness",
    "failure_signals",
)
_SENSITIVE_CONFIGURATION_KEYS = frozenset(
    {"api_key", "authorization", "cookie", "headers", "password", "secret", "token"}
)


def _sanitize_configuration(value: Any, *, key: str = "") -> Any:
    if key.lower() in _SENSITIVE_CONFIGURATION_KEYS:
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {
            str(item_key): _sanitize_configuration(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_configuration(item) for item in value]
    return value


def _fingerprint_payload(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def evaluation_dataset_content_fingerprint(dataset: EvaluationDataset) -> str:
    """Fingerprint actual ordered case content, not only its declared version."""

    return _fingerprint_payload(
        {
            "dataset_id": dataset.dataset_id,
            "dataset_version": dataset.version,
            "cases": [case.model_dump(mode="json") for case in dataset.cases],
        }
    )


def _resolve_dataset_identity(
    *,
    dataset: EvaluationDataset | None,
    case: EvaluationCase | None,
    dataset_id: str | None,
    dataset_version: str | None,
) -> tuple[str | None, str | None, str | None]:
    if dataset is not None:
        if dataset_id is not None and dataset_id != dataset.dataset_id:
            raise ValueError("dataset_id does not match the supplied EvaluationDataset")
        if dataset_version is not None and dataset_version != dataset.version:
            raise ValueError("dataset_version does not match the supplied EvaluationDataset")
        if case is not None and case not in dataset.cases:
            raise ValueError("evaluation case is not present in the supplied EvaluationDataset")
        return dataset.dataset_id, dataset.version, evaluation_dataset_content_fingerprint(dataset)

    # A direct single-case evaluation has no complete dataset object. Its
    # snapshot still fingerprints the actual case content it was given, while
    # a suite uses the stronger full-dataset fingerprint above.
    if case is not None:
        return (
            dataset_id,
            dataset_version,
            _fingerprint_payload(
                {
                    "dataset_id": dataset_id,
                    "dataset_version": dataset_version,
                    "cases": [case.model_dump(mode="json")],
                }
            ),
        )
    return dataset_id, dataset_version, None


def build_evaluation_snapshot(
    *,
    evaluator_version: str,
    expected_completed_nodes: Sequence[str],
    dataset_id: str | None = None,
    dataset_version: str | None = None,
    dataset: EvaluationDataset | None = None,
    case: EvaluationCase | None = None,
    metric_names: Sequence[str] | None = None,
    configuration: Mapping[str, Any] | None = None,
) -> EvaluationSnapshot:
    """Create a stable snapshot without reading or modifying production State."""

    resolved_dataset_id, resolved_dataset_version, dataset_content_fingerprint = _resolve_dataset_identity(
        dataset=dataset,
        case=case,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
    )

    payload = {
        "snapshot_version": EVALUATION_SNAPSHOT_VERSION,
        "evaluator_version": evaluator_version,
        "dataset_id": resolved_dataset_id,
        "dataset_version": resolved_dataset_version,
        "dataset_content_fingerprint": dataset_content_fingerprint,
        "expected_completed_nodes": list(expected_completed_nodes),
        "metric_names": list(metric_names or EVALUATION_METRIC_NAMES),
        "configuration": _sanitize_configuration(dict(configuration or {})),
    }
    return EvaluationSnapshot(**payload, fingerprint=_fingerprint_payload(payload))


def validate_evaluation_snapshot(
    snapshot: EvaluationSnapshot,
    *,
    evaluator_version: str,
    expected_completed_nodes: Sequence[str],
    dataset_id: str | None = None,
    dataset_version: str | None = None,
    dataset: EvaluationDataset | None = None,
    case: EvaluationCase | None = None,
    metric_names: Sequence[str] | None = None,
    configuration: Mapping[str, Any] | None = None,
) -> EvaluationSnapshot:
    """Reject snapshots not bound to this evaluator, dataset, case and config."""

    expected = build_evaluation_snapshot(
        evaluator_version=evaluator_version,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        dataset=dataset,
        case=case,
        expected_completed_nodes=expected_completed_nodes,
        metric_names=metric_names,
        configuration=configuration,
    )
    if snapshot.model_dump(mode="json") != expected.model_dump(mode="json"):
        raise ValueError("evaluation snapshot does not match evaluator, dataset/case, or configuration")
    return snapshot
