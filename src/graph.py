"""带检查点和增强路由的 LangGraph 深度研究流程。

节点返回的字典更新会由 LangGraph 自动合并到状态中。
这是 LangGraph 文档推荐的模式。
"""

import os
import uuid
import sqlite3
import asyncio
from typing import Optional, Dict, Any
from pathlib import Path
from contextlib import asynccontextmanager

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from src.state import ResearchState
from src.state_compat import (
    canonical_documents,
    canonical_findings,
    canonical_plan,
    canonical_report_text,
    hydrate_canonical_state,
    legacy_projection_patch,
)
from src.runtime_lifecycle import (
    adopt_terminal_lifecycle_patch,
    apply_terminal_lifecycle,
    build_cache_replay_state,
    cancelled_lifecycle_patch,
    classify_terminal_lifecycle,
    create_new_run_state,
    execution_context_from_state,
    filter_runtime_owned_input,
    is_successful_cache_payload,
    resume_lifecycle_patch,
    start_run,
    timeout_lifecycle_patch,
    unhandled_exception_lifecycle_patch,
)
from src.runtime_control import ExecutionContext, RunPolicy, invoke_with_execution_context
from src.runtime_lease import PersistentRunLease
from src.agents import ResearchPlanner, ResearchSearcher, ResearchSynthesizer, ReportWriter
from src.agent_trace import trace_node_execution
from src.utils.cache import ResearchCache
from src.config import config
from src.exceptions import DeepResearchError
from src.memory import ResearchMemoryStore, project_research_memories
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


PERSISTENT_LEASE_TTL_SECONDS = 300.0


def _memory_store_from_config() -> ResearchMemoryStore:
    return ResearchMemoryStore(
        Path(config.research_memory_store_path),
        max_records=config.research_memory_max_records,
    )


def _persist_completed_research_memory(final_state: Dict[str, Any]) -> None:
    """Best-effort post-run memory write; never changes run outcome."""

    if not config.research_memory_enabled:
        return
    if final_state.get("status") != "completed" or final_state.get("terminal_reason") != "completed":
        return
    try:
        records = project_research_memories(
            final_state,
            ttl_days=config.research_memory_ttl_days,
        )
        if not records:
            return
        _memory_store_from_config().upsert_many(records)
    except Exception:
        logger.exception("Research memory persistence failed; run result is unchanged")


def _create_initial_state(
    topic: str,
    *,
    thread_id: Optional[str] = None,
    run_policy: Optional[RunPolicy] = None,
) -> ResearchState:
    """Create a fresh, pending run at the graph entry boundary."""
    return create_new_run_state(topic, thread_id=thread_id, run_policy=run_policy)


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


@asynccontextmanager
async def create_sqlite_checkpointer():
    """创建用于流程持久化的 SQLite 检查点上下文管理器。
    
    Usage:
        async with create_sqlite_checkpointer() as checkpointer:
            graph = create_research_graph(checkpointer=checkpointer)
            result = await graph.ainvoke(...)
    """
    checkpoint_path = get_checkpoint_path()
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        logger.info(f"SQLite 检查点管理器已初始化：{checkpoint_path}")
        yield checkpointer


# =============================================================================
# 图构建
# =============================================================================

def create_research_graph(checkpointer=None):
    """创建带增强路由和错误处理的研究流程图。
    
    Args:
        checkpointer: 可选的持久化检查点管理器（MemorySaver 或 AsyncSqliteSaver）
        
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


# =============================================================================
# 研究执行
# =============================================================================

async def _persist_lifecycle_patch(
    graph: Any,
    run_config: Optional[Dict[str, Any]],
    patch: Dict[str, str],
) -> None:
    """Best-effort checkpoint persistence for runtime-only lifecycle fields."""
    if not patch or not run_config:
        return

    try:
        await graph.aupdate_state(run_config, patch)
    except Exception:
        logger.exception("写入运行时生命周期检查点失败")


async def _invoke_with_terminal_lifecycle(
    graph: Any,
    initial_state: Optional[Any],
    run_config: Optional[Dict[str, Any]],
    *,
    has_checkpointer: bool,
    execution_context: Optional[ExecutionContext] = None,
) -> Dict[str, Any]:
    """Invoke a graph and apply terminal runtime lifecycle semantics."""
    try:
        final_state = await invoke_with_execution_context(
            lambda: graph.ainvoke(initial_state, config=run_config),
            execution_context,
        )
    except asyncio.CancelledError:
        if has_checkpointer:
            await _persist_lifecycle_patch(graph, run_config, cancelled_lifecycle_patch())
        raise
    except asyncio.TimeoutError:
        if has_checkpointer:
            await _persist_lifecycle_patch(graph, run_config, timeout_lifecycle_patch())
        raise
    except Exception as exc:
        if has_checkpointer:
            trace_events = getattr(exc, "_agent_trace_events", None)
            patch: Dict[str, Any] = unhandled_exception_lifecycle_patch()
            if trace_events is not None:
                patch["agent_trace"] = trace_events
            await _persist_lifecycle_patch(graph, run_config, patch)
        raise

    patch = classify_terminal_lifecycle(final_state)
    if has_checkpointer:
        await _persist_lifecycle_patch(graph, run_config, patch)
    return apply_terminal_lifecycle(final_state)

async def run_research(
    topic: str, 
    verbose: bool = True, 
    use_cache: bool = True,
    use_checkpoints: bool = True,
    thread_id: Optional[str] = None,
    run_policy: Optional[RunPolicy] = None,
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
            replay_state = build_cache_replay_state(cached_result, run_policy=run_policy)
            if replay_state:
                logger.info("使用缓存的研究结果进行 replay run")
                return replay_state
            logger.info("缓存结果不是可 replay 的完成报告，将执行 Graph")
    
    run_config: Dict[str, Any] = {}
    tid: Optional[str] = None
    if use_checkpoints:
        checkpointer = create_memory_checkpointer()
        tid = thread_id or f"research-{uuid.uuid4().hex[:8]}"
        run_config["configurable"] = {"thread_id": tid}
        logger.info(f"使用 thread_id 进行检查点跟踪：{tid}")
    else:
        checkpointer = None

    initial_state = start_run(
        _create_initial_state(topic, thread_id=tid, run_policy=run_policy)
    )
    
    graph = create_research_graph(checkpointer=checkpointer)
    
    try:
        final_state = await _invoke_with_terminal_lifecycle(
            graph,
            initial_state,
            run_config if run_config else None,
            has_checkpointer=use_checkpoints,
            execution_context=initial_state.execution_context,
        )
    except Exception as e:
        logger.error(f"研究流程失败：{e}")
        if run_config.get("configurable", {}).get("thread_id"):
            logger.info(f"线程 ID：{run_config['configurable']['thread_id']}")
        raise

    _persist_completed_research_memory(final_state)
    
    if use_cache and is_successful_cache_payload(final_state):
        cache.set(topic, final_state)
    
    if verbose:
        logger.info("流程已完成")
        if report_text := canonical_report_text(final_state):
            logger.info(f"报告已生成：{len(report_text)} 个字符")
    
    return final_state


async def run_research_with_persistence(
    topic: str, 
    verbose: bool = True, 
    use_cache: bool = True,
    thread_id: Optional[str] = None,
    run_policy: Optional[RunPolicy] = None,
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
            # A replay executes no Graph nodes and touches no checkpoint, so it
            # intentionally has no thread lease even if a caller supplied one.
            replay_state = build_cache_replay_state(cached_result, run_policy=run_policy)
            if replay_state:
                logger.info("使用缓存的研究结果进行 replay run")
                return replay_state
            logger.info("缓存结果不是可 replay 的完成报告，将执行 Graph")
    
    tid = thread_id or f"research-{uuid.uuid4().hex[:8]}"
    run_config = {"configurable": {"thread_id": tid}}
    logger.info(f"使用 thread_id 进行持久化检查点跟踪：{tid}")
    initial_state = start_run(
        _create_initial_state(topic, thread_id=tid, run_policy=run_policy)
    )

    async with PersistentRunLease(
        get_checkpoint_path(), tid, ttl_seconds=PERSISTENT_LEASE_TTL_SECONDS
    ) as lease:
        async with create_sqlite_checkpointer() as checkpointer:
            graph = create_research_graph(checkpointer=checkpointer)

            try:
                final_state = await _invoke_with_terminal_lifecycle(
                    graph,
                    initial_state,
                    run_config,
                    has_checkpointer=True,
                    execution_context=initial_state.execution_context,
                )
                lease.assert_held()
            except Exception as e:
                logger.error(f"研究流程失败：{e}")
                logger.info(f"流程状态已保存到磁盘。可使用 thread_id 恢复：{tid}")
                raise

    _persist_completed_research_memory(final_state)
    
    if use_cache and is_successful_cache_payload(final_state):
        cache.set(topic, final_state)
    
    if verbose:
        logger.info("流程已完成")
        if report_text := canonical_report_text(final_state):
            logger.info(f"报告已生成：{len(report_text)} 个字符")
    
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
    
    async with PersistentRunLease(
        get_checkpoint_path(), thread_id, ttl_seconds=PERSISTENT_LEASE_TTL_SECONDS
    ) as lease:
        async with create_sqlite_checkpointer() as checkpointer:
            graph = create_research_graph(checkpointer=checkpointer)

            state = await graph.aget_state(run_config)
            if not state or not state.values:
                raise DeepResearchError(f"未找到 thread_id 对应的检查点：{thread_id}")

            checkpoint_state = dict(state.values)
            next_nodes = tuple(state.next)
            logger.info(f"找到检查点，阶段：{checkpoint_state.get('current_stage', 'unknown')}")

            # A pending checkpoint cancelled through cancel_research can retain
            # LangGraph's queued node. Runtime ownership makes it terminal
            # without rewriting that queue or inventing a Graph route.
            if checkpoint_state.get("status") == "cancelled":
                return checkpoint_state

            if not next_nodes:
                adoption_patch = adopt_terminal_lifecycle_patch(
                    checkpoint_state, thread_id=thread_id
                )
                if adoption_patch:
                    await graph.aupdate_state(run_config, adoption_patch)
                    checkpoint_state.update(adoption_patch)
                return checkpoint_state

            lifecycle_patch = resume_lifecycle_patch(
                checkpoint_state, next_nodes, thread_id=thread_id
            )
            if lifecycle_patch:
                await graph.aupdate_state(run_config, lifecycle_patch)
                checkpoint_state.update(lifecycle_patch)

            input_state = filter_runtime_owned_input(additional_input) if additional_input else None
            final_state = await _invoke_with_terminal_lifecycle(
                graph,
                input_state or None,
                run_config,
                has_checkpointer=True,
                execution_context=execution_context_from_state(
                    checkpoint_state, thread_id=thread_id
                ),
            )
            lease.assert_held()

    _persist_completed_research_memory(final_state)
    
    return final_state


async def cancel_research(thread_id: str) -> Dict[str, Any]:
    """Cancel a paused persistent run without changing Graph routing.

    The contract is cooperative. A caller cancelling an already-running
    asyncio task must cancel that task itself; this API acquires the same lease
    and therefore safely terminalizes only a checkpoint with no active runner.
    """

    run_config = {"configurable": {"thread_id": thread_id}}
    async with PersistentRunLease(
        get_checkpoint_path(), thread_id, ttl_seconds=PERSISTENT_LEASE_TTL_SECONDS
    ):
        async with create_sqlite_checkpointer() as checkpointer:
            graph = create_research_graph(checkpointer=checkpointer)
            state = await graph.aget_state(run_config)
            if not state or not state.values:
                raise DeepResearchError(f"未找到 thread_id 对应的检查点：{thread_id}")

            checkpoint_state = dict(state.values)
            if not state.next or checkpoint_state.get("status") == "cancelled":
                return checkpoint_state

            lifecycle_patch = resume_lifecycle_patch(
                checkpoint_state, tuple(state.next), thread_id=thread_id
            )
            cancellation_patch: Dict[str, Any] = {
                key: value
                for key, value in lifecycle_patch.items()
                if key in {"run_id", "iteration", "execution_context"}
            }
            cancellation_patch.update(cancelled_lifecycle_patch())
            await graph.aupdate_state(run_config, cancellation_patch)
            checkpoint_state.update(cancellation_patch)
            return checkpoint_state


async def get_workflow_state(thread_id: str) -> Optional[Dict[str, Any]]:
    """根据线程 ID 获取流程当前状态。
    
    Args:
        thread_id: 要查询的线程 ID
        
    Returns:
        当前状态字典；找不到时返回 None
    """
    run_config = {"configurable": {"thread_id": thread_id}}
    
    async with create_sqlite_checkpointer() as checkpointer:
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
