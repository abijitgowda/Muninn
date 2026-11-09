"""LM Studio provider — local models served by LM Studio's OpenAI-compatible API.

LM Studio speaks the OpenAI wire format at `<host>/v1`, with two differences
that matter here:

- `response_format: {"type": "json_object"}` is rejected; structured output
  goes through `json_schema` instead. When no schema is given we pass a
  permissive `{"type": "object"}` schema so the reply is still valid JSON.
- The context window is fixed when the model is loaded in LM Studio, not per
  request, so `num_ctx` is not sent. Load the model with a context length at
  least as large as `num_ctx_ingest` / `num_ctx_query`.
"""

from __future__ import annotations

from typing import Any

from .openai_provider import OpenAIProvider


class LMStudioProvider(OpenAIProvider):
    label = "LM Studio"

    def __init__(
        self,
        host: str = "http://localhost:1234",
        model: str = "gemma4:e4b",
        timeout: float = 300.0,
        api_key: str | None = None,
    ):
        host = host.rstrip("/")
        base_url = host if host.endswith("/v1") else f"{host}/v1"
        # LM Studio ignores the key unless auth is enabled in its server settings.
        super().__init__(model=model, api_key=api_key or "lm-studio", base_url=base_url, timeout=timeout)
        self.host = host

    def _json_response_format(self, json_schema: dict | None) -> dict[str, Any]:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "response",
                "schema": json_schema or {"type": "object"},
            },
        }

    def _token_limit(self, num_ctx: int) -> dict[str, Any]:
        # num_ctx is a context-window size, not an output cap; let LM Studio decide.
        return {}
