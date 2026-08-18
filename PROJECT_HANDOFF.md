# ResearchOS / Multi-Agent Research System — Handoff

> 本文是新会话的唯一交接基线；与历史 handoff、提交记录或旧测试结论冲突时，以当前源码和本文为准。
>
> 最后更新：2026-08-18 · P0–P4.5 COMPLETE · P4 COMPLETE / formally closed · 工作区仍含未提交实现。

## 当前基线

- **Graph V1 保持不变**：`Planner -> Searcher -> Synthesizer -> Writer`；现有 router、Writer 输入和 legacy 主链均不变。
- **P0–P4.5 COMPLETE**：完成显式 State 双写、Evidence sidecar、lifecycle/checkpoint/lease、统一 execution policy/provider、LLM attempt accounting、Trace/Evaluation governance 与 P4 closure hardening。
- **验证基线**：fake-only 全量 **257 passed，2 个既有 Pydantic deprecation warnings**。CI 使用 Python 3.11 和固定 pytest `--basetemp`。
- 默认运行组合：DeepSeek + Tavily + `deterministic_v2`。`legacy_agent` 仅为显式兼容模式；`EVIDENCE_ANALYZER_ENABLED=false` 仍是默认值。

| 阶段 | 状态 | 已交付能力 |
|---|---|---|
| P0–P2 | COMPLETE | State/V1 显式双写、LLM/Search 基础、Evidence 保护性 sidecar。 |
| P3 | COMPLETE | run identity、terminal lifecycle、checkpoint/resume/cache replay、Trace、只读 Offline Evaluation。 |
| P4.1–P4.5 | COMPLETE | persisted runtime policy/lease、统一 deadline/budget/retry、Provider contract、LLM execution、Writer profile、Trace retention/redaction、content-bound Evaluation snapshot。 |

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
| Evaluation | 离线、确定性、只读；snapshot 绑定 evaluator、配置与实际 dataset/case 内容，不进入生产路由。 |

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
| Search | deterministic 顺序 SearchExecutor、provider factory、partial、global budget/deadline、typed errors。 | 缺少代表性质量/SLO 数据；多 provider 可用性与失败策略尚未被业务需求证明。 | 分支式研究、来源策略与按需 provider resilience。 |
| Synthesis / Evidence | compatibility findings、strict grounding、optional Evidence sidecar、failure isolation。 | 默认 sidecar 关闭；需要以离线质量基线判断何种任务启用、partial evidence 如何影响报告与质量。 | evidence-aware research 成为可衡量、可配置的研究策略，而非无条件多加一个 Agent。 |
| Report | ordered serial sections、citation projection、attempt trace、read-only latency profile。 | fake profile 证明串行 LLM 是主要等待，但缺少代表性真实 workload/SLO；尚未定义 section failure、partial report、citation ordering 语义。 | 在明确契约后进行 bounded concurrency 或 partial composition。 |
| Evaluation | fixed dataset、deterministic read-only metrics、version/config/content snapshot。 | 没有参考答案、研究质量 rubric、回归阈值或真实 workload 分层；不能据此驱动生产路由。 | 可比较的质量回归体系；是否进入人工或自动治理须单独设计。 |
| Memory | State 已有 `MemoryItem`、retrieved IDs 等占位字段。 | 没有存储、检索、provenance、过期/删除、隐私或注入点，因此尚不是能力。 | 有来源与生命周期的 research memory。 |
| Reflection / Supervisor | State 已有 critic feedback、next action、supervisor decision 占位。 | 没有质量信号到动作的协议、循环上限、成本预算、checkpoint/approval 语义。 | 基于证据的修订循环与受控监督路由。 |
| Multi-Agent | 当前四个角色是固定串行职责，不是可调度的多 Agent 协作系统。 | 缺少 branch contract、join/merge、共享预算、公平取消和结果归因。 | 并行研究分支与可审计汇聚。 |

## 近期路线图：先证明需求，再改变控制流

### 近期 — P5 Research Quality & Evidence Baseline（推荐下一阶段）

目标是在 **不改变 Graph V1** 的条件下，建立决定后续投资的业务证据：

1. 扩充离线 evaluation cases，定义来源覆盖、grounded citation、report completeness、failure/partial 的可比较 rubric 与回归阈值。
2. 对比 Evidence disabled / enabled / partial 的结果，明确哪些任务类型值得启用 sidecar，以及 Evidence 对报告采用的可观测收益。
3. 使用代表性 workload 的 trace/profile 记录 Writer latency、provider failure 分布和用户可接受的 SLO；fake-only 继续作为稳定 CI，真实集成验证独立、可选且不混入 CI。
4. 根据上述基线给出明确决策：Writer performance、provider resilience、计划修订或 Graph V2 中哪一项具有可验证的业务优先级。

选择 P5 的理由：P4 已把运行时和可观测性基础打牢，但当前最缺的是质量、收益和 SLO 证据。先增加并发、fallback 或 Agent loop 都会把未定义的业务语义固化到架构中。

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
- 更丰富的 evaluation dataset、reference/rubric 与真实集成验证流程。

## 下一会话启动

```powershell
git status --short --branch
git log -3 --oneline --decorate
& .\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-local
```

开始任何实现前确认：不改 Graph 拓扑、router、Writer 输入或 legacy fields；显式双写不退化；Evidence failure isolation、runtime ownership 与 Evaluation read-only 边界保持成立。
