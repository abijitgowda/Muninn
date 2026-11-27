"""Factory — create the right LLM provider from config settings."""

from __future__ import annotations

import os

from .base import LLMError, LLMProvider


def create_provider(
    provider: str = "ollama",
    model: str = "gemma4:e4b",
    host: str = "http://localhost:11434",
    timeout: float = 300.0,
    api_key: str | None = None,
    base_url: str | None = None,
    think: bool | None = None,
    keep_alive: str | int | None = None,
) -> LLMProvider:
    """Create an LLM provider by name.

    Providers:
        ollama      — local Ollama (default), uses `host` (ollama_host)
        lmstudio    — local LM Studio, uses `host` (lmstudio_host)
        openai      — OpenAI API
        anthropic   — Anthropic Claude API
        openrouter  — OpenRouter (OpenAI-compatible)
        custom      — any OpenAI-compatible endpoint (set base_url), e.g. oMLX

    `think` toggles thinking on hybrid reasoning models (None = model default).
    `keep_alive` is Ollama-only: how long the model stays loaded after a call.
    """
    p = provider.lower()

    if p == "ollama":
        from .ollama_provider import OllamaProvider

        return OllamaProvider(host=host, model=model, timeout=timeout, think=think, keep_alive=keep_alive)

    if p == "lmstudio":
        from .lmstudio_provider import LMStudioProvider

        return LMStudioProvider(
            host=host,
            model=model,
            timeout=timeout,
            api_key=api_key or os.environ.get("LMSTUDIO_API_KEY"),
            think=think,
        )

    if p == "openai":
        from .openai_provider import OpenAIProvider

        return OpenAIProvider(
            model=model,
            api_key=api_key or os.environ.get("OPENAI_API_KEY"),
            base_url=base_url or "https://api.openai.com/v1",
            timeout=timeout,
        )

    if p == "anthropic":
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            model=model,
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"),
            timeout=timeout,
            think=think,
        )

    if p == "openrouter":
        from .openai_provider import OpenAIProvider

        return OpenAIProvider(
            model=model,
            api_key=api_key or os.environ.get("OPENROUTER_API_KEY"),
            base_url=base_url or "https://openrouter.ai/api/v1",
            timeout=timeout,
        )

    if p == "custom":
        if not base_url:
            raise LLMError("custom provider requires base_url")
        from .openai_provider import OpenAIProvider

        return OpenAIProvider(
            model=model,
            api_key=api_key or os.environ.get("LLM_API_KEY", ""),
            base_url=base_url,
            timeout=timeout,
            think=think,
            sends_template_kwargs=True,
        )

    raise LLMError(
        f"Unknown provider: {provider!r}. Use: ollama, lmstudio, openai, anthropic, openrouter, custom"
    )
