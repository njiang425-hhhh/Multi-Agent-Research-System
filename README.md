# ResearchOS

ResearchOS is a compact, source-aware research agent. It turns one research
question into a structured Markdown report through a fixed four-stage flow:

```text
Question → Planner → Searcher → Synthesizer → Writer → Markdown report
```

The project is designed as an Agent-development portfolio: the important parts
are a clear planning contract, deterministic and bounded web research,
traceable sources, and an inspectable end-to-end workflow. It is not presented
as a production Agent Platform.

## What it demonstrates

- **Planner** — one structured LLM call produces objectives, bounded search
  queries, and a report outline.
- **Searcher** — `SearchExecutor` runs deterministic search, URL de-duplication,
  bounded extraction, credibility filtering, and source-document projection.
- **Bounded adaptive follow-up** — when planned-query coverage is incomplete,
  Searcher may run at most one supplementary search and extraction. It never
  replans, loops, or changes the Graph.
- **Synthesizer** — turns the gathered sources into research findings.
- **Writer** — follows the planned outline and produces a cited Markdown report.
- **Observability and evaluation** — trace, usage accounting, and read-only
  evaluation make a run inspectable without changing its route.

## Architecture

```text
User question
    │
    ▼
Planner ── ResearchPlan(objectives, queries, outline)
    │
    ▼
Searcher ── deterministic search + extract + provenance
    │           └─ optional one-round adaptive follow-up
    ▼
Synthesizer ── findings
    │
    ▼
Writer ── cited Markdown report
```

The LangGraph topology remains linear. Search adaptation stays inside the
Searcher implementation rather than adding a Reflection, Supervisor, or Graph
branch. See [ARCHITECTURE.md](ARCHITECTURE.md) for inputs, outputs, and the
default execution path.

## Quick start

Requirements: Python 3.11+, one LLM provider, and one search provider. The
included example configuration uses DeepSeek and Tavily.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Set `DEEPSEEK_API_KEY` and `TAVILY_API_KEY` in `.env`, then run:

```powershell
.venv\Scripts\python.exe main.py "Compare the EU AI Act with current US federal AI governance."
```

The report is saved to `outputs/`. For the Chainlit demo:

```powershell
.venv\Scripts\chainlit run app.py
```

## Default portfolio path

The default configuration intentionally favors one readable execution path:

```dotenv
SEARCHER_MODE=deterministic_v2
SEARCHER_ADAPTIVE_ENABLED=true
SEARCHER_ADAPTIVE_MAX_ROUNDS=1
WRITER_SECTION_EXECUTION_MODE=serial
RESEARCH_MEMORY_ENABLED=false
EVIDENCE_ANALYZER_ENABLED=false
```

`deterministic_v2` is the supported Searcher path. The Writer runs sections
serially by default. The adaptive follow-up is bounded to one attempt and uses
the existing query plan; it is not a general reflection loop.

## Source provenance and limits

Each search result retains its originating query, title, URL, snippet, and
extracted content when available. Search results are de-duplicated, scored,
projected into documents, and later surfaced through report citations.

The optional Evidence sidecar provides deeper document analysis diagnostics,
but it is disabled by default and does not control Graph routing. It should not
be interpreted as a factuality guarantee. Likewise, trace and evaluation are
observational rather than runtime gates.

## Repository map

```text
src/
  graph.py                  Graph V1 and run entry points
  agents/                   Planner / Searcher / Synthesizer / Writer entry modules
  search/                   deterministic executor, coverage, and providers
  prompts/                  agent prompts
  evidence/                 optional provenance/evidence sidecar
  evaluation/               read-only evaluation and showcase support
  memory/                   optional local lexical-memory implementation
  runtime_*.py              lifecycle, deadline, checkpoint, and lease support

scripts/
  run_showcase.py           manual end-to-end showcase archive runner

experiments/
  memory/                   local memory demo entry point
  writer_performance/       serial vs bounded Writer benchmark
  benchmarks/               manual workload/repeatability benchmarks
  governance/               notes for action and human-review exploration
  archives/                 historical benchmark, showcase, and handoff records
```

The `src/agents/` package is the stable public entry point. Its Phase 1
compatibility implementation preserves the existing agent behavior while the
package layout makes the four roles easy to discover.

## Validation

The test suite uses fakes for LLMs, providers, and web extraction. Run:

```powershell
.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-local
git diff --check
```

## Optional experiments

Memory, Writer concurrency, governance contracts, and historical workload
benchmarks remain available for inspection, but they are intentionally outside
the default demo path. Their scope, compatibility boundaries, and Phase 2
deferrals are documented in [experiments/README.md](experiments/README.md) and
[PROJECT_HANDOFF.md](PROJECT_HANDOFF.md).
