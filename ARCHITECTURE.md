# ResearchOS architecture

## Product boundary

ResearchOS is a four-stage research Agent pipeline. It accepts a question and
returns a source-aware Markdown report. The portfolio path is deliberately
linear and bounded:

```text
Question → Planner → Searcher → Synthesizer → Writer
```

The project does not use a Supervisor, Reflection node, automatic replanning,
or multi-agent message bus.

## Agent contracts

| Stage | Input | Output | Default behavior |
|---|---|---|---|
| Planner | `query` | `ResearchPlan` | one structured LLM call; objectives, queries, outline |
| Searcher | `ResearchPlan` | `Documents` | `deterministic_v2`; bounded search, extraction, credibility-stable source ordering |
| Synthesizer | `Documents` | source-linked `Findings` | structured claims map source numbers to `document_id`; Evidence stays optional |
| Writer | plan, `Documents`, `Findings` | canonical `Report` | serial section generation; document order fixes citation numbering |

## Search behavior

`SearchExecutor` owns the default search path:

1. run the planned queries under a fixed search budget;
2. normalize and de-duplicate URLs;
3. extract a bounded number of pages, prioritizing query coverage;
4. filter results by credibility;
5. project retained sources into unique Documents, retaining the first
   credibility-sorted title/URI/snippet/credibility and filling only missing
   body content from later duplicates.

If an existing plan query lacks a result or extracted content, Searcher can run
one supplementary attempt. The attempt reuses a missing planned query, permits
at most one search plus one extraction, and ends regardless of progress.

## Cross-cutting support

- Runtime code enforces existing deadlines, retries, budget accounting, and
  optional checkpoint/resume semantics. It does not choose business routes.
- Trace and usage record node/LLM/tool observations.
- Evaluation reads completed states and reports metrics; it never gates a run.
- Evidence and Memory are optional experiments, not requirements for the core
  demo.

## Code navigation

Start with these files:

1. `src/graph.py` — Graph V1 topology and runner boundaries.
2. `src/agents/planner.py` — Planner public entry point.
3. `src/agents/searcher.py` — Searcher public entry point.
4. `src/agents/synthesizer.py` — Synthesizer public entry point.
5. `src/agents/writer.py` — Writer public entry point.
6. `src/search/executor.py` — deterministic web-research implementation.

Each Agent class now lives in its role module. `src/agents/_llm_support.py`
owns only shared retry and accounting helpers; `src/state_compat.py` is the
explicit canonical-first boundary for legacy State, cache, and checkpoint
payloads. It is deliberately not a Pydantic validator or automatic sync layer.

Canonical data flow is deliberately small:

```text
query → research_plan → documents → findings → report
```

`documents` is the single Writer bibliography. Its ordered, unique, valid web
Documents define citation numbers, and `Report.citations[n-1]` is the exact
target for every retained `[n]`. Findings must have at least one valid
`source_document_id` before Writer can use them. Evidence only enriches
`document_analyses`, `evidence`, and diagnostics; it never replaces Findings
or changes Writer inputs. At the Graph output boundary, canonical values are
explicitly projected to legacy fields for old checkpoints and consumers.
