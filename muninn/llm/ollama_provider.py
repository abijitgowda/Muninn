"""Ollama provider — wraps the existing Ollama client."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ..ollama import Ollama, OllamaError
from .base import LLMError, LLMMetrics, LLMProvider


class OllamaProvider(LLMProvider):
    def __init__(
        self, host: str = "http://localhost:11434", model: str = "gemma4:e4b", timeout: float = 300.0
    ):
        self.model = model
        self.host = host
        self.last_metrics = LLMMetrics()
        self._client = Ollama(host=host, model=model, timeout=timeout)

    def ping(self) -> bool:
        return self._client.ping()

    def chat(
        self, system, user, *, json_mode=False, json_schema=None, temperature=0.2, num_ctx=32768, retries=2
    ) -> str:
        try:
            result = self._client.chat(
                system,
                user,
                json_mode=json_mode,
                json_schema=json_schema,
                temperature=temperature,
                num_ctx=num_ctx,
                retries=retries,
            )
            self._sync_metrics()
            return result
        except OllamaError as e:
            raise LLMError(str(e)) from e

    def chat_stream(self, system, user, *, temperature=0.2, num_ctx=32768) -> Iterator[str]:
        yield from self._client.chat_stream(system, user, temperature=temperature, num_ctx=num_ctx)

    def chat_json(
        self, system, user, *, json_schema=None, temperature=0.0, num_ctx=32768, retries=2
    ) -> dict[str, Any]:
        try:
            result = self._client.chat_json(
                system,
                user,
                json_schema=json_schema,
                temperature=temperature,
                num_ctx=num_ctx,
                retries=retries,
            )
            self._sync_metrics()
            return result
        except OllamaError as e:
            raise LLMError(str(e)) from e

    def embed(self, texts, *, model=None) -> list[list[float]]:
        return self._client.embed(texts, model=model)

    def close(self):
        self._client.close()

    def _sync_metrics(self):
        self.last_metrics = self._client.last_metrics
