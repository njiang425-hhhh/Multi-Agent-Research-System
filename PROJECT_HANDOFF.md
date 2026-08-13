# ResearchOS / Multi-Agent Research System 项目交接文档

> 用途：供下一次 AI Agent 会话直接加载，快速恢复项目背景、关键决策、当前实现边界和后续计划。
>
> 最后更新：2026-08-10  
> 当前分支：`main`  
> 当前本地基线提交：`0bfb200` (`feat: add tested evidence layer baseline`)  
> GitHub：<https://github.com/njiang425-hhhh/Multi-Agent-Research-System>
> Git 状态：本地 `main` 与 `origin/main` 均为 `0bfb200`；P0/P1 已成功推送。`PROJECT_HANDOFF.md` 仍作为本地未跟踪交接资料。

本文档已合并以下两类信息：

- 当前工作区源码、配置契约、Git 状态和实际回归结果（事实基线）。
- `E:\Desktop\Deep Research Agent 项目上下文总结.md` 中的阶段记录和架构路线（历史补充）。

若历史总结与当前源码不一致，以当前源码和最近验证结果为准。例如：DuckDuckGo 目前仍是 legacy 路径，独立 `DuckDuckGoProvider` 尚未实现；Evidence Layer 已作为独立模块实现，但尚未接入当前 Graph。

## 1. 项目目标

项目基于 `deep-research-agent` 二次开发，最终目标是构建 ResearchOS：一个具备以下能力的自主研究 Agent 系统。

- Planning
- Tool Calling
- Long-term Memory
- Reflection
- Multi-Agent Collaboration
- Evaluation

当前阶段仍以稳定的单流程 Deep Research 系统为核心。Memory、Reflection、Supervisor、Evaluation 只完成了 State 契约预留，尚未实现业务逻辑。

## 2. 长期开发原则

后续会话和开发应继续遵守：

1. 不盲目增加功能，先确认现有实现和真实问题。
2. 优先理解源码、数据契约和运行链。
3. 保持工程质量，明确模块边界。
4. 每次修改前先分析影响范围。
5. 小步修改、小步验证、小步提交。
6. 始终保证项目可运行。
7. 不为未来能力提前实现复杂逻辑，只提供必要扩展点。
8. 不轻易修改 Graph 拓扑；涉及拓扑调整时先单独设计。
9. 保留兼容层，逐步迁移旧字段和旧运行模式。
10. **LLM 不控制 Runtime**：LLM 负责理解、规划、分析和判断；代码负责 budget、timeout、retry、去重、状态和终止条件。
11. **模块化优先**：新增能力优先建立独立 module、interface 和数据契约，不继续把职责堆入 `src/agents.py`。

## 3. 当前整体架构

```text
CLI / Chainlit Web
        |
        v
src/graph.py（LangGraph Orchestration）
        |
        +--> plan --------> ResearchPlanner
        |
        +--> search ------> ResearchSearcher
        |                       |
        |                       +--> legacy_agent
        |                       |      LLM create_agent + tools
        |                       |
        |                       +--> deterministic_v2
        |                              SearchExecutor
        |                                  |
        |                                  +--> web_search Tool
        |                                  |      |
        |                                  |      +--> TavilyProvider
        |                                  |      +--> DuckDuckGo legacy path
        |                                  |
        |                                  +--> extract_webpage_content Tool
        |
        +--> synthesize --> ResearchSynthesizer
        |
        +--> write_report -> ReportWriter
        |
        v
ResearchState（当前旧字段承载运行，新字段提供 V1 契约和未来预留）

所有 Agent
    |
    v
src/llm/factory.py
    +--> Gemini
    +--> OpenAI
    +--> DeepSeek（ChatOpenAI + DeepSeek base URL）
    +--> Ollama
    +--> llama.cpp
```

架构方向是将系统逐步分成以下层级：

1. **Interface Layer**：CLI、Chainlit。
2. **Orchestration Layer**：LangGraph 节点、路由、恢复和持久化。
3. **Agent Layer**：Planner、Searcher、Synthesizer、Writer；未来增加 Supervisor、Critic、Memory。
4. **State Contract Layer**：统一的 ResearchState、Agent Trace、Usage、Memory、Evaluation 数据模型。
5. **Runtime Service Layer**：LLM Factory、SearchExecutor、Search Provider。
6. **Tool Layer**：稳定的 LangChain Tool 接口。
7. **Observability / Evaluation Layer**：未来的 Trace、质量评分、离线评测和回归测试。

已完成的 Evidence Layer 当前以独立、可测试模块存在于 `src/evidence/`，不改变生产 Graph：

```text
SearchResult
    -> Document
    -> Result Analyzer / Evidence Analyzer
    -> DocumentAnalysis + Evidence + Finding
```

已实现：`SearchResult -> Document` adapter、`ResultAnalyzer`、Evidence 契约、Finding 契约和 deterministic Finding Aggregator；全部仅可独立调用。该层仍是后续 State V2、Memory、Reflection 和 Evaluation 的共同数据基础，尚未写入 State、接入 Synthesizer 或加入 Graph。

### 3.1 P1 Evidence Layer 关键决策

1. **独立优先**：`src/evidence/` 不导入 Graph、Agent 或 LLM Factory；P1 只建立可单测的数据与运行时边界。
2. **复用而不迁移**：复用已有 `Document` 和 `Finding`，不修改 `ResearchState`，不自动同步旧字段。
3. **显式转换**：`SearchResult -> Document` 通过 adapter 完成；URL 规范化并生成稳定 Document ID，credibility 必须显式绑定。
4. **Evidence 可追溯**：Evidence ID 由稳定输入生成；quote 必须能在 content/snippet 中定位，非法 quote 不成为可用证据。
5. **Runtime 控制权归代码**：`AnalyzerConfig` 控制文档数、文本长度、timeout、retry 与 partial；模型只返回结构化 draft，不控制循环。
6. **Finding 引用受限**：`Finding.evidence_refs` / `contradictory_evidence_refs` 只允许 Evidence ID；`FindingAggregationResult` 验证未知、重复和冲突引用。
7. **Baseline 可复现**：第一版 Finding Aggregator 不调用 LLM，按规范化 claim 聚类，并用相关性、可信度、证据强度、来源独立性与冲突比例确定性计算 confidence。

## 4. 当前 LangGraph 工作流

### 4.1 Graph 拓扑

```text
START
  |
  v
plan
  |-- error / 无搜索查询 ------------------------> END
  v
search
  |-- error / 无结果 / 结果少于 2 条 ------------> END
  v
synthesize
  |-- error / 无 key_findings -------------------> END
  v
write_report
  |
  v
END
```

Graph 定义在 `src/graph.py`，当前节点为：

| Node | Agent 方法 | 主要输入 | 主要输出 |
|---|---|---|---|
| `plan` | `ResearchPlanner.plan` | `research_topic` | `plan` |
| `search` | `ResearchSearcher.search` | `plan.search_queries` | `search_results`、`credibility_scores`、`error` |
| `synthesize` | `ResearchSynthesizer.synthesize` | `search_results` | `key_findings` |
| `write_report` | `ReportWriter.write_report` | `plan`、`key_findings`、搜索资料 | `report_sections`、`final_report` |

### 4.2 关键拓扑决策

- 当前 Graph 拓扑没有为了 DeepSeek、Searcher V2 或 Provider Layer 而改变。
- Agent 方法直接作为 LangGraph Node 注册。
- 每个节点返回 State patch，由 LangGraph 合并回 `ResearchState`。
- 当前是确定的顺序流程，没有并行子图和 Supervisor 动态路由。
- `error` 是各路由的统一提前终止信号。
- Search 节点至少需要 2 条 `search_results` 才会进入 Synthesizer。

## 5. ResearchState V1 决策

State 定义位于 `src/state.py`，`state_version=1`。

### 5.1 核心决策

- 保留所有旧字段，不破坏现有 Agent 和 Graph。
- 新增标准字段和未来扩展字段，并提供安全默认值。
- 不使用 Pydantic alias 自动同步新旧字段。
- 当前运行链仍主要读写旧字段；新字段目前不是旧字段的自动镜像。
- 后续迁移必须逐节点进行，不能假设 `query` 与 `research_topic` 等字段已经同步。

### 5.2 当前新增标准字段

| 字段 | 目的 | 当前状态 |
|---|---|---|
| `state_version` | State 契约版本 | 已启用，固定为 V1 默认值 |
| `query` | 标准研究问题 | 已建模，尚未全面替代旧字段 |
| `research_plan` | 标准研究计划 | 已建模，尚未全面替代 `plan` |
| `documents` | 标准文档集合 | 已建模，尚未全面替代搜索结果 |
| `findings` | 结构化研究发现 | 已建模，尚未全面替代 `key_findings` |
| `report` | 结构化报告 | 已建模，尚未全面替代旧报告字段 |
| `agent_trace` | Agent/Tool 执行轨迹 | 已建模，尚未系统写入 |
| `usage` | 统一资源使用统计 | 已建模，旧 token 字段仍保留 |
| `current_stage` | 当前工作阶段 | 已建模并可用于运行状态 |
| `run_id` | 一次运行的标识 | 已建模 |
| `status` | 运行状态 | 已建模 |
| `error` | 统一错误文本 | 当前 Graph 已使用 |
| `iteration` | 新版迭代计数 | 已建模，旧 `iterations` 仍保留 |

### 5.3 未来能力预留字段

这些字段只有数据模型，没有对应 Agent 或工作流逻辑：

- Memory：`retrieved_memory`、`memory_ids`
- Reflection / Critic：`critic_feedback`、`quality_score`
- Multi-Agent：`agent_messages`、`active_agent`
- Supervisor：`next_action`、`pending_tasks`、`supervisor_decision`

### 5.4 旧字段兼容映射

| 旧字段（当前活跃） | 新字段（目标契约） | 当前同步状态 |
|---|---|---|
| `research_topic` | `query` | 未自动同步 |
| `plan` | `research_plan` | 未自动同步 |
| `search_results` | `documents` | 需要显式转换，未自动同步 |
| `key_findings` | `findings` | 需要显式转换，未自动同步 |
| `report_sections` + `final_report` | `report` | 需要显式转换，未自动同步 |
| `iterations` | `iteration` | 未自动同步 |
| `llm_calls`、token 字段、`llm_call_details` | `usage` | 尚未统一写入 |

旧字段还包括：

- `research_topic`
- `plan`
- `search_results`
- `key_findings`
- `report_sections`
- `final_report`
- `iterations`
- `credibility_scores`
- `llm_calls`
- `total_input_tokens`
- `total_output_tokens`
- `llm_call_details`

## 6. LLM Provider 架构

### 6.1 已完成

- `get_llm()` 已从 `src/agents.py` 抽离到 `src/llm/factory.py`。
- Agent 构造接口仍是 `llm: BaseChatModel`，调用方式没有变化。
- 已支持：`gemini`、`openai`、`deepseek`、`ollama`、`llamacpp`。
- DeepSeek 使用 LangChain `ChatOpenAI`，通过 `DEEPSEEK_BASE_URL` 接入 OpenAI-compatible API。
- Provider 配置和校验集中在 `src/config.py`。

### 6.2 保持的边界

- Agent 不负责实例化具体 Provider。
- Graph 不感知具体 LLM Provider。
- 当前没有模型级 fallback、路由或负载均衡。
- 不应把 DeepSeek 特有逻辑重新放回 Agent。

## 7. Searcher 稳定性与 Searcher V2

### 7.1 Legacy Searcher

旧模式仍保留为 `SEARCHER_MODE=legacy_agent`：

- 使用 LLM Agent Loop。
- 使用现有 Tool Calling 接口。
- 保留 recursion limit、timeout、低重试次数和 Prompt 停止规则。
- 这些限制是旧 Agent Loop 防止重复搜索、空结果循环和长时间阻塞的安全边界。

已做过两轮稳定性优化：

- Searcher 默认 `max_retries=1`。
- 增加 Agent recursion limit。
- 增加执行 timeout。
- timeout 和异常转换为明确的 SearchError。
- Prompt 增加空结果停止、重复搜索限制、最大搜索/提取次数和信息充分即结束规则。

### 7.2 Deterministic Searcher V2

新模式为 `SEARCHER_MODE=deterministic_v2`，目标是：

```text
ResearchPlan.search_queries
        |
        v
Deterministic SearchExecutor
  - 固定搜索预算
  - 固定提取预算
  - URL 去重
  - timeout
  - retry
  - partial result 策略
        |
        v
兼容旧 Agent 返回契约
  search_results
  credibility_scores
  error
```

`ResearchSearcher.search()` 的外部接口没有改变，因此 Graph 无需修改。

### 7.3 Runtime 与 LLM 的职责边界

Searcher V2 的核心决策不是简单“减少 Agent 搜索次数”，而是收回搜索循环的运行控制权：

| 职责 | 归属 |
|---|---|
| 查询理解、研究规划、结果含义分析、信息充分性判断 | LLM / Agent |
| search/extract 次数、timeout、retry、URL 去重、错误处理、结束条件 | 确定性 Runtime |

当前 `SearchExecutor` 已实现 Runtime 部分；独立的 `ResultAnalyzer` 已实现文档分析 Runtime，但尚未接入生产链。因此当前生产运行中的信息综合仍主要发生在后续 Synthesizer 中。

### 7.4 SearchConfig 当前默认值

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `max_search_times` | 3 | 一次 Executor 运行允许的搜索调用预算 |
| `max_extract_times` | 4 | 网页正文提取预算 |
| `total_timeout_seconds` | 90 | Executor 总超时 |
| `search_retry_times` | 0 | 单次搜索额外重试次数 |
| `extract_retry_times` | 0 | 单次提取额外重试次数 |
| `allow_partial_results` | `true` | 超时或部分失败时允许返回已获得结果 |

这些参数属于 Search Runtime Config，应保留；它们不是临时补丁。

## 8. Search Provider Layer

### 8.1 已完成结构

```text
src/search/providers/
    base.py       # SearchProvider 抽象接口
    models.py     # ProviderSearchResult
    errors.py     # 结构化 Provider 异常
    factory.py    # SearchProviderFactory
    tavily.py     # TavilyProvider
```

统一 `ProviderSearchResult` 包含：`query`、`title`、`url`、`snippet`、`provider` 和 `metadata`。Provider 专有字段留在 `metadata`，SearchExecutor 不依赖它们。

结构化异常包括：

- `SearchProviderError`
- `TimeoutError`
- `AuthError`
- `RateLimitError`
- `UnavailableError`
- `CircuitOpenError`

Tavily HTTP 错误映射：

- 401 / 403 -> `AuthError`
- 429 -> `RateLimitError`
- 请求超时 -> Provider `TimeoutError`
- 5xx -> `UnavailableError`

### 8.2 Tool Adapter 决策

`src/utils/tools.py` 中的 `web_search`：

- Tool 名称不变。
- 输入参数不变。
- 输出仍是兼容旧系统的 `List[dict]`。
- `ProviderSearchResult` 在 Tool Adapter 转换为旧 `SearchResult` 格式。
- Tavily 正常无结果返回 `[]`。
- Tavily Provider 异常向上传播，不再伪装成空结果。
- SearchExecutor 不感知具体 Provider，因此保持不变。

### 8.3 当前未完成边界

- 暂未实现 Provider fallback。
- DuckDuckGo 尚未迁移为独立 `DuckDuckGoProvider`。
- Factory 将 `duckduckgo` 识别为支持名称，但默认没有注册 DuckDuckGo builder。
- `web_search` 对 DuckDuckGo 仍走旧 `web_utils.py` 路径；旧路径异常仍可能被转换为 `[]`。
- 暂未实现 circuit breaker，只有异常契约预留。

## 9. 循环导入修复

曾出现调用链：

```text
src.utils.tools
  -> src.search.providers
  -> src.search.__init__
  -> src.search.executor
  -> src.utils.tools
```

修复方案是保持 `src/search/__init__.py` 轻量化，并通过 `__getattr__` 延迟导出：

- `SearchConfig`
- `SearchExecutor`
- `SearchExecutionResult`
- `SearchExecutionStats`

不要重新在 package `__init__.py` 中直接 eager import Executor，否则会恢复循环依赖。

## 10. 已完成阶段汇总

| 阶段 | 状态 | 主要结果 |
|---|---|---|
| 架构分析 | 完成 | 完成目录、入口、Graph、State、Agent、Tool、Prompt 和执行流分析 |
| Phase 1：State V1 | 完成 | 建立新版契约和未来预留字段，保留全部旧字段 |
| Phase 2：LLM Factory | 完成 | Provider 实例化从 Agent 抽离，Agent 接口不变 |
| Phase 3：DeepSeek | 完成 | 通过 `ChatOpenAI` 接入 DeepSeek compatible API |
| Phase 4：Legacy Searcher 稳定性 | 完成 | recursion limit、timeout、低 retry、Prompt 停止规则 |
| Phase 5：SearchExecutor V2 | 完成 | 确定性预算、超时、重试、URL 去重和 partial result |
| Phase 6：Provider Contract | 完成 | Provider 接口、统一结果模型、结构化异常和 Factory |
| Phase 7：TavilyProvider | 完成 | 异步调用、结果转换和 HTTP 错误映射 |
| Phase 8：Tool Adapter | 完成 | `web_search` 接入 Tavily，同时保持旧 Tool 契约 |
| Phase 9：Import 修复 | 完成 | `src.search` 延迟导出，解除循环导入 |
| Phase 10：Workflow 回归 | 完成 | DeepSeek + Tavily + deterministic_v2 全流程成功 |
| P0：最小测试安全网 | 完成 | State、LLM Factory、SearchConfig、SearchExecutor、Tavily Provider 与 Tool Adapter 的 fake 测试 |
| P1.1：Evidence 数据契约 | 完成 | `DocumentAnalysis`、`Evidence`、`AnalysisResult`，复用既有 `Document` / `Finding` |
| P1.2：Document Adapter | 完成 | `SearchResult -> Document`、URL 规范化、稳定 Document ID、显式 credibility 绑定 |
| P1.3：ResultAnalyzer | 完成 | 注入式 Fake Model 协议、有界文本、quote 校验、稳定 Evidence ID、partial failure |
| P1.4：Finding baseline | 完成 | Finding 契约与 deterministic rule-based claim 聚类、Evidence 引用和 confidence |
| P0/P1 发布提交 | 完成并已推送 | `0bfb200`；README 更新，`LICENSE` 已按用户要求删除 |

当前系统已经从“不受控的 LLM 搜索循环”演进为“稳定 Research Pipeline + 可控 Search Runtime + 独立 Evidence Layer”。下一阶段的主线是单独设计 Evidence Layer 的兼容接入，而不是直接改动生产 Graph。

## 11. 验证记录

### 11.1 完整 Workflow 最近一次成功回归

测试任务：`Research the development trend of LangGraph framework`

环境：

- `MODEL_PROVIDER=deepseek`
- `SEARCH_PROVIDER=tavily`
- `SEARCHER_MODE=deterministic_v2`

结果：

| 阶段 | 结果 | 耗时 / 输出 |
|---|---|---|
| Planner | PASS | 18.66 秒；3 个研究目标、3 个搜索查询、8 个报告章节 |
| Searcher | PASS | 14.61 秒；3 次 search、4 次 extract、9 条结果 |
| Synthesizer | PASS | 39.04 秒；15 条 findings |
| Writer | PASS | 172.06 秒；8 个章节 |
| Final State | PASS | 生成 `final_report`，长度约 20,635 字符 |
| 总耗时 | PASS | 约 246.54 秒 |

最终 `current_stage=complete`，没有 error。

Writer 耗时约占全流程 69.8%，是当前最明显的性能瓶颈。可能原因包括输入上下文较大、Prompt 要求较长以及一次生成完整报告。该结论只用于后续设计，不授权当前直接拆分 Writer 或修改 Graph。

### 11.2 上传前静态验证

- P0/P1 测试集：`113 passed, 1 warning`。
- 全部测试使用 fake 输入；不调用真实 LLM、Tavily、DuckDuckGo 或网页。
- 测试覆盖 State、LLM Factory、Search Runtime、Provider、Tool Adapter、Evidence Layer 和 Finding baseline。
- 唯一 warning 是既有 `ResearchState` class-based Pydantic `Config` 弃用提示。
- 当前 `main` 与 `origin/main` 均指向 `0bfb200`，P0/P1 已成功推送。
- `PROJECT_HANDOFF.md` 保持本地未跟踪，未纳入 `0bfb200` 提交；本次更新同样尚未提交。

注意：系统默认 Python 环境可能没有安装 `ddgs`；项目 `.venv` 中依赖完整。后续应激活 `.venv` 或按 `requirements.txt` / `pyproject.toml` 重新安装依赖。

## 12. 重要文件及修改记录

| 文件 | 当前职责 / 已完成修改 |
|---|---|
| `src/state.py` | ResearchState V1；新增标准字段、运行字段和未来能力模型；保留全部旧字段 |
| `src/graph.py` | 四节点 LangGraph、条件路由、运行和持久化入口；拓扑未因本轮重构改变 |
| `src/agents.py` | 四类 Agent；Searcher legacy 稳定性控制；Searcher V2 模式切换与适配 |
| `src/config.py` | LLM/Search Provider 配置、DeepSeek 配置、Searcher 模式和运行参数 |
| `src/llm/factory.py` | 统一创建 Gemini/OpenAI/DeepSeek/Ollama/llama.cpp ChatModel |
| `src/prompts/searcher.py` | Legacy Agent Loop 的有限预算和停止规则 |
| `src/search/config.py` | Search Runtime Config |
| `src/search/models.py` | SearchExecutor 结果和统计模型 |
| `src/search/executor.py` | 确定性搜索、去重、预算、timeout、retry、partial results |
| `src/search/__init__.py` | 延迟导出，避免循环导入 |
| `src/search/providers/base.py` | SearchProvider 抽象接口 |
| `src/search/providers/models.py` | ProviderSearchResult 契约 |
| `src/search/providers/errors.py` | Provider 结构化异常体系 |
| `src/search/providers/factory.py` | Provider 注册和创建；当前默认仅注册 Tavily |
| `src/search/providers/tavily.py` | Tavily 调用、响应转换和错误映射 |
| `src/utils/tools.py` | 保持 `web_search` Tool 契约；Tavily Provider 适配到旧返回结构 |
| `src/utils/web_utils.py` | DuckDuckGo 旧实现和网页提取；后续需要拆分迁移 |
| `src/evidence/models.py` | `DocumentAnalysis`、`Evidence`、`AnalysisResult`；不复制 `Document` |
| `src/evidence/adapters.py` | `SearchResult -> Document`、URL 规范化、稳定 ID、显式 credibility 绑定 |
| `src/evidence/config.py` | Analyzer 的 document/text/timeout/retry/partial Runtime Config |
| `src/evidence/protocols.py` / `drafts.py` | 注入式 `AnalyzerModel` 协议和结构化分析草稿契约 |
| `src/evidence/service.py` | 独立 `ResultAnalyzer`；不调用 Search Tool、不写 State、不生成 Finding |
| `src/evidence/finding_models.py` | `FindingDraft`、`FindingAggregationResult`；强制 Evidence ID 引用 |
| `src/evidence/aggregation.py` | 无 LLM 的 deterministic rule-based Finding Aggregator |
| `tests/` | P0 安全网与 P1 Evidence Layer 测试，共 113 项 |
| `.env.example` | 无密钥的 DeepSeek、Tavily、Searcher V2 配置模板 |
| `README.md` | 当前架构、安装、配置、运行方法和实现边界 |
| `requirements.txt` | 当前运行依赖 |
| `pyproject.toml` | 项目元数据和依赖契约 |
| `.gitignore` | 排除 `.env`、虚拟环境、IDE、缓存和运行输出 |

`LICENSE` 已按用户要求删除。当前 Git 历史为：

```text
5620e70 Initial ResearchOS architecture with deterministic search
0bfb200 feat: add tested evidence layer baseline
```

`0bfb200` 已推送。后续应继续保持小步提交纪律；交接文档是否纳入后续提交由用户单独决定。

## 13. 当前安全配置约定

- 真实 Key 只存在本地 `.env`，禁止提交。
- 可提交配置模板是 `.env.example`。
- 下一次会话不得打印或复制真实 `DEEPSEEK_API_KEY`、`TAVILY_API_KEY`。
- 推荐运行组合：

```dotenv
MODEL_PROVIDER=deepseek
SEARCH_PROVIDER=tavily
SEARCHER_MODE=deterministic_v2
```

- 具体模型名和 Key 以本地 `.env` 为准。

## 14. 待办事项与推荐优先级

### P0：最小工程安全网（完成）

已建立 113 项 fake-only pytest 回归测试，覆盖既有 State、LLM Factory、Search Runtime、Tavily Provider、Tool Adapter，以及 P1 Evidence Layer。下一步可评估简单 CI，但不要在 CI 前重构业务代码。

### P1：独立 Result Analyzer / Evidence Layer（基础完成）

已完成独立模块，当前不修改生产数据流：

目标数据流：

```text
SearchExecutor
    -> SearchResult[]
    -> Document[]
    -> Result Analyzer
         - relevance_score
         - credibility_score
         - key_points
         - evidence
    -> DocumentAnalysis[] / Evidence[] / Finding[]
    -> FindingAggregationResult
```

当前边界：ResultAnalyzer 只分析既有 Document；不拥有 search/extract 控制权，不调用 Provider，不生成 Finding。Finding baseline 则只消费 Evidence 并确定性聚类。P1 的下一步不是继续扩功能，而是单独设计兼容接入方案与 LLM structured-output adapter；Graph 变更必须另行批准。

### P2：逐步激活 ResearchState V1 / State V2 迁移

该阶段应与 Result Analyzer 的数据模型衔接，按节点小步迁移，每次保持旧字段双写：

1. 入口同时写 `research_topic` 和 `query`。
2. Planner 同时写 `plan` 和 `research_plan`。
3. Searcher/Analyzer 将 `SearchResult` 显式转换为 `Document`，同时保留 `search_results`。
4. Analyzer/Synthesizer 同时写 `key_findings` 和结构化 `findings`。
5. Writer 同时写旧报告字段和 `report`。
6. 将 LLM tracker 汇总到 `usage`，同时保留旧 token 字段。
7. 节点稳定后才讨论废弃旧字段；不能直接删除。

不要使用 Pydantic alias 做同步。同步应由明确的 adapter/helper 或节点输出完成，便于观察和测试。

### P3：Agent Trace 与 Evaluation 基础

1. 让现有节点真实写入 `current_stage`、`status` 和 `run_id`。
2. 用 `agent_trace` 记录 Agent/Tool/LLM 开始结束、latency、token、error 和 runtime budget。
3. 定义固定离线 Evaluation 任务集和输入输出，不先增加 Critic Agent。
4. 至少记录成功率、总耗时、各节点耗时、搜索/提取次数、有效来源数和报告完整度。
5. `quality_score` 和 `critic_feedback` 在评测标准明确后再启用。

### P4：完成 Search Provider 架构闭环（可与 P1 设计并行）

1. 将 DuckDuckGo 从 `web_utils.py` 迁移成独立 `DuckDuckGoProvider`。
2. 让 `web_search` 对所有 Provider 统一走 Factory。
3. 保证 `no_results` 与 `provider_error` 在所有 Provider 中可区分。
4. 在单 Provider 稳定后，再设计可配置 fallback；不要直接写死 Tavily -> DuckDuckGo。
5. fallback 应放在 Provider/Provider Router 层，不应侵入 SearchExecutor 或 Graph。
6. Circuit Breaker 只在有真实失败数据和监控需求后实现。

### P5：Writer 性能优化

当前 Writer 一次生成完整报告，占最近一次端到端耗时约 70%。目标架构可评估：

```text
Report Planner
    -> Section Writer（按章节）
    -> Report Merger
    -> Final Report
```

实现前必须先回答：

- 章节是否并行，如何控制总 token 和并发？
- 跨章节事实、术语和引用如何保持一致？
- Section 失败时是否允许 partial report？
- Writer Supervisor 是否真的必要，还是确定性章节调度器更合适？

优先做耗时和 token profiling，再决定拆分方式。不要仅凭一次耗时直接重构。

### P6：Memory / Reflection / Supervisor

推荐实现顺序：

1. **Memory retrieval adapter**：先只读检索，不改变 Graph 决策。
2. **Critic / Reflection**：先作为报告后的评估节点，不立即形成无限反思循环。
3. **Supervisor**：最后引入动态路由，必须有 `max_iterations`、`pending_tasks` 和终止协议。
4. **Multi-Agent Collaboration**：通过结构化 `agent_messages` 和 `supervisor_decision` 传递，不依赖自由文本隐式控制。

在实现这些能力前，先单独设计 Graph V2；不要直接改当前生产拓扑。

## 15. 已知风险和技术债

1. 新旧 State 字段目前不会自动同步，误读新字段可能得到空默认值。
2. Graph 的 Search 路由要求至少 2 条结果；partial result 只有 1 条时会提前结束。
3. Evidence Layer 已独立实现，但尚未接入 Graph、State、Synthesizer 或 Writer；生产运行仍只使用旧 `search_results -> key_findings` 链。
4. `ResultAnalyzer` 当前只支持注入式 `AnalyzerModel` / Fake Model；实际 LangChain structured-output adapter 尚未实现。
5. Finding baseline 仅按规范化 claim 的 deterministic 规则聚类；尚未实现语义聚类或 LLM Finding Aggregator。
6. DuckDuckGo 仍在旧路径，异常可能被吞掉并表现为空列表。
7. Provider fallback 尚未实现，Tavily 故障会直接影响搜索链。
8. 已有 113 项测试，但尚未建立 CI。
9. Writer 是完整流程中耗时最长的阶段，最近一次约占总耗时 69.8%；后续可评估章节化生成，但当前不要贸然优化。
10. Legacy Searcher 仍依赖 LLM Agent Loop，只作为兼容模式保留。
11. 部分旧源码存在行尾空格，尚未做统一格式化；不要把全项目格式化混入功能提交。
12. Windows PowerShell 控制台可能显示中文日志乱码；判断前先确认终端编码，不要直接认定源文件损坏。
13. 当前 Provider Factory 的 `SUPPORTED_PROVIDERS` 包含 DuckDuckGo，但默认 builder 只注册 Tavily，这是迁移中的刻意状态。
14. Result Analyzer 接入、Writer 拆分和 Supervisor 都会改变数据流或拓扑，必须分别设计，不能合并成一次大型重构。
15. `PROJECT_HANDOFF.md` 是本地未跟踪资料；它不会随代码提交自动同步，后续如要纳入版本控制应单独决定并提交。

## 16. 下一次会话建议起点

下一次会话开始时建议执行：

```powershell
git status --short --branch
git log -3 --oneline --decorate
& .\.venv\Scripts\python.exe -m pip check
& .\.venv\Scripts\python.exe -c "from src.utils.tools import web_search; from src.search.executor import SearchExecutor; from src.graph import run_research; print('core imports PASS')"
```

然后阅读：

1. `PROJECT_HANDOFF.md`
2. `README.md`
3. 与当前任务直接相关的源码文件

推荐下一项实际工作：**单独设计 Evidence Layer 的兼容接入方案（State 双写、旧字段回退、提前终止与 partial 行为）；第一步仍不修改 Graph。**

## 17. 下一次会话可直接使用的上下文提示

```text
请先读取项目根目录 PROJECT_HANDOFF.md，并以其中的当前架构、实现边界、风险和待办优先级为准。

继续遵守：先分析、保持兼容、小步修改、每步验证、不直接大规模重构。

当前生产链是：
Planner -> Searcher -> Synthesizer -> Writer

当前推荐运行模式是：
DeepSeek + Tavily + deterministic_v2

Memory、Reflection、Supervisor、Evaluation 只有 State 预留字段，尚未实现。
Graph 当前不得在没有单独设计和确认的情况下修改。

P0 和 P1 独立模块已完成并有 113 项 fake-only pytest 测试：
- `src/evidence/` 已包含 DocumentAnalysis、Evidence、SearchResult adapter、ResultAnalyzer、Finding 契约和 deterministic Aggregator。
- Evidence Layer 尚未接入 Graph、State、Synthesizer 或 Writer。
- `0bfb200` 已在本地 `main` 和 `origin/main`；`PROJECT_HANDOFF.md` 未跟踪且未纳入该提交。

下一项工作应先设计 P2 兼容接入：明确 `search_results -> documents`、`key_findings -> findings` 的显式双写和回退策略，先提出设计与测试方案，不要直接把 analyze 节点插入当前 Graph。
```
