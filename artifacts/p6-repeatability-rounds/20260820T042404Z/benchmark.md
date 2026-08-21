# P5.3 Real Workload & SLO Benchmark Report

This report separates **observed data** from **derived metrics**. It is not a production-policy recommendation.

## Benchmark identity

- Generated at: `2026-08-20T05:51:45.925520+00:00`
- Dataset: `researchos_offline_baseline` v`2`
- Modes: `disabled, enabled, partial`

## Observed case/mode runs

| Case | Tags | Mode | Status | Terminal reason | Total wall s | Evidence records | Sidecar s | Provider errors |
|---|---|---|---|---|---:|---:|---:|---:|
| ai-regulation-overview | policy, comparison | disabled | completed | completed | 293.100 | 0 | 0.000 | 0 |
| urban-climate-adaptation | climate, evidence | disabled | completed | completed | 340.837 | 0 | 0.000 | 0 |
| semiconductor-supply-chain | industry, risk | disabled | completed | completed | 297.037 | 0 | 0.000 | 0 |
| clinical-evidence-translation | health, evidence, implementation | disabled | completed | completed | 235.809 | 0 | 0.000 | 0 |
| coastal-adaptation-partial-evidence | climate, partial, evidence | disabled | completed | completed | 296.399 | 0 | 0.000 | 0 |
| search-provider-failure | failure, provider, search | disabled | completed | completed | 256.109 | 0 | 0.000 | 0 |
| ai-regulation-overview | policy, comparison | enabled | completed | completed | 252.806 | 40 | 45.930 | 3 |
| urban-climate-adaptation | climate, evidence | enabled | completed | completed | 327.403 | 34 | 34.370 | 1 |
| semiconductor-supply-chain | industry, risk | enabled | completed | completed | 282.525 | 32 | 31.370 | 0 |
| clinical-evidence-translation | health, evidence, implementation | enabled | completed | completed | 322.419 | 23 | 33.490 | 2 |
| coastal-adaptation-partial-evidence | climate, partial, evidence | enabled | completed | completed | 311.980 | 36 | 38.430 | 0 |
| search-provider-failure | failure, provider, search | enabled | completed | completed | 269.022 | 27 | 32.840 | 0 |
| ai-regulation-overview | policy, comparison | partial | failed | timeout | 90.012 | 0 | 0.000 | 2 |
| urban-climate-adaptation | climate, evidence | partial | completed | completed | 334.047 | 44 | 42.370 | 1 |
| semiconductor-supply-chain | industry, risk | partial | completed | completed | 272.351 | 61 | 56.990 | 1 |
| clinical-evidence-translation | health, evidence, implementation | partial | completed | completed | 353.417 | 27 | 28.890 | 0 |
| coastal-adaptation-partial-evidence | climate, partial, evidence | partial | completed | completed | 325.196 | 33 | 33.790 | 0 |
| search-provider-failure | failure, provider, search | partial | completed | completed | 400.880 | 20 | 46.090 | 3 |

## Derived SLO summary

| Mode | Success rate | Quality pass rate | p50 s | p95 s | Provider failure rate | Writer/total | Evidence adoption | Grounded citations |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| disabled | 1.000 | 0.000 | 293.100 | 340.837 | 0.000 | 0.692 | 0.000 | 0 |
| enabled | 1.000 | 0.167 | 282.525 | 327.403 | 0.500 | 0.592 | 1.000 | 41 |
| partial | 0.833 | 0.000 | 325.196 | 400.880 | 0.667 | 0.602 | 0.800 | 36 |

## Derived Evidence comparison

### enabled relative to disabled

- Comparison status: `comparable`
- Matched successful cases: `ai-regulation-overview, urban-climate-adaptation, semiconductor-supply-chain, clinical-evidence-translation, coastal-adaptation-partial-evidence`
- Comparison note: `-`
- Quality pass-rate deltas: `{'source_coverage': 0.0, 'grounded_citation': 0.2, 'report_completeness': 0.0}`
- Observed wall-latency overhead (s): `33.951417`
- Token overhead: `89398`
- LLM/tool-call overhead: `44` / `0`
- Benefited cases: `coastal-adaptation-partial-evidence`
- Benefited task tags: `climate, evidence, partial`

### partial relative to disabled

- Comparison status: `comparable`
- Matched successful cases: `urban-climate-adaptation, semiconductor-supply-chain, clinical-evidence-translation, coastal-adaptation-partial-evidence`
- Comparison note: `-`
- Quality pass-rate deltas: `{'source_coverage': -0.2, 'grounded_citation': 0.0, 'report_completeness': -0.2}`
- Observed wall-latency overhead (s): `114.928717`
- Token overhead: `75405`
- LLM/tool-call overhead: `34` / `0`
- Benefited cases: `-`
- Benefited task tags: `-`

