"""LLM Provider 工厂。

该模块集中负责创建 LangChain ChatModel 实例。Provider 行为集中在这里，
业务 Agent 只依赖 BaseChatModel 接口。
"""

import logging
from typing import Any, Mapping, Optional

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from langchain_core.language_models import BaseChatModel

from src.config import config

logger = logging.getLogger(__name__)


def get_llm(
    temperature: float = 0.7,
    model_override: Optional[str] = None,
    provider_override: Optional[str] = None,
    extra_body: Optional[Mapping[str, Any]] = None,
) -> BaseChatModel:
    """根据配置创建 LLM 实例。

    Args:
        temperature: LLM 温度参数。
        model_override: 可选模型名称，用于覆盖 config.model_name。
        provider_override: 可选提供商，用于覆盖 config.model_provider。

    Returns:
        ChatOllama、ChatOpenAI 或 ChatGoogleGenerativeAI 实例。
    """

    model_name = model_override or config.model_name
    provider = provider_override or config.model_provider
    chat_openai_extra = {"extra_body": extra_body} if extra_body is not None else {}

    if provider == "ollama":
        logger.info(f"使用 Ollama 模型：{model_name}")
        return ChatOllama(
            model=model_name,
            base_url=config.ollama_base_url,
            temperature=temperature,
            num_ctx=8192,
        )

    if provider == "openai":
        logger.info(f"使用 OpenAI 模型：{model_name}")
        return ChatOpenAI(
            model=model_name,
            base_url=f"{config.openai_base_url}/v1",
            api_key=config.openai_api_key,
            temperature=temperature,
            **chat_openai_extra,
        )

    if provider == "deepseek":
        logger.info(f"使用 DeepSeek 模型：{model_name}")
        return ChatOpenAI(
            model=model_name,
            base_url=config.deepseek_base_url,
            api_key=config.deepseek_api_key,
            temperature=temperature,
            **chat_openai_extra,
        )

    if provider == "llamacpp":
        logger.info(f"使用 llama.cpp 服务器模型：{model_name}")
        return ChatOpenAI(
            model=model_name,
            base_url=f"{config.llamacpp_base_url}/v1",
            api_key="not-needed",
            temperature=temperature,
            **chat_openai_extra,
        )

    # 保持原逻辑：未匹配到其他 Provider 时使用 Gemini。
    logger.info(f"使用 Gemini 模型：{model_name}")
    return ChatGoogleGenerativeAI(
        model=model_name,
        google_api_key=config.google_api_key,
        temperature=temperature,
    )
