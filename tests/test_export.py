import json
from datetime import date

import pandas as pd

from murmur import pipeline
from murmur.analysis import analyze_day
from murmur.export import compact_month, export_day
from murmur.universe import record_universe
from tests.test_analysis import COMPANIES, DAY_, SATURDAY, cfg, seed  # noqa: F401

TODAY = date(2026, 11, 5)


def prepared(store, scorer, cfg, day=DAY_, prev=date(2026, 11, 4)):  # noqa: F811
    seed(store, scorer, day=day, prev=prev)
    record_universe(store, cfg, day)
    analyze_day(store, cfg, day, list(COMPANIES))


def test_export_layout(store, scorer, cfg):  # noqa: F811
    prepared(store, scorer, cfg)
    written = export_day(store, cfg, DAY_, today=TODAY)
    base = cfg.published_dir
    rel = sorted(p.relative_to(base).as_posix() for p in written)
    assert rel == [
        "2026/11/05/articles.parquet",
        "2026/11/05/correlation.png",
        "2026/11/05/correlations.parquet",
        "2026/11/05/prices.parquet",
        "2026/11/05/returns.parquet",
        "2026/11/05/sentiment.parquet",
        "2026/11/05/sentiment.png",
        "2026/11/05/window_sentiment.parquet",
        "2026/11/universe.parquet",
        "index.json",
    ]
    articles = pd.read_parquet(base / "2026/11/05/articles.parquet")
    assert len(articles) == 8 and set(articles["window"]) == {"overnight", "intraday"}
    assert list(articles.columns)[:6] == [
        "id",
        "url",
        "title",
        "published_at",
        "trading_day",
        "window",
    ]
    returns = pd.read_parquet(base / "2026/11/05/returns.parquet")
    assert len(returns) == 4 and set(returns.columns) >= {"overnight", "intraday", "daily"}
    assert len(pd.read_parquet(base / "2026/11/05/sentiment.parquet")) == 8
    corr = pd.read_parquet(base / "2026/11/05/correlations.parquet")
    assert len(corr) == 6 and list(corr.columns)[:4] == ["date", "window", "return_type", "lag"]
    ws = pd.read_parquet(base / "2026/11/05/window_sentiment.parquet")
    assert len(ws) == 12 and list(ws.columns) == [
        "date",
        "ticker",
        "window",
        "n_articles",
        "mean_score",
        "overnight",
        "intraday",
        "daily",
    ]
    universe = pd.read_parquet(base / "2026/11/universe.parquet")
    assert list(universe.columns) == ["month", "rank", "ticker", "name", "market_cap", "cik"]
    assert len(universe) == 4

    index = json.loads((base / "index.json").read_text())
    assert index["murmur_version"] == "0.3.0"
    entries = {e["path"]: e for e in index["files"]}
    assert entries["2026/11/05/returns.parquet"] == {
        "table": "returns",
        "date": "2026-11-05",
        "path": "2026/11/05/returns.parquet",
        "format": "parquet",
        "rows": 4,
    }
    assert entries["2026/11/universe.parquet"]["date"] == "2026-11"
    assert entries["2026/11/05/sentiment.png"]["format"] == "png"
    assert "index.json" not in entries


def test_non_trading_day_writes_no_empty_files(store, scorer, cfg):  # noqa: F811
    seed(store, scorer, with_prices=False, day=SATURDAY)
    record_universe(store, cfg, SATURDAY)
    analyze_day(store, cfg, SATURDAY, list(COMPANIES))
    written = export_day(store, cfg, SATURDAY, today=TODAY)
    names = sorted(p.name for p in written)
    assert names == ["index.json", "universe.parquet"]  # only the universe has rows
    assert not (cfg.published_dir / "2026/11/07").exists()


def test_stale_empty_files_are_removed(store, scorer, cfg):  # noqa: F811
    prepared(store, scorer, cfg)
    export_day(store, cfg, DAY_, today=TODAY)
    corr = cfg.published_dir / "2026/11/05/correlations.parquet"
    assert corr.exists()
    store.conn.execute("DELETE FROM correlations")
    export_day(store, cfg, DAY_, today=TODAY)
    assert not corr.exists()
    index = {e["path"] for e in json.loads((cfg.published_dir / "index.json").read_text())["files"]}
    assert "2026/11/05/correlations.parquet" not in index


def test_monthly_compaction(store, scorer, cfg):  # noqa: F811
    # a day in October, exported when "today" is in November -> compacted into month files
    oct30, oct29 = date(2026, 10, 30), date(2026, 10, 29)
    prepared(store, scorer, cfg, day=oct30, prev=oct29)
    written = export_day(store, cfg, oct30, today=TODAY)
    base = cfg.published_dir
    rel = {p.relative_to(base).as_posix() for p in written}
    assert {
        "2026/10/articles.parquet",
        "2026/10/prices.parquet",
        "2026/10/correlations.parquet",
    } <= rel
    assert not (base / "2026/10/30/articles.parquet").exists()
    # the day directory keeps only the charts
    assert sorted(p.name for p in (base / "2026/10/30").iterdir()) == [
        "correlation.png",
        "sentiment.png",
    ]
    assert len(pd.read_parquet(base / "2026/10/articles.parquet")) == 8
    assert len(pd.read_parquet(base / "2026/10/universe.parquet")) == 4
    index = {e["path"]: e for e in json.loads((base / "index.json").read_text())["files"]}
    assert index["2026/10/articles.parquet"] == {
        "table": "articles",
        "date": "2026-10",
        "path": "2026/10/articles.parquet",
        "format": "parquet",
        "rows": 8,
    }
    assert index["2026/10/30/sentiment.png"]["date"] == "2026-10-30"

    # a later backfill of the same month is folded in without duplicates
    export_day(store, cfg, oct30, today=TODAY)
    assert len(pd.read_parquet(base / "2026/10/articles.parquet")) == 8
    assert len(pd.read_parquet(base / "2026/10/returns.parquet")) == 4
    assert len(pd.read_parquet(base / "2026/10/correlations.parquet")) == 6
    assert compact_month(base, "articles", "2026-10") is None  # nothing daily left


def test_current_month_is_not_compacted(store, scorer, cfg):  # noqa: F811
    prepared(store, scorer, cfg)
    export_day(store, cfg, DAY_, today=TODAY)
    assert (cfg.published_dir / "2026/11/05/articles.parquet").exists()
    assert not (cfg.published_dir / "2026/11/articles.parquet").exists()


def test_export_all(store, scorer, cfg):  # noqa: F811
    prepared(store, scorer, cfg)
    written = pipeline.export_all(store, cfg)
    days = {p.relative_to(cfg.published_dir).as_posix() for p in written}
    assert "2026/11/04/prices.parquet" in days and "2026/11/05/prices.parquet" in days
