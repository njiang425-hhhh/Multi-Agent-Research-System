# Legacy 兼容路径

受支持的作品集路径为 `SEARCHER_MODE=deterministic_v2` 与串行 Writer section。历史
autonomous Searcher 已隔离到 `src/agents/compat/autonomous_searcher.py`，仅在显式请求
`SearchConfig(mode="legacy_agent")` 时启用。

它仅为避免破坏旧调用方而保留；不属于默认工作流评估范围，也不应被作为推荐的 Agent 架构。
