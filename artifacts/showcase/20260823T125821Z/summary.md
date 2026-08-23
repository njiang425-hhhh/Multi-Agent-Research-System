# P16 End-to-End Showcase

Observed DeepSeek/Tavily (or injected fake) runs with post-run, read-only P5/P9/P10 evaluation. These metrics are descriptive and do not control runtime behavior or claim provider-quality improvement.

- Generated at: `2026-08-23T13:16:37.445324+00:00`
- Cases: `4`; completed: `3`; failed: `1`
- Adaptive triggered: `2`; memory enabled cases: `0`

| Case | Category | Status | Wall s | Adaptive | Memory retrieved | Source | Grounded citation | Report |
|---|---|---|---:|---|---:|---|---|---|
| comparison-ai-governance | comparison | completed | 354.711 | no_progress | 0 | passed | failed | passed |
| evidence-clinical-ai-screening | evidence_research | failed | 175.527 | not_needed | 0 | passed | unavailable | unavailable |
| risk-regulated-ai-agents | risk_implementation | completed | 290.570 | not_needed | 0 | passed | failed | passed |
| trend-ai-semiconductor-supply | trend_industry | completed | 275.457 | no_progress | 0 | passed | failed | passed |

## Boundaries

- Graph V1, Planner/Searcher/Synthesizer/Writer contracts, Runtime ownership, and Writer input are unchanged.
- Adaptive diagnostics only observe the existing single P14 supplementary-search bound.
- Memory is off unless one explicit case is selected; it remains bounded local lexical memory and never reaches Writer.
- P9/P10 lexical metrics and P5 report metrics are read-only archive observations, not quality gates or provider claims.
