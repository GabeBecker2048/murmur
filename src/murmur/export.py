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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from murmur import __version__
from murmur.clock import DAY, first_trading_day, month_key, today_in, utc_now_iso
from murmur.config import Config
from murmur.store import Store
from murmur.universe import applicable_month

log = logging.getLogger(__name__)

DAILY = "daily"
MONTHLY = "monthly"

CC_BY = {
    "name": "CC BY 4.0",
    "url": "https://creativecommons.org/licenses/by/4.0/",
    "attribution": "Murmur (github.com/GabeBecker2048/murmur)",
}
CC_BY_NC_SA = {
    "name": "CC BY-NC-SA 4.0",
    "url": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
    "attribution": "Murmur (github.com/GabeBecker2048/murmur); scores by ProsusAI/finbert",
    "note": "Scores come from FinBERT, which is licensed CC BY-NC-SA 4.0.",
}


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[str, ...]
    key: tuple[str, ...]
    query: Callable[[Store, Config, str], list[tuple]]
    """``(store, config, day_iso | month)`` -> rows in ``columns`` order."""


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
    return [
        (
            month,
            effective,
            m.rank,
            m.cik,
            m.ticker,
            m.name,
            m.exchange,
            int(m.market_cap) if m.market_cap is not None else None,
            m.weight,
        )
        for m in members
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


def q_rolling(store: Store, config: Config, iso: str) -> list[tuple]:
    cik_of = store.ticker_ciks()
    out = []
    for r in store.rolling_on(iso):
        out.append(
            tuple(
                (cik_of.get(r["ticker"]) if r["ticker"] else None) if c == "cik" else r[c]
                for c in ROLLING_COLUMNS
            )
        )
    return out


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
ROLLING_KEY = (
    "as_of",
    "months",
    "window",
    "return_type",
    "lag",
    "source",
    "scope",
    "ticker",
    "method",
)
ROLLING_COLUMNS = (
    "as_of",
    "months",
    "window",
    "return_type",
    "lag",
    "source",
    "scope",
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
            "removals versus the previous month.",
            MONTHLY,
            (
                Table(
                    "constituents", CONSTITUENTS_COLUMNS, ("month", "cik", "ticker"), q_constituents
                ),
                Table("changes", CHANGES_COLUMNS, ("month", "cik", "ticker", "action"), q_changes),
            ),
            CC_BY,
        ),
        Dataset(
            "prices",
            "Prices",
            "Daily bars (open, high, low, close, adjusted close, volume) and the three "
            "returns computed from them: overnight (open over the previous close), "
            "intraday (close over open) and daily (close over the previous close).",
            DAILY,
            (Table("prices", PRICES_COLUMNS, ("ticker", "date"), q_prices),),
            CC_BY,
        ),
        Dataset(
            "news",
            "News",
            "One row per headline and company: title, URL, publisher, which source(s) "
            "reported it, when it was published, the trading day and window it was "
            "assigned to, and the sentiment scores where the headline has been scored.",
            DAILY,
            (Table("articles", ARTICLES_COLUMNS, ("article_id", "ticker"), q_articles),),
            CC_BY_NC_SA,
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
            CC_BY_NC_SA,
        ),
        Dataset(
            "correlations",
            "Correlations",
            "Does news lead or follow prices? `daily`: each trading day's cross-sectional "
            "correlation of window sentiment with each return type at lags -1, 0 and +1, "
            "pooled and per source. `rolling`: the same over 1, 3, 6 and 12 months, pooled "
            "and per company. The PNG charts are drawn from these tables.",
            DAILY,
            (
                Table(
                    "daily",
                    DAILY_COLUMNS,
                    ("date", "window", "return_type", "lag", "scope", "source", "method", "model"),
                    q_daily,
                ),
                Table(
                    "rolling",
                    ROLLING_COLUMNS,
                    (
                        "as_of",
                        "months",
                        "window",
                        "return_type",
                        "lag",
                        "source",
                        "scope",
                        "ticker",
                        "method",
                    ),
                    q_rolling,
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


def _write_parquet(path: Path, columns: Sequence[str], rows: list[tuple]) -> int:
    import pandas as pd

    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows, columns=list(columns))
    df.to_parquet(path, index=False, engine="pyarrow")
    return len(df)


def _write_or_remove(path: Path, table: Table, rows: list[tuple], written: list[Path]) -> None:
    if rows:
        _write_parquet(path, table.columns, rows)
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
        if ds.cadence != DAILY:
            continue
        for table in ds.tables:
            rows = table.query(store, config, iso)
            _write_or_remove(day_path(base, ds.name, day, table.name), table, rows, written)
        if ds.charts:
            written += _charts(store, config, base, ds.name, day)

    written += export_month(store, config, applicable_month(day))
    written += compact_finished_months(base, config, month_key(today))
    written.append(write_index(base, config))
    log.info("export: %d files for %s under %s", len(written), iso, base)
    return written


def export_month(store: Store, config: Config, month: str) -> list[Path]:
    """Write the monthly datasets (companies, the index) for ``month``."""
    base = config.published_dir
    written: list[Path] = []
    for ds in datasets(config):
        if ds.cadence != MONTHLY:
            continue
        for table in ds.tables:
            rows = table.query(store, config, month)
            _write_or_remove(month_path(base, ds.name, month, table.name), table, rows, written)
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
        if ds.cadence != DAILY:
            continue
        root = base / ds.name
        if not root.is_dir():
            continue
        for mdir in sorted(p for p in root.glob("*/*") if p.is_dir()):
            month = f"{mdir.parent.name}-{mdir.name}"
            if month >= current_month or not any(p.is_dir() for p in mdir.iterdir()):
                continue
            for table in ds.tables:
                out = compact_month(base, ds.name, table, month)
                if out:
                    written.append(out)
    return written


def compact_month(base: Path, ds: str, table: Table, month: str) -> Path | None:
    """Merge a month's ``DD/YYYYMMDD.<table>.parquet`` files (and an existing month
    file) into ``YYYYMM.<table>.parquet``, deduplicated by the table's key."""
    import pandas as pd

    mdir = month_dir(base, ds, month)
    month_file = month_path(base, ds, month, table.name)
    daily = sorted(p for p in mdir.glob(f"*/*.{table.name}.parquet") if p.parent.is_dir())
    if not daily:
        return None
    frames = [pd.read_parquet(p) for p in daily]
    if month_file.exists():
        frames.insert(0, pd.read_parquet(month_file))
    df = pd.concat(frames, ignore_index=True)
    key = list(table.key)
    df = df.drop_duplicates(subset=key, keep="last").sort_values(key)
    df.to_parquet(month_file, index=False, engine="pyarrow")
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
    return sorted(p for p in set(written) if p.exists())


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
            "tables": {t.name: {"columns": list(t.columns), "key": list(t.key)} for t in ds.tables},
            "license": ds.license,
            "file_count": len(files),
            "bytes": sum(f["bytes"] for f in files),
            "first_date": min(dates) if dates else None,
            "last_date": max(dates) if dates else None,
            "files": files,
        }
    return {
        "murmur_version": __version__,
        "generated_at": utc_now_iso(),
        "layout": {
            "daily": "{dataset}/{YYYY}/{MM}/{DD}/{YYYYMMDD}.{table}.parquet",
            "monthly": "{dataset}/{YYYY}/{MM}/{YYYYMM}.{table}.parquet",
            "csv": "append ?format=csv to any .parquet URL",
        },
        "datasets": out,
    }


def write_index(base: Path, config: Config) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    path = base / "index.json"
    path.write_text(json.dumps(manifest(base, config), indent=1) + "\n", encoding="utf-8")
    return path
