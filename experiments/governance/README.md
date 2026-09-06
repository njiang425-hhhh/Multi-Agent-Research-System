# 冻结的 Governance 探索

本包包含 action authorization、quality-to-action advice、durable action request 和
human-review contract。默认 Runner、Graph、Planner、Searcher、Synthesizer、Writer 与
稳定 evaluator 都不会导入它们。

这些模块仅保留用于设计讨论和 fake contract test。它们不会派发外部 action、修改研究内容、
新增 Graph route 或形成生产级审批工作流。为兼容历史 import，仍保留以下 deprecated shim：
`src.action_execution`、`src.human_review`、`src.evaluation.quality_action` 和
`src.evaluation.action_authorization`。
