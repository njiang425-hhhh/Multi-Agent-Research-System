"""带增强用户体验的深度研究代理 Chainlit 交互界面。"""

import asyncio
import chainlit as cl
from pathlib import Path
from datetime import datetime
from typing import Optional

from src.config import config
from src.graph import create_research_graph
from src.runtime_lifecycle import apply_terminal_lifecycle, create_new_run_state, start_run
from src.state_compat import canonical_documents, canonical_findings, canonical_report, canonical_report_text, canonical_usage
from src.utils.exports import ReportExporter
from src.utils.history import ResearchHistory
from src.callbacks import (
    progress_callback, 
    ProgressUpdate, 
    ResearchStage,
    emit_complete
)


# ═══════════════════════════════════════════════════════════════════════════════
# 视觉常量
# ═══════════════════════════════════════════════════════════════════════════════

STAGE_EMOJI = {
    ResearchStage.INITIALIZING: "",
    ResearchStage.PLANNING: "",
    ResearchStage.SEARCHING: "",
    ResearchStage.EXTRACTING: "",
    ResearchStage.SYNTHESIZING: "",
    ResearchStage.WRITING: "",
    ResearchStage.COMPLETE: "",
    ResearchStage.ERROR: ""
}

STAGE_COLORS = {
    ResearchStage.INITIALIZING: "#6b7280",
    ResearchStage.PLANNING: "#8b5cf6",
    ResearchStage.SEARCHING: "#3b82f6",
    ResearchStage.EXTRACTING: "#06b6d4",
    ResearchStage.SYNTHESIZING: "#10b981",
    ResearchStage.WRITING: "#f59e0b",
    ResearchStage.COMPLETE: "#22c55e",
    ResearchStage.ERROR: "#ef4444"
}

STAGE_NAMES = {
    ResearchStage.INITIALIZING: "初始化",
    ResearchStage.PLANNING: "规划策略",
    ResearchStage.SEARCHING: "网络搜索",
    ResearchStage.EXTRACTING: "内容提取",
    ResearchStage.SYNTHESIZING: "AI 综合",
    ResearchStage.WRITING: "撰写报告",
    ResearchStage.COMPLETE: "已完成",
    ResearchStage.ERROR: "错误"
}


# ═══════════════════════════════════════════════════════════════════════════════
# 进度显示
# ═══════════════════════════════════════════════════════════════════════════════

class EnhancedProgressDisplay:
    """带动态视觉效果的现代化进度显示。"""
    
    def __init__(self):
        self.message: cl.Message = None
        self.step_messages: dict = {}  # 跟踪每个步骤的消息
        self.updates: list[ProgressUpdate] = []
        self.current_stage: ResearchStage = ResearchStage.INITIALIZING
        self.start_time: datetime = None
        self.topic: str = ""
        
    async def initialize(self, topic: str):
        """初始化简洁的进度卡片。"""
        self.start_time = datetime.now()
        self.updates = []
        self.topic = topic
        self.current_stage = ResearchStage.INITIALIZING
        
        content = self._render()
        self.message = cl.Message(content=content)
        await self.message.send()
    
    async def update(self, progress_update: ProgressUpdate):
        """以平滑过渡更新显示。"""
        self.updates.append(progress_update)
        self.current_stage = progress_update.stage
        
        if self.message:
            self.message.content = self._render()
            await self.message.update()
    
    def _get_elapsed(self) -> str:
        """获取格式化的已用时间。"""
        if not self.start_time:
            return "0s"
        delta = datetime.now() - self.start_time
        minutes, seconds = divmod(delta.seconds, 60)
        if minutes > 0:
            return f"{minutes} 分 {seconds} 秒"
        return f"{seconds} 秒"
    
    def _render_progress_bar(self, pct: float) -> str:
        """渲染现代化进度条。"""
        filled = int(25 * pct / 100)
        empty = 25 - filled
        bar = "█" * filled + "░" * empty
        return f"`[{bar}]` **{pct:.0f}%**"
    
    def _render_stage_pipeline(self) -> str:
        """渲染带状态指示器的阶段流程。"""
        stages = [
            ResearchStage.PLANNING,
            ResearchStage.SEARCHING,
            ResearchStage.EXTRACTING,
            ResearchStage.SYNTHESIZING,
            ResearchStage.WRITING,
            ResearchStage.COMPLETE
        ]
        
        current_idx = -1
        if self.current_stage in stages:
            current_idx = stages.index(self.current_stage)
        
        lines = []
        for idx, stage in enumerate(stages):
            emoji = STAGE_EMOJI.get(stage, "")
            name = STAGE_NAMES.get(stage, stage.value)
            
            if idx < current_idx:
                # 已完成
                lines.append(f"  [完成] ~~{name}~~")
            elif idx == current_idx:
                # 当前阶段——进行中
                lines.append(f"  **{name}** <- *进行中*")
            else:
                # 待处理
                lines.append(f"  [ ] {name}")
        
        return "\n".join(lines)
    
    def _render_activity_feed(self) -> str:
        """渲染带时间戳的近期活动。"""
        if not self.updates:
            return "*正在初始化研究代理……*"
        
        lines = []
        recent = self.updates[-6:]  # 最近 6 条更新
        
        for update in reversed(recent):
            emoji = STAGE_EMOJI.get(update.stage, "")
            time_str = update.timestamp.strftime("%H:%M:%S")
            msg = update.message
            
            line = f"`{time_str}` {emoji}{msg}" if emoji else f"`{time_str}` {msg}"
            if update.details:
                # 截断过长的详情
                details = update.details[:60] + "..." if len(update.details) > 60 else update.details
                line += f"\n> _{details}_"
            lines.append(line)
        
        return "\n\n".join(lines)
    
    def _render(self) -> str:
        """渲染完整的进度显示。"""
        elapsed = self._get_elapsed()
        
        # 获取当前进度百分比
        pct = 0
        if self.updates:
            for update in reversed(self.updates):
                if update.progress_pct is not None:
                    pct = update.progress_pct
                    break
        
        progress_bar = self._render_progress_bar(pct)
        pipeline = self._render_stage_pipeline()
        activity = self._render_activity_feed()
        
        return f"""
## 研究进行中

**主题：** *{self.topic}*

---

### 进度

{progress_bar}

已用时间：**{elapsed}**

---

### 流程状态

{pipeline}

---

### 活动记录

{activity}
"""


# ═══════════════════════════════════════════════════════════════════════════════
# 研究运行器
# ═══════════════════════════════════════════════════════════════════════════════

async def run_research_with_updates(topic: str, progress_display: EnhancedProgressDisplay):
    """运行研究并实时更新进度。"""
    progress_callback.reset()
    
    async def on_progress(update: ProgressUpdate):
        await progress_display.update(update)
    
    progress_callback.register_async(on_progress)
    
    try:
        initial_state = start_run(create_new_run_state(topic))
        graph = create_research_graph()
        final_state = apply_terminal_lifecycle(await graph.ainvoke(initial_state))
        
        documents = canonical_documents(final_state)
        findings = canonical_findings(final_state)
        await emit_complete(topic, len(documents), len(findings))
        
        return final_state
    finally:
        progress_callback.unregister(on_progress)


# ═══════════════════════════════════════════════════════════════════════════════
# 聊天处理器
# ═══════════════════════════════════════════════════════════════════════════════

@cl.on_chat_start
async def start():
    """初始化欢迎界面。"""
    
    # 保存会话状态
    cl.user_session.set("research_count", 0)
    
    # 创建快速开始操作按钮
    actions = [
        cl.Action(
            name="example_quantum",
            payload={"topic": "2025 年量子计算的未来"},
            label="量子计算"
        ),
        cl.Action(
            name="example_ai",
            payload={"topic": "AI 代理和自主系统的最新突破"},
            label="AI 代理"
        ),
        cl.Action(
            name="example_climate",
            payload={"topic": "气候变化缓解领域的新兴技术"},
            label="气候技术"
        ),
        cl.Action(
            name="show_history",
            payload={},
            label="查看历史"
        ),
        cl.Action(
            name="show_settings",
            payload={},
            label="设置"
        )
    ]
    
    welcome_content = f"""
# 深度研究代理

**将问题转化为全面且来源可靠的研究报告。**

我会分析研究主题，在网络上搜索权威来源，评估来源可信度，综合关键发现，并在几分钟内生成带有规范引用的专业报告。

---

## 我能做什么

| 步骤 | 说明 |
|------|-------------|
| **规划** | 创建有策略的研究目标和搜索查询 |
| **搜索** | 使用 DuckDuckGo 搜索权威来源 |
| **提取** | 从高可信度网站提取完整内容 |
| **综合** | 使用 AI 分析并交叉核对研究发现 |
| **撰写** | 生成全面且带引用的报告 |

---

## 快速开始

点击下面的主题，或输入你自己的研究问题：

"""
    
    await cl.Message(
        content=welcome_content,
        actions=actions
    ).send()


@cl.action_callback("example_quantum")
async def on_example_quantum(action: cl.Action):
    """处理量子计算示例。"""
    topic = action.payload.get("topic")
    await start_research(topic)


@cl.action_callback("example_ai")
async def on_example_ai(action: cl.Action):
    """处理 AI 代理示例。"""
    topic = action.payload.get("topic")
    await start_research(topic)


@cl.action_callback("example_climate")
async def on_example_climate(action: cl.Action):
    """处理气候技术示例。"""
    topic = action.payload.get("topic")
    await start_research(topic)


@cl.action_callback("show_history")
async def on_show_history(action: cl.Action):
    """显示研究历史。"""
    history = ResearchHistory()
    entries = history.get_recent(limit=10)
    
    if not entries:
        await cl.Message(
            content="**研究历史**\n\n*没有找到历史研究记录，请在上方开始第一次研究！*"
        ).send()
        return
    
    content = "# 研究历史\n\n"
    content += "| 日期 | 主题 | 来源 | 发现 |\n"
    content += "|------|------|------|------|\n"
    
    for entry in entries:
        date = entry.get('timestamp', '无数据')
        if isinstance(date, str) and len(date) > 10:
            date = date[:10]
        topic = entry.get('topic', '未知主题')[:40]
        if len(entry.get('topic', '')) > 40:
            topic += "..."
        sources = entry.get('metadata', {}).get('sources', '无数据')
        findings = entry.get('metadata', {}).get('findings', '无数据')
        content += f"| {date} | {topic} | {sources} | {findings} |\n"
    
    await cl.Message(content=content).send()


@cl.action_callback("show_settings")
async def on_show_settings(action: cl.Action):
    """显示当前配置。"""
    content = f"""# 当前设置

## 模型配置
| 设置 | 值 |
|---------|-------|
| 提供商 | `{config.model_provider}` |
| 模型 | `{config.model_name}` |
| 摘要模型 | `{config.summarization_model}` |

## 搜索配置
| 设置 | 值 |
|---------|-------|
| 最大搜索查询数 | `{config.max_search_queries}` |
| 每个查询的结果数 | `{config.max_search_results_per_query}` |
| 最低可信度分数 | `{config.min_credibility_score}` |

## 报告配置
| 设置 | 值 |
|---------|-------|
| 最大章节数 | `{config.max_report_sections}` |
| 每章最少字数 | `{config.min_section_words}` |
| 引用格式 | `{config.citation_style.upper()}` |

---

*如需修改设置，请编辑 `.env` 文件并重启应用。*
"""
    await cl.Message(content=content).send()


@cl.action_callback("download_md")
async def on_download_md(action: cl.Action):
    """处理 Markdown 下载。"""
    file_path = action.payload.get("path")
    if file_path and Path(file_path).exists():
        elements = [cl.File(name=Path(file_path).name, path=file_path, display="inline")]
        await cl.Message(content="**Markdown 报告：**", elements=elements).send()


@cl.action_callback("download_html")
async def on_download_html(action: cl.Action):
    """处理 HTML 下载。"""
    file_path = action.payload.get("path")
    if file_path and Path(file_path).exists():
        elements = [cl.File(name=Path(file_path).name, path=file_path, display="inline")]
        await cl.Message(content="**HTML 报告：**", elements=elements).send()


@cl.action_callback("download_txt")
async def on_download_txt(action: cl.Action):
    """处理 TXT 下载。"""
    file_path = action.payload.get("path")
    if file_path and Path(file_path).exists():
        elements = [cl.File(name=Path(file_path).name, path=file_path, display="inline")]
        await cl.Message(content="**纯文本报告：**", elements=elements).send()


@cl.action_callback("view_sources")
async def on_view_sources(action: cl.Action):
    """显示详细来源列表。"""
    sources = action.payload.get("sources", [])
    credibility = action.payload.get("credibility", [])
    
    if not sources:
        await cl.Message(content="*没有可用的来源。*").send()
        return
    
    content = "# 来源分析\n\n"
    content += "| # | 可信度 | 来源 | URL |\n"
    content += "|---|--------|------|-----|\n"
    
    for i, source in enumerate(sources[:30]):
        title = source.get('title', '未知标题')[:35]
        if len(source.get('title', '')) > 35:
            title += "..."
        url = source.get('url', '无数据')
        
        # 获取可信度徽章
        cred = credibility[i] if i < len(credibility) else {}
        level = cred.get('level', 'unknown')
        score = cred.get('score', '无数据')
        
        badge = "[高]" if level == 'high' else "[中]" if level == 'medium' else "[低]"
        content += f"| {i+1} | {badge} {score} | {title} | [链接]({url}) |\n"
    
    await cl.Message(content=content).send()


@cl.action_callback("view_findings")
async def on_view_findings(action: cl.Action):
    """详细显示关键发现。"""
    findings = action.payload.get("findings", [])
    
    if not findings:
        await cl.Message(content="*没有可用的研究发现。*").send()
        return
    
    content = "# 关键发现\n\n"
    for i, finding in enumerate(findings, 1):
        content += f"**{i}.** {finding}\n\n"
    
    await cl.Message(content=content).send()


async def start_research(topic: str):
    """开始研究指定主题。"""
    
    # 验证配置
    try:
        config.validate_config()
    except ValueError as e:
        await cl.Message(
            content=f"""## 配置错误

**错误：** {str(e)}

请确认 `.env` 文件已经正确配置所需的 API 密钥。

```
# .env 配置示例
GEMINI_API_KEY=your-api-key-here
MODEL_PROVIDER=gemini
MODEL_NAME=gemini-2.5-flash
```
"""
        ).send()
        return
    
    # 增加研究次数
    count = cl.user_session.get("research_count", 0) + 1
    cl.user_session.set("research_count", count)
    
    # 显示配置摘要
    await cl.Message(
            content=f"""## 开始研究 #{count}

**主题：** *{topic}*

| 配置项 | 值 |
|---------------|-------|
| 模型 | `{config.model_name}` |
| 最大查询数 | `{config.max_search_queries}` |
| 最大章节数 | `{config.max_report_sections}` |

*研究即将开始……*
"""
    ).send()
    
    # 初始化进度显示
    progress_display = EnhancedProgressDisplay()
    await progress_display.initialize(topic)
    
    try:
        # 运行研究
        final_state = await run_research_with_updates(topic, progress_display)
        
        # 检查错误
        if final_state.get("error"):
            await cl.Message(
                content=f"""## 研究失败

**错误：** {final_state.get('error')}

请重试，或简化研究主题。
"""
            ).send()
            return
        
        # 提取结果
        documents = canonical_documents(final_state)
        findings = canonical_findings(final_state)
        canonical_result_report = canonical_report(final_state)
        report_sections = (
            canonical_result_report.sections
            if canonical_result_report is not None
            else final_state.get('report_sections', [])
        )
        credibility_scores = [document.credibility or {} for document in documents]
        
        # 统计指标
        unique_sources = set()
        for document in documents:
            if document.uri:
                unique_sources.add(document.uri)
        
        high_cred = sum(1 for s in credibility_scores if s.get('level') == 'high')
        medium_cred = sum(1 for s in credibility_scores if s.get('level') == 'medium')
        
        # LLM 指标
        usage = canonical_usage(final_state)
        llm_calls = usage.llm_calls
        total_input = usage.input_tokens
        total_output = usage.output_tokens
        total_tokens = total_input + total_output
        
        # 已用时间
        elapsed = 0
        if progress_display.start_time:
            elapsed = (datetime.now() - progress_display.start_time).seconds
        
        # 创建带操作按钮的交互式摘要
        summary_actions = [
            cl.Action(
                name="view_sources",
                payload={
                    "sources": [{"title": document.title, "url": document.uri} for document in documents[:30]],
                    "credibility": credibility_scores[:30]
                },
                label="查看来源"
            ),
            cl.Action(
                name="view_findings",
                payload={"findings": [finding.statement for finding in findings[:20]]},
                label="查看发现"
            )
        ]
        
        summary_content = f"""
## 研究完成！

### 研究指标

| 类别 | 指标 | 数值 |
|----------|--------|-------|
| 来源 | 去重后总数 | **{len(unique_sources)}** |
| | 高可信度 | **{high_cred}** [高] |
| | 中可信度 | **{medium_cred}** [中] |
| 分析 | 关键洞见 | **{len(findings)}** |
| | 报告章节 | **{len(report_sections)}** |
| 性能 | 总耗时 | **{elapsed} 秒** |
| | LLM 调用次数 | **{llm_calls}** |
| | 使用的 Token | **{total_tokens:,}** |

---

*点击下面的按钮查看数据：*
"""
        
        await cl.Message(
            content=summary_content,
            actions=summary_actions
        ).send()
        
        # 保存并显示报告
        report = canonical_report_text(final_state)
        if report:
            
            # 保存文件
            output_dir = Path("outputs")
            output_dir.mkdir(exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_topic = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in topic)
            safe_topic = safe_topic[:30].strip()
            filename = f"{safe_topic}_{timestamp}.md"
            output_file = output_dir / filename
            output_file.write_text(report, encoding='utf-8')
            
            # 导出其他格式
            exporter = ReportExporter()
            base_path = output_file.with_suffix('')
            html_file = exporter.export(report, base_path, format='html')
            txt_file = exporter.export(report, base_path, format='txt')
            
            # 添加到历史记录
            history = ResearchHistory()
            history.add_research(
                topic=topic,
                output_file=output_file,
                metadata={
                    'sources': len(unique_sources),
                    'sections': len(report_sections),
                    'findings': len(findings),
                    'elapsed_seconds': elapsed,
                    'total_tokens': total_tokens
                }
            )
            
            # 下载操作
            download_actions = [
                cl.Action(
                    name="download_md",
                    payload={"path": str(output_file)},
                    label="Markdown"
                ),
                cl.Action(
                    name="download_html",
                    payload={"path": str(html_file)},
                    label="HTML"
                ),
                cl.Action(
                    name="download_txt",
                    payload={"path": str(txt_file)},
                    label="文本"
                )
            ]
            
            await cl.Message(
                content=f"""## 下载报告

报告已保存！请选择格式：

| 格式 | 文件 | 大小 |
|--------|------|------|
| Markdown | `{filename}` | {output_file.stat().st_size:,} 字节 |
| HTML | `{html_file.name}` | {html_file.stat().st_size:,} 字节 |
| 纯文本 | `{txt_file.name}` | {txt_file.stat().st_size:,} 字节 |
""",
                actions=download_actions
            ).send()
            
            # 显示报告
            await cl.Message(
                content=f"""## 完整报告

---

{report}
"""
            ).send()
            
            # 引导用户开始下一次研究
            next_actions = [
                cl.Action(
                    name="example_quantum",
                    payload={"topic": f"{topic.split()[0] if topic.split() else '技术'}的最新进展"},
                    label="相关主题"
                )
            ]
            
            await cl.Message(
                content="""---

## 要继续吗？

输入另一个研究主题，或点击上面的建议操作！

*提示：你可以针对研究中的具体方面提出后续问题。*
""",
                actions=next_actions
            ).send()
        
        else:
            await cl.Message(
                content="**警告：** 未生成报告，请换一个主题重试。"
            ).send()
    
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        await cl.Message(
            content=f"""## 未预期的错误

**错误：** {str(e)}

<details>
<summary>技术细节（点击展开）</summary>

```python
{error_details}
```

</details>

请检查日志后重试。
"""
        ).send()


@cl.on_message
async def main(message: cl.Message):
    """处理用户消息。"""
    topic = message.content.strip()
    
    if not topic:
        await cl.Message(
            content="请输入研究主题。"
        ).send()
        return
    
    # 检查特殊命令
    if topic.lower() in ["/history", "history", "show history"]:
        await on_show_history(cl.Action(name="show_history", payload={}))
        return
    
    if topic.lower() in ["/settings", "settings", "show settings"]:
        await on_show_settings(cl.Action(name="show_settings", payload={}))
        return
    
    if topic.lower() in ["/help", "help"]:
        await cl.Message(
            content="""## 帮助

### 命令
- `/history` - 查看研究历史
- `/settings` - 查看当前配置
- `/help` - 显示此帮助信息

### 提示
- 尽量明确研究主题，以获得更好的结果
- 可以使用“……的最新趋势是什么？”这类问题
- 查询当前信息时加入“2025 年”等时间上下文

### 示例主题
- “2025 年量子计算的未来”
- “Transformer 模型是如何工作的？”
- “微服务架构的最佳实践”
"""
        ).send()
        return
    
    await start_research(topic)


if __name__ == "__main__":
    from chainlit.cli import run_chainlit
    run_chainlit(__file__)
