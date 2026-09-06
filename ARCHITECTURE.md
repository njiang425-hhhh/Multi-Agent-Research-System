# 项目架构

## 产品边界

本仓库的主产品表面是一个线性、有界的 Research Agent。默认工作流不包含 Supervisor、
Critic、Reflection loop、Replanner 或 agent message bus。

```text
query → ResearchPlan → Documents → Findings → Report
          Planner      Searcher     Synthesizer  Writer
```

## 运行链路

```text
CLI ───────┐
           ├─ ResearchRunner ── cache lookup/replay ─┐
Chainlit ──┘       │                                 │
                    ├─ initial canonical state        │ cache hit
                    ├─ lifecycle / terminal handling  ├─ completed canonical state
                    ├─ checkpoint / lease / resume    │
                    └─ optional Memory persistence    │
                              │                        │
                              ▼                        ▼
                   Graph: Planner → Searcher → Synthesizer → Writer
```

`src/runner.py` 拥有一次运行的生命周期；`src/graph.py` 只拥有节点注册、线性拓扑和最小
输入 hydration adapter。Graph 可以接收已配置的 checkpointer，但 cache、SQLite persistence、
lease、resume、terminal classification 与 Memory persistence 均属于 Runner。

普通 Runner 在启用 checkpoint 时使用内存 `MemorySaver`。SQLite persistence/resume 是显式的
`run_research_with_persistence` / `resume_research` 路径。Chainlit 使用同一 Runner，但为 UI
会话显式关闭 cache、checkpoint 和 Memory persistence。

## Canonical State

`ResearchState` 保持 flat。核心业务契约如下：

| 字段 | 生产者 | 消费者 / 不变量 |
|---|---|---|
| `query` | Runner | Planner 输入 |
| `research_plan` | Planner | Searcher 输入 |
| `documents` | Searcher | 有序且唯一的参考文献表；Synthesizer/Writer 输入 |
| `findings` | Synthesizer | 每个可用论断都具有 `source_document_ids` |
| `report` | Writer | `Report.citations[n-1]` 对应 `documents[n-1].uri` |

运行时字段（`run_id`、`status`、`execution_context`、`terminal_reason`）、可观测字段
（`usage`、`agent_trace`、`search_diagnostics`）和可选 Evidence/Memory sidecar 字段共享
同一 flat State，但不会形成第二份业务真相。

旧字段（`research_topic`、`plan`、`search_results`、`key_findings`、`report_sections`、
`final_report`、旧 iteration/usage totals）仅用于旧输入、checkpoint 与 cache payload。
`src/state_compat.py` 在显式边界处 hydrate；canonical 值始终优先。新 Agent 只写 canonical
业务字段；`legacy_projection_patch` 仍作为旧消费者可选调用的导出辅助函数。

## 搜索与来源追溯

`SEARCHER_MODE=deterministic_v2` 是受支持模式。`SearchExecutor` 拥有计划查询预算、提取
上限、重试、URL 去重、可信度稳定排序和 provider attempt record。Searcher 将唯一
authoritative 的 `SearchExecutionStats` 投影到 usage、trace event 与 diagnostics，保证
search/extract 统计一致。

当计划查询 coverage 不完整时，可执行一次可选的 supplementary search/extract。它复用现有
计划、严格有界，并且不新增 Graph edge。历史 autonomous tool-agent loop 被隔离到
`src/agents/compat/autonomous_searcher.py`，只在显式请求 `legacy_agent` 时运行。

Documents 构成 citation map：它们唯一且顺序稳定，并且是 Writer 唯一的 bibliography。
Findings 引用 `document_id`，citation number 也依据相同的有序 Documents 解析。

## Cache、checkpoint 与可观测性

Cache 存储 schema v2、JSON-mode 的 Pydantic State dump。cache replay 会恢复完整 canonical
State，并创建新的 replay lifecycle；不会只返回 `final_report`。旧版 `default=str` 的
string-fallback 条目会被视为 cache miss。最小读取兼容路径可以 hydrate 历史 full-v2 legacy
dump，而不会再次令 legacy 字段成为权威。

Usage、trace 和 search diagnostics 都是观测数据。search/extract 次数来自
`SearchExecutionStats`；Agent Trace 发出对应的 tool record；Usage 将相同总数投影为
`UsageMetrics.tool_calls`。

## Evaluation 与可选能力

`src/evaluation/` 提供稳定、只读的作品集评估能力：planning quality、research coverage、
source diversity、citation integrity、可选 Evidence grounding、usage、latency、trace、报告
完整度与 fixed showcase 渲染。

Citation integrity 是 provenance 校验，而不是事实正确性保证。Evidence sidecar 未运行、
关闭或未完成时，Evidence grounding 独立显示 unavailable；这不会使 citation 失败。

| 范围 | 位置 | 状态 |
|---|---|---|
| Evidence sidecar | `src/evidence/` | 可选，默认关闭；只读观测 |
| 本地 lexical Memory | `src/memory/` | 可选，默认关闭；有界 |
| SQLite checkpoint/resume | `src/runner.py` | 可选持久化运行路径 |
| Legacy State/Search API | `src/state_compat.py`、`src/agents/compat/` | 仅兼容 |
| 手动 fixed showcase 输出 | `artifacts/showcase/` | 不提交到 Git |

默认 CI 只运行 `tests/core/` 中的 fake-only golden-path 回归。
