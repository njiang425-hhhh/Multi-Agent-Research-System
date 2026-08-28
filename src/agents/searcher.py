"""Searcher node with deterministic search as the default execution path."""

from __future__ import annotations

import asyncio
from copy import copy
import json
import logging
import time
from typing import Any, Dict, List, Optional

from langchain_core.language_models import BaseChatModel

from src.agents._llm_support import _usage_from_legacy_totals
from src.callbacks import (
    emit_error,
    emit_extraction_complete,
    emit_search_results,
    emit_search_start,
)
from src.config import config
from src.evidence.adapters import normalize_web_url, scored_search_results_to_documents
from src.exceptions import SearchError
from src.llm.factory import get_llm
from src.llm_tracker import estimate_tokens
from src.memory import ResearchMemoryStore, build_search_memory_hints
from src.memory.retrieval import (
    default_memory_store as _default_memory_store,
    retrieve_memory_items_with_diagnostics as _retrieve_memory_items_with_diagnostics,
)
from src.prompts import SEARCHER_SYSTEM_PROMPT, SEARCHER_USER_TEMPLATE
from src.runtime_lifecycle import failed_lifecycle_patch
from src.search.config import SearchConfig
from src.search.coverage import SearchCoverage, measure_search_coverage
from src.search.executor import SearchExecutor
from src.state import ResearchState, SearchResult
from src.state_compat import (
    canonical_iteration,
    canonical_plan as _research_plan,
    canonical_query as _research_query,
    canonical_usage,
)
from src.utils.credibility import CredibilityScorer
from src.utils.tools import get_research_tools


logger = logging.getLogger(__name__)

SEARCHER_AGENT_RECURSION_LIMIT = 12
SEARCHER_AGENT_TIMEOUT_SECONDS = 90
SEARCHER_ADAPTIVE_MAX_ROUNDS = 1
SEARCHER_ADAPTIVE_TIMEOUT_SECONDS = 30.0
SEARCHER_ADAPTIVE_MAX_SEARCH_CALLS = 1
SEARCHER_ADAPTIVE_MAX_EXTRACT_CALLS = 1


def _create_agent(*args, **kwargs):
    """Resolve the public injection seam at call time for existing tests."""

    from src import agents

    return agents.create_agent(*args, **kwargs)


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

        agent_graph = _create_agent(
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
                    "iterations": canonical_iteration(state) + 1,
                    "iteration": canonical_iteration(state) + 1,
                    "llm_calls": canonical_usage(state).llm_calls + 1,
                    "total_input_tokens": canonical_usage(state).input_tokens + input_tokens,
                    "total_output_tokens": canonical_usage(state).output_tokens + output_tokens,
                    "llm_call_details": state.llm_call_details + [call_detail],
                    "usage": _usage_from_legacy_totals(
                        state,
                        llm_calls=canonical_usage(state).llm_calls + 1,
                        total_input_tokens=canonical_usage(state).input_tokens + input_tokens,
                        total_output_tokens=canonical_usage(state).output_tokens + output_tokens,
                    ),
                }

            except SearchError as e:
                logger.warning(f"第 {attempt + 1} 次搜索尝试失败：{e}")
                if attempt == self.max_retries - 1:
                    logger.error(f"经过 {self.max_retries} 次尝试后搜索仍失败")
                    await emit_error(f"搜索失败：{e}")
                    return {
                        "error": f"搜索失败：{e}",
                        "iterations": canonical_iteration(state) + 1,
                        "iteration": canonical_iteration(state) + 1,
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
                        "iterations": canonical_iteration(state) + 1,
                        "iteration": canonical_iteration(state) + 1,
                        **failed_lifecycle_patch(),
                    }
                else:
                    await asyncio.sleep(2 ** attempt)

        return {
            "error": "搜索失败：已超过最大重试次数",
            "iterations": canonical_iteration(state) + 1,
            "iteration": canonical_iteration(state) + 1,
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
                "iterations": canonical_iteration(state) + 1,
                "iteration": canonical_iteration(state) + 1,
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
            "iterations": canonical_iteration(state) + 1,
            "iteration": canonical_iteration(state) + 1,
            "llm_calls": canonical_usage(state).llm_calls,
            "total_input_tokens": canonical_usage(state).input_tokens,
            "total_output_tokens": canonical_usage(state).output_tokens,
            "llm_call_details": state.llm_call_details + [call_detail],
            "usage": _usage_from_legacy_totals(
                state,
                llm_calls=canonical_usage(state).llm_calls,
                total_input_tokens=canonical_usage(state).input_tokens,
                total_output_tokens=canonical_usage(state).output_tokens,
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



__all__ = ["ResearchSearcher", "SEARCHER_AGENT_RECURSION_LIMIT"]
