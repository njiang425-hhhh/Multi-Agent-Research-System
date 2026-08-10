# Multi-Agent Research System

一个基于 LangGraph 和 LangChain 构建的自主研究系统。系统通过规划、确定性搜索、信息综合和报告写作四个阶段，将一个研究问题转化为带来源与可信度评分的结构化报告。

当前版本在稳定的 ResearchOS 基础运行链之上，增加了独立、可测试的 Evidence Layer 基线：标准化文档适配、文档级证据分析契约、注入式 ResultAnalyzer 和确定性 Finding Aggregator。

## 当前能力

- LangGraph 工作流：`Planner → Searcher → Synthesizer → Writer`
- LLM Provider：DeepSeek、OpenAI、Gemini、Ollama、llama.cpp
- Searcher V2：确定性查询执行、URL 去重、搜索/提取预算、超时和重试控制
- Search Provider Layer：统一 Provider 契约、结构化错误和 Factory
- Tavily 搜索：推荐的默认搜索 Provider
- Legacy Searcher：保留原 `create_agent` 搜索模式，可配置切换
- 来源可信度评分、引用格式化和网页正文提取
- Token、耗时和 LLM 调用记录
- 文件缓存、内存/SQLite Checkpoint
- CLI 与 Chainlit Web 界面
- Evidence Layer：`SearchResult → Document → DocumentAnalysis / Evidence → Finding`
- ResultAnalyzer：有界文本输入、content/snippet 降级、quote 验证、稳定 Evidence ID 与 partial failure 策略
- Rule-based Finding Aggregator：按规范化 claim 聚类，过滤无效/未知来源 Evidence，并确定性计算 Finding confidence
- 113 项无外部 API 的 pytest 回归测试

Evidence Layer 当前保持独立，尚未接入 LangGraph、State 双写、Synthesizer 或 Writer。Memory、Reflection、Supervisor 和 Evaluation 的 State 字段也仍只处于预留状态。

## 工作流程

![研究工作流程](assets/flow.png)

1. `ResearchPlanner` 生成研究目标、搜索查询和报告大纲。
2. `ResearchSearcher` 使用确定性 Executor 执行查询、调用搜索 Provider、去重 URL 并提取正文。
3. `ResearchSynthesizer` 基于搜索结果生成关键发现。
4. `ReportWriter` 按大纲生成章节、引用来源并汇编最终报告。

Evidence Layer 目前可独立调用与测试，尚不改变上述生产 Graph 拓扑：

```text
SearchResult
  -> Document Adapter
  -> ResultAnalyzer
  -> DocumentAnalysis + Evidence
  -> Rule-based Finding Aggregator
  -> Finding
```

## 环境要求

- Python 3.11+
- 一个可用的 LLM Provider
- 推荐配置 Tavily API Key 用于网络搜索

## 安装

```bash
git clone https://github.com/njiang425-hhhh/Multi-Agent-Research-System.git
cd Multi-Agent-Research-System
python -m venv .venv
```

Linux/macOS：

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

Windows PowerShell：

```powershell
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 配置

复制环境模板：

```bash
cp .env.example .env
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

推荐的 DeepSeek + Tavily + Searcher V2 配置：

```dotenv
MODEL_PROVIDER=deepseek
DEEPSEEK_API_KEY=your_deepseek_api_key
DEEPSEEK_BASE_URL=https://api.deepseek.com
MODEL_NAME=deepseek-chat
SUMMARIZATION_MODEL=deepseek-chat

SEARCHER_MODE=deterministic_v2
SEARCH_PROVIDER=tavily
TAVILY_API_KEY=your_tavily_api_key

MAX_SEARCH_QUERIES=3
MAX_SEARCH_RESULTS_PER_QUERY=3
MIN_CREDIBILITY_SCORE=40
MAX_REPORT_SECTIONS=8
CITATION_STYLE=apa
```

`.env` 已被 Git 忽略。不要提交任何真实 API Key。

其他模型 Provider：

```dotenv
# OpenAI
MODEL_PROVIDER=openai
OPENAI_API_KEY=your_openai_api_key
MODEL_NAME=your_model_name

# Gemini
MODEL_PROVIDER=gemini
GEMINI_API_KEY=your_gemini_api_key
MODEL_NAME=your_model_name

# Ollama
MODEL_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
MODEL_NAME=qwen2.5:7b

# llama.cpp
MODEL_PROVIDER=llamacpp
LLAMACPP_BASE_URL=http://localhost:8080
MODEL_NAME=your_model_name
```

Searcher 模式：

```dotenv
# 推荐
SEARCHER_MODE=deterministic_v2

# 兼容原自主Tool Loop
SEARCHER_MODE=legacy_agent
```

## 使用方法

### CLI

```bash
# 交互模式
python main.py

# 指定主题
python main.py "Research the development trend of LangGraph framework"
```

报告默认保存到 `outputs/`。

### Chainlit Web界面

```bash
chainlit run app.py --host 127.0.0.1 --port 8000
```

### Python API

```python
import asyncio
from src.graph import run_research


async def main():
    state = await run_research(
        topic="Research the development trend of LangGraph framework",
        verbose=True,
        use_cache=False,
    )
    print(state.get("final_report"))


asyncio.run(main())
```

持久化运行可使用 `run_research_with_persistence()`、`resume_research()`、`get_workflow_state()` 和 `list_research_threads()`。

## 项目结构

```text
Multi-Agent-Research-System/
├── src/
│   ├── agents.py                 # Planner/Searcher/Synthesizer/Writer
│   ├── graph.py                  # LangGraph Workflow
│   ├── state.py                  # ResearchState V1契约
│   ├── config.py                 # 项目配置
│   ├── llm/
│   │   └── factory.py            # LLM Provider Factory
│   ├── search/
│   │   ├── config.py             # Search Runtime Config
│   │   ├── executor.py           # Deterministic SearchExecutor
│   │   └── providers/
│   │       ├── base.py           # SearchProvider接口
│   │       ├── errors.py         # 结构化Provider异常
│   │       ├── factory.py        # SearchProviderFactory
│   │       ├── models.py         # ProviderSearchResult
│   │       └── tavily.py         # TavilyProvider
│   ├── evidence/
│   │   ├── adapters.py            # SearchResult -> Document
│   │   ├── models.py              # DocumentAnalysis、Evidence、AnalysisResult
│   │   ├── service.py             # 注入式 ResultAnalyzer
│   │   ├── finding_models.py      # Finding 聚合契约
│   │   └── aggregation.py         # 确定性 Finding Aggregator
│   ├── prompts/                  # Agent提示词
│   └── utils/                    # Tools、缓存、引用和网页处理
├── app.py                        # Chainlit入口
├── main.py                       # CLI入口
├── .env.example                  # 安全配置模板
├── requirements.txt
└── pyproject.toml
```

## 已验证运行链

当前 DeepSeek + Tavily + Deterministic Searcher V2 配置已完成完整回归测试：

```text
Planner → Searcher → Synthesizer → Writer → final_report
```

测试中成功生成搜索结果、关键发现、8个报告章节和最终 Markdown 报告。

## 测试与开发检查

```bash
python -m pip check
pytest -q tests
```

当前测试覆盖 State 兼容、LLM Factory、Search Runtime、Tavily Provider、Tool Adapter、Evidence 模型与 Adapter、ResultAnalyzer，以及 Finding 聚合基线；全部使用 fake 输入，不调用真实 LLM、Provider 或网页。

新增搜索 Provider 时，实现 `src/search/providers/base.py` 中的 `SearchProvider` 接口，并在 `SearchProviderFactory` 注册。Provider异常必须使用结构化异常，不能将网络故障转换为正常空列表。

## Acknowledgements

本项目使用 LangGraph、LangChain、Chainlit、Tavily、DeepSeek、httpx、Beautiful Soup 和 DDGS 等开源项目与服务。
