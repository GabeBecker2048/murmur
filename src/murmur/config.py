"""Configuration: layered TOML.

    packaged defaults  <  ops file (``--config`` / ``MURMUR_CONFIG`` / ``./murmur.toml``)
                       <  settings file (``<data_dir>/settings.toml``, written by the API)
                       <  environment (``MURMUR_DATA_DIR``, API keys, API token)

Every table merges key by key except ``[universe.static]``, which a higher layer
replaces wholesale. ``Config.provenance`` records which layer set each leaf.
"""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

ENV_CONFIG = "MURMUR_CONFIG"
ENV_DATA_DIR = "MURMUR_DATA_DIR"
ENV_FINNHUB_KEY = "MURMUR_FINNHUB_API_KEY"
ENV_POLYGON_KEY = "MURMUR_POLYGON_API_KEY"
ENV_API_TOKEN = "MURMUR_API_TOKEN"
ENV_CORS = "MURMUR_CORS_ORIGINS"
DEFAULT_USER_CONFIG = Path("murmur.toml")
SETTINGS_FILENAME = "settings.toml"

NEWS_SOURCES = ("yahoo", "finnhub", "gdelt")
PRICE_SOURCES = ("yfinance", "polygon")
SPLIT_SOURCES = ("auto", "polygon", "none")
UNIVERSE_SOURCES = ("static", "marketcap")
DEVICES = ("cpu", "cuda", "auto")
THEMES = ("auto", "light", "dark")
METHODS = ("pearson", "spearman")
RUN_KINDS = ("full", "collect", "analyze")
"""What a scheduled run does: everything; news collection only (the evening slot when
``schedule.analysis_time`` is set); prices + analysis for a past session (the morning slot)."""
TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"

SECRET_KEYS = ("finnhub.api_key", "polygon.api_key")
"""Masked when shown, refused when edited through the API."""
LOCKED_KEYS = ("data_dir", "timezone", "api.host", "api.port", *SECRET_KEYS)
"""Keys the settings API refuses: they need a restart, or are secrets."""

# Tables that a higher layer replaces wholesale instead of merging key by key.
REPLACE_TABLES = {("universe", "static")}
INDEX_NAME_PATTERN = r"^[a-z][a-z0-9]{1,15}$"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class NewsConfig:
    max_articles: int
    sources: tuple[str, ...]


@dataclass(frozen=True)
class PricesConfig:
    source: str
    splits: str
    """Where split ratios come from: ``polygon``, ``none``, or ``auto`` (polygon when a key
    is configured). An unadjusted source (polygon) needs them for correct returns."""


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
    """Coverage: how many of the ranked companies get news and sentiment."""
    min_market_cap: float
    static: dict[str, str]


@dataclass(frozen=True)
class IndexConfig:
    name: str
    """Dataset and endpoint name of the cap-weighted list (``m100``)."""
    size: int
    """How many companies are ranked and weighted; coverage is at most this."""

    @property
    def title(self) -> str:
        return f"Murmur {self.size}"


@dataclass(frozen=True)
class ScheduleConfig:
    time: str
    """The daily slot: the whole day, or only news collection when ``analysis_time`` is set."""
    analysis_time: str
    """Optional second slot (HH:MM): prices + analysis + export for the previous session.
    For price sources whose bar for a day is final only after the evening run."""
    paused: bool
    retries: int
    retry_delay_minutes: int

    @property
    def hour_minute(self) -> tuple[int, int]:
        return _hm(self.time)

    @property
    def split(self) -> bool:
        return bool(self.analysis_time)

    @property
    def slots(self) -> list[tuple[str, str]]:
        """``(HH:MM, kind)`` for each daily slot."""
        if not self.analysis_time:
            return [(self.time, "full")]
        return [(self.time, "collect"), (self.analysis_time, "analyze")]


def _hm(text: str) -> tuple[int, int]:
    hour, minute = text.split(":")
    return int(hour), int(minute)


@dataclass(frozen=True)
class UiConfig:
    title: str
    tagline: str
    default_months: int
    default_method: str
    theme: str


@dataclass(frozen=True)
class DatasetsConfig:
    private: tuple[str, ...]
    """Datasets whose files `/data` refuses without the API token. They stay in the manifest,
    flagged ``public: false``."""
    contact: str
    """Takedown / questions address printed in every license notice (default: sec.contact)."""


@dataclass(frozen=True)
class ApiConfig:
    host: str
    port: int
    token: str
    """Bearer token for mutating endpoints; env only. Empty = writes refused (503)."""
    cors_origins: tuple[str, ...]


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
    index: IndexConfig
    schedule: ScheduleConfig
    api: ApiConfig
    ui: UiConfig
    datasets: DatasetsConfig
    source_path: Path | None
    """The ops file that was merged over the defaults, if any."""
    raw: dict = field(default_factory=dict, compare=False, repr=False)
    """The merged TOML tree the dataclasses were built from."""
    provenance: dict[str, str] = field(default_factory=dict, compare=False, repr=False)
    """dotted leaf path -> default|file|api|env"""

    @property
    def store_path(self) -> Path:
        return self.data_dir / "murmur.sqlite"

    @property
    def published_dir(self) -> Path:
        return self.data_dir / "published"

    @property
    def heartbeat_path(self) -> Path:
        """The scheduler's heartbeat (written by every tick)."""
        return self.data_dir / "heartbeat"

    @property
    def worker_heartbeat_path(self) -> Path:
        return self.data_dir / "worker.heartbeat"

    @property
    def settings_path(self) -> Path:
        return self.data_dir / SETTINGS_FILENAME


def packaged_defaults() -> dict:
    text = resources.files("murmur").joinpath("default_config.toml").read_text("utf-8")
    return tomllib.loads(text)


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


def _leaves(tree: Mapping, path: tuple[str, ...] = ()) -> list[str]:
    out = []
    for key, value in tree.items():
        here = (*path, key)
        if isinstance(value, Mapping) and here not in REPLACE_TABLES:
            out += _leaves(value, here)
        else:
            out.append(".".join(here))
    return out


def resolve_user_path(path: str | Path | None, env: Mapping[str, str]) -> Path | None:
    if path is not None:
        return Path(path)
    if env.get(ENV_CONFIG):
        return Path(env[ENV_CONFIG])
    if DEFAULT_USER_CONFIG.is_file():
        return DEFAULT_USER_CONFIG
    return None


def _read_toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def load_config(
    path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    settings_override: Mapping | None = None,
) -> Config:
    """Build the effective config. ``settings_override`` replaces the settings
    file's content (used to validate a candidate before writing it)."""
    env = os.environ if env is None else env
    raw = packaged_defaults()
    provenance = {leaf: "default" for leaf in _leaves(raw)}

    user_path = resolve_user_path(path, env)
    if user_path is not None:
        if not user_path.is_file():
            raise ConfigError(f"config file not found: {user_path}")
        user = _read_toml(user_path)
        raw = _merge(raw, user)
        provenance.update({leaf: "file" for leaf in _leaves(user)})
    if env.get(ENV_DATA_DIR):
        raw["data_dir"] = env[ENV_DATA_DIR]
        provenance["data_dir"] = "env"

    settings_path = Path(raw["data_dir"]) / SETTINGS_FILENAME
    settings = settings_override
    if settings is None and settings_path.is_file():
        settings = _read_toml(settings_path)
    if settings:
        raw = _merge(raw, settings)
        provenance.update({leaf: "api" for leaf in _leaves(settings)})

    for env_key, table, key in (
        (ENV_FINNHUB_KEY, "finnhub", "api_key"),
        (ENV_POLYGON_KEY, "polygon", "api_key"),
        (ENV_API_TOKEN, "api", "token"),
        (ENV_CORS, "api", "cors_origins"),
    ):
        if env.get(env_key):
            raw.setdefault(table, {})[key] = env[env_key]
            provenance[f"{table}.{key}"] = "env"
    return _build(raw, user_path, provenance)


def _build(raw: dict, user_path: Path | None, provenance: dict[str, str]) -> Config:
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
        if not re.fullmatch(TIME_PATTERN, schedule_time):
            raise ConfigError(f"schedule.time must be HH:MM, not {schedule_time!r}")
        analysis_time = str(raw["schedule"].get("analysis_time", "") or "")
        if analysis_time and not re.fullmatch(TIME_PATTERN, analysis_time):
            raise ConfigError(
                f"schedule.analysis_time must be HH:MM or empty, not {analysis_time!r}"
            )
        splits = str(raw.get("prices", {}).get("splits", "auto"))
        if splits not in SPLIT_SOURCES:
            raise ConfigError(f"prices.splits must be one of {SPLIT_SOURCES}, not {splits!r}")
        ui = raw.get("ui", {})
        if str(ui.get("theme", "auto")) not in THEMES:
            raise ConfigError(f"ui.theme must be one of {THEMES}")
        if str(ui.get("default_method", "pearson")) not in METHODS:
            raise ConfigError(f"ui.default_method must be one of {METHODS}")
        if int(ui.get("default_months", 3)) not in (1, 3, 6, 12):
            raise ConfigError("ui.default_months must be 1, 3, 6 or 12")
        private = raw.get("datasets", {}).get("private", [])
        if isinstance(private, str):
            private = [private]
        private = tuple(str(x) for x in private)
        contact = str(
            raw.get("datasets", {}).get("contact", "") or raw.get("sec", {}).get("contact", "")
        )
        api = raw.get("api", {})
        cors = api.get("cors_origins", ())
        if isinstance(cors, str):
            cors = tuple(x.strip() for x in cors.split(",") if x.strip())
        port = int(api.get("port", 8100))
        if not 1 <= port <= 65535:
            raise ConfigError(f"api.port must be 1..65535, not {port}")
        for key in ("max_articles",):
            if int(raw["news"][key]) < 1:
                raise ConfigError(f"news.{key} must be >= 1")
        if int(raw["schedule"]["retries"]) < 0 or int(raw["schedule"]["retry_delay_minutes"]) < 1:
            raise ConfigError("schedule.retries must be >= 0 and retry_delay_minutes >= 1")
        if int(universe.get("size", 100)) < 1 or int(raw["sentiment"]["batch_size"]) < 1:
            raise ConfigError("universe.size and sentiment.batch_size must be >= 1")
        index = raw.get("index", {})
        index_name = str(index.get("name", "m100"))
        if not re.fullmatch(INDEX_NAME_PATTERN, index_name):
            raise ConfigError(f"index.name must match {INDEX_NAME_PATTERN}, not {index_name!r}")
        index_size = int(index.get("size", universe.get("size", 100)))
        if index_size < 1:
            raise ConfigError("index.size must be >= 1")
        if universe["source"] == "marketcap" and int(universe.get("size", 100)) > index_size:
            raise ConfigError(
                f"universe.size ({universe.get('size', 100)}) cannot exceed index.size "
                f"({index_size}): coverage is a subset of the index"
            )
        return Config(
            data_dir=Path(raw["data_dir"]),
            timezone=str(raw["timezone"]),
            news=NewsConfig(max_articles=int(raw["news"]["max_articles"]), sources=sources),
            prices=PricesConfig(source=price_source, splits=splits),
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
            index=IndexConfig(name=index_name, size=index_size),
            schedule=ScheduleConfig(
                time=schedule_time,
                analysis_time=analysis_time,
                paused=bool(raw["schedule"].get("paused", False)),
                retries=int(raw["schedule"]["retries"]),
                retry_delay_minutes=int(raw["schedule"]["retry_delay_minutes"]),
            ),
            api=ApiConfig(
                host=str(api.get("host", "0.0.0.0")),
                port=port,
                token=str(api.get("token", "")),
                cors_origins=tuple(cors),
            ),
            ui=UiConfig(
                title=str(ui.get("title", "Murmur")),
                tagline=str(ui.get("tagline", "")),
                default_months=int(ui.get("default_months", 3)),
                default_method=str(ui.get("default_method", "pearson")),
                theme=str(ui.get("theme", "auto")),
            ),
            datasets=DatasetsConfig(private=private, contact=contact),
            source_path=user_path,
            raw=raw,
            provenance=provenance,
        )
    except KeyError as e:
        raise ConfigError(f"missing config key: {e.args[0]}") from None
    except (TypeError, ValueError) as e:
        if isinstance(e, ConfigError):
            raise
        raise ConfigError(f"invalid config value: {e}") from None


# -- the settings layer (edited through the API) -------------------------------

SETTINGS_SCHEMA: list[dict] = [
    {
        "path": "news.max_articles",
        "type": "integer",
        "min": 1,
        "help": "Headlines per ticker per Yahoo fetch.",
    },
    {
        "path": "news.sources",
        "type": "list",
        "choices": list(NEWS_SOURCES),
        "help": (
            "News sources the daily run queries. finnhub needs an API "
            "key; gdelt is paced at one request per 5 s."
        ),
    },
    {
        "path": "prices.source",
        "type": "choice",
        "choices": list(PRICE_SOURCES),
        "help": (
            "Daily price source. polygon's free plan cannot serve today's bar at 18:00: set"
            " schedule.analysis_time for it."
        ),
    },
    {
        "path": "prices.splits",
        "type": "choice",
        "choices": list(SPLIT_SOURCES),
        "help": (
            "Split ratios for return computation (polygon needs its key; auto = polygon when"
            " the key is set). Unadjusted sources need them."
        ),
    },
    {
        "path": "sentiment.model",
        "type": "string",
        "help": (
            "Hugging Face model id. Changing it re-scores every headline on the next sentiment job."
        ),
        "restart_required": False,
    },
    {
        "path": "sentiment.batch_size",
        "type": "integer",
        "min": 1,
        "help": "Headlines per FinBERT batch.",
    },
    {
        "path": "sentiment.device",
        "type": "choice",
        "choices": list(DEVICES),
        "help": "cpu, cuda, or auto. The server image is CPU-only.",
    },
    {
        "path": "sec.contact",
        "type": "string",
        "help": "Contact email the SEC requires in the User-Agent.",
    },
    {
        "path": "universe.source",
        "type": "choice",
        "choices": list(UNIVERSE_SOURCES),
        "help": "marketcap builds the monthly top-N; static uses universe.static.",
    },
    {
        "path": "universe.size",
        "type": "integer",
        "min": 1,
        "help": "Coverage: how many ranked companies get news and sentiment (at most index.size).",
    },
    {
        "path": "index.name",
        "type": "string",
        "pattern": INDEX_NAME_PATTERN,
        "help": "Dataset and endpoint name of the cap-weighted index (m100 -> Murmur 100).",
    },
    {
        "path": "index.size",
        "type": "integer",
        "min": 1,
        "help": "Companies ranked and weighted each month; coverage is at most this.",
    },
    {
        "path": "universe.min_market_cap",
        "type": "number",
        "min": 0,
        "help": "Screener floor in USD.",
    },
    {
        "path": "universe.static",
        "type": "table",
        "help": "ticker = name pairs for the static universe. Replaces the list wholesale.",
    },
    {
        "path": "schedule.time",
        "type": "string",
        "pattern": r"^([01]\d|2[0-3]):[0-5]\d$",
        "help": "Daily run time, HH:MM in the configured timezone.",
    },
    {
        "path": "schedule.analysis_time",
        "type": "string",
        "pattern": TIME_PATTERN,
        "help": (
            "Optional morning slot (HH:MM) running prices + analysis + export for the previous"
            " session; schedule.time then only collects news. Empty = one full run."
        ),
    },
    {
        "path": "schedule.paused",
        "type": "boolean",
        "help": "When true the scheduler queues nothing; jobs started by hand still run.",
    },
    {
        "path": "schedule.retries",
        "type": "integer",
        "min": 0,
        "help": "Extra attempts after a failed run.",
    },
    {
        "path": "schedule.retry_delay_minutes",
        "type": "integer",
        "min": 1,
        "help": "Minutes between attempts.",
    },
    {
        "path": "ui.title",
        "type": "string",
        "help": "Dashboard title.",
    },
    {
        "path": "ui.tagline",
        "type": "string",
        "help": "One line under the title.",
    },
    {
        "path": "ui.default_months",
        "type": "choice",
        "choices": [1, 3, 6, 12],
        "help": "Rolling horizon the dashboard opens with.",
    },
    {
        "path": "ui.default_method",
        "type": "choice",
        "choices": list(METHODS),
        "help": "Correlation method the dashboard opens with.",
    },
    {
        "path": "ui.theme",
        "type": "choice",
        "choices": list(THEMES),
        "help": "Default theme; visitors can switch.",
    },
    {
        "path": "datasets.contact",
        "type": "string",
        "help": "Contact printed in every license notice (takedown requests); default sec.contact.",
    },
    {
        "path": "datasets.private",
        "type": "list",
        "choices": ["companies", "prices", "news", "sentiment", "correlations"],
        "help": (
            "Datasets served only with the API token (kept in the manifest, flagged"
            " public=false). prices stays here until the source's terms allow publishing."
        ),
    },
    {
        "path": "data_dir",
        "type": "string",
        "editable": False,
        "restart_required": True,
        "help": "Data directory (env MURMUR_DATA_DIR). Restart to change.",
    },
    {
        "path": "timezone",
        "type": "string",
        "editable": False,
        "restart_required": True,
        "help": "Exchange timezone; the calendar is NYSE regardless.",
    },
    {
        "path": "api.host",
        "type": "string",
        "editable": False,
        "restart_required": True,
        "help": "Bind address.",
    },
    {
        "path": "api.port",
        "type": "integer",
        "editable": False,
        "restart_required": True,
        "help": "Bind port.",
    },
    {
        "path": "finnhub.api_key",
        "type": "secret",
        "editable": False,
        "help": "Env MURMUR_FINNHUB_API_KEY.",
    },
    {
        "path": "polygon.api_key",
        "type": "secret",
        "editable": False,
        "help": "Env MURMUR_POLYGON_API_KEY.",
    },
]


def _get_path(tree: Mapping, dotted: str):
    node = tree
    for part in dotted.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


def settings_schema(config: Config) -> list[dict]:
    """The registry with each key's default, current value and provenance."""
    defaults = packaged_defaults()
    out = []
    for entry in SETTINGS_SCHEMA:
        item = {"editable": True, "restart_required": False, **entry}
        current = _get_path(config.raw, entry["path"])
        if entry["type"] == "secret":
            current = mask(current)
        item["default"] = _get_path(defaults, entry["path"])
        item["current"] = current
        item["source"] = config.provenance.get(entry["path"], "default")
        out.append(item)
    return out


def mask(value) -> str | None:
    if not value:
        return None
    text = str(value)
    return "****" + text[-4:] if len(text) > 8 else "****"


def effective_settings(config: Config) -> dict:
    """The merged tree with secrets masked, for ``GET /settings``."""
    import copy

    raw = copy.deepcopy(config.raw)
    for dotted in SECRET_KEYS:
        table, key = dotted.split(".")
        if raw.get(table, {}).get(key):
            raw[table][key] = mask(raw[table][key])
    raw.setdefault("api", {})["token"] = mask(config.api.token)
    return raw


def _apply_patch(current: dict, patch: Mapping, path: tuple[str, ...] = ()) -> dict:
    """Merge a PUT body into the settings layer: tables key by key, ``universe.static``
    wholesale, JSON null removes an override."""
    out = dict(current)
    for key, value in patch.items():
        here = (*path, key)
        if value is None:
            out.pop(key, None)
        elif isinstance(value, Mapping) and here not in REPLACE_TABLES:
            out[key] = _apply_patch(
                out.get(key, {}) if isinstance(out.get(key), dict) else {}, value, here
            )
            if not out[key]:
                out.pop(key)
        else:
            out[key] = dict(value) if isinstance(value, Mapping) else value
    return out


def write_settings(config: Config, patch: Mapping, env: Mapping[str, str] | None = None) -> Config:
    """Apply ``patch`` to the settings layer, validate the result by building a
    Config from it, write the file atomically, and return the new Config."""
    import tomli_w

    for leaf in _leaves(patch):
        if leaf in LOCKED_KEYS or any(leaf.startswith(k + ".") for k in LOCKED_KEYS):
            raise ConfigError(f"{leaf} cannot be changed through the API")
    path = config.settings_path
    current = _read_toml(path) if path.is_file() else {}
    candidate = _apply_patch(current, patch)
    new = load_config(config.source_path, env, settings_override=candidate or {})
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(".toml.part")
    part.write_text(tomli_w.dumps(candidate), encoding="utf-8")
    os.replace(part, path)
    return new


class ConfigProvider:
    """Re-reads the layered config when either file changes; cheap to call per request."""

    def __init__(self, path: str | Path | None = None, env: Mapping[str, str] | None = None):
        self.path = path
        self.env = env
        self._stamp = None
        self._config: Config | None = None

    def _stamps(self, config: Config | None):
        files = [resolve_user_path(self.path, self.env or os.environ)]
        if config is not None:
            files.append(config.settings_path)
        out = []
        for f in files:
            try:
                st = f.stat() if f else None
                out.append((str(f), st.st_mtime_ns, st.st_size) if st else (str(f), None))
            except OSError:
                out.append((str(f), None))
        return tuple(out)

    def get(self) -> Config:
        stamp = self._stamps(self._config)
        if self._config is None or stamp != self._stamp:
            self._config = load_config(self.path, self.env)
            self._stamp = self._stamps(self._config)
        return self._config

    def invalidate(self) -> None:
        self._stamp = None
