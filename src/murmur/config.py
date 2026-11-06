"""Configuration: packaged defaults merged with an optional user TOML file.

Resolution order for the user file: ``--config`` (explicit path) >
``MURMUR_CONFIG`` > ``./murmur.toml`` if it exists > nothing. ``MURMUR_DATA_DIR``
overrides ``data_dir`` from any file.
"""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

ENV_CONFIG = "MURMUR_CONFIG"
ENV_DATA_DIR = "MURMUR_DATA_DIR"
ENV_FINNHUB_KEY = "MURMUR_FINNHUB_API_KEY"
ENV_POLYGON_KEY = "MURMUR_POLYGON_API_KEY"
NEWS_SOURCES = ("yahoo", "finnhub", "gdelt")
PRICE_SOURCES = ("yfinance", "polygon")
DEFAULT_USER_CONFIG = Path("murmur.toml")

UNIVERSE_SOURCES = ("static", "marketcap")
DEVICES = ("cpu", "cuda", "auto")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class NewsConfig:
    max_articles: int
    sources: tuple[str, ...]


@dataclass(frozen=True)
class PricesConfig:
    source: str


@dataclass(frozen=True)
class KeysConfig:
    finnhub: str
    polygon: str


@dataclass(frozen=True)
class SentimentConfig:
    model: str
    batch_size: int
    device: str


@dataclass(frozen=True)
class SecConfig:
    contact: str


@dataclass(frozen=True)
class UniverseConfig:
    source: str
    size: int
    min_market_cap: float
    static: dict[str, str]


@dataclass(frozen=True)
class ScheduleConfig:
    time: str
    retries: int
    retry_delay_minutes: int

    @property
    def hour_minute(self) -> tuple[int, int]:
        hour, minute = self.time.split(":")
        return int(hour), int(minute)


@dataclass(frozen=True)
class Config:
    data_dir: Path
    timezone: str
    news: NewsConfig
    prices: PricesConfig
    keys: KeysConfig
    sentiment: SentimentConfig
    sec: SecConfig
    universe: UniverseConfig
    schedule: ScheduleConfig
    source_path: Path | None
    """The user file that was merged over the defaults, if any."""

    @property
    def store_path(self) -> Path:
        return self.data_dir / "murmur.sqlite"

    @property
    def published_dir(self) -> Path:
        return self.data_dir / "published"

    @property
    def heartbeat_path(self) -> Path:
        return self.data_dir / "heartbeat"


def packaged_defaults() -> dict:
    text = resources.files("murmur").joinpath("default_config.toml").read_text("utf-8")
    return tomllib.loads(text)


# Tables that a user file replaces wholesale instead of merging key by key.
REPLACE_TABLES = {("universe", "static")}


def _merge(base: dict, override: Mapping, path: tuple[str, ...] = ()) -> dict:
    out = dict(base)
    for key, value in override.items():
        here = (*path, key)
        if (
            isinstance(value, Mapping)
            and isinstance(out.get(key), dict)
            and here not in REPLACE_TABLES
        ):
            out[key] = _merge(out[key], value, here)
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
    if env.get(ENV_FINNHUB_KEY):
        raw.setdefault("finnhub", {})["api_key"] = env[ENV_FINNHUB_KEY]
    if env.get(ENV_POLYGON_KEY):
        raw.setdefault("polygon", {})["api_key"] = env[ENV_POLYGON_KEY]
    return _build(raw, user_path)


def _build(raw: dict, user_path: Path | None) -> Config:
    try:
        universe = raw["universe"]
        static = {str(k).upper(): str(v) for k, v in universe.get("static", {}).items()}
        if universe["source"] not in UNIVERSE_SOURCES:
            raise ConfigError(
                f"universe.source must be one of {UNIVERSE_SOURCES}, not {universe['source']!r}"
            )
        if universe["source"] == "static" and not static:
            raise ConfigError("universe.source is 'static' but universe.static is empty")
        device = str(raw["sentiment"].get("device", "cpu"))
        if device not in DEVICES:
            raise ConfigError(f"sentiment.device must be one of {DEVICES}, not {device!r}")
        sources = tuple(str(x) for x in raw["news"].get("sources", ["yahoo"]))
        bad = [x for x in sources if x not in NEWS_SOURCES]
        if bad or not sources:
            raise ConfigError(
                f"news.sources must be a non-empty list from {NEWS_SOURCES}, not {sources}"
            )
        price_source = str(raw.get("prices", {}).get("source", "yfinance"))
        if price_source not in PRICE_SOURCES:
            raise ConfigError(f"prices.source must be one of {PRICE_SOURCES}, not {price_source!r}")
        schedule_time = str(raw["schedule"]["time"])
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", schedule_time):
            raise ConfigError(f"schedule.time must be HH:MM, not {schedule_time!r}")
        return Config(
            data_dir=Path(raw["data_dir"]),
            timezone=str(raw["timezone"]),
            news=NewsConfig(max_articles=int(raw["news"]["max_articles"]), sources=sources),
            prices=PricesConfig(source=price_source),
            keys=KeysConfig(
                finnhub=str(raw.get("finnhub", {}).get("api_key", "")),
                polygon=str(raw.get("polygon", {}).get("api_key", "")),
            ),
            sentiment=SentimentConfig(
                model=str(raw["sentiment"]["model"]),
                batch_size=int(raw["sentiment"]["batch_size"]),
                device=device,
            ),
            sec=SecConfig(contact=str(raw.get("sec", {}).get("contact", ""))),
            universe=UniverseConfig(
                source=str(universe["source"]),
                size=int(universe.get("size", 100)),
                min_market_cap=float(universe.get("min_market_cap", 10_000_000_000)),
                static=static,
            ),
            schedule=ScheduleConfig(
                time=schedule_time,
                retries=int(raw["schedule"]["retries"]),
                retry_delay_minutes=int(raw["schedule"]["retry_delay_minutes"]),
            ),
            source_path=user_path,
        )
    except KeyError as e:
        raise ConfigError(f"missing config key: {e.args[0]}") from None
