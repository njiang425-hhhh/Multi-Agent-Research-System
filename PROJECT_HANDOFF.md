# ResearchOS / Multi-Agent Research System 交接文档

> 用途：供下一次 AI 会话直接加载。本文件是项目的唯一交接基线；如历史提交、旧讨论或旧测试记录与本文冲突，以本文和当前源码为准。
>
> 最后更新：2026-08-15
> 分支：`main`
> 当前提交：本次提交 `feat: complete runtime lifecycle`（待推送至 `origin/main`）
> 推荐真实运行组合：DeepSeek + Tavily + `deterministic_v2`

## 0. 当前事实快照

- 生产 Graph 拓扑未变：`Planner -> Searcher -> Synthesizer -> Writer`。
- legacy fields 仍是生产兼容主链：`search_results`、`credibility_scores`、`key_findings`、`report_sections`、`final_report`。Writer 仍只消费 legacy 主链。
- **P2 COMPLETE：** 六项 ResearchState V1 业务数据迁移均已完成，全部通过显式双写保持 legacy 主链不变。
- `EVIDENCE_ANALYZER_ENABLED=false` 是默认值。Evidence 是 Synthesizer 内部 protected optional sidecar；关闭时不创建 Evidence 专用 LLM、不运行 Pipeline，行为与 P2.2a 基线等价。
- **P3 COMPLETE：** P3.1 Runtime Lifecycle、P3.2a append-only Agent Trace 和 P3.2b deterministic Offline Evaluation 均已 closure；Graph 和业务语义未改。
- 最新 fake-only 全量回归：**218 passed, 2 warnings**（既存 Pydantic class-based Config deprecation warnings）。
- Stage 2 最小真实 DeepSeek Evidence 验证 PASS；Stage 3 Planner -> Searcher -> Synthesizer 的真实生产验证 PASS。Writer 完整验证未纳入此轮，原因是独立的串行 DeepSeek 延迟问题。

## 1. 项目目标与开发原则

项目目标是将现有 Deep Research 工作流渐进演化为 ResearchOS：稳定的研究流水线，并为后续 Memory、Reflection、Supervisor、Evaluation 和多 Agent 协作提供边界清晰的数据/运行时基础。

当前优先级是稳定生产流水线和渐进式 State 迁移，不提前实现未来 Agent。

必须持续遵守：

1. 小步设计、小步修改、小步验证；先检查当前实现再改动。
2. 未单独设计、确认和验证前，不修改 Graph 拓扑。
3. 不删除 legacy fields；新旧字段仅通过显式 adapter 或 State patch 双写。
4. 禁止用 Pydantic alias 或 validator 自动同步字段。
5. LLM 负责理解、规划、分析和生成；Runtime 代码负责 timeout、retry、budget、去重、partial 和终止条件。
6. 新能力优先放在独立 module/service 层，不把复杂 orchestration 堆入 `src/agents.py`。
7. 真实 API Key 只存在本地 `.env`，绝不打印、复制或提交。

## 2. 当前生产架构

```text
CLI / Chainlit
    |
    v
LangGraph: src/graph.py
    |
    +--> plan --------> ResearchPlanner
    |
    +--> search ------> ResearchSearcher
    |                      |
    |                      +--> legacy_agent
    |                      \--> deterministic_v2 -> SearchExecutor -> Provider/Tools
    |
    +--> synthesize --> ResearchSynthesizer
    |                      |
    |                      +--> legacy synthesis -> key_findings
    |                      +--> compatibility findings
    |                      \--> optional EvidenceSidecarService (flag controlled)
    |
    \--> write_report -> ReportWriter (legacy inputs only)
```

Graph 路由保持：`START -> plan -> search -> synthesize -> write_report -> END`；任一节点 error 会提前结束，Searcher 无足够结果、Synthesizer 无 `key_findings` 也会提前结束。

Evidence sidecar 只在 legacy synthesis 成功且 `key_findings` 非空后调用。其失败不写顶层 `error`、不调用 `emit_error()`、不触发 synthesis retry、不阻断 Writer、不改变 Graph 路由。

## 3. ResearchState V1 迁移状态

`src/state.py` 的 V1 契约已建立，迁移以逐节点、显式双写进行。

| 映射 | 当前状态 | 说明 |
|---|---|---|
| `research_topic -> query` | P2 已完成 | Web 与 Graph 入口显式双写；Agent 优先读取 V1 字段并保留 legacy fallback。 |
| `plan -> research_plan` | P2 已完成 | Planner 成功路径显式双写，不改变 legacy `plan` 消费者。 |
| `search_results -> documents` | P2 已完成 | Searcher 在可信度过滤后显式双写。 |
| `key_findings -> findings` | P2 已完成 | 先写 compatibility projection；P2.2b 可安全替换为 evidence-backed findings。 |
| `report_sections/final_report -> report` | P2 已完成 | Writer 继续只消费 legacy 输入，成功 patch 显式写入确定性 Report projection。 |
| legacy tracking -> `usage` | P2 已完成 | 各成功节点从最终 legacy totals 重建 UsageMetrics；Evidence delta 先合并 legacy totals。 |
| 运行时生命周期字段 | P3 已完成 | P3.1 的 `run_id`、`status`、`iteration`、`current_stage` ownership 保持不变；`agent_trace` 是独立、append-only 的 observability sidecar；Offline Evaluation 只读其结果。 |

Evidence 最小持久化字段已启用：

| 字段 | 作用 |
|---|---|
| `document_analyses` | sidecar 的 `DocumentAnalysis[]`。 |
| `evidence` | 可追溯的 `Evidence[]`。 |
| `evidence_diagnostics` | sidecar 状态、analyzer/aggregation/sidecar 分类错误。 |

旧 checkpoint 若 `documents` 为空而存在 `search_results`，sidecar 可显式 backfill Documents，且 `credibility=None`、diagnostics source 为 `legacy_search_results_backfill`。二者都为空时安全返回 `not_run`/fallback。

Web 入口显式将同一 topic 写入 `research_topic` 和 `query`。Legacy checkpoint 可继续沿既有 legacy 读取路径运行；恢复时不进行通用 hydration，也不自动回填缺失的历史 V1 字段。上述 sidecar 内部的按需 Documents projection 是既有节点行为，不是 checkpoint hydration adapter。

## 4. Search Runtime / Provider

- `legacy_agent`：保留用于兼容，使用 LLM Agent loop 和既有 tools。
- `deterministic_v2`：推荐生产模式；`SearchExecutor` 负责固定 search/extract budget、URL 去重、timeout、retry、partial results 和结构化错误处理。

Searcher 对 Graph 保持 legacy 输出契约，并执行 P2.1 双写：

```text
filtered (SearchResult, credibility) pairs
    -> search_results + credibility_scores   # 原内容/排序不变
    -> scored_search_results_to_documents()
    -> documents                              # 可按 normalized URL 去重
```

credibility 必须转换时显式绑定到对应 SearchResult；不得按两个独立列表的索引猜测。无效 URL 可转为 `invalid_source` Document，但不得改变 legacy `search_results`。

`src/search/providers/` 包含 Provider contract、统一结果/错误模型、factory 和 TavilyProvider。DuckDuckGo 仍为 legacy 路径；provider fallback 和 circuit breaker 尚未实现。后续只能在 Provider 层设计，不能侵入 Graph/SearchExecutor。

## 5. Evidence Layer 当前架构

```text
documents
  -> EvidenceSidecarService
  -> EvidencePipeline
       -> ResultAnalyzer (AnalyzerConfig owns budget/timeout/retry/partial)
       -> AnalysisResult: DocumentAnalysis[] + Evidence[]
       -> RuleBasedFindingAggregator (deterministic, non-LLM)
       -> EvidencePipelineResult
  -> EvidenceSidecarResult
  -> protected Synthesizer State patch merge
```

### 生命周期

1. 原有 synthesis LLM 成功，得到 `key_findings`。
2. 无条件生成 P2.2a compatibility `findings`。
3. 构造原有成功 patch。
4. flag disabled：写 `evidence_diagnostics.status="disabled"` 后返回；绝不创建 production Evidence LLM。
5. flag enabled：在独立 protected boundary 懒创建 production sidecar 并运行。
6. 合并 analyses/evidence/diagnostics、必要 checkpoint backfill documents 和 tracking delta。
7. 仅满足采用规则时替换 compatibility findings；否则保留它们。

### 关键 invariant

- **Compatibility first：** compatibility findings 永远先生成；Evidence 不得修改 `key_findings`。
- **安全采用：** evidence-backed finding 必须至少有一个 Evidence ref，所有 refs 必须存在于本次 persisted evidence，且 diagnostics 与 partial policy 允许采用；否则 fallback。
- **错误隔离：** Analyzer、structured parse/provider、Aggregator、sidecar factory 和 runtime error 都只进入 `evidence_diagnostics`，不会写顶层 `error`。
- **确定性 grounding：** Draft 的 `source_start/source_end` 是不可信 transient metadata。ResultAnalyzer 对原始 source text 做 strict unique exact quote search；0 次或多次匹配均 reject。仅唯一匹配时 runtime 计算 persisted `Evidence.source_start/source_end`。禁止 fuzzy、trim、大小写/Unicode/空白规范化、自动修 quote；LLM offset 不能消除重复 quote。
- **Evidence ID：** 使用最终 runtime span；相同 document/quote/claim/relation 的正确、错误或空 Draft offsets 必须得到相同 span 与 Evidence ID。
- **Runtime ownership：** AnalyzerConfig 控制 max documents、max chars、timeout、retry 和 partial；LLM 不搜索、不控制循环。Aggregator 不计 LLM call。

### DeepSeek Structured Output 与 tracking

生产 adapter 使用：

```python
model.with_structured_output(
    DocumentAnalysisDraft,
    method="function_calling",
    include_raw=True,
)
```

- 不使用 `strict=True`，不在 Agent/Pipeline 引入 Provider 分支，也没有多模式 structured-output 配置。
- DeepSeek thinking mode 与固定 tool choice 不兼容。仅 Evidence production factory 的专用 LLM 用 `get_llm(extra_body={"thinking": {"type": "disabled"}})` 关闭 thinking；Planner/Searcher/Synthesizer/Writer 保持原设置。
- 每次实际 Analyzer invocation（成功、structured parse/provider failure 和 retry）都产生 serializable call record。优先 raw provider usage；不可用时估算或标记 unavailable，禁止伪造 provider tokens。
- Sidecar 返回 LLM calls/tokens/details delta；Synthesizer 先对 legacy tracking append/increment，再从最终 totals 显式重建 `usage`，避免重复计数。

## 6. P2 COMPLETE 记录

### P2.1：`search_results -> documents`

- 已完成，兼容 `legacy_agent` 和 `deterministic_v2`。
- 在可信度过滤后显式双写最终有效结果，不改变 `search_results` 或 `credibility_scores` 的旧行为。
- 复用 `src/evidence/adapters.py`；Document 可按 normalized URL 去重，使用稳定 Document ID。

### P2.2a：`key_findings -> compatibility findings`

- 已完成，转换逻辑在 `src/evidence/compat.py`。
- `statement` 保留原文本与顺序；finding_id 为稳定 deterministic hash，重复文本基于稳定位置获得唯一 ID。
- `evidence_refs=[]`、`contradictory_evidence_refs=[]`、`confidence=None`、`status="unverified"`；不生成虚假 Evidence ID。
- Synthesizer 失败或空 `key_findings` 时保持原有错误与提前终止语义。

### P2.2b：Evidence-aware Synthesis

- 已完成：production LangChain analyzer adapter、独立 pipeline、contracts/state diagnostics、sidecar/tracking、Synthesizer protected orchestration 和 DeepSeek 兼容路径。
- Sidecar 可注入；测试使用 fake model/service，不调用真实 API。
- `DocumentAnalysis`/`Evidence` 位于 `src/evidence/contracts.py`，由 `models.py` 重新导出，以避免 State 与 Evidence models 循环依赖。
- P2.2b 是 P2 的 Evidence-aware Synthesis 子阶段；其余四项业务数据迁移已随后完成。P2 现正式关闭。

### P2 Closure：其余四项业务数据迁移

- `research_topic -> query`：Graph/CLI 与 Web 入口均显式双写；不使用 alias 或 validator。
- `plan -> research_plan`：Planner 成功 patch 显式双写，legacy `plan` 继续作为现有消费者的事实来源。
- `report_sections/final_report -> report`：Writer 保持 legacy 输入和生成逻辑；只在成功返回前构造 V1 Report，citations 由稳定去重 projection 生成。
- legacy tracking -> `usage`：Planner、两种 Searcher、Synthesizer 与 Writer 在成功路径从最终 legacy totals 构造完整 UsageMetrics；Evidence delta 先合并再重建 usage。
- Legacy checkpoint 兼容政策：允许继续运行，不对历史 V1 字段做自动回填，不新增 hydration adapter。

## 6.1 P3.1 COMPLETE：Runtime Lifecycle

P3.1 在不改变 Graph 拓扑、router 条件语义或 legacy 业务字段的前提下，完成了运行时身份与生命周期闭环。实现集中于 `src/runtime_lifecycle.py`，runner 边界在 `src/graph.py`，Web 入口复用相同的新运行初始化与终态规则。

### Lifecycle invariants

- `run_id` 是每次 fresh Graph run 的独立 UUID；它由 lifecycle helper 创建，绝不与 checkpoint `thread_id` 混用。`thread_id` 只由 persistent runner / Async SQLite checkpointer 拥有。
- fresh run 以 `current_stage="received"`、`status="pending"` 初始化，开始执行时变为 `planning/running`；正常节点按既有流程推进 stage。Writer 成功写入 `complete/completed`。
- 所有既有 legacy `iterations` 写点显式同步写 V1 `iteration`，且不重新定义 legacy 计数含义。
- terminal classification 不改变或伪造 legacy `error`：有 `final_report` 的正常终态为 `complete/completed`；既有 agent failure 保持 `failed/failed`；router 提前结束且无报告时写 `failed/failed`；未捕获异常 best-effort 持久化失败终态后原样重抛。
- terminal lifecycle patch 在存在 checkpointer 时使用 `aupdate_state()` 持久化，并合并进返回 state；不清除 `snapshot.next`，不改变 LangGraph 调度语义。

### Async SQLite、resume 与 adoption

- persistent runner 使用 `AsyncSqliteSaver` 和 async-compatible context manager；`ainvoke()`、`aget_state()`、`aupdate_state()` 均通过同一 Async SQLite checkpoint 路径工作。
- P3 checkpoint resume 保留原 `run_id`，不在 resume 边界递增 `iteration` / `iterations`。terminal checkpoint（`snapshot.next == ()`）直接返回，不重新执行 Graph。
- runnable checkpoint 在恢复前写 `status="running"`；仅在 resume 边界按 `snapshot.next` 映射 stage：`plan -> planning`、`search -> searching`、`synthesize -> synthesizing`、`write_report -> reporting`。未知 node 不猜测。
- pre-P3 checkpoint 仅在 `run_id` 缺失或为空时最小 adoption：生成 UUID，`iteration` 从 legacy `iterations` 种子化；pending checkpoint 恢复 running/stage，terminal checkpoint 按 `final_report` 分类为 completed 或 failed。不会 hydration `query`、`documents`、`findings`、`report`、`usage` 等业务 V1 字段，也不会新增 legacy `error`。
- resume 的 `additional_input` 不能覆盖 runtime-owned `run_id`、`status`、`current_stage`、`iteration`。

### Cache replay

- 只有存在 `final_report`、没有 legacy `error` 且不是 failed lifecycle 的完成结果可作为成功 cache。
- cache hit 是新的 replay run：对 payload deep copy，生成新 UUID `run_id`，写入 `completed/complete` 和 `iteration=0`；保留缓存中的 legacy `iterations` 与业务结果。因此 cache replay 有意允许 `iteration != iterations`。
- replay 不创建或复用 checkpoint `thread_id`，不执行 Graph 节点。failed、router early termination 或无报告的结果不会作为成功 cache/replay。

### 历史阶段摘要

P0/P1 已完成基础 State、LLM Factory、Search Runtime、Provider/Tavily/Tool Adapter，以及可独立测试的 Evidence baseline（Document adapter、ResultAnalyzer、Evidence contracts、deterministic aggregator）。这些能力已被 P2.1/P2.2 使用，并已通过 P2.2b protected sidecar 进入 Synthesizer。

## 7. 最新验证结果

### Fake-only 回归

- 最新完整 pytest：**218 passed, 2 warnings**。
- 测试不调用真实 DeepSeek、Tavily 或网页。
- 两个既存 Pydantic class-based Config deprecation warnings 不影响验证结论。

### Stage 2：最小真实 DeepSeek Evidence 验证 — PASS

使用 production `EvidenceSidecarService.create_production()`、DeepSeek 专用 non-thinking LLM、function calling、最小本地 Document 和 `retry_times=0`。

- HTTP、structured output、`DocumentAnalysisDraft`、verbatim unique quote、deterministic runtime grounding、Evidence 生成及 provider usage tracking 均成功。
- 1 次成功 Analyzer invocation，无 retry；token source 为 provider。
- LLM Draft offsets 即使不正确，runtime persisted offsets 仍基于 exact unique match，这是预期设计。

### Stage 3：真实 Planner -> Searcher -> Synthesizer — PASS

使用 DeepSeek、Tavily、`deterministic_v2` 和 production Evidence sidecar；通过 LangGraph stream 在 Synthesizer 完成后停止，Writer 未启动。

| 检查项 | 结果 |
|---|---|
| Planner | PASS；3 queries；54.461s；无顶层 error。 |
| Searcher / P2.1 | PASS；9 `search_results`、9 `documents`；Document ID 全唯一，9/9 有对应 legacy source 且绑定 credibility；18.434s。 |
| Legacy synthesis | PASS；15 `key_findings`；`key_findings` 未被 sidecar 修改。 |
| Sidecar | 输入 9 Documents；因 `EVIDENCE_MAX_DOCUMENTS=8` 分析 8 个，产出 8 analyses 和 48 Evidence。 |
| Diagnostics | `partial`；analyzer partial（文档上限与部分 quote 被严格 reject），Aggregator completed；无 aggregation/sidecar error。 |
| Grounding | 抽查 persisted Evidence span 切片严格等于 `source_quote`。 |
| Findings | 未产生可安全采用的 evidence-backed finding，按规则保留 15 条 compatibility/unverified findings；这是合法 fallback。 |
| Tracking merge | 8 次 Evidence Analyzer 调用；provider input/output tokens `12,633 / 6,794`；已追加至最终 legacy totals：`llm_calls=10`、input `15,934`、output `7,548`。 |
| Synthesizer | PASS；122.183s；`current_stage=reporting`；顶层 `error=None`。 |
| Writer | 本轮未启动，不计为 Evidence failure。 |

总观察窗口为 195.680s；Evidence Analyzer 累计调用时长 76.440s，按 Synthesizer node 与 legacy synthesis tracking 推导的 sidecar 增量约 76.483s。

## 8. 当前重要文件

| 文件 | 当前职责 |
|---|---|
| `src/runtime_lifecycle.py` | P3.1 runtime identity、fresh/resume/terminal/cache replay lifecycle helpers、runtime-owned input protection。 |
| `src/agent_trace.py` | P3.2a append-only Agent Trace contract 与 Graph node / LLM / Tool projection orchestration。 |
| `src/evaluation/` | P3.2b fixed dataset、deterministic evaluation contracts/evaluator 与 injected offline suite runner；不依赖 Graph/Agents。 |
| `src/graph.py` | 固定四节点 LangGraph、条件路由、运行/Async SQLite checkpoint/resume/cache 入口；本阶段未改拓扑。 |
| `src/state.py` | ResearchState V1、legacy/V1 fields、P3.1 lifecycle fields、Evidence 最小 persistence fields 和 `EvidenceDiagnostics`。 |
| `src/agents.py` | Planner/Searcher/Synthesizer/Writer；正常节点 stage 推进、legacy `iterations`/V1 `iteration` 显式双写；Searcher Documents 双写，Synthesizer protected sidecar 编排。 |
| `src/evidence/adapters.py` | 显式 SearchResult+credibility 到 Document 转换、URL normalization/dedup、稳定 ID。 |
| `src/evidence/compat.py` | deterministic `key_findings -> Finding[]` compatibility projection。 |
| `src/evidence/contracts.py` | 独立 persisted `DocumentAnalysis`、`Evidence` contracts。 |
| `src/evidence/models.py` | `AnalysisResult` 与 contracts 重导出兼容。 |
| `src/evidence/service.py` | ResultAnalyzer、预算/partial 和 deterministic quote grounding。 |
| `src/evidence/langchain_adapter.py` | BaseChatModel 到 `DocumentAnalysisDraft` 的 structured-output adapter 与 invocation tracking。 |
| `src/evidence/pipeline.py` | 独立 Analyzer + Aggregator pipeline runtime。 |
| `src/evidence/sidecar.py` | production/fake 可注入 sidecar、checkpoint backfill、diagnostics、tracking delta。 |
| `src/evidence/config.py` | Evidence flag 及 Analyzer runtime config。 |
| `src/llm/factory.py` | provider-neutral `get_llm()`；可选 `extra_body` 透传。 |
| `.env.example` | 无密钥的 Evidence feature/runtime 配置模板。 |
| `tests/evidence/`、相关 Searcher/Synthesizer tests | P2 dual-write、grounding、adapter/pipeline/sidecar、tracking、merge/fallback 的 fake-only 覆盖。 |

## 9. 已知问题与技术债

1. **Writer latency：** 已定位为串行 DeepSeek section generation 延迟；与 Evidence 无关。不要为此修改 Graph。后续先做 profiling，再单独设计 timeout、partial report 和可能的 section 化生成策略。
2. **Evidence partial：** max documents 默认 8；超过上限时 diagnostics 标记 partial，已完成结果仍可用于 aggregation/adoption（受 partial policy 限制）。严格 quote grounding 会拒绝不存在或重复 quote，这是有意的真实性保护。
3. **P2 V1 契约：** 六项业务数据迁移已完成，但新旧字段不是自动镜像；后续改动仍须显式双写并保持 legacy 行为。
4. **Checkpoint serialization：** Async SQLite 运行验证通过；交互式 checkpoint probe 仍会看到 Pydantic/msgpack 对未注册模型的 warning。此项不属于 P3.1，后续应单独审查序列化注册与跨版本兼容策略。
5. **Callbacks / UI：** Web callback 可能在 router early termination 后仍发出完成展示事件；lifecycle state 已正确 failed，但 UI/callback 语义应另行收敛。
6. **Runner hardening：** lifecycle ownership 已清晰，但调用方重用 persistent `thread_id` 的策略尚未额外限制；可在后续 runner hardening 阶段补充 API guard / 并发策略。
7. **Provider：** DuckDuckGo 独立 Provider、provider fallback、circuit breaker 尚未完成。
8. **CI：** fake regression 已有，但尚未建立持续集成。
9. **环境：** 使用项目 `.venv`。Windows PowerShell 可能显示中文日志乱码；先确认终端编码，不要据此判断源码损坏。

## 10. 后续路线图

### P3.2：Agent Trace / Evaluation

**P3.2a 已关闭：** `src/agent_trace.py` 独立拥有 Trace contract 和 append/merge orchestration。每个 Graph node execution 追加一条带 `trace_id`（即 P3.1 `run_id`）、node/agent/operation/type、attempt、status、起止时间、duration、error 和 metadata 的 Node event；所有历史 event ID 保留，checkpoint resume/retry 只追加新 attempt。`llm_call_details`（含 Evidence Analyzer invocation records）被投影为 LLM trace，不重算或累加 usage/token；deterministic SearchExecutor 的每一次 search/extract/retry 写 runtime invocation record，并投影为 Tool trace。未捕获的节点异常保留原异常，同时由 runner 将失败 Node event 与既有 lifecycle patch 一起 best-effort checkpoint。cache replay 是新 run 且不执行节点，显式清空 source-run trace，避免 `run_id` 混淆。

**P3.2b 已关闭：** `src/evaluation/` 独立提供小型固定 dataset（3 个典型 research query）、纯确定性 `evaluate_run()` 和注入式 `run_offline_evaluation()`。它只读取既有 State/Trace/Usage/Evidence/Findings/Report，输出可 JSON 序列化的 per-run metrics 与 suite aggregate，适合后续跨版本/配置比较。评估 run completion、Trace identity/completeness、Usage integrity、documents/evidence/findings coverage、report structure 和 error/failed trace signals；Evidence disabled/not-run、缺失 usage 或 legacy 缺字段一律明确标记 `unavailable`，不回填 legacy totals、不重算统计。没有 LLM-as-a-judge、参考答案、评分模型或 Graph/Agent integration。

**P3 最终 closure：** 已复核 lifecycle terminalization、Async checkpoint/resume/pre-P3 adoption、cache replay、Node/LLM/Tool Trace append/identity、Trace checkpoint resume/cache isolation、以及 Evaluation 的只读/failed/unavailable 语义。最终补充了真实 Async SQLite trace resume append、cache replay trace 清空与 evaluation outcome 不掩盖子指标 failed 的 fake-only 回归。P3 没有 blocker。

P3 后续扩展应保持 Trace/Evaluation 只做 observability；不要重新定义 P3.1 的 `run_id`、`status`、`iteration`、`current_stage` ownership，也不要先增加 Critic Agent、动态路由、Memory、Reflection 或 Supervisor。

### 远期（独立设计）

- Search Provider 闭环：DuckDuckGo provider 化、统一 factory 和可配置 fallback。
- Writer 性能：profiling 后再评估 section planner/writer/merger、timeout 和 partial report。
- Memory、Reflection、Supervisor、多 Agent：先设计 Graph V2、预算、终止协议和结构化消息契约。

## 11. 下一次会话启动方式

```powershell
git status --short --branch
git log -3 --oneline --decorate
& .\.venv\Scripts\python.exe -m pytest -q
```

随后阅读本文件及当前任务相关的源码、测试。开始任何实现前确认：不改 Graph 拓扑；不删除 legacy fields；双写显式发生；Evidence failure 必须隔离；P3 生命周期设计须与 P2 业务数据兼容链分开评审。
