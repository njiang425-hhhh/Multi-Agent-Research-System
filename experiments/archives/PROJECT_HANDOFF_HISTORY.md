# ResearchOS / 多智能体研究系统 - 交接基线

> 本文是下一会话唯一交接基线；若与历史 handoff、提交记录或旧测试结论冲突，以当前源码和本文为准。
>
> 最后更新：2026-08-23。ResearchOS 是轻量级、个人/学习/展示型 multi-agent research project。P0-P17 已关闭；P11-P13 作为 Advanced Architecture Exploration 已完成并冻结。没有自动 action dispatch 或生产 rollout。

## 目录

1. Project Scope / Positioning（项目定位）
2. Project Snapshot（项目快照）
3. Current Decision Point（当前决策点）
4. Definition of Done（完成定义）
5. Current Architecture（当前架构）
6. End-to-End Research Flow（端到端研究流程）
7. Core Data Flow / Agent I/O（核心数据流与 Agent I/O）
8. Capability Status Matrix（能力状态矩阵）
9. Development Guardrails / DO NOT BREAK（开发护栏）
10. Milestone Map（里程碑地图）
11. Optional Future / Productionization（可选未来工作）
12. Detailed History / Appendix（详细历史与附录）
13. 下一会话启动

## Project Scope / Positioning

ResearchOS 是一个 **lightweight multi-agent research project**，面向个人使用、学习和能力展示。它的优先级是让四 Agent 研究主链可运行、结果可解释、来源/provenance 可追溯，并用小而稳定的 contract 展示研究系统的工程边界。

- **核心目标**：Query 到 plan、search、findings、带引用 report 的完整研究体验；可重复的质量/coverage 评估；轻量 Memory 与 bounded adaptive research 的演示能力。
- **非目标**：将此仓库演进为 enterprise production-grade Agent Platform，或把架构实验变为必须上线的治理平台。
- **工作取向**：优先 Agent research capability、可解释性、可运行性和 showcase；复杂治理/执行机制可以保留为设计与代码实验，但不驱动主路线。

### Explicit Non-Goals

- production RBAC、OIDC 或 SAML 集成。
- enterprise approval、audit 或合规留存平台。
- production SLA、rollout governance 或 on-call 运营体系。
- complex distributed action runtime、跨服务事务或 production exactly-once workflow guarantees。
- provider fallback、circuit breaker 或跨 provider 调度策略。
- 没有真实 Agent 需求时的 Graph V2。
- arbitrary autonomous action execution。

## Project Snapshot

| 项目 | 当前事实 |
|---|---|
| 项目定位 | **轻量级、个人/学习/展示型 Multi-Agent Research System**；不以 enterprise production readiness 为目标。 |
| 核心研究能力 | 面向来源的研究 Agent：把用户 Query 转为结构化计划、搜索来源、综合 Findings 和带引用的 Report，同时保持 runtime、provenance 与 Evaluation 边界。 |
| 当前阶段 | P0-P17 已关闭。P11-P13 是已完成、冻结的 Advanced Architecture Exploration，不要求 productionize；P14/P15 完成 bounded adaptive research 与 local memory showcase，P16 完成真实 archive showcase，P17 完成 release documentation/cleanup。 |
| Graph | 固定线性 Graph V1：`Planner -> Searcher -> Synthesizer -> Writer`。现有 router、Writer 输入、legacy 主链和 Report contract 不变。 |
| Provider / Search | 当前默认运行组合：DeepSeek + Tavily + `deterministic_v2`。`SearchExecutor` 拥有确定性 search 与 round-robin-by-query extraction ordering；`legacy_agent` 仅显式兼容。 |
| 默认开关 | `EVIDENCE_ANALYZER_ENABLED=false`；`RESEARCH_MEMORY_ENABLED=false`；`WRITER_SECTION_EXECUTION_MODE=serial`；`SEARCHER_ADAPTIVE_ENABLED=true`，但 hard cap 固定为 1 round。Writer `bounded` 需显式开启，实验 bound 默认 `2`。 |
| 测试基线 | fake-only 全量：**364 passed，2 个既有 Pydantic deprecation warnings**。CI 使用 Python 3.11 与固定 pytest `--basetemp`；真实 provider 验证仅手工执行，不进 CI。 |
| Advanced experiment | P11 advisory、P12 eligibility、P13 ledger/human-review handler 展示治理设计能力；仍保持 contract-only/default-off，不是项目的核心产品路径。 |
| 下一步 | **DONE**。后续仅按 Optional Future / Productionization 中的独立需求重新立项。 |

## Current Decision Point

**主线已经从治理执行探索切回 Agent capability。** P11.1、P12.1、P13.1 与 P13.2 作为 **Advanced Architecture Exploration** 已完成并冻结：它们保留为可阅读、可测试的 contract/control-plane 示例，但不继续推进为 production human-review workflow、企业审批系统或自动 action 平台。

核心 capability closure 已完成。README、handoff、demo 和 manual showcase entry points 已统一到当前的 lightweight research showcase 定位；后续不再有主线 backlog。

保持现有护栏：Graph V1、router、Writer input、legacy contracts 与 Runtime ownership 不变；Evaluation 继续 read-only，不能成为 production gate。P13 的 runner production integration、RBAC、approval SLA、enterprise audit 与 rollout 统一移入 Optional Future / Productionization。

## Definition of Done

项目在满足以下条件时即可收尾，不需要 enterprise productionization：

- 四 Agent 主链稳定运行：Planner、Searcher、Synthesizer、Writer 能完成来源驱动的报告输出。
- 至少完成 3-5 个真实 showcase tasks，并能展示 plan、来源、findings、report 与关键运行信息。
- planning、coverage、report evaluation 可复现，且保留其 dataset/config/snapshot 或 archive。
- 至少一个轻量、bounded adaptive research 能力可演示。
- P8 Memory 能以受限、可解释的方式演示。
- README、architecture、quick start 和 demo 资料完整。
- fake-only regression 稳定；真实 provider 验证保持手工 showcase，不作为 CI 或 production gate。

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
        |      +--> P14 coverage 不足时最多一次 supplementary search（仍经 SearchExecutor）
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

### Core / Showcase Capabilities

| 能力 | 状态 | 默认 | 当前决策 / 限制 |
|---|---|---|---|
| Graph V1 线性研究流 | Implemented | Enabled | 保持 `Planner -> Searcher -> Synthesizer -> Writer`。 |
| Deterministic Search | Implemented | Enabled | `deterministic_v2` + `SearchExecutor`；`legacy_agent` 仅兼容。 |
| Evidence sidecar | Implemented | Disabled | P5/P6 不足以支持默认启用或 selector。 |
| Writer serial scheduling | Implemented | Enabled | 展示默认保持 serial。 |
| Writer bounded sections | Implemented | Experimental | 显式 `bounded`，默认 bound=2；P7 仅证明本地 Writer 加速。 |
| Research Memory V1 | Implemented | Disabled | SQLite/lexical/provenance baseline；P15 提供显式 local fake demo 与启用时 diagnostics，普通 run 默认关闭。 |
| Planning Enhancement | Implemented | Enabled（既有 Planner 内） | P9 是 prompt/normalization + fake-only lexical baseline；不证明真实 provider 质量。 |
| Offline Evaluation / calibration / repeatability | Implemented | Offline only | read-only；snapshot 绑定 evaluator、config 与 dataset content。 |
| P10 Research Coverage | Implemented | Enabled inside deterministic_v2 | extraction candidates 按 originating query round-robin；不增加预算，不改 Graph/router/Writer/runtime ownership。 |
| P14 Light Reflection / Adaptive Research | Implemented | Enabled | Searcher 内 primary coverage 后最多一次 supplementary search；不改 Graph、Writer 或 Runtime ownership。 |
| P16 Showcase & Evaluation | Closed | Manual only | 4 个固定真实任务、per-case archive、P14/P15 diagnostics 与 P5/P9/P10 read-only evaluation。 |
| P17 Documentation / Release | Closed | N/A | README、architecture、quick start、demo、showcase snapshot、cleanup 与 release notes。 |

### Advanced Architecture / Future

| 能力 | 状态 | 默认 | 当前决策 / 限制 |
|---|---|---|---|
| Quality-to-Action Advisory Contract | Implemented | Offline only | P11.1 统一 P5/P9/P10/runtime observations；只输出 `candidate/blocked/unavailable` advisory，不写 State 或触发 action。 |
| Action Authorization & Eligibility Contract | Frozen exploration | Contract only | P12.1 的 deterministic eligibility design；不是核心产品路径，也不接入自动执行。 |
| Generic Action Execution Infrastructure | Frozen exploration | Control plane only | P13.1 的 immutable request/ledger/CAS/recovery 示例；无 dispatcher 或 State transition。 |
| Checkpointed Human Review | Frozen exploration | Explicit/default off | P13.2 的 fake-only handler/receipt/resume-adapter contract；不做 production runner integration。 |
| Provider fallback / circuit breaker | Optional Future | N/A | 仅在真实多 provider 需求出现时评审。 |
| Evidence selector | Future | N/A | P6 evidence sufficiency 未满足。 |
| Supervisor / parallel branch-join | Optional Future | N/A | 不属于 P14 light adaptation；须由真实 Agent capability requirement 驱动。 |
| Graph V2 | Optional Future | N/A | 只有 Graph V1 无法承载明确 showcase/capability requirement 时才评审。 |
| Production human approval / enterprise action platform | Optional Productionization | N/A | P13.3 runner integration、RBAC、audit、SLA/rollout 都移入此处，不是 backlog。 |
| Semantic/vector memory | Optional Future | N/A | P15 只激活现有 local lexical baseline。 |

## Advanced Architecture Exploration (Frozen)

P11-P13 统一定位为 **Advanced Architecture Exploration**：它们展示 advisory、authorization、durable control-plane 与 checkpointed human-review 的治理设计能力，保留重要的确定性测试、边界与历史决策；它们不是 core research capability，也不构成继续建设 enterprise execution platform 的承诺。除非未来出现明确的个人/showcase Agent 需求，否则不做 production runner integration、RBAC、approval SLA、enterprise audit 或自动 action dispatch。

**P10 Research Coverage 已关闭。** P10.1 建立 measurement-only baseline；P10.2 在 `SearchExecutor` 内实现 deterministic extraction coverage ordering。它不改变 Graph、router、Writer 输入、runtime ownership、默认预算或 provider policy。

**P11.1 Quality-to-Action Advisory Contract 已完成。** 新增 Evaluation-only contracts 与纯函数 adapters，将 P9 planning、P10 research coverage、P5 run/evidence/report quality、以及 runtime lifecycle/error/trace/usage observations 组合为 advisory recommendation。`ActionRecommendation.advisory` 固定为 `true`，状态只允许 `candidate`、`blocked`、`unavailable`；不写入 `ResearchState.next_action`，不修改 `status/current_stage/terminal_reason`，不触发 Graph edge，也不调用 Planner/Searcher/Writer。

P11.1 三层边界：

- observed：Runtime adapter 只读取 `status`、`current_stage`、`terminal_reason`、`error`、`agent_trace`、`usage`；它不改变 Runtime lifecycle ownership。
- derived：P5/P9/P10 evaluator metrics 通过纯 adapter 映射为 `QualitySignal`；既有 evaluator 语义不变，fake-only / rubric 结果仍不等于真实语义质量。
- advisory：resolver 只返回结构化 action candidate/block；Runtime hard-stop facts 优先；`unavailable` 不等于 passed；failed signal 不自动获得执行权限。

P11.1 contract snapshot 绑定 evaluator version、policy version、metric thresholds、source fields 与 input fingerprint；threshold policy 进入 snapshot/fingerprint。当前 action taxonomy 为 `continue`、`accept_partial`、`retry_research`、`replan`、`stop_fail`、`human_review`，但本阶段所有输出都保持 advisory。

**P12.1 Action Authorization & Eligibility Contract 已完成。** 新增独立、deterministic、read-only contract layer。`ActionAuthorization` 明确 allowed action、target stage/node、max executions、local budget、deadline/no-progress/failure/partial policy 和 durable provenance；`ActionEligibilityDecision` 独立使用 `eligible/blocked/unavailable`，不会改变 P11 `ActionRecommendation.candidate/blocked/unavailable` 的 advisory 语义。

P12.1 三层边界：

- authorization：显式的单 action authority，不是 executor request；`ActionProvenance` 为未来 durable ledger 预留 authorization/action identity、grantor、recommendation/state fingerprint 与 source signal IDs。
- eligibility：只读取 advisory、authorization、targetable deficit、`ExecutionContext` remaining deadline/operation budget、action-local limits、per-action preconditions 和 calibration/readiness；authorization provenance 必须 fingerprint-bind 到当前 advisory。effective deadline = `min(run remaining, authorization deadline, local action limit)`；action budget 只能收紧 Runtime remaining budget，不能重置或扩大。
- execution：仍未实现。没有 action queue、ledger、retry loop、State merge、checkpoint mutation、Graph edge、Agent/Graph/provider call 或 Evaluation production gate。

P12.1 readiness validators：`retry_research` 要求 `search` target、coverage deficit、selectors、显式 business-retry-not-operation-retry 和 bounded search/extract/operation budget；`replan` 要求 plan target、plan replacement 与 downstream invalidation contract；`accept_partial` 要求 product partial-output contract；`human_review` 要求 approval scope 和 approval workflow contract。每个 validator 只返回未来 State merge/replacement/invalidation/attribution rule representation。operation retry 仍是 Runtime-owned 的同一 provider/tool attempt retry；business-level `retry_research` 是独立授权、独立 target/budget 的未来 action。

**P13.1 Generic Action Execution Infrastructure 已完成。** `ActionExecutionRequest` 将 action/request/run/thread/target、authorization/recommendation/state binding、expected checkpoint revision、effective budget/deadline snapshot 与 idempotency key 固化为 immutable request。独立 SQLite ledger 记录 `queued`、`claimed`、`executing`、`waiting_approval` 及六种 terminal lifecycle，包含 revision、attempt、worker/lease owner、timestamps、input/output/state-transition fingerprints、error 与 recovery marker。

P13.1 四层边界：

- request：只表达一个已授权 logical action，不是 dispatch command；duplicate request 只返回同一 action，content conflict 被拒绝。
- ledger：CAS revision + action/idempotency uniqueness 只保证一个 logical action 和一次 logical commit record；external effect 仍是 at-least-once，crash after external execution before commit 会安全终结为 unknown outcome，绝不自动重放或 commit State。
- executor：仅定义 `ActionExecutor` interface，未注册 handler、未自动 dispatch、未调用 Agent/Graph/provider。
- State transition：仍未实现。commit 仅记录 future transition fingerprint；未来 applier 必须在 Runtime-owned checkpoint boundary 重验 authorization/checkpoint/state/runtime，并负责 actual State mutation。

P13.1 lease/runtime：claim、executing marker、waiting marker、commit 与 recovery 都短时取得现有 checkpoint `thread_id` lease；waiting 不持锁。每次 claim/commit 重验 run/thread、authorization/recommendation/state fingerprint、checkpoint revision、deadline、operation budget 与 cancelled/terminal facts。action budget 只能收紧 current Runtime remaining budget，不能重置 `ExecutionContext`。trace metadata 已定义 action/request/authorization/attempt/transition/worker/lease/error/recovery attribution，但未改变 Usage 或既有 Trace store。

**P13.2 Checkpointed Human Review 已完成。** `CheckpointedHumanReviewHandler` 只接受 `human_review` 的 eligible、authorization-bound request，先将 ledger 从 `queued -> claimed -> waiting_approval`，然后释放 thread lease。`ApprovalRequest` 固化 allowed role、run/thread、authorization、checkpoint/state binding；`ApprovalPayload` 是明确的 approve/reject 人工输入；SQLite `ApprovalReceipt` 以 action/idempotency 唯一约束 first terminal approval wins。

P13.2 approval/resume：相同 payload 返回同一 receipt，冲突 approval 被拒绝；reject terminalizes action 且不调用 resume adapter。approve 只产生 persisted pending receipt，**不会自动 resume**。runner boundary 必须显式调用 handler 的 `resume_approved`；它重验 authorization/checkpoint/state/runtime 后，从 `waiting_approval` CAS claim，记录一次 resume attempt，再通过 injected `CheckpointResumeAdapter` 恢复唯一已有 queued checkpoint。重复 resume 返回同一 receipt；receipt/ledger recovery 不会自动再次调用 adapter。

P13.2 继续不写研究内容或 `ResearchState` 业务字段，不新增 Graph node/edge/router branch，也不改变 Usage。deadline/cancel 在 approval 或 resume 前均 terminalize 为 `expired/cancelled`；waiting 仍消耗 absolute run deadline。approval receipt、resume request 与 terminal result 绑定 action/request/authorization、approver、ledger attempt 与 checkpoint revision。adapter 是 runner-boundary contract，生产 integration 与 real workflow calibration 仍未 rollout。

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
- P14 只在 Searcher 内进行一次 deterministic coverage check；supplementary search 固定最多 `1 search + 1 extract`、retry=0、local timeout<=30s，复用剩余 Runtime context。它不 replan、不新增 query/objective/outline、不改 Graph/router/Writer input；无新 URL/content/coverage、timeout/error 或 Runtime 容量不足只进 `search_diagnostics`，继续 primary 输出。
- P15 只观察既有 P8 local lexical memory：默认 `RESEARCH_MEMORY_ENABLED=false`；启用时 `memory_diagnostics` 记录 retrieved IDs、score/matched terms/provenance、Planner context 与 Searcher site hints，显式 demo 记录 write count。它不进入 Writer、不改 Graph/router/top-level error，也不构成 vector/semantic/personalized Memory V2。
- 不得从单个真实样本或 fake-only 结果推导生产策略；后续决策必须保留 observed/derived 区分和 archive 路径。

## Milestone Map

| 阶段 | Milestone | 状态 | 目的 / 核心改动 | 关键决策 |
|---|---|---|---|---|
| Foundation & Runtime | P0-P2 | Closed | 建立 V1 contracts 与安全研究原语：显式 State double-write、LLM/Search 基础、optional Evidence sidecar | 保留 legacy path 与 failure isolation。 |
| Foundation & Runtime | P3 | Closed | lifecycle、checkpoint/resume/cache replay、Trace、read-only Evaluation | Runtime semantics 与 observation 不进入业务 routing。 |
| Foundation & Runtime | P4.1-P4.5 | Closed | persisted policy/lease、deadline/budget/retry、provider/LLM contracts、Writer profile、trace retention、snapshot binding | Runtime 拥有 global controls；provider/agent 不选 fallback 或跨 service retry。 |
| Research Quality | P5.1 | Closed | 6 fixed cases、quality rubric、regression gate | quality evaluation 是 deterministic/read-only，不进生产 routing。 |
| Research Quality | P5.2 | Closed | disabled/enabled/partial fake benchmark | benchmark 不编码 selector/default policy。 |
| Research Quality | P5.3 + closure | Closed | manual DeepSeek/Tavily archive 与 observed/derived comparison | 不默认启用 Evidence；不启动 provider/Graph V2 项目。 |
| Research Quality | P6 | Closed | reference fixtures、repeatability harness、two-round closure | Evidence selector、Writer optimization、provider resilience 均 insufficient/not assessable。 |
| Research Quality | P7 | Closed | bounded=2 scheduler、shared coordinator、deterministic assembly | serial 保持默认；不继续扩展性能项目。 |
| Research Quality | P8 | Closed | source-backed SQLite、lexical retrieval、bounded Planner/Searcher injection | default-off；不引入 Memory Agent/vector DB。 |
| Research Quality | P9 | Closed | 6-case fake baseline、purpose labels、prompt/normalization | 不引入 replan、额外 LLM call、Graph change。 |
| Research Quality | P10.1-P10.2 | Closed | coverage metrics 与 fixed-budget deterministic extraction ordering | 不增加预算、不引入 replan/Graph V2。 |
| Governance Exploration | P11.1 | Frozen | QualitySignal/ThresholdSpec/SignalProvenance/ActionRecommendation；deterministic advisory matrix | Evaluation-only；不写 State、不自动执行 action。 |
| Governance Exploration | P12.1 | Frozen | ActionAuthorization/Eligibility/Target/Budget/policies/provenance；validators 与 future transition rules | Contract-only；不接入自动 action。 |
| Governance Exploration | P13.1-P13.2 | Frozen | ledger/CAS/recovery 与 default-off human-review handler/resume-adapter contract | 不做 production runner integration、enterprise approval 或 dispatch。 |
| Capability Closure | P14 | Closed | Searcher 内一次 coverage-triggered supplementary search，primary-first merge/dedup 与 diagnostics | 不引入 Supervisor、Graph V2、replan 或 action execution。 |
| Capability Closure | P15 | Closed | explicit local fake A/B/C demo、memory observability 与 SQLite connection cleanup | 保持 default-off；不做 semantic/vector/personalized memory。 |
| Capability Closure | P16 | Closed | 4 个真实 research showcase tasks、可复现 per-case evaluation/archive | 展示与学习，不形成 production gate；真实 failure 也保留为 observation。 |
| Capability Closure | P17 | Closed | README、architecture、quick start、demo、cleanup、release notes | **DONE**；不把冻结 exploration 或 Optional Future 重新放回主线。 |

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
- P11.1：advisory contract 关闭；P12.1 已补齐 authorization/eligibility，P13.1 已补齐 generic execution control plane；三者均未获得业务 action execution permission。
- P12.1：authorization/eligibility contract 关闭；P13.1 已提供 request/ledger/lease/idempotency/recovery control plane，但 action-specific handler、checkpoint pause/resume、actual State transition 与真实 workload evidence 仍需独立评审。
- P13.1：generic action execution infrastructure 关闭；它不等于 executable action。首个具体 action 仍需独立 handler、State transition、checkpoint/recovery product protocol 与 calibration。
- P13.2：checkpointed human-review handler 关闭为 fake-only/default-off baseline；它不等于 production approval workflow。仍需真实 runner adapter integration、approval UX/identity/audit retention、SLO、timeout/cancel recovery 演练与 real-workflow calibration。
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
- P11.1 当前结论：advisory contract implementation complete；不建议直接进入自动 Reflection。P12.1 与 P13.1 已补齐 eligibility 和 generic execution control plane；仍须在 action-specific handler、checkpoint pause/resume、State transition 与真实 workload calibration 齐备后，才可评审 executable action。

### J. P12.1 Action Authorization & Eligibility Contract Closure

- 新增 `src/evaluation/action_authorization.py` 与 `tests/test_action_authorization.py`；`src/evaluation/__init__.py` 仅导出纯 contract API，不接入生产 Graph 或 Runtime execution path。
- contracts：`ActionAuthorization`、`ActionEligibilityDecision`、`ActionTarget`、`ActionBudget`、`ActionDeadlinePolicy`、`NoProgressPolicy`、`ActionFailurePolicy`、`ActionPartialPolicy`、`ActionProvenance`，并以 `StateTransitionRule` 表示未来 merge/replacement/downstream invalidation/attribution 要求。没有 execution ledger。
- eligibility：valid P11 advisory + matching explicit authorization + complete targetable deficit + non-exhausted Runtime deadline/operation budget + bounded action budget/local deadline + readiness `ready` + validator preconditions 才能得到 `eligible`。任何 recommendation 仍是 advisory；Evaluation 无法成为 production gate。
- runtime boundary：effective deadline 取 run remaining、authorization deadline、local action timeout 的最小值；action budget 只取 Runtime remaining 的更小值。operation retry 只重试相同 provider/tool operation，business `retry_research` 永远需要单独 authorization/target/budget。
- future state rules：retry research 仅能 target-scoped append/dedupe 并 invalidate downstream derived outputs；replan 需要 replace plan 和 invalidate all plan-dependent outputs；accept partial 不 merge content，只声明 delivery semantics；human review 只产生 approval record。P12.1 不执行这些规则。
- P12.1 validation：targeted `tests/test_action_authorization.py tests/test_quality_action.py` = `27 passed, 1 warning`；full fake-only pytest = `317 passed, 2 warnings`。覆盖 missing/mismatched authorization、provenance binding、incomplete target、runtime deadline/budget exhausted、budget narrowing、calibration insufficiency、retry boundary、replan invalidation、partial/approval preconditions、determinism、input immutability 和 no external calls。
- P12 closure recommendation：可以关闭 **P12.1 contract sub-phase**，但不能关闭 P12 execution readiness。首个可能评审的 executable candidate 是 `human_review`，因为它可以先实现为 checkpointed approval wait/resume，不改变 research content；仍需 approval workflow、durable ledger、lease/at-least-once idempotency、timeout/cancel 和真实 workload evidence。

### K. P13.1 Generic Action Execution Infrastructure Closure

- 新增 `src/action_execution.py` 与 `tests/test_action_execution.py`。模块不导入或调用 Graph、Agent、provider、Writer 或 `ResearchState`；`ActionExecutor` 只有 protocol interface，没有 handler 或 dispatcher。
- `ActionExecutionRequest` 绑定 `action_id/request_id/run_id/thread_id`、action target、authorization id、recommendation/authorization/state fingerprints、expected checkpoint revision、effective budget/deadline、idempotency key 与 created timestamp。request immutable；cache replay 使用新 run，不能复用 source action idempotency identity。
- SQLite ledger 使用 action-id/idempotency-key uniqueness 与 revision CAS。lifecycle：`queued -> claimed -> executing|waiting_approval -> succeeded|rejected|no_progress|expired|failed|cancelled`；entry 保存 attempt、worker/lease owner、timestamps、input/output/state-transition fingerprints、error、recovery marker 和 claim-time constraints。
- recovery：`claimed` crash 回到 `queued`，可由新 worker reclaim；`executing` crash 终结为 `failed/external_execution_uncommitted`，不自动重试外部 effect 或写 State。相同 terminal outcome 的 duplicate commit 返回同一 entry；不同 outcome 被拒绝。
- Runtime bridge：control plane 接收 Runtime-owned current view 并在 claim/commit 重验 all bindings、checkpoint revision、deadline/budget/cancel/terminal facts；short lease 使用与 checkpoint runner 相同 path，waiting return 后 lease 已释放。P13.1 只记录 future state-transition fingerprint，不实施 merge/replacement/invalidation。
- P13.1 validation：targeted `tests/test_action_execution.py tests/test_action_authorization.py tests/test_quality_action.py` = `43 passed, 1 warning`；full fake-only pytest = `333 passed, 2 warnings`。覆盖 duplicate request/claim/commit、lease conflict、stale checkpoint/authorization、deadline/cancel、before/after execution recovery、terminal suppression、waiting lease release、cache replay isolation、budget narrowing、deterministic recovery、trace attribution 和 no external calls。
- P13.1 closure recommendation：可以关闭 generic control-plane sub-phase；**不**可关闭 P13 executable-action readiness。下一步候选仍是 checkpointed `human_review`，但须先独立实现 approval payload、pause/wait/resume adapter、terminal/timeout/cancel semantics、actual Runtime checkpoint integration 与真实 workflow calibration。

### L. P13.2 Checkpointed Human Review Closure

- 新增 `src/human_review.py` 与 `tests/test_human_review.py`。handler 不导入或调用 Graph、Agent、provider、Writer 或 `ResearchState`；实际 checkpoint continuation 只能由 injected `CheckpointResumeAdapter` 在 runner boundary 执行。
- lifecycle：eligible + authorization-bound `human_review` 明确进入 `queued -> claimed -> waiting_approval`，随后 lease 释放。approval receipt first-wins；approve 只进入 `pending` resume state，reject 将 action terminalize 为 `rejected`。没有自动 dispatch 或 automatic approval policy。
- approval validation：提交时重验 action/request/authorization/run/thread/checkpoint/state binding、approver role、policy version、Runtime deadline/budget/cancel/terminal facts。相同 payload idempotent，冲突 payload 拒绝；expired/cancelled action 不接受新 approval。
- resume/recovery：显式 `resume_approved` 才能 claim waiting action 并调用 adapter；duplicate resume suppressed。crash while waiting 保留 waiting；crash after approval before resume 保留 pending receipt；crash after resume result/ledger commit before receipt update 可收敛为 resumed；uncertain adapter execution 被标记 unknown，禁止自动重放。
- P13.2 validation：targeted `tests/test_human_review.py tests/test_action_execution.py tests/test_action_authorization.py tests/test_quality_action.py` = `53 passed, 1 warning`；full fake-only pytest = `343 passed, 2 warnings`。覆盖 approve/reject、identical/conflicting approval、stale authorization/checkpoint、deadline/cancel、waiting/approval recovery、duplicate resume、lease conflict/loss、terminal reopen suppression、role/scope、approved-before-resume no external call。
- P13.2 closure recommendation：可以关闭 fake-only/default-off handler sub-phase；**不**可关闭 P13 production action readiness。继续保持 Graph V1，不实现 `retry_research`、replan、Reflection、Supervisor、Graph V2 或 provider fallback。生产前仍需真实 runner integration、authenticated approver identity/role source、audit retention/privacy、approval SLO、timeout/cancel/lease failure drill、at-least-once recovery 与 real-workflow calibration。

### M. P14 Light Reflection / Adaptive Research Closure

- `ResearchSearcher._search_with_executor()` 保持 Graph V1 的单个 Searcher node：`primary search -> pure query coverage -> optional supplementary search -> primary-first merge/dedup -> existing credibility/documents -> Synthesizer/Writer`。没有 Reflection node、Graph/router 改动、Supervisor、replan、LLM query generation、P11-P13 action framework 或 Graph V2。
- 新增 `src/search/coverage.py` 的 deterministic pure helper。它仅按既有 `ResearchPlan.search_queries` 与 `SearchResult`/`Document` 的 normalized query association 计算 `result_query_coverage`、`extracted_query_coverage` 和按原 plan 顺序的 missing query lists；不导入 Evaluation，也不让 P10 evaluator 变成 runtime gate。
- Trigger 需要 primary 有可用 URL、result 或 extraction coverage 小于 `1.0`、`SEARCHER_ADAPTIVE_ENABLED=true`、round cap 仍为 1，且 shared Runtime context 尚有 deadline/operation budget。query 优先首个 missing result query，否则首个 missing extracted query；直接复用原 `SearchQuery.query`，不修改 plan。
- supplementary executor 固定 `max_search_times=1`、`max_extract_times=1`、retry=0、`total_timeout_seconds<=30`，并沿用 primary 后的 `ExecutionContext`，因此不会重置或扩大 Runtime deadline/budget。`SEARCHER_ADAPTIVE_MAX_ROUNDS` 只能是 0 或 1，生产硬上限仍是 1。
- `SearchExecutor.execute(..., exclude_urls=...)` 在 provider 返回后按 normalized URL 过滤 primary 已见 URL，使其不会进入 supplementary result/extract candidates。merge 保留 primary 顺序与 metadata；supplement 仅追加新 normalized URL，或在 primary 缺 content 时补 content。随后仍走已有 credibility scoring 与 document projection。
- `search_diagnostics` 保存 adaptive observation，不写 `llm_call_details`，不改变 Usage。duplicate-only/no new content/no coverage improvement、supplement timeout/error、或 Runtime capacity 不足都只标记 no-progress/failure/skip diagnostics，保持 primary 输出并且绝不发起第二轮。
- Observed fake examples：`alpha,beta` primary 仅覆盖 `alpha` 时，result/extracted coverage `0.5 -> 1.0`，一次 supplement `beta` 后进入既有流程；`alpha,beta,gamma` 时 supplement 后仍有 `gamma` missing，但 search calls 固定为 primary+supplement 两次，不启动第三轮。
- P14 validation：targeted `tests/test_search_adaptive.py tests/test_search_executor.py tests/test_searcher_documents.py tests/test_search_config.py tests/test_agent_trace.py tests/test_writer_report_migration.py` = `55 passed, 1 warning`；full fake-only pytest = `355 passed, 2 warnings`；`git diff --check` clean。覆盖 all-covered no supplement、missing result/extraction、still-missing no second round、primary-first content upgrade、seeded dedup/no-progress、no-content supplement、supplement failure、Runtime budget skip，以及 Graph V1/Writer trace contract regression。
- P14 closure recommendation：**closed / GO** for lightweight showcase capability. 不将 fake-only coverage improvement 推导为真实 provider quality，也不扩展为 autonomous action 或 production retry platform。

### N. P15 Memory Demo Activation Closure

- P15 复用 P8 contracts：source-backed records 仍只由 completed run 投影并 best-effort 写入本地 SQLite；Planner 只获得 bounded prior context，Searcher 只追加 bounded `site:<host>` hints 且仍经 `SearchExecutor`；Writer 不读取 memory；read/write failure 非致命。没有 Memory Agent、Graph/router 改动、vector DB/embeddings、semantic retrieval、personalization 或 top-level error 语义变化。
- 默认仍为 `RESEARCH_MEMORY_ENABLED=false`。显式入口是 `& .\.venv\Scripts\python.exe scripts\run_memory_demo.py`；它只执行 deterministic/local fake A/B/C showcase，不调用 LLM、Provider、网页或 Graph，也不会开启普通 research run 的 Memory。`--store-path` 可保留 demo SQLite；默认临时文件会清理。
- 启用 Memory 时，`memory_diagnostics` 记录 retrieval outcome、retrieved count/IDs、每条 lexical score/matched terms/grounding/provenance、Planner context IDs、Searcher hint query/count；显式 demo 的 Run A 记录 write count/IDs。该字段不进 Writer、Usage 或 routing。P8 SQLite store 同时修正为每次操作提交并关闭 connection，避免 Windows demo temporary DB 被锁。
- Observed fake demo：Run A 写入 `1` 条 source-backed record；related Run B `AI regulation enforcement risk tiers` 检索 `1` 条（score `1.0`，matched `ai/enforcement/regulation/risk/tiers`，`legacy_source_url` provenance），Planner context 使用该 ID，Searcher 产生一个 `site:eu.example` hint；unrelated Run C `coastal flood adaptation planning` 检索 `0` 条。memory-off 的 retrieved/context/hint 都为 `0`/empty。固定 fake plan 的 planned-query overlap 为 `1.0`、novelty 为 empty，result/extracted coverage delta 均为 `0.0`；这只证明 injection/provenance 观察路径，不代表真实 provider、规划或报告质量提升。
- P15 validation：targeted `tests/test_memory_demo.py tests/test_research_memory.py tests/test_search_adaptive.py tests/test_v1_task_plan_migration.py tests/test_usage_migration.py` = `31 passed, 1 warning`；full fake-only pytest = `361 passed, 2 warnings`；`git diff --check` clean。覆盖 completed write/incomplete skip、related retrieval、unrelated no-overmatch、retrieval limit、Planner retrieval/context diagnostics、Searcher hint-through-executor、read failure nonfatal、memory-off 无 injection，以及 demo A/B/C output。
- P15 closure recommendation：**closed / GO** for optional local-memory showcase. 不将 fake-only score/coverage observations 外推为语义记忆能力或真实质量改善。

### O. P16 End-to-End Showcase & Evaluation Closure

- 新增 `src/evaluation/showcase.py` 与 `scripts/run_showcase.py`。固定四个展示任务覆盖 comparison（EU AI Act 与美国联邦 AI governance）、evidence/research（AI-assisted mammography）、risk/implementation（regulated enterprise customer-support agents）和 trend/industry（AI data-center semiconductor supply chains）；按顺序调用既有 Graph V1 `run_research`，cache disabled、checkpoint enabled。没有新 Agent、Graph/router/Writer/Runtime 改动、provider fallback、replan、Supervisor、Memory V2 或 production gate。
- `run_showcase` 接受 injected runner，单个 case 发生异常时写出 failed state observation 后继续其他 case。每个 archive record 保存 input、ResearchPlan、executed queries、SearchResult/Document、findings/report/citations、Trace/Usage、wall time/status/error、P14 adaptive diagnostics、P15 memory diagnostics 和 P5/P9/P10 evaluator outputs。`archive_showcase` 写 `artifacts/showcase/<timestamp>/summary.json`、`summary.md` 及 `cases/<case>.json`、`cases/<case>.report.md`；JSON 是 source of record。
- P5 `evaluate_run`、P9 `evaluate_planning_quality` 和 P10 `evaluate_research_coverage` 都在 run 后对 copied State 只读执行；P16 static lexical facets / quality rubric 只用于 archive observation，绝不是 runtime gate、provider quality conclusion 或改进 request。P5 grounded-citation 仍要求 existing Evidence records；默认 Evidence sidecar disabled 时该 metric 的 failed/unavailable 是预期可观察结果，不由 showcase 修复。
- Memory 默认 off。CLI 只有 `--memory-case <selected-case>` 才在 exactly one case 上暂时启用既有 P8 SQLite memory 并使用 archive-local DB；Runner finally 恢复 config。实际 P16 round 未选择 memory case，四项 retrieved/context/site-hint 均为 0/empty；P15 local fake A/B/C 保留为 dedicated memory demonstration。
- Actual manual DeepSeek + Tavily round archive：`artifacts/showcase/20260823T125821Z/`。4 cases all executed: comparison `completed` (354.711s), clinical evidence `failed` (175.527s, existing Writer `Operation deadline exhausted`), risk/implementation `completed` (290.570s), trend/industry `completed` (275.457s). This is observed reliability data, not a retry or a production recommendation.
- Actual adaptive observations: comparison and trend each triggered the hard-capped single supplementary search because extracted query coverage was `0.666667` while result coverage was `1.0`; both supplementary Tavily searches returned no result, kept `0.666667 -> 0.666667`, and correctly recorded `no_progress` without a second round. Clinical and risk cases were `not_needed` at primary result/extraction coverage `1.0/1.0`.
- Actual P5/P9/P10 archive observations: all four had P5 `source_coverage=passed`; completed comparison/risk/trend had `report_completeness=passed` while clinical was unavailable after Writer failure. Grounded citation was failed for completed cases (Evidence sidecar default off) and unavailable for clinical. P10 result coverage was `1.0` for all; P10 extracted coverage was comparison `0.75` and all other cases `1.0`. P9 lexical facets had mixed scores, so they remain descriptive prompt/plan observations rather than acceptance criteria.
- P16 validation: targeted `tests/test_showcase.py` = `3 passed, 1 warning`; full fake-only pytest = `364 passed, 2 warnings`; `git diff --check` clean. Tests cover deterministic archive shape, per-case reports, P5/P9/P10 post-run output, read-only input, adaptive/memory observation capture, default memory-off, one selected memory case, and failure isolation/secret redaction.
- P16 closure recommendation：**closed / GO** for the lightweight showcase. The archive proves end-to-end observability and preserves actual failure/no-progress behavior; it does not prove provider quality, sufficient evidence grounding, stable latency, or production readiness. P17 documentation/cleanup/release subsequently closed the mainline.

### P. P17 Documentation / Cleanup / Release Closure

- README 已重构为展示型入口：明确 lightweight multi-agent research system 定位、Planner/Searcher/Synthesizer/Writer 主链、P14 one-round adaptive search、P15 local lexical memory demo、Runtime/Trace/Usage/Evaluation 辅助边界，以及 P11-P13 frozen architecture exploration。新的 text architecture 图同时展示 primary search、optional supplementary search、optional memory hints 和 cross-cutting concerns；不暗示 Graph V2、Supervisor、replan 或 action execution。
- Quick Start 已覆盖 Python 环境、`.env` 的最小 DeepSeek + Tavily 配置、普通 CLI/API research run、deterministic local memory demo 和 manual P16 showcase runner（含 exactly-one memory case opt-in）。所有真实 provider 调用仍明确标为 manual-only，Evaluation 仍是 post-run/read-only。
- README 写入 P16 `artifacts/showcase/20260823T125821Z/` 的 observed snapshot：4 cases executed、3 completed/1 existing Writer operation-deadline failure、2 adaptive triggers/both no-progress、all source coverage passed、completed report completeness passed、Evidence default-off 导致 grounded-citation failed/unavailable。该表和 metrics 明确不是 provider/factual-quality、latency 或 production readiness 结论。
- Cleanup audit：移除了 README 中过期的“Evidence/Memory/Evaluation only reserved”、113-test baseline、旧项目能力列表和未接入 Evidence 描述；检查 `PROJECT_HANDOFF.md`、`scripts/run_memory_demo.py`、`scripts/run_showcase.py`，没有遗留将 P11-P13 推进为 production action、将 P16 作为 future work，或与 current default-off Memory/read-only Evaluation 语义冲突的入口。P11-P13 modules/tests、P5-P13 history 和 Optional Future 表保留，因为它们仍有测试覆盖和架构阅读价值。
- P17 不改 Graph/router、Writer input、Runtime ownership、provider policy、feature flags 或 State contracts；不新增功能。
- Final validation: full fake-only pytest = `364 passed, 2 warnings`; `git diff --check` clean. 建议 release commit message：`feat: complete ResearchOS showcase release`; 建议 tag/version：`v1.0.0-showcase`。
- P17 closure recommendation：**closed / GO / DONE**。ResearchOS 现在是可展示、可复现、可阅读的 lightweight multi-agent research project；未来工作只在出现明确新需求时从 Optional Future / Productionization 独立立项。

## Optional Future / Productionization

以下内容不是 P14-P17 backlog，也不是项目收尾条件。它们只在未来出现明确的 Agent/showcase 需求，或有人选择把项目独立 productize 时重新评审：

| 方向 | 定位 | 重新立项的最低条件 |
|---|---|---|
| Graph V2 | Optional Future | Graph V1 无法承载一个明确、可验证的 Agent capability；先定义 State/merge、预算、终止和迁移方案。 |
| Supervisor | Optional Future | 多个真实 next-action 路径需要结构化选择；不以自然语言 prompt 替代 router。 |
| Parallel multi-agent branch/join | Optional Future | 有独立子问题、join/merge、shared budget 与 attribution 的明确展示需求。 |
| Provider fallback / circuit breaker | Optional Productionization | 真实多 provider 需求与维护意愿出现；当前单 provider 研究系统不需要。 |
| Production human approval | Optional Productionization | 真实 runner integration、认证身份、审批 UX、audit/privacy、timeout/recovery 和运维责任被独立接受。 |
| Enterprise action execution platform | Optional Productionization | 有实际业务 action 与长期维护主体；当前 P11-P13 不构成承诺。 |
| Semantic/vector memory | Optional Future | P15 的 lexical memory showcase 已证明不足，且有清晰的检索/隐私目标。 |

**Graph V1 默认继续。** Reflection、Supervisor、branch/join 或 human approval 都不是 Graph V2 的自动触发器。任何 Graph V2 proposal 都必须先说明它服务的具体 Agent capability、最小成功证据、State/merge 约束、Runtime deadline/budget/cancel impact 与回退方案；否则保持当前线性主链。

## Maintenance Notes

- Pydantic/msgpack Async SQLite serialization warnings 与跨版本 checkpoint 策略。
- router early termination 后 callback/UI 一致性。
- `legacy_agent` 的最终退役或受限再接入方案。

## Future Maintenance Session

```powershell
git status --short --branch
git log -3 --oneline --decorate
& .\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-local
```

如独立 future work 被批准，开始前重确认以上 guardrails：不改 Graph/router/Writer-input/legacy fields；显式 double-write、Evidence failure isolation、runtime ownership、Evaluation read-only boundary 均不得退化。
