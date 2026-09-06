"""Flat canonical research-state contract with explicit legacy inputs.

The model never synchronizes two business representations itself. Legacy
hydration/projection is an explicit ``src.state_compat`` boundary operation.
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

    ``documents`` 是 Research Agent 的唯一来源集合。网页搜索结果在
    compatibility boundary 转换、去重并按可信度稳定排序后进入该字段。
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
    source_document_ids: List[str] = Field(
        default_factory=list,
        description="支撑该发现的 canonical Document ID（按 citation 顺序）",
    )
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


class ResearchState(BaseModel):
    """One flat State for canonical data, runtime facts, and optional sidecars.

    The canonical business contract is ``query → research_plan → documents →
    findings → report``. Legacy fields remain only for old inputs, checkpoints,
    and cache payloads; ``state_compat`` hydrates them explicitly and canonical
    values win whenever both are present.
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
    search_diagnostics: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Searcher 的只读运行诊断，不参与路由、Usage 或 Writer 输入"
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
    # 可选 Memory sidecar fields
    # =========================================================================

    retrieved_memory: List[MemoryItem] = Field(
        default_factory=list,
        description="可选 local memory 检索到的记忆"
    )
    memory_ids: List[str] = Field(
        default_factory=list,
        description="可选 local memory 使用的记忆 ID"
    )
    memory_diagnostics: Dict[str, Any] = Field(
        default_factory=dict,
        description="可选 local memory 的只读检索、hint 与写入诊断；不参与路由或 Writer 输入"
    )
    # =========================================================================
    # Legacy compatibility input fields. New runs leave these at defaults;
    # state_compat hydrates them at explicit input/checkpoint boundaries.
    # =========================================================================
    
    # 用户输入
    research_topic: str = Field(default="", description="旧版研究主题")
    
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
    credibility_scores: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="来源可信度分数"
    )
    
    # Legacy usage totals; canonical ``usage`` is authoritative for new runs.
    llm_calls: int = Field(default=0, description="LLM API 调用总次数")
    total_input_tokens: int = Field(default=0, description="使用的输入 Token 总数")
    total_output_tokens: int = Field(default=0, description="生成的输出 Token 总数")
    llm_call_details: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="每次 LLM 调用的详情"
    )
    
    class Config:
        arbitrary_types_allowed = True
