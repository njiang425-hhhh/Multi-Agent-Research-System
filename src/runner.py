"""Minimal run orchestration for the research Graph.

``src.graph`` owns only the Planner → Searcher → Synthesizer → Writer
topology.  This module owns a run's cache, checkpoint, lifecycle, lease, and
post-run memory boundaries without adding another runtime framework.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from src.config import config
from src.exceptions import DeepResearchError
from src.graph import create_research_graph
from src.memory import ResearchMemoryStore, project_research_memories
from src.runtime_control import ExecutionContext, RunPolicy, invoke_with_execution_context
from src.runtime_lease import PersistentRunLease
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
from src.state import ResearchState
from src.state_compat import canonical_report_text
from src.utils.cache import ResearchCache


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
        if records:
            _memory_store_from_config().upsert_many(records)
    except Exception:
        logger.exception("Research memory persistence failed; run result is unchanged")


def _create_initial_state(
    topic: str,
    *,
    thread_id: Optional[str] = None,
    run_policy: Optional[RunPolicy] = None,
) -> ResearchState:
    return create_new_run_state(topic, thread_id=thread_id, run_policy=run_policy)


def get_checkpoint_path() -> Path:
    """Return the SQLite checkpoint path used by persistent runs."""

    cache_dir = Path(".cache/checkpoints")
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "research_checkpoints.db"


def create_memory_checkpointer() -> MemorySaver:
    """Create the process-local checkpointer used by ordinary runs."""

    return MemorySaver()


@asynccontextmanager
async def create_sqlite_checkpointer():
    """Yield the SQLite checkpointer used by persistent/resume workflows."""

    checkpoint_path = get_checkpoint_path()
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        logger.info("SQLite 检查点管理器已初始化：%s", checkpoint_path)
        yield checkpointer


async def _persist_lifecycle_patch(
    graph: Any,
    run_config: Optional[Dict[str, Any]],
    patch: Dict[str, Any],
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
    """Invoke Graph once and attach runtime-owned terminal lifecycle fields."""

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


class ResearchRunner:
    """One small orchestration boundary shared by CLI, web, and compatibility APIs.

    Options intentionally control only invocation semantics.  They do not
    affect Graph routing, agent selection, or search strategy.
    """

    def __init__(
        self,
        *,
        use_cache: bool = True,
        use_checkpoints: bool = True,
        persist_memory: bool | None = None,
    ) -> None:
        self.use_cache = use_cache
        self.use_checkpoints = use_checkpoints
        self.persist_memory = persist_memory

    async def run(
        self,
        topic: str,
        *,
        verbose: bool = True,
        thread_id: Optional[str] = None,
        run_policy: Optional[RunPolicy] = None,
    ) -> Dict[str, Any]:
        """Run with optional in-memory checkpointing and canonical cache replay."""

        logger.info("开始研究：%s", topic)
        cache = ResearchCache()
        if self.use_cache:
            cached_result = cache.get(topic)
            if cached_result:
                replay_state = build_cache_replay_state(cached_result, run_policy=run_policy)
                if replay_state:
                    logger.info("使用缓存的研究结果进行 replay run")
                    return replay_state
                logger.info("缓存结果不是可 replay 的完成报告，将执行 Graph")

        run_config: Dict[str, Any] = {}
        tid: Optional[str] = None
        if self.use_checkpoints:
            checkpointer = create_memory_checkpointer()
            tid = thread_id or f"research-{uuid.uuid4().hex[:8]}"
            run_config["configurable"] = {"thread_id": tid}
            logger.info("使用 thread_id 进行检查点跟踪：%s", tid)
        else:
            checkpointer = None

        initial_state = start_run(_create_initial_state(topic, thread_id=tid, run_policy=run_policy))
        graph = create_research_graph(checkpointer=checkpointer)
        try:
            final_state = await _invoke_with_terminal_lifecycle(
                graph,
                initial_state,
                run_config or None,
                has_checkpointer=self.use_checkpoints,
                execution_context=initial_state.execution_context,
            )
        except Exception as error:
            logger.error("研究流程失败：%s", error)
            if tid:
                logger.info("线程 ID：%s", tid)
            raise

        self._persist_memory_if_enabled(final_state)
        if self.use_cache and is_successful_cache_payload(final_state):
            cache.set(topic, final_state)
        self._log_completion(final_state, verbose)
        return final_state

    async def run_with_persistence(
        self,
        topic: str,
        *,
        verbose: bool = True,
        thread_id: Optional[str] = None,
        run_policy: Optional[RunPolicy] = None,
    ) -> Dict[str, Any]:
        """Run with SQLite checkpoints and the existing single-thread lease."""

        logger.info("开始研究：%s", topic)
        cache = ResearchCache()
        if self.use_cache:
            cached_result = cache.get(topic)
            if cached_result:
                replay_state = build_cache_replay_state(cached_result, run_policy=run_policy)
                if replay_state:
                    logger.info("使用缓存的研究结果进行 replay run")
                    return replay_state
                logger.info("缓存结果不是可 replay 的完成报告，将执行 Graph")

        tid = thread_id or f"research-{uuid.uuid4().hex[:8]}"
        run_config = {"configurable": {"thread_id": tid}}
        initial_state = start_run(_create_initial_state(topic, thread_id=tid, run_policy=run_policy))

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
                except Exception as error:
                    logger.error("研究流程失败：%s", error)
                    logger.info("流程状态已保存到磁盘。可使用 thread_id 恢复：%s", tid)
                    raise

        self._persist_memory_if_enabled(final_state)
        if self.use_cache and is_successful_cache_payload(final_state):
            cache.set(topic, final_state)
        self._log_completion(final_state, verbose)
        return final_state

    async def resume(
        self,
        thread_id: str,
        additional_input: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Resume an existing SQLite checkpoint without changing Graph routing."""

        logger.info("使用 thread_id 恢复研究：%s", thread_id)
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
                if checkpoint_state.get("status") == "cancelled":
                    return checkpoint_state
                if not next_nodes:
                    adoption_patch = adopt_terminal_lifecycle_patch(checkpoint_state, thread_id=thread_id)
                    if adoption_patch:
                        await graph.aupdate_state(run_config, adoption_patch)
                        checkpoint_state.update(adoption_patch)
                    return checkpoint_state

                lifecycle_patch = resume_lifecycle_patch(checkpoint_state, next_nodes, thread_id=thread_id)
                if lifecycle_patch:
                    await graph.aupdate_state(run_config, lifecycle_patch)
                    checkpoint_state.update(lifecycle_patch)
                input_state = filter_runtime_owned_input(additional_input) if additional_input else None
                final_state = await _invoke_with_terminal_lifecycle(
                    graph,
                    input_state or None,
                    run_config,
                    has_checkpointer=True,
                    execution_context=execution_context_from_state(checkpoint_state, thread_id=thread_id),
                )
                lease.assert_held()

        self._persist_memory_if_enabled(final_state)
        return final_state

    async def cancel(self, thread_id: str) -> Dict[str, Any]:
        """Terminalize a paused persistent run while preserving its queued Graph node."""

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
                lifecycle_patch = resume_lifecycle_patch(checkpoint_state, tuple(state.next), thread_id=thread_id)
                cancellation_patch = {
                    key: value
                    for key, value in lifecycle_patch.items()
                    if key in {"run_id", "iteration", "execution_context"}
                }
                cancellation_patch.update(cancelled_lifecycle_patch())
                await graph.aupdate_state(run_config, cancellation_patch)
                checkpoint_state.update(cancellation_patch)
                return checkpoint_state

    def _persist_memory_if_enabled(self, final_state: Dict[str, Any]) -> None:
        if self.persist_memory is False:
            return
        _persist_completed_research_memory(final_state)

    @staticmethod
    def _log_completion(final_state: Dict[str, Any], verbose: bool) -> None:
        if not verbose:
            return
        logger.info("流程已完成")
        if report_text := canonical_report_text(final_state):
            logger.info("报告已生成：%s 个字符", len(report_text))


async def run_research(
    topic: str,
    verbose: bool = True,
    use_cache: bool = True,
    use_checkpoints: bool = True,
    thread_id: Optional[str] = None,
    run_policy: Optional[RunPolicy] = None,
    persist_memory: bool | None = None,
) -> Dict[str, Any]:
    """Compatibility wrapper for the ordinary shared runner entry point."""

    return await ResearchRunner(
        use_cache=use_cache,
        use_checkpoints=use_checkpoints,
        persist_memory=persist_memory,
    ).run(topic, verbose=verbose, thread_id=thread_id, run_policy=run_policy)


async def run_research_with_persistence(
    topic: str,
    verbose: bool = True,
    use_cache: bool = True,
    thread_id: Optional[str] = None,
    run_policy: Optional[RunPolicy] = None,
    persist_memory: bool | None = None,
) -> Dict[str, Any]:
    """Compatibility wrapper for the SQLite-persistent runner entry point."""

    return await ResearchRunner(
        use_cache=use_cache,
        persist_memory=persist_memory,
    ).run_with_persistence(topic, verbose=verbose, thread_id=thread_id, run_policy=run_policy)


async def resume_research(
    thread_id: str,
    additional_input: Optional[Dict[str, Any]] = None,
    *,
    persist_memory: bool | None = None,
) -> Dict[str, Any]:
    """Compatibility wrapper for persistent checkpoint resume."""

    return await ResearchRunner(persist_memory=persist_memory).resume(thread_id, additional_input)


async def cancel_research(thread_id: str) -> Dict[str, Any]:
    """Compatibility wrapper for cooperative persistent-run cancellation."""

    return await ResearchRunner().cancel(thread_id)


async def get_workflow_state(thread_id: str) -> Optional[Dict[str, Any]]:
    """Read a persisted workflow state without executing Graph."""

    run_config = {"configurable": {"thread_id": thread_id}}
    async with create_sqlite_checkpointer() as checkpointer:
        graph = create_research_graph(checkpointer=checkpointer)
        state = await graph.aget_state(run_config)
        return dict(state.values) if state and state.values else None


def list_research_threads() -> list[str]:
    """List persistent checkpoint thread IDs."""

    checkpoint_path = get_checkpoint_path()
    if not checkpoint_path.exists():
        return []
    try:
        with sqlite3.connect(str(checkpoint_path)) as connection:
            rows = connection.execute(
                "SELECT DISTINCT thread_id FROM checkpoints ORDER BY thread_ts DESC"
            ).fetchall()
        return [row[0] for row in rows]
    except Exception as error:
        logger.warning("列出线程失败：%s", error)
        return []
