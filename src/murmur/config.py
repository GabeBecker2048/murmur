"""Configuration: packaged defaults merged with an optional user TOML file.

Resolution order for the user file: ``--config`` (explicit path) >
``MURMUR_CONFIG`` > ``./murmur.toml`` if it exists > nothing. ``MURMUR_DATA_DIR``
overrides ``data_dir`` from any file.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

ENV_CONFIG = "MURMUR_CONFIG"
ENV_DATA_DIR = "MURMUR_DATA_DIR"
DEFAULT_USER_CONFIG = Path("murmur.toml")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class NewsConfig:
    max_articles: int


@dataclass(frozen=True)
class SentimentConfig:
    model: str
    batch_size: int


@dataclass(frozen=True)
class UniverseConfig:
    source: str
    static: dict[str, str]


@dataclass(frozen=True)
class Config:
    data_dir: Path
    timezone: str
    news: NewsConfig
    sentiment: SentimentConfig
    universe: UniverseConfig
    source_path: Path | None
    """The user file that was merged over the defaults, if any."""

    @property
    def store_path(self) -> Path:
        return self.data_dir / "murmur.sqlite"

    @property
    def published_dir(self) -> Path:
        return self.data_dir / "published"


def packaged_defaults() -> dict:
    text = resources.files("murmur").joinpath("default_config.toml").read_text("utf-8")
    return tomllib.loads(text)


def _merge(base: dict, override: Mapping) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def resolve_user_path(path: str | Path | None, env: Mapping[str, str]) -> Path | None:
    if path is not None:
        return Path(path)
    if env.get(ENV_CONFIG):
        return Path(env[ENV_CONFIG])
    if DEFAULT_USER_CONFIG.is_file():
        return DEFAULT_USER_CONFIG
    return None


def load_config(path: str | Path | None = None, env: Mapping[str, str] | None = None) -> Config:
    env = os.environ if env is None else env
    raw = packaged_defaults()
    user_path = resolve_user_path(path, env)
    if user_path is not None:
        if not user_path.is_file():
            raise ConfigError(f"config file not found: {user_path}")
        with user_path.open("rb") as f:
            raw = _merge(raw, tomllib.load(f))
    if env.get(ENV_DATA_DIR):
        raw["data_dir"] = env[ENV_DATA_DIR]
    return _build(raw, user_path)


def _build(raw: dict, user_path: Path | None) -> Config:
    try:
        universe = raw["universe"]
        static = {str(k): str(v) for k, v in universe.get("static", {}).items()}
        if universe["source"] == "static" and not static:
            raise ConfigError("universe.source is 'static' but universe.static is empty")
        if universe["source"] != "static":
            raise ConfigError(f"unsupported universe.source {universe['source']!r} (0.1.0: static)")
        return Config(
            data_dir=Path(raw["data_dir"]),
            timezone=str(raw["timezone"]),
            news=NewsConfig(max_articles=int(raw["news"]["max_articles"])),
            sentiment=SentimentConfig(
                model=str(raw["sentiment"]["model"]),
                batch_size=int(raw["sentiment"]["batch_size"]),
            ),
            universe=UniverseConfig(source=str(universe["source"]), static=static),
            source_path=user_path,
        )
    except KeyError as e:
        raise ConfigError(f"missing config key: {e.args[0]}") from None
