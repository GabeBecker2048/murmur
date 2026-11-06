"""The settings layer: provenance, the schema registry, PUT semantics, atomic writes."""

import tomllib

import pytest

from murmur.config import (
    ConfigError,
    ConfigProvider,
    effective_settings,
    load_config,
    settings_schema,
    write_settings,
)


@pytest.fixture
def cfg(tmp_path):
    ops = tmp_path / "murmur.toml"
    ops.write_text(f'data_dir = "{tmp_path / "data"}"\n[news]\nmax_articles = 15\n')
    return load_config(ops, env={"MURMUR_FINNHUB_API_KEY": "abcdefghijkl"})


def test_layers_and_provenance(cfg, tmp_path):
    assert cfg.news.max_articles == 15 and cfg.provenance["news.max_articles"] == "file"
    assert cfg.provenance["schedule.time"] == "default"
    assert cfg.provenance["finnhub.api_key"] == "env" and cfg.keys.finnhub == "abcdefghijkl"
    assert cfg.settings_path == tmp_path / "data" / "settings.toml"
    assert cfg.api.port == 8100 and cfg.api.token == ""
    cfg.settings_path.parent.mkdir()
    cfg.settings_path.write_text('[news]\nmax_articles = 7\n[schedule]\ntime = "17:30"\n')
    again = load_config(cfg.source_path, env={})
    assert again.news.max_articles == 7 and again.provenance["news.max_articles"] == "api"
    assert again.schedule.time == "17:30"


def test_effective_settings_masks_secrets(cfg):
    eff = effective_settings(cfg)
    assert eff["finnhub"]["api_key"] == "****ijkl"
    assert eff["api"]["token"] is None
    assert eff["news"]["max_articles"] == 15
    assert cfg.keys.finnhub == "abcdefghijkl"  # the config itself is untouched


def test_schema_registry(cfg):
    schema = {s["path"]: s for s in settings_schema(cfg)}
    assert schema["news.sources"]["choices"] == ["yahoo", "finnhub", "gdelt"]
    assert (
        schema["news.max_articles"]["current"] == 15
        and schema["news.max_articles"]["default"] == 20
    )
    assert schema["news.max_articles"]["source"] == "file"
    assert (
        schema["finnhub.api_key"]["current"] == "****ijkl"
        and not schema["finnhub.api_key"]["editable"]
    )
    assert schema["data_dir"]["restart_required"] is True
    assert schema["schedule.time"]["pattern"]


def test_write_settings_merges_validates_and_is_atomic(cfg, tmp_path):
    new = write_settings(
        cfg, {"news": {"max_articles": 9, "sources": ["yahoo", "finnhub"]}}, env={}
    )
    assert new.news.max_articles == 9 and new.news.sources == ("yahoo", "finnhub")
    on_disk = tomllib.loads(cfg.settings_path.read_text())
    assert on_disk == {"news": {"max_articles": 9, "sources": ["yahoo", "finnhub"]}}
    assert not cfg.settings_path.with_suffix(".toml.part").exists()
    # a second patch merges key by key
    new = write_settings(new, {"schedule": {"time": "17:00"}}, env={})
    assert new.news.max_articles == 9 and new.schedule.time == "17:00"
    # null removes an override, falling back to the ops file
    new = write_settings(new, {"news": {"max_articles": None}}, env={})
    assert new.news.max_articles == 15 and new.provenance["news.max_articles"] == "file"
    assert "max_articles" not in tomllib.loads(cfg.settings_path.read_text())["news"]
    # universe.static replaces wholesale
    new = write_settings(
        new,
        {"universe": {"source": "static", "static": {"AAPL": "Apple", "MSFT": "Microsoft"}}},
        env={},
    )
    assert new.universe.static == {"AAPL": "Apple", "MSFT": "Microsoft"}
    new = write_settings(new, {"universe": {"static": {"GOOGL": "Alphabet"}}}, env={})
    assert new.universe.static == {"GOOGL": "Alphabet"}


def test_write_settings_rejects_bad_values_and_locked_keys(cfg):
    before = cfg.settings_path.read_text() if cfg.settings_path.exists() else None
    with pytest.raises(ConfigError, match="schedule.time"):
        write_settings(cfg, {"schedule": {"time": "6pm"}}, env={})
    with pytest.raises(ConfigError, match="news.sources"):
        write_settings(cfg, {"news": {"sources": ["bloomberg"]}}, env={})
    for patch in ({"data_dir": "/elsewhere"}, {"finnhub": {"api_key": "x"}}, {"api": {"port": 1}}):
        with pytest.raises(ConfigError, match="cannot be changed"):
            write_settings(cfg, patch, env={})
    after = cfg.settings_path.read_text() if cfg.settings_path.exists() else None
    assert before == after  # nothing written on failure


def test_provider_reloads_on_change(cfg, tmp_path):
    provider = ConfigProvider(cfg.source_path, env={})
    first = provider.get()
    assert provider.get() is first  # cached
    write_settings(first, {"news": {"max_articles": 3}}, env={})
    assert provider.get().news.max_articles == 3
    cfg.source_path.write_text(f'data_dir = "{tmp_path / "data"}"\n[news]\nmax_articles = 21\n')
    import os
    import time

    os.utime(cfg.source_path, (time.time() + 2, time.time() + 2))
    assert provider.get().news.max_articles == 3  # the api layer still wins
    write_settings(provider.get(), {"news": {"max_articles": None}}, env={})
    assert provider.get().news.max_articles == 21


def test_env_token_and_cors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = load_config(
        env={"MURMUR_API_TOKEN": "secret-token", "MURMUR_CORS_ORIGINS": "http://a, http://b"}
    )
    assert cfg.api.token == "secret-token" and cfg.api.cors_origins == ("http://a", "http://b")
    assert cfg.provenance["api.token"] == "env"
    assert effective_settings(cfg)["api"]["token"] == "****oken"
