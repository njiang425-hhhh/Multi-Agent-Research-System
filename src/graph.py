"""Planner → Searcher → Synthesizer → Writer LangGraph topology only."""

import warnings
from typing import Any, Dict

from langgraph.graph import StateGraph, START, END
from src.state import ResearchState
from src.state_compat import (
    canonical_documents,
    canonical_findings,
    canonical_plan,
    canonical_report_text,
    hydrate_canonical_state,
    legacy_projection_patch,
)
from src.agents import ResearchPlanner, ResearchSearcher, ResearchSynthesizer, ReportWriter
from src.agent_trace import trace_node_execution
from src.config import config
import logging

logger = logging.getLogger(__name__)


_RUNNER_COMPAT_EXPORTS = frozenset(
    {
        "run_research",
        "run_research_with_persistence",
        "resume_research",
        "cancel_research",
        "get_workflow_state",
        "list_research_threads",
        "get_checkpoint_path",
        "create_memory_checkpointer",
        "create_sqlite_checkpointer",
    }
)


def __getattr__(name: str) -> Any:
    """Provide a temporary import-path compatibility facade for Runner APIs."""

    if name not in _RUNNER_COMPAT_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    warnings.warn(
        f"src.graph.{name} moved to src.runner.{name}; import it from src.runner instead",
        DeprecationWarning,
        stacklevel=2,
    )
    from src import runner

    return getattr(runner, name)


def create_research_graph(checkpointer=None):
    """Create the four-node research workflow.
    
    Args:
        checkpointer: An already-configured checkpointer supplied by Runner.
        
    Returns:
        编译后的 LangGraph 流程
    """
    
    planner = ResearchPlanner()
    searcher = ResearchSearcher()
    synthesizer = ResearchSynthesizer()
    writer = ReportWriter(citation_style=config.citation_style)
    
    workflow = StateGraph(ResearchState)
    
    # Agents return canonical business patches. This boundary explicitly adds
    # legacy projections for historical checkpoints, UI, and callers.
    async def plan_node(state: ResearchState) -> Dict[str, Any]:
        canonical_state = hydrate_canonical_state(state)
        patch = await trace_node_execution(canonical_state, node="plan", agent="ResearchPlanner", operation="plan", execute=planner.plan)
        return legacy_projection_patch(canonical_state, patch)

    async def search_node(state: ResearchState) -> Dict[str, Any]:
        canonical_state = hydrate_canonical_state(state)
        patch = await trace_node_execution(canonical_state, node="search", agent="ResearchSearcher", operation="search", execute=searcher.search)
        return legacy_projection_patch(canonical_state, patch)

    async def synthesize_node(state: ResearchState) -> Dict[str, Any]:
        canonical_state = hydrate_canonical_state(state)
        patch = await trace_node_execution(canonical_state, node="synthesize", agent="ResearchSynthesizer", operation="synthesize", execute=synthesizer.synthesize)
        return legacy_projection_patch(canonical_state, patch)

    async def writer_node(state: ResearchState) -> Dict[str, Any]:
        canonical_state = hydrate_canonical_state(state)
        patch = await trace_node_execution(canonical_state, node="write_report", agent="ReportWriter", operation="write_report", execute=writer.write_report)
        return legacy_projection_patch(canonical_state, patch)

    workflow.add_node("plan", plan_node)
    workflow.add_node("search", search_node)
    workflow.add_node("synthesize", synthesize_node)
    workflow.add_node("write_report", writer_node)
    
    workflow.add_edge(START, "plan")
    
    def should_continue_after_plan(state: ResearchState) -> str:
        """验证规划输出并进行适当路由。"""
        if state.error:
            logger.error(f"规划失败：{state.error}")
            return END
        
        plan = canonical_plan(state)
        if not plan or not plan.search_queries:
            logger.error("计划中未生成搜索查询")
            return END
            
        logger.info(f"计划验证通过：{len(plan.search_queries)} 个查询")
        return "search"
    
    def should_continue_after_search(state: ResearchState) -> str:
        """验证搜索结果并进行适当路由。"""
        if state.error:
            logger.error(f"搜索失败：{state.error}")
            return END
        
        documents = canonical_documents(state)
        if not documents:
            logger.warning("未找到搜索结果")
            return END
        
        if len(documents) < 2:
            logger.warning(f"搜索结果不足：{len(documents)}")
            return END
            
        logger.info(f"搜索验证通过：{len(documents)} 个 canonical Documents")
        return "synthesize"
    
    def should_continue_after_synthesize(state: ResearchState) -> str:
        """验证综合输出并进行适当路由。"""
        if state.error:
            logger.error(f"综合失败：{state.error}")
            return END
        
        documents = canonical_documents(state)
        document_ids = {document.document_id for document in documents}
        findings = [
            finding
            for finding in canonical_findings(state)
            if any(source_id in document_ids for source_id in finding.source_document_ids)
        ]
        if not findings:
            logger.warning("未提取到关键发现")
            return END
        
        logger.info(f"综合验证通过：{len(findings)} 条 source-linked 发现")
        return "write_report"
    
    def should_continue_after_report(state: ResearchState) -> str:
        """验证最终报告并完成流程。"""
        if state.error:
            logger.error(f"报告生成失败：{state.error}")
        elif not canonical_report_text(state):
            logger.error("未生成报告")
        else:
            logger.info("报告生成完成")
            
        return END
    
    workflow.add_conditional_edges(
        "plan",
        should_continue_after_plan,
        {"search": "search", END: END}
    )
    
    workflow.add_conditional_edges(
        "search",
        should_continue_after_search,
        {"synthesize": "synthesize", END: END}
    )
    
    workflow.add_conditional_edges(
        "synthesize",
        should_continue_after_synthesize,
        {"write_report": "write_report", END: END}
    )
    
    workflow.add_conditional_edges(
        "write_report",
        should_continue_after_report,
        {END: END}
    )
    
    return workflow.compile(checkpointer=checkpointer)
