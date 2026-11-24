"""Publish the store as datasets under ``<data_dir>/published/`` (served at ``/data/``).

Six datasets, each its own directory, every file named after its date and table:

    index.json                                          the manifest
    companies/2026/11/202611.companies.parquet          monthly: every company ever ranked
    m100/2026/11/202611.constituents.parquet            monthly: rank, weight, cap
    m100/2026/11/202611.changes.parquet                 monthly: added / removed
    prices/2026/11/05/20261105.prices.parquet           daily: the bar + the three returns
    prices/2026/10/202610.prices.parquet                a finished month, compacted
    news/2026/11/05/20261105.articles.parquet           daily: article x ticker + scores
    sentiment/2026/11/05/20261105.sentiment.parquet     daily: ticker x window x source
    correlations/2026/11/05/20261105.daily.parquet      daily
    correlations/2026/11/05/20261105.rolling.parquet    daily
    correlations/2026/11/05/20261105.leadlag.png        charts live with the correlations

Daily files are ``{dataset}/{YYYY}/{MM}/{DD}/{YYYYMMDD}.{table}.parquet``; monthly
files and compacted months are ``{dataset}/{YYYY}/{MM}/{YYYYMM}.{table}.parquet``.
Compaction (finished months only) merges a month's day files, deduplicated by the
table's key, and removes them, so a glob never double counts. A table with no
rows is never written and a stale file is removed. Every row that names a
company carries both ``cik`` and ``ticker``. CSV is never stored: the API
converts on request.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from murmur import __version__
from murmur.bundle import archives_manifest
from murmur.clock import DAY, first_trading_day, month_key, today_in, utc_now_iso
from murmur.config import Config
from murmur.publish import (
    DATA_VERSION,
    cc_by,
    file_metadata,
    news_terms,
    round_cap,
    round_weight,
    source_terms,
)
from murmur.store import Store
from murmur.universe import applicable_month

log = logging.getLogger(__name__)

DAILY = "daily"
MONTHLY = "monthly"


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[str, ...]
    key: tuple[str, ...]
    query: Callable[[Store, Config, str], list[tuple]]
    """``(store, config, day_iso | month)`` -> rows in ``columns`` order."""
    cadence: str | None = None
    """Overrides the dataset's cadence (a monthly table inside a daily dataset)."""


def table_cadence(ds: Dataset, table: Table) -> str:
    return table.cadence or ds.cadence


@dataclass(frozen=True)
class Dataset:
    name: str
    title: str
    description: str
    cadence: str
    tables: tuple[Table, ...]
    license: dict
    charts: bool = False


# -- queries ------------------------------------------------------------------


def _rows(records: Sequence[sqlite3.Row], columns: Sequence[str], **extra) -> list[tuple]:
    return [tuple(extra[c] if c in extra else r[c] for c in columns) for r in records]


def _with_cik(store: Store, records, columns, cik_of: dict[str, int] | None = None):
    cik_of = store.ticker_ciks() if cik_of is None else cik_of
    out = []
    for r in records:
        keys = r.keys()
        out.append(
            tuple(
                (cik_of.get(r["ticker"]) if c == "cik" and "cik" not in keys else r[c])
                for c in columns
            )
        )
    return out


def q_companies(store: Store, config: Config, month: str) -> list[tuple]:
    rows = store.companies_snapshot(upto=month)
    return [
        (
            r["cik"],
            r["ticker"],
            r["name"],
            r["exchange"],
            r["country"],
            r["first_month"],
            r["last_month"],
            r["months"],
            month,
        )
        for r in rows
    ]


def q_constituents(store: Store, config: Config, month: str) -> list[tuple]:
    members = store.universe_month(month)
    year, mm = (int(x) for x in month.split("-"))
    effective = first_trading_day(year, mm).isoformat()
    reconstructed = store.universe_month_method(month) == "reconstructed"
    return [
        (
            month,
            effective,
            m.rank,
            m.cik,
            m.ticker,
            m.name,
            m.exchange,
            round_cap(m.market_cap),
            round_weight(m.weight),
            reconstructed,
        )
        for m in members
    ]


def q_level(store: Store, config: Config, iso: str) -> list[tuple]:
    row = store.index_level_on(iso)
    if row is None:
        return []
    return [tuple(bool(row[c]) if c == "rebalance" else row[c] for c in LEVEL_COLUMNS)]


def q_contributions(store: Store, config: Config, iso: str) -> list[tuple]:
    return [
        (
            r["date"],
            r["cik"],
            r["ticker"],
            round_weight(r["weight"]),
            r["return"],
            r["contribution"],
            r["points"],
        )
        for r in store.index_contributions_on(iso)
    ]


def q_changes(store: Store, config: Config, month: str) -> list[tuple]:
    return _rows(store.universe_changes(month), CHANGES_COLUMNS)


def q_prices(store: Store, config: Config, iso: str) -> list[tuple]:
    return _with_cik(store, store.prices_with_returns_on(iso), PRICES_COLUMNS)


def q_articles(store: Store, config: Config, iso: str) -> list[tuple]:
    return _with_cik(
        store, store.articles_with_scores_on(iso, config.sentiment.model), ARTICLES_COLUMNS
    )


def q_sentiment(store: Store, config: Config, iso: str) -> list[tuple]:
    rows = store.window_sentiment_on(iso, config.sentiment.model)
    return _with_cik(store, rows, SENTIMENT_COLUMNS)


def q_daily(store: Store, config: Config, iso: str) -> list[tuple]:
    return _rows(store.correlations_on(iso), DAILY_COLUMNS)


def q_placebo(store: Store, config: Config, iso: str) -> list[tuple]:
    return [tuple(r) for r in store.placebo_on(iso)]


def q_rolling(store: Store, config: Config, iso: str) -> list[tuple]:
    """Pooled rolling results only; per-company rows are the monthly snapshot table."""
    return [
        tuple(r[c] for c in ROLLING_COLUMNS)
        for r in store.rolling_on(iso)
        if r["scope"] == "pooled"
    ]


def q_rolling_tickers(store: Store, config: Config, month: str) -> list[tuple]:
    """Per-company rolling results as of the month's last session (end-of-month snapshot;
    for the current month, the latest session so far, rewritten daily)."""
    cik_of = store.ticker_ciks()
    return [
        tuple(cik_of.get(r["ticker"]) if c == "cik" else r[c] for c in ROLLING_TICKERS_COLUMNS)
        for r in store.rolling_month_snapshot(month)
    ]


COMPANIES_COLUMNS = (
    "cik",
    "ticker",
    "name",
    "exchange",
    "country",
    "first_month",
    "last_month",
    "months",
    "snapshot_month",
)
CONSTITUENTS_COLUMNS = (
    "month",
    "effective_date",
    "rank",
    "cik",
    "ticker",
    "name",
    "exchange",
    "market_cap",
    "weight",
    "reconstructed",
)
CHANGES_COLUMNS = ("month", "cik", "ticker", "name", "action", "rank")
PRICES_COLUMNS = (
    "cik",
    "ticker",
    "date",
    "open",
    "high",
    "low",
    "close",
    "adj_close",
    "volume",
    "source",
    "prev_date",
    "prev_close",
    "split_ratio",
    "overnight",
    "intraday",
    "daily",
)
ARTICLES_COLUMNS = (
    "article_id",
    "cik",
    "ticker",
    "title",
    "url",
    "publisher",
    "content_type",
    "source",
    "sources",
    "published_at",
    "first_seen_at",
    "trading_day",
    "window",
    "positive",
    "negative",
    "neutral",
    "label",
    "score",
    "model",
)
SENTIMENT_COLUMNS = (
    "date",
    "cik",
    "ticker",
    "window",
    "source",
    "model",
    "n_articles",
    "mean_score",
)
DAILY_COLUMNS = (
    "date",
    "window",
    "return_type",
    "lag",
    "scope",
    "source",
    "method",
    "model",
    "n",
    "estimate",
    "p_value",
    "ci_low",
    "ci_high",
    "tickers",
    "computed_at",
)
ROLLING_KEY = ("as_of", "months", "window", "return_type", "lag", "source", "method")
ROLLING_COLUMNS = (
    "as_of",
    "months",
    "window",
    "return_type",
    "lag",
    "source",
    "method",
    "n",
    "estimate",
    "p_value",
    "ci_low",
    "ci_high",
    "computed_at",
)
LEVEL_COLUMNS = (
    "date",
    "level",
    "return",
    "constituents",
    "priced",
    "weight_priced",
    "rebalance",
)
CONTRIBUTIONS_COLUMNS = ("date", "cik", "ticker", "weight", "return", "contribution", "points")
PLACEBO_COLUMNS = Store.PLACEBO_COLUMNS
PLACEBO_KEY = ("as_of", "months", "window", "return_type", "lag", "source")
ROLLING_TICKERS_KEY = (
    "as_of",
    "months",
    "window",
    "return_type",
    "lag",
    "source",
    "ticker",
    "method",
)
ROLLING_TICKERS_COLUMNS = (
    "as_of",
    "months",
    "window",
    "return_type",
    "lag",
    "source",
    "cik",
    "ticker",
    "method",
    "n",
    "estimate",
    "p_value",
    "ci_low",
    "ci_high",
    "computed_at",
)


def datasets(config: Config) -> list[Dataset]:
    """The published datasets, in manifest order. The index dataset is named after
    ``config.index.name``."""
    index = config.index
    contact = config.datasets.contact
    CC_BY, NEWS_TERMS, SOURCE_TERMS = cc_by(contact), news_terms(contact), source_terms(contact)
    return [
        Dataset(
            "companies",
            "Companies",
            "Every company ranked up to the snapshot month: CIK (the durable identity), "
            "latest ticker and name, exchange, country, first and last month ranked. The "
            "join key supplier for every other dataset.",
            MONTHLY,
            (Table("companies", COMPANIES_COLUMNS, ("cik", "ticker"), q_companies),),
            CC_BY,
        ),
        Dataset(
            index.name,
            index.title,
            f"The {index.size} largest US-domiciled companies on NYSE/Nasdaq by market cap, "
            "rebalanced on the first trading day of each month, with each company's rank "
            "and its weight (share of the total cap). `changes` lists additions and "
            "removals versus the previous month. Months backfilled after the fact are "
            "`reconstructed`: caps from SEC share counts times that day's close, out of "
            "today's candidate pool. `level` (daily): the index level, base "
            f"{config.index.base_level:g} at the close of the base session, chained through "
            "each session's weighted daily return; a month's weights take effect at the "
            "close of its first session. `contributions` (daily): each member's "
            "start-of-day weight, daily return and contribution to the move (in return "
            "units and in index points).",
            MONTHLY,
            (
                Table(
                    "constituents", CONSTITUENTS_COLUMNS, ("month", "cik", "ticker"), q_constituents
                ),
                Table("changes", CHANGES_COLUMNS, ("month", "cik", "ticker", "action"), q_changes),
                Table("level", LEVEL_COLUMNS, ("date",), q_level, cadence=DAILY),
                Table(
                    "contributions",
                    CONTRIBUTIONS_COLUMNS,
                    ("date", "ticker"),
                    q_contributions,
                    cadence=DAILY,
                ),
            ),
            CC_BY,
        ),
        Dataset(
            "prices",
            "Prices",
            "Daily bars (open, high, low, close, adjusted close, volume) and the three "
            "returns computed from them: overnight (open over the previous close), "
            "intraday (close over open) and daily (close over the previous close). "
            "prev_close is split-adjusted when split_ratio is set.",
            DAILY,
            (Table("prices", PRICES_COLUMNS, ("ticker", "date"), q_prices),),
            SOURCE_TERMS,
        ),
        Dataset(
            "news",
            "News",
            "One row per headline and company: title, URL, publisher, which source(s) "
            "reported it, when it was published, the trading day and window it was "
            "assigned to, and the sentiment scores where the headline has been scored.",
            DAILY,
            (Table("articles", ARTICLES_COLUMNS, ("article_id", "ticker"), q_articles),),
            NEWS_TERMS,
        ),
        Dataset(
            "sentiment",
            "Sentiment",
            "Per company, trading day, window (overnight, intraday, day) and source: the "
            "number of headlines and their mean sentiment score. The input to the "
            "correlations.",
            DAILY,
            (
                Table(
                    "sentiment",
                    SENTIMENT_COLUMNS,
                    ("date", "source", "ticker", "window"),
                    q_sentiment,
                ),
            ),
            NEWS_TERMS,
        ),
        Dataset(
            "correlations",
            "Correlations",
            "Does news lead or follow prices? `daily`: each trading day's cross-sectional "
            "correlation of window sentiment with each return type at lags -1, 0 and +1, "
            "pooled and per source. `rolling`: the same over 1, 3, 6 and 12 months, pooled, "
            "as of every session. `rolling_tickers`: the per-company rolling results as of "
            "each month's last session (monthly). `placebo`: the shuffle test, the pooled "
            "day-sentiment/daily-return r next to the r of 200 shuffles of each session's "
            "sentiment across companies. The PNG charts are drawn from these tables.",
            DAILY,
            (
                Table(
                    "daily",
                    DAILY_COLUMNS,
                    ("date", "window", "return_type", "lag", "scope", "source", "method", "model"),
                    q_daily,
                ),
                Table("rolling", ROLLING_COLUMNS, ROLLING_KEY, q_rolling),
                Table("placebo", PLACEBO_COLUMNS, PLACEBO_KEY, q_placebo),
                Table(
                    "rolling_tickers",
                    ROLLING_TICKERS_COLUMNS,
                    ROLLING_TICKERS_KEY,
                    q_rolling_tickers,
                    cadence=MONTHLY,
                ),
            ),
            CC_BY,
            charts=True,
        ),
    ]


def dataset(config: Config, name: str) -> Dataset | None:
    return next((d for d in datasets(config) if d.name == name), None)


# -- paths --------------------------------------------------------------------


def day_dir(base: Path, ds: str, day: date) -> Path:
    return base / ds / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}"


def month_dir(base: Path, ds: str, month: str) -> Path:
    year, mm = month.split("-")
    return base / ds / year / mm


def day_path(base: Path, ds: str, day: date, table: str, ext: str = "parquet") -> Path:
    return day_dir(base, ds, day) / f"{day:%Y%m%d}.{table}.{ext}"


def month_path(base: Path, ds: str, month: str, table: str) -> Path:
    return month_dir(base, ds, month) / f"{month.replace('-', '')}.{table}.parquet"


def _write_parquet(
    path: Path, columns: Sequence[str], rows: list[tuple], meta: Mapping[str, str] | None = None
) -> int:
    import pandas as pd

    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows, columns=list(columns))
    _write_frame(path, df, meta)
    return len(df)


def _write_frame(path: Path, df, meta: Mapping[str, str] | None) -> None:
    """Write a DataFrame as Parquet with the license/provenance metadata in the footer."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.Table.from_pandas(df, preserve_index=False)
    if meta:
        existing = table.schema.metadata or {}
        table = table.replace_schema_metadata(
            {**existing, **{k.encode(): v.encode() for k, v in meta.items()}}
        )
    pq.write_table(table, path)


def read_metadata(path: Path) -> dict[str, str]:
    """The Murmur keys stamped into a Parquet footer (empty for a foreign file)."""
    import pyarrow.parquet as pq

    raw = pq.read_schema(path).metadata or {}
    return {k.decode(): v.decode() for k, v in raw.items() if not k.startswith(b"pandas")}


def _meta(ds: Dataset, table: Table, cadence_key: str) -> dict[str, str]:
    return file_metadata(ds.name, table.name, ds.license, {"murmur_period": cadence_key})


def _write_or_remove(
    path: Path, table: Table, rows: list[tuple], written: list[Path], meta: Mapping[str, str]
) -> None:
    if rows:
        _write_parquet(path, table.columns, rows, meta)
        written.append(path)
    else:
        path.unlink(missing_ok=True)
        _prune(path.parent)


def _prune(d: Path) -> None:
    """Remove ``d`` and its parents while they are empty directories."""
    while d.is_dir() and not any(d.iterdir()):
        d.rmdir()
        d = d.parent


# -- export -------------------------------------------------------------------


def export_day(store: Store, config: Config, day: date, today: date | None = None) -> list[Path]:
    """Write every daily dataset for ``day`` and the monthly datasets for its month,
    compact finished months and rewrite the manifest. Returns the files written."""
    base = config.published_dir
    iso = day.isoformat()
    today = today or today_in(config.timezone)
    written: list[Path] = []

    for ds in datasets(config):
        for table in ds.tables:
            if table_cadence(ds, table) != DAILY:
                continue
            rows = table.query(store, config, iso)
            _write_or_remove(
                day_path(base, ds.name, day, table.name),
                table,
                rows,
                written,
                _meta(ds, table, iso),
            )
        if ds.charts and ds.cadence == DAILY:
            written += _charts(store, config, base, ds.name, day)

    written += export_month(store, config, applicable_month(day))
    written += compact_finished_months(base, config, month_key(today))
    written += export_findings(store, config)
    written.append(write_index(base, config))
    log.info("export: %d files for %s under %s", len(written), iso, base)
    return written


def export_month(store: Store, config: Config, month: str) -> list[Path]:
    """Write every monthly table (companies, the index, the per-company rolling snapshot)
    for ``month``."""
    base = config.published_dir
    written: list[Path] = []
    for ds in datasets(config):
        for table in ds.tables:
            if table_cadence(ds, table) != MONTHLY:
                continue
            rows = table.query(store, config, month)
            _write_or_remove(
                month_path(base, ds.name, month, table.name),
                table,
                rows,
                written,
                _meta(ds, table, month),
            )
    return written


def _charts(store: Store, config: Config, base: Path, ds: str, day: date) -> list[Path]:
    from murmur.analysis import HORIZONS
    from murmur.api import charts

    iso = day.isoformat()
    model = config.sentiment.model
    written: list[Path] = []
    out = day_dir(base, ds, day)

    returns = {r["ticker"]: r for r in store.returns_on(iso)}
    day_rows = [
        r
        for r in store.window_sentiment_on(iso, model)
        if r["window"] == DAY and r["source"] == "all"
    ]
    sentiment_png = day_path(base, ds, day, "sentiment", "png")
    correlation_png = day_path(base, ds, day, "correlation", "png")
    leadlag_png = day_path(base, ds, day, "leadlag", "png")
    if day_rows:
        out.mkdir(parents=True, exist_ok=True)
        sentiment_png.write_bytes(
            charts.sentiment_bar(
                [r["ticker"] for r in day_rows],
                [r["mean_score"] for r in day_rows],
                f"Headline sentiment by company, previous close to close, {iso}",
            )
        )
        written.append(sentiment_png)
        pairs = [
            (r["mean_score"], returns[r["ticker"]]["daily"], r["ticker"])
            for r in day_rows
            if r["ticker"] in returns and returns[r["ticker"]]["daily"] is not None
        ]
        if len(pairs) >= 2:
            pearson = next(
                (
                    c
                    for c in store.correlations_on(iso)
                    if c["method"] == "pearson"
                    and c["window"] == DAY
                    and c["return_type"] == "daily"
                    and c["lag"] == 0
                    and c["source"] == "all"
                ),
                None,
            )
            subtitle = (
                f"Pearson r = {pearson['estimate']:.3f}, p = {pearson['p_value']:.3f}, "
                f"n = {pearson['n']}"
                if pearson
                else "no correlation computed"
            )
            correlation_png.write_bytes(
                charts.scatter(pairs, f"Sentiment vs daily return, {iso}", subtitle)
            )
            written.append(correlation_png)
        else:
            correlation_png.unlink(missing_ok=True)
    else:
        sentiment_png.unlink(missing_ok=True)
        correlation_png.unlink(missing_ok=True)

    pooled = [
        dict(r)
        for r in store.rolling_on(iso)
        if r["scope"] == "pooled" and r["method"] == "pearson" and r["source"] == "all"
    ]
    picked = charts.pick_horizon(pooled, HORIZONS)
    if picked is not None:
        horizon, rows = picked
        out.mkdir(parents=True, exist_ok=True)
        leadlag_png.write_bytes(charts.leadlag(rows, horizon, iso))
        written.append(leadlag_png)
    else:
        leadlag_png.unlink(missing_ok=True)
    _prune(out)
    return written


# -- monthly compaction -------------------------------------------------------


def compact_finished_months(base: Path, config: Config, current_month: str) -> list[Path]:
    """Fold every finished month's day files into one file per table."""
    written = []
    for ds in datasets(config):
        if not any(table_cadence(ds, t) == DAILY for t in ds.tables):
            continue
        root = base / ds.name
        if not root.is_dir():
            continue
        for mdir in sorted(p for p in root.glob("*/*") if p.is_dir()):
            month = f"{mdir.parent.name}-{mdir.name}"
            if month >= current_month or not any(p.is_dir() for p in mdir.iterdir()):
                continue
            for table in ds.tables:
                if table_cadence(ds, table) != DAILY:
                    continue
                out = compact_month(base, ds, table, month)
                if out:
                    written.append(out)
    return written


def compact_month(base: Path, ds: Dataset | str, table: Table, month: str) -> Path | None:
    """Merge a month's ``DD/YYYYMMDD.<table>.parquet`` files (and an existing month
    file) into ``YYYYMM.<table>.parquet``, deduplicated by the table's key."""
    import pandas as pd

    name = ds if isinstance(ds, str) else ds.name
    mdir = month_dir(base, name, month)
    month_file = month_path(base, name, month, table.name)
    daily = sorted(p for p in mdir.glob(f"*/*.{table.name}.parquet") if p.parent.is_dir())
    if not daily:
        return None
    frames = [pd.read_parquet(p) for p in daily]
    if month_file.exists():
        frames.insert(0, pd.read_parquet(month_file))
    df = pd.concat(frames, ignore_index=True)
    key = list(table.key)
    df = df.drop_duplicates(subset=key, keep="last").sort_values(key)
    meta = None if isinstance(ds, str) else _meta(ds, table, month)
    _write_frame(month_file, df, meta)
    for p in daily:
        p.unlink()
        _prune(p.parent)
    log.info(
        "compacted %s/%s %s: %d rows from %d daily files",
        ds,
        table.name,
        month,
        len(df),
        len(daily),
    )
    return month_file


def rebuild_month(store: Store, config: Config, month: str) -> list[Path]:
    """Regenerate a month's files from the store (not a merge), so deletions
    reach the published tree. A finished month ends up compacted, the current
    month as day files."""
    base = config.published_dir
    for ds in datasets(config):
        mdir = month_dir(base, ds.name, month)
        if not mdir.is_dir():
            continue
        for p in mdir.rglob("*.parquet"):
            p.unlink()
        for ddir in (p for p in mdir.iterdir() if p.is_dir()):
            _prune(ddir)
        _prune(mdir)
    today = today_in(config.timezone)
    written: list[Path] = []
    for iso in store.known_days():
        if iso.startswith(month):
            written += export_day(store, config, date.fromisoformat(iso), today=today)
    written += export_month(store, config, month)
    written += compact_finished_months(base, config, month_key(today))
    written.append(write_index(base, config))
    written += update_archives(config, today)
    return sorted(p for p in set(written) if p.exists())


# -- archives -------------------------------------------------------------------


def update_archives(config: Config, today: date | None = None) -> list[Path]:
    """(Re)write ``archives/murmur-YYYYMM.tar.gz`` for every finished month that has no
    archive yet or whose files changed after it was written, then refresh the manifest.
    Called after a day's export, a full export and a month rebuild (not per day)."""
    from murmur import bundle

    base = config.published_dir
    current = month_key(today or today_in(config.timezone))
    m = manifest(base, config)
    newest: dict[str, str] = {}
    for spec in m["datasets"].values():
        if not spec["public"]:
            continue
        for f in spec["files"]:
            month = f["date"][:7]
            if month < current and f["format"] == "parquet":
                newest[month] = max(newest.get(month, ""), f["modified"])
    written = []
    for month, modified in sorted(newest.items()):
        path = base / bundle.ARCHIVES / f"murmur-{month.replace('-', '')}.tar.gz"
        if path.exists():
            stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
            if stamp.strftime("%Y-%m-%dT%H:%M:%SZ") >= modified:
                continue
        out = bundle.month_archive(config, month, m)
        if out is not None:
            written.append(out)
    if written:
        written.append(write_index(base, config))
    return written


# -- findings -------------------------------------------------------------------

FINDINGS_DIR = "findings"


def export_findings(store: Store, config: Config) -> list[Path]:
    """Write every frozen findings snapshot as ``findings/<id>.json`` (with its license
    block) and the newest one again as ``findings/latest.json``. A frozen snapshot never
    changes, so an existing file is left alone; drafts and the live row are not
    published."""
    base = config.published_dir / FINDINGS_DIR
    written: list[Path] = []
    rows = [r for r in store.findings_list() if r["kind"] == "frozen"]
    if not rows:
        return written
    base.mkdir(parents=True, exist_ok=True)
    license = cc_by(config.datasets.contact)
    for i, r in enumerate(rows):  # newest window first
        doc = json.loads(store.findings_get(r["id"])["payload"])
        body = json.dumps({"license": license, **doc}, indent=1) + "\n"
        targets = [base / f"{r['id']}.json"] + ([base / "latest.json"] if i == 0 else [])
        for path in targets:
            if path.exists() and path.read_text(encoding="utf-8") == body:
                continue
            tmp = path.with_suffix(".json.part")
            tmp.write_text(body, encoding="utf-8")
            tmp.replace(path)
            written.append(path)
    return written


def _findings_manifest(base: Path) -> list[dict]:
    root = base / FINDINGS_DIR
    out = []
    if not root.is_dir():
        return out
    for path in sorted(root.glob("*.json")):
        if path.name == "latest.json":
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append(
            {
                "id": doc.get("id", path.stem),
                "title": (doc.get("text") or {}).get("title"),
                "start": doc.get("window", {}).get("start"),
                "end": doc.get("window", {}).get("end"),
                "path": path.relative_to(base).as_posix(),
                "bytes": path.stat().st_size,
            }
        )
    return sorted(out, key=lambda f: f["end"] or "", reverse=True)


# -- manifest -----------------------------------------------------------------


def _row_count(path: Path) -> int | None:
    if path.suffix == ".parquet":
        import pyarrow.parquet as pq

        return pq.read_metadata(path).num_rows
    return None


def _entry(base: Path, path: Path) -> dict:
    rel = path.relative_to(base)
    stamp, table, ext = rel.name.split(".", 2)
    if len(stamp) == 8:
        day = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}"
    else:
        day = f"{stamp[:4]}-{stamp[4:]}"
    st = path.stat()
    return {
        "table": "charts" if ext == "png" else table,
        "date": day,
        "path": rel.as_posix(),
        "format": ext,
        "rows": _row_count(path),
        "bytes": st.st_size,
        "modified": datetime.fromtimestamp(st.st_mtime, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def manifest(base: Path, config: Config) -> dict:
    out = {}
    for ds in datasets(config):
        root = base / ds.name
        files = []
        if root.is_dir():
            files = [
                _entry(base, p)
                for p in sorted(root.rglob("*"))
                if p.is_file() and p.suffix in (".parquet", ".png")
            ]
        dates = [f["date"] for f in files if f["format"] == "parquet"]
        out[ds.name] = {
            "title": ds.title,
            "description": ds.description,
            "cadence": ds.cadence,
            "tables": {
                t.name: {
                    "columns": list(t.columns),
                    "key": list(t.key),
                    "cadence": table_cadence(ds, t),
                }
                for t in ds.tables
            },
            "license": ds.license,
            "public": ds.name not in config.datasets.private,
            "file_count": len(files),
            "bytes": sum(f["bytes"] for f in files),
            "first_date": min(dates) if dates else None,
            "last_date": max(dates) if dates else None,
            "files": files,
        }
    return {
        "murmur_version": __version__,
        "data_version": DATA_VERSION,
        "generated_at": utc_now_iso(),
        "licensing": (
            "Each dataset carries its own license block; the same text is stamped into every "
            "Parquet footer (keys license, license_url, attribution, notice, contact) and sent "
            "as Link/X-Murmur-* headers on /data responses."
        ),
        "layout": {
            "daily": "{dataset}/{YYYY}/{MM}/{DD}/{YYYYMMDD}.{table}.parquet",
            "monthly": "{dataset}/{YYYY}/{MM}/{YYYYMM}.{table}.parquet",
            "csv": "append ?format=csv to any .parquet URL",
        },
        "datasets": out,
        "archives": {
            "description": (
                "One .tar.gz per finished month with every public dataset's tables for that "
                "month, a README with the licenses and SHA256SUMS. Any other range: "
                "GET /api/v1/bundle?from=&to="
            ),
            "files": archives_manifest(base),
        },
        "findings": {
            "description": (
                "Frozen findings snapshots: the conclusion over a window, numbers and prose, "
                "never changed once published. findings/latest.json is the newest."
            ),
            "license": cc_by(config.datasets.contact),
            "files": _findings_manifest(base),
        },
    }


def write_index(base: Path, config: Config) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    path = base / "index.json"
    path.write_text(json.dumps(manifest(base, config), indent=1) + "\n", encoding="utf-8")
    return path
