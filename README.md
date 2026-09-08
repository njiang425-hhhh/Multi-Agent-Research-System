# 多智能体研究系统

一个轻量、可追溯的 Research Agent 作品集项目。它通过固定、可检查的工作流，将一个问题
转化为带引用的 Markdown 研究报告；它不是开放式 Agent 平台。

```text
query → research_plan → documents → findings → report
```

## 项目是什么

本项目展示可靠研究工作流所需的工程取舍：有界检索、canonical 来源对象、来源关联的
论断、稳定引用、可安全重放的缓存与离线评估。核心路径足够小，可以在一次阅读中解释
清楚；通过 fake-only 回归套件保障迭代安全。

## 架构

```text
CLI / Chainlit
      │
      ▼
ResearchRunner ── cache · lifecycle · checkpoints · resume · memory persistence
      │
      ▼
LangGraph 拓扑
Planner → Searcher → Synthesizer → Writer
  │         │            │             │
ResearchPlan Documents  Findings       Report
```

`src/graph.py` 只负责线性的四节点拓扑，`src/runner.py` 负责一次运行的编排。新运行只
使用 canonical 字段；`src/state_compat.py` 在显式边界处 hydrate 旧输入与旧 checkpoint，
冲突时始终以 canonical 值为准。

## 核心设计

- **确定性有界搜索**：`SEARCHER_MODE=deterministic_v2` 是默认且受支持的路径。
  `SearchExecutor` 负责预算、提取上限、去重、可信度排序和一次有界自适应补搜。
- **Canonical provenance**：有序且唯一的 `Document` 是唯一参考文献表；
  `Finding.source_document_ids` 将论断关联回来源，文档顺序决定引用编号。
- **Citation integrity 不等于事实保证**：评估检查引用编号、文档顺序、URL 与参考文献表
  一致性。可选的 Evidence grounding 独立观察已记录的支持、反驳或缺失支持；关闭
  Evidence 时其状态为 unavailable。
- **运行时控制**：trace、usage、diagnostics、cache replay、内存 checkpoint 与可选
  SQLite resume 都属于运行编排，而不是 Graph 路由。
- **离线评估**：fake-only 测试覆盖 canonical 工作流、缓存 round-trip、确定性搜索、
  来源关联、引用完整性、Evidence 语义、报告生成和入口一致性；CI 不调用真实 provider。

## 快速开始

要求：Python 3.11+、一个 LLM provider 和一个搜索 provider。`pyproject.toml` 是唯一正式
依赖真源。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

`.env.example` 是明确的 **DeepSeek + Tavily** 配置。填入 `DEEPSEEK_API_KEY` 和
`TAVILY_API_KEY` 后运行：

```powershell
.venv\Scripts\python.exe main.py "Compare the EU AI Act with current US federal AI governance."
```

CLI 会将 canonical 报告写入 `outputs/`。Chainlit 演示使用同一个 `ResearchRunner` 入口，
但为浏览器会话显式关闭 cache 和 Memory persistence：

```powershell
.venv\Scripts\chainlit run app.py
```

未使用 `.env` 覆盖时，代码默认使用 Gemini 和 DuckDuckGo；请使用相匹配的 provider/key
组合，不要将示例配置与默认回退配置混用。

## Timeout 配置

| 配置项 | 默认值 | 作用范围 |
|---|---:|---|
| `LLM_OPERATION_TIMEOUT_SECONDS` | 180 秒 | 每次 Planner、Synthesizer 或 Writer 的 LLM operation。本地 deadline 会与可选 `RunPolicy` 的全局剩余 deadline 取较小值。 |
| `SEARCHER_TOTAL_TIMEOUT_SECONDS` | 90 秒 | 确定性 SearchExecutor 的总 timeout；不受 LLM timeout 配置影响。 |

可在 `.env` 中覆盖，例如 `LLM_OPERATION_TIMEOUT_SECONDS=240`。未提供 `RunPolicy.total_timeout_seconds`
时，run 没有全局 deadline；提供后，它不会替代 LLM timeout，而是进一步收紧每次 operation 的有效 deadline。

## 可选能力

| 能力 | 默认值 | 作用范围 |
|---|---:|---|
| 本地 Memory | 关闭 | 有界词法检索和可选的完成后持久化；不会作为 Writer 输入。 |
| Evidence sidecar | 关闭 | 来源分析与 grounding 观测；不会改变 Graph 路由。 |
| SQLite checkpoint/resume | 普通运行关闭 | 带 lease 的显式持久化运行/恢复路径。 |

## 评估与 Showcase

稳定 evaluator 提供 planning/retrieval coverage、source diversity、citation integrity、可选
Evidence grounding、usage、latency、trace 与报告完整度。这些都是只读观测：任何得分都
不会改变路由，也不构成事实正确性保证。

运行完整 fake-only 回归：

```powershell
.venv\Scripts\python.exe -m pytest -q tests/core
git diff --check
```

固定 showcase runner 是手动入口，因为它会调用已配置的 provider；结果会以时间戳写入
被 Git 忽略的 `artifacts/showcase/`：

```powershell
.venv\Scripts\python.exe scripts\run_showcase.py
```

## 目录说明

```text
src/
  runner.py                 CLI / Web 共用的运行编排
  graph.py                  Planner → Searcher → Synthesizer → Writer 拓扑
  agents/                   canonical 核心 Agent；compat/ 保存 legacy Searcher
  search/                   确定性 executor 和 provider adapter
  evaluation/               稳定、只读的 evaluator 与固定 showcase
  state.py                  flat canonical State 与 legacy 输入字段
  state_compat.py           显式 canonical-first hydration/projection 边界
  evidence/, memory/        可选 sidecar

tests/core/                 唯一的 fake-only golden-path 回归层
scripts/run_showcase.py     手动生成被忽略的 showcase 输出
```

详见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## GitHub 作品集信息

- **建议 description**：`A deterministic, source-aware Research Agent with canonical provenance, replay-safe runtime controls, and offline evaluation.`
- **建议 topics**：`langgraph`、`agentic-ai`、`research-agent`、`llm`、`retrieval`、
  `evaluation`、`python`、`provenance`。
