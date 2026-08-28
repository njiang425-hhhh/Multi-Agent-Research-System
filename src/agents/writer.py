"""Writer node for the legacy-stable source and finding sequence."""

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
    _usage_from_legacy_totals,
)
from src.callbacks import emit_error, emit_writing_complete, emit_writing_section, emit_writing_start
from src.config import config
from src.execution_policy import ExecutionContextCoordinator
from src.exceptions import ReportGenerationError
from src.llm.factory import get_llm
from src.llm_execution import execute_llm_operation
from src.prompts import WRITER_SYSTEM_PROMPT, WRITER_USER_TEMPLATE
from src.runtime_lifecycle import completed_lifecycle_patch, failed_lifecycle_patch
from src.state import Report, ReportSection, ResearchState, SearchResult
from src.state_compat import canonical_iteration, canonical_plan as _research_plan, canonical_query as _research_query, canonical_usage
from src.utils.citations import CitationFormatter
from src.utils.tools import get_research_tools


logger = logging.getLogger(__name__)


def _report_citations(
    search_results: List[SearchResult],
    report_sections: List[ReportSection],
) -> List[str]:
    """Project report sources into an ordered, stable, de-duplicated URL list."""
    citations: List[str] = []
    seen_urls = set()

    for result in search_results:
        url = getattr(result, "url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            citations.append(url)

    for section in report_sections:
        for url in getattr(section, "sources", []) or []:
            if url and url not in seen_urls:
                seen_urls.add(url)
                citations.append(url)

    return citations


def _legacy_report_to_v1(
    state: ResearchState,
    report_sections: List[ReportSection],
    final_report: str,
) -> Report:
    """Explicitly project successful legacy Writer output into the V1 contract."""
    return Report(
        title=state.research_topic,
        sections=list(report_sections),
        content=final_report,
        citations=_report_citations(state.search_results, report_sections),
        status="completed",
    )


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

        if not state.plan or not state.key_findings:
            await emit_error("报告生成所需的数据不足")
            return {"error": "报告生成所需的数据不足", **failed_lifecycle_patch()}

        await emit_writing_start(len(state.plan.report_outline))

        report_sections: list[ReportSection] = []
        report_call_details: list[dict[str, Any]] = []
        current_context = state.execution_context

        try:
            batch = await self._write_sections(state)
            report_sections = batch.sections
            report_call_details = batch.call_details
            current_context = batch.execution_context

            if not report_sections:
                raise ReportGenerationError("未生成报告章节")

            temp_state = ResearchState(
                research_topic=state.research_topic,
                plan=state.plan,
                report_sections=report_sections,
                search_results=state.search_results,
            )
            final_report = self._compile_report(temp_state)
            if state.search_results:
                final_report = self.citation_formatter.update_report_citations(
                    final_report,
                    style=self.citation_style,
                    search_results=state.search_results,
                )
            if state.credibility_scores:
                high_cred_sources = [
                    i + 1 for i, score in enumerate(state.credibility_scores)
                    if score.get("level") == "high"
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
        report = _legacy_report_to_v1(state, report_sections, final_report)
        patch: Dict[str, Any] = {
            "report_sections": report_sections,
            "final_report": final_report,
            "report": report,
            **completed_lifecycle_patch(),
            "iterations": state.iterations + 1,
            "iteration": state.iteration + 1,
            "llm_calls": state.llm_calls + calls,
            "total_input_tokens": state.total_input_tokens + input_tokens,
            "total_output_tokens": state.total_output_tokens + output_tokens,
            "llm_call_details": state.llm_call_details + report_call_details,
            "usage": _usage_from_legacy_totals(
                state,
                llm_calls=state.llm_calls + calls,
                total_input_tokens=state.total_input_tokens + input_tokens,
                total_output_tokens=state.total_output_tokens + output_tokens,
            ),
        }
        if current_context is not None:
            patch["execution_context"] = current_context
        return patch

    async def _write_sections(self, state: ResearchState) -> _WriterSectionBatch:
        if self.section_execution_mode == "bounded" and self.section_concurrency > 1:
            return await self._write_sections_bounded(state)
        return await self._write_sections_serial(state)

    async def _write_sections_serial(self, state: ResearchState) -> _WriterSectionBatch:
        report_sections: list[ReportSection] = []
        report_call_details: list[dict[str, Any]] = []
        current_context = state.execution_context
        total_sections = len(state.plan.report_outline) if state.plan else 0

        try:
            for section_idx, section_title in enumerate(state.plan.report_outline, 1):
                await emit_writing_section(section_title, section_idx, total_sections)
                section, section_details, current_context = await self._write_section_result(
                    state,
                    section_idx - 1,
                    section_title,
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

    async def _write_sections_bounded(self, state: ResearchState) -> _WriterSectionBatch:
        outline = list(state.plan.report_outline) if state.plan else []
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
        execution_context=None,
    ) -> tuple[ReportSection | None, list[dict[str, Any]], Any]:
        section_kwargs: dict[str, Any] = {}
        if execution_context is not None:
            section_kwargs["execution_context"] = execution_context
        written = await self._write_section(
            state.research_topic,
            section_title,
            state.key_findings,
            state.search_results,
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
        findings: List[str],
        search_results: List,
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
        if search_results:
            sources_context = "\n可用于引用的来源：\n" + "\n".join(
                f"[{i+1}] {r.title} ({r.url})"
                for i, r in enumerate(search_results[:15])
            )
        input_message = WRITER_USER_TEMPLATE.format(
            topic=topic,
            section_title=section_title,
            min_words=config.min_section_words,
            findings=chr(10).join(f"- {f}" for f in findings),
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
                content = f"\n\n{chr(10).join(findings[:3])}\n\n"
            else:
                logger.error(f"无法创建章节“{section_title}”：没有内容和研究发现")
                return None, execution.call_details, execution.context

        citations = re.findall(r'\[(\d+)\]', content)
        source_urls = []
        seen_source_urls = set()
        for cite_num in citations:
            idx = int(cite_num) - 1
            if 0 <= idx < len(search_results):
                url = search_results[idx].url
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

    def _compile_report(self, state: ResearchState) -> str:
        """将所有章节汇编为最终报告。"""
        search_results = getattr(state, 'search_results', []) or []
        report_sections = getattr(state, 'report_sections', []) or []

        unique_sources = set()
        for result in search_results:
            if hasattr(result, 'url') and result.url:
                unique_sources.add(result.url)

        for section in report_sections:
            if hasattr(section, 'sources'):
                unique_sources.update(section.sources)

        source_count = len(unique_sources) if unique_sources else len(search_results)

        report_parts = [
            f"# {state.research_topic}\n",
            f"**深度研究报告**\n",
            f"\n## 执行摘要\n",
            f"本报告对 {state.research_topic} 进行了全面分析。",
            f"本次研究覆盖 **{source_count} 个来源**，",
            f"并综合为 **{len(report_sections)} 个主要章节**。\n",
            f"\n## 研究目标\n"
        ]

        if state.plan and hasattr(state.plan, 'objectives'):
            for i, obj in enumerate(state.plan.objectives, 1):
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

        source_info = []
        seen_urls = set()

        for result in search_results:
            if hasattr(result, 'url') and result.url and result.url not in seen_urls:
                seen_urls.add(result.url)
                title = getattr(result, 'title', '')
                source_info.append((result.url, title))

        for section in report_sections:
            if hasattr(section, 'sources'):
                for url in section.sources:
                    if url not in seen_urls:
                        seen_urls.add(url)
                        source_info.append((url, ''))

        if not has_references_section:
            if source_info:
                for i, (url, title) in enumerate(source_info[:30], 1):
                    citation = self.citation_formatter.format_apa(url, title)
                    report_parts.append(f"{i}. {citation}\n")
            else:
                report_parts.append("*本次研究没有可用来源。*\n")

        return "".join(report_parts)
