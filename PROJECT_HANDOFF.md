# ResearchOS Portfolio Refactor Handoff

## Product position

ResearchOS is a source-aware Research Agent portfolio project. Its default,
inspectable path is fixed and bounded:

```text
Query → Planner → ResearchPlan → Searcher → Documents → Synthesizer → Findings → Writer → Report
```

The repository is not positioned as a production Agent Platform. Runtime,
memory, governance, historical benchmark, and Evidence work remain supporting
or optional material rather than the primary demo story.

## Phase 1 — Portfolio Slimming

Completed previously:

- README and architecture documentation focus on the four-stage Agent flow,
  deterministic bounded search, one adaptive follow-up, provenance, trace, and
  evaluation.
- Experimental scripts and historical archives moved under `experiments/`.
- Default behavior remains deterministic Searcher, at most one adaptive round,
  serial Writer, and optional Memory/Evidence disabled.

## Phase 2A — Core Contract Cleanup

Completed in this phase:

- Removed `src/agents/_implementation.py`; all four class bodies now live in
  `planner.py`, `searcher.py`, `synthesizer.py`, and `writer.py`.
- Added the minimal `src/agents/_llm_support.py` for retry conversion,
  LLM-attempt accounting, failure patches, and `UsageMetrics` projection.
- Moved optional Agent memory retrieval helpers into `src/memory/retrieval.py`.
- Kept `src.agents.create_agent` as the public injection seam. Searcher and
  Synthesizer resolve it at call time, so current monkeypatch tests remain
  valid without global implementation swapping.
- Added `src/state_compat.py`: explicit canonical-first hydration and fallback
  for `query`, `research_plan`, `iteration`, `usage`, and report presentation.
  It never uses Pydantic validators or mutates caller/checkpoint payloads.
- Graph nodes hydrate canonical fields through the explicit boundary before
  Agent execution.
- Graph plan routing, runtime terminal/cache checks, CLI, Web display, and the
  read-only evaluator now prefer canonical plan, report, iteration, and usage
  data with legacy fallback.

## Current reading path

1. `README.md`
2. `ARCHITECTURE.md`
3. `src/graph.py`
4. `src/state_compat.py`
5. `src/agents/planner.py`
6. `src/agents/searcher.py`
7. `src/agents/synthesizer.py`
8. `src/agents/writer.py`
9. `src/search/executor.py`

## Phase 2B — Canonical Research Data Flow Migration

Completed in this phase:

- The Agent main path is now `query → research_plan → documents → findings → report`.
- Searcher returns canonical, credibility-stable, URL-unique Documents. A later
  duplicate may fill missing content but never replaces the first item's title,
  URI, snippet, or credibility record.
- Synthesizer receives Documents only and emits Findings with
  `source_document_ids`; invalid source numbers are discarded and no confidence
  is invented.
- Writer receives only plan, Documents, and source-linked Findings. Document
  order defines citation numbers; invalid `[n]` markers are removed;
  `Report.citations[n-1]` is the bibliography target, and section sources only
  include actually used valid URLs.
- Evidence sidecar is optional enrichment. It writes document analyses,
  evidence, and diagnostics but cannot replace core Findings or alter Writer
  inputs.
- `legacy_projection_patch` is the one Graph output boundary for
  `documents → search_results/credibility_scores`, `findings → key_findings`,
  and `report → report_sections/final_report`. Agent class bodies no longer
  double-write these semantic fields.
- CLI, Web summary, Graph completion logging, and Evaluation use canonical
  Documents, Findings, and Report first. Cache/replay and checkpoint hydration
  retain legacy fallback through `state_compat`.

## Compatibility boundary

```text
legacy State / cache / checkpoint
          │
          ▼
src.state_compat.hydrate_canonical_state
          │  canonical-first: query, research_plan, documents, findings, report, iteration, usage
          ▼
Graph V1 and four Agent modules (canonical inputs/outputs)
          │
          ▼
legacy projection at Graph output → canonical-first CLI / Web / Runtime / Evaluation
```

Legacy fields remain in `ResearchState`. They are not silently synchronized;
the adapter returns a copy and canonical values win on explicit conflicts.

## Remaining Phase 2B follow-up / Phase 2C boundary

Do not merge these into Phase 2A:

1. Remove legacy State fields or change `research_topic` requiredness.
2. Delete legacy State fields or remove legacy cache/checkpoint read support.
3. Add claim-level Evidence confidence merging; Evidence currently remains
   optional diagnostics by design.
4. Merge/remove runtime lifecycle, cache, checkpoint, resume, or lease code.
5. Refactor Evaluation, Memory, or governance beyond canonical-first reading.
6. Add Graph V2, Supervisor, Critic, Reflection, Replanner, or new Agents.

## Validation

Run with a repository-local pytest temporary directory:

```powershell
.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-local
git diff --check
```

The prior milestone-by-milestone record remains at
`experiments/archives/PROJECT_HANDOFF_HISTORY.md`.
