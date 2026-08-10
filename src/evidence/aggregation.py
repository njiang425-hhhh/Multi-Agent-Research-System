"""Deterministic baseline aggregation from grounded evidence to existing Findings."""

from collections import defaultdict
from hashlib import sha256
import re
from typing import Dict, Sequence

from src.evidence.finding_models import FindingAggregationResult
from src.evidence.models import DocumentAnalysis, Evidence
from src.state import Document, Finding


class RuleBasedFindingAggregator:
    """Aggregate evidence by normalized claim without any model invocation."""

    def aggregate(
        self,
        *,
        documents: Sequence[Document],
        analyses: Sequence[DocumentAnalysis],
        evidence: Sequence[Evidence],
    ) -> FindingAggregationResult:
        """Return deterministic Findings based only on eligible Evidence records."""
        document_by_id = {
            document.document_id: document
            for document in sorted(documents, key=lambda item: item.document_id)
            if document.document_id
        }
        analysis_by_document_id = {
            analysis.document_id: analysis
            for analysis in sorted(analyses, key=lambda item: item.document_id)
            if analysis.document_id in document_by_id
        }

        eligible_evidence: list[Evidence] = []
        errors: list[str] = []
        for item in sorted(evidence, key=lambda value: value.evidence_id):
            if item.status == "invalid":
                errors.append(f"{item.evidence_id}: invalid Evidence was excluded")
                continue
            if item.document_id not in document_by_id:
                errors.append(f"{item.evidence_id}: Evidence references an unknown document")
                continue
            if not self._normalize_claim(item.claim):
                errors.append(f"{item.evidence_id}: Evidence claim is empty after normalization")
                continue
            eligible_evidence.append(item)

        grouped_evidence: Dict[str, list[Evidence]] = defaultdict(list)
        for item in eligible_evidence:
            grouped_evidence[self._normalize_claim(item.claim)].append(item)

        findings: list[Finding] = []
        for normalized_claim in sorted(grouped_evidence):
            group = sorted(grouped_evidence[normalized_claim], key=lambda item: item.evidence_id)
            support_ids = [item.evidence_id for item in group if item.relation == "supports"]
            contradiction_ids = [
                item.evidence_id for item in group if item.relation == "contradicts"
            ]
            status = self._finding_status(support_ids, contradiction_ids)
            findings.append(
                Finding(
                    finding_id=self._finding_id(normalized_claim),
                    statement=self._canonical_statement(group),
                    evidence_refs=support_ids,
                    contradictory_evidence_refs=contradiction_ids,
                    confidence=self._confidence(
                        group,
                        document_by_id=document_by_id,
                        analysis_by_document_id=analysis_by_document_id,
                    ),
                    reasoning_summary=self._reasoning_summary(
                        support_ids=support_ids,
                        contradiction_ids=contradiction_ids,
                    ),
                    status=status,
                )
            )

        is_partial = bool(errors)
        return FindingAggregationResult(
            available_evidence_ids=[item.evidence_id for item in eligible_evidence],
            findings=findings,
            errors=errors,
            completed=not is_partial,
            partial=is_partial,
        )

    @staticmethod
    def _normalize_claim(claim: str) -> str:
        """Normalize claim identity while retaining enough text for deterministic grouping."""
        collapsed = " ".join(claim.casefold().split())
        return re.sub(r"[^\w\s]", "", collapsed).strip()

    @staticmethod
    def _canonical_statement(group: Sequence[Evidence]) -> str:
        """Use the lowest Evidence ID as a stable display representative."""
        return " ".join(group[0].claim.split())

    @staticmethod
    def _finding_id(normalized_claim: str) -> str:
        digest = sha256(normalized_claim.encode("utf-8")).hexdigest()[:24]
        return f"finding:{digest}"

    @staticmethod
    def _finding_status(support_ids: Sequence[str], contradiction_ids: Sequence[str]) -> str:
        if support_ids and contradiction_ids:
            return "contested"
        if support_ids:
            return "supported"
        return "insufficient"

    @staticmethod
    def _reasoning_summary(
        *,
        support_ids: Sequence[str],
        contradiction_ids: Sequence[str],
    ) -> str:
        if support_ids and contradiction_ids:
            return (
                f"{len(support_ids)} supporting and {len(contradiction_ids)} contradictory "
                "Evidence records were grouped by claim."
            )
        if support_ids:
            return f"{len(support_ids)} supporting Evidence records were grouped by claim."
        if contradiction_ids:
            return f"Only {len(contradiction_ids)} contradictory Evidence records were available."
        return "Only contextual Evidence records were available."

    def _confidence(
        self,
        evidence: Sequence[Evidence],
        *,
        document_by_id: Dict[str, Document],
        analysis_by_document_id: Dict[str, DocumentAnalysis],
    ) -> float:
        support_weights: list[tuple[str, float]] = []
        contradiction_weights: list[float] = []

        for item in evidence:
            weight = self._evidence_weight(
                item,
                document=document_by_id[item.document_id],
                analysis=analysis_by_document_id.get(item.document_id),
            )
            if item.relation == "supports":
                support_weights.append((item.document_id, weight))
            elif item.relation == "contradicts":
                contradiction_weights.append(weight)

        support_total = sum(weight for _, weight in support_weights)
        contradiction_total = sum(contradiction_weights)
        if support_total <= 0:
            return 0.0

        reliability = support_total / len(support_weights)
        independent_documents = len({document_id for document_id, _ in support_weights})
        corroboration = min(1.0, independent_documents / 3.0)
        conflict_factor = support_total / (support_total + contradiction_total)
        confidence = reliability * (0.5 + 0.5 * corroboration) * conflict_factor
        return round(max(0.0, min(1.0, confidence)), 6)

    @staticmethod
    def _evidence_weight(
        evidence: Evidence,
        *,
        document: Document,
        analysis: DocumentAnalysis | None,
    ) -> float:
        relevance = analysis.relevance_score if analysis is not None else 0.5
        credibility = RuleBasedFindingAggregator._credibility_score(document, analysis)
        strength = evidence.strength if evidence.strength is not None else 0.5
        status_weight = 1.0 if evidence.status == "grounded" else 0.6
        return (0.45 * credibility) + (0.30 * relevance) + (0.15 * strength) + (0.10 * status_weight)

    @staticmethod
    def _credibility_score(document: Document, analysis: DocumentAnalysis | None) -> float:
        if analysis is not None and analysis.credibility_score is not None:
            return analysis.credibility_score / 100.0
        if isinstance(document.credibility, dict):
            value = document.credibility.get("score")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if 0.0 <= float(value) <= 100.0:
                    return float(value) / 100.0
        return 0.5
