# ResearchOS / Multi-Agent Research System — Handoff

> 本文是新会话的唯一交接基线；与历史 handoff、提交记录或旧测试结论冲突时，以当前源码和本文为准。
>
> 最后更新：2026-08-20 · P0–P5 formally closed；P6 formally closed（deterministic calibration、two-round real repeatability 与 closure review）；工作区仍含未提交实现。

## 当前基线

- **Graph V1 保持不变**：`Planner -> Searcher -> Synthesizer -> Writer`；现有 router、Writer 输入和 legacy 主链均不变。
- **P0–P5 formally closed**：完成显式 State 双写、Evidence sidecar、lifecycle/checkpoint/lease、统一 execution policy/provider、LLM attempt accounting、Trace/Evaluation governance，以及 Research Quality / Evidence Value / Real Workload & SLO Baseline；P5 的目标是建立决策基线，不是改变生产策略。
- **验证基线**：fake-only 全量 **268 passed，2 个既有 Pydantic deprecation warnings**。CI 使用 Python 3.11 和固定 pytest `--basetemp`，真实 API 集成验证不进入 CI。
- 默认运行组合：DeepSeek + Tavily + `deterministic_v2`。`legacy_agent` 仅为显式兼容模式；`EVIDENCE_ANALYZER_ENABLED=false` 仍是默认值。

| 阶段 | 状态 | 已交付能力 |
|---|---|---|
| P0–P2 | COMPLETE | State/V1 显式双写、LLM/Search 基础、Evidence 保护性 sidecar。 |
| P3 | COMPLETE | run identity、terminal lifecycle、checkpoint/resume/cache replay、Trace、只读 Offline Evaluation。 |
| P4.1–P4.5 | COMPLETE | persisted runtime policy/lease、统一 deadline/budget/retry、Provider contract、LLM execution、Writer profile、Trace retention/redaction、content-bound Evaluation snapshot。 |
| P5.1 | COMPLETE | 6 个代表性 offline cases（success/partial/failure）、case-level quality rubric、deterministic regression gate；只读、fake-injected。 |
| P5.2 | COMPLETE | 同一 cases/rubric 的 Evidence disabled/enabled/partial fake benchmark；质量、adoption、usage/latency 及 per-case/tag delta；只读、fake-injected。 |
| P5.3 | COMPLETE | 独立手工 DeepSeek + Tavily harness、observed/derived JSON+Markdown archive、SLO/reliability/overhead summary；不进 CI。 |
| P5 closure review | FORMALLY CLOSED | 基于完整真实归档作有限推断：不默认/选择性启用 Evidence，不启动 Writer、provider resilience 或 Graph V2 架构项目。 |
| P6.1 | COMPLETE | 独立版本化、reference-backed 的 deterministic rubric calibration；reference expectation/rationale 与内容一起 fingerprint。 |
| P6.2 | COMPLETE | 注入式多轮 repeatability harness、per-run observed record、derived aggregate/dispersion、跨轮 matched comparison 与仅供评审的 initiative assessment；fake-only CI 覆盖。 |
| P6.3 | COMPLETE | P5.3 完整归档兼容地纳入 Round 1；完成一轮独立真实 Round 2，执行 two-round closure review。Round 3 按预声明 stopping rule 不执行。 |
| P6 closure review | FORMALLY CLOSED | 两轮真实样本已足以否定当前 initiative evidence sufficiency；不进入 Evidence selector、Writer optimization、provider resilience 或 Graph V2。 |

## 当前架构与 ownership

```text
CLI / Chainlit
  -> Graph V1
       Planner -> Searcher -> Synthesizer -> Writer
                    |             |
                    |             +-> legacy findings + optional Evidence sidecar
                    +-> deterministic_v2 (default) -> SearchExecutor -> Provider/Tools
                    +-> legacy_agent (explicit compatibility only)
```

| 边界 | 当前 ownership |
|---|---|
| Graph | 固定拓扑、既有 router、runner/checkpoint 入口；不承载业务 retry 或新能力编排。 |
| State / Agent | Agent 生成业务 patch；legacy/V1 字段只能显式双写，不做 alias、自动 hydration 或隐式同步。 |
| Runtime / Execution | Runtime 拥有 global deadline、operation budget、terminal reason、lease、cancel/resume 的 at-least-once 边界；service 只声明局部限制。 |
| Search / Provider | SearchExecutor 顺序执行、局部 search/extract 限制；Provider 只负责 transport、typed error、retryable/Retry-After。 |
| Evidence | optional sidecar、strict unique grounding、diagnostics；失败不得改顶层 error、router 或 Writer。 |
| Trace | logical append-only，checkpoint 物理 retention/compaction；不计 usage、不参与路由。 |
| Evaluation | 离线、确定性、只读；snapshot 绑定 evaluator、配置与实际 dataset/case 内容。P5.1 对 source coverage、grounded citation、report completeness 应用 case-level 阈值；P5.2 对同一 cases 输出 Evidence mode value comparison；P5.3 归档真实手工 workload 的 observed/derived 数据；P6.1 校准固定 reference oracle，P6.2 聚合重复运行。`grounded_citation` 仅是 URL/provenance grounding，不证明事实正确性或语义蕴含。均不进入生产路由。 |

### 必须保持的 invariants

- `run_id` 不等于 checkpoint `thread_id`；resume 保留 run identity，cache replay 是新 run，delivery 语义是 at-least-once。
- 默认 deterministic Search、Evidence 和 LLM adapter 共享 Runtime 的 deadline/budget；`legacy_agent` 不承诺该控制链，不能恢复为默认模式。
- Provider 不选择 fallback 或 circuit；Agent 不自建跨 service retry/timeout。
- Evidence compatibility findings 先于 sidecar；sidecar failure 只进入 diagnostics。
- Writer 继续消费 legacy 输入；Trace/Evaluation 只做观测与离线判断。

## 能力盘点：基础设施、近期缺口与远期目标

| 领域 | 已具备的基础设施 | 近期能力缺口 / 判断依据 | 远期目标 |
|---|---|---|---|
| Planning | structured plan、query/outline、LLM execution/trace/usage。 | 缺少计划质量基线、计划覆盖评估及“何时需要重规划”的业务规则。 | 可审计的计划修订与任务分解。 |
| Search | deterministic 顺序 SearchExecutor、provider factory、partial、global budget/deadline、typed errors。 | 单次 P5.3 看到 Evidence mode 的 provider errors，但没有多 provider、availability SLO、选择顺序或重复样本，因此不构成 resilience 项目依据。 | 分支式研究、来源策略与按需 provider resilience。 |
| Synthesis / Evidence | compatibility findings、strict grounding、optional Evidence sidecar、failure isolation；P5.2 fake benchmark 与 P5.3 real harness。 | 默认 sidecar 关闭；完整真实重跑已有 4（enabled）/5（partial）个 matched-success case，但只是一次 observed sample，且 enabled/partial 都出现 provider-error/partial diagnostics，不能升级为默认启用策略。 | evidence-aware research 成为可衡量、可配置的研究策略，而非无条件多加一个 Agent。 |
| Report | ordered serial sections、citation projection、attempt trace、read-only latency profile。 | P5.3 的 Writer/total 为 0.602–0.671、p95 为 354–379s，说明它是候选瓶颈；但没有明确用户 SLO 或重复样本，不满足优化项目的启动条件。 | 在明确契约后进行 bounded concurrency 或 partial composition。 |
| Evaluation | fixed dataset、deterministic read-only metrics、version/config/content snapshot；P5.1 quality rubric/regression gate、P5.2 Evidence value benchmark、P5.3 real archive/SLO。 | 已足以正式关闭“建立 baseline”的 P5；尚无 reference answers、重复 real samples 或可行动的稳定失败信号，不足以驱动自动治理/Reflection。 | 校准后的可比较质量回归体系；是否进入人工或自动治理须单独设计。 |
| Memory | State 已有 `MemoryItem`、retrieved IDs 等占位字段。 | 没有存储、检索、provenance、过期/删除、隐私或注入点，因此尚不是能力。 | 有来源与生命周期的 research memory。 |
| Reflection / Supervisor | State 已有 critic feedback、next action、supervisor decision 占位。 | 没有质量信号到动作的协议、循环上限、成本预算、checkpoint/approval 语义。 | 基于证据的修订循环与受控监督路由。 |
| Multi-Agent | 当前四个角色是固定串行职责，不是可调度的多 Agent 协作系统。 | 缺少 branch contract、join/merge、共享预算、公平取消和结果归因。 | 并行研究分支与可审计汇聚。 |

## 近期路线图：先证明需求，再改变控制流

### 近期 — P5 Research Quality & Evidence Baseline

#### P5.1 COMPLETE — Offline quality baseline

- `researchos_offline_baseline` v2 有 6 个固定任务：政策比较、城市气候适应、半导体风险、临床证据转化，以及明确的 partial-evidence 和 provider-failure 场景。
- 每个 case 在 dataset 内容中持有 `ResearchQualityRubric`：最少 distinct sources、grounded citations、report sections、report characters 与 heading 要求；rubric 随 dataset content fingerprint 和 snapshot 变化。
- evaluator 只读取现有 State/Report/Evidence：`source_coverage` 统计 document URI，`grounded_citation` 要求报告 URL 与 `grounded`/`partial` 的有效 Evidence source URL 对应，`report_completeness` 检查固定报告下限。
- suite regression gate 比较 case `expected_outcome`，并要求非 failure cases 的三项 quality metrics 均达到 dataset 中的确定性通过率阈值。gate 仅是离线输出，不影响 run、Graph、router、Writer 或 runtime/evidence ownership。
- 测试仅将 local fake case runner 注入 `run_offline_evaluation`；CI 继续只运行 fake-only pytest。真实 provider/LLM workload 必须保持为独立、手工可选验证。

#### P5.2 COMPLETE — Evidence Value Benchmark

- `run_evidence_value_benchmark` 对同一 `researchos_offline_baseline` v2 cases 和 P5.1 rubric 顺序运行 `disabled`、`enabled`、`partial` 三种注入模式；不会构造 Graph、provider、LLM 或修改 returned State。
- 每个 mode 保留完整 P5.1 `OfflineEvaluationResult`，并汇总三项 quality pass rate、Evidence diagnostics/evidence record/grounded-report adoption，以及现有 persisted UsageMetrics 的 latency、LLM/tool calls、tokens。
- enabled/partial 都相对于 disabled 输出 per-case delta；只有 quality pass count 增长的 case 才标为 `value_observed`，其 tags 汇总为 `benefited_task_tags`。因此“哪些任务类型有收益”及 partial 的实际价值由可复现输入结果决定，而非由 benchmark 写死策略。
- benchmark snapshot 绑定 dataset、模式顺序与配置。默认 CI 测试使用 deterministic fake runner；真实 DeepSeek/Tavily 比较必须由显式手工 harness 注入该 runner，使用独立 configuration/snapshot，且不加入 CI。
- Evidence diagnostics 仍只是观测/sidecar 输出：sidecar failure、partial adoption、legacy Writer 输入和现有 failure isolation 完全不变。

#### P5.3 COMPLETE — Real Workload & SLO Baseline

- `scripts/run_real_workload_benchmark.py` 是独立、手工运行的 DeepSeek + Tavily 入口；它禁用 cache，顺序使用同一 P5.1 dataset 的 disabled/enabled/partial mode，并只在每个新 Graph run 的进程环境中临时切换 Evidence flags，随后恢复。它不属于 CI，也不写 `.env` 或生产默认配置。
- `src/evaluation/real_workload.py` 将 harness wall time、Graph node trace（Planner/Searcher/Synthesizer/Writer）、EvidenceAnalyzer attempts、UsageMetrics、provider/tool errors、retry/timeout/partial/failure 作为 **observed**；将 P5.1 quality、P5.2 value comparison、nearest-rank p50/p95、success/quality/provider-failure rate、Writer/total 比例作为 **derived**。
- archive 同时生成 `benchmark.json`（机器读取源记录）和 `benchmark.md`（人类报告），明确 observed/derived 分界。只有 disabled 与目标 mode 对同一非 failure case 都成功时，才计算 observed wall-latency / LLM / tool-call overhead；否则 comparison 标为 `inconclusive`，不能解释为收益。
- 余额恢复后已完整重跑，归档为 `artifacts/real-workload-benchmarks/20260819T042430Z/`（DeepSeek + Tavily、cache disabled、6 个固定 P5.1 cases × 3 modes，约 85 分钟）。三个 mode 都完成了全量 case 矩阵；无 timeout/retry。disabled 为 6/6 success、quality pass 0/6，source coverage / grounded citation / report completeness 为 1.000 / 0.000 / 1.000，p50/p95 为 265.158s / 357.798s，Writer/total 为 0.671；enabled 为 5/6 success、quality pass 1/6，三项为 0.833 / 0.167 / 0.833，Evidence adoption 0.800（35 grounded records），p50/p95 291.394s / 378.542s、Writer/total 0.602，4/6 case 有 provider errors、5 个 partial、1 个 failure；partial 为 6/6 success、quality pass 2/6，三项为 1.000 / 0.333 / 1.000，Evidence adoption 1.000（45 grounded records），p50/p95 305.003s / 354.473s、Writer/total 0.642，4/6 case 有 provider errors、6 个 partial、0 个 failure。
- 已得可比较的派生 comparison：enabled 相对 disabled 有 4 个 matched-success cases（`ai-regulation-overview`、`semiconductor-supply-chain`、`clinical-evidence-translation`、`coastal-adaptation-partial-evidence`），grounded-citation pass-rate +0.200、observed wall-latency overhead +266.679s、token/LLM-call overhead +79,332 / +34；partial 有 5 个 matched-success cases（前述四个加 `urban-climate-adaptation`），grounded-citation pass-rate +0.200、observed wall-latency overhead +125.747s、token/LLM-call overhead +119,911 / +43。两个 mode 的 benefited case 都仅为 `ai-regulation-overview`，tags 为 `policy, comparison`。这些均是单次 **observed data/derived metrics**，不构成生产策略建议；旧的余额耗尽归档保留作历史原始记录。

#### P5 closure review — FORMALLY CLOSED

P5 的交付目标（确定性 quality baseline、fake-only value benchmark、独立真实 workload/SLO harness，以及一次完整三模式真实归档）均已满足，因此 **P5 正式关闭**。下面的结论严格区分运行、质量与推断：

1. **Run success 不等于 quality outcome。** disabled 运行 6/6 success，但 quality pass 为 0/6（grounded citation 0）；partial 运行 6/6 success 但所有 case 都有 partial diagnostics。quality 是现有确定性 rubric 的 **derived** 结果，不能被 completion 覆盖。
2. **Evidence 不默认启用，也不选择性启用。** 单次观测显示 enabled/partial 的 grounded-citation pass-rate 各较 disabled +0.200，且 partial adoption 为 1.000；但 overall quality pass 仅为 1/6、2/6，enabled 还有 1 个失败，两个模式各有 4/6 case 出现 provider errors。唯一 benefited case/tag 是 `ai-regulation-overview` / `policy, comparison`，不足以定义稳定的 selector、阈值或默认策略。
3. **Writer performance 不立项。** 0.602–0.671 的 Writer/total 与 354–379s p95 是真实 **observed profile**，足以作为后续采样指标；没有用户 SLO、重复运行或 section correctness/partial-composition 契约，不满足 bounded concurrency 或 partial composition 的启动条件。
4. **provider resilience 不立项；Graph V2 不立项。** provider errors 和 partial diagnostics 证明 failure isolation 被观测到，但没有第二 provider、availability SLO、健康选择规则或重复失败分布。也没有任何要求 reflection、supervisor routing、并行 branch/join 的业务控制流证据；现有 Graph V1 不变。

#### P6.1–P6.2 COMPLETE — Evaluation calibration & repeatability harness

- `src/evaluation/calibration_dataset.py` 定义与 P5 workload 完全分离的 `researchos_quality_calibration` v1：完整 provenance/structure、来源不足、未 grounded 报告 URL、结构不完整四类 reference-backed fixtures。每例的 reference source、expected signal/count 和 rationale 都进入 `calibration_content_fingerprint`；任何内容变化都会改变 calibration 及 evaluator snapshot identity。
- `run_rubric_calibration` 只读取由调用方注入的 State-like fixture，并复用既有 evaluator 比较三项 metric 和可观察计数；输出 alignment，不写 State、不构造 Graph/provider/LLM，也不改变 P5 dataset 或 regression threshold。这里的 `grounded_citation` 明确仅验证 report URL 与有有效 Evidence 的 source URL 的 provenance 对齐，**不**验证事实正确性、claim support 或 semantic entailment。
- `run_repeatability_benchmark` 顺序调用既有注入式 P5.3 runner；每轮将完整 P5.3 result 保存为 `observed_runs`（嵌套 P5.3 继续自身 observed/derived 分界），并把 mode-level quality、wall latency、p95、Writer share/node latency、Evidence adoption、provider-error rate 的 count/mean/min/max/sample SD/CV 输出到 `derived_metrics`。
- 对 enabled/partial 的 Evidence value，P6.2 汇总每轮 P5.3 quality delta 和 observed wall delta；只有同一轮、同一 non-failure case 且 disabled/目标 mode 均 completed 的 pair 才进入跨轮 matched comparison。结果明确输出 matched-success rate、quality/grounded-citation/wall-time delta 的 dispersion 与 repeated-benefit rate；没有 pair 即为 unavailable/inconclusive，不作收益推断。
- `RepeatabilityDecisionCriteria` 仅生成 `InitiativeEvidenceAssessment`，从不修改生产配置、Evidence 开关、Graph 或 provider policy。Evidence selector 的充分证据要求至少 3 轮、same-case positive provenance delta 的 repeated/matched-success 稳定性以及无过度 provider-error 回退；Writer 与 provider resilience 分别还要求显式的 latency / error-rate SLO，缺失时标为 `not_assessable`。`sufficient` 仅表示可进入后续人工立项评审，不自动开始优化、selector、fallback 或 Graph V2。
- `scripts/run_repeatability_benchmark.py --rounds 3` 是 P6.3 手工 DeepSeek + Tavily 入口，cache disabled、`ci: false`，归档 JSON 与 Markdown；CI 仅运行 deterministic fake runner 测试。

#### P6.3 two-round closure review — FORMALLY CLOSED

Round 1 使用 P5.3 的完整真实归档 `artifacts/real-workload-benchmarks/20260819T042430Z/benchmark.json`；它可无转换解析为当前 `RealWorkloadBenchmarkResult`，与 Round 2 的 `artifacts/p6-repeatability-rounds/20260820T042404Z/benchmark.json` 具有相同的 dataset（`researchos_offline_baseline` v2）、mode 顺序（disabled/enabled/partial）、cache-disabled、DeepSeek + Tavily 手工执行配置和 observed/derived 合同。因此两轮是可比较的真实样本。以下数值均为 archive 中的 observed data 或由其得到的 derived metric，不能视为生产策略。

| 信号 | Enabled：Round 1 → Round 2 | Partial：Round 1 → Round 2 |
|---|---|---|
| grounded-citation pass-rate delta | +0.200 → +0.200 | +0.200 → 0.000 |
| overall quality pass rate | 0.167 → 0.167 | 0.333 → 0.000 |
| benefited case/tag | `ai-regulation-overview` / `policy, comparison` → `coastal-adaptation-partial-evidence` / `climate, evidence, partial` | `ai-regulation-overview` / `policy, comparison` → none |
| matched-success cases | 4 → 5 | 5 → 4 |
| observed wall-latency overhead | +266.679s → +33.951s | +125.747s → +114.929s |
| Writer/total | 0.602 → 0.592 | 0.642 → 0.602 |
| mode p50 / p95 | 291.394 / 378.542s → 282.525 / 327.403s | 305.003 / 354.473s → 325.196 / 400.880s |
| provider-error rate | 0.667 → 0.500 | 0.667 → 0.667 |

Provider-error 的 operation/case 分布同样不稳定：enabled 的 `analyze_document` LLM errors 从 7 次变为 6 次，受影响 case 从 `ai-regulation / clinical / search-provider-failure` 变为 `ai-regulation / clinical / urban-climate`；partial 的 `analyze_document` errors 从 7 次变为 5 次且受影响 case 改变，Round 2 还出现了 `plan` 与 run-level error。Writer/total 在两轮中仍处于 0.592–0.691 的 observed 区间，但没有用户定义的 latency SLO 或 section-correctness/partial-composition contract，不能解释为优化项目证据。

这里的 `grounded_citation` **仅**代表报告 URL 与有效 Evidence source URL 的 provenance/URL grounding；它不代表事实正确性、claim support 或 semantic entailment。因此 enabled 的两轮 +0.200 不能被提升为“Evidence 已稳定提升报告真实性”的结论。

**停止 Round 3 的理由：** 两轮已经出现 benefited case/tag 转移或消失、partial 的 grounded-citation delta 消失、quality outcome 下滑、matched population 与 provider-error distribution 变化。它们足以否定当前 initiative 的 evidence-sufficiency；继续采样可能描述更多波动，但不会改变当前“不足以立项”的决策，故不为满足 `>=3` 的 sufficient gate 而强制执行 Round 3。

结论仅限当前证据：

1. **Evidence selector：insufficient。** 没有同一 case/tag 的重复稳定受益，也没有稳定的 partial 增益；这不表示 Evidence 永久无价值。
2. **Writer optimization：not_assessable。** profile 可供未来观测，但缺用户 SLO，且没有质量/section 契约，不能立项。
3. **Provider resilience：insufficient / not_assessable。** 缺 availability/error-rate SLO 与可行动的 provider taxonomy，且错误 operation/case 分布不稳定；不得据此实现 fallback/circuit。
4. **P6 可以正式关闭。** P6.1 calibration、P6.2 harness 和两轮可比较真实采样已完成，并已依据预声明 stopping rule 作出非自动化 closure judgement；不需 Round 3。

**下一阶段唯一推荐方向：Evaluation measurement-contract governance。** 在任何 Evidence/Writer/provider 产品项目之前，先通过单独评审定义用户 SLO、provider error taxonomy、reference-answer/claim-support 的评价边界与采样停止规则；该方向不改变生产控制流或默认策略。

### 中期 — 只在触发条件满足时实施的能力

- **Writer bounded concurrency / partial composition**：仅当代表性 profile 持续显示 Writer 是 SLO 瓶颈时启动。先定义有序合成、citation determinism、section failure、partial report、共享 budget/cancel 和 trace identity；可在 Graph V1 内完成，不自动要求 Graph V2。
- **Provider fallback / circuit breaker**：仅当至少两个可用 provider、明确 availability SLO、错误分类/Retry-After 解释、选择顺序、健康观测和跨 provider usage/trace 规则均已定义时启动。历史 `web_utils` circuit 代码不等于当前 Provider policy。
- **Evidence-aware research 产品化**：仅在 P5 显示 grounded evidence 改善目标任务质量后，设计任务选择、配置、partial adoption 与 Evaluation 回归门槛；保持 failure isolation，不能把 Evidence 失败变为全局失败。
- **计划修订 / Reflection**：仅当 Evaluation 能提供可行动且稳定的失败信号时，设计单一、有限次数的 replan/research/rewrite 机制及其预算和终止条件。

## Graph V2 启动条件

**Graph V2 不是默认下一步，也不是性能优化、增加模型或新增数据字段的前提。** 只有下列业务控制流有明确需求、成功指标和迁移方案时才立项：

1. Reflection loop：质量信号需要决定 replan、re-search 或 rewrite，且必须定义最大轮数、收敛/终止与成本上限。
2. Supervisor routing：系统须在多个下一动作间动态选择，而非沿既有线性 router 前进。
3. 并行研究分支：需要并发子问题、结果 join/merge、共享 budget、取消传播与可追溯 attribution。
4. 人工审批：运行须在 checkpoint 处等待/恢复审批，并有权限、超时和 terminal semantics。

立项前必须产出：业务场景与 SLO、State/message/branch-merge contract、runtime budget/retry/cancel/partial 协议、checkpoint/resume/lease 影响、legacy migration/rollback，以及离线 evaluation 方案。任一缺失时维持 Graph V1。

## 最终 ResearchOS 演进方向

保留长期方向：**Memory、Reflection、Supervisor、Evaluation 和多 Agent 协作**。它们应建立在 P0–P4 的 runtime、trace、evidence、evaluation 与兼容边界之上，而不是绕过这些边界新增 Agent。

- Memory 先解决 provenance、retention、privacy 和 retrieval policy，随后才接入 planning/search。
- Reflection 先消费已验证的 Evaluation 信号，并是 bounded、可终止、可计费的循环。
- Supervisor 先使用结构化 decision contract；不以自然语言提示替代 router、预算或权限策略。
- Multi-Agent 先定义独立分支和 merge 的数据/运行时协议；并发不是默认行为。
- Evaluation 继续先做只读质量基线；进入生产治理、自动 gate 或人工审批流须单独评审。

## Backlog（非 P4 scope）

- Pydantic/msgpack Async SQLite serialization warnings 与跨版本 checkpoint 策略。
- router early termination 后的 callback/UI 展示一致性。
- `legacy_agent` 的最终退役或受限再接入方案。
- 后续唯一推荐：Evaluation measurement-contract governance（用户 SLO、provider error taxonomy、reference-answer/claim-support 边界与采样 stopping rule）；不得把 P5/P6 observed data 直接变为生产策略。

## 下一会话启动

```powershell
git status --short --branch
git log -3 --oneline --decorate
& .\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-local
```

开始任何实现前确认：不改 Graph 拓扑、router、Writer 输入或 legacy fields；显式双写不退化；Evidence failure isolation、runtime ownership 与 Evaluation read-only 边界保持成立。
