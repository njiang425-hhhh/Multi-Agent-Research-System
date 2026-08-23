# P5.3 Real Workload & SLO Benchmark Report

This report separates **observed data** from **derived metrics**. It is not a production-policy recommendation.

## Benchmark identity

- Generated at: `2026-08-19T04:08:08.149029+00:00`
- Dataset: `researchos_offline_baseline` v`2`
- Modes: `disabled, enabled, partial`

## Observed case/mode runs

| Case | Tags | Mode | Status | Terminal reason | Total wall s | Evidence records | Sidecar s | Provider errors |
|---|---|---|---|---|---:|---:|---:|---:|
| ai-regulation-overview | policy, comparison | disabled | failed | timeout | 398.739 | 0 | 0.000 | 2 |
| urban-climate-adaptation | climate, evidence | disabled | completed | completed | 617.229 | 0 | 0.000 | 0 |
| semiconductor-supply-chain | industry, risk | disabled | failed | timeout | 539.904 | 0 | 0.000 | 2 |
| clinical-evidence-translation | health, evidence, implementation | disabled | failed | timeout | 229.040 | 0 | 0.000 | 2 |
| coastal-adaptation-partial-evidence | climate, partial, evidence | disabled | failed | timeout | 268.690 | 0 | 0.000 | 2 |
| search-provider-failure | failure, provider, search | disabled | failed | timeout | 155.936 | 0 | 0.000 | 2 |
| ai-regulation-overview | policy, comparison | enabled | failed | timeout | 294.403 | 34 | 50.680 | 2 |
| urban-climate-adaptation | climate, evidence | enabled | failed | agent_failed | 1.234 | 0 | 0.000 | 4 |
| semiconductor-supply-chain | industry, risk | enabled | failed | agent_failed | 1.177 | 0 | 0.000 | 4 |
| clinical-evidence-translation | health, evidence, implementation | enabled | failed | agent_failed | 1.188 | 0 | 0.000 | 4 |
| coastal-adaptation-partial-evidence | climate, partial, evidence | enabled | failed | agent_failed | 1.177 | 0 | 0.000 | 4 |
| search-provider-failure | failure, provider, search | enabled | failed | agent_failed | 1.220 | 0 | 0.000 | 4 |
| ai-regulation-overview | policy, comparison | partial | failed | agent_failed | 1.181 | 0 | 0.000 | 4 |
| urban-climate-adaptation | climate, evidence | partial | failed | agent_failed | 1.731 | 0 | 0.000 | 4 |
| semiconductor-supply-chain | industry, risk | partial | failed | agent_failed | 1.321 | 0 | 0.000 | 4 |
| clinical-evidence-translation | health, evidence, implementation | partial | failed | agent_failed | 1.163 | 0 | 0.000 | 4 |
| coastal-adaptation-partial-evidence | climate, partial, evidence | partial | failed | agent_failed | 1.174 | 0 | 0.000 | 4 |
| search-provider-failure | failure, provider, search | partial | failed | agent_failed | 1.193 | 0 | 0.000 | 4 |

## Derived SLO summary

| Mode | Success rate | Quality pass rate | p50 s | p95 s | Provider failure rate | Writer/total | Evidence adoption | Grounded citations |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| disabled | 0.167 | 0.000 | 268.690 | 617.229 | 0.833 | 0.638 | 0.000 | 0 |
| enabled | 0.000 | 0.000 | 1.188 | 294.403 | 1.000 | 0.391 | 0.200 | 0 |
| partial | 0.000 | 0.000 | 1.181 | 1.731 | 1.000 | 0.000 | 0.000 | 0 |

## Derived Evidence comparison

### enabled relative to disabled

- Quality pass-rate deltas: `{'source_coverage': -0.8, 'grounded_citation': 0.0, 'report_completeness': -0.2}`
- Latency overhead (s): `0.0`
- Token overhead: `-34534`
- Benefited cases: `-`
- Benefited task tags: `-`

### partial relative to disabled

- Quality pass-rate deltas: `{'source_coverage': -1.0, 'grounded_citation': 0.0, 'report_completeness': -0.2}`
- Latency overhead (s): `0.0`
- Token overhead: `-55672`
- Benefited cases: `-`
- Benefited task tags: `-`
