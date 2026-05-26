"""Anthropic provider — Claude models via the Anthropic API."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from typing import Any

import httpx

from .base import LLMError, LLMMetrics, LLMProvider


class AnthropicProvider(LLMProvider):
    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        api_key: str | None = None,
        timeout: float = 300.0,
    ):
        self.model = model
        self.last_metrics = LLMMetrics()
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._client = httpx.Client(
            timeout=timeout,
            base_url="https://api.anthropic.com",
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )

    def ping(self) -> bool:
        return bool(self._api_key)

    def chat(
        self, system, user, *, json_mode=False, json_schema=None, temperature=0.2, num_ctx=32768, retries=2
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": min(num_ctx, 8192),
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "temperature": temperature,
        }

        last_err = None
        for attempt in range(retries + 1):
            try:
                t0 = time.monotonic()
                r = self._client.post("/v1/messages", json=payload)
                r.raise_for_status()
                data = r.json()
                elapsed_ms = (time.monotonic() - t0) * 1000
                content = ""
                for block in data.get("content", []):
                    if block.get("type") == "text":
                        content += block.get("text", "")
                usage = data.get("usage", {})
                self.last_metrics = LLMMetrics(
                    model=data.get("model", self.model),
                    prompt_tokens=usage.get("input_tokens", 0),
                    completion_tokens=usage.get("output_tokens", 0),
                    total_duration_ms=elapsed_ms,
                    eval_rate=usage.get("output_tokens", 0) / (elapsed_ms / 1000) if elapsed_ms > 0 else 0,
                )
                return content
            except httpx.HTTPError as e:
                last_err = e
                if attempt < retries:
                    time.sleep(2**attempt)
                    continue
                raise LLMError(f"Anthropic request failed: {e}") from e
        raise LLMError(f"Anthropic request failed after retries: {last_err}")

    def chat_stream(self, system, user, *, temperature=0.2, num_ctx=32768) -> Iterator[str]:
        payload = {
            "model": self.model,
            "max_tokens": min(num_ctx, 8192),
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "temperature": temperature,
            "stream": True,
        }
        with self._client.stream("POST", "/v1/messages", json=payload) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                try:
                    obj = json.loads(data_str)
                    if obj.get("type") == "content_block_delta":
                        chunk = obj.get("delta", {}).get("text", "")
                        if chunk:
                            yield chunk
                    elif obj.get("type") == "message_stop":
                        break
                except json.JSONDecodeError:
                    continue

    def chat_json(
        self, system, user, *, json_schema=None, temperature=0.0, num_ctx=32768, retries=2
    ) -> dict[str, Any]:
        json_system = system + "\n\nRespond with valid JSON only. No prose, no markdown fences."
        raw = self.chat(json_system, user, temperature=temperature, num_ctx=num_ctx, retries=retries)
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.startswith("json\n"):
                cleaned = cleaned[5:]
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise LLMError(f"Invalid JSON from Anthropic: {e}\n---\n{raw[:500]}") from e

    def embed(self, texts, *, model=None) -> list[list[float]]:
        raise LLMError(
            "Anthropic does not provide an embedding API. Use Ollama for embeddings (configured separately)."
        )

    def close(self):
        self._client.close()
