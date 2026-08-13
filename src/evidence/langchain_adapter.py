"""LangChain adapter for production document-analysis structured output.

The adapter deliberately owns only one bounded model invocation.  Document
selection, timeouts, retries, and partial-result policy remain responsibilities
of :class:`src.evidence.service.ResultAnalyzer`.
"""

from collections.abc import Mapping
import time
from typing import Any

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field

from src.evidence.drafts import DocumentAnalysisDraft
from src.evidence.protocols import DocumentAnalysisRequest
from src.llm_tracker import estimate_tokens


ANALYZER_SYSTEM_PROMPT = """You analyze one supplied research source.

Use only the supplied source text. Do not search the web, call tools, retrieve
other sources, or infer facts that are not present in the source text.

Return the requested structured analysis. Exact quote is required for every
evidence item: source_quote must be copied verbatim, character-for-character,
from SOURCE_TEXT. Never paraphrase, rewrite, supplement, or normalize a quote.
Prefer a quote that occurs only once in SOURCE_TEXT. If no verbatim source_quote
can be found, omit that evidence item; never alter source_quote to satisfy a
format requirement.

Offsets are optional. Fill source_start and source_end only when you are
completely certain of both values. They use Python slicing semantics: zero-based,
source_start is inclusive, source_end is end-exclusive, and the mandatory check
is SOURCE_TEXT[source_start:source_end] == source_quote. If you are uncertain
about either offset, set source_start=null and source_end=null. Never guess offsets."""


class EvidenceAnalyzerCallRecord(BaseModel):
    """One serializable Evidence Analyzer invocation compatible with legacy tracking."""

    agent: str = "EvidenceAnalyzer"
    operation: str = "analyze_document"
    model: str = ""
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    duration: float = Field(default=0.0, ge=0.0)
    success: bool
    error: str | None = None
    token_source: str = "unavailable"


class LangChainAnalyzerModel:
    """Adapt a LangChain chat model to the Evidence Layer ``AnalyzerModel`` protocol."""

    def __init__(self, model: BaseChatModel, *, model_name: str | None = None) -> None:
        # ChatOpenAI defaults to ``json_schema``.  Use the OpenAI-compatible
        # tool-calling contract explicitly so providers that support function
        # calling but not response_format=json_schema remain usable.
        self._structured_model = model.with_structured_output(
            DocumentAnalysisDraft,
            method="function_calling",
            include_raw=True,
        )
        self._model_name = model_name or str(
            getattr(model, "model_name", getattr(model, "model", ""))
        )
        self._call_records: list[EvidenceAnalyzerCallRecord] = []

    @property
    def call_records(self) -> list[EvidenceAnalyzerCallRecord]:
        """Return a copy of every actual structured-model invocation record."""
        return list(self._call_records)

    async def analyze_document(
        self,
        request: DocumentAnalysisRequest,
    ) -> DocumentAnalysisDraft:
        """Invoke one structured, source-grounded document analysis.

        Provider, transport, and structured parsing errors intentionally pass
        through unchanged so ``ResultAnalyzer`` can apply its configured retry
        and partial-result behavior.
        """
        messages = self._messages(request)
        start_time = time.monotonic()
        result: Any = None
        try:
            result = await self._structured_model.ainvoke(messages)
            draft = self._to_draft(result)
        except Exception as exc:
            self._record_call(
                messages=messages,
                result=result,
                duration=time.monotonic() - start_time,
                success=False,
                error=str(exc),
            )
            raise

        self._record_call(
            messages=messages,
            result=result,
            duration=time.monotonic() - start_time,
            success=True,
            error=None,
        )
        return draft

    @staticmethod
    def _to_draft(result: Any) -> DocumentAnalysisDraft:
        if isinstance(result, DocumentAnalysisDraft):
            return result
        if isinstance(result, Mapping):
            if "parsing_error" in result and result["parsing_error"] is not None:
                raise result["parsing_error"]
            parsed = result.get("parsed") if "parsed" in result else result
            if parsed is None:
                raise TypeError("Structured analyzer output is missing parsed content")
            if isinstance(parsed, DocumentAnalysisDraft):
                return parsed
            return DocumentAnalysisDraft.model_validate(parsed)
        raise TypeError(
            "Structured analyzer output must be DocumentAnalysisDraft or a mapping"
        )

    def _record_call(
        self,
        *,
        messages: list[dict[str, str]],
        result: Any,
        duration: float,
        success: bool,
        error: str | None,
    ) -> None:
        input_tokens, output_tokens, token_source = self._token_usage(messages, result)
        self._call_records.append(
            EvidenceAnalyzerCallRecord(
                model=self._model_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                duration=round(duration, 2),
                success=success,
                error=error,
                token_source=token_source,
            )
        )

    @staticmethod
    def _token_usage(
        messages: list[dict[str, str]],
        result: Any,
    ) -> tuple[int, int, str]:
        usage = LangChainAnalyzerModel._usage_metadata(result)
        if usage is not None:
            input_tokens = LangChainAnalyzerModel._usage_value(
                usage, "input_tokens", "prompt_tokens"
            )
            output_tokens = LangChainAnalyzerModel._usage_value(
                usage, "output_tokens", "completion_tokens"
            )
            if input_tokens is not None and output_tokens is not None:
                return input_tokens, output_tokens, "provider"

        try:
            input_text = "\n".join(message["content"] for message in messages)
            output_text = LangChainAnalyzerModel._serializable_output(result)
            return estimate_tokens(input_text), estimate_tokens(output_text), "estimated"
        except Exception:
            return 0, 0, "unavailable"

    @staticmethod
    def _usage_metadata(result: Any) -> Mapping[str, Any] | None:
        candidates: list[Any] = [result]
        if isinstance(result, Mapping):
            candidates.extend([result.get("raw"), result.get("parsed")])
        for candidate in candidates:
            if candidate is None:
                continue
            usage = getattr(candidate, "usage_metadata", None)
            if isinstance(usage, Mapping):
                return usage
            response_metadata = getattr(candidate, "response_metadata", None)
            if isinstance(response_metadata, Mapping):
                nested = response_metadata.get("token_usage") or response_metadata.get("usage")
                if isinstance(nested, Mapping):
                    return nested
            if isinstance(candidate, Mapping):
                usage = candidate.get("usage_metadata") or candidate.get("token_usage")
                if isinstance(usage, Mapping):
                    return usage
        return None

    @staticmethod
    def _usage_value(usage: Mapping[str, Any], *names: str) -> int | None:
        for name in names:
            value = usage.get(name)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                return value
        return None

    @staticmethod
    def _serializable_output(result: Any) -> str:
        if isinstance(result, BaseModel):
            return result.model_dump_json()
        if isinstance(result, Mapping):
            parsed = result.get("parsed") if "parsed" in result else result
            if isinstance(parsed, BaseModel):
                return parsed.model_dump_json()
            return str(parsed)
        return str(result)

    @staticmethod
    def _messages(request: DocumentAnalysisRequest) -> list[dict[str, str]]:
        objectives = "\n".join(f"- {objective}" for objective in request.objectives)
        return [
            {"role": "system", "content": ANALYZER_SYSTEM_PROMPT},
            {
                "role": "human",
                "content": (
                    f"Research topic:\n{request.topic}\n\n"
                    f"Research objectives:\n{objectives or '- None provided'}\n\n"
                    f"Document ID: {request.document_id}\n"
                    f"Source URL: {request.source_url}\n"
                    f"Title: {request.title}\n"
                    f"Text source: {request.text_source}\n\n"
                    f"Source text:\n{request.source_text}"
                ),
            },
        ]
