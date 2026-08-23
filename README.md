# ResearchOS

ResearchOS 是一个**轻量级多智能体研究系统**，面向学习、个人研究和能力展示。它将研究问题依次经过规划、来源搜索、信息综合与报告写作，产出带来源的 Markdown 研究报告；项目刻意保持小而可读，并非生产级行动执行平台。

## 项目能力

- **规划器（Planner）**：一次 LLM 调用生成 `ResearchPlan`，包含研究目标、搜索查询和报告大纲。
- **搜索器（Searcher）**：通过确定性 `SearchExecutor` 执行查询、去重 URL、在固定搜索与抽取预算内获取内容，并保留来源溯源信息。
- **P14 有界自适应搜索**：主搜索的查询覆盖不足时，最多再执行一次固定预算的补充搜索；不重新规划、不生成新查询、不循环。
- **综合器（Synthesizer）**：基于当前搜索结果生成研究发现；可选 Evidence sidecar 只补充溯源诊断。
- **写作器（Writer）**：按既有大纲串行生成报告章节、引用与最终报告。
- **P15 本地词法记忆演示**：现有 SQLite、来源支持的词法记忆默认关闭；Planner 只接收有界历史上下文，Searcher 只接收有界 `site:<host>` 提示，Writer 不读取记忆。

Runtime、Trace、Usage 和 Evaluation 是辅助能力：它们记录截止时间/预算、运行轨迹、调用统计和运行后的只读指标，但不参与业务路由或自动改写研究流程。

P11-P13 保留为**冻结的架构探索**：质量建议、授权/资格、通用行动账本与人工审查合约仍有测试和历史价值，但不接入 Graph，也不构成自动行动或生产工作流。

## 架构

```text
研究问题
  |
  v
规划器（Planner） -----------------> 研究计划（ResearchPlan）
  |
  v
搜索器（Searcher） -> 主搜索 / 抽取 -> 搜索结果（SearchResults）+ 文档（Documents）
  |                       |
  |                       +--> 覆盖是否不足？
  |                              最多一次可选补充搜索
  |                              （最多 1 次搜索 + 1 次抽取，无循环）
  |
  +--> 可选本地记忆 `site:<host>` 提示（仍经 SearchExecutor）
  |
  v
综合器（Synthesizer） -------------> 研究发现（Findings）
  |
  v
写作器（Writer） -----------------> 研究报告（Report）

横切能力：Runtime / Trace / Usage / Evaluation / Memory
```

Graph 保持固定的线性结构：`Planner -> Searcher -> Synthesizer -> Writer`。自适应搜索只位于 Searcher 内部；不会新增 Reflection 节点、Supervisor、Graph V2、重新规划，也不会改变 Writer 输入。

## 快速开始

### 1. 安装环境

要求：Python 3.11+，一个可用的 LLM Provider 和一个搜索 Provider。DeepSeek + Tavily 是经过手工真实展示验证的组合。

```bash
git clone https://github.com/njiang425-hhhh/Multi-Agent-Research-System.git
cd Multi-Agent-Research-System
python -m venv .venv
```

Linux 或 macOS：

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

Windows PowerShell：

```powershell
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. 配置 `.env`

```powershell
Copy-Item .env.example .env
```

最小 DeepSeek + Tavily 配置示例：

```dotenv
MODEL_PROVIDER=deepseek
DEEPSEEK_API_KEY=your_deepseek_api_key
MODEL_NAME=deepseek-chat

SEARCHER_MODE=deterministic_v2
SEARCH_PROVIDER=tavily
TAVILY_API_KEY=your_tavily_api_key

RESEARCH_MEMORY_ENABLED=false
SEARCHER_ADAPTIVE_ENABLED=true
SEARCHER_ADAPTIVE_MAX_ROUNDS=1
```

请妥善保管 `.env`。项目也支持 OpenAI、Gemini、Ollama 和 llama.cpp，具体 Provider 配置见 [`.env.example`](.env.example)。

### 3. 运行普通研究任务

```powershell
.venv\Scripts\python.exe main.py "比较欧盟《人工智能法案》与当前美国联邦人工智能治理方式对企业部署的影响。"
```

CLI 会将报告写入 `outputs/`。也可以通过 Python API 调用：

```python
import asyncio
from src.graph import run_research


async def main():
    state = await run_research("评估全球半导体供应链的关键韧性风险。")
    print(state.get("final_report"))


asyncio.run(main())
```

### 4. 运行本地记忆演示

这个确定性的 P15 A/B/C 演示只使用本地 fake 与 SQLite；不会调用 LLM、搜索 Provider、网页或 Graph。

```powershell
.venv\Scripts\python.exe scripts\run_memory_demo.py
```

演示包含：已完成的 Run A 写入来源支持的记忆、相关的 Run B 检索记忆并生成站点提示，以及无关的 Run C 不产生错误命中。可传入 `--store-path .cache\memory-demo\memory.db` 保留 SQLite 文件。

### 5. 运行真实展示集

P16 手工 runner 会顺序执行四个固定的 DeepSeek + Tavily 任务，并在 `artifacts\showcase\<时间戳>\` 中生成 `summary.json`、`summary.md`、每个 case 的 JSON 与报告 Markdown。

```powershell
.venv\Scripts\python.exe scripts\run_showcase.py
```

如需只对一个指定 case 显式启用既有的本地词法记忆：

```powershell
.venv\Scripts\python.exe scripts\run_showcase.py --case comparison-ai-governance --memory-case comparison-ai-governance
```

真实 Provider 调用只供手工执行，不会进入 CI。runner 会将失败保留为观测结果并继续其余 case；Evaluation 始终只读，绝不会成为运行时门禁。

## P16 展示集快照

当前工作区中 `2026-08-23` 的 archive 位于 [`artifacts/showcase/20260823T125821Z/`](artifacts/showcase/20260823T125821Z/)，其中记录了四个真实 DeepSeek + Tavily 运行：

| 任务 | 类别 | 结果 | 耗时 | 自适应搜索观测 |
|---|---|---|---:|---|
| `comparison-ai-governance` | 对比 | 完成 | 354.711 秒 | 触发一次；无进展 |
| `evidence-clinical-ai-screening` | 证据研究 | 失败 | 175.527 秒 | 不需要 |
| `risk-regulated-ai-agents` | 风险与实施 | 完成 | 290.570 秒 | 不需要 |
| `trend-ai-semiconductor-supply` | 趋势与行业 | 完成 | 275.457 秒 | 触发一次；无进展 |

观测摘要：**3 个完成 / 1 个失败**。两次自适应搜索都严格限制在一轮内，且没有带来新增进展。四个 case 的 source coverage 均通过；三个完成报告的 report completeness 均通过。由于可选 Evidence sidecar 保持关闭，完成报告的 grounded-citation 为 failed，Writer 截止时间失败的 case 则为 unavailable。

这些是 P5/P9/P10 只读评估产生的观测/archive 指标，不代表事实质量、Provider 质量、稳定时延或生产就绪承诺。

## 已知限制

- Writer 可能耗尽既有 Runtime operation deadline；P16 中包含一个已观测到的 Writer 截止时间失败。
- Evidence 默认关闭，因此没有现有 Evidence records 时，不应期待 grounded-citation 指标通过。
- Memory 是本地、词法、来源支持、有界且默认关闭的能力；它不是语义/向量记忆、个性化能力，也不作为 Writer 输入。
- 自适应搜索最多只有一轮补充搜索/抽取，且允许正确结束为无进展。
- 默认展示使用单一 Provider 组合；没有 Provider fallback 或 circuit breaker。
- Graph V1 是固定线性结构；没有 Supervisor、branch/join、重新规划、Reflection 节点或 Graph V2。

## 验证

仅使用 fake 输入的测试不会调用真实 LLM、Provider 或网页：

```powershell
.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-local
git diff --check
```

当前发布基线：**364 passed，2 个既有 Pydantic deprecation warnings**。P16 真实展示集仅手工执行；其 archive 会保留实际的完成、无进展和失败结果，而不会掩盖它们。

## 项目结构

```text
src/
  agents.py                    规划器 / 搜索器 / 综合器 / 写作器
  graph.py                     固定 Graph V1 runner 与生命周期边界
  runtime_control.py           截止时间、预算、重试、取消、租约合约
  search/                      SearchExecutor、覆盖辅助函数与 Provider
  memory/                      本地 SQLite 词法记忆与确定性演示
  evaluation/                  只读 P5/P9/P10/P16 评估与 archive
  action_execution.py          冻结的 P13 架构探索
  human_review.py              冻结的 P13 架构探索
scripts/
  run_memory_demo.py           P15 仅本地确定性演示
  run_showcase.py              P16 手工 DeepSeek + Tavily archive runner
artifacts/showcase/            手工 P16 archive
tests/                         仅使用 fake 输入的回归测试集
```

有关架构合约、里程碑决策、历史验证和未来边界，见 [`PROJECT_HANDOFF.md`](PROJECT_HANDOFF.md)。

## 致谢

ResearchOS 使用了 LangGraph、LangChain、Chainlit、Tavily、DeepSeek、httpx、Beautiful Soup 和 DDGS。
