"""Ollama client — thin httpx wrapper around /api/chat with JSON-mode + streaming.

Part of the Muninn local wiki system.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx


class OllamaError(Exception):
    pass


@dataclass
class LLMMetrics:
    """Performance metrics from the last Ollama call."""

    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_duration_ms: float = 0
    prompt_eval_rate: float = 0  # tokens/sec for prompt processing
    eval_rate: float = 0  # tokens/sec for generation

    def __str__(self) -> str:
        if not self.eval_rate:
            return ""
        return (
            f"{self.eval_rate:.1f} tok/s ({self.completion_tokens} tokens in {self.total_duration_ms:.0f}ms)"
        )


class Ollama:
    def __init__(
        self,
        host: str = "http://localhost:11434",
        model: str = "qwen2.5:14b",
        timeout: float = 300.0,
    ) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._client = httpx.Client(timeout=timeout, base_url=self.host)
        self.last_metrics = LLMMetrics()

    def __enter__(self) -> Ollama:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # ---- public API ----

    def ping(self) -> bool:
        try:
            r = self._client.get("/api/tags")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    def chat(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        json_schema: dict | None = None,
        temperature: float = 0.2,
        num_ctx: int = 32768,
        retries: int = 2,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_ctx": num_ctx,
            },
        }
        if json_schema:
            payload["format"] = json_schema
        elif json_mode:
            payload["format"] = "json"

        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                r = self._client.post("/api/chat", json=payload)
                r.raise_for_status()
                data = r.json()
                self._capture_metrics(data)
                return (data.get("message", {}) or {}).get("content", "")
            except httpx.HTTPError as e:
                last_err = e
                if attempt < retries:
                    time.sleep(2**attempt)
                    continue
                raise OllamaError(f"Ollama request failed: {e}") from e
        raise OllamaError(f"Ollama request failed after retries: {last_err}")

    def chat_stream(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.2,
        num_ctx: int = 32768,
    ) -> Iterator[str]:
        """Yield content chunks as they arrive from Ollama's NDJSON stream."""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": True,
            "options": {"temperature": temperature, "num_ctx": num_ctx},
        }
        with self._client.stream("POST", "/api/chat", json=payload) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                chunk = (obj.get("message") or {}).get("content") or ""
                if chunk:
                    yield chunk
                if obj.get("done"):
                    break

    def _capture_metrics(self, data: dict[str, Any]) -> None:
        """Extract performance metrics from Ollama response."""
        total_ns = data.get("total_duration", 0)
        prompt_count = data.get("prompt_eval_count", 0)
        eval_count = data.get("eval_count", 0)
        prompt_ns = data.get("prompt_eval_duration", 0)
        eval_ns = data.get("eval_duration", 0)
        self.last_metrics = LLMMetrics(
            model=data.get("model", self.model),
            prompt_tokens=prompt_count,
            completion_tokens=eval_count,
            total_duration_ms=total_ns / 1_000_000 if total_ns else 0,
            prompt_eval_rate=(prompt_count / (prompt_ns / 1e9)) if prompt_ns else 0,
            eval_rate=(eval_count / (eval_ns / 1e9)) if eval_ns else 0,
        )

    def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        """Get embeddings via Ollama's OpenAI-compat endpoint."""
        embed_model = model or "mxbai-embed-large"
        r = self._client.post(
            "/v1/embeddings",
            json={"model": embed_model, "input": texts},
        )
        r.raise_for_status()
        data = r.json().get("data") or []
        return [d["embedding"] for d in sorted(data, key=lambda d: d["index"])]

    def chat_json(
        self,
        system: str,
        user: str,
        *,
        json_schema: dict | None = None,
        temperature: float = 0.0,
        num_ctx: int = 32768,
        retries: int = 2,
    ) -> dict[str, Any]:
        """Convenience: chat with format=json, parse result."""
        raw = self.chat(
            system,
            user,
            json_mode=json_schema is None,
            json_schema=json_schema,
            temperature=temperature,
            num_ctx=num_ctx,
            retries=retries,
        )
        # Strip ```json fences if a model adds them anyway
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.startswith("json\n"):
                cleaned = cleaned[5:]
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise OllamaError(f"Ollama returned invalid JSON: {e}\n---\n{raw[:1000]}") from e
