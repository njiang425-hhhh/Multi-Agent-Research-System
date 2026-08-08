"""带检查点和增强路由的 LangGraph 深度研究流程。

节点返回的字典更新会由 LangGraph 自动合并到状态中。
这是 LangGraph 文档推荐的模式。
"""

import os
import uuid
import sqlite3
from typing import Optional, Dict, Any
from pathlib import Path
from contextlib import contextmanager

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver

from src.state import ResearchState
from src.agents import ResearchPlanner, ResearchSearcher, ResearchSynthesizer, ReportWriter
from src.utils.cache import ResearchCache
from src.config import config
from src.exceptions import DeepResearchError
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# =============================================================================
# 检查点管理
# =============================================================================

def get_checkpoint_path() -> Path:
    """获取 SQLite 检查点存储路径。"""
    cache_dir = Path(".cache/checkpoints")
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "research_checkpoints.db"


def create_memory_checkpointer() -> MemorySaver:
    """创建内存检查点管理器（重启后不会保留）。"""
    return MemorySaver()


@contextmanager
def create_sqlite_checkpointer():
    """创建用于流程持久化的 SQLite 检查点上下文管理器。
    
    Usage:
        with create_sqlite_checkpointer() as checkpointer:
            graph = create_research_graph(checkpointer=checkpointer)
            result = await graph.ainvoke(...)
    """
    checkpoint_path = get_checkpoint_path()
    with SqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        logger.info(f"SQLite 检查点管理器已初始化：{checkpoint_path}")
        yield checkpointer


# =============================================================================
# 图构建
# =============================================================================

def create_research_graph(checkpointer=None):
    """创建带增强路由和错误处理的研究流程图。
    
    Args:
        checkpointer: 可选的持久化检查点管理器（MemorySaver 或 SqliteSaver）
        
    Returns:
        编译后的 LangGraph 流程
    """
    
    planner = ResearchPlanner()
    searcher = ResearchSearcher()
    synthesizer = ResearchSynthesizer()
    writer = ReportWriter(citation_style=config.citation_style)
    
    workflow = StateGraph(ResearchState)
    
    workflow.add_node("plan", planner.plan)
    workflow.add_node("search", searcher.search)
    workflow.add_node("synthesize", synthesizer.synthesize)
    workflow.add_node("write_report", writer.write_report)
    
    workflow.add_edge(START, "plan")
    
    def should_continue_after_plan(state: ResearchState) -> str:
        """验证规划输出并进行适当路由。"""
        if state.error:
            logger.error(f"规划失败：{state.error}")
            return END
        
        if not state.plan or not state.plan.search_queries:
            logger.error("计划中未生成搜索查询")
            return END
            
        logger.info(f"计划验证通过：{len(state.plan.search_queries)} 个查询")
        return "search"
    
    def should_continue_after_search(state: ResearchState) -> str:
        """验证搜索结果并进行适当路由。"""
        if state.error:
            logger.error(f"搜索失败：{state.error}")
            return END
        
        if not state.search_results:
            logger.warning("未找到搜索结果")
            return END
        
        if len(state.search_results) < 2:
            logger.warning(f"搜索结果不足：{len(state.search_results)}")
            return END
            
        logger.info(f"搜索验证通过：{len(state.search_results)} 个结果")
        return "synthesize"
    
    def should_continue_after_synthesize(state: ResearchState) -> str:
        """验证综合输出并进行适当路由。"""
        if state.error:
            logger.error(f"综合失败：{state.error}")
            return END
        
        if not state.key_findings:
            logger.warning("未提取到关键发现")
            return END
        
        logger.info(f"综合验证通过：{len(state.key_findings)} 条发现")
        return "write_report"
    
    def should_continue_after_report(state: ResearchState) -> str:
        """验证最终报告并完成流程。"""
        if state.error:
            logger.error(f"报告生成失败：{state.error}")
        elif not state.final_report:
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


# =============================================================================
# 研究执行
# =============================================================================

async def run_research(
    topic: str, 
    verbose: bool = True, 
    use_cache: bool = True,
    use_checkpoints: bool = True,
    thread_id: Optional[str] = None
) -> Dict[str, Any]:
    """运行指定主题的研究流程。
    
    Args:
        topic: 研究主题
        verbose: 是否启用详细日志
        use_cache: 如有缓存是否使用缓存结果
        use_checkpoints: 是否启用检查点持久化以便崩溃恢复
        thread_id: 可选的检查点跟踪线程 ID（未提供时自动生成）
    
    Returns:
        完整的累积状态字典
    """
    logger.info(f"开始研究：{topic}")
    
    cache = ResearchCache()
    if use_cache:
        cached_result = cache.get(topic)
        if cached_result:
            logger.info("使用缓存的研究结果")
            return cached_result
    
    initial_state = ResearchState(research_topic=topic)
    
    run_config: Dict[str, Any] = {}
    
    if use_checkpoints:
        checkpointer = create_memory_checkpointer()
        tid = thread_id or f"research-{uuid.uuid4().hex[:8]}"
        run_config["configurable"] = {"thread_id": tid}
        logger.info(f"使用 thread_id 进行检查点跟踪：{tid}")
    else:
        checkpointer = None
    
    graph = create_research_graph(checkpointer=checkpointer)
    
    try:
        final_state = await graph.ainvoke(initial_state, config=run_config if run_config else None)
    except Exception as e:
        logger.error(f"研究流程失败：{e}")
        if run_config.get("configurable", {}).get("thread_id"):
            logger.info(f"线程 ID：{run_config['configurable']['thread_id']}")
        raise
    
    if use_cache and not final_state.get("error"):
        cache.set(topic, final_state)
    
    if verbose:
        logger.info("流程已完成")
        if final_state.get("final_report"):
            logger.info(f"报告已生成：{len(final_state['final_report'])} 个字符")
    
    return final_state


async def run_research_with_persistence(
    topic: str, 
    verbose: bool = True, 
    use_cache: bool = True,
    thread_id: Optional[str] = None
) -> Dict[str, Any]:
    """运行带 SQLite 持久化的研究流程，以便崩溃恢复。
    
    此版本会将检查点持久化到磁盘，支持重启后继续运行。
    
    Args:
        topic: 研究主题
        verbose: 是否启用详细日志
        use_cache: 如有缓存是否使用缓存结果
        thread_id: 可选的检查点跟踪线程 ID（未提供时自动生成）
    
    Returns:
        完整的累积状态字典
    """
    logger.info(f"开始研究：{topic}")
    
    cache = ResearchCache()
    if use_cache:
        cached_result = cache.get(topic)
        if cached_result:
            logger.info("使用缓存的研究结果")
            return cached_result
    
    initial_state = ResearchState(research_topic=topic)
    
    tid = thread_id or f"research-{uuid.uuid4().hex[:8]}"
    run_config = {"configurable": {"thread_id": tid}}
    logger.info(f"使用 thread_id 进行持久化检查点跟踪：{tid}")
    
    with create_sqlite_checkpointer() as checkpointer:
        graph = create_research_graph(checkpointer=checkpointer)
        
        try:
            final_state = await graph.ainvoke(initial_state, config=run_config)
        except Exception as e:
            logger.error(f"研究流程失败：{e}")
            logger.info(f"流程状态已保存到磁盘。可使用 thread_id 恢复：{tid}")
            raise
    
    if use_cache and not final_state.get("error"):
        cache.set(topic, final_state)
    
    if verbose:
        logger.info("流程已完成")
        if final_state.get("final_report"):
            logger.info(f"报告已生成：{len(final_state['final_report'])} 个字符")
    
    return final_state


async def resume_research(
    thread_id: str,
    additional_input: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """从 SQLite 检查点恢复之前中断的研究流程。
    
    Args:
        thread_id: 中断流程的线程 ID
        additional_input: 可选的附加输入
        
    Returns:
        完整的累积状态字典
    """
    logger.info(f"使用 thread_id 恢复研究：{thread_id}")
    
    run_config = {"configurable": {"thread_id": thread_id}}
    
    with create_sqlite_checkpointer() as checkpointer:
        graph = create_research_graph(checkpointer=checkpointer)
        
        state = await graph.aget_state(run_config)
        if not state or not state.values:
            raise DeepResearchError(f"未找到 thread_id 对应的检查点：{thread_id}")
        
        logger.info(f"找到检查点，阶段：{state.values.get('current_stage', 'unknown')}")
        
        input_state = additional_input if additional_input else None
        final_state = await graph.ainvoke(input_state, config=run_config)
    
    return final_state


async def get_workflow_state(thread_id: str) -> Optional[Dict[str, Any]]:
    """根据线程 ID 获取流程当前状态。
    
    Args:
        thread_id: 要查询的线程 ID
        
    Returns:
        当前状态字典；找不到时返回 None
    """
    run_config = {"configurable": {"thread_id": thread_id}}
    
    with create_sqlite_checkpointer() as checkpointer:
        graph = create_research_graph(checkpointer=checkpointer)
        
        state = await graph.aget_state(run_config)
        if state and state.values:
            return dict(state.values)
    
    return None


def list_research_threads() -> list:
    """列出检查点中的所有可用研究线程 ID。"""
    checkpoint_path = get_checkpoint_path()
    if not checkpoint_path.exists():
        return []
    
    try:
        conn = sqlite3.connect(str(checkpoint_path))
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT thread_id FROM checkpoints ORDER BY thread_ts DESC")
        threads = [row[0] for row in cursor.fetchall()]
        conn.close()
        return threads
    except Exception as e:
        logger.warning(f"列出线程失败：{e}")
        return []
