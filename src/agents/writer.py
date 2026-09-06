"""Writer node for the canonical Documents → Findings → Report flow."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import re
from typing import Any, Dict, List, Literal, Optional

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
from src.execution_policy import ExecutionContextCoordinator
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
        section_execution_mode: Literal["serial", "bounded"] | None = None,
        section_concurrency: int | None = None,
    ):
        self.llm = llm or get_llm(temperature=0.7)
        self.tools = get_research_tools(agent_type="writing")
        self.max_retries = max_retries
        self.citation_style = citation_style
        self.citation_formatter = citation_formatter or CitationFormatter()
        self.section_execution_mode = section_execution_mode or config.writer_section_execution_mode
        self.section_concurrency = max(1, section_concurrency or config.writer_section_concurrency)

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
        if self.section_execution_mode == "bounded" and self.section_concurrency > 1:
            return await self._write_sections_bounded(state, documents, findings)
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
        total_sections = len(_research_plan(state).report_outline) if _research_plan(state) else 0

        try:
            for section_idx, section_title in enumerate(_research_plan(state).report_outline, 1):
                await emit_writing_section(section_title, section_idx, total_sections)
                section, section_details, current_context = await self._write_section_result(
                    state,
                    section_idx - 1,
                    section_title,
                    documents,
                    findings,
                    current_context,
                )
                if section:
                    report_sections.append(section)
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

    async def _write_sections_bounded(
        self,
        state: ResearchState,
        documents: List[Document],
        findings: List[Finding],
    ) -> _WriterSectionBatch:
        outline = list(_research_plan(state).report_outline) if _research_plan(state) else []
        total_sections = len(outline)
        coordinator = ExecutionContextCoordinator(state.execution_context)
        results: list[tuple[ReportSection | None, list[dict[str, Any]]] | None] = [
            None for _ in outline
        ]
        failures: dict[int, Exception] = {}
        failure_observed = asyncio.Event()
        queue: asyncio.Queue[tuple[int, str]] = asyncio.Queue()
        for index, section_title in enumerate(outline):
            await emit_writing_section(section_title, index + 1, total_sections)
            queue.put_nowait((index, section_title))

        async def worker() -> None:
            while not failure_observed.is_set():
                try:
                    index, section_title = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                if failure_observed.is_set():
                    queue.task_done()
                    return
                try:
                    section, section_details, _context = await self._write_section_result(
                        state,
                        index,
                        section_title,
                        documents,
                        findings,
                        coordinator,
                    )
                    results[index] = (section, section_details)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    failures[index] = error
                    failure_observed.set()
                finally:
                    queue.task_done()

        worker_count = min(self.section_concurrency, total_sections)
        tasks = [asyncio.create_task(worker()) for _ in range(worker_count)]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        ordered_details = self._ordered_section_details(results, failures, total_sections)
        if failures:
            primary_index = min(failures)
            primary = failures[primary_index]
            setattr(primary, "llm_call_details", ordered_details)
            if getattr(primary, "execution_context", None) is None and coordinator.context is not None:
                setattr(primary, "execution_context", coordinator.context)
            raise primary

        return _WriterSectionBatch(
            sections=[
                section
                for item in results
                if item is not None
                for section in [item[0]]
                if section is not None
            ],
            call_details=ordered_details,
            execution_context=coordinator.context,
        )

    def _ordered_section_details(
        self,
        results: list[tuple[ReportSection | None, list[dict[str, Any]]] | None],
        failures: dict[int, Exception],
        total_sections: int,
    ) -> list[dict[str, Any]]:
        details: list[dict[str, Any]] = []
        for index in range(total_sections):
            if results[index] is not None:
                details.extend(results[index][1])
            elif index in failures:
                details.extend(list(getattr(failures[index], "llm_call_details", ()) or ()))
        return details

    async def _write_section_result(
        self,
        state: ResearchState,
        section_index: int,
        section_title: str,
        documents: List[Document],
        findings: List[Finding],
        execution_context=None,
    ) -> tuple[ReportSection | None, list[dict[str, Any]], Any]:
        section_kwargs: dict[str, Any] = {}
        if execution_context is not None:
            section_kwargs["execution_context"] = execution_context
        written = await self._write_section(
            _research_query(state),
            section_title,
            findings,
            documents,
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
            self._annotate_section_detail(detail, section_index, section_title)
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
        annotated.setdefault("writer_execution_mode", self.section_execution_mode)
        annotated.setdefault("writer_section_concurrency", self.section_concurrency)
        return annotated

    async def _write_section(
        self,
        topic: str,
        section_title: str,
        findings: List[Finding],
        documents: List[Document],
        execution_context=None,
    ) -> tuple:
        """Write one section; runtime owns retries, budget, and deadline."""
        logger.info(f"正在撰写章节：{section_title}")

        system_prompt = WRITER_SYSTEM_PROMPT.format(min_words=config.min_section_words)

        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("human", "{input}")
        ])

        sources_context = ""
        if documents:
            sources_context = "\n可用于引用的来源：\n" + "\n".join(
                f"[{i+1}] {document.title} ({document.uri})"
                for i, document in enumerate(documents)
            )
        input_message = WRITER_USER_TEMPLATE.format(
            topic=topic,
            section_title=section_title,
            min_words=config.min_section_words,
            findings=chr(10).join(
                f"- {finding.statement}（建议来源："
                + ", ".join(
                    f"[{index + 1}]"
                    for index, document in enumerate(documents)
                    if document.document_id in finding.source_document_ids
                )
                + "）"
                for finding in findings
            ),
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
            return match.group(0) if 1 <= number <= len(documents) else ""

        content = re.sub(r'\[(\d+)\]', keep_valid_citation, content)
        citations = re.findall(r'\[(\d+)\]', content)
        source_urls = []
        seen_source_urls = set()
        for cite_num in citations:
            idx = int(cite_num) - 1
            if 0 <= idx < len(documents):
                url = documents[idx].uri
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

        has_references_section = False
        for section in report_sections:
            content = section.content.strip()

            if "## References" in content or section.title.lower() in {"references", "参考文献"}:
                has_references_section = True

            if content.startswith(f"## {section.title}"):
                report_parts.append(f"\n{content}\n\n")
            else:
                report_parts.append(f"\n## {section.title}\n\n")
                report_parts.append(content)
                report_parts.append("\n")

        if not has_references_section:
            report_parts.append("\n---\n\n## 参考文献\n\n")

        if not has_references_section:
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
