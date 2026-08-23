"""带依赖注入的研究流程代理节点。"""

import asyncio
from copy import copy
from dataclasses import dataclass
from typing import Callable, List, Optional, Dict, Any, Protocol, Literal
import logging
import time
import json
import re
from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser, JsonOutputParser
from langchain_core.language_models import BaseChatModel
from langchain.agents import create_agent

from src.state import (
    EvidenceDiagnostics,
    Finding,
    MemoryItem,
    ResearchState,
    ResearchPlan,
    SearchQuery,
    Report,
    ReportSection,
    SearchResult,
    UsageMetrics,
)
from src.runtime_lifecycle import completed_lifecycle_patch, failed_lifecycle_patch
from src.llm.factory import get_llm
from src.utils.tools import get_research_tools
from src.config import config
from src.utils.credibility import CredibilityScorer
from src.utils.citations import CitationFormatter
from src.llm_tracker import estimate_tokens
from src.llm_execution import execute_llm_operation
from src.execution_policy import ExecutionContextCoordinator
from src.exceptions import PlanningError, SearchError, SynthesisError, ReportGenerationError
from src.search.config import SearchConfig
from src.search.executor import SearchExecutor
from src.search.coverage import SearchCoverage, measure_search_coverage
from src.evidence.adapters import normalize_web_url, scored_search_results_to_documents
from src.evidence.compat import key_findings_to_findings
from src.evidence.config import EvidenceRuntimeConfig
from src.evidence.sidecar import EvidenceSidecarResult, EvidenceSidecarService
from src.memory import (
    ResearchMemoryStore,
    build_search_memory_hints,
    format_planner_memory_context,
    memory_retrieval_items,
)
from src.planning import normalize_research_plan
from src.prompts import (
    PLANNER_SYSTEM_PROMPT, PLANNER_USER_TEMPLATE,
    SEARCHER_SYSTEM_PROMPT, SEARCHER_USER_TEMPLATE,
    SYNTHESIZER_SYSTEM_PROMPT, SYNTHESIZER_USER_TEMPLATE,
    WRITER_SYSTEM_PROMPT, WRITER_USER_TEMPLATE
)
from src.callbacks import (
    emit_planning_start, emit_planning_complete,
    emit_search_start, emit_search_results, 
    emit_extraction_start, emit_extraction_complete,
    emit_synthesis_start, emit_synthesis_progress, emit_synthesis_complete,
    emit_writing_start, emit_writing_section, emit_writing_complete,
    emit_error
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Searcher stability limits. These are intentionally local to the Searcher so
# the other Agents and the shared Graph behavior remain unchanged.
SEARCHER_AGENT_RECURSION_LIMIT = 12
SEARCHER_AGENT_TIMEOUT_SECONDS = 90
LLM_OPERATION_TIMEOUT_SECONDS = 90
SEARCHER_ADAPTIVE_MAX_ROUNDS = 1
SEARCHER_ADAPTIVE_TIMEOUT_SECONDS = 30.0
SEARCHER_ADAPTIVE_MAX_SEARCH_CALLS = 1
SEARCHER_ADAPTIVE_MAX_EXTRACT_CALLS = 1


def _default_memory_store() -> ResearchMemoryStore:
    return ResearchMemoryStore(
        Path(config.research_memory_store_path),
        max_records=config.research_memory_max_records,
    )


def _retrieve_memory_items_with_diagnostics(
    store: ResearchMemoryStore | None,
    query: str,
) -> tuple[list[MemoryItem], dict[str, Any]]:
    if (
        not config.research_memory_enabled
        or config.research_memory_retrieval_limit <= 0
        or store is None
    ):
        return [], {
            "enabled": bool(config.research_memory_enabled),
            "retrieval_outcome": "disabled_or_unavailable",
            "retrieved_count": 0,
            "retrieved_memory_ids": [],
            "retrieval": [],
        }
    try:
        items, observations = memory_retrieval_items(
            query,
            store.retrieve(query, limit=config.research_memory_retrieval_limit),
        )
        return items, {
            "enabled": True,
            "retrieval_outcome": "completed",
            "retrieved_count": len(items),
            "retrieved_memory_ids": [item.memory_id for item in items],
            "retrieval": observations,
        }
    except Exception as exc:
        logger.warning("Research memory retrieval failed: %s", exc)
        return [], {
            "enabled": True,
            "retrieval_outcome": "failed",
            "retrieval_error": str(exc),
            "retrieved_count": 0,
            "retrieved_memory_ids": [],
            "retrieval": [],
        }


def _retrieve_memory_items(
    store: ResearchMemoryStore | None,
    query: str,
) -> list[MemoryItem]:
    """Compatibility wrapper for the P8 retrieval projection."""

    items, _ = _retrieve_memory_items_with_diagnostics(store, query)
    return items


def _research_query(state: ResearchState) -> str:
    """Read the V1 task field, preserving legacy-only checkpoint support."""
    return state.query or state.research_topic


def _research_plan(state: ResearchState) -> Optional[ResearchPlan]:
    """Read the V1 plan field, preserving legacy-only checkpoint support."""
    return state.research_plan or state.plan


def _usage_from_legacy_totals(
    state: ResearchState,
    *,
    llm_calls: int,
    total_input_tokens: int,
    total_output_tokens: int,
) -> UsageMetrics:
    """Explicitly mirror final legacy tracking totals into V1 UsageMetrics."""
    return UsageMetrics(
        llm_calls=llm_calls,
        tool_calls=state.usage.tool_calls,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        total_tokens=total_input_tokens + total_output_tokens,
        latency_seconds=state.usage.latency_seconds,
        estimated_cost=state.usage.estimated_cost,
    )


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


def _legacy_attempt_limit_to_retries(max_attempts: int) -> int:
    """Preserve the historical constructor bound while using P4 semantics."""
    return max(0, max_attempts - 1)


def _llm_patch_totals(
    state: ResearchState,
    call_details: List[Dict[str, Any]],
) -> tuple[int, int, int]:
    """Count every real attempt once; output tokens exist only on success."""
    calls = len(call_details)
    input_tokens = sum(int(item.get("input_tokens") or 0) for item in call_details)
    output_tokens = sum(int(item.get("output_tokens") or 0) for item in call_details)
    return calls, input_tokens, output_tokens


def _llm_failure_patch(
    state: ResearchState,
    error: Exception,
    message: str,
    *,
    include_iteration: bool = True,
) -> Dict[str, Any]:
    """Create one legacy-compatible failure patch with P4 attempt records."""
    details = list(getattr(error, "llm_call_details", ()) or ())
    calls, input_tokens, output_tokens = _llm_patch_totals(state, details)
    patch: Dict[str, Any] = {
        "error": message,
        **({"iterations": state.iterations + 1, "iteration": state.iteration + 1} if include_iteration else {}),
        **failed_lifecycle_patch(),
    }
    if details:
        patch.update(
            {
                "llm_calls": state.llm_calls + calls,
                "total_input_tokens": state.total_input_tokens + input_tokens,
                "total_output_tokens": state.total_output_tokens + output_tokens,
                "llm_call_details": state.llm_call_details + details,
                "usage": _usage_from_legacy_totals(
                    state,
                    llm_calls=state.llm_calls + calls,
                    total_input_tokens=state.total_input_tokens + input_tokens,
                    total_output_tokens=state.total_output_tokens + output_tokens,
                ),
            }
        )
    execution_context = getattr(error, "context", None) or getattr(error, "execution_context", None)
    if execution_context is not None:
        patch["execution_context"] = execution_context
    return patch


# =============================================================================
# 研究规划代理
# =============================================================================

class ResearchPlanner:
    """负责规划研究策略的自主代理。"""
    
    def __init__(
        self,
        llm: Optional[BaseChatModel] = None,
        max_retries: int = 3,
        memory_store: ResearchMemoryStore | None = None,
    ):
        self.llm = llm or get_llm(temperature=0.7)
        self.max_retries = max_retries
        self.memory_store = (
            memory_store
            if memory_store is not None
            else (_default_memory_store() if config.research_memory_enabled else None)
        )

    async def plan(self, state: ResearchState) -> Dict[str, Any]:
        """使用结构化 LLM 输出创建研究计划。
        
        返回将由 LangGraph 合并到状态中的更新字典。
        """
        topic = _research_query(state)
        logger.info(f"正在规划研究：{topic}")
        retrieved_memory, memory_diagnostics = _retrieve_memory_items_with_diagnostics(
            self.memory_store,
            topic,
        )
        memory_context = format_planner_memory_context(retrieved_memory)
        if config.research_memory_enabled:
            memory_diagnostics["planner_context_memory_ids"] = [
                item.memory_id for item in retrieved_memory
            ]
            memory_diagnostics["planner_context_count"] = len(retrieved_memory)
        
        await emit_planning_start(topic)
        
        system_prompt = PLANNER_SYSTEM_PROMPT.format(
            max_queries=config.max_search_queries,
            max_sections=config.max_report_sections
        )
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("human", PLANNER_USER_TEMPLATE)
        ])
        
        chain = prompt | self.llm | JsonOutputParser()
        input_text = (
            f"{topic} {config.max_search_queries} {config.max_report_sections} "
            f"{memory_context}"
        )
        try:
            execution = await execute_llm_operation(
                lambda: chain.ainvoke({
                    "topic": topic,
                    "max_queries": config.max_search_queries,
                    "max_sections": config.max_report_sections,
                    "memory_context": memory_context,
                }),
                agent="ResearchPlanner",
                operation_name="plan",
                model=config.model_name,
                input_text=input_text,
                local_timeout_seconds=LLM_OPERATION_TIMEOUT_SECONDS,
                # Constructor compatibility: historical ``max_retries`` was
                # an attempt limit; the shared contract names retries clearly.
                max_retries=_legacy_attempt_limit_to_retries(self.max_retries),
                context=state.execution_context,
            )
        except Exception as error:
            logger.error(f"规划调用失败：{error}")
            await emit_error(f"规划失败：{error}")
            return _llm_failure_patch(state, error, f"Planning failed: {error}")

        try:
            result = execution.value
            if not all(key in result for key in ["topic", "objectives", "search_queries", "report_outline"]):
                raise PlanningError("返回的计划结构无效")
            if not result["search_queries"]:
                raise PlanningError("未生成搜索查询")
            plan = normalize_research_plan(
                result,
                max_queries=config.max_search_queries,
                max_sections=config.max_report_sections,
            )
        except Exception as error:
            setattr(error, "llm_call_details", execution.call_details)
            setattr(error, "execution_context", execution.context)
            await emit_error(f"规划失败：{error}")
            return _llm_failure_patch(state, error, f"Planning failed: {error}")

        calls, input_tokens, output_tokens = _llm_patch_totals(state, execution.call_details)
        logger.info(f"已创建包含 {len(plan.search_queries)} 个查询的计划（上限：{config.max_search_queries}）")
        logger.info(f"报告大纲包含 {len(plan.report_outline)} 个章节（上限：{config.max_report_sections}）")
        await emit_planning_complete(len(plan.search_queries), len(plan.report_outline))
        patch: Dict[str, Any] = {
            "plan": plan,
            "research_plan": plan,
            "retrieved_memory": retrieved_memory,
            "memory_ids": [item.memory_id for item in retrieved_memory],
            "current_stage": "searching",
            "iterations": state.iterations + 1,
            "iteration": state.iteration + 1,
            "llm_calls": state.llm_calls + calls,
            "total_input_tokens": state.total_input_tokens + input_tokens,
            "total_output_tokens": state.total_output_tokens + output_tokens,
            "llm_call_details": state.llm_call_details + execution.call_details,
            "usage": _usage_from_legacy_totals(
                state,
                llm_calls=state.llm_calls + calls,
                total_input_tokens=state.total_input_tokens + input_tokens,
                total_output_tokens=state.total_output_tokens + output_tokens,
            ),
        }
        if config.research_memory_enabled:
            patch["memory_diagnostics"] = memory_diagnostics
        if execution.context is not None:
            patch["execution_context"] = execution.context
        return patch


# =============================================================================
# 研究搜索代理
# =============================================================================

class ResearchSearcher:
    """负责执行研究搜索的自主代理。"""
    
    def __init__(
        self, 
        llm: Optional[BaseChatModel] = None,
        credibility_scorer: Optional[CredibilityScorer] = None,
        max_retries: int = 1,
        search_config: Optional[SearchConfig] = None,
        memory_store: ResearchMemoryStore | None = None,
    ):
        self.llm = llm or get_llm(temperature=0.3)
        self.tools = get_research_tools(agent_type="search")
        self.credibility_scorer = credibility_scorer or CredibilityScorer()
        self.max_retries = max_retries
        self.search_config = search_config or SearchConfig.from_project_config(config)
        self.search_executor = SearchExecutor(search_config=self.search_config)
        self.memory_store = (
            memory_store
            if memory_store is not None
            else (_default_memory_store() if config.research_memory_enabled else None)
        )

    @staticmethod
    def _has_content(result: SearchResult) -> bool:
        return bool(result.content and result.content.strip())

    @staticmethod
    def _coverage_metadata(coverage: SearchCoverage) -> dict[str, Any]:
        return {
            "result_query_coverage": coverage.result_query_coverage,
            "extracted_query_coverage": coverage.extracted_query_coverage,
            "missing_result_queries": [query.query for query in coverage.missing_result_queries],
            "missing_extracted_queries": [
                query.query for query in coverage.missing_extracted_queries
            ],
        }

    @staticmethod
    def _runtime_allows_adaptive_search(
        execution_context: Any,
    ) -> tuple[bool, str | None]:
        """Check only runtime-owned remaining capacity; never reserve or reset it."""

        if execution_context is None:
            return True, None
        remaining_timeout = execution_context.remaining_timeout_seconds()
        if remaining_timeout is not None and remaining_timeout <= 0:
            return False, "runtime_deadline_exhausted"
        remaining_operations = execution_context.remaining_operation_calls()
        if remaining_operations is not None and remaining_operations <= 0:
            return False, "runtime_budget_exhausted"
        return True, None

    def _adaptive_executor(self) -> Any:
        """Create a one-round executor without changing the primary executor."""

        if not isinstance(self.search_executor, SearchExecutor):
            # Keep injected legacy doubles observable as the primary executor.
            # Production uses SearchExecutor, where the bounded config below
            # is mandatory.
            return copy(self.search_executor)
        return SearchExecutor(
            search_config=SearchConfig(
                mode="deterministic_v2",
                max_search_times=SEARCHER_ADAPTIVE_MAX_SEARCH_CALLS,
                max_extract_times=SEARCHER_ADAPTIVE_MAX_EXTRACT_CALLS,
                max_results_per_search=min(self.search_config.max_results_per_search, 3),
                total_timeout_seconds=min(
                    self.search_config.total_timeout_seconds,
                    SEARCHER_ADAPTIVE_TIMEOUT_SECONDS,
                ),
                search_retry_times=0,
                extract_retry_times=0,
                allow_partial_results=True,
            ),
            search_tool=self.search_executor.search_tool,
            extract_tool=self.search_executor.extract_tool,
        )

    @staticmethod
    def _merge_primary_search_results(
        primary_results: List[SearchResult],
        supplementary_results: List[SearchResult],
    ) -> tuple[List[SearchResult], List[SearchResult], int]:
        """Keep primary ordering and allow supplementary content-only upgrades."""

        merged = list(primary_results)
        primary_positions: dict[str, int] = {}
        for index, result in enumerate(primary_results):
            normalized_url = normalize_web_url(result.url)
            if normalized_url and normalized_url not in primary_positions:
                primary_positions[normalized_url] = index

        appended: List[SearchResult] = []
        content_upgrades = 0
        for result in supplementary_results:
            normalized_url = normalize_web_url(result.url)
            if not normalized_url:
                continue
            primary_index = primary_positions.get(normalized_url)
            if primary_index is not None:
                primary = merged[primary_index]
                if not ResearchSearcher._has_content(primary) and ResearchSearcher._has_content(result):
                    merged[primary_index] = primary.model_copy(update={"content": result.content})
                    content_upgrades += 1
                continue
            primary_positions[normalized_url] = len(merged)
            merged.append(result)
            appended.append(result)
        return merged, appended, content_upgrades
        
    async def search(self, state: ResearchState) -> Dict[str, Any]:
        """使用工具自主执行研究搜索。
        
        返回将由 LangGraph 合并到状态中的搜索结果字典。
        """
        plan = _research_plan(state)
        if not plan:
            await emit_error("没有可用的研究计划")
            return {"error": "没有可用的研究计划", **failed_lifecycle_patch()}

        if self.search_config.mode == "deterministic_v2":
            return await self._search_with_executor(state)
        
        logger.info(f"自主代理开始研究：已规划 {len(plan.search_queries)} 个查询")
        
        total_queries = len(plan.search_queries)
        for i, query in enumerate(plan.search_queries, 1):
            await emit_search_start(query.query, i, total_queries)
        
        max_searches = min(config.max_search_queries, 3)
        max_results_per_search = min(config.max_search_results_per_query, 3)
        expected_total_results = max_searches * max_results_per_search
        max_extractions = min(max_searches + 1, 4)
        target_sources = min(expected_total_results, max_extractions)
        
        system_prompt = SEARCHER_SYSTEM_PROMPT.format(
            max_searches=max_searches,
            max_results_per_search=max_results_per_search,
            max_extractions=max_extractions,
            expected_total_results=target_sources
        )
        
        agent_graph = create_agent(
            self.llm,
            self.tools,
            system_prompt=system_prompt
        )
        
        for attempt in range(self.max_retries):
            try:
                start_time = time.time()
                
                objectives_text = "\n".join(f"- {obj}" for obj in plan.objectives)
                queries_text = "\n".join(
                    f"- {q.query} (Purpose: {q.purpose})" 
                    for q in plan.search_queries
                )
                
                input_message = SEARCHER_USER_TEMPLATE.format(
                    topic=_research_query(state),
                    objectives=objectives_text,
                    queries=queries_text,
                    min_sources=target_sources,
                    max_searches=max_searches,
                    max_extractions=max_extractions
                )
                
                input_tokens = estimate_tokens(input_message)
                
                try:
                    result = await asyncio.wait_for(
                        agent_graph.ainvoke(
                            {
                                "messages": [{"role": "user", "content": input_message}]
                            },
                            config={"recursion_limit": SEARCHER_AGENT_RECURSION_LIMIT},
                        ),
                        timeout=SEARCHER_AGENT_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError as exc:
                    raise SearchError(
                        f"搜索代理执行超时（{SEARCHER_AGENT_TIMEOUT_SECONDS} 秒）",
                        details=(
                            "Searcher Agent 超过单次执行时间限制；"
                            f"recursion_limit={SEARCHER_AGENT_RECURSION_LIMIT}"
                        ),
                    ) from exc
                except Exception as exc:
                    if isinstance(exc, SearchError):
                        raise
                    raise SearchError(
                        "搜索代理执行失败",
                        details=str(exc),
                    ) from exc
                
                duration = time.time() - start_time
                
                messages = result.get('messages', [])
                output_text = ""
                if messages:
                    output_text = str(messages[-1].content if hasattr(messages[-1], 'content') else str(messages[-1]))
                
                output_tokens = estimate_tokens(output_text)
                
                search_results = self._extract_results_from_messages(messages)
                
                logger.info(f"自主代理收集了 {len(search_results)} 个结果")
                
                total_extracted_chars = sum(
                    len(r.content) if r.content else 0 
                    for r in search_results
                )
                extracted_count = sum(1 for r in search_results if r.content)
                
                await emit_extraction_complete(extracted_count, total_extracted_chars)
                
                if not search_results:
                    await emit_error("代理没有收集到任何搜索结果")
                    raise SearchError("代理没有收集到任何搜索结果")
            
                scored_results = self.credibility_scorer.score_search_results(search_results)
                
                filtered_scored = [
                    item for item in scored_results
                    if item['credibility']['score'] >= config.min_credibility_score
                ]
                
                credibility_scores = [item['credibility'] for item in filtered_scored]
                sorted_results = [item['result'] for item in filtered_scored]
                documents = scored_search_results_to_documents(
                    (item["result"], item["credibility"])
                    for item in filtered_scored
                )
                
                logger.info(f"已过滤 {len(search_results)} -> {len(sorted_results)} 个结果（最低可信度={config.min_credibility_score}）")
                
                for q in plan.search_queries:
                    q.completed = True
                
                call_detail = {
                    'agent': 'ResearchSearcher',
                    'operation': 'autonomous_search',
                    'model': config.model_name,
                    'input_tokens': input_tokens,
                    'output_tokens': output_tokens,
                    'duration': round(duration, 2),
                    'results_count': len(sorted_results),
                    'original_results_count': len(search_results),
                    'min_credibility_score': config.min_credibility_score,
                    'attempt': attempt + 1
                }
                
                return {
                    "search_results": sorted_results,
                    "credibility_scores": credibility_scores,
                    "documents": documents,
                    "current_stage": "synthesizing",
                    "iterations": state.iterations + 1,
                    "iteration": state.iteration + 1,
                    "llm_calls": state.llm_calls + 1,
                    "total_input_tokens": state.total_input_tokens + input_tokens,
                    "total_output_tokens": state.total_output_tokens + output_tokens,
                    "llm_call_details": state.llm_call_details + [call_detail],
                    "usage": _usage_from_legacy_totals(
                        state,
                        llm_calls=state.llm_calls + 1,
                        total_input_tokens=state.total_input_tokens + input_tokens,
                        total_output_tokens=state.total_output_tokens + output_tokens,
                    ),
                }
                
            except SearchError as e:
                logger.warning(f"第 {attempt + 1} 次搜索尝试失败：{e}")
                if attempt == self.max_retries - 1:
                    logger.error(f"经过 {self.max_retries} 次尝试后搜索仍失败")
                    await emit_error(f"搜索失败：{e}")
                    return {
                        "error": f"搜索失败：{e}",
                        "iterations": state.iterations + 1,
                        "iteration": state.iteration + 1,
                        **failed_lifecycle_patch(),
                    }
                else:
                    await asyncio.sleep(2 ** attempt)
            except Exception as e:
                search_error = SearchError("搜索阶段执行失败", details=str(e))
                logger.warning(f"第 {attempt + 1} 次搜索尝试失败：{search_error}")
                if attempt == self.max_retries - 1:
                    logger.error(f"经过 {self.max_retries} 次尝试后搜索仍失败")
                    await emit_error(f"搜索失败：{search_error}")
                    return {
                        "error": f"搜索失败：{search_error}",
                        "iterations": state.iterations + 1,
                        "iteration": state.iteration + 1,
                        **failed_lifecycle_patch(),
                    }
                else:
                    await asyncio.sleep(2 ** attempt)
        
        return {
            "error": "搜索失败：已超过最大重试次数",
            "iterations": state.iterations + 1,
            "iteration": state.iteration + 1,
            **failed_lifecycle_patch(),
        }

    async def _search_with_executor(self, state: ResearchState) -> Dict[str, Any]:
        """Run deterministic_v2 while preserving the legacy Agent contract."""
        plan = _research_plan(state)
        if not plan:
            await emit_error("没有可用的研究计划")
            return {"error": "没有可用的研究计划", **failed_lifecycle_patch()}

        logger.info(
            "Deterministic Search Executor 开始研究：已规划 "
            f"{len(plan.search_queries)} 个查询"
        )

        retrieved_memory = list(state.retrieved_memory)
        memory_diagnostics = dict(state.memory_diagnostics)
        if not retrieved_memory:
            retrieved_memory, retrieval_diagnostics = _retrieve_memory_items_with_diagnostics(
                self.memory_store,
                _research_query(state),
            )
            if config.research_memory_enabled:
                memory_diagnostics.update(retrieval_diagnostics)
        memory_hints = build_search_memory_hints(
            _research_query(state),
            retrieved_memory,
            limit=config.research_memory_search_hint_limit,
        )
        search_queries = [*plan.search_queries, *memory_hints]
        if config.research_memory_enabled:
            memory_diagnostics.update(
                {
                    "enabled": True,
                    "retrieved_count": len(retrieved_memory),
                    "retrieved_memory_ids": [item.memory_id for item in retrieved_memory],
                    "searcher_site_hints": [hint.query for hint in memory_hints],
                    "searcher_site_hint_count": len(memory_hints),
                }
            )

        total_queries = len(search_queries)
        for i, query in enumerate(search_queries, 1):
            await emit_search_start(query.query, i, total_queries)

        search_args = dict(
            max_results_per_search=self.search_config.max_results_per_search,
        )
        # Keep direct legacy executor doubles compatible when there is no P4
        # runtime context; runner-created states always supply one.
        if state.execution_context is not None:
            search_args["execution_context"] = state.execution_context
        execution = await self.search_executor.execute(search_queries, **search_args)

        search_results = execution.search_results
        latest_execution_context = execution.execution_context
        primary_coverage = measure_search_coverage(plan, search_results)
        adaptive_diagnostic: dict[str, Any] = {
            "kind": "adaptive_search",
            "enabled": bool(config.searcher_adaptive_enabled),
            "max_rounds": SEARCHER_ADAPTIVE_MAX_ROUNDS,
            "rounds_attempted": 0,
            "primary_coverage": self._coverage_metadata(primary_coverage),
        }

        has_usable_primary_result = any(normalize_web_url(result.url) for result in search_results)
        coverage_incomplete = (
            primary_coverage.result_query_coverage < 1.0
            or primary_coverage.extracted_query_coverage < 1.0
        )
        configured_rounds = min(
            max(0, config.searcher_adaptive_max_rounds),
            SEARCHER_ADAPTIVE_MAX_ROUNDS,
        )
        adaptive_attempted = False
        supplementary_execution = None

        if not has_usable_primary_result:
            adaptive_diagnostic["outcome"] = "skipped_no_usable_primary_results"
        elif not config.searcher_adaptive_enabled or configured_rounds == 0:
            adaptive_diagnostic["outcome"] = "disabled"
        elif not coverage_incomplete:
            adaptive_diagnostic["outcome"] = "not_needed"
        else:
            current_context = latest_execution_context or state.execution_context
            allowed, skip_reason = self._runtime_allows_adaptive_search(current_context)
            if not allowed:
                adaptive_diagnostic["outcome"] = "skipped_runtime_unavailable"
                adaptive_diagnostic["skip_reason"] = skip_reason
            elif not adaptive_attempted:
                supplementary_query = (
                    primary_coverage.missing_result_queries[0]
                    if primary_coverage.missing_result_queries
                    else primary_coverage.missing_extracted_queries[0]
                )
                adaptive_attempted = True
                adaptive_diagnostic["rounds_attempted"] = 1
                adaptive_diagnostic["supplementary_query"] = supplementary_query.query
                await emit_search_start(supplementary_query.query, 1, 1)
                adaptive_executor = self._adaptive_executor()
                adaptive_args: dict[str, Any] = {
                    "max_results_per_search": min(self.search_config.max_results_per_search, 3),
                }
                if isinstance(adaptive_executor, SearchExecutor):
                    adaptive_args["exclude_urls"] = [result.url for result in search_results]
                if current_context is not None:
                    adaptive_args["execution_context"] = current_context
                try:
                    supplementary_execution = await adaptive_executor.execute(
                        [supplementary_query],
                        **adaptive_args,
                    )
                except Exception as exc:
                    adaptive_diagnostic["outcome"] = "supplementary_failed"
                    adaptive_diagnostic["supplementary_error"] = str(exc)
                else:
                    if supplementary_execution.execution_context is not None:
                        latest_execution_context = supplementary_execution.execution_context
                    search_results, appended_results, content_upgrades = self._merge_primary_search_results(
                        search_results,
                        supplementary_execution.search_results,
                    )
                    post_coverage = measure_search_coverage(plan, search_results)
                    coverage_improved = (
                        post_coverage.result_query_coverage > primary_coverage.result_query_coverage
                        or post_coverage.extracted_query_coverage
                        > primary_coverage.extracted_query_coverage
                    )
                    new_content_bearing_results = sum(
                        1 for result in appended_results if self._has_content(result)
                    ) + content_upgrades
                    adaptive_diagnostic.update(
                        {
                            "supplementary_error": supplementary_execution.error,
                            "supplementary_partial": supplementary_execution.partial,
                            "supplementary_search_calls": supplementary_execution.stats.search_calls,
                            "supplementary_extract_calls": supplementary_execution.stats.extract_calls,
                            "new_unique_urls": len(appended_results),
                            "content_upgrades": content_upgrades,
                            "new_content_bearing_results": new_content_bearing_results,
                            "coverage_improved": coverage_improved,
                            "post_coverage": self._coverage_metadata(post_coverage),
                        }
                    )
                    if (
                        supplementary_execution.error
                        and supplementary_execution.error != "No search results"
                        and not appended_results
                        and not content_upgrades
                    ):
                        adaptive_diagnostic["outcome"] = "supplementary_failed"
                    elif not appended_results and not content_upgrades:
                        adaptive_diagnostic["outcome"] = "no_progress"
                    elif coverage_improved and new_content_bearing_results > 0:
                        adaptive_diagnostic["outcome"] = "completed"
                    else:
                        adaptive_diagnostic["outcome"] = "no_progress"

        total_extracted_chars = sum(
            len(result.content) if result.content else 0
            for result in search_results
        )
        extracted_count = sum(1 for result in search_results if result.content)
        await emit_extraction_complete(extracted_count, total_extracted_chars)

        if not search_results:
            error = execution.error or "Deterministic Search Executor 没有返回搜索结果"
            await emit_error(f"搜索失败：{error}")
            failure_patch = {
                "search_results": [],
                "credibility_scores": [],
                "error": f"搜索失败：{error}",
                "iterations": state.iterations + 1,
                "iteration": state.iteration + 1,
                **failed_lifecycle_patch(),
            }
            if execution.execution_context is not None:
                failure_patch["execution_context"] = execution.execution_context
            return failure_patch

        scored_results = self.credibility_scorer.score_search_results(search_results)
        filtered_scored = [
            item
            for item in scored_results
            if item["credibility"]["score"] >= config.min_credibility_score
        ]
        credibility_scores = [item["credibility"] for item in filtered_scored]
        sorted_results = [item["result"] for item in filtered_scored]
        documents = scored_search_results_to_documents(
            (item["result"], item["credibility"])
            for item in filtered_scored
        )

        for query in plan.search_queries:
            query.completed = True

        call_detail = {
            "agent": "ResearchSearcher",
            "operation": "deterministic_search",
            "model": config.model_name,
            "input_tokens": 0,
            "output_tokens": 0,
            "duration": execution.stats.elapsed_seconds,
            "results_count": len(sorted_results),
            "original_results_count": len(search_results),
            "search_calls": execution.stats.search_calls,
            "extract_calls": execution.stats.extract_calls,
            "partial": execution.partial,
            "memory_hint_count": len(memory_hints),
            "memory_hint_source_urls": [
                url
                for item in retrieved_memory
                for url in (item.metadata.get("source_refs") or [])
            ][: config.research_memory_search_hint_limit],
            # Preserve the runtime's per-attempt records for Agent Trace. This
            # is metadata only; legacy LLM totals remain unchanged.
            "tool_invocations": execution.stats.invocation_records,
        }

        logger.info(
            "Deterministic Search Executor 收集了 "
            f"{len(search_results)} 个结果，过滤后剩余 {len(sorted_results)} 个"
        )

        result_patch = {
            "search_results": sorted_results,
            "credibility_scores": credibility_scores,
            "documents": documents,
            "retrieved_memory": retrieved_memory,
            "memory_ids": [item.memory_id for item in retrieved_memory],
            "search_diagnostics": state.search_diagnostics + [adaptive_diagnostic],
            "error": None,
            "current_stage": "synthesizing",
            "iterations": state.iterations + 1,
            "iteration": state.iteration + 1,
            "llm_calls": state.llm_calls,
            "total_input_tokens": state.total_input_tokens,
            "total_output_tokens": state.total_output_tokens,
            "llm_call_details": state.llm_call_details + [call_detail],
            "usage": _usage_from_legacy_totals(
                state,
                llm_calls=state.llm_calls,
                total_input_tokens=state.total_input_tokens,
                total_output_tokens=state.total_output_tokens,
            ),
        }
        if config.research_memory_enabled:
            result_patch["memory_diagnostics"] = memory_diagnostics
        # Agents forward but never interpret or synthesize runtime control
        # state. Terminal classification remains at the runner boundary.
        if latest_execution_context is not None:
            result_patch["execution_context"] = latest_execution_context
        return result_patch
    
    def _extract_results_from_messages(self, messages: list) -> List[SearchResult]:
        """从代理消息中提取搜索结果。"""
        search_results = []
        
        for msg in messages:
            if hasattr(msg, 'name') and msg.name == 'web_search':
                try:
                    content = msg.content
                    if isinstance(content, str):
                        tool_results = json.loads(content)
                    else:
                        tool_results = content
                    
                    if isinstance(tool_results, list):
                        for item in tool_results:
                            if isinstance(item, dict):
                                search_results.append(SearchResult(
                                    query=item.get('query', ''),
                                    title=item.get('title', ''),
                                    url=item.get('url', ''),
                                    snippet=item.get('snippet', ''),
                                    content=None
                                ))
                except Exception as e:
                    logger.warning(f"解析工具结果出错：{e}")
            
            if hasattr(msg, 'name') and msg.name == 'extract_webpage_content':
                try:
                    content = msg.content
                    if search_results and content:
                        for sr in reversed(search_results):
                            if not sr.content:
                                sr.content = content
                                break
                except Exception as e:
                    logger.warning(f"更新内容出错：{e}")
        
        return search_results


# =============================================================================
# 研究综合代理
# =============================================================================

class ResearchSynthesizer:
    """负责综合研究发现的自主代理。"""
    
    def __init__(
        self,
        llm: Optional[BaseChatModel] = None,
        max_retries: int = 3,
        evidence_config: Optional[EvidenceRuntimeConfig] = None,
        sidecar_factory: Optional[Callable[[], EvidenceSidecarService]] = None,
    ):
        self.llm = llm or get_llm(temperature=0.3, model_override=config.summarization_model)
        self.tools = get_research_tools(agent_type="synthesis")
        self.max_retries = max_retries
        self.evidence_config = evidence_config or EvidenceRuntimeConfig.from_environment()
        self.sidecar_factory = sidecar_factory or (
            lambda: EvidenceSidecarService.create_production(config=self.evidence_config)
        )
        
    async def synthesize(self, state: ResearchState) -> Dict[str, Any]:
        """使用工具和推理自主综合关键发现。
        
        返回将由 LangGraph 合并到状态中的关键发现字典。
        """
        topic = _research_query(state)
        logger.info(f"正在从 {len(state.search_results)} 个结果中综合研究发现")
        
        if not state.search_results:
            await emit_error("没有可供综合的搜索结果")
            return {"error": "没有可供综合的搜索结果", **failed_lifecycle_patch()}
        
        await emit_synthesis_start(len(state.search_results))
        
        agent_graph = create_agent(
            self.llm,
            self.tools,
            system_prompt=SYNTHESIZER_SYSTEM_PROMPT
        )
        
        results_to_use = state.search_results[:20]
        credibility_scores_to_use = state.credibility_scores[:20] if state.credibility_scores else []
        results_text = self._format_results_text(results_to_use, credibility_scores_to_use)
        input_message = SYNTHESIZER_USER_TEMPLATE.format(topic=topic, results=results_text)

        def output_text(result: dict[str, Any]) -> str:
            messages = result.get("messages", [])
            if not messages:
                return ""
            last_msg = messages[-1]
            return str(last_msg.content if hasattr(last_msg, "content") else last_msg)

        try:
            execution = await execute_llm_operation(
                lambda: agent_graph.ainvoke({"messages": [{"role": "user", "content": input_message}]}),
                agent="ResearchSynthesizer",
                operation_name="autonomous_synthesis",
                model=config.summarization_model,
                input_text=input_message,
                local_timeout_seconds=LLM_OPERATION_TIMEOUT_SECONDS,
                max_retries=_legacy_attempt_limit_to_retries(self.max_retries),
                context=state.execution_context,
                output_text=output_text,
            )
        except Exception as error:
            logger.error(f"综合调用失败：{error}")
            await emit_error(f"综合失败：{error}")
            return _llm_failure_patch(state, error, f"综合失败：{error}")

        text = output_text(execution.value)
        key_findings = self._extract_findings(text, state.search_results)
        findings = key_findings_to_findings(key_findings)
        calls, input_tokens, output_tokens = _llm_patch_totals(state, execution.call_details)
        logger.info(f"已提取 {len(key_findings)} 条关键发现")
        await emit_synthesis_complete(len(key_findings))
        success_patch: Dict[str, Any] = {
            "key_findings": key_findings,
            "findings": findings,
            "current_stage": "reporting",
            "iterations": state.iterations + 1,
            "iteration": state.iteration + 1,
            "llm_calls": state.llm_calls + calls,
            "total_input_tokens": state.total_input_tokens + input_tokens,
            "total_output_tokens": state.total_output_tokens + output_tokens,
            "llm_call_details": state.llm_call_details + execution.call_details,
            "usage": _usage_from_legacy_totals(
                state,
                llm_calls=state.llm_calls + calls,
                total_input_tokens=state.total_input_tokens + input_tokens,
                total_output_tokens=state.total_output_tokens + output_tokens,
            ),
        }
        if execution.context is not None:
            success_patch["execution_context"] = execution.context
        return await self._merge_evidence_sidecar(state, success_patch)

    async def _merge_evidence_sidecar(
        self,
        state: ResearchState,
        success_patch: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Attach optional Evidence output without affecting legacy synthesis success."""
        plan = _research_plan(state)
        if not self.evidence_config.enabled:
            success_patch["evidence_diagnostics"] = EvidenceDiagnostics(
                status="disabled",
                source=self._evidence_source(state),
            )
            return success_patch

        if not success_patch["key_findings"]:
            success_patch["evidence_diagnostics"] = EvidenceDiagnostics(
                status="not_run",
                source=self._evidence_source(state),
            )
            return success_patch

        try:
            sidecar = self.sidecar_factory()
            sidecar_args = dict(
                topic=_research_query(state),
                documents=state.documents,
                search_results=state.search_results,
                objectives=plan.objectives if plan else (),
            )
            execution_context = success_patch.get("execution_context") or state.execution_context
            if execution_context is not None:
                sidecar_args["execution_context"] = execution_context
            sidecar_result = await sidecar.run(**sidecar_args)
        except Exception as exc:
            success_patch["evidence_diagnostics"] = EvidenceDiagnostics(
                status="failed",
                source=self._evidence_source(state),
                sidecar_errors=[f"Evidence sidecar integration failed: {exc}"],
            )
            return success_patch

        success_patch["document_analyses"] = sidecar_result.document_analyses
        success_patch["evidence"] = sidecar_result.evidence
        success_patch["evidence_diagnostics"] = sidecar_result.diagnostics
        if sidecar_result.execution_context is not None:
            success_patch["execution_context"] = sidecar_result.execution_context
        if sidecar_result.diagnostics.source == "legacy_search_results_backfill":
            success_patch["documents"] = sidecar_result.documents
        if self._can_adopt_evidence_findings(sidecar_result):
            success_patch["findings"] = sidecar_result.findings

        success_patch["llm_calls"] += sidecar_result.llm_calls_delta
        success_patch["total_input_tokens"] += sidecar_result.input_tokens_delta
        success_patch["total_output_tokens"] += sidecar_result.output_tokens_delta
        success_patch["llm_call_details"] = (
            success_patch["llm_call_details"] + sidecar_result.llm_call_details
        )
        success_patch["usage"] = _usage_from_legacy_totals(
            state,
            llm_calls=success_patch["llm_calls"],
            total_input_tokens=success_patch["total_input_tokens"],
            total_output_tokens=success_patch["total_output_tokens"],
        )
        return success_patch

    def _can_adopt_evidence_findings(self, result: EvidenceSidecarResult) -> bool:
        """Accept only fully reference-valid Findings allowed by sidecar diagnostics."""
        diagnostics = result.diagnostics
        partial_allowed = self.evidence_config.analyzer.allow_partial_results
        diagnostics_allow_adoption = (
            diagnostics.aggregation_attempted
            and (diagnostics.aggregation_completed or (diagnostics.aggregation_partial and partial_allowed))
            and (
                diagnostics.analyzer_completed
                or (diagnostics.analyzer_partial and partial_allowed)
            )
        )
        if not diagnostics_allow_adoption or not result.findings:
            return False

        evidence_ids = {item.evidence_id for item in result.evidence}
        for finding in result.findings:
            references = set(finding.evidence_refs) | set(finding.contradictory_evidence_refs)
            if not references or not references.issubset(evidence_ids):
                return False
        return True

    @staticmethod
    def _evidence_source(state: ResearchState) -> str:
        if state.documents:
            return "p2_documents"
        if state.search_results:
            return "legacy_search_results_backfill"
        return "none"
    
    def _format_results_text(self, results: list, credibility_scores: list) -> str:
        """格式化带可信度信息的搜索结果。"""
        if len(results) != len(credibility_scores):
            return "\n\n".join([
                f"[{i+1}] {r.title}\nURL：{r.url}\n摘要：{r.snippet}\n" +
                (f"内容：{r.content[:300]}..." if r.content else "")
                for i, r in enumerate(results)
            ])
        
        return "\n\n".join([
            f"[{i+1}] {r.title}\n"
            f"URL：{r.url}\n"
            f"可信度：{cred.get('level', 'unknown').upper()}（分数：{cred.get('score', 'N/A')}/100）- {', '.join(cred.get('factors', []))}\n"
            f"摘要：{r.snippet}\n" +
            (f"内容：{r.content[:300]}..." if r.content else "")
            for i, (r, cred) in enumerate(zip(results, credibility_scores))
        ])
    
    def _extract_findings(self, output_text: str, search_results: list) -> List[str]:
        """从综合输出中提取关键发现。"""
        json_match = re.search(r'\[(.*?)\]', output_text, re.DOTALL)
        
        key_findings = []
        if json_match:
            try:
                findings = json.loads(json_match.group(0))
                if isinstance(findings, list):
                    key_findings = [str(f) for f in findings]
                else:
                    key_findings = [str(findings)]
            except json.JSONDecodeError:
                pass
        
        if not key_findings:
            lines = output_text.split('\n')
            for line in lines:
                line = line.strip().lstrip('-').lstrip('*').lstrip('>').strip()
                line = re.sub(r'^\d+\.\s*', '', line)
                if len(line) > 30 and not line.startswith('[') and not line.startswith(']'):
                    key_findings.append(line)
            key_findings = key_findings[:15]
        
        if not key_findings and search_results:
            logger.warning("代理没有生成发现，将根据结果创建基础发现")
            key_findings = [
                f"{r.title}: {r.snippet[:100]}..."
                for r in search_results[:10]
                if r.snippet
            ]
        
        return key_findings


# =============================================================================
# 报告撰写代理
# =============================================================================

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
