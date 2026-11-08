"""Provider selection: each local provider talks to its own host."""

from __future__ import annotations

import pytest
import yaml

from muninn.config import load_config
from muninn.llm import create_provider
from muninn.llm.lmstudio_provider import LMStudioProvider
from muninn.llm.ollama_provider import OllamaProvider


def _load(tmp_path, settings: dict):
    cfg_yaml = tmp_path / "wiki.yaml"
    cfg_yaml.write_text(yaml.safe_dump({"settings": settings, "sources": []}))
    (tmp_path / "vault").mkdir(exist_ok=True)
    return load_config(config_path=cfg_yaml, vault_path=tmp_path / "vault").settings


HOSTS = {"ollama_host": "http://ollama-box:11434", "lmstudio_host": "http://lms-box:1234"}


def test_ollama_provider_uses_ollama_host(tmp_path):
    s = _load(tmp_path, {"llm_provider": "ollama", **HOSTS})
    with s.create_llm("query") as llm:
        assert isinstance(llm, OllamaProvider)
        assert llm.host == "http://ollama-box:11434"
    assert s.embed_host == "http://ollama-box:11434"


def test_lmstudio_provider_uses_lmstudio_host(tmp_path):
    s = _load(tmp_path, {"llm_provider": "lmstudio", **HOSTS})
    with s.create_llm("ingest") as llm:
        assert isinstance(llm, LMStudioProvider)
        assert llm.host == "http://lms-box:1234"
        assert str(llm._client.base_url).rstrip("/") == "http://lms-box:1234/v1"
    # embeddings follow the local provider by default
    assert s.embed_provider_resolved == "lmstudio"
    assert s.embed_host == "http://lms-box:1234"


def test_per_operation_provider_hosts(tmp_path):
    s = _load(tmp_path, {"llm_provider": "ollama", "llm_provider_query": "lmstudio", **HOSTS})
    with s.create_llm("ingest") as ingest, s.create_llm("query") as query:
        assert ingest.host == "http://ollama-box:11434"
        assert query.host == "http://lms-box:1234"


def test_explicit_embed_provider(tmp_path):
    s = _load(tmp_path, {"llm_provider": "lmstudio", "embed_provider": "ollama", **HOSTS})
    assert s.embed_host == "http://ollama-box:11434"


def test_cloud_provider_embeds_with_ollama(tmp_path):
    s = _load(tmp_path, {"llm_provider": "anthropic", **HOSTS})
    assert s.embed_provider_resolved == "ollama"


def test_invalid_embed_provider_rejected(tmp_path):
    with pytest.raises(ValueError):
        _load(tmp_path, {"embed_provider": "openai"})


def test_lmstudio_host_with_v1_suffix_not_doubled():
    llm = create_provider("lmstudio", host="http://localhost:1234/v1")
    assert str(llm._client.base_url).rstrip("/") == "http://localhost:1234/v1"
    llm.close()


def test_lmstudio_json_mode_uses_json_schema():
    llm = LMStudioProvider()
    assert llm._json_response_format(None)["type"] == "json_schema"
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    assert llm._json_response_format(schema)["json_schema"]["schema"] == schema
    assert llm._token_limit(65536) == {}
    llm.close()
