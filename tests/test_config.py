"""Config loader sanity checks."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from muninn.config import SourceConfig, load_config


def test_source_config_basic():
    s = SourceConfig(
        name="x",
        type="browser_history",
        url="file:///tmp/H",
        tool="muninn.sources.browser_history:BrowserHistorySource",
    )
    assert s.name == "x"
    assert s.enabled is True


def test_load_config_round_trip(tmp_path):
    cfg_yaml = tmp_path / "wiki.yaml"
    cfg_yaml.write_text(
        yaml.safe_dump(
            {
                "settings": {"ollama_model": "test-model"},
                "sources": [
                    {
                        "name": "test",
                        "type": "browser_history",
                        "url": "file:///tmp/foo",
                        "tool": "muninn.sources.browser_history:BrowserHistorySource",
                        "options": {"browser": "chrome"},
                    }
                ],
            }
        )
    )
    (tmp_path / "vault").mkdir()
    cfg = load_config(config_path=cfg_yaml, vault_path=tmp_path / "vault")
    assert len(cfg.sources) == 1
    assert cfg.sources[0].options["browser"] == "chrome"
    assert cfg.settings.ollama_model == "test-model"


def test_secret_resolution(monkeypatch):
    monkeypatch.setenv("MY_SECRET", "shh")
    s = SourceConfig(
        name="x",
        type="t",
        url="x",
        tool="m:C",
        secret_env="MY_SECRET",
    )
    assert s.secret == "shh"


def test_secret_missing_raises(monkeypatch):
    monkeypatch.delenv("MISSING_SECRET", raising=False)
    s = SourceConfig(
        name="x",
        type="t",
        url="x",
        tool="m:C",
        secret_env="MISSING_SECRET",
    )
    with pytest.raises(RuntimeError):
        _ = s.secret


def test_per_op_model_overrides(tmp_path):
    cfg_yaml = tmp_path / "wiki.yaml"
    cfg_yaml.write_text(
        yaml.safe_dump(
            {
                "settings": {
                    "ollama_model": "base",
                    "ollama_model_ingest": "ingest-model",
                    "ollama_model_query": "query-model",
                },
                "sources": [
                    {"name": "t", "type": "t", "url": "u", "tool": "m:C", "options": {}}
                ],
            }
        )
    )
    (tmp_path / "vault").mkdir()
    cfg = load_config(config_path=cfg_yaml, vault_path=tmp_path / "vault")
    assert cfg.settings.ollama_model == "base"
    assert cfg.settings.model_for_ingest == "ingest-model"
    assert cfg.settings.model_for_query == "query-model"


def test_per_op_model_falls_back(tmp_path):
    cfg_yaml = tmp_path / "wiki.yaml"
    cfg_yaml.write_text(yaml.safe_dump({"settings": {"ollama_model": "only-default"}, "sources": []}))
    (tmp_path / "vault").mkdir()
    cfg = load_config(config_path=cfg_yaml, vault_path=tmp_path / "vault")
    assert cfg.settings.model_for_ingest == "only-default"
    assert cfg.settings.model_for_query == "only-default"
