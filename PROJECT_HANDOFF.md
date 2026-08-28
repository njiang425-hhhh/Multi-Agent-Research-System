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
- Graph nodes hydrate only the non-semantic core fields before Agent execution;
  this keeps legacy-only checkpoint/resume input working without altering
  `search_results` / `documents` or `key_findings` / `findings` behavior.
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

## Compatibility boundary

```text
legacy State / cache / checkpoint
          │
          ▼
src.state_compat.hydrate_canonical_state
          │  canonical-first: query, research_plan, iteration, usage
          ▼
Graph V1 and four Agent modules
          │
          ▼
canonical-first CLI / Web / Runtime / Evaluation presentation
```

Legacy fields remain in `ResearchState`. They are not silently synchronized;
the adapter returns a copy and canonical values win on explicit conflicts.

## Explicit Phase 2B boundary

Do not merge these into Phase 2A:

1. Remove legacy State fields or change `research_topic` requiredness.
2. Make `Documents` the Writer source list. Documents currently de-duplicate
   URLs while `search_results` preserves order for citation numbering.
3. Make `Findings` the Writer input. Evidence sidecar may replace `findings`,
   while current Writer intentionally consumes `key_findings`.
4. Change Writer citation numbering, source order, or optional Evidence
   semantics.
5. Merge/remove runtime lifecycle, cache, checkpoint, resume, or lease code.
6. Refactor Evaluation, Memory, or governance beyond canonical-first reading.
7. Add Graph V2, Supervisor, Critic, Reflection, Replanner, or new Agents.

## Validation

Run with a repository-local pytest temporary directory:

```powershell
.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-local
git diff --check
```

The prior milestone-by-milestone record remains at
`experiments/archives/PROJECT_HANDOFF_HISTORY.md`.
