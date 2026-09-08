"""Writer node for the canonical Documents → Findings → Report flow."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re
from typing import Any, Dict, Iterable, List, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from src.agents._llm_support import (
    LLM_OPERATION_TIMEOUT_SECONDS,
    _legacy_attempt_limit_to_retries,
    _llm_failure_patch,
    _llm_patch_totals,
    _usage_from_totals,
)
from src.callbacks import emit_error, emit_writing_complete, emit_writing_section, emit_writing_start
from src.config import config
from src.exceptions import ReportGenerationError
from src.llm.factory import get_llm
from src.llm_execution import execute_llm_operation
from src.prompts import WRITER_SYSTEM_PROMPT, WRITER_USER_TEMPLATE
from src.runtime_lifecycle import completed_lifecycle_patch, failed_lifecycle_patch
from src.state import Document, Finding, Report, ReportSection, ResearchState
from src.state_compat import (
    canonical_documents,
    canonical_findings,
    canonical_iteration,
    canonical_plan as _research_plan,
    canonical_query as _research_query,
    canonical_usage,
)
from src.utils.citations import CitationFormatter
from src.utils.tools import get_research_tools


logger = logging.getLogger(__name__)


def _citeable_documents(documents: List[Document]) -> List[Document]:
    """Keep the canonical order while defensively excluding invalid sources."""

    cited: List[Document] = []
    seen_ids: set[str] = set()
    seen_uris: set[str] = set()
    for document in documents:
        if (
            not document.document_id
            or document.document_id in seen_ids
            or document.uri in seen_uris
            or document.source_type != "web"
            or document.status == "invalid_source"
            or not document.uri.startswith(("http://", "https://"))
        ):
            continue
        seen_ids.add(document.document_id)
        seen_uris.add(document.uri)
        cited.append(document)
    return cited


def _writer_ready_findings(findings: List[Finding], documents: List[Document]) -> List[Finding]:
    """Retain only claims with at least one source in the writer bibliography."""

    document_ids = {document.document_id for document in documents}
    return [
        finding
        for finding in findings
        if finding.statement and any(source_id in document_ids for source_id in finding.source_document_ids)
    ]


def _scope_terms(value: str) -> set[str]:
    """Return deterministic, language-agnostic terms for lightweight matching."""

    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", " ", value.casefold())
    words = set(re.findall(r"[a-z0-9_]{2,}", normalized))
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
    # Bigrams keep Chinese matching useful without introducing a tokenizer dependency.
    words.update(chinese[index : index + 2] for index in range(max(0, len(chinese) - 1)))
    return words


def _scope_score(finding: Finding, section_title: str, section_objective: str) -> int:
    finding_terms = _scope_terms(finding.statement)
    title_terms = _scope_terms(section_title)
    objective_terms = _scope_terms(section_objective)
    # A title match is a stronger signal than a broad research objective.
    return 3 * len(finding_terms & title_terms) + len(finding_terms & objective_terms)


@dataclass(frozen=True, slots=True)
class _SectionScope:
    identifier: str
    index: int
    title: str
    objective: str
    primary_findings: list[Finding]
    supporting_findings: list[Finding] = field(default_factory=list)


@dataclass(slots=True)
class _WritingContext:
    """Transient serial-writer context; it deliberately never enters State."""

    used_finding_ids: list[str] = field(default_factory=list)
    previous_section_summary: str = ""


def _section_scopes(plan, findings: List[Finding]) -> list[_SectionScope]:
    """Assign every finding to exactly one stable, primary section.

    ResearchPlan currently exposes titles and top-level objectives rather than
    per-section objectives.  The objective at the matching outline index is the
    narrowest available signal; the title itself remains the fallback objective.
    """

    outline = list(getattr(plan, "report_outline", ()) or ())
    objectives = list(getattr(plan, "objectives", ()) or ())
    scopes = [
        _SectionScope(
            identifier=f"section:{index}",
            index=index,
            title=title,
            objective=objectives[index - 1] if index <= len(objectives) else title,
            primary_findings=[],
        )
        for index, title in enumerate(outline, 1)
    ]
    if not scopes:
        return scopes

    primary_by_section: dict[str, list[Finding]] = {scope.identifier: [] for scope in scopes}
    for finding in findings:
        # max is stable: ties intentionally retain the earliest outline section.
        selected = max(
            scopes,
            key=lambda scope: (_scope_score(finding, scope.title, scope.objective), -scope.index),
        )
        primary_by_section[selected.identifier].append(finding)

    return [
        _SectionScope(
            identifier=scope.identifier,
            index=scope.index,
            title=scope.title,
            objective=scope.objective,
            primary_findings=primary_by_section[scope.identifier],
        )
        for scope in scopes
    ]


def _documents_for_findings(findings: Iterable[Finding], documents: List[Document]) -> list[Document]:
    """Select scoped source documents while retaining their canonical order."""

    source_ids = {
        source_id
        for finding in findings
        for source_id in finding.source_document_ids
    }
    return [document for document in documents if document.document_id in source_ids]


def _summary_from_section(content: str, limit: int = 420) -> str:
    """Keep only a compact, heading-free carry-forward summary for the next section."""

    plain = re.sub(r"(?m)^\s{0,3}#{1,6}\s+.*$", "", content)
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain[:limit]


def _section_body(content: str) -> str:
    """Remove model-authored structural headings; report structure is code-owned."""

    content = re.sub(r"(?m)^\s{0,3}#{1,6}\s+.*$\n?", "", content)
    content = re.sub(r"(?m)^\s*(?:第\s*\d+\s*章|\d+(?:\.\d+)+\.?)\s+.*$\n?", "", content)
    return content.strip()


def _reserved_heading(title: str) -> bool:
    normalized = re.sub(r"\s+", " ", title).strip().casefold()
    return normalized in {"executive summary", "执行摘要", "research objectives", "研究目标", "references", "参考文献"}


@dataclass(slots=True)
class _WriterSectionBatch:
    sections: list[ReportSection]
    call_details: list[dict[str, Any]]
    execution_context: Any


class ReportWriter:
    """负责撰写研究报告的自主代理。"""

    def __init__(
        self,
        llm: Optional[BaseChatModel] = None,
        citation_formatter: Optional[CitationFormatter] = None,
        citation_style: str = 'apa',
        max_retries: int = 3,
    ):
        self.llm = llm or get_llm(temperature=0.7)
        self.tools = get_research_tools(agent_type="writing")
        self.max_retries = max_retries
        self.citation_style = citation_style
        self.citation_formatter = citation_formatter or CitationFormatter()

    async def write_report(self, state: ResearchState) -> Dict[str, Any]:
        """通过验证和重试撰写最终研究报告。

        返回将由 LangGraph 合并到状态中的报告数据字典。
        """
        logger.info("正在撰写最终报告")

        plan = _research_plan(state)
        documents = _citeable_documents(canonical_documents(state))[:15]
        findings = _writer_ready_findings(canonical_findings(state), documents)
        if not plan or not documents or not findings:
            await emit_error("报告生成所需的数据不足")
            return {"error": "报告生成所需的数据不足", **failed_lifecycle_patch()}

        await emit_writing_start(len(plan.report_outline))

        report_sections: list[ReportSection] = []
        report_call_details: list[dict[str, Any]] = []
        current_context = state.execution_context

        try:
            batch = await self._write_sections(state, documents, findings)
            report_sections = batch.sections
            report_call_details = batch.call_details
            current_context = batch.execution_context

            if not report_sections:
                raise ReportGenerationError("未生成报告章节")

            final_report = self._compile_report(_research_query(state), plan, report_sections, documents)
            high_cred_sources = [
                index + 1
                for index, document in enumerate(documents)
                if (document.credibility or {}).get("level") == "high"
            ]
            if high_cred_sources:
                final_report += f"\n\n---\n\n**注：** 本次研究优先采用了 {len(high_cred_sources)} 个高可信度来源。"
            if len(final_report) < 500:
                raise ReportGenerationError("报告过短，内容不足")
        except Exception as error:
            # Preserve successful earlier section attempts, then append the
            # failing operation's attempts exactly once.
            details = report_call_details + list(getattr(error, "llm_call_details", ()) or ())
            setattr(error, "llm_call_details", details)
            if getattr(error, "context", None) is None and current_context is not None:
                setattr(error, "execution_context", current_context)
            logger.error(f"报告生成失败：{error}")
            await emit_error(f"报告生成失败：{error}")
            return _llm_failure_patch(state, error, f"报告撰写失败：{error}")

        calls, input_tokens, output_tokens = _llm_patch_totals(state, report_call_details)
        logger.info(f"报告生成完成：{len(final_report)} 个字符")
        await emit_writing_complete(len(final_report))
        report = Report(
            title=_research_query(state),
            sections=report_sections,
            content=final_report,
            citations=[document.uri for document in documents],
            status="completed",
        )
        patch: Dict[str, Any] = {
            "report": report,
            **completed_lifecycle_patch(),
            "iteration": canonical_iteration(state) + 1,
            "llm_call_details": state.llm_call_details + report_call_details,
            "usage": _usage_from_totals(
                state,
                llm_calls=canonical_usage(state).llm_calls + calls,
                input_tokens=canonical_usage(state).input_tokens + input_tokens,
                output_tokens=canonical_usage(state).output_tokens + output_tokens,
            ),
        }
        if current_context is not None:
            patch["execution_context"] = current_context
        return patch

    async def _write_sections(
        self,
        state: ResearchState,
        documents: List[Document],
        findings: List[Finding],
    ) -> _WriterSectionBatch:
        return await self._write_sections_serial(state, documents, findings)

    async def _write_sections_serial(
        self,
        state: ResearchState,
        documents: List[Document],
        findings: List[Finding],
    ) -> _WriterSectionBatch:
        report_sections: list[ReportSection] = []
        report_call_details: list[dict[str, Any]] = []
        current_context = state.execution_context
        scopes = [
            scope for scope in _section_scopes(_research_plan(state), findings)
            if not _reserved_heading(scope.title)
        ]
        total_sections = len(scopes)
        writing_context = _WritingContext()

        try:
            for section_idx, scope in enumerate(scopes, 1):
                await emit_writing_section(scope.title, section_idx, total_sections)
                section, section_details, current_context = await self._write_section_result(
                    state,
                    scope,
                    documents,
                    writing_context,
                    current_context,
                )
                if section:
                    report_sections.append(section)
                    for finding in scope.primary_findings + scope.supporting_findings:
                        if finding.finding_id and finding.finding_id not in writing_context.used_finding_ids:
                            writing_context.used_finding_ids.append(finding.finding_id)
                    writing_context.previous_section_summary = _summary_from_section(section.content)
                report_call_details.extend(section_details)
        except Exception as error:
            details = report_call_details + list(getattr(error, "llm_call_details", ()) or ())
            setattr(error, "llm_call_details", details)
            if getattr(error, "execution_context", None) is None and current_context is not None:
                setattr(error, "execution_context", current_context)
            raise

        return _WriterSectionBatch(
            sections=report_sections,
            call_details=report_call_details,
            execution_context=current_context,
        )

    async def _write_section_result(
        self,
        state: ResearchState,
        scope: _SectionScope,
        documents: List[Document],
        writing_context: _WritingContext,
        execution_context=None,
    ) -> tuple[ReportSection | None, list[dict[str, Any]], Any]:
        section_kwargs: dict[str, Any] = {}
        if execution_context is not None:
            section_kwargs["execution_context"] = execution_context
        scoped_findings = scope.primary_findings + scope.supporting_findings
        supporting_documents = _documents_for_findings(scoped_findings, documents)
        written = await self._write_section(
            _research_query(state),
            scope.title,
            scope.primary_findings,
            supporting_documents,
            section_objective=scope.objective,
            supporting_findings=scope.supporting_findings,
            used_finding_ids=list(writing_context.used_finding_ids),
            previous_section_summary=writing_context.previous_section_summary,
            citation_documents=documents,
            **section_kwargs,
        )
        # Existing tests and external injection seams return the old two-tuple.
        # The production implementation returns context.
        if len(written) == 3:
            section, raw_details, current_context = written
        else:
            section, legacy_detail = written
            raw_details = [legacy_detail] if isinstance(legacy_detail, dict) else []
            current_context = execution_context
        section_details = [
            self._annotate_section_detail(detail, scope.index - 1, scope.title)
            for detail in (raw_details or [])
            if isinstance(detail, dict)
        ]
        return section, section_details, current_context

    def _annotate_section_detail(
        self,
        detail: dict[str, Any],
        section_index: int,
        section_title: str,
    ) -> dict[str, Any]:
        annotated = dict(detail)
        annotated.setdefault("section_index", section_index)
        annotated.setdefault("section_title", section_title)
        return annotated

    async def _write_section(
        self,
        topic: str,
        section_title: str,
        findings: List[Finding],
        documents: List[Document],
        *,
        section_objective: str | None = None,
        supporting_findings: List[Finding] | None = None,
        used_finding_ids: List[str] | None = None,
        previous_section_summary: str = "",
        citation_documents: List[Document] | None = None,
        execution_context=None,
    ) -> tuple:
        """Write one section; runtime owns retries, budget, and deadline."""
        logger.info(f"正在撰写章节：{section_title}")

        supporting_findings = supporting_findings or []
        used_finding_ids = used_finding_ids or []
        citation_documents = citation_documents or documents

        system_prompt = WRITER_SYSTEM_PROMPT.format(min_words=config.min_section_words)

        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("human", "{input}")
        ])

        sources_context = ""
        if documents:
            supporting_document_ids = {document.document_id for document in documents}
            sources_context = "\n可用于引用的来源：\n" + "\n".join(
                f"[{index + 1}] {document.title} ({document.uri})"
                for index, document in enumerate(citation_documents)
                if document.document_id in supporting_document_ids
            )

        def render_findings(items: List[Finding]) -> str:
            return chr(10).join(
                f"- ({finding.finding_id}) {finding.statement}（建议来源："
                + ", ".join(
                    f"[{index + 1}]"
                    for index, document in enumerate(citation_documents)
                    if document.document_id in finding.source_document_ids
                )
                + "）"
                for finding in items
            ) or "- 无"

        input_message = WRITER_USER_TEMPLATE.format(
            topic=topic,
            section_title=section_title,
            section_objective=section_objective or section_title,
            min_words=config.min_section_words,
            primary_findings=render_findings(findings),
            supporting_findings=render_findings(supporting_findings),
            used_finding_ids=", ".join(used_finding_ids) or "无",
            previous_section_summary=previous_section_summary or "无（这是正文第一章）",
            sources_context=sources_context,
        )
        chain = prompt | self.llm | StrOutputParser()
        execution = await execute_llm_operation(
            lambda: chain.ainvoke({"input": input_message}),
            agent="ReportWriter",
            operation_name=f"write_section_{section_title[:30]}",
            model=config.model_name,
            input_text=input_message,
            local_timeout_seconds=LLM_OPERATION_TIMEOUT_SECONDS,
            max_retries=_legacy_attempt_limit_to_retries(self.max_retries),
            context=execution_context,
        )
        content = execution.value if isinstance(execution.value, str) else str(execution.value)
        if not content or len(content.strip()) < 50:
            logger.warning(f"章节“{section_title}”生成的内容不足：{len(content)} 个字符")
            if findings:
                logger.info(f"正在为章节“{section_title}”创建备用内容")
                content = f"\n\n{chr(10).join(finding.statement for finding in findings[:3])}\n\n"
            else:
                logger.error(f"无法创建章节“{section_title}”：没有内容和研究发现")
                return None, execution.call_details, execution.context

        def keep_valid_citation(match: re.Match[str]) -> str:
            number = int(match.group(1))
            return match.group(0) if 1 <= number <= len(citation_documents) else ""

        content = _section_body(content)
        content = re.sub(r'\[(\d+)\]', keep_valid_citation, content)
        citations = re.findall(r'\[(\d+)\]', content)
        source_urls = []
        seen_source_urls = set()
        for cite_num in citations:
            idx = int(cite_num) - 1
            if 0 <= idx < len(citation_documents):
                url = citation_documents[idx].uri
                if url and url not in seen_source_urls:
                    seen_source_urls.add(url)
                    source_urls.append(url)
        section = ReportSection(
            title=section_title,
            content=content,
            sources=source_urls,
        )
        logger.info(f"章节“{section_title}”撰写成功：{len(content)} 个字符")
        return section, execution.call_details, execution.context

    def _compile_report(
        self,
        topic: str,
        plan,
        report_sections: List[ReportSection],
        documents: List[Document],
    ) -> str:
        """Compile sections against the one canonical bibliography order."""
        source_count = len(documents)

        report_parts = [
            f"# {topic}\n",
            f"**深度研究报告**\n",
            f"\n## 执行摘要\n",
            f"本报告对 {topic} 进行了全面分析。",
            f"本次研究覆盖 **{source_count} 个来源**，",
            f"并综合为 **{len(report_sections)} 个主要章节**。\n",
            f"\n## 研究目标\n"
        ]

        if plan and hasattr(plan, 'objectives'):
            for i, obj in enumerate(plan.objectives, 1):
                report_parts.append(f"{i}. {obj}\n")

        report_parts.append("\n---\n")

        body_sections = [section for section in report_sections if not _reserved_heading(section.title)]
        for section_number, section in enumerate(body_sections, 1):
            # Apply the boundary again for injected/legacy section writers.
            content = _section_body(section.content)
            report_parts.append(f"\n## {section_number}. {section.title}\n\n")
            report_parts.append(content)
            report_parts.append("\n")

        report_parts.append("\n---\n\n## 参考文献\n\n")
        if documents:
            for i, document in enumerate(documents, 1):
                citation = self._format_citation(document)
                report_parts.append(f"{i}. {citation}\n")
        else:
            report_parts.append("*本次研究没有可用来源。*\n")

        return "".join(report_parts)

    def _format_citation(self, document: Document) -> str:
        style = self.citation_style.lower()
        if style == "mla":
            return self.citation_formatter.format_mla(document.uri, document.title)
        if style == "chicago":
            return self.citation_formatter.format_chicago(document.uri, document.title)
        if style == "ieee":
            return self.citation_formatter.format_ieee(document.uri, document.title)
        return self.citation_formatter.format_apa(document.uri, document.title)


__all__ = ["ReportWriter"]
