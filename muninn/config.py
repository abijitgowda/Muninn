"""Load wiki.yaml (single config file) + .env (secrets only) into typed pydantic models.

Source connectors are referenced by a dotted-path `tool:` field — the loader
resolves them at runtime so adding a new source is just a new file + YAML entry.
"""

from __future__ import annotations

import os
from importlib import import_module
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VAULT = PROJECT_ROOT / "Muninn-Vault"
DEFAULT_CONFIG = PROJECT_ROOT / "wiki.yaml"
DEFAULT_ENV = PROJECT_ROOT / ".env"


class SourceConfig(BaseModel):
    """One entry in wiki.yaml sources list."""

    name: str
    type: str
    enabled: bool = True
    url: str = ""
    secret_env: str | None = None
    tool: str  # dotted path: module.path:ClassName
    options: dict[str, Any] = Field(default_factory=dict)

    @property
    def secret(self) -> str | None:
        """Resolve the secret from .env / environment."""
        if not self.secret_env:
            return None
        val = os.environ.get(self.secret_env)
        if val:
            return val
        raise RuntimeError(f"Source {self.name!r} requires {self.secret_env} — add it to .env")

    def load_tool(self) -> Any:
        """Import the connector class referenced by `tool:`.

        Restricted to the muninn.sources namespace to prevent
        arbitrary code execution via wiki.yaml.
        """
        if ":" not in self.tool:
            raise ValueError(f"tool must be 'module:Class', got {self.tool!r}")
        module_path, class_name = self.tool.split(":", 1)
        if not module_path.startswith("muninn.sources."):
            raise ValueError(f"tool module must be under 'muninn.sources', got {module_path!r}")
        module = import_module(module_path)
        return getattr(module, class_name)


class Settings(BaseModel):
    """Global settings from wiki.yaml `settings:` block.

    Model selection follows a fallback chain:
      ingest pipeline →  ollama_model_ingest  →  ollama_model
      query / serve   →  ollama_model_query   →  ollama_model
    """

    vault_path: Path
    # LLM provider: ollama (default), openai, anthropic, openrouter, custom
    llm_provider: str = "ollama"
    llm_provider_ingest: str | None = None
    llm_provider_query: str | None = None
    llm_base_url: str | None = None
    # Model names — provider-agnostic
    llm_model: str = "gemma4:e4b"
    llm_model_ingest: str | None = None
    llm_model_query: str | None = None
    llm_timeout: float = 300.0
    # Ollama-specific
    ollama_host: str = "http://localhost:11434"
    # Context window and body limits — tune for your hardware/model
    num_ctx_ingest: int = 65536  # context window for ingest LLM calls
    num_ctx_query: int = 32768  # context window for query LLM calls
    max_body_ingest: int = 24000  # max chars of raw source body sent to LLM
    max_body_query: int = 16000  # max chars per page body sent to query LLM
    retrieval_mode: str = "adaptive"  # "hybrid" | "reranked" | "adaptive"
    max_retrieval_pages: int = 5  # max page bodies sent to LLM per query
    two_pass_ingest: bool = True  # chain-of-thought: analysis pass then extraction pass
    # Memory model
    consolidation_enabled: bool = True
    decay_rate: float = 0.95  # per-week strength multiplier (halves in ~14 weeks)
    abstraction_threshold: int = 5  # sources count to trigger synthesis
    archive_threshold: float = 0.1  # strength below this → lifecycle: stale
    log_dir: Path = Path.home() / "Library" / "Logs" / "Muninn"
    log_level: str = "WARNING"

    @property
    def model_for_ingest(self) -> str:
        return self.llm_model_ingest or self.llm_model

    @property
    def model_for_query(self) -> str:
        return self.llm_model_query or self.llm_model

    @property
    def provider_for_ingest(self) -> str:
        return self.llm_provider_ingest or self.llm_provider

    @property
    def provider_for_query(self) -> str:
        return self.llm_provider_query or self.llm_provider

    def create_llm(self, operation: str = "query") -> "LLMProvider":
        """Create an LLM provider for the given operation (ingest or query)."""
        from muninn.llm import create_provider

        if operation == "ingest":
            provider, model = self.provider_for_ingest, self.model_for_ingest
        else:
            provider, model = self.provider_for_query, self.model_for_query
        return create_provider(
            provider=provider,
            model=model,
            host=self.ollama_host,
            timeout=self.llm_timeout,
            base_url=self.llm_base_url,
        )

    @property
    def state_dir(self) -> Path:
        return self.vault_path / ".muninn"

    @property
    def manifest_path(self) -> Path:
        return self.state_dir / "manifest.db"

    @property
    def skills_dir(self) -> Path:
        return self.vault_path / ".agents" / "skills"

    @property
    def raw_root(self) -> Path:
        return self.vault_path / "Raw" / "Sources"


class Config(BaseModel):
    settings: Settings
    sources: list[SourceConfig]

    def source(self, name: str) -> SourceConfig:
        for s in self.sources:
            if s.name == name:
                return s
        raise KeyError(f"No source named {name!r} in config")

    def enabled(self) -> list[SourceConfig]:
        return [s for s in self.sources if s.enabled]


def load_config(
    config_path: Path | None = None,
    env_path: Path | None = None,
    vault_path: Path | None = None,
) -> Config:
    """Load .env (secrets only) then wiki.yaml (everything else), return a Config."""
    env_path = env_path or DEFAULT_ENV
    if env_path.exists():
        load_dotenv(env_path, override=False)

    config_path = config_path or DEFAULT_CONFIG
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config not found at {config_path}. Run `muninn init` to create it from wiki.example.yaml."
        )

    raw = yaml.safe_load(config_path.read_text())
    if not isinstance(raw, dict) or "sources" not in raw:
        raise ValueError(f"{config_path} must be a YAML mapping with a 'sources' list")

    # Settings from the yaml, with env-var overrides for backwards compat.
    raw_settings = raw.get("settings") or {}
    vault = vault_path or Path(raw_settings.get("vault_path", str(DEFAULT_VAULT))).expanduser()
    if not vault.is_absolute():
        vault = (PROJECT_ROOT / vault).resolve()

    settings = Settings(
        vault_path=vault,
        llm_provider=raw_settings.get("llm_provider", "ollama"),
        llm_provider_ingest=raw_settings.get("llm_provider_ingest"),
        llm_provider_query=raw_settings.get("llm_provider_query"),
        llm_base_url=raw_settings.get("llm_base_url"),
        llm_model=raw_settings.get("llm_model") or raw_settings.get("ollama_model", "gemma4:e4b"),
        llm_model_ingest=raw_settings.get("llm_model_ingest") or raw_settings.get("ollama_model_ingest"),
        llm_model_query=raw_settings.get("llm_model_query") or raw_settings.get("ollama_model_query"),
        llm_timeout=float(raw_settings.get("llm_timeout") or raw_settings.get("ollama_timeout", 300)),
        ollama_host=raw_settings.get("ollama_host", "http://localhost:11434"),
        num_ctx_ingest=int(raw_settings.get("num_ctx_ingest", 65536)),
        num_ctx_query=int(raw_settings.get("num_ctx_query", 32768)),
        max_body_ingest=int(raw_settings.get("max_body_ingest", 24000)),
        max_body_query=int(raw_settings.get("max_body_query", 16000)),
        retrieval_mode=raw_settings.get("retrieval_mode", "keyword"),
        max_retrieval_pages=int(raw_settings.get("max_retrieval_pages", 5)),
        two_pass_ingest=raw_settings.get("two_pass_ingest", True),
        consolidation_enabled=raw_settings.get("consolidation_enabled", True),
        decay_rate=float(raw_settings.get("decay_rate", 0.95)),
        abstraction_threshold=int(raw_settings.get("abstraction_threshold", 5)),
        archive_threshold=float(raw_settings.get("archive_threshold", 0.1)),
        log_level=raw_settings.get("log_level", "WARNING"),
    )

    sources = [SourceConfig(**entry) for entry in raw["sources"]]
    return Config(settings=settings, sources=sources)
