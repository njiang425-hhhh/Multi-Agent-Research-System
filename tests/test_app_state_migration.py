"""Web entry-point coverage for the ResearchState V1 migration."""

import asyncio

import app


class _FakeProgressDisplay:
    async def update(self, _update: object) -> None:
        pass


class _FakeRunner:
    def __init__(self) -> None:
        self.topic = None

    async def run(self, topic, *, verbose=True):
        self.topic = topic
        return {"search_results": [], "key_findings": []}


def test_web_entry_uses_explicit_runner_options(monkeypatch) -> None:
    """The Web entry uses Runner rather than invoking Graph directly."""
    runner = _FakeRunner()

    async def fake_emit_complete(*_args: object) -> None:
        pass

    captured_options = {}

    def create_runner(**options):
        captured_options.update(options)
        return runner

    monkeypatch.setattr(app, "ResearchRunner", create_runner)
    monkeypatch.setattr(app, "emit_complete", fake_emit_complete)

    asyncio.run(app.run_research_with_updates("Web migration topic", _FakeProgressDisplay()))

    assert runner.topic == "Web migration topic"
    assert captured_options == {
        "use_cache": False,
        "use_checkpoints": False,
        "persist_memory": False,
    }
