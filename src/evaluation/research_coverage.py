"""Deterministic fake-only research coverage evaluation for P10.1."""

from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from src.evaluation.contracts import EvaluationMetric, EvaluationSnapshot
from src.evaluation.snapshot import build_evaluation_snapshot
from src.state import Document, ResearchPlan, SearchResult


RESEARCH_COVERAGE_EVALUATOR_VERSION = "p10.research_coverage.v1"
RESEARCH_COVERAGE_METRICS = (
    "planned_query_facet_coverage",
    "executed_query_facet_coverage",
    "result_facet_coverage",
    "extracted_facet_coverage",
    "source_domain_balance",
)

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


class ResearchCoverageCase(BaseModel):
    """A fixed fake case with lexical facet expectations."""

    case_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    required_facets: list[str] = Field(min_length=1)
    min_domains_per_facet: int = Field(default=1, ge=1)


class ResearchCoverageDataset(BaseModel):
    dataset_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    cases: list[ResearchCoverageCase] = Field(min_length=1)


class ResearchCoverageObservation(BaseModel):
    """Read-only observed plan/search outputs for one fake-only case."""

    plan: ResearchPlan
    executed_queries: list[str] = Field(default_factory=list)
    search_results: list[SearchResult] = Field(default_factory=list)
    documents: list[Document] = Field(default_factory=list)


class ResearchCoverageCaseResult(BaseModel):
    case_id: str
    metrics: list[EvaluationMetric]


class ResearchCoverageSummary(BaseModel):
    total_cases: int = 0
    fully_passing_cases: int = 0
    planned_query_facet_coverage_mean: float = 0.0
    executed_query_facet_coverage_mean: float = 0.0
    result_facet_coverage_mean: float = 0.0
    extracted_facet_coverage_mean: float = 0.0
    source_domain_balance_mean: float = 0.0
    result_or_extracted_failure_case_ids: list[str] = Field(default_factory=list)
    p10_2_candidate: bool = False


class ResearchCoverageResult(BaseModel):
    evaluator_version: str = RESEARCH_COVERAGE_EVALUATOR_VERSION
    evaluation_snapshot: EvaluationSnapshot
    dataset_id: str
    dataset_version: str
    dataset_content_fingerprint: str
    fake_payload_fingerprint: str
    results: list[ResearchCoverageCaseResult]
    summary: ResearchCoverageSummary
    configuration: dict[str, Any] = Field(default_factory=dict)


def research_coverage_content_fingerprint(value: Any) -> str:
    """Fingerprint deterministic dataset or fake payload content."""

    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(text or "")}


def _facet_hits(values: Sequence[str], facets: Sequence[str]) -> tuple[float, list[str], list[str]]:
    tokens = _tokens(" ".join(str(value or "") for value in values))
    matched = [facet for facet in facets if facet.lower() in tokens]
    missing = [facet for facet in facets if facet.lower() not in tokens]
    return len(matched) / len(facets), matched, missing


def _result_text(result: SearchResult) -> str:
    return " ".join(
        [
            result.query,
            result.title,
            result.snippet,
            result.url,
            result.content or "",
        ]
    )


def _document_text(document: Document) -> str:
    return " ".join(
        [
            document.metadata.get("search_query", "") if isinstance(document.metadata, dict) else "",
            document.title,
            document.snippet,
            document.uri,
            document.content or "",
        ]
    )


def _host(url: str) -> str:
    try:
        return urlsplit(url).netloc.lower()
    except ValueError:
        return ""


def _facet_domains(
    *,
    facets: Sequence[str],
    search_results: Sequence[SearchResult],
    documents: Sequence[Document],
) -> dict[str, list[str]]:
    domains: dict[str, set[str]] = {facet: set() for facet in facets}
    for result in search_results:
        tokens = _tokens(_result_text(result))
        host = _host(result.url)
        if not host:
            continue
        for facet in facets:
            if facet.lower() in tokens:
                domains[facet].add(host)
    for document in documents:
        tokens = _tokens(_document_text(document))
        host = _host(document.uri)
        if not host:
            continue
        for facet in facets:
            if facet.lower() in tokens:
                domains[facet].add(host)
    return {facet: sorted(values) for facet, values in domains.items()}


def _facet_presence(
    *,
    facets: Sequence[str],
    search_results: Sequence[SearchResult],
    documents: Sequence[Document],
    require_content: bool,
) -> tuple[float, list[str], list[str]]:
    hits: list[str] = []
    for facet in facets:
        needle = facet.lower()
        found = any(
            needle in _tokens(_result_text(result))
            and (not require_content or bool(result.content and result.content.strip()))
            for result in search_results
        ) or any(
            needle in _tokens(_document_text(document))
            and (not require_content or bool(document.content and document.content.strip()))
            for document in documents
        )
        if found:
            hits.append(facet)
    missing = [facet for facet in facets if facet not in hits]
    return len(hits) / len(facets), hits, missing


def _metric(name: str, *, value: Any, passed: bool, **metadata: Any) -> EvaluationMetric:
    return EvaluationMetric(
        name=name,
        status="passed" if passed else "failed",
        value=value,
        metadata=metadata,
    )


def evaluate_research_coverage(
    observations: Mapping[str, ResearchCoverageObservation],
    *,
    dataset: ResearchCoverageDataset,
    fake_payloads: Mapping[str, Any],
    configuration: Mapping[str, Any] | None = None,
) -> ResearchCoverageResult:
    """Evaluate existing fake-only plan/search outputs without mutation."""

    dataset_fingerprint = research_coverage_content_fingerprint(dataset)
    fake_payload_fingerprint = research_coverage_content_fingerprint(fake_payloads)
    resolved_configuration = {
        **dict(configuration or {}),
        "dataset_content_fingerprint": dataset_fingerprint,
        "fake_payload_fingerprint": fake_payload_fingerprint,
    }
    snapshot = build_evaluation_snapshot(
        evaluator_version=RESEARCH_COVERAGE_EVALUATOR_VERSION,
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.version,
        expected_completed_nodes=("plan", "search"),
        metric_names=RESEARCH_COVERAGE_METRICS,
        configuration=resolved_configuration,
    )

    results: list[ResearchCoverageCaseResult] = []
    planned_scores: list[float] = []
    executed_scores: list[float] = []
    result_scores: list[float] = []
    extracted_scores: list[float] = []
    domain_scores: list[float] = []
    fully_passing = 0
    result_or_extracted_failures: list[str] = []

    for case in dataset.cases:
        observation = observations.get(case.case_id)
        if observation is None:
            metrics = [
                EvaluationMetric(name=name, status="unavailable", reason="observation is absent")
                for name in RESEARCH_COVERAGE_METRICS
            ]
            results.append(ResearchCoverageCaseResult(case_id=case.case_id, metrics=metrics))
            result_or_extracted_failures.append(case.case_id)
            continue

        query_values = [
            " ".join([item.query, item.purpose])
            for item in observation.plan.search_queries
        ]
        planned_score, planned_hits, planned_missing = _facet_hits(
            query_values, case.required_facets
        )
        executed_score, executed_hits, executed_missing = _facet_hits(
            observation.executed_queries, case.required_facets
        )
        result_score, result_hits, result_missing = _facet_presence(
            facets=case.required_facets,
            search_results=observation.search_results,
            documents=observation.documents,
            require_content=False,
        )
        extracted_score, extracted_hits, extracted_missing = _facet_presence(
            facets=case.required_facets,
            search_results=observation.search_results,
            documents=observation.documents,
            require_content=True,
        )
        domains_by_facet = _facet_domains(
            facets=case.required_facets,
            search_results=observation.search_results,
            documents=observation.documents,
        )
        balanced_facets = [
            facet
            for facet, domains in domains_by_facet.items()
            if len(domains) >= case.min_domains_per_facet
        ]
        domain_score = len(balanced_facets) / len(case.required_facets)
        domain_missing = [
            facet for facet in case.required_facets if facet not in balanced_facets
        ]

        metrics = [
            _metric(
                "planned_query_facet_coverage",
                value=planned_score,
                passed=planned_score == 1.0,
                matched_facets=planned_hits,
                missing_facets=planned_missing,
                expected_facets=case.required_facets,
            ),
            _metric(
                "executed_query_facet_coverage",
                value=executed_score,
                passed=executed_score == 1.0,
                matched_facets=executed_hits,
                missing_facets=executed_missing,
                expected_facets=case.required_facets,
                executed_queries=list(observation.executed_queries),
            ),
            _metric(
                "result_facet_coverage",
                value=result_score,
                passed=result_score == 1.0,
                matched_facets=result_hits,
                missing_facets=result_missing,
                expected_facets=case.required_facets,
            ),
            _metric(
                "extracted_facet_coverage",
                value=extracted_score,
                passed=extracted_score == 1.0,
                matched_facets=extracted_hits,
                missing_facets=extracted_missing,
                expected_facets=case.required_facets,
            ),
            _metric(
                "source_domain_balance",
                value=domain_score,
                passed=domain_score == 1.0,
                matched_facets=balanced_facets,
                missing_facets=domain_missing,
                domains_by_facet=domains_by_facet,
                min_domains_per_facet=case.min_domains_per_facet,
            ),
        ]

        planned_scores.append(planned_score)
        executed_scores.append(executed_score)
        result_scores.append(result_score)
        extracted_scores.append(extracted_score)
        domain_scores.append(domain_score)
        fully_passing += int(all(metric.status == "passed" for metric in metrics))
        if result_score < 1.0 or extracted_score < 1.0:
            result_or_extracted_failures.append(case.case_id)
        results.append(ResearchCoverageCaseResult(case_id=case.case_id, metrics=metrics))

    scored_cases = len(planned_scores)
    return ResearchCoverageResult(
        evaluation_snapshot=snapshot,
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.version,
        dataset_content_fingerprint=dataset_fingerprint,
        fake_payload_fingerprint=fake_payload_fingerprint,
        results=results,
        summary=ResearchCoverageSummary(
            total_cases=len(dataset.cases),
            fully_passing_cases=fully_passing,
            planned_query_facet_coverage_mean=(
                sum(planned_scores) / scored_cases if scored_cases else 0.0
            ),
            executed_query_facet_coverage_mean=(
                sum(executed_scores) / scored_cases if scored_cases else 0.0
            ),
            result_facet_coverage_mean=sum(result_scores) / scored_cases
            if scored_cases
            else 0.0,
            extracted_facet_coverage_mean=sum(extracted_scores) / scored_cases
            if scored_cases
            else 0.0,
            source_domain_balance_mean=sum(domain_scores) / scored_cases
            if scored_cases
            else 0.0,
            result_or_extracted_failure_case_ids=result_or_extracted_failures,
            p10_2_candidate=bool(result_or_extracted_failures),
        ),
        configuration=resolved_configuration,
    )
