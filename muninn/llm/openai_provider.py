"""OpenAI-compatible provider — covers OpenAI, OpenRouter, Groq, Together, any compatible endpoint."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from typing import Any

import httpx

from .base import LLMError, LLMMetrics, LLMProvider


class OpenAIProvider(LLMProvider):
    def __init__(
        self,
        model: str = "gpt-4o",
        api_key: str | None = None,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 300.0,
    ):
        self.model = model
        self.last_metrics = LLMMetrics()
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            timeout=timeout,
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
        )

    def ping(self) -> bool:
        try:
            r = self._client.get("/models")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    def chat(
        self, system, user, *, json_mode=False, json_schema=None, temperature=0.2, num_ctx=32768, retries=2
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": num_ctx,
        }
        if json_mode or json_schema:
            payload["response_format"] = {"type": "json_object"}

        last_err = None
        for attempt in range(retries + 1):
            try:
                t0 = time.monotonic()
                r = self._client.post("/chat/completions", json=payload)
                r.raise_for_status()
                data = r.json()
                elapsed_ms = (time.monotonic() - t0) * 1000
                content = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                self.last_metrics = LLMMetrics(
                    model=data.get("model", self.model),
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    total_duration_ms=elapsed_ms,
                    eval_rate=usage.get("completion_tokens", 0) / (elapsed_ms / 1000)
                    if elapsed_ms > 0
                    else 0,
                )
                return content
            except httpx.HTTPError as e:
                last_err = e
                if attempt < retries:
                    time.sleep(2**attempt)
                    continue
                raise LLMError(f"OpenAI request failed: {e}") from e
        raise LLMError(f"OpenAI request failed after retries: {last_err}")

    def chat_stream(self, system, user, *, temperature=0.2, num_ctx=32768) -> Iterator[str]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": num_ctx,
            "stream": True,
        }
        with self._client.stream("POST", "/chat/completions", json=payload) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    obj = json.loads(data_str)
                    delta = obj["choices"][0].get("delta", {})
                    chunk = delta.get("content", "")
                    if chunk:
                        yield chunk
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue

    def chat_json(
        self, system, user, *, json_schema=None, temperature=0.0, num_ctx=32768, retries=2
    ) -> dict[str, Any]:
        raw = self.chat(
            system, user, json_mode=True, temperature=temperature, num_ctx=num_ctx, retries=retries
        )
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.startswith("json\n"):
                cleaned = cleaned[5:]
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise LLMError(f"Invalid JSON from OpenAI: {e}\n---\n{raw[:500]}") from e

    def embed(self, texts, *, model=None) -> list[list[float]]:
        embed_model = model or "text-embedding-3-small"
        r = self._client.post("/embeddings", json={"model": embed_model, "input": texts})
        r.raise_for_status()
        data = r.json().get("data", [])
        return [d["embedding"] for d in sorted(data, key=lambda d: d["index"])]

    def close(self):
        self._client.close()
