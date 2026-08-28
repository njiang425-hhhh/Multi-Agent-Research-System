"""Planner node for the canonical research flow."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate

from src.agents._llm_support import (
    LLM_OPERATION_TIMEOUT_SECONDS,
    _legacy_attempt_limit_to_retries,
    _llm_failure_patch,
    _llm_patch_totals,
    _usage_from_legacy_totals,
)
from src.callbacks import emit_error, emit_planning_complete, emit_planning_start
from src.config import config
from src.exceptions import PlanningError
from src.llm.factory import get_llm
from src.llm_execution import execute_llm_operation
from src.memory import ResearchMemoryStore, format_planner_memory_context
from src.memory.retrieval import (
    default_memory_store as _default_memory_store,
    retrieve_memory_items_with_diagnostics as _retrieve_memory_items_with_diagnostics,
)
from src.planning import normalize_research_plan
from src.prompts import PLANNER_SYSTEM_PROMPT, PLANNER_USER_TEMPLATE
from src.state import ResearchState
from src.state_compat import canonical_iteration, canonical_query as _research_query, canonical_usage


logger = logging.getLogger(__name__)


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
            "iterations": canonical_iteration(state) + 1,
            "iteration": canonical_iteration(state) + 1,
            "llm_calls": canonical_usage(state).llm_calls + calls,
            "total_input_tokens": canonical_usage(state).input_tokens + input_tokens,
            "total_output_tokens": canonical_usage(state).output_tokens + output_tokens,
            "llm_call_details": state.llm_call_details + execution.call_details,
            "usage": _usage_from_legacy_totals(
                state,
                llm_calls=canonical_usage(state).llm_calls + calls,
                total_input_tokens=canonical_usage(state).input_tokens + input_tokens,
                total_output_tokens=canonical_usage(state).output_tokens + output_tokens,
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



__all__ = ["ResearchPlanner"]
