# 核心回归层

这些测试覆盖 release-ready Research Agent 的 golden path：

| 契约 | 测试模块 |
|---|---|
| 完整 fake workflow 与 trace | `test_agent_trace.py` |
| Canonical cache round-trip | `test_cache_canonical_serialization.py` |
| Citation integrity / Evidence grounding 语义 | `test_citation_evaluation_semantics.py` |
| Runner、CLI、Web 一致性 | `test_runner_entry_parity.py` |
| Canonical State 与 legacy hydration 边界 | `test_state_convergence.py` |
| 确定性 SearchExecutor 与来源 Documents | `test_search_executor.py`、`test_searcher_documents.py` |
| 来源关联 Findings | `test_synthesizer_findings.py` |
| Writer 报告生成 | `test_writer_report_migration.py` |

其余顶层测试覆盖 runtime compatibility、provider、可选 Memory/Evidence sidecar 与其他非核心
契约。冻结的 Governance 测试位于 `tests/experiments/governance/`；默认 CI 始终为 fake-only。
