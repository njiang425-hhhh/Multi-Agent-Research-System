"""Standalone, bounded document analysis service for the Evidence Layer."""

import asyncio
from hashlib import sha256
import time
from typing import Optional, Sequence

from src.evidence.config import AnalyzerConfig
from src.evidence.drafts import DocumentAnalysisDraft, EvidenceDraft
from src.evidence.models import AnalysisResult, DocumentAnalysis, Evidence
from src.evidence.protocols import AnalyzerModel, DocumentAnalysisRequest
from src.state import Document


class ResultAnalyzer:
    """Analyze existing documents without controlling search or orchestration."""

    def __init__(
        self,
        model: AnalyzerModel,
        config: Optional[AnalyzerConfig] = None,
    ) -> None:
        self.model = model
        self.config = config or AnalyzerConfig()

    async def analyze(
        self,
        *,
        topic: str,
        documents: Sequence[Document],
        objectives: Sequence[str] = (),
    ) -> AnalysisResult:
        """Produce document analyses and grounded evidence for existing sources."""
        normalized_topic = topic.strip()
        if not normalized_topic:
            raise ValueError("topic must not be empty")

        selected_documents = list(documents[: self.config.max_documents])
        analyses: list[DocumentAnalysis] = []
        evidence_records: list[Evidence] = []
        errors: list[str] = []
        had_incomplete_work = len(documents) > len(selected_documents)
        if had_incomplete_work:
            errors.append(
                f"Document limit reached: analyzed {len(selected_documents)} of {len(documents)} documents"
            )

        deadline = time.monotonic() + self.config.total_timeout_seconds
        timed_out = False

        for document in selected_documents:
            problem = self._document_problem(document)
            if problem is not None:
                analyses.append(self._skipped_analysis(document, problem))
                errors.append(f"{document.document_id or '<missing-document-id>'}: {problem}")
                had_incomplete_work = True
                continue

            source_text, text_source = self._select_source_text(document)
            if source_text is None or text_source is None:
                message = "Document has no usable content or snippet"
                analyses.append(self._skipped_analysis(document, message))
                errors.append(f"{document.document_id}: {message}")
                had_incomplete_work = True
                continue

            if time.monotonic() >= deadline:
                errors.append("Result analyzer timeout before all documents could be analyzed")
                had_incomplete_work = True
                timed_out = True
                break

            request = DocumentAnalysisRequest(
                topic=normalized_topic,
                objectives=list(objectives),
                document_id=document.document_id,
                source_url=document.uri,
                title=document.title,
                source_text=source_text,
                text_source=text_source,
                credibility=document.credibility,
            )

            try:
                draft = await self._invoke_with_retries(request, deadline)
            except asyncio.TimeoutError:
                errors.append("Result analyzer timeout before all documents could be analyzed")
                had_incomplete_work = True
                timed_out = True
                break
            except Exception as exc:
                message = f"Document analysis failed after retries: {exc}"
                analyses.append(
                    DocumentAnalysis(
                        document_id=document.document_id,
                        relevance_score=0.0,
                        analyzed_text_source=text_source,
                        status="failed",
                        error=message,
                    )
                )
                errors.append(f"{document.document_id}: {message}")
                had_incomplete_work = True
                continue

            analysis, document_evidence, document_errors = self._build_document_output(
                document=document,
                draft=draft,
                source_text=source_text,
                text_source=text_source,
            )
            analyses.append(analysis)
            evidence_records.extend(document_evidence)
            errors.extend(document_errors)
            if analysis.status != "analyzed":
                had_incomplete_work = True

        if timed_out:
            errors.append("Result analyzer stopped after reaching its total timeout")

        completed = not had_incomplete_work
        partial = had_incomplete_work and self.config.allow_partial_results
        return AnalysisResult(
            documents=selected_documents,
            analyses=analyses,
            evidence=evidence_records,
            errors=errors,
            completed=completed,
            partial=partial,
        )

    async def _invoke_with_retries(
        self,
        request: DocumentAnalysisRequest,
        deadline: float,
    ) -> DocumentAnalysisDraft:
        """Invoke only the injected model within the analyzer-owned deadline."""
        last_error: Optional[Exception] = None
        for _ in range(self.config.retry_times + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError
            try:
                draft = await asyncio.wait_for(
                    self.model.analyze_document(request),
                    timeout=remaining,
                )
                if not isinstance(draft, DocumentAnalysisDraft):
                    raise TypeError("AnalyzerModel must return DocumentAnalysisDraft")
                return draft
            except asyncio.TimeoutError:
                raise
            except Exception as exc:
                last_error = exc

        if last_error is not None:
            raise last_error
        raise RuntimeError("AnalyzerModel failed without an exception")

    def _build_document_output(
        self,
        *,
        document: Document,
        draft: DocumentAnalysisDraft,
        source_text: str,
        text_source: str,
    ) -> tuple[DocumentAnalysis, list[Evidence], list[str]]:
        evidence_records: list[Evidence] = []
        evidence_ids: list[str] = []
        errors: list[str] = []
        seen_evidence_ids: set[str] = set()

        for item in draft.evidence:
            span = self._resolve_quote_span(source_text, item)
            if span is None:
                errors.append(
                    f"{document.document_id}: evidence quote could not be uniquely validated"
                )
                continue

            source_start, source_end = span
            evidence_id = self._evidence_id(
                document_id=document.document_id,
                source_start=source_start,
                source_end=source_end,
                draft=item,
            )
            if evidence_id in seen_evidence_ids:
                continue
            seen_evidence_ids.add(evidence_id)

            evidence_records.append(
                Evidence(
                    evidence_id=evidence_id,
                    document_id=document.document_id,
                    source_url=document.uri,
                    claim=item.claim,
                    source_quote=item.source_quote,
                    source_start=source_start,
                    source_end=source_end,
                    text_source=text_source,
                    relation=item.relation,
                    strength=item.strength,
                    status="grounded" if text_source == "content" else "partial",
                )
            )
            evidence_ids.append(evidence_id)

        analysis_is_partial = text_source == "snippet" or bool(errors)
        analysis = DocumentAnalysis(
            document_id=document.document_id,
            relevance_score=draft.relevance_score,
            credibility_score=self._credibility_score(document),
            key_points=draft.key_points,
            evidence_ids=evidence_ids,
            analyzed_text_source=text_source,
            status="partial" if analysis_is_partial else "analyzed",
            error="; ".join(errors) if errors else None,
        )
        return analysis, evidence_records, errors

    def _select_source_text(self, document: Document) -> tuple[Optional[str], Optional[str]]:
        if document.content and document.content.strip():
            return document.content[: self.config.max_chars_per_document], "content"
        if document.snippet and document.snippet.strip():
            return document.snippet[: self.config.max_chars_per_document], "snippet"
        return None, None

    @staticmethod
    def _document_problem(document: Document) -> Optional[str]:
        if not document.document_id:
            return "Document is missing document_id"
        if document.status == "invalid_source":
            return "Document has invalid_source status"
        if not document.uri:
            return "Document is missing uri"
        return None

    @staticmethod
    def _skipped_analysis(document: Document, message: str) -> DocumentAnalysis:
        return DocumentAnalysis(
            document_id=document.document_id or "<missing-document-id>",
            relevance_score=0.0,
            analyzed_text_source="none",
            status="skipped",
            error=message,
        )

    @staticmethod
    def _resolve_quote_span(source_text: str, draft: EvidenceDraft) -> Optional[tuple[int, int]]:
        """Compute the sole persisted span from one exact source-text match.

        Draft offsets are untrusted model metadata and never participate in
        grounding. A quote must occur exactly once in the original text.
        """
        first_index = source_text.find(draft.source_quote)
        if first_index < 0:
            return None
        if source_text.find(draft.source_quote, first_index + 1) >= 0:
            return None
        return first_index, first_index + len(draft.source_quote)

    @staticmethod
    def _evidence_id(
        *,
        document_id: str,
        source_start: int,
        source_end: int,
        draft: EvidenceDraft,
    ) -> str:
        identity = "\x1f".join(
            [
                document_id,
                str(source_start),
                str(source_end),
                draft.source_quote,
                draft.claim,
                draft.relation,
            ]
        )
        return f"evidence:{sha256(identity.encode('utf-8')).hexdigest()[:24]}"

    @staticmethod
    def _credibility_score(document: Document) -> Optional[float]:
        if not isinstance(document.credibility, dict):
            return None
        value = document.credibility.get("score")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if not 0.0 <= float(value) <= 100.0:
            return None
        return float(value)
