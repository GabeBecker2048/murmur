"""Publish a trading day's tables as files under ``<data_dir>/published/``.

Layout from ROADMAP.md: Parquet for the large tables (articles, prices,
returns, sentiment), one file per day, compacted into one file per month once
the month is over; CSV for the small ones (universe, daily analysis).
``index.json`` lists every file as ``{table, date, path, format, rows}``.
"""

from __future__ import annotations

import csv
import json
import logging
import shutil
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import date
from pathlib import Path

from murmur import __version__
from murmur.clock import DAY, month_key, today_in, utc_now_iso
from murmur.config import Config
from murmur.store import Store
from murmur.universe import applicable_month

log = logging.getLogger(__name__)

PARQUET_TABLES = ("articles", "prices", "returns", "sentiment")
KEYS = {
    "articles": ["id"],
    "prices": ["ticker", "date"],
    "returns": ["ticker", "date"],
    "sentiment": ["article_id", "ticker", "model"],
}
COLUMNS = {
    "articles": [
        "id",
        "url",
        "title",
        "published_at",
        "trading_day",
        "window",
        "source",
        "provider",
        "content_type",
        "first_seen_at",
        "tickers",
    ],
    "prices": ["ticker", "date", "open", "high", "low", "close", "adj_close", "volume", "source"],
    "returns": [
        "ticker",
        "date",
        "prev_date",
        "prev_close",
        "open",
        "close",
        "overnight",
        "intraday",
        "daily",
    ],
    "sentiment": [
        "article_id",
        "ticker",
        "trading_day",
        "window",
        "published_at",
        "positive",
        "negative",
        "neutral",
        "label",
        "score",
        "model",
    ],
}
CORRELATION_COLUMNS = [
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
]


def _write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for row in rows:
            w.writerow(row)
            n += 1
    return n


def _write_parquet(path: Path, columns: Sequence[str], rows: list[tuple]) -> int:
    import pandas as pd

    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows, columns=list(columns))
    df.to_parquet(path, index=False, engine="pyarrow")
    return len(df)


def _rows(records: list[sqlite3.Row], columns: Sequence[str]) -> list[tuple]:
    return [tuple(r[c] for c in columns) for r in records]


def _day_path(base: Path, table: str, day: date, suffix: str) -> Path:
    return base / table / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}{suffix}"


def export_day(store: Store, config: Config, day: date, today: date | None = None) -> list[Path]:
    base = config.published_dir
    model = config.sentiment.model
    iso = day.isoformat()
    today = today or today_in(config.timezone)
    written: list[Path] = []

    members = store.universe_for(iso)
    if members:
        p = base / "universe" / f"{applicable_month(day)}.csv"
        _write_csv(
            p,
            ["ticker", "name", "market_cap", "rank"],
            [(m.ticker, m.name, m.market_cap, m.rank) for m in members],
        )
        written.append(p)

    tables = {
        "articles": store.articles_on(iso),
        "prices": store.prices_on(iso),
        "returns": store.returns_on(iso),
        "sentiment": store.sentiment_on(iso, model),
    }
    for table, records in tables.items():
        p = _day_path(base, table, day, ".parquet")
        _write_parquet(p, COLUMNS[table], _rows(records, COLUMNS[table]))
        written.append(p)

    correlations = store.correlations_on(iso)
    p = _day_path(base, "analysis/daily", day, ".csv")
    _write_csv(p, CORRELATION_COLUMNS, _rows(correlations, CORRELATION_COLUMNS))
    written.append(p)

    per_ticker = _per_ticker_table(store, iso, model)
    p = _day_path(base, "analysis/daily", day, "-tickers.csv")
    _write_csv(
        p,
        ["ticker", "window", "n_articles", "mean_score", "overnight", "intraday", "daily"],
        per_ticker,
    )
    written.append(p)

    written += _charts(base, day, per_ticker, correlations)
    written += compact_finished_months(base, month_key(today))
    _write_index(base)
    written.append(base / "index.json")
    log.info("export: %d files for %s under %s", len(written), iso, base)
    return written


def _per_ticker_table(store: Store, iso: str, model: str) -> list[tuple]:
    returns = {r["ticker"]: r for r in store.returns_on(iso)}
    rows = []
    for r in store.window_sentiment_on(iso, model):
        ret = returns.get(r["ticker"])
        rows.append(
            (
                r["ticker"],
                r["window"],
                r["n_articles"],
                r["mean_score"],
                ret["overnight"] if ret else None,
                ret["intraday"] if ret else None,
                ret["daily"] if ret else None,
            )
        )
    return rows


def _charts(base: Path, day: date, per_ticker: list[tuple], correlations) -> list[Path]:
    day_rows = [r for r in per_ticker if r[1] == DAY]
    if not day_rows:
        return []
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    written = []
    tickers = [r[0] for r in day_rows]
    scores = [r[3] for r in day_rows]

    fig, ax = plt.subplots(figsize=(max(8, 0.22 * len(tickers)), 4))
    ax.bar(tickers, scores, color=["#2a9d8f" if s >= 0 else "#e76f51" for s in scores])
    ax.axhline(0, color="black", linewidth=0.6)
    ax.set_ylabel("mean sentiment (positive − negative)")
    ax.set_title(f"Headline sentiment by company, previous close to close, {day.isoformat()}")
    ax.tick_params(axis="x", rotation=80, labelsize=7)
    fig.tight_layout()
    p = _day_path(base, "analysis/daily", day, "-sentiment.png")
    fig.savefig(p, dpi=120)
    plt.close(fig)
    written.append(p)

    pairs = [(r[3], r[6], r[0]) for r in day_rows if r[6] is not None]
    if len(pairs) >= 2:
        xs = [p[0] for p in pairs]
        ys = [p[1] * 100 for p in pairs]
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.scatter(xs, ys, color="#264653")
        for x, y, t in zip(xs, ys, (p[2] for p in pairs), strict=True):
            ax.annotate(t, (x, y), fontsize=6, xytext=(2, 2), textcoords="offset points")
        if len(set(xs)) > 1:
            import numpy as np

            slope, intercept = np.polyfit(xs, ys, 1)
            grid = np.linspace(min(xs), max(xs), 2)
            ax.plot(grid, slope * grid + intercept, color="#e76f51", linewidth=1)
        pearson = next(
            (
                c
                for c in correlations
                if c["method"] == "pearson" and c["window"] == DAY and c["return_type"] == "daily"
            ),
            None,
        )
        if pearson:
            subtitle = (
                f"Pearson r = {pearson['estimate']:.3f}, "
                f"p = {pearson['p_value']:.3f}, n = {pearson['n']}"
            )
        else:
            subtitle = "no correlation computed"
        ax.set_xlabel("mean sentiment, previous close to close")
        ax.set_ylabel("daily return (%)")
        ax.set_title(f"Sentiment vs daily return, {day.isoformat()}\n{subtitle}")
        fig.tight_layout()
        p = _day_path(base, "analysis/daily", day, "-correlation.png")
        fig.savefig(p, dpi=120)
        plt.close(fig)
        written.append(p)
    return written


# -- monthly compaction -------------------------------------------------------


def compact_finished_months(base: Path, current_month: str) -> list[Path]:
    """Fold each finished month's daily Parquet files into ``table/YYYY/MM.parquet``.

    A daily file written later for a compacted month (a backfill) is folded in
    on the next call: the month file is re-read, rows deduplicated by key.
    """
    written = []
    for table in PARQUET_TABLES:
        root = base / table
        if not root.is_dir():
            continue
        for month_dir in sorted(p for p in root.glob("*/*") if p.is_dir()):
            month = f"{month_dir.parent.name}-{month_dir.name}"
            if month >= current_month:
                continue
            out = compact_month(base, table, month)
            if out:
                written.append(out)
    return written


def compact_month(base: Path, table: str, month: str) -> Path | None:
    import pandas as pd

    year, mm = month.split("-")
    month_dir = base / table / year / mm
    month_file = base / table / year / f"{mm}.parquet"
    daily = sorted(month_dir.glob("*.parquet")) if month_dir.is_dir() else []
    if not daily:
        return None
    frames = [pd.read_parquet(p) for p in daily]
    if month_file.exists():
        frames.insert(0, pd.read_parquet(month_file))
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=KEYS[table], keep="last").sort_values(KEYS[table])
    df.to_parquet(month_file, index=False, engine="pyarrow")
    for p in daily:
        p.unlink()
    shutil.rmtree(month_dir, ignore_errors=True)
    log.info("compacted %s/%s: %d rows from %d daily files", table, month, len(df), len(daily))
    return month_file


# -- index --------------------------------------------------------------------


def _row_count(path: Path) -> int | None:
    if path.suffix == ".csv":
        with path.open(encoding="utf-8") as f:
            return max(sum(1 for _ in f) - 1, 0)
    if path.suffix == ".parquet":
        import pyarrow.parquet as pq

        return pq.read_metadata(path).num_rows
    return None


def _entry(base: Path, path: Path) -> dict:
    rel = path.relative_to(base)
    parts = rel.parts
    if parts[0] == "universe":
        table, day = "universe", rel.stem
    elif parts[0] == "analysis":
        table, day = "/".join(parts[:2]), f"{parts[2]}-{parts[3]}-{rel.stem[:2]}"
    elif len(parts) == 3:  # table/YYYY/MM.parquet (compacted month)
        table, day = parts[0], f"{parts[1]}-{rel.stem}"
    else:
        table, day = parts[0], f"{parts[1]}-{parts[2]}-{rel.stem[:2]}"
    return {
        "table": table,
        "date": day,
        "path": rel.as_posix(),
        "format": path.suffix.lstrip("."),
        "rows": _row_count(path),
    }


def _write_index(base: Path) -> None:
    files = [
        _entry(base, p)
        for p in sorted(p for p in base.rglob("*") if p.is_file() and p.name != "index.json")
    ]
    index = {"murmur_version": __version__, "generated_at": utc_now_iso(), "files": files}
    (base / "index.json").write_text(json.dumps(index, indent=1) + "\n", encoding="utf-8")
