"""
LLM 工厂 — 返回通过 OpenAI 兼容接口（ChatGPT 协议）访问的 LangChain 聊天模型。

通过 OPENAI_BASE_URL 可接入 OpenAI 及任何兼容 ChatGPT 协议的服务商
（DeepSeek、Moonshot Kimi、阿里云百炼兼容模式、vLLM、Ollama、one-api 中转等）。
"""
from __future__ import annotations

import logging

from langchain_core.language_models.chat_models import BaseChatModel

from .config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL

logger = logging.getLogger(__name__)


def get_llm(temperature: float = 0.0) -> BaseChatModel:
    """返回一个配置好的 LangChain 聊天模型（OpenAI 兼容接口）。"""
    try:
        from langchain_openai import ChatOpenAI  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "请先安装 langchain-openai: pip install -e ."
        ) from exc

    if not OPENAI_API_KEY:
        raise ValueError(
            "需要设置 OPENAI_API_KEY；"
            "如使用非 OpenAI 官方接口，请同时设置 OPENAI_BASE_URL 与 OPENAI_MODEL。"
        )
    logger.info(
        "使用 OpenAI 兼容接口: base_url=%s, model=%s",
        OPENAI_BASE_URL or "https://api.openai.com/v1（官方）",
        OPENAI_MODEL,
    )
    return ChatOpenAI(
        model=OPENAI_MODEL,
        api_key=OPENAI_API_KEY,
        **({"base_url": OPENAI_BASE_URL} if OPENAI_BASE_URL else {}),
        temperature=temperature,
    )
