# P7 Writer Performance A/B Benchmark

Manual-only observed data. Evidence disabled, cache disabled, no repeatability claim.

- Generated at: `2026-08-21T03:31:58.713691+00:00`
- Case: `ai-regulation-overview`

| Arm | Status | Total wall s | Writer node s | Writer LLM s | Sections | Citations | Eval |
|---|---|---:|---:|---:|---:|---:|---|
| serial(1) | completed | 238.816 | 166.782 | 166.764 | 8 | 9 | failed |
| bounded(2) | completed | 238.236 | 119.109 | 212.908 | 8 | 9 | failed |

## Comparison

- Writer node latency delta, bounded - serial (s): `-47.673324`
- Total wall latency delta, bounded - serial (s): `-0.579667`
- Regressions: `-`
