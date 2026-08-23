# ResearchOS / 多智能体研究系统 - 交接基线

> 本文是下一会话唯一交接基线；若与历史 handoff、提交记录或旧测试结论冲突，以当前源码和本文为准。
>
> 最后更新：2026-08-22。P0-P10 已正式关闭；P10.2 deterministic extraction coverage ordering 已完成。P11.1 Quality-to-Action Advisory Contract 已完成，但仍保持 Evaluation-only advisory 边界。

## 目录

1. Project Snapshot（项目快照）
2. Current Architecture（当前架构）
3. End-to-End Research Flow（端到端研究流程）
4. Core Data Flow / Agent I/O（核心数据流与 Agent I/O）
5. Capability Status Matrix（能力状态矩阵）
6. Current Decision Point（当前决策点）
7. Development Guardrails / DO NOT BREAK（开发护栏）
8. P0-P9 Milestone Map（里程碑地图）
9. Detailed History / Appendix（详细历史与附录）
10. Graph V2 启动条件、长期演进方向与下一会话启动

## Project Snapshot

| 项目 | 当前事实 |
|---|---|
| 产品目标 | 面向来源的研究 Agent：把用户 Query 转为结构化计划、搜索来源、综合 Findings 和带引用的 Report，同时保持 runtime、provenance 与 Evaluation 边界。 |
| 当前阶段 | P0-P10 已正式关闭；P11.1 advisory contract 已完成。系统保持 Graph V1，未获得任何生产 action 执行权限。 |
| Graph | 固定线性 Graph V1：`Planner -> Searcher -> Synthesizer -> Writer`。现有 router、Writer 输入、legacy 主链和 Report contract 不变。 |
| Provider / Search | 当前默认运行组合：DeepSeek + Tavily + `deterministic_v2`。`SearchExecutor` 拥有确定性 search 与 round-robin-by-query extraction ordering；`legacy_agent` 仅显式兼容。 |
| 默认开关 | `EVIDENCE_ANALYZER_ENABLED=false`；`RESEARCH_MEMORY_ENABLED=false`；`WRITER_SECTION_EXECUTION_MODE=serial`。Writer `bounded` 需显式开启，实验 bound 默认 `2`。 |
| 测试基线 | fake-only 全量：**303 passed，2 个既有 Pydantic deprecation warnings**。CI 使用 Python 3.11 与固定 pytest `--basetemp`；真实 provider 验证仅手工执行，不进 CI。 |
| 当前决策 | 不改 Graph/router/Writer/runtime ownership。P11.1 只统一 quality observations 并输出 advisory recommendation；不自动 routing、re-search、replan 或 Reflection。 |

## Current Architecture

```text
CLI / Chainlit / Runner
        |
        +-- run identity、checkpoint / resume / cache replay、terminal lifecycle
        v
   Graph V1（固定线性 router）
        |
        +--> Planner ------------------- ResearchPlan
        |      |                              |
        |      |                              +--> objectives / search queries / report outline
        |      +-- 可选 P8 Memory prior context（bounded，默认关闭）
        |
        +--> Searcher ------------------ SearchResults + Documents
        |      |
        |      +--> deterministic_v2 SearchExecutor --> Provider / Tools（DeepSeek/Tavily）
        |      +--> 可选 Memory provenance `site:<host>` hints（仍经 SearchExecutor）
        |
        +--> Synthesizer --------------- Findings + 可选 Evidence sidecar
        |      |
        |      +--> compatibility findings 优先；Evidence failure 仅 diagnostics
        |
        +--> Writer -------------------- Report / sections / citations
               |
               +--> 默认 serial；bounded section scheduling 仅实验模式

Cross-cutting：
  Runtime & Execution：shared deadline、operation budget、retry、cancel、lease、terminal reason、at-least-once
  Trace：append-only logical trace；checkpoint retention / compaction；不参与 usage 或路由
  Usage：LLM/tool attempts 与 tokens；每个真实 attempt 仅计一次
  Evaluation：offline、deterministic、read-only snapshot 与 benchmark archive
  Memory：本地 SQLite、source-backed records、仅 completed run 后 best-effort write
```

### Ownership Boundaries

| 边界 | Owner 与规则 |
|---|---|
| Graph | 固定拓扑、现有 router、runner/checkpoint 入口；不承载业务 retry 或新增能力编排。 |
| State / Agent | Agent 产生业务 patch；legacy/V1 字段只能显式双写，不做 alias、auto-hydration 或隐式同步。 |
| Runtime / Execution | 拥有 global deadline、operation budget、terminal reason、lease、cancel/resume、at-least-once；service 只声明局部限制。 |
| Search / Provider | `SearchExecutor` 拥有确定性 search/extract 和局部限制；Provider 只负责 transport、typed error、retryable、Retry-After，不选 fallback/circuit。 |
| Evidence | optional sidecar、strict unique grounding、diagnostics；compatibility findings 优先，sidecar failure 不改变顶层 error、router 或 Writer 输入。 |
| Memory | local SQLite、deterministic lexical retrieval、source-backed projection；默认关闭，仅在既有 Planner/Searcher 内 bounded injection。 |
| Trace / Usage / Evaluation | 只观测：Trace 不等于 Usage、不参与 routing；Evaluation 只读，不改生产配置或流程。 |

## End-to-End Research Flow

1. CLI/Chainlit 创建或 resume run；Runtime 建立 run identity、lifecycle、deadline/budget 和 checkpoint/lease 语义。
2. Planner 读取 canonical Query，可选读取 bounded P8 Memory 作为 prior context；一次 LLM call 生成现有 `ResearchPlan`。P9 仅以 prompt/normalization 改善 purpose、去重与 objective-outline 对齐。
3. Searcher 消费 `ResearchPlan.search_queries`。默认 `deterministic_v2` 经 `SearchExecutor`；Memory source hint 仅追加 bounded `site:<host>` query，仍走同一 executor。
4. Search 结果成为 `SearchResult[]` 和 V1 `Document[]`；结果列表保持 search/result 原序，extraction 在固定预算内按 originating query 做 deterministic round-robin，可信度、去重、runtime controls、partial/error 语义保持现有路径。
5. Synthesizer 产出 legacy findings、V1 `Finding[]` 和可选 Evidence；Evidence sidecar 不能替代 compatibility findings 或改变全局失败语义。
6. Writer 消费 legacy findings/search inputs 与 outline，生成有序 sections/citations，显式双写 V1 `Report`；默认 serial，hard failure 时 all-or-nothing。
7. 仅 completed run 后，P8 才投影 source-backed findings 到本地 Memory。Trace/Usage 记录运行；Evaluation 离线只读 State/Report/Evidence，产出 snapshot/benchmark，不修改该 run。

## Core Data Flow / Agent I/O

| 阶段 | 输入 | 输出 | Contract / 备注 |
|---|---|---|---|
| Intake / Runner | 用户输入、checkpoint state | canonical `query`、`run_id`、execution context | `run_id` 不等于 checkpoint `thread_id`；cache replay 是新 run。 |
| Planner | Query、可选 bounded `MemoryItem` prior context | `ResearchPlan` / legacy `plan`、`retrieved_memory`、`memory_ids` | `ResearchPlan = topic + objectives + SearchQuery[] + report_outline`；memory 不是证据/最终引用，不能替代当前 objectives。 |
| Searcher | `ResearchPlan.search_queries`、可选 provenance hints | `SearchResult[]`、V1 `Document[]`、search diagnostics | 所有搜索经 `SearchExecutor`；memory 不得绕过。 |
| Synthesizer | search results/documents、plan objectives | legacy `key_findings`、V1 `Finding[]`、可选 `Evidence[]` | Evidence 仅为 provenance/grounding 诊断，不证明事实正确性或语义蕴含。 |
| Writer | legacy findings、legacy search results、outline | 有序 `ReportSection[]`、`final_report`、V1 `Report`、citations | Writer 不直接消费 memory；outline-index assemble；hard failure 无 partial report。 |
| Memory post-run | completed State、Findings/Evidence/source URLs | SQLite `ResearchMemoryRecord` | 仅 terminal completed 写入；storage failure 非致命；TTL/dedup/prune 生效。 |
| Evaluation | 已完成 State/Report/Evidence/Trace/Usage | read-only result、snapshot、benchmark archive | 不构造生产控制流，不改 returned State。 |

## Capability Status Matrix

| 能力 | 状态 | 默认 | 当前决策 / 限制 |
|---|---|---|---|
| Graph V1 线性研究流 | Implemented | Enabled | 保持 `Planner -> Searcher -> Synthesizer -> Writer`。 |
| Deterministic Search | Implemented | Enabled | `deterministic_v2` + `SearchExecutor`；`legacy_agent` 仅兼容。 |
| Evidence sidecar | Implemented | Disabled | P5/P6 不足以支持默认启用或 selector。 |
| Writer serial scheduling | Implemented | Enabled | 生产默认保持 serial。 |
| Writer bounded sections | Implemented | Experimental | 显式 `bounded`，默认 bound=2；P7 仅证明本地 Writer 加速。 |
| Research Memory V1 | Implemented | Disabled | SQLite/lexical/provenance baseline；无 rollout、privacy UX、semantic ranking、真实 effectiveness 证据。 |
| Planning Enhancement | Implemented | Enabled（既有 Planner 内） | P9 是 prompt/normalization + fake-only lexical baseline；不证明真实 provider 质量。 |
| Offline Evaluation / calibration / repeatability | Implemented | Offline only | read-only；snapshot 绑定 evaluator、config 与 dataset content。 |
| Quality-to-Action Advisory Contract | Implemented | Offline only | P11.1 统一 P5/P9/P10/runtime observations；只输出 `candidate/blocked/unavailable` advisory，不写 State 或触发 action。 |
| Provider fallback / circuit breaker | Future | N/A | 需要两个 provider、availability SLO、taxonomy、policy、cross-provider observability。 |
| Evidence selector | Future | N/A | P6 evidence sufficiency 未满足。 |
| Reflection / replan / Supervisor | Future | N/A | 缺 quality-to-action protocol、bounded loop/budget、checkpoint/approval 语义。 |
| Graph V2 / branch-join | Future | N/A | 仅在下文启动条件满足时评审。 |
| P10.1 Research Coverage Baseline | Implemented | Offline only | deterministic fake-only；只读评估 plan/search outputs，不改生产默认、Graph/router、Writer 输入或 runtime ownership。 |
| P10.2 Coverage Ordering | Implemented | Enabled inside deterministic_v2 | SearchExecutor extraction candidates 按 originating query round-robin；不增加 search/extract budget，不改 Graph/router/Writer/runtime ownership。 |

## Current Decision Point

**P10 Research Coverage 已关闭。** P10.1 建立 measurement-only baseline；P10.2 在 `SearchExecutor` 内实现 deterministic extraction coverage ordering。它不改变 Graph、router、Writer 输入、runtime ownership、默认预算或 provider policy。

**P11.1 Quality-to-Action Advisory Contract 已完成。** 新增 Evaluation-only contracts 与纯函数 adapters，将 P9 planning、P10 research coverage、P5 run/evidence/report quality、以及 runtime lifecycle/error/trace/usage observations 组合为 advisory recommendation。`ActionRecommendation.advisory` 固定为 `true`，状态只允许 `candidate`、`blocked`、`unavailable`；不写入 `ResearchState.next_action`，不修改 `status/current_stage/terminal_reason`，不触发 Graph edge，也不调用 Planner/Searcher/Writer。

P11.1 三层边界：

- observed：Runtime adapter 只读取 `status`、`current_stage`、`terminal_reason`、`error`、`agent_trace`、`usage`；它不改变 Runtime lifecycle ownership。
- derived：P5/P9/P10 evaluator metrics 通过纯 adapter 映射为 `QualitySignal`；既有 evaluator 语义不变，fake-only / rubric 结果仍不等于真实语义质量。
- advisory：resolver 只返回结构化 action candidate/block；Runtime hard-stop facts 优先；`unavailable` 不等于 passed；failed signal 不自动获得执行权限。

P11.1 contract snapshot 绑定 evaluator version、policy version、metric thresholds、source fields 与 input fingerprint；threshold policy 进入 snapshot/fingerprint。当前 action taxonomy 为 `continue`、`accept_partial`、`retry_research`、`replan`、`stop_fail`、`human_review`，但本阶段所有输出都保持 advisory。

P10.1 observed before baseline：

- dataset：`researchos_research_coverage` `p10.1.v1`，3 cases：AI regulation jurisdiction balance、clinical evidence practice translation、enterprise AI evaluation coverage。
- metrics：`planned_query_facet_coverage`、`executed_query_facet_coverage`、`result_facet_coverage`、`extracted_facet_coverage`、`source_domain_balance`。
- fixed config：`SEARCHER_MODE=deterministic_v2`、`max_search_times=3`、`max_extract_times=4`、`max_results_per_search=3`、`MIN_CREDIBILITY_SCORE=40`、`RESEARCH_MEMORY_ENABLED=false`、`EVIDENCE_ANALYZER_ENABLED=false`。
- snapshot fingerprint：`2b85563ebf6019d6135fb09130815315275c6e6cb9c2f45491855af51df6f23a`；dataset content fingerprint：`62dc1abda05d0dcd4249fea726777514b171bccc8095fcfde141048c38c3aff1`；fake payload fingerprint：`068344c0a570977b1c0d782b7745850a269abcf11c14da3dfdba9a31e39d427e`。
- observed before result：planned/executed/result/domain coverage means all `1.0`；`extracted_facet_coverage_mean=0.7111111111111111`；fully passing `0/3`；failed extracted facets are `china`、`outcome`、`procurement`。

P10.2 observed after baseline：

- ordering：每个已执行 query 先选 top-1 result，再按原 query 顺序选 top-2，继续直到 `max_extract_times`；每个 query 内原始 result ranking 保持不变；最终 `search_results` 列表原序保持不变。
- observed after result：planned/executed/result/domain/extracted coverage means all `1.0`；fully passing `3/3`；`p10_2_candidate=false`；无 result/extracted failure case。
- derived conclusion：当前 fake-only baseline 的 coverage failure 已被最小 deterministic extraction ordering 解决。不得据此宣称真实 provider 质量；不启动 Reflection、Supervisor、Graph V2、provider fallback、Evidence selector 或预算提高。

## Development Guardrails / DO NOT BREAK

- 不改 Graph 拓扑、现有 router、Writer legacy 输入或既有 `ResearchPlan`/Report contracts；例外需要独立批准的控制流和迁移设计。
- `run_id` 必须不同于 checkpoint `thread_id`；resume 保留 run identity，cache replay 是新 run，delivery 保持 at-least-once。
- 默认 deterministic Search、Evidence、LLM adapter 共享 Runtime deadline/budget。`legacy_agent` 不承诺该控制链，不能恢复为默认。
- 不引入 provider fallback/circuit selection，也不让 Agent 自建跨 service retry/timeout；保留 typed provider errors 与 runtime-owned retry/budget policy。
- Evidence compatibility findings 必须先于 sidecar；sidecar failure 只进 diagnostics，不能改变 top-level error、router 或 Writer 输入。
- Writer 保持 all-or-nothing。bounded sections 必须保留 outline-index assemble、first-seen citation determinism、经 runtime `ExecutionContextCoordinator` 的 shared deadline/budget 和 cancel propagation；不得直接共享 immutable `ExecutionContext`。
- Memory 默认关闭。retrieval/write failure 非致命，不得改变 top-level error、router、Writer 输入或 cache replay。provenance 只能是 `evidence_grounded`、`evidence_partial`、`legacy_source_url`，不证明 fact correctness 或 claim support。
- P8 memory 只作 prior context：不能替代当前 Query/objectives、充当最终 citation、绕过 `SearchExecutor` 或直接进入 Writer。
- P9 必须保持一次 Planner LLM call；normalization 只清理/去重/标记已有条目，不得凭空生成 objectives、queries、sections、Agent 或 Graph node。
- Trace 是 append-only observation，不拥有 Usage/routing；Usage 每个真实 LLM attempt 仅计一次；Evaluation 是 deterministic/read-only，除非单独批准不得成为生产 gate。
- 不得从单个真实样本或 fake-only 结果推导生产策略；后续决策必须保留 observed/derived 区分和 archive 路径。

## P0-P9 Milestone Map

| Milestone | 目的 | 核心改动 | 关键决策 |
|---|---|---|---|
| P0-P2 | 建立 V1 contracts 与安全研究原语 | 显式 State double-write、LLM/Search 基础、optional Evidence sidecar | 保留 legacy path 与 failure isolation。 |
| P3 | 让 run 可操作、可观测 | lifecycle、checkpoint/resume/cache replay、Trace、read-only Evaluation | Runtime semantics 与 observation 不进入业务 routing。 |
| P4.1-P4.5 | 集中 execution governance | persisted policy/lease、deadline/budget/retry、provider/LLM contracts、Writer profile、trace retention、snapshot binding | Runtime 拥有 global controls；provider/agent 不选 fallback 或跨 service retry。 |
| P5.1 | 建立 offline quality baseline | 6 fixed cases、quality rubric、regression gate | quality evaluation 是 deterministic/read-only，不进生产 routing。 |
| P5.2 | 离线衡量 Evidence value | disabled/enabled/partial fake benchmark | benchmark 不编码 selector/default policy。 |
| P5.3 + closure | 观测真实 workload/SLO | manual DeepSeek/Tavily archive 与 observed/derived comparison | 不默认启用 Evidence；不启动 provider/Graph V2 项目。 |
| P6 | 校准并重复 Evidence 测量 | reference fixtures、repeatability harness、two-round closure | Evidence selector、Writer optimization、provider resilience 均 insufficient/not assessable。 |
| P7 | 试验 Writer section concurrency | bounded=2 scheduler、shared coordinator、deterministic assembly | 生产 Writer 保持 serial；无新 product/SLO 决策不继续扩展性能项目。 |
| P8 | 建立最小 research memory | source-backed SQLite、lexical retrieval、bounded Planner/Searcher injection | 默认关闭；不引入 Memory Agent/vector DB。 |
| P9 | 在既有 contract 内改善 planning quality | 6-case fake baseline、purpose labels、prompt/normalization | 不引入 replan、额外 LLM call、Graph change，也不宣称真实 provider 质量。 |
| P10.1 | 建立 research coverage 测量基线 | 3-case fake baseline、plan/search/result/extracted/domain facet metrics、snapshot/fingerprint | measurement-only；暴露 extracted coverage failure；不实现 intervention。 |
| P10.2 | 提高固定 extraction budget 下的 facet coverage | SearchExecutor extraction candidates by query round-robin；targeted ordering tests | 关闭 P10；不增加预算、不改生产默认控制流、不引入 replan/Graph V2。 |
| P11.1 | 统一 quality-to-action advisory semantics | QualitySignal/ThresholdSpec/SignalProvenance/ActionRecommendation；P5/P9/P10/runtime adapters；deterministic decision matrix | Evaluation-only；不写 State、不改 Graph/router/Writer/runtime、不自动执行 action。 |

## Detailed History / Appendix

### A. P5 Research Quality and Evidence Baseline

- `researchos_offline_baseline` v2 有 6 个固定任务：政策比较、城市气候适应、半导体风险、临床证据转化，以及 partial-evidence/provider-failure 场景。每 case 有 `ResearchQualityRubric`（distinct sources、grounded citations、report sections/characters、heading）；dataset content 进入 snapshot fingerprint。
- evaluator 只读取 State/Report/Evidence：`source_coverage` 统计 document URI；`grounded_citation` 对齐 report URL 与有效 grounded/partial Evidence source URL；`report_completeness` 检查固定报告下限。regression gate 只产出离线结果，不改 Graph/router/Writer/Runtime/Evidence ownership。
- `run_evidence_value_benchmark` 对同一 dataset/rubric 注入 `disabled`、`enabled`、`partial`，不构造 Graph/provider/LLM，也不改 returned State；输出 quality、Evidence adoption/diagnostics、UsageMetrics 与 per-case/tag delta，只有 quality pass count 上升才标 `value_observed`。
- P5.3 手工入口：`scripts/run_real_workload_benchmark.py`；DeepSeek + Tavily，cache disabled，P5.1 dataset 三种 mode，不写 `.env` 或生产默认。真实 archive：`artifacts/real-workload-benchmarks/20260819T042430Z/`（`benchmark.json`/`benchmark.md`），6 cases x 3 modes，约 85 分钟，无 timeout/retry。harness wall/node trace/Evidence attempts/usage/errors 是 **observed**；quality/value/SLO 是 **derived**。

| Mode | Success | Quality pass | Source / grounded citation / completeness | p50 / p95 | Writer / total | 其他 observed |
|---|---:|---:|---|---|---:|---|
| disabled | 6/6 | 0/6 | 1.000 / 0.000 / 1.000 | 265.158s / 357.798s | 0.671 | 无 Evidence adoption。 |
| enabled | 5/6 | 1/6 | 0.833 / 0.167 / 0.833 | 291.394s / 378.542s | 0.602 | adoption 0.800（35 grounded）；4/6 provider-error、5 partial、1 failure。 |
| partial | 6/6 | 2/6 | 1.000 / 0.333 / 1.000 | 305.003s / 354.473s | 0.642 | adoption 1.000（45 grounded）；4/6 provider-error、6 partial。 |

- enabled 对 disabled 有 4 个 matched-success cases：grounded-citation `+0.200`、observed wall `+266.679s`、token/LLM-call `+79,332 / +34`；partial 有 5 个：`+0.200`、`+125.747s`、`+119,911 / +43`。两个 mode 唯一 benefited case 为 `ai-regulation-overview`，tag `policy, comparison`。
- P5 closure：run success 不等于 quality；Evidence 不默认/选择性启用；Writer performance、provider resilience、Graph V2 不立项。`grounded_citation` 只代表 URL/provenance grounding，不代表事实正确性、claim support、semantic entailment。

### B. P6 Calibration, Repeatability, and Closure

- `researchos_quality_calibration` v1 与 P5 workload 分离：4 个 reference-backed fixtures（完整 provenance/structure、来源不足、ungrounded report URL、结构不完整）；reference expectation/rationale 进入 `calibration_content_fingerprint`。
- `run_rubric_calibration` 只读 injected State-like data；`run_repeatability_benchmark` 顺序调用 P5.3 runner，保存 observed runs 与 derived dispersion；二者均不改生产配置、Evidence flags、Graph、provider policy。
- sufficient gate 要求至少 3 rounds、stable same-case positive provenance delta、无过度 provider-error fallback、以及 Writer/provider 的显式 SLO；即使 sufficient 也仅允许人工评审，不自动 rollout。手工入口：`scripts/run_repeatability_benchmark.py --rounds 3`，cache disabled、`ci: false`；CI 只跑 deterministic fake runners。
- Round 1：`artifacts/real-workload-benchmarks/20260819T042430Z/benchmark.json`。Round 2：`artifacts/p6-repeatability-rounds/20260820T042404Z/benchmark.json`。二者同为 `researchos_offline_baseline` v2、disabled/enabled/partial、cache-disabled DeepSeek + Tavily 和 observed/derived contract。

| 信号 | Enabled：Round 1 -> Round 2 | Partial：Round 1 -> Round 2 |
|---|---|---|
| grounded-citation pass-rate delta | +0.200 -> +0.200 | +0.200 -> 0.000 |
| overall quality pass rate | 0.167 -> 0.167 | 0.333 -> 0.000 |
| benefited case/tag | `ai-regulation-overview` / `policy, comparison` -> `coastal-adaptation-partial-evidence` / `climate, evidence, partial` | `ai-regulation-overview` / `policy, comparison` -> none |
| matched-success cases | 4 -> 5 | 5 -> 4 |
| observed wall-latency overhead | +266.679s -> +33.951s | +125.747s -> +114.929s |
| Writer / total | 0.602 -> 0.592 | 0.642 -> 0.602 |
| mode p50 / p95 | 291.394 / 378.542s -> 282.525 / 327.403s | 305.003 / 354.473s -> 325.196 / 400.880s |
| provider-error rate | 0.667 -> 0.500 | 0.667 -> 0.667 |

- provider-error distribution 不稳定：enabled `analyze_document` errors `7 -> 6` 且 affected cases 改变；partial `7 -> 5`，Round 2 还有 plan/run-level error。Round 3 有意不执行：两轮已经显示 benefited case/tag 转移/消失、partial delta 消失、quality 下滑、matched population/error distribution 改变；继续采样不改变当前非批准决策。
- P6 closure：Evidence selector **insufficient**；Writer optimization 因缺 user SLO/section contract 为 **not assessable**；provider resilience **insufficient / not assessable**。不得据此实现 selector、fallback/circuit 或 Graph V2。

### C. P7 Writer Performance Experiment Closure

- P7 只改 Writer 内部 section scheduling；Graph V1/router、Writer legacy input、V1 `Report`、Evidence sidecar、provider policy、runtime ownership 不变。`WRITER_SECTION_EXECUTION_MODE` / `WRITER_SECTION_CONCURRENCY` 已实现；生产 `serial`，`bounded` 显式开启，实验默认 2。
- section 无相互依赖：只读 shared topic、outline title、legacy findings/search results、shared runtime context。并发探针证明 immutable `ExecutionContext(max_operation_calls=1)` 有 lost-update；runtime `ExecutionContextCoordinator` 以 lock 原子消费 shared deadline/budget。bounded 保持 all-or-nothing、cancel propagation、outline-index assemble、first-seen citations、stable details/Trace。
- 历史 tests：targeted `25 passed, 1 warning`；全量 fake-only `273 passed, 2 warnings`。真实 A/B archive：`artifacts/writer-performance-experiments/20260821T033158Z/`；DeepSeek + Tavily、Evidence disabled、cache disabled、单 `ai-regulation-overview`，不做 repeatability，不改默认。

| Arm | Status | Total wall | Writer node | Section LLM sum | Sections | Citations | Quality |
|---|---|---:|---:|---:|---:|---:|---|
| serial(1) | completed | 238.816s | 166.782s | 166.764s | 8 | 9 | source coverage passed；grounded citation failed；report completeness passed |
| bounded(2) | completed | 238.236s | 119.109s | 212.908s | 8 | 9 | source coverage passed；grounded citation failed；report completeness passed |

- 单样本 observed：Writer wall `-47.673s` / `-28.58%` / 约 `1.40x`；total wall 仅 `-0.580s` / `-0.24%`。Non-Writer aggregate `72.033s -> 119.127s`（`+47.094s`），archive 无 Planner/Searcher/Synthesizer per-node latency，不能归因。section LLM sum / Writer wall 为 serial `1.00x`、bounded `1.79x`；缺 per-section start/end/token，不能作更强判断。
- 决策：bounded=2 技术可行但仍 experimental；生产 serial；无新的 product/SLO 决策不得扩大 Writer performance 工作。

### D. P8 Research Memory V1 Closure

- P8 是 local/default-off baseline：`RESEARCH_MEMORY_ENABLED=false`；无 Reflection、Supervisor、Graph V2、branch/join、provider fallback、Memory Agent、vector DB、personalized memory。
- `ResearchMemoryRecord` 从 completed-run `Finding` 或 legacy `key_findings` 投影，必须有 source URL；Evidence-backed 用 Evidence URL/document IDs，legacy 用 `SearchResult` / `ReportSection.sources`。存 `memory_id`、stable `content_hash`、run/topic/statement/summary、source/evidence/document refs、timestamps/TTL、grounding/provenance；不存 raw prompts、full reports/pages、secrets、provider payloads、unredacted traces。
- provenance 严格为 `evidence_grounded`、`evidence_partial`、`legacy_source_url`，只声明 URL/provenance，不声明 correctness/support/entailment。
- `ResearchMemoryStore` 是 `RESEARCH_MEMORY_STORE_PATH` 本地 SQLite（默认 `.cache/research_memory/memory.db`），按 normalized `(statement, source_refs)` stable hash dedup，以 `updated_at`、`memory_id` 确定性 trim；支持 upsert/get/delete/prune/retrieve/count/dump；TTL 默认 30 days，max 默认 500。
- retrieval 是 query/topic/statement/summary/tags/source hostname 上 deterministic lexical token overlap，投影到 `MemoryItem`/`memory_ids`；`RESEARCH_MEMORY_RETRIEVAL_LIMIT=0` 禁用 injection。write 仅 `status=completed` 且 `terminal_reason=completed` 的 runner boundary；storage failure 非致命，cache replay 无 write path。
- Planner 只取 bounded memory prior context；Searcher 将 provenance URL 变成 bounded `site:<host>` hints，经 `SearchExecutor`；Writer 不消费 memory。retrieval/write failure 不改变 top-level error、router、Writer、cache replay。
- 历史 tests：targeted/closure `31 passed, 1 warning`；全量 `279 passed, 2 warnings`。无需真实 API benchmark 才能关闭这个 local/default-off baseline；剩余限制为 rollout、privacy UX、semantic ranking、memory-quality evaluation、real-workload effectiveness。

### E. P9 Planning Enhancement Closure

- `researchos_planning_quality` `p9.v1` 有 6 fixed fake cases 和 3 deterministic lexical metrics：objective facet coverage、normalized purpose-category diversity、outline facet alignment；复用 snapshot/fingerprint、Trace/Usage 和一次 Planner call。
- prompt 要求 `background`、`mechanism`、`comparison`、`authority`、`implementation`、`risk_limitations`、`trends` 中的 bounded purpose label，要求 objective-outline 对应；当前 Query/objectives 优先于 P8 prior memory。`normalize_research_plan` 只 trim/deduplicate 已给 objectives/queries/outline 并标记已有 purpose；不创建 objectives/queries/sections/Agent/node/额外 LLM call。
- frozen fake before/after：fully passing `1/6 -> 6/6`；objective coverage `0.5556 -> 1.0000`；purpose diversity `1.3333 -> 3.0000`；outline alignment `0.5417 -> 1.0000`。改善：`ai-regulation-comparison`、`urban-climate-adaptation`、`clinical-evidence-translation`、`enterprise-ai-evaluation`、`renewable-grid-integration`；`semiconductor-supply-chain` 保持 `1.0 / 3 / 1.0`。
- 历史验证：targeted `29 passed, 1 warning`；全量 `282 passed, 2 warnings`。未运行真实 API benchmark；fixture 结果不证明真实 provider 或端到端研究质量。

### F. Historical Decision Summary

- P5/P6：不默认/selector Evidence；不因当前证据启动 provider resilience 或 Graph V2。
- P7：不切 Writer 默认，不继续扩大 Writer benchmark。
- P8：baseline 关闭且默认关闭；不意味着 Memory V2 rollout。
- P9：baseline 关闭；不引入 automatic replan/Reflection/Supervisor/Graph V2，也不形成生产策略。
- P10.1：measurement-only baseline 关闭；fake-only observed extracted coverage failure 支持 P10.2 最小 intervention。
- P10.2：coverage ordering 关闭；不继续扩大到 replan、Reflection、Supervisor、Graph V2、provider fallback、Evidence selector 或预算提高。
- P11.1：advisory contract 关闭；已完成统一 signal/action 数据语义，但没有生产 action 权限。P11.2 仅在补齐真实 workload calibration、action authorization、bounded budget/termination、checkpoint/approval 和 merge contract 后评审。
- measurement-contract governance 仍是 Evidence、provider、Reflection、Writer-default 等高风险改动前置条件；不得把 P5/P6/P7 observed 或 P8/P9 fake-only 数据直接转为生产 policy。

### G. P10.1 Research Coverage Measurement Closure

- 新增 `src/evaluation/research_coverage.py`、`src/evaluation/research_coverage_dataset.py`、`tests/test_research_coverage.py`；`src/evaluation/__init__.py` 仅导出 offline evaluation API。
- evaluator 只读 `ResearchCoverageObservation(plan, executed_queries, search_results, documents)`；不构造 Graph、Agent、provider、Writer、Runtime，也不修改 State。
- fake runner 在测试内使用 deterministic `SearchExecutor` + fake search/extract tools。固定 budget 为 search `3`、extract `4`、per-search results `3`；每 case 三个 planned facets/维度都有 search result 和 document，但第三个 query 的 facet 因当前顺序提取策略没有 full content。
- Baseline observed：`planned_query_facet_coverage_mean=1.0`、`executed_query_facet_coverage_mean=1.0`、`result_facet_coverage_mean=1.0`、`source_domain_balance_mean=1.0`、`extracted_facet_coverage_mean=0.7111111111111111`、fully passing `0/3`。
- P10.2 candidate was approved and implemented as the minimal SearchExecutor ordering change below.

### H. P10.2 Deterministic Extraction Coverage Ordering Closure

- `SearchExecutor` now selects extraction candidates by originating query: rank 1 from each executed query in query order, then rank 2 from each query, and so on until `max_extract_times` is reached. Search calls, extraction calls, default budgets, returned `SearchResult[]` order, Graph/router/Writer input/runtime ownership remain unchanged.
- Targeted tests cover query count greater than extraction budget, query with no results, duplicate/invalid filtered results, partial extraction failure, deterministic ordering, and P10.1 before/after baseline comparison.
- P10.1 before observed：planned/executed/result/domain coverage `1.0`；extracted coverage `0.7111111111111111`；fully passing `0/3`; P10.2 candidate `true`.
- P10.2 after observed：planned/executed/result/domain/extracted coverage `1.0`；fully passing `3/3`; P10.2 candidate `false`.
- Historical validation：targeted `tests/test_search_executor.py tests/test_research_coverage.py` = `17 passed, 1 warning`；full fake-only = `290 passed, 2 warnings`.
- P10 closure recommendation：close P10. Further coverage work requires a new measurement contract with real provider/product metric evidence; do not continue by adding replan, Reflection, Supervisor, Graph V2, provider fallback, Evidence selector, or higher default budgets.

### I. P11.1 Quality-to-Action Advisory Contract Closure

- 新增 `src/evaluation/quality_action.py` 与 `tests/test_quality_action.py`；`src/evaluation/__init__.py` 仅导出 Evaluation API，不接入生产 Graph。
- contracts：`QualitySignal`、`ThresholdSpec`、`SignalProvenance`、`ActionRecommendation`，以及只读组合结果 `QualityActionEvaluation`。Signal 支持 `passed/failed/unavailable/inconclusive`、`observed/derived`、`run/node/facet/document/claim/report`、deterministic、threshold、reason、provenance。
- adapters：`planning_quality_signals`、`research_coverage_signals`、`run_evaluation_signals`、`runtime_observation_signals`。adapters 不重跑 LLM/search/provider；既有 P5/P9/P10 evaluator 不修改。
- decision matrix：all pass -> `continue/candidate`；extraction facet failure -> `retry_research/blocked`；planning failure -> `replan/blocked`；Runtime budget/deadline/cancel/failure -> `stop_fail/candidate`；unavailable/conflicting -> `human_review/blocked`；partial output -> `accept_partial/blocked`。
- observed/derived/advisory：Runtime 字段是 observed control-plane facts；P5/P9/P10 metrics 是 derived evaluation observations；recommendation 永远 advisory，不获得生产执行权限。
- targeted validation：`tests/test_quality_action.py tests/test_planning_quality.py tests/test_research_coverage.py tests/test_offline_evaluation.py` = `30 passed, 1 warning`；覆盖 deterministic repeatability、threshold boundary、unavailable vs failed、precedence、hard-stop priority、conflict、read-only State 和 no external calls。
- P11.1 当前结论：contract implementation complete；不建议直接进入自动 Reflection/P11.2。下一阶段只有在真实 workload calibration、action policy authorization、bounded loop/budget/termination、checkpoint/approval 语义齐备后，才可重新评审 P11.2。

## Graph V2 启动条件

**Graph V2 不是默认下一步，也不是性能优化、增加模型或新增数据字段的前提。** 只有同时具备明确业务需求、成功指标和迁移方案时才可立项：

1. Reflection loop：quality signal 决定 replan/re-search/rewrite，并定义最大轮数、收敛/终止、cost cap。
2. Supervisor routing：系统在多个 next action 中选择，而不是沿现在线性 router 前进。
3. Parallel research branches：并发子问题有 join/merge、shared budget、cancel propagation、可追溯 attribution。
4. Human approval：checkpoint wait/resume 定义 permission、timeout、terminal semantics。

任何 proposal 必须定义 business scenario/SLO、State/message/branch-merge contract、runtime budget/retry/cancel/partial protocol、checkpoint/resume/lease impact、legacy migration/rollback、offline evaluation。任一缺失则保持 Graph V1。

## 最终 ResearchOS 演进方向

长期方向仍是 **Memory、Reflection、Supervisor、Evaluation、Multi-Agent collaboration**，但必须建立在现有 runtime、trace、provenance、evaluation、compatibility 边界上，不能绕过它们另加 Agent。

- Memory：P8 已覆盖最小 provenance/storage/retrieval/retention 与 bounded Planner/Searcher injection；personalized/semantic memory 是独立未来工作。
- Reflection：必须消费已验证的 Evaluation signals，并保持 bounded、可终止、可计费。
- Supervisor：必须用结构化 decision contract；自然语言 prompt 不替代 router、budget、authorization policy。
- Multi-Agent：必须先定义独立 branches 和 merge/data/runtime contracts；并发不是默认。
- Evaluation：保持 read-only；生产 governance、automatic gates、approval flow 必须单独评审。

## Backlog

- Pydantic/msgpack Async SQLite serialization warnings 与跨版本 checkpoint 策略。
- router early termination 后 callback/UI 一致性。
- `legacy_agent` 的最终退役或受限再接入方案。

## 下一会话启动

```powershell
git status --short --branch
git log -3 --oneline --decorate
& .\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-local
```

开始任何实现前，重确认以上 guardrails：不改 Graph/router/Writer-input/legacy fields；显式 double-write、Evidence failure isolation、runtime ownership、Evaluation read-only boundary 均不得退化。
