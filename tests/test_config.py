"""Config loader sanity checks."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from muninn.config import SourceConfig, load_config


def test_source_config_valid_schedule():
    s = SourceConfig(
        name="x",
        type="browser_history",
        url="file:///tmp/H",
        tool="muninn.sources.browser_history:BrowserHistorySource",
        schedule="hourly",
    )
    assert s.schedule == "hourly"


def test_source_config_invalid_schedule():
    with pytest.raises(Exception):
        SourceConfig(
            name="x",
            type="x",
            url="x",
            tool="m:C",
            schedule="every_2min",  # not in the allowed set
        )


def test_load_config_round_trip(tmp_path, monkeypatch):
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


def test_secret_resolution(monkeypatch, tmp_path):
    monkeypatch.setenv("MY_SECRET", "shh")
    s = SourceConfig(
        name="x",
        type="t",
        url="x",
        tool="m:C",
        secret_env="MY_SECRET",
        schedule="hourly",
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
        schedule="hourly",
    )
    with pytest.raises(RuntimeError):
        _ = s.secret


def test_per_op_model_overrides(tmp_path, monkeypatch):
    """Ingest/query model overrides take precedence; both fall back to ollama_model."""
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


def test_per_op_model_falls_back(tmp_path, monkeypatch):
    cfg_yaml = tmp_path / "wiki.yaml"
    cfg_yaml.write_text(yaml.safe_dump({"settings": {"ollama_model": "only-default"}, "sources": []}))
    (tmp_path / "vault").mkdir()
    cfg = load_config(config_path=cfg_yaml, vault_path=tmp_path / "vault")
    assert cfg.settings.model_for_ingest == "only-default"
    assert cfg.settings.model_for_query == "only-default"
