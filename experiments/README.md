# Experiments and historical material

This directory keeps work that is useful for engineering discussion but is not
part of the default ResearchOS portfolio path.

| Area | Location | Status |
|---|---|---|
| Local lexical memory demo | `memory/` | Optional, default off |
| Writer serial vs bounded benchmark | `writer_performance/` | Experimental |
| Real-workload and repeatability runners | `benchmarks/` | Manual only |
| Governance/action exploration | `governance/` | Frozen, not wired to Graph |
| Prior showcase/benchmark outputs and handoff history | `archives/` | Historical record |

The governance implementation remains under `src/` in Phase 1 because its
modules import each other and are directly covered by tests. Moving it would
require compatibility shims and broader package changes, so that work is
explicitly deferred to Phase 2 rather than forced into this low-risk pass.
