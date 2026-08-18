"""Tests for deterministic search runtime configuration."""

from types import SimpleNamespace

import pytest

from src.search.config import SearchConfig


def test_search_config_uses_documented_defaults() -> None:
    config = SearchConfig()

    assert config.mode == "deterministic_v2"
    assert config.max_search_times == 3
    assert config.max_extract_times == 4
    assert config.max_results_per_search == 3
    assert config.total_timeout_seconds == 90.0
    assert config.search_retry_times == 0
    assert config.extract_retry_times == 0
    assert config.allow_partial_results is True


def test_project_default_uses_the_runtime_owned_deterministic_search_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_config = SimpleNamespace(max_search_queries=3, max_search_results_per_query=3)
    monkeypatch.delenv("SEARCHER_MODE", raising=False)

    assert SearchConfig.from_project_config(project_config).mode == "deterministic_v2"


@pytest.mark.parametrize("mode", ["legacy_agent", "deterministic_v2"])
def test_search_config_accepts_supported_modes(mode: str) -> None:
    assert SearchConfig(mode=mode).mode == mode


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("mode", "unbounded_agent"),
        ("max_search_times", -1),
        ("max_extract_times", -1),
        ("max_results_per_search", -1),
        ("max_search_times", 1.5),
        ("extract_retry_times", "1"),
    ],
)
def test_search_config_rejects_invalid_parameters(field_name: str, value: object) -> None:
    with pytest.raises(ValueError):
        SearchConfig(**{field_name: value})


@pytest.mark.parametrize("timeout", [0, -0.1])
def test_search_config_requires_a_positive_timeout(timeout: float) -> None:
    with pytest.raises(ValueError, match="total_timeout_seconds"):
        SearchConfig(total_timeout_seconds=timeout)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("search_retry_times", -1),
        ("extract_retry_times", -1),
        ("search_retry_times", 0.5),
        ("extract_retry_times", "0"),
    ],
)
def test_search_config_rejects_invalid_retry_values(field_name: str, value: object) -> None:
    with pytest.raises(ValueError):
        SearchConfig(**{field_name: value})


def test_search_config_reads_and_bounds_runtime_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    project_config = SimpleNamespace(
        max_search_queries=7,
        max_search_results_per_query=8,
    )
    monkeypatch.setenv("SEARCHER_MODE", "deterministic_v2")
    monkeypatch.setenv("SEARCHER_TOTAL_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("SEARCHER_SEARCH_RETRY_TIMES", "2")
    monkeypatch.setenv("SEARCHER_EXTRACT_RETRY_TIMES", "1")
    monkeypatch.setenv("SEARCHER_ALLOW_PARTIAL_RESULTS", "off")

    config = SearchConfig.from_project_config(project_config)

    assert config.mode == "deterministic_v2"
    assert config.max_search_times == 3
    assert config.max_extract_times == 4
    assert config.max_results_per_search == 3
    assert config.total_timeout_seconds == 12.5
    assert config.search_retry_times == 2
    assert config.extract_retry_times == 1
    assert config.allow_partial_results is False
