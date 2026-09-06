# 实验与归档材料

此目录中的任何内容都不会改变默认的
Planner → Searcher → Synthesizer → Writer 工作流。

| 范围 | 位置 | 状态 |
|---|---|---|
| Governance、action authorization/execution、human review | `governance/` | 冻结的契约探索 |
| Calibration、Evidence-value、real-workload、repeatability evaluator | `evaluation/` | 手动/研究性评估 |
| 使用真实 provider 的 benchmark 入口 | `benchmarks/` | 仅手动运行；不进入 CI |
| 本地 Memory demo | `memory/` | 可选演示 |
| Writer performance study | `writer_performance/` | 实验性 |
| 历史结果记录 | `archives/` | 只读归档 |
| Legacy 说明 | `legacy/` | 兼容历史 |

原 `src` import path 的短小模块保留了旧脚本和契约测试需要的直接导入。它们转发到本目录
中的实现，而不是第二份真相。
