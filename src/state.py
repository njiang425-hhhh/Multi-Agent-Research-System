"""ResearchOS V1 状态契约。

本模块同时保留 V0 旧字段和 V1 标准字段。V1 只声明数据契约，不负责
新旧字段之间的自动同步；当前 Agent 和 Graph 仍可继续使用旧字段。
"""

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field
from src.evidence.contracts import DocumentAnalysis, Evidence
from src.agent_trace import AgentTraceEvent
from src.runtime_control import ExecutionContext, TerminalReason


class SearchQuery(BaseModel):
    """带元数据的搜索查询。"""
    query: str = Field(description="搜索查询文本")
    purpose: str = Field(description="发起此查询的原因")
    completed: bool = Field(default=False)


class SearchResult(BaseModel):
    """带内容的搜索结果。"""
    query: str = Field(description="原始查询")
    title: str = Field(description="结果标题")
    url: str = Field(description="结果 URL")
    snippet: str = Field(description="结果摘要/简介")
    content: Optional[str] = Field(default=None, description="可用时的完整抓取内容")


class Document(BaseModel):
    """ResearchOS 的通用文档模型。

    当前 Deep Research 流程仍使用 SearchResult；该模型先作为 V1 标准
    documents 字段的契约，为网页、文件、数据库和记忆文档预留统一结构。
    """

    document_id: str = Field(default="", description="文档稳定标识")
    source_type: str = Field(default="web", description="来源类型：web、file、database、memory 等")
    title: str = Field(default="", description="文档标题")
    uri: str = Field(default="", description="文档 URI 或 URL")
    snippet: str = Field(default="", description="文档摘要")
    content: Optional[str] = Field(default=None, description="文档正文")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="文档元数据")
    credibility: Optional[Dict[str, Any]] = Field(default=None, description="来源可信度信息")
    status: str = Field(default="retrieved", description="文档处理状态")


class Finding(BaseModel):
    """带证据引用能力的研究发现模型。"""

    finding_id: str = Field(default="", description="发现稳定标识")
    statement: str = Field(default="", description="发现内容")
    evidence_refs: List[str] = Field(default_factory=list, description="支持证据或文档 ID")
    contradictory_evidence_refs: List[str] = Field(
        default_factory=list,
        description="相反证据或文档 ID"
    )
    confidence: Optional[float] = Field(default=None, description="发现置信度")
    reasoning_summary: Optional[str] = Field(default=None, description="推理摘要")
    status: str = Field(default="unverified", description="发现验证状态")


class ReportSection(BaseModel):
    """研究报告的一个章节。"""
    title: str = Field(description="章节标题")
    content: str = Field(description="Markdown 格式的章节内容")
    sources: List[str] = Field(default_factory=list, description="使用的来源 URL")


class Report(BaseModel):
    """ResearchOS 的统一报告模型。"""

    report_id: str = Field(default="", description="报告稳定标识")
    title: str = Field(default="", description="报告标题")
    sections: List[ReportSection] = Field(default_factory=list, description="报告章节")
    content: Optional[str] = Field(default=None, description="最终渲染后的报告内容")
    citations: List[str] = Field(default_factory=list, description="报告引用")
    version: int = Field(default=1, description="报告版本")
    status: str = Field(default="draft", description="报告状态")


class ResearchPlan(BaseModel):
    """包含查询和大纲的研究计划。"""
    topic: str = Field(description="研究主题")
    objectives: List[str] = Field(description="研究目标")
    search_queries: List[SearchQuery] = Field(description="要执行的搜索查询")
    report_outline: List[str] = Field(description="报告章节大纲")


class UsageMetrics(BaseModel):
    """运行级资源使用统计。"""

    llm_calls: int = Field(default=0, description="LLM 调用次数")
    tool_calls: int = Field(default=0, description="工具调用次数")
    input_tokens: int = Field(default=0, description="输入 Token 数量")
    output_tokens: int = Field(default=0, description="输出 Token 数量")
    total_tokens: int = Field(default=0, description="总 Token 数量")
    latency_seconds: float = Field(default=0.0, description="累计耗时")
    estimated_cost: Optional[float] = Field(default=None, description="估算成本")


class EvidenceDiagnostics(BaseModel):
    """Serializable sidecar diagnostics that never represent the top-level run error."""

    status: Literal["not_run", "disabled", "completed", "partial", "failed"] = "not_run"
    source: Literal["none", "p2_documents", "legacy_search_results_backfill"] = "none"
    analyzer_completed: bool = False
    analyzer_partial: bool = False
    aggregation_attempted: bool = False
    aggregation_completed: bool = False
    aggregation_partial: bool = False
    analyzer_errors: List[str] = Field(default_factory=list)
    aggregation_errors: List[str] = Field(default_factory=list)
    sidecar_errors: List[str] = Field(default_factory=list)


class MemoryItem(BaseModel):
    """未来 Memory Agent 使用的检索记忆项。"""

    memory_id: str = Field(default="", description="记忆标识")
    memory_type: str = Field(default="", description="记忆类型")
    content: str = Field(default="", description="记忆内容")
    relevance_score: Optional[float] = Field(default=None, description="相关性分数")
    source: Optional[str] = Field(default=None, description="记忆来源")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="记忆元数据")
    created_at: Optional[str] = Field(default=None, description="记忆创建时间")


class CriticFeedback(BaseModel):
    """未来 Reflection/Critic Agent 使用的反馈项。"""

    feedback_id: str = Field(default="", description="反馈标识")
    category: str = Field(default="", description="反馈类别")
    severity: str = Field(default="info", description="反馈严重程度")
    message: str = Field(default="", description="反馈内容")
    target: Optional[str] = Field(default=None, description="反馈目标")
    evidence_refs: List[str] = Field(default_factory=list, description="相关证据引用")
    suggested_action: Optional[str] = Field(default=None, description="建议动作")
    resolved: bool = Field(default=False, description="是否已解决")


class AgentMessage(BaseModel):
    """未来多 Agent 协作使用的可序列化消息。"""

    message_id: str = Field(default="", description="消息标识")
    sender: str = Field(default="", description="发送方")
    receiver: str = Field(default="", description="接收方")
    role: str = Field(default="", description="消息角色")
    content: str = Field(default="", description="消息内容")
    message_type: str = Field(default="", description="消息类型")
    created_at: Optional[str] = Field(default=None, description="消息时间")


class SupervisorDecision(BaseModel):
    """未来 Supervisor Agent 使用的决策占位模型。"""

    action: Optional[str] = Field(default=None, description="下一步动作")
    reason: Optional[str] = Field(default=None, description="决策原因")
    target_agent: Optional[str] = Field(default=None, description="目标 Agent")
    confidence: Optional[float] = Field(default=None, description="决策置信度")


class QualityScore(BaseModel):
    """未来 Evaluation/Critic 使用的质量评分占位模型。"""

    overall: Optional[float] = Field(default=None, description="总体质量分数")
    factuality: Optional[float] = Field(default=None, description="事实性分数")
    citation_coverage: Optional[float] = Field(default=None, description="引用覆盖度")
    completeness: Optional[float] = Field(default=None, description="完整性分数")
    coherence: Optional[float] = Field(default=None, description="连贯性分数")
    task_alignment: Optional[float] = Field(default=None, description="任务对齐分数")
    evaluator: Optional[str] = Field(default=None, description="评估者")
    passed: Optional[bool] = Field(default=None, description="是否通过")


class ResearchState(BaseModel):
    """ResearchOS V1 研究流程状态。

    新字段是未来标准契约，旧字段保留用于兼容当前 Agent、Graph、CLI、
    Web 和持久化逻辑。本阶段不通过 alias 或校验器自动同步新旧字段。
    Legacy checkpoint 保持其已存储的字段继续运行；缺失的 V1 字段不自动回填。
    """

    # State 契约版本
    state_version: int = Field(default=1, description="State 契约版本")

    # =========================================================================
    # V1 标准任务字段
    # =========================================================================

    query: str = Field(default="", description="标准化研究问题或任务")
    research_plan: Optional[ResearchPlan] = Field(
        default=None,
        description="V1 标准研究计划"
    )
    documents: List[Document] = Field(
        default_factory=list,
        description="V1 标准研究文档集合"
    )
    findings: List[Finding] = Field(
        default_factory=list,
        description="V1 带证据引用能力的研究发现"
    )
    document_analyses: List[DocumentAnalysis] = Field(
        default_factory=list,
        description="Evidence Layer 文档分析结果"
    )
    evidence: List[Evidence] = Field(
        default_factory=list,
        description="Evidence Layer 可追溯证据"
    )
    evidence_diagnostics: EvidenceDiagnostics = Field(
        default_factory=EvidenceDiagnostics,
        description="Evidence sidecar 诊断信息"
    )
    report: Optional[Report] = Field(default=None, description="V1 标准报告")
    agent_trace: List[AgentTraceEvent] = Field(
        default_factory=list,
        description="Agent 和工具执行轨迹"
    )
    usage: UsageMetrics = Field(default_factory=UsageMetrics, description="运行资源使用统计")

    # =========================================================================
    # V1 运行时字段
    # =========================================================================

    current_stage: Literal[
        "received",
        "planning",
        "memory_retrieval",
        "searching",
        "extracting",
        "synthesizing",
        "reflecting",
        "replanning",
        "reporting",
        "writing",
        "evaluating",
        "complete",
        "completed",
        "failed",
        "paused",
    ] = Field(default="planning", description="当前流程阶段")
    run_id: str = Field(default="", description="本次研究运行标识")
    status: Literal[
        "pending",
        "running",
        "completed",
        "failed",
        "paused",
        "cancelled",
    ] = Field(default="pending", description="运行状态")
    error: Optional[str] = Field(default=None, description="错误消息（如有）")
    iteration: int = Field(default=0, description="V1 迭代次数")
    execution_context: Optional[ExecutionContext] = Field(
        default=None,
        description="Runtime 持久化执行上下文；不承载业务字段",
    )
    terminal_reason: Optional[TerminalReason] = Field(
        default=None,
        description="由 Runtime 写入的终态原因；不替代 legacy error",
    )

    # =========================================================================
    # 未来扩展字段：Memory / Reflection / Multi-Agent Supervisor / Evaluation
    # =========================================================================

    retrieved_memory: List[MemoryItem] = Field(
        default_factory=list,
        description="未来 Memory Agent 检索到的记忆"
    )
    memory_ids: List[str] = Field(
        default_factory=list,
        description="未来 Memory Agent 使用的记忆 ID"
    )
    critic_feedback: List[CriticFeedback] = Field(
        default_factory=list,
        description="未来 Critic/Reflection Agent 的反馈"
    )
    agent_messages: List[AgentMessage] = Field(
        default_factory=list,
        description="未来多 Agent 协作消息"
    )
    active_agent: Optional[str] = Field(default=None, description="当前活跃 Agent")
    next_action: Optional[str] = Field(default=None, description="未来 Supervisor 决定的下一动作")
    pending_tasks: List[str] = Field(
        default_factory=list,
        description="未来 Supervisor 管理的待处理任务"
    )
    supervisor_decision: Optional[SupervisorDecision] = Field(
        default=None,
        description="未来 Supervisor 决策"
    )

    # =========================================================================
    # 兼容字段：当前 Agent、Graph、CLI、Web 仍使用这些字段
    # =========================================================================
    
    # 用户输入
    research_topic: str = Field(description="要研究的主题")
    
    # 规划阶段
    plan: Optional[ResearchPlan] = Field(default=None, description="研究计划")
    
    # 搜索阶段
    search_results: List[SearchResult] = Field(
        default_factory=list,
        description="收集到的所有搜索结果"
    )
    
    # 综合阶段
    key_findings: List[str] = Field(
        default_factory=list,
        description="从搜索结果中提取的关键发现"
    )
    
    # 报告生成阶段
    report_sections: List[ReportSection] = Field(
        default_factory=list,
        description="生成的报告章节"
    )
    
    final_report: Optional[str] = Field(
        default=None,
        description="完整的 Markdown 最终报告"
    )
    
    # 元数据
    iterations: int = Field(default=0, description="迭代次数")
    
    # 质量与指标
    quality_score: Optional[QualityScore] = Field(default=None, description="报告质量指标")
    credibility_scores: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="来源可信度分数"
    )
    
    # LLM 跟踪
    llm_calls: int = Field(default=0, description="LLM API 调用总次数")
    total_input_tokens: int = Field(default=0, description="使用的输入 Token 总数")
    total_output_tokens: int = Field(default=0, description="生成的输出 Token 总数")
    llm_call_details: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="每次 LLM 调用的详情"
    )
    
    class Config:
        arbitrary_types_allowed = True
