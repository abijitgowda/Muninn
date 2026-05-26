"""Base protocol for LLM providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any


class LLMError(Exception):
    pass


@dataclass
class LLMMetrics:
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_duration_ms: float = 0
    eval_rate: float = 0

    def __str__(self) -> str:
        if not self.eval_rate:
            return ""
        return (
            f"{self.eval_rate:.1f} tok/s ({self.completion_tokens} tokens in {self.total_duration_ms:.0f}ms)"
        )


class LLMProvider(ABC):
    """Common interface for all LLM backends."""

    model: str
    last_metrics: LLMMetrics

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
    ) -> str: ...

    @abstractmethod
    def chat_stream(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.2,
        num_ctx: int = 32768,
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
    ) -> dict[str, Any]: ...

    @abstractmethod
    def embed(self, texts: list[str], *, model: str | None = None) -> list[list[float]]: ...

    def close(self) -> None:  # noqa: B027
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
