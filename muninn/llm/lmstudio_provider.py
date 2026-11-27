"""LM Studio provider — local models served by LM Studio's OpenAI-compatible API.

LM Studio speaks the OpenAI wire format at `<host>/v1`, with two differences
that matter here:

- `response_format: {"type": "json_object"}` is rejected; structured output
  goes through `json_schema` instead. When no schema is given we pass a
  permissive `{"type": "object"}` schema so the reply is still valid JSON.
- LM Studio sets thinking when a model is loaded and ignores per-request
  switches (`chat_template_kwargs`, `reasoning_effort`) for Qwen3.5. To turn
  thinking off per request we pre-fill the assistant turn with an empty
  `<think></think>` block — exactly what Qwen's own template emits when
  enable_thinking is false — so one loaded model can think for queries and
  skip it for ingest. Only applied to models that use <think> tags.
- The context window is fixed when the model is loaded in LM Studio, not per
  request, so `num_ctx` is not sent. Load the model with a context length at
  least as large as `num_ctx_ingest` / `num_ctx_query`.

Long batched runs can crash LM Studio's MLX engine (seen with Qwen3.5-122B at
--parallel 4: "[metal::malloc] Resource limit (499000) exceeded" or a GPU
out-of-memory after ~75-100 requests). `reload_model()` unloads and reloads the
model through LM Studio's REST API, which resets the engine; the ingest pipeline
calls it every `lmstudio_reload_every` extractions.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from .base import LLMError
from .openai_provider import OpenAIProvider, _describe_http_error

log = logging.getLogger(__name__)

# Model families whose chat template wraps reasoning in <think>…</think>.
_THINK_TAG_FAMILIES = ("qwen3", "qwq", "deepseek-r1")
EMPTY_THINK_PREFILL = "<think>\n\n</think>\n\n"


class LMStudioProvider(OpenAIProvider):
    label = "LM Studio"
    sends_template_kwargs = True

    def __init__(
        self,
        host: str = "http://localhost:1234",
        model: str = "gemma4:e4b",
        timeout: float = 300.0,
        api_key: str | None = None,
        think: bool | None = None,
    ):
        host = host.rstrip("/")
        base_url = host if host.endswith("/v1") else f"{host}/v1"
        # LM Studio ignores the key unless auth is enabled in its server settings.
        super().__init__(
            model=model, api_key=api_key or "lm-studio", base_url=base_url, timeout=timeout, think=think
        )
        self.host = host
        self._root = host[: -len("/v1")] if host.endswith("/v1") else host

    def reload_model(self, context_length: int | None = None) -> float:
        """Unload every loaded instance of this model, then load it again.

        Returns seconds taken. Other models (e.g. the embedding model) are untouched.
        Callers must make sure no requests are in flight.
        """
        t0 = time.monotonic()
        api = f"{self._root}/api/v1/models"
        try:
            with httpx.Client(timeout=900.0, headers=self._client.headers) as c:
                listing = c.get(api)
                listing.raise_for_status()
                for m in listing.json().get("models", []):
                    instances = m.get("loaded_instances") or []
                    if m.get("key") == self.model or any(i.get("id") == self.model for i in instances):
                        for inst in instances:
                            c.post(f"{api}/unload", json={"instance_id": inst["id"]}).raise_for_status()
                body: dict[str, Any] = {"model": self.model}
                if context_length:
                    body["context_length"] = context_length
                c.post(f"{api}/load", json=body).raise_for_status()
        except httpx.HTTPError as e:
            raise LLMError(f"{self.label} reload of {self.model} failed: {_describe_http_error(e)}") from e
        elapsed = time.monotonic() - t0
        log.info("reloaded %s in %.0fs", self.model, elapsed)
        return elapsed

    def _json_response_format(self, json_schema: dict | None) -> dict[str, Any]:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "response",
                "schema": json_schema or {"type": "object"},
            },
        }

    def _apply_thinking(self, payload: dict[str, Any], think: bool | None) -> None:
        super()._apply_thinking(payload, think)
        if think is False and any(f in self.model.lower() for f in _THINK_TAG_FAMILIES):
            payload["messages"].append({"role": "assistant", "content": EMPTY_THINK_PREFILL})

    def _token_limit(self, num_ctx: int) -> dict[str, Any]:
        # num_ctx is a context-window size, not an output cap; let LM Studio decide.
        return {}
