"""Construct only real zzcode provider clients from explicit evaluation config."""

from __future__ import annotations

import os

from ...models import (
    AnthropicCompatibleModelClient,
    OllamaModelClient,
    OpenAICompatibleModelClient,
)
from .models import AgentRunConfig


DEFAULT_OPENAI_BASE_URL = "https://www.right.codes/codex/v1"
DEFAULT_ANTHROPIC_BASE_URL = "https://www.right.codes/claude/v1"
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"


def required_provider_environment(config: AgentRunConfig) -> tuple[str, ...]:
    if config.provider == "openai":
        return ("OPENAI_API_KEY",)
    if config.provider == "anthropic":
        return ("ANTHROPIC_API_KEY", "RIGHT_CODES_API_KEY", "OPENAI_API_KEY")
    return ()


def provider_credentials_available(config: AgentRunConfig) -> bool:
    names = required_provider_environment(config)
    return not names or any(os.environ.get(name) for name in names)


def build_real_model_client(config: AgentRunConfig):
    timeout = float(config.provider_timeout_seconds)
    if config.provider == "openai":
        return OpenAICompatibleModelClient(
            model=config.model,
            base_url=config.base_url or DEFAULT_OPENAI_BASE_URL,
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            temperature=float(config.temperature),
            timeout=timeout,
        )
    if config.provider == "anthropic":
        api_key = next(
            (
                os.environ[name]
                for name in required_provider_environment(config)
                if os.environ.get(name)
            ),
            "",
        )
        return AnthropicCompatibleModelClient(
            model=config.model,
            base_url=config.base_url or DEFAULT_ANTHROPIC_BASE_URL,
            api_key=api_key,
            temperature=float(config.temperature),
            timeout=timeout,
        )
    return OllamaModelClient(
        model=config.model,
        host=config.host or DEFAULT_OLLAMA_HOST,
        temperature=float(config.temperature),
        top_p=float(config.top_p),
        timeout=timeout,
    )
