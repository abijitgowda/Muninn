"""OpenAI-compatible provider — covers OpenAI, OpenRouter, Groq, Together, any compatible endpoint."""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Iterator
from typing import Any

import httpx

from .base import PROVIDER_DEFAULT, LLMError, LLMMetrics, LLMProvider, parse_json_object

log = logging.getLogger(__name__)


def _describe_http_error(e: httpx.HTTPError) -> str:
    """Status line plus the server's own error text (e.g. LM Studio's "Model unloaded")."""
    if isinstance(e, httpx.HTTPStatusError):
        body = e.response.text.strip()
        try:
            err = json.loads(body).get("error", body)
            body = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        except (ValueError, AttributeError):
            pass
        return f"{e.response.status_code} {e.response.reason_phrase}: {body[:300]}"
    return str(e)


# Waits (seconds) before retrying when the server is temporarily unable to answer:
# unreachable, a 5xx, or a local server whose model crashed or is reloading (LM Studio
# answers 400 "No models loaded" until its just-in-time loader brings the model back).
# ~2 minutes in total covers a reload of a large model.
TRANSIENT_RETRY_WAITS: tuple[float, ...] = (5, 15, 30, 60)
_MODEL_GONE_MARKERS = ("no models loaded", "model unloaded", "model is unloaded", "crashed", "is loading")


def _is_transient(e: httpx.HTTPError) -> bool:
    if isinstance(e, httpx.TimeoutException):
        return False  # a timeout already waited llm_timeout; retrying doubles that
    if isinstance(e, httpx.TransportError):
        return True  # connection refused / reset while the server restarts
    if isinstance(e, httpx.HTTPStatusError):
        if e.response.status_code >= 500:
            return True
        text = e.response.text.lower()
        return any(m in text for m in _MODEL_GONE_MARKERS)
    return False


class OpenAIProvider(LLMProvider):
    label = "OpenAI"  # used in error messages
    # Self-hosted OpenAI-compatible servers (LM Studio, oMLX, vLLM, llama.cpp)
    # accept chat_template_kwargs to toggle thinking; hosted APIs reject it.
    sends_template_kwargs = False

    def __init__(
        self,
        model: str = "gpt-4o",
        api_key: str | None = None,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 300.0,
        think: bool | None = None,
        sends_template_kwargs: bool | None = None,
    ):
        self.model = model
        self.think = think
        self._warned_unwanted_reasoning = False
        if sends_template_kwargs is not None:
            self.sends_template_kwargs = sends_template_kwargs
        self.last_metrics = LLMMetrics()
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._base_url = base_url.rstrip("/")
        self.host = self._base_url
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
        self,
        system,
        user,
        *,
        json_mode=False,
        json_schema=None,
        temperature=0.2,
        num_ctx=32768,
        retries=2,
        think=PROVIDER_DEFAULT,
    ) -> str:
        think = self._resolve_think(think)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            **self._token_limit(num_ctx),
        }
        if json_mode or json_schema:
            payload["response_format"] = self._json_response_format(json_schema)
        self._apply_thinking(payload, think)

        last_err = None
        attempt = 0
        transient_waits = iter(TRANSIENT_RETRY_WAITS if retries > 0 else ())
        while True:
            try:
                t0 = time.monotonic()
                r = self._client.post("/chat/completions", json=payload)
                r.raise_for_status()
                data = r.json()
                elapsed_ms = (time.monotonic() - t0) * 1000
                message = data["choices"][0]["message"]
                content = message["content"]
                if think is False and message.get("reasoning_content"):
                    self._warn_unwanted_reasoning()
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
                if _is_transient(e):
                    wait = next(transient_waits, None)
                    if wait is not None:
                        log.warning(
                            "%s unavailable (%s); retrying in %.0fs",
                            self.label,
                            _describe_http_error(e),
                            wait,
                        )
                        time.sleep(wait)
                        continue
                elif attempt < retries:
                    time.sleep(2**attempt)
                    attempt += 1
                    continue
                raise LLMError(f"{self.label} request failed: {_describe_http_error(last_err)}") from e

    def chat_stream(
        self, system, user, *, temperature=0.2, num_ctx=32768, think=PROVIDER_DEFAULT
    ) -> Iterator[str]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            **self._token_limit(num_ctx),
            "stream": True,
        }
        self._apply_thinking(payload, self._resolve_think(think))
        try:
            with self._client.stream("POST", "/chat/completions", json=payload) as r:
                if r.is_error:
                    r.read()
                    raise LLMError(f"{self.label} {r.status_code}: {r.text[:500]}")
                yield from self._iter_stream(r)
        except httpx.HTTPError as e:
            raise LLMError(f"{self.label} stream failed: {e}") from e

    @staticmethod
    def _iter_stream(r: httpx.Response) -> Iterator[str]:
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
        self,
        system,
        user,
        *,
        json_schema=None,
        temperature=0.0,
        num_ctx=32768,
        retries=2,
        think=PROVIDER_DEFAULT,
    ) -> dict[str, Any]:
        raw = ""
        last_err: Exception | None = None
        for attempt in range(2):  # regenerate once on invalid JSON
            raw = self.chat(
                system,
                user,
                json_mode=True,
                json_schema=json_schema,
                temperature=temperature if attempt == 0 else max(temperature, 0.3),
                num_ctx=num_ctx,
                retries=retries,
                think=think,
            )
            try:
                return parse_json_object(raw)
            except json.JSONDecodeError as e:
                last_err = e
        raise LLMError(f"Invalid JSON from {self.label}: {last_err}\n---\n{raw[:500]}")

    def embed(self, texts, *, model=None) -> list[list[float]]:
        embed_model = model or "text-embedding-3-small"
        r = self._client.post("/embeddings", json={"model": embed_model, "input": texts})
        r.raise_for_status()
        data = r.json().get("data", [])
        return [d["embedding"] for d in sorted(data, key=lambda d: d["index"])]

    def close(self):
        self._client.close()

    # ---- hooks for OpenAI-compatible servers that differ in the details ----

    def _apply_thinking(self, payload: dict[str, Any], think: bool | None) -> None:
        if think is not None and self.sends_template_kwargs:
            payload["chat_template_kwargs"] = {"enable_thinking": think}

    def _warn_unwanted_reasoning(self) -> None:
        if not self._warned_unwanted_reasoning:
            self._warned_unwanted_reasoning = True
            log.warning(
                "%s returned reasoning for %s although thinking is off — output tokens are being "
                "spent on discarded thinking. Check the model's thinking setting.",
                self.label,
                self.model,
            )

    def _json_response_format(self, json_schema: dict | None) -> dict[str, Any]:
        return {"type": "json_object"}

    def _token_limit(self, num_ctx: int) -> dict[str, Any]:
        return {"max_tokens": num_ctx}
