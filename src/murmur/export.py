"""Publish a day's tables as files under ``<data_dir>/published/``.

Layout follows ROADMAP.md. 0.1.0 writes CSV everywhere; 0.3.0 adds Parquet for
the large tables and monthly compaction. ``index.json`` lists every file as
``{table, date, path, format, rows}``.
"""

from __future__ import annotations

import csv
import json
import logging
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import date
from pathlib import Path

from murmur import __version__
from murmur.clock import utc_now_iso, window_bounds
from murmur.config import Config
from murmur.store import Store
from murmur.universe import applicable_month

log = logging.getLogger(__name__)


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


def _rows(records: list[sqlite3.Row], columns: Sequence[str]) -> list[tuple]:
    return [tuple(r[c] for c in columns) for r in records]


def _day_path(base: Path, table: str, day: date, suffix: str) -> Path:
    return base / table / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}{suffix}"


def export_day(store: Store, config: Config, day: date) -> list[Path]:
    base = config.published_dir
    model = config.sentiment.model
    iso = day.isoformat()
    start, end = window_bounds(day, config.timezone)
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

    cols = [
        "id",
        "url",
        "title",
        "published_at",
        "source",
        "provider",
        "content_type",
        "first_seen_at",
        "tickers",
    ]
    p = _day_path(base, "articles", day, ".csv")
    _write_csv(p, cols, _rows(store.articles_in_window(start, end), cols))
    written.append(p)

    cols = ["ticker", "date", "open", "high", "low", "close", "adj_close", "volume", "source"]
    p = _day_path(base, "prices", day, ".csv")
    _write_csv(p, cols, _rows(store.prices_on(iso), cols))
    written.append(p)

    cols = [
        "article_id",
        "ticker",
        "published_at",
        "positive",
        "negative",
        "neutral",
        "label",
        "score",
        "model",
    ]
    p = _day_path(base, "sentiment", day, ".csv")
    _write_csv(p, cols, _rows(store.sentiment_in_window(start, end, model), cols))
    written.append(p)

    cols = [
        "date",
        "window",
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
    p = _day_path(base, "analysis/daily", day, ".csv")
    _write_csv(p, cols, _rows(store.correlations_on(iso), cols))
    written.append(p)

    per_ticker = _per_ticker_table(store, iso, model)
    p = _day_path(base, "analysis/daily", day, "-tickers.csv")
    _write_csv(
        p, ["ticker", "n_articles", "mean_score", "prev_close", "close", "return"], per_ticker
    )
    written.append(p)

    written += _charts(base, day, per_ticker, store.correlations_on(iso))
    _write_index(base)
    written.append(base / "index.json")
    log.info("export: %d files under %s", len(written), base)
    return written


def _per_ticker_table(store: Store, iso: str, model: str) -> list[tuple]:
    closes = store.last_two_closes(iso)
    rows = []
    for r in store.daily_sentiment_on(iso, model):
        prev_close = close = ret = None
        bars = closes.get(r["ticker"], [])
        if len(bars) == 2 and bars[0][0] == iso and bars[1][1]:
            close, prev_close = bars[0][1], bars[1][1]
            ret = close / prev_close - 1
        rows.append((r["ticker"], r["n_articles"], r["mean_score"], prev_close, close, ret))
    return rows


def _charts(base: Path, day: date, per_ticker: list[tuple], correlations) -> list[Path]:
    if not per_ticker:
        return []
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    written = []
    tickers = [r[0] for r in per_ticker]
    scores = [r[2] for r in per_ticker]

    fig, ax = plt.subplots(figsize=(max(8, 0.22 * len(tickers)), 4))
    ax.bar(tickers, scores, color=["#2a9d8f" if s >= 0 else "#e76f51" for s in scores])
    ax.axhline(0, color="black", linewidth=0.6)
    ax.set_ylabel("mean sentiment (positive − negative)")
    ax.set_title(f"Headline sentiment by company, {day.isoformat()}")
    ax.tick_params(axis="x", rotation=80, labelsize=7)
    fig.tight_layout()
    p = _day_path(base, "analysis/daily", day, "-sentiment.png")
    fig.savefig(p, dpi=120)
    plt.close(fig)
    written.append(p)

    pairs = [(r[2], r[5], r[0]) for r in per_ticker if r[5] is not None]
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
        pearson = next((c for c in correlations if c["method"] == "pearson"), None)
        if pearson:
            subtitle = (
                f"Pearson r = {pearson['estimate']:.3f}, "
                f"p = {pearson['p_value']:.3f}, n = {pearson['n']}"
            )
        else:
            subtitle = "no correlation computed"
        ax.set_xlabel("mean sentiment (positive − negative)")
        ax.set_ylabel("daily return (%)")
        ax.set_title(f"Sentiment vs daily return, {day.isoformat()}\n{subtitle}")
        fig.tight_layout()
        p = _day_path(base, "analysis/daily", day, "-correlation.png")
        fig.savefig(p, dpi=120)
        plt.close(fig)
        written.append(p)
    return written


def _write_index(base: Path) -> None:
    files = []
    for path in sorted(p for p in base.rglob("*") if p.is_file() and p.name != "index.json"):
        rel = path.relative_to(base)
        parts = rel.parts
        table = "/".join(parts[:-1]) if parts[0] != "analysis" else "/".join(parts[:2])
        if parts[0] == "universe":
            table, day = "universe", rel.stem
        elif parts[0] == "analysis":
            day = f"{parts[2]}-{parts[3]}-{rel.stem[:2]}"
        else:
            table = parts[0]
            day = f"{parts[1]}-{parts[2]}-{rel.stem[:2]}"
        rows = None
        if path.suffix == ".csv":
            with path.open(encoding="utf-8") as f:
                rows = max(sum(1 for _ in f) - 1, 0)
        files.append(
            {
                "table": table,
                "date": day,
                "path": rel.as_posix(),
                "format": path.suffix.lstrip("."),
                "rows": rows,
            }
        )
    index = {"murmur_version": __version__, "generated_at": utc_now_iso(), "files": files}
    (base / "index.json").write_text(json.dumps(index, indent=1) + "\n", encoding="utf-8")
