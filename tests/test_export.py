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
        "analysis/daily/2026/11/05-correlation.png",
        "analysis/daily/2026/11/05-sentiment.png",
        "analysis/daily/2026/11/05-tickers.csv",
        "analysis/daily/2026/11/05.csv",
        "articles/2026/11/05.parquet",
        "index.json",
        "prices/2026/11/05.parquet",
        "returns/2026/11/05.parquet",
        "sentiment/2026/11/05.parquet",
        "universe/2026-11.csv",
    ]
    articles = pd.read_parquet(base / "articles/2026/11/05.parquet")
    assert len(articles) == 8 and set(articles["window"]) == {"overnight", "intraday"}
    assert list(articles.columns)[:6] == [
        "id",
        "url",
        "title",
        "published_at",
        "trading_day",
        "window",
    ]
    returns = pd.read_parquet(base / "returns/2026/11/05.parquet")
    assert len(returns) == 4 and set(returns.columns) >= {"overnight", "intraday", "daily"}
    sentiment = pd.read_parquet(base / "sentiment/2026/11/05.parquet")
    assert len(sentiment) == 8
    corr = (base / "analysis/daily/2026/11/05.csv").read_text().splitlines()
    assert corr[0].startswith("date,window,return_type,lag,") and len(corr) == 7
    tickers_csv = (base / "analysis/daily/2026/11/05-tickers.csv").read_text().splitlines()
    assert tickers_csv[0] == "ticker,window,n_articles,mean_score,overnight,intraday,daily"
    assert len(tickers_csv) == 13

    index = json.loads((base / "index.json").read_text())
    assert index["murmur_version"] == "0.3.0"
    entries = {e["path"]: e for e in index["files"]}
    assert entries["returns/2026/11/05.parquet"] == {
        "table": "returns",
        "date": "2026-11-05",
        "path": "returns/2026/11/05.parquet",
        "format": "parquet",
        "rows": 4,
    }
    assert entries["analysis/daily/2026/11/05.csv"]["rows"] == 6
    assert entries["universe/2026-11.csv"]["date"] == "2026-11"


def test_non_trading_day_exports_empty_tables_and_no_charts(store, scorer, cfg):  # noqa: F811
    seed(store, scorer, with_prices=False, day=SATURDAY)
    record_universe(store, cfg, SATURDAY)
    analyze_day(store, cfg, SATURDAY, list(COMPANIES))
    written = export_day(store, cfg, SATURDAY, today=TODAY)
    names = {p.name for p in written}
    assert "07.parquet" in names and not any(n.endswith(".png") for n in names)
    assert len(pd.read_parquet(cfg.published_dir / "articles/2026/11/07.parquet")) == 0


def test_monthly_compaction(store, scorer, cfg):  # noqa: F811
    # a day in October, exported when "today" is in November -> compacted into month files
    oct30, oct29 = date(2026, 10, 30), date(2026, 10, 29)
    prepared(store, scorer, cfg, day=oct30, prev=oct29)
    written = export_day(store, cfg, oct30, today=TODAY)
    base = cfg.published_dir
    rel = {p.relative_to(base).as_posix() for p in written}
    assert "articles/2026/10.parquet" in rel and "prices/2026/10.parquet" in rel
    assert not (base / "articles/2026/10").exists()
    assert not (base / "articles/2026/10/30.parquet").exists()
    assert len(pd.read_parquet(base / "articles/2026/10.parquet")) == 8
    # small tables stay daily CSV
    assert (base / "analysis/daily/2026/10/30.csv").exists()
    index = {e["path"]: e for e in json.loads((base / "index.json").read_text())["files"]}
    assert index["articles/2026/10.parquet"] == {
        "table": "articles",
        "date": "2026-10",
        "path": "articles/2026/10.parquet",
        "format": "parquet",
        "rows": 8,
    }

    # a later backfill of the same month is folded in without duplicates
    export_day(store, cfg, oct30, today=TODAY)
    assert len(pd.read_parquet(base / "articles/2026/10.parquet")) == 8
    assert len(pd.read_parquet(base / "returns/2026/10.parquet")) == 4
    assert compact_month(base, "articles", "2026-10") is None  # nothing daily left


def test_current_month_is_not_compacted(store, scorer, cfg):  # noqa: F811
    prepared(store, scorer, cfg)
    export_day(store, cfg, DAY_, today=TODAY)
    assert (cfg.published_dir / "articles/2026/11/05.parquet").exists()
    assert not (cfg.published_dir / "articles/2026/11.parquet").exists()


def test_export_all(store, scorer, cfg):  # noqa: F811
    prepared(store, scorer, cfg)
    written = pipeline.export_all(store, cfg)
    days = {p.relative_to(cfg.published_dir).as_posix() for p in written}
    assert "prices/2026/11/04.parquet" in days and "prices/2026/11/05.parquet" in days
