from pathlib import Path

import pytest

from murmur.config import ConfigError, load_config


def test_packaged_defaults_load_without_user_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = load_config(env={})
    assert cfg.source_path is None
    assert cfg.data_dir == Path("data")
    assert cfg.timezone == "America/New_York"
    assert cfg.news.max_articles == 20
    assert cfg.sentiment.model == "ProsusAI/finbert"
    assert cfg.universe.source == "static"
    assert len(cfg.universe.static) == 50
    assert cfg.universe.static["BRK.B"] == "Berkshire Hathaway"
    assert cfg.store_path == Path("data/murmur.sqlite")
    assert cfg.published_dir == Path("data/published")


def test_user_file_merges_over_defaults(tmp_path):
    user = tmp_path / "custom.toml"
    user.write_text('data_dir = "/srv/murmur"\n[news]\nmax_articles = 5\n')
    cfg = load_config(user, env={})
    assert cfg.source_path == user
    assert cfg.data_dir == Path("/srv/murmur")
    assert cfg.news.max_articles == 5
    assert cfg.sentiment.batch_size == 32  # untouched default
    assert len(cfg.universe.static) == 50


def test_resolution_order(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "murmur.toml").write_text('data_dir = "from-cwd"\n')
    env_file = tmp_path / "env.toml"
    env_file.write_text('data_dir = "from-env"\n')
    explicit = tmp_path / "explicit.toml"
    explicit.write_text('data_dir = "from-flag"\n')

    assert load_config(env={}).data_dir == Path("from-cwd")
    assert load_config(env={"MURMUR_CONFIG": str(env_file)}).data_dir == Path("from-env")
    assert load_config(explicit, env={"MURMUR_CONFIG": str(env_file)}).data_dir == Path("from-flag")
    assert load_config(explicit, env={"MURMUR_DATA_DIR": "/override"}).data_dir == Path("/override")


def test_missing_explicit_file_is_an_error(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.toml", env={})


def test_static_universe_can_be_replaced(tmp_path):
    user = tmp_path / "u.toml"
    user.write_text('[universe.static]\nAAPL = "Apple"\n"BRK.B" = "Berkshire"\n')
    cfg = load_config(user, env={})
    # TOML tables merge key by key, so the user list is added to the default one
    assert cfg.universe.static["BRK.B"] == "Berkshire"
    assert "MSFT" in cfg.universe.static


def test_unsupported_universe_source(tmp_path):
    user = tmp_path / "u.toml"
    user.write_text('[universe]\nsource = "marketcap"\n')
    with pytest.raises(ConfigError):
        load_config(user, env={})
