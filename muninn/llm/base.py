"""Base protocol for LLM providers."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any


class LLMError(Exception):
    pass


# Per-call `think=` default: use the provider's configured setting.
PROVIDER_DEFAULT: Any = object()

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def parse_json_object(raw: str) -> dict[str, Any]:
    """Parse a model reply that should be one JSON object.

    Tolerates what local servers commonly wrap around it: a leading
    <think>…</think> block (thinking models whose reasoning isn't split out),
    ```json fences, and stray prose before/after the object.
    Raises json.JSONDecodeError if no object can be recovered.
    """
    text = _THINK_BLOCK_RE.sub("", raw).strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json\n"):
            text = text[5:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(text[start : end + 1])


@dataclass
class LLMMetrics:
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_duration_ms: float = 0
    prompt_eval_rate: float = 0  # tokens/sec for prompt processing (Ollama only)
    eval_rate: float = 0  # tokens/sec for generation

    def __str__(self) -> str:
        if not self.eval_rate:
            return ""
        return (
            f"{self.eval_rate:.1f} tok/s ({self.completion_tokens} tokens in {self.total_duration_ms:.0f}ms)"
        )


class LLMProvider(ABC):
    """Common interface for all LLM backends."""

    model: str
    host: str = ""  # endpoint this provider talks to, for error messages
    last_metrics: LLMMetrics
    # Thinking-mode control for hybrid reasoning models (Qwen3.5, etc.):
    # None = model default, False = disable, True = enable. Calls can override
    # it with think=...; omitting it (PROVIDER_DEFAULT) uses this value.
    think: bool | None = None

    def _resolve_think(self, think: Any) -> bool | None:
        return self.think if think is PROVIDER_DEFAULT else think

    @abstractmethod
    def ping(self) -> bool: ...

    @abstractmethod
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
        think: Any = PROVIDER_DEFAULT,
    ) -> str: ...

    @abstractmethod
    def chat_stream(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.2,
        num_ctx: int = 32768,
        think: Any = PROVIDER_DEFAULT,
    ) -> Iterator[str]: ...

    @abstractmethod
    def chat_json(
        self,
        system: str,
        user: str,
        *,
        json_schema: dict | None = None,
        temperature: float = 0.0,
        num_ctx: int = 32768,
        retries: int = 2,
        think: Any = PROVIDER_DEFAULT,
    ) -> dict[str, Any]: ...

    @abstractmethod
    def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]: ...

    def close(self) -> None:  # noqa: B027
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
