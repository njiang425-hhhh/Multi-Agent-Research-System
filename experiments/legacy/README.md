# Legacy Agent paths

The core portfolio path uses `SEARCHER_MODE=deterministic_v2` and a serial
Writer. The legacy autonomous Searcher and bounded Writer scheduling remain in
the Phase 1 compatibility implementation so existing callers and tests retain
their behavior.

They are intentionally not part of the README or architecture reading path.
Physically separating their implementation from shared Agent helpers is a
Phase 2 task because it currently touches public imports, runtime injection,
and direct test seams.
