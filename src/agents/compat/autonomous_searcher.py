"""Deprecated autonomous Searcher compatibility path.

The supported Searcher is ``deterministic_v2`` in ``src.agents.searcher``.
This module preserves the historical tool-agent loop only for callers that
explicitly select ``SearchConfig(mode=\"legacy_agent\")``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from src.agents._llm_support import _usage_from_totals
from src.callbacks import emit_error, emit_extraction_complete, emit_search_start
from src.config import config
from src.evidence.adapters import scored_search_results_to_documents
from src.exceptions import SearchError
from src.llm_tracker import estimate_tokens
from src.prompts import SEARCHER_SYSTEM_PROMPT, SEARCHER_USER_TEMPLATE
from src.runtime_lifecycle import failed_lifecycle_patch
from src.state import ResearchState, SearchResult
from src.state_compat import canonical_iteration, canonical_plan, canonical_query, canonical_usage


logger = logging.getLogger(__name__)

SEARCHER_AGENT_RECURSION_LIMIT = 12
SEARCHER_AGENT_TIMEOUT_SECONDS = 90


def _create_agent(*args: Any, **kwargs: Any) -> Any:
    """Resolve the historical public injection seam only when this mode runs."""

    from src import agents

    return agents.create_agent(*args, **kwargs)


async def run_legacy_autonomous_search(searcher: Any, state: ResearchState) -> dict[str, Any]:
    """Execute the former opaque tool-agent search loop without changing it."""

    plan = canonical_plan(state)
    if not plan:
        await emit_error("没有可用的研究计划")
        return {"error": "没有可用的研究计划", **failed_lifecycle_patch()}

    logger.info("Legacy autonomous Searcher 开始研究：已规划 %s 个查询", len(plan.search_queries))
    total_queries = len(plan.search_queries)
    for index, query in enumerate(plan.search_queries, 1):
        await emit_search_start(query.query, index, total_queries)

    max_searches = min(config.max_search_queries, 3)
    max_results_per_search = min(config.max_search_results_per_query, 3)
    expected_total_results = max_searches * max_results_per_search
    max_extractions = min(max_searches + 1, 4)
    target_sources = min(expected_total_results, max_extractions)
    system_prompt = SEARCHER_SYSTEM_PROMPT.format(
        max_searches=max_searches,
        max_results_per_search=max_results_per_search,
        max_extractions=max_extractions,
        expected_total_results=target_sources,
    )
    agent_graph = _create_agent(searcher.llm, searcher.tools, system_prompt=system_prompt)

    for attempt in range(searcher.max_retries):
        try:
            started_at = time.time()
            input_message = SEARCHER_USER_TEMPLATE.format(
                topic=canonical_query(state),
                objectives="\n".join(f"- {objective}" for objective in plan.objectives),
                queries="\n".join(
                    f"- {query.query} (Purpose: {query.purpose})" for query in plan.search_queries
                ),
                min_sources=target_sources,
                max_searches=max_searches,
                max_extractions=max_extractions,
            )
            input_tokens = estimate_tokens(input_message)
            try:
                result = await asyncio.wait_for(
                    agent_graph.ainvoke(
                        {"messages": [{"role": "user", "content": input_message}]},
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
                raise SearchError("搜索代理执行失败", details=str(exc)) from exc

            messages = result.get("messages", [])
            output_text = str(messages[-1].content if messages and hasattr(messages[-1], "content") else "")
            output_tokens = estimate_tokens(output_text)
            search_results = extract_results_from_messages(messages)
            logger.info("Legacy autonomous Searcher 收集了 %s 个结果", len(search_results))
            extracted_count = sum(1 for result in search_results if result.content)
            await emit_extraction_complete(
                extracted_count,
                sum(len(result.content) if result.content else 0 for result in search_results),
            )
            if not search_results:
                await emit_error("代理没有收集到任何搜索结果")
                raise SearchError("代理没有收集到任何搜索结果")

            filtered_scored = [
                item
                for item in searcher.credibility_scorer.score_search_results(search_results)
                if item["credibility"]["score"] >= config.min_credibility_score
            ]
            sorted_results = [item["result"] for item in filtered_scored]
            documents = scored_search_results_to_documents(
                (item["result"], item["credibility"]) for item in filtered_scored
            )
            for query in plan.search_queries:
                query.completed = True
            call_detail = {
                "agent": "ResearchSearcher",
                "operation": "autonomous_search",
                "model": config.model_name,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "duration": round(time.time() - started_at, 2),
                "results_count": len(sorted_results),
                "original_results_count": len(search_results),
                "min_credibility_score": config.min_credibility_score,
                "attempt": attempt + 1,
            }
            return {
                "documents": documents,
                "current_stage": "synthesizing",
                "iteration": canonical_iteration(state) + 1,
                "llm_call_details": state.llm_call_details + [call_detail],
                "usage": _usage_from_totals(
                    state,
                    llm_calls=canonical_usage(state).llm_calls + 1,
                    input_tokens=canonical_usage(state).input_tokens + input_tokens,
                    output_tokens=canonical_usage(state).output_tokens + output_tokens,
                ),
            }
        except SearchError as error:
            logger.warning("Legacy autonomous Searcher 第 %s 次失败：%s", attempt + 1, error)
            if attempt == searcher.max_retries - 1:
                await emit_error(f"搜索失败：{error}")
                return {
                    "error": f"搜索失败：{error}",
                    "iteration": canonical_iteration(state) + 1,
                    **failed_lifecycle_patch(),
                }
        except Exception as error:
            search_error = SearchError("搜索阶段执行失败", details=str(error))
            logger.warning("Legacy autonomous Searcher 第 %s 次失败：%s", attempt + 1, search_error)
            if attempt == searcher.max_retries - 1:
                await emit_error(f"搜索失败：{search_error}")
                return {
                    "error": f"搜索失败：{search_error}",
                    "iteration": canonical_iteration(state) + 1,
                    **failed_lifecycle_patch(),
                }
        await asyncio.sleep(2**attempt)

    return {
        "error": "搜索失败：已超过最大重试次数",
        "iteration": canonical_iteration(state) + 1,
        **failed_lifecycle_patch(),
    }


def extract_results_from_messages(messages: list[Any]) -> list[SearchResult]:
    """Preserve the historical message parser for legacy tool-agent output."""

    search_results: list[SearchResult] = []
    for message in messages:
        if getattr(message, "name", None) == "web_search":
            try:
                tool_results = json.loads(message.content) if isinstance(message.content, str) else message.content
                if isinstance(tool_results, list):
                    search_results.extend(
                        SearchResult(
                            query=item.get("query", ""),
                            title=item.get("title", ""),
                            url=item.get("url", ""),
                            snippet=item.get("snippet", ""),
                            content=None,
                        )
                        for item in tool_results
                        if isinstance(item, dict)
                    )
            except Exception as error:
                logger.warning("解析 legacy 工具结果出错：%s", error)
        if getattr(message, "name", None) == "extract_webpage_content":
            try:
                if search_results and message.content:
                    next(result for result in reversed(search_results) if not result.content).content = message.content
            except (StopIteration, AttributeError):
                pass
            except Exception as error:
                logger.warning("更新 legacy 内容出错：%s", error)
    return search_results


__all__ = [
    "SEARCHER_AGENT_RECURSION_LIMIT",
    "SEARCHER_AGENT_TIMEOUT_SECONDS",
    "extract_results_from_messages",
    "run_legacy_autonomous_search",
]
