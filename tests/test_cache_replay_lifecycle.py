"""Fake-only coverage for cache replay runtime lifecycle semantics."""

import asyncio
from copy import deepcopy
from uuid import UUID

import pytest

from src import graph as graph_module


class _FakeCache:
    def __init__(self, payload: dict | None) -> None:
        self.payload = payload
        self.set_calls: list[tuple[str, dict]] = []

    def get(self, _topic: str):
        return self.payload

    def set(self, topic: str, state: dict) -> None:
        self.set_calls.append((topic, state))


class _FakeGraph:
    def __init__(self) -> None:
        self.calls = 0
        self.initial_states = []

    async def ainvoke(self, state, config=None):
        self.calls += 1
        self.initial_states.append((state, config))
        return {
            "run_id": state.run_id,
            "status": "completed",
            "current_stage": "complete",
            "final_report": "# graph report",
            "error": None,
            "iteration": state.iteration,
            "iterations": state.iterations,
        }


def _eligible_payload() -> dict:
    return {
        "run_id": "cached-run-id",
        "status": "completed",
        "current_stage": "complete",
        "iteration": 4,
        "iterations": 4,
        "error": None,
        "final_report": "# cached report",
        "key_findings": ["cached finding"],
        "nested": {"values": ["original"]},
    }


def test_cache_hit_creates_isolated_replay_runs_without_executing_graph(monkeypatch) -> None:
    cached_payload = _eligible_payload()
    original_payload = deepcopy(cached_payload)
    cache = _FakeCache(cached_payload)
    graph = _FakeGraph()
    monkeypatch.setattr(graph_module, "ResearchCache", lambda: cache)
    monkeypatch.setattr(graph_module, "create_research_graph", lambda checkpointer=None: graph)

    first = asyncio.run(graph_module.run_research("cache topic", verbose=False))
    second = asyncio.run(graph_module.run_research("cache topic", verbose=False))

    assert UUID(first["run_id"]).version == 4
    assert UUID(second["run_id"]).version == 4
    assert first["run_id"] != second["run_id"]
    assert first["run_id"] != cached_payload["run_id"]
    assert first["status"] == second["status"] == "completed"
    assert first["current_stage"] == second["current_stage"] == "complete"
    assert first["iteration"] == second["iteration"] == 0
    assert first["iterations"] == second["iterations"] == 4
    assert graph.calls == 0
    assert cache.set_calls == []

    first["nested"]["values"].append("changed replay")
    assert cached_payload == original_payload


@pytest.mark.parametrize(
    "payload",
    [
        {"final_report": "# report", "error": "legacy failure", "iterations": 2},
        {"final_report": "# report", "status": "failed", "iterations": 2},
        {"final_report": None, "error": None, "iterations": 2},
    ],
)
def test_ineligible_cache_payload_executes_graph_instead_of_replaying(monkeypatch, payload: dict) -> None:
    cache = _FakeCache(payload)
    graph = _FakeGraph()
    monkeypatch.setattr(graph_module, "ResearchCache", lambda: cache)
    monkeypatch.setattr(graph_module, "create_research_graph", lambda checkpointer=None: graph)

    result = asyncio.run(
        graph_module.run_research(
            "cache topic",
            verbose=False,
            use_checkpoints=False,
        )
    )

    assert graph.calls == 1
    assert result["final_report"] == "# graph report"
    assert result["run_id"] != payload.get("run_id")
    assert cache.set_calls == [("cache topic", result)]


def test_successful_cache_write_excludes_router_early_termination(monkeypatch) -> None:
    cache = _FakeCache(None)

    class _EarlyGraph(_FakeGraph):
        async def ainvoke(self, state, config=None):
            self.calls += 1
            self.initial_states.append((state, config))
            return {
                "run_id": state.run_id,
                "status": "running",
                "current_stage": "synthesizing",
                "final_report": None,
                "error": None,
                "iteration": state.iteration,
                "iterations": state.iterations,
            }

    graph = _EarlyGraph()
    monkeypatch.setattr(graph_module, "ResearchCache", lambda: cache)
    monkeypatch.setattr(graph_module, "create_research_graph", lambda checkpointer=None: graph)

    result = asyncio.run(
        graph_module.run_research(
            "cache topic",
            verbose=False,
            use_checkpoints=False,
        )
    )

    assert graph.calls == 1
    assert result["status"] == "failed"
    assert result["current_stage"] == "failed"
    assert result["error"] is None
    assert cache.set_calls == []
