# P5.3 Real Workload & SLO Benchmark Report

This report separates **observed data** from **derived metrics**. It is not a production-policy recommendation.

## Benchmark identity

- Generated at: `2026-08-19T05:49:26.875800+00:00`
- Dataset: `researchos_offline_baseline` v`2`
- Modes: `disabled, enabled, partial`

## Observed case/mode runs

| Case | Tags | Mode | Status | Terminal reason | Total wall s | Evidence records | Sidecar s | Provider errors |
|---|---|---|---|---|---:|---:|---:|---:|
| ai-regulation-overview | policy, comparison | disabled | completed | completed | 265.158 | 0 | 0.000 | 0 |
| urban-climate-adaptation | climate, evidence | disabled | completed | completed | 357.798 | 0 | 0.000 | 0 |
| semiconductor-supply-chain | industry, risk | disabled | completed | completed | 230.809 | 0 | 0.000 | 0 |
| clinical-evidence-translation | health, evidence, implementation | disabled | completed | completed | 240.136 | 0 | 0.000 | 0 |
| coastal-adaptation-partial-evidence | climate, partial, evidence | disabled | completed | completed | 301.461 | 0 | 0.000 | 0 |
| search-provider-failure | failure, provider, search | disabled | completed | completed | 278.040 | 0 | 0.000 | 0 |
| ai-regulation-overview | policy, comparison | enabled | completed | completed | 291.394 | 23 | 32.860 | 1 |
| urban-climate-adaptation | climate, evidence | enabled | failed | agent_failed | 16.096 | 0 | 0.000 | 1 |
| semiconductor-supply-chain | industry, risk | enabled | completed | completed | 308.646 | 43 | 34.450 | 0 |
| clinical-evidence-translation | health, evidence, implementation | enabled | completed | completed | 378.542 | 21 | 35.200 | 2 |
| coastal-adaptation-partial-evidence | climate, partial, evidence | enabled | completed | completed | 325.660 | 46 | 42.820 | 0 |
| search-provider-failure | failure, provider, search | enabled | completed | completed | 275.522 | 25 | 42.430 | 4 |
| ai-regulation-overview | policy, comparison | partial | completed | completed | 305.003 | 46 | 44.380 | 1 |
| urban-climate-adaptation | climate, evidence | partial | completed | completed | 354.473 | 30 | 32.860 | 0 |
| semiconductor-supply-chain | industry, risk | partial | completed | completed | 308.463 | 32 | 26.680 | 0 |
| clinical-evidence-translation | health, evidence, implementation | partial | completed | completed | 293.863 | 41 | 41.580 | 2 |
| coastal-adaptation-partial-evidence | climate, partial, evidence | partial | completed | completed | 259.306 | 26 | 38.160 | 2 |
| search-provider-failure | failure, provider, search | partial | completed | completed | 306.025 | 22 | 41.010 | 2 |

## Derived SLO summary

| Mode | Success rate | Quality pass rate | p50 s | p95 s | Provider failure rate | Writer/total | Evidence adoption | Grounded citations |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| disabled | 1.000 | 0.000 | 265.158 | 357.798 | 0.000 | 0.671 | 0.000 | 0 |
| enabled | 0.833 | 0.167 | 291.394 | 378.542 | 0.667 | 0.602 | 0.800 | 35 |
| partial | 1.000 | 0.333 | 305.003 | 354.473 | 0.667 | 0.642 | 1.000 | 45 |

## Derived Evidence comparison

### enabled relative to disabled

- Comparison status: `comparable`
- Matched successful cases: `ai-regulation-overview, semiconductor-supply-chain, clinical-evidence-translation, coastal-adaptation-partial-evidence`
- Comparison note: `-`
- Quality pass-rate deltas: `{'source_coverage': -0.2, 'grounded_citation': 0.2, 'report_completeness': -0.2}`
- Observed wall-latency overhead (s): `266.679012`
- Token overhead: `79332`
- LLM/tool-call overhead: `34` / `0`
- Benefited cases: `ai-regulation-overview`
- Benefited task tags: `comparison, policy`

### partial relative to disabled

- Comparison status: `comparable`
- Matched successful cases: `ai-regulation-overview, urban-climate-adaptation, semiconductor-supply-chain, clinical-evidence-translation, coastal-adaptation-partial-evidence`
- Comparison note: `-`
- Quality pass-rate deltas: `{'source_coverage': 0.0, 'grounded_citation': 0.2, 'report_completeness': 0.0}`
- Observed wall-latency overhead (s): `125.747007`
- Token overhead: `119911`
- LLM/tool-call overhead: `43` / `0`
- Benefited cases: `ai-regulation-overview`
- Benefited task tags: `comparison, policy`
