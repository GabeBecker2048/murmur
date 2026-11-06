"""Publish a trading day's tables under ``<data_dir>/published/``.

Layout: date first, one Parquet file per table.

    index.json                                  every file: table, date, path, format, rows
    2026/11/universe.parquet                    the month's universe
    2026/11/05/prices.parquet                   one trading day, one file per table
    2026/11/05/sentiment.png, correlation.png   charts
    2026/10/prices.parquet                      a finished month, compacted from its days

Finished months are compacted into one file per table; a later backfill is
merged in on the next export, deduplicated by key. Files with no rows are
never written.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from murmur import __version__
from murmur.clock import DAY, month_key, today_in, utc_now_iso
from murmur.config import Config
from murmur.store import Store
from murmur.universe import applicable_month

log = logging.getLogger(__name__)

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
    "window_sentiment": [
        "date",
        "ticker",
        "window",
        "n_articles",
        "mean_score",
        "overnight",
        "intraday",
        "daily",
    ],
    "correlations": [
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
    ],
    "rolling_correlations": [
        "as_of",
        "months",
        "window",
        "return_type",
        "lag",
        "scope",
        "ticker",
        "method",
        "n",
        "estimate",
        "p_value",
        "ci_low",
        "ci_high",
        "computed_at",
    ],
    "universe": ["month", "rank", "ticker", "name", "market_cap", "cik"],
}
KEYS = {
    "articles": ["id"],
    "prices": ["ticker", "date"],
    "returns": ["ticker", "date"],
    "sentiment": ["article_id", "ticker", "model"],
    "window_sentiment": ["date", "ticker", "window"],
    "correlations": ["date", "window", "return_type", "lag", "scope", "source", "method", "model"],
    "rolling_correlations": [
        "as_of",
        "months",
        "window",
        "return_type",
        "lag",
        "scope",
        "ticker",
        "method",
    ],
}
DAY_TABLES = tuple(KEYS)
"""Tables written per trading day and compacted per month, in this order."""


def _write_parquet(path: Path, columns: Sequence[str], rows: list[tuple]) -> int:
    import pandas as pd

    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows, columns=list(columns))
    df.to_parquet(path, index=False, engine="pyarrow")
    return len(df)


def _rows(records: list[sqlite3.Row], columns: Sequence[str]) -> list[tuple]:
    return [tuple(r[c] for c in columns) for r in records]


def day_dir(base: Path, day: date) -> Path:
    return base / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}"


def month_dir(base: Path, month: str) -> Path:
    year, mm = month.split("-")
    return base / year / mm


def export_day(store: Store, config: Config, day: date, today: date | None = None) -> list[Path]:
    base = config.published_dir
    model = config.sentiment.model
    iso = day.isoformat()
    today = today or today_in(config.timezone)
    written: list[Path] = []

    month = applicable_month(day)
    members = store.universe_month(month) or store.universe_for(iso)
    if members:
        p = month_dir(base, month) / "universe.parquet"
        _write_parquet(
            p,
            COLUMNS["universe"],
            [(month, m.rank, m.ticker, m.name, m.market_cap, m.cik) for m in members],
        )
        written.append(p)

    correlations = store.correlations_on(iso)
    rolling = store.rolling_on(iso)
    tables = {
        "articles": _rows(store.articles_on(iso), COLUMNS["articles"]),
        "prices": _rows(store.prices_on(iso), COLUMNS["prices"]),
        "returns": _rows(store.returns_on(iso), COLUMNS["returns"]),
        "sentiment": _rows(store.sentiment_on(iso, model), COLUMNS["sentiment"]),
        "window_sentiment": _per_ticker_table(store, iso, model),
        "correlations": _rows(correlations, COLUMNS["correlations"]),
        "rolling_correlations": _rows(rolling, COLUMNS["rolling_correlations"]),
    }
    # A file with no rows is never written, and a stale one is removed, so the
    # tree only lists days that have data.
    for table, rows in tables.items():
        p = day_dir(base, day) / f"{table}.parquet"
        if rows:
            _write_parquet(p, COLUMNS[table], rows)
            written.append(p)
        else:
            p.unlink(missing_ok=True)

    written += _charts(base, day, tables["window_sentiment"], correlations)
    written += _leadlag_chart(base, day, rolling)
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
                iso,
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
    # per_ticker columns: date, ticker, window, n_articles, mean_score, overnight, intraday, daily
    day_rows = [r for r in per_ticker if r[2] == DAY]
    if not day_rows:
        return []
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    written = []
    out = day_dir(base, day)
    out.mkdir(parents=True, exist_ok=True)
    tickers = [r[1] for r in day_rows]
    scores = [r[4] for r in day_rows]

    fig, ax = plt.subplots(figsize=(max(8, 0.22 * len(tickers)), 4))
    ax.bar(tickers, scores, color=["#2a9d8f" if s >= 0 else "#e76f51" for s in scores])
    ax.axhline(0, color="black", linewidth=0.6)
    ax.set_ylabel("mean sentiment (positive − negative)")
    ax.set_title(f"Headline sentiment by company, previous close to close, {day.isoformat()}")
    ax.tick_params(axis="x", rotation=80, labelsize=7)
    fig.tight_layout()
    p = out / "sentiment.png"
    fig.savefig(p, dpi=120)
    plt.close(fig)
    written.append(p)

    pairs = [(r[4], r[7], r[1]) for r in day_rows if r[7] is not None]
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
        p = out / "correlation.png"
        fig.savefig(p, dpi=120)
        plt.close(fig)
        written.append(p)
    return written


def _leadlag_chart(base: Path, day: date, rolling) -> list[Path]:
    """Pooled Pearson by lag for the three matched pairs, at the longest horizon
    that has every lag."""
    from murmur.analysis import HORIZONS, MATCHED_PAIRS

    pooled = [r for r in rolling if r["scope"] == "pooled" and r["method"] == "pearson"]
    if not pooled:
        return []
    by_h: dict[int, dict[tuple[str, str, int], sqlite3.Row]] = {}
    for r in pooled:
        by_h.setdefault(r["months"], {})[(r["window"], r["return_type"], r["lag"])] = r
    horizon = next(
        (
            h
            for h in sorted(HORIZONS, reverse=True)
            if h in by_h
            and all((w, t, lag) in by_h[h] for w, t in MATCHED_PAIRS for lag in (-1, 0, 1))
        ),
        None,
    )
    if horizon is None:
        return []
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = by_h[horizon]
    fig, ax = plt.subplots(figsize=(7, 4.5))
    colors = {"overnight": "#264653", "intraday": "#2a9d8f", "day": "#e76f51"}
    for i, (window, return_type) in enumerate(MATCHED_PAIRS):
        xs = [lag + (i - 1) * 0.08 for lag in (-1, 0, 1)]
        ys = [rows[(window, return_type, lag)]["estimate"] for lag in (-1, 0, 1)]
        err = [
            [
                ys[j] - (rows[(window, return_type, lag)]["ci_low"] or ys[j])
                for j, lag in enumerate((-1, 0, 1))
            ],
            [
                (rows[(window, return_type, lag)]["ci_high"] or ys[j]) - ys[j]
                for j, lag in enumerate((-1, 0, 1))
            ],
        ]
        n = rows[(window, return_type, 0)]["n"]
        ax.errorbar(
            xs,
            ys,
            yerr=err,
            fmt="o-",
            capsize=3,
            color=colors[window],
            label=f"{window} news vs {return_type} return (n={n})",
        )
    ax.axhline(0, color="black", linewidth=0.6)
    ax.set_xticks([-1, 0, 1])
    ax.set_xticklabels(
        ["-1\nnews after return\n(follows)", "0\nsame session", "+1\nnews before return\n(leads)"]
    )
    ax.set_ylabel("Pearson r, pooled over tickers and days")
    ax.set_title(f"Does news lead or follow prices?  last {horizon} month(s) to {day.isoformat()}")
    ax.legend(fontsize=8)
    fig.tight_layout()
    out = day_dir(base, day)
    out.mkdir(parents=True, exist_ok=True)
    p = out / "leadlag.png"
    fig.savefig(p, dpi=120)
    plt.close(fig)
    return [p]


# -- monthly compaction -------------------------------------------------------


def compact_finished_months(base: Path, current_month: str) -> list[Path]:
    """Fold each finished month's daily files into ``YYYY/MM/<table>.parquet``."""
    written = []
    for mdir in sorted(p for p in base.glob("*/*") if p.is_dir()):
        month = f"{mdir.parent.name}-{mdir.name}"
        if month >= current_month or not any(p.is_dir() for p in mdir.iterdir()):
            continue
        for table in DAY_TABLES:
            out = compact_month(base, table, month)
            if out:
                written.append(out)
    return written


def compact_month(base: Path, table: str, month: str) -> Path | None:
    """Merge a month's ``DD/<table>.parquet`` files (and an existing month file)
    into ``YYYY/MM/<table>.parquet``, deduplicated by key."""
    import pandas as pd

    mdir = month_dir(base, month)
    month_file = mdir / f"{table}.parquet"
    daily = sorted(p for p in mdir.glob(f"*/{table}.parquet") if p.parent.is_dir())
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
        if not any(p.parent.iterdir()):
            p.parent.rmdir()
    log.info("compacted %s %s: %d rows from %d daily files", month, table, len(df), len(daily))
    return month_file


# -- index --------------------------------------------------------------------


def _row_count(path: Path) -> int | None:
    if path.suffix == ".parquet":
        import pyarrow.parquet as pq

        return pq.read_metadata(path).num_rows
    return None


def _entry(base: Path, path: Path) -> dict:
    rel = path.relative_to(base)
    parts = rel.parts  # YYYY/MM/<table>.parquet or YYYY/MM/DD/<table>.<ext>
    if len(parts) == 3:
        day = f"{parts[0]}-{parts[1]}"
    else:
        day = f"{parts[0]}-{parts[1]}-{parts[2]}"
    return {
        "table": "charts" if path.suffix == ".png" else rel.stem,
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
