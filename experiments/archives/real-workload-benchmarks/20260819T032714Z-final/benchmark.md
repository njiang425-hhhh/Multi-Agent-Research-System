# P5.3 Real Workload & SLO Benchmark Report

This report separates **observed data** from **derived metrics**. It is not a production-policy recommendation.

## Benchmark identity

- Generated at: `2026-08-19T04:07:30.391325+00:00`
- Dataset: `researchos_offline_baseline` v`2`
- Modes: `disabled, enabled, partial`

## Observed case/mode runs

| Case | Tags | Mode | Status | Terminal reason | Total wall s | Evidence records | Sidecar s | Provider errors |
|---|---|---|---|---|---:|---:|---:|---:|
| ai-regulation-overview | policy, comparison | disabled | failed | timeout | 483.811 | 0 | 0.000 | 2 |
| urban-climate-adaptation | climate, evidence | disabled | failed | timeout | 275.573 | 0 | 0.000 | 2 |
| semiconductor-supply-chain | industry, risk | disabled | failed | timeout | 90.046 | 0 | 0.000 | 2 |
| clinical-evidence-translation | health, evidence, implementation | disabled | completed | completed | 530.219 | 0 | 0.000 | 0 |
| coastal-adaptation-partial-evidence | climate, partial, evidence | disabled | failed | timeout | 226.068 | 0 | 0.000 | 2 |
| search-provider-failure | failure, provider, search | disabled | completed | completed | 706.814 | 0 | 0.000 | 0 |
| ai-regulation-overview | policy, comparison | enabled | failed | timeout | 90.027 | 0 | 0.000 | 2 |
| urban-climate-adaptation | climate, evidence | enabled | failed | agent_failed | 1.297 | 0 | 0.000 | 4 |
| semiconductor-supply-chain | industry, risk | enabled | failed | agent_failed | 1.218 | 0 | 0.000 | 4 |
| clinical-evidence-translation | health, evidence, implementation | enabled | failed | agent_failed | 1.209 | 0 | 0.000 | 4 |
| coastal-adaptation-partial-evidence | climate, partial, evidence | enabled | failed | agent_failed | 1.215 | 0 | 0.000 | 4 |
| search-provider-failure | failure, provider, search | enabled | failed | agent_failed | 1.196 | 0 | 0.000 | 4 |
| ai-regulation-overview | policy, comparison | partial | failed | agent_failed | 1.203 | 0 | 0.000 | 4 |
| urban-climate-adaptation | climate, evidence | partial | failed | agent_failed | 1.217 | 0 | 0.000 | 4 |
| semiconductor-supply-chain | industry, risk | partial | failed | agent_failed | 1.199 | 0 | 0.000 | 4 |
| clinical-evidence-translation | health, evidence, implementation | partial | failed | agent_failed | 1.205 | 0 | 0.000 | 4 |
| coastal-adaptation-partial-evidence | climate, partial, evidence | partial | failed | agent_failed | 1.192 | 0 | 0.000 | 4 |
| search-provider-failure | failure, provider, search | partial | failed | agent_failed | 1.254 | 0 | 0.000 | 4 |

## Derived SLO summary

| Mode | Success rate | Quality pass rate | p50 s | p95 s | Provider failure rate | Writer/total | Evidence adoption | Grounded citations |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| disabled | 0.333 | 0.000 | 275.573 | 706.814 | 0.667 | 0.675 | 0.000 | 0 |
| enabled | 0.000 | 0.000 | 1.215 | 90.027 | 1.000 | 0.000 | 0.000 | 0 |
| partial | 0.000 | 0.000 | 1.203 | 1.254 | 1.000 | 0.000 | 0.000 | 0 |

## Derived Evidence comparison

### enabled relative to disabled

- Comparison status: `inconclusive`
- Matched successful cases: `-`
- Comparison note: `no non-failure case completed in both disabled and target modes`
- Quality pass-rate deltas: `{'source_coverage': -0.8, 'grounded_citation': 0.0, 'report_completeness': -0.2}`
- Observed wall-latency overhead (s): `-`
- Token overhead: `-59502`
- LLM/tool-call overhead: `-` / `-`
- Benefited cases: `-`
- Benefited task tags: `-`

### partial relative to disabled

- Comparison status: `inconclusive`
- Matched successful cases: `-`
- Comparison note: `no non-failure case completed in both disabled and target modes`
- Quality pass-rate deltas: `{'source_coverage': -0.8, 'grounded_citation': 0.0, 'report_completeness': -0.2}`
- Observed wall-latency overhead (s): `-`
- Token overhead: `-59458`
- LLM/tool-call overhead: `-` / `-`
- Benefited cases: `-`
- Benefited task tags: `-`
