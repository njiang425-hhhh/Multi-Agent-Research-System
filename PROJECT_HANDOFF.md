# ResearchOS Portfolio Refactor Handoff

## Current product position

ResearchOS is a source-aware research Agent portfolio project. Its core value
is a fixed, inspectable research flow:

```text
Question → Planner → Searcher → Synthesizer → Writer → Markdown report
```

The repository is not positioned as a production Agent Platform. Runtime,
memory, governance, and benchmark work may remain available for engineering
discussion, but they are not part of the default demo story.

## Phase 1 — low-risk portfolio slimming

Completed in this phase:

- Rewrote the README around the four-stage Agent pipeline, deterministic
  bounded search, one-round adaptive follow-up, provenance, trace, and
  read-only evaluation.
- Added `ARCHITECTURE.md` with the Graph, Agent contracts, bounded-search
  behavior, and code-reading order.
- Replaced the monolithic public `src.agents` module with an `src/agents/`
  package exposing role-specific Planner, Searcher, Synthesizer, and Writer
  modules. Existing `from src.agents import ...` imports remain valid.
- Moved the former monolithic implementation to
  `src/agents/_implementation.py`; Phase 1 wrappers preserve its behavior and
  existing test injection seam.
- Moved optional Memory, Writer-performance, and manual benchmark scripts into
  `experiments/`, and moved historical showcase/benchmark outputs and the prior
  detailed handoff into `experiments/archives/`.
- Kept the default path unchanged: deterministic Searcher, at most one bounded
  adaptive follow-up, serial Writer, optional Memory and Evidence disabled.

## Current code-reading path

1. `README.md`
2. `ARCHITECTURE.md`
3. `src/graph.py`
4. `src/agents/planner.py`
5. `src/agents/searcher.py`
6. `src/agents/synthesizer.py`
7. `src/agents/writer.py`
8. `src/search/executor.py`

## Core versus optional material

| Area | Location | Position |
|---|---|---|
| Four-stage research pipeline | `src/graph.py`, `src/agents/`, `src/search/` | Core |
| Prompts, citations, credibility, providers | `src/prompts/`, `src/utils/`, `src/llm/` | Core support |
| Trace, usage, timeout, checkpoint support | `src/runtime_*.py`, `src/agent_trace.py` | Existing support; not a primary demo feature |
| Evidence sidecar | `src/evidence/` | Optional, disabled by default |
| Local lexical memory | `src/memory/`, `experiments/memory/` | Optional experiment |
| Writer concurrency experiments | `experiments/writer_performance/` | Experimental |
| Workload/repeatability benchmarks | `experiments/benchmarks/`, `experiments/archives/` | Manual/historical |
| Action and human-review contracts | `src/action_execution.py`, `src/human_review.py`, `experiments/governance/` | Frozen exploration, not connected to Graph |

## Behavior invariants retained in Phase 1

- Graph V1 remains `Planner → Searcher → Synthesizer → Writer`.
- The current State contract, including legacy/V1 compatibility fields, is
  unchanged.
- Runtime control, lifecycle, lease, cache, and checkpoint semantics are
  unchanged.
- Searcher defaults to `deterministic_v2`; legacy search remains available for
  compatibility rather than as a portfolio default.
- Adaptive search remains limited to one planned-query follow-up; no replan,
  Reflection, Supervisor, or Graph branch was added.
- Writer defaults to serial section generation; bounded concurrency remains a
  compatibility experiment.
- Evidence, evaluation, and Memory do not alter Graph routing.

## Phase 2 candidates

Do not begin these without a separate scope decision:

1. Physically extract shared Agent helpers and class bodies out of
   `src/agents/_implementation.py`. The Phase 1 package split intentionally
   avoids behavior rewrites and preserves public/test injection seams.
2. Choose one canonical State representation and isolate legacy checkpoint
   compatibility. No State fields were removed in Phase 1.
3. Relocate governance source modules behind compatibility shims, or remove
   them after deciding they no longer support the portfolio.
4. Simplify Runtime modules only after deciding which checkpoint/resume APIs
   remain supported.
5. Choose whether source URL provenance alone is the core story or whether the
   optional Evidence sidecar should be integrated end-to-end.
6. Compress evaluation exports and test grouping without changing the existing
   read-only evaluator behavior.

## Validation

Run the deterministic fake-only suite with a repository-local temporary path:

```powershell
.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-local
git diff --check
```

The prior milestone-by-milestone record is preserved at
`experiments/archives/PROJECT_HANDOFF_HISTORY.md`.

Phase 1 verification on 2026-08-28: `364 passed, 2 warnings` using the
repository-local pytest temporary directory.
