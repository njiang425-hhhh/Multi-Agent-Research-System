"""研究流程实时进度更新的回调系统。"""

import asyncio
from typing import Callable, Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class ResearchStage(Enum):
    """研究流程阶段。"""
    INITIALIZING = "initializing"
    PLANNING = "planning"
    SEARCHING = "searching"
    EXTRACTING = "extracting"
    SYNTHESIZING = "synthesizing"
    WRITING = "writing"
    COMPLETE = "complete"
    ERROR = "error"


@dataclass
class ProgressUpdate:
    """进度更新事件。"""
    stage: ResearchStage
    message: str
    details: Optional[str] = None
    progress_pct: Optional[float] = None  # 0-100
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)


class ProgressCallback:
    """管理研究流程的进度回调。"""
    
    _instance: Optional['ProgressCallback'] = None
    _callbacks: List[Callable[[ProgressUpdate], None]] = []
    _async_callbacks: List[Callable[[ProgressUpdate], Any]] = []
    _updates: List[ProgressUpdate] = []
    _current_stage: ResearchStage = ResearchStage.INITIALIZING
    
    def __new__(cls):
        """单例模式，确保全局只有一个回调管理器。"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._callbacks = []
            cls._instance._async_callbacks = []
            cls._instance._updates = []
            cls._instance._current_stage = ResearchStage.INITIALIZING
        return cls._instance
    
    def reset(self):
        """为新的研究会话重置状态。"""
        self._updates = []
        self._current_stage = ResearchStage.INITIALIZING
    
    def register(self, callback: Callable[[ProgressUpdate], None]):
        """注册同步回调函数。"""
        if callback not in self._callbacks:
            self._callbacks.append(callback)
    
    def register_async(self, callback: Callable[[ProgressUpdate], Any]):
        """注册异步回调函数。"""
        if callback not in self._async_callbacks:
            self._async_callbacks.append(callback)
    
    def unregister(self, callback: Callable):
        """注销回调函数。"""
        if callback in self._callbacks:
            self._callbacks.remove(callback)
        if callback in self._async_callbacks:
            self._async_callbacks.remove(callback)
    
    def clear_callbacks(self):
        """清除所有已注册的回调。"""
        self._callbacks = []
        self._async_callbacks = []
    
    async def emit(self, update: ProgressUpdate):
        """向所有已注册的回调发送进度更新。"""
        self._current_stage = update.stage
        self._updates.append(update)
        
        # 记录更新
        logger.info(f"[{update.stage.value}] {update.message}" + 
                   (f" - {update.details}" if update.details else ""))
        
        # 调用同步回调
        for callback in self._callbacks:
            try:
                callback(update)
            except Exception as e:
                logger.error(f"同步回调执行出错：{e}")
        
        # 调用异步回调
        for callback in self._async_callbacks:
            try:
                await callback(update)
            except Exception as e:
                logger.error(f"异步回调执行出错：{e}")
    
    @property
    def current_stage(self) -> ResearchStage:
        return self._current_stage
    
    @property
    def updates(self) -> List[ProgressUpdate]:
        return self._updates.copy()


# 全局进度回调实例
progress_callback = ProgressCallback()


# 发送进度的便捷函数
async def emit_progress(
    stage: ResearchStage,
    message: str,
    details: Optional[str] = None,
    progress_pct: Optional[float] = None,
    **metadata
):
    """发送进度更新。"""
    update = ProgressUpdate(
        stage=stage,
        message=message,
        details=details,
        progress_pct=progress_pct,
        metadata=metadata
    )
    await progress_callback.emit(update)


async def emit_planning_start(topic: str):
    """发送规划阶段开始事件。"""
    await emit_progress(
        ResearchStage.PLANNING,
        "正在创建研究计划",
        f"主题：{topic}",
        progress_pct=5
    )


async def emit_planning_complete(num_queries: int, num_sections: int):
    """发送规划阶段完成事件。"""
    await emit_progress(
        ResearchStage.PLANNING,
        "研究计划已创建",
        f"已规划 {num_queries} 个搜索查询和 {num_sections} 个报告章节",
        progress_pct=15
    )


async def emit_search_start(query: str, query_num: int, total_queries: int):
    """发送搜索开始事件。"""
    base_progress = 15
    search_progress_range = 35  # 15% to 50%
    progress = base_progress + (query_num / total_queries) * search_progress_range
    
    await emit_progress(
        ResearchStage.SEARCHING,
        f"正在搜索（{query_num}/{total_queries}）",
        f"查询：{query[:60]}..." if len(query) > 60 else f"查询：{query}",
        progress_pct=progress
    )


async def emit_search_results(num_results: int, query_num: int, total_queries: int):
    """发送搜索结果事件。"""
    base_progress = 15
    search_progress_range = 35
    progress = base_progress + ((query_num + 0.5) / total_queries) * search_progress_range
    
    await emit_progress(
        ResearchStage.SEARCHING,
        f"找到 {num_results} 个结果",
        f"查询 {query_num}/{total_queries} 已完成",
        progress_pct=progress
    )


async def emit_extraction_start(url: str, current: int, total: int):
    """发送内容提取开始事件。"""
    base_progress = 50
    extract_progress_range = 15  # 50% to 65%
    progress = base_progress + (current / total) * extract_progress_range
    
    # 从 URL 提取域名，以便更清晰地显示
    try:
        from urllib.parse import urlparse
        domain = urlparse(url).netloc
    except:
        domain = url[:40]
    
    await emit_progress(
        ResearchStage.EXTRACTING,
        f"正在提取内容（{current}/{total}）",
        f"来源：{domain}",
        progress_pct=progress
    )


async def emit_extraction_complete(num_extracted: int, total_chars: int):
    """发送内容提取完成事件。"""
    await emit_progress(
        ResearchStage.EXTRACTING,
        "内容提取完成",
        f"已提取 {num_extracted} 个页面，共 {total_chars:,} 个字符",
        progress_pct=65
    )


async def emit_synthesis_start(num_sources: int):
    """发送综合阶段开始事件。"""
    await emit_progress(
        ResearchStage.SYNTHESIZING,
        "正在分析来源",
        f"正在综合 {num_sources} 个来源并提取关键发现",
        progress_pct=68
    )


async def emit_synthesis_progress(message: str):
    """发送综合进度事件。"""
    await emit_progress(
        ResearchStage.SYNTHESIZING,
        message,
        progress_pct=72
    )


async def emit_synthesis_complete(num_findings: int):
    """发送综合完成事件。"""
    await emit_progress(
        ResearchStage.SYNTHESIZING,
        "综合完成",
        f"已提取 {num_findings} 条关键发现",
        progress_pct=78
    )


async def emit_writing_start(num_sections: int):
    """发送撰写阶段开始事件。"""
    await emit_progress(
        ResearchStage.WRITING,
        "正在撰写报告",
        f"正在生成 {num_sections} 个章节",
        progress_pct=80
    )


async def emit_writing_section(section_title: str, section_num: int, total_sections: int):
    """发送章节撰写进度事件。"""
    base_progress = 80
    writing_progress_range = 18  # 80% to 98%
    progress = base_progress + (section_num / total_sections) * writing_progress_range
    
    await emit_progress(
        ResearchStage.WRITING,
        f"正在撰写章节（{section_num}/{total_sections}）",
        f"章节：{section_title[:50]}..." if len(section_title) > 50 else f"章节：{section_title}",
        progress_pct=progress
    )


async def emit_writing_complete(report_length: int):
    """发送撰写完成事件。"""
    await emit_progress(
        ResearchStage.WRITING,
        "报告撰写完成",
        f"已生成 {report_length:,} 个字符的报告",
        progress_pct=98
    )


async def emit_complete(topic: str, sources: int, findings: int):
    """发送研究完成事件。"""
    await emit_progress(
        ResearchStage.COMPLETE,
        "研究完成！",
        f"已分析 {sources} 个来源，提取 {findings} 条洞见",
        progress_pct=100
    )


async def emit_error(error_message: str):
    """发送错误事件。"""
    await emit_progress(
        ResearchStage.ERROR,
        "发生错误",
        error_message,
        progress_pct=None
    )
