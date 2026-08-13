"""LLM provider selection tests without creating real model clients."""

from typing import Any

import pytest

import src.llm.factory as factory_module


class FakeChatOpenAI:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class FakeChatOllama:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class FakeChatGoogleGenerativeAI:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


@pytest.fixture
def fake_llm_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(factory_module, "ChatOpenAI", FakeChatOpenAI)
    monkeypatch.setattr(factory_module, "ChatOllama", FakeChatOllama)
    monkeypatch.setattr(
        factory_module,
        "ChatGoogleGenerativeAI",
        FakeChatGoogleGenerativeAI,
    )


def test_get_llm_selects_deepseek_and_preserves_compatible_base_url(
    monkeypatch: pytest.MonkeyPatch,
    fake_llm_clients: None,
) -> None:
    monkeypatch.setattr(factory_module.config, "model_provider", "deepseek")
    monkeypatch.setattr(factory_module.config, "model_name", "deepseek-chat")
    monkeypatch.setattr(factory_module.config, "deepseek_base_url", "https://deepseek.invalid")
    monkeypatch.setattr(factory_module.config, "deepseek_api_key", "fake-deepseek-key")

    llm = factory_module.get_llm(temperature=0.2)

    assert isinstance(llm, FakeChatOpenAI)
    assert llm.kwargs == {
        "model": "deepseek-chat",
        "base_url": "https://deepseek.invalid",
        "api_key": "fake-deepseek-key",
        "temperature": 0.2,
    }


def test_get_llm_passes_explicit_extra_body_only_to_chat_openai(
    monkeypatch: pytest.MonkeyPatch,
    fake_llm_clients: None,
) -> None:
    monkeypatch.setattr(factory_module.config, "model_provider", "deepseek")
    monkeypatch.setattr(factory_module.config, "model_name", "deepseek-v4-pro")
    monkeypatch.setattr(factory_module.config, "deepseek_base_url", "https://deepseek.invalid")
    monkeypatch.setattr(factory_module.config, "deepseek_api_key", "fake-deepseek-key")
    extra_body = {"thinking": {"type": "disabled"}}

    llm = factory_module.get_llm(extra_body=extra_body)

    assert isinstance(llm, FakeChatOpenAI)
    assert llm.kwargs["extra_body"] == extra_body


def test_get_llm_selects_openai_and_appends_v1_to_base_url(
    monkeypatch: pytest.MonkeyPatch,
    fake_llm_clients: None,
) -> None:
    monkeypatch.setattr(factory_module.config, "model_provider", "openai")
    monkeypatch.setattr(factory_module.config, "model_name", "fake-openai-model")
    monkeypatch.setattr(factory_module.config, "openai_base_url", "https://openai.invalid")
    monkeypatch.setattr(factory_module.config, "openai_api_key", "fake-openai-key")

    llm = factory_module.get_llm(temperature=0.4)

    assert isinstance(llm, FakeChatOpenAI)
    assert llm.kwargs == {
        "model": "fake-openai-model",
        "base_url": "https://openai.invalid/v1",
        "api_key": "fake-openai-key",
        "temperature": 0.4,
    }


def test_get_llm_selects_ollama_with_runtime_context_limit(
    monkeypatch: pytest.MonkeyPatch,
    fake_llm_clients: None,
) -> None:
    monkeypatch.setattr(factory_module.config, "model_provider", "ollama")
    monkeypatch.setattr(factory_module.config, "model_name", "qwen-fake")
    monkeypatch.setattr(factory_module.config, "ollama_base_url", "http://ollama.invalid")

    llm = factory_module.get_llm(temperature=0.1)

    assert isinstance(llm, FakeChatOllama)
    assert llm.kwargs == {
        "model": "qwen-fake",
        "base_url": "http://ollama.invalid",
        "temperature": 0.1,
        "num_ctx": 8192,
    }


def test_get_llm_selects_gemini(
    monkeypatch: pytest.MonkeyPatch,
    fake_llm_clients: None,
) -> None:
    monkeypatch.setattr(factory_module.config, "model_provider", "gemini")
    monkeypatch.setattr(factory_module.config, "model_name", "gemini-fake")
    monkeypatch.setattr(factory_module.config, "google_api_key", "fake-gemini-key")

    llm = factory_module.get_llm(temperature=0.6)

    assert isinstance(llm, FakeChatGoogleGenerativeAI)
    assert llm.kwargs == {
        "model": "gemini-fake",
        "google_api_key": "fake-gemini-key",
        "temperature": 0.6,
    }


def test_get_llm_selects_llamacpp_and_appends_v1_to_base_url(
    monkeypatch: pytest.MonkeyPatch,
    fake_llm_clients: None,
) -> None:
    monkeypatch.setattr(factory_module.config, "model_provider", "llamacpp")
    monkeypatch.setattr(factory_module.config, "model_name", "llamacpp-fake")
    monkeypatch.setattr(factory_module.config, "llamacpp_base_url", "http://llamacpp.invalid")

    llm = factory_module.get_llm(temperature=0.3)

    assert isinstance(llm, FakeChatOpenAI)
    assert llm.kwargs == {
        "model": "llamacpp-fake",
        "base_url": "http://llamacpp.invalid/v1",
        "api_key": "not-needed",
        "temperature": 0.3,
    }


def test_get_llm_uses_model_override(
    monkeypatch: pytest.MonkeyPatch,
    fake_llm_clients: None,
) -> None:
    monkeypatch.setattr(factory_module.config, "model_provider", "deepseek")
    monkeypatch.setattr(factory_module.config, "model_name", "configured-model")
    monkeypatch.setattr(factory_module.config, "deepseek_base_url", "https://deepseek.invalid")
    monkeypatch.setattr(factory_module.config, "deepseek_api_key", "fake-deepseek-key")

    llm = factory_module.get_llm(model_override="override-model")

    assert isinstance(llm, FakeChatOpenAI)
    assert llm.kwargs["model"] == "override-model"


def test_get_llm_uses_provider_override(
    monkeypatch: pytest.MonkeyPatch,
    fake_llm_clients: None,
) -> None:
    monkeypatch.setattr(factory_module.config, "model_provider", "gemini")
    monkeypatch.setattr(factory_module.config, "model_name", "configured-model")
    monkeypatch.setattr(factory_module.config, "ollama_base_url", "http://ollama.invalid")

    llm = factory_module.get_llm(provider_override="ollama")

    assert isinstance(llm, FakeChatOllama)
    assert llm.kwargs["model"] == "configured-model"
    assert llm.kwargs["base_url"] == "http://ollama.invalid"


def test_get_llm_uses_gemini_for_an_unmatched_provider(
    monkeypatch: pytest.MonkeyPatch,
    fake_llm_clients: None,
) -> None:
    monkeypatch.setattr(factory_module.config, "model_provider", "unmatched-provider")
    monkeypatch.setattr(factory_module.config, "model_name", "fallback-model")
    monkeypatch.setattr(factory_module.config, "google_api_key", "fake-gemini-key")

    llm = factory_module.get_llm()

    assert isinstance(llm, FakeChatGoogleGenerativeAI)
    assert llm.kwargs["model"] == "fallback-model"
    assert llm.kwargs["google_api_key"] == "fake-gemini-key"
