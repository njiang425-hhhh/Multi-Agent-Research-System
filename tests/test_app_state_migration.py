"""Web entry-point coverage for the ResearchState V1 migration."""

import asyncio

import app


class _FakeProgressDisplay:
    async def update(self, _update: object) -> None:
        pass


class _FakeGraph:
    def __init__(self) -> None:
        self.initial_state = None

    async def ainvoke(self, state):
        self.initial_state = state
        return {"search_results": [], "key_findings": []}


def test_web_entry_double_writes_legacy_topic_and_v1_query(monkeypatch) -> None:
    """The Web entry passes identical legacy and V1 task values to the graph."""
    graph = _FakeGraph()

    async def fake_emit_complete(*_args: object) -> None:
        pass

    monkeypatch.setattr(app, "create_research_graph", lambda: graph)
    monkeypatch.setattr(app, "emit_complete", fake_emit_complete)

    asyncio.run(app.run_research_with_updates("Web migration topic", _FakeProgressDisplay()))

    assert graph.initial_state is not None
    assert graph.initial_state.research_topic == "Web migration topic"
    assert graph.initial_state.query == "Web migration topic"
