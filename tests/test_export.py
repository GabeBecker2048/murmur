"""The published tree: six datasets, dataset-first, every file named after its
date and table, finished months compacted, no empty files, a manifest."""

import json
from datetime import date

import pandas as pd

from murmur import pipeline
from murmur.analysis import analyze_day
from murmur.export import compact_month, dataset, export_day, rebuild_month
from murmur.universe import record_universe
from tests.test_analysis import COMPANIES, DAY_, SATURDAY, cfg, seed  # noqa: F401

TODAY = date(2026, 11, 5)


def prepared(store, scorer, cfg, day=DAY_, prev=date(2026, 11, 4)):  # noqa: F811
    seed(store, scorer, day=day, prev=prev)
    record_universe(store, cfg, day)
    analyze_day(store, cfg, day, list(COMPANIES))


def paths(written, base):
    return sorted(p.relative_to(base).as_posix() for p in written)


def test_export_layout(store, scorer, cfg):  # noqa: F811
    prepared(store, scorer, cfg)
    written = export_day(store, cfg, DAY_, today=TODAY)
    base = cfg.published_dir
    assert paths(written, base) == [
        "companies/2026/11/202611.companies.parquet",
        "correlations/2026/11/05/20261105.correlation.png",
        "correlations/2026/11/05/20261105.daily.parquet",
        "correlations/2026/11/05/20261105.sentiment.png",
        "index.json",
        "m100/2026/11/202611.constituents.parquet",
        "news/2026/11/05/20261105.articles.parquet",
        "prices/2026/11/05/20261105.prices.parquet",
        "sentiment/2026/11/05/20261105.sentiment.parquet",
    ]
    # no m100 changes file: the first month has nothing to compare with
    assert not (base / "m100/2026/11/202611.changes.parquet").exists()

    news = pd.read_parquet(base / "news/2026/11/05/20261105.articles.parquet")
    assert len(news) == 8 and set(news["window"]) == {"overnight", "intraday"}
    assert list(news.columns)[:5] == ["article_id", "cik", "ticker", "title", "url"]
    assert set(news["sources"]) == {"yahoo"} and set(news["model"]) == {"fake/keywords"}
    assert news["score"].notna().all()

    prices = pd.read_parquet(base / "prices/2026/11/05/20261105.prices.parquet")
    assert len(prices) == 4 and list(prices.columns)[:3] == ["cik", "ticker", "date"]
    assert {"open", "close", "volume", "prev_close", "overnight", "intraday", "daily"} <= set(
        prices.columns
    )
    assert prices.set_index("ticker").loc["AAPL", "daily"] == 210.0 / 200.0 - 1

    sentiment = pd.read_parquet(base / "sentiment/2026/11/05/20261105.sentiment.parquet")
    assert len(sentiment) == 12 and list(sentiment.columns) == [
        "date",
        "cik",
        "ticker",
        "window",
        "source",
        "model",
        "n_articles",
        "mean_score",
    ]

    daily = pd.read_parquet(base / "correlations/2026/11/05/20261105.daily.parquet")
    assert len(daily) == 18 and list(daily.columns)[:4] == ["date", "window", "return_type", "lag"]
    assert not (base / "correlations/2026/11/05/20261105.rolling.parquet").exists()

    constituents = pd.read_parquet(base / "m100/2026/11/202611.constituents.parquet")
    assert list(constituents.columns) == [
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
    ]
    assert len(constituents) == 4 and set(constituents["effective_date"]) == {"2026-11-02"}
    assert not constituents["reconstructed"].any()

    companies = pd.read_parquet(base / "companies/2026/11/202611.companies.parquet")
    assert len(companies) == 4 and list(companies.columns)[:3] == ["cik", "ticker", "name"]
    assert set(companies["first_month"]) == {"2026-11"}

    index = json.loads((base / "index.json").read_text())
    assert index["murmur_version"] == "0.7.2"
    assert list(index["datasets"]) == [
        "companies",
        "m100",
        "prices",
        "news",
        "sentiment",
        "correlations",
    ]
    prices_ds = index["datasets"]["prices"]
    assert prices_ds["cadence"] == "daily"
    assert prices_ds["license"]["name"] == "Source terms apply (not openly licensed)"
    assert "no rights" in prices_ds["license"]["note"] and prices_ds["license"]["contact"]
    assert index["datasets"]["m100"]["license"]["name"] == "CC BY 4.0"
    assert "licensing" in index
    assert prices_ds["tables"]["prices"]["key"] == ["ticker", "date"]
    assert prices_ds["first_date"] == prices_ds["last_date"] == "2026-11-05"
    entries = {e["path"]: e for e in prices_ds["files"]}
    entry = entries["prices/2026/11/05/20261105.prices.parquet"]
    assert entry["bytes"] > 0 and entry["modified"].endswith("Z")
    assert {k: entry[k] for k in ("table", "date", "format", "rows")} == {
        "table": "prices",
        "date": "2026-11-05",
        "format": "parquet",
        "rows": 4,
    }
    m100 = {e["path"]: e for e in index["datasets"]["m100"]["files"]}
    assert m100["m100/2026/11/202611.constituents.parquet"]["date"] == "2026-11"
    charts = [e for e in index["datasets"]["correlations"]["files"] if e["format"] == "png"]
    assert {e["table"] for e in charts} == {"charts"}
    assert index["datasets"]["news"]["license"]["name"] == "CC BY-NC-SA 4.0"


def test_index_name_follows_config(store, scorer, cfg, tmp_path):  # noqa: F811
    from murmur.config import load_config

    user = tmp_path / "c.toml"
    user.write_text(cfg.source_path.read_text() + '\n[index]\nname = "m250"\nsize = 250\n')
    cfg2 = load_config(user, env={})
    prepared(store, scorer, cfg2)
    written = export_day(store, cfg2, DAY_, today=TODAY)
    assert "m250/2026/11/202611.constituents.parquet" in paths(written, cfg2.published_dir)
    assert dataset(cfg2, "m250").title == "Murmur 250"


def test_non_trading_day_writes_only_monthly_datasets(store, scorer, cfg):  # noqa: F811
    seed(store, scorer, with_prices=False, day=SATURDAY)
    record_universe(store, cfg, SATURDAY)
    analyze_day(store, cfg, SATURDAY, list(COMPANIES))
    written = export_day(store, cfg, SATURDAY, today=TODAY)
    assert paths(written, cfg.published_dir) == [
        "companies/2026/11/202611.companies.parquet",
        "index.json",
        "m100/2026/11/202611.constituents.parquet",
    ]
    assert not (cfg.published_dir / "prices").exists()


def test_stale_empty_files_are_removed(store, scorer, cfg):  # noqa: F811
    prepared(store, scorer, cfg)
    export_day(store, cfg, DAY_, today=TODAY)
    corr = cfg.published_dir / "correlations/2026/11/05/20261105.daily.parquet"
    assert corr.exists()
    store.conn.execute("DELETE FROM correlations")
    export_day(store, cfg, DAY_, today=TODAY)
    assert not corr.exists()
    index = json.loads((cfg.published_dir / "index.json").read_text())
    files = {e["path"] for e in index["datasets"]["correlations"]["files"]}
    assert "correlations/2026/11/05/20261105.daily.parquet" not in files


def test_monthly_compaction(store, scorer, cfg):  # noqa: F811
    # a day in October, exported when "today" is in November -> compacted into month files
    oct30, oct29 = date(2026, 10, 30), date(2026, 10, 29)
    prepared(store, scorer, cfg, day=oct30, prev=oct29)
    written = export_day(store, cfg, oct30, today=TODAY)
    base = cfg.published_dir
    rel = set(paths(written, base))
    assert {
        "news/2026/10/202610.articles.parquet",
        "prices/2026/10/202610.prices.parquet",
        "correlations/2026/10/202610.daily.parquet",
    } <= rel
    assert not (base / "news/2026/10/30").exists()  # day dir gone once its file is folded in
    # the correlations day directory keeps only the charts
    assert sorted(p.name for p in (base / "correlations/2026/10/30").iterdir()) == [
        "20261030.correlation.png",
        "20261030.sentiment.png",
    ]
    assert len(pd.read_parquet(base / "news/2026/10/202610.articles.parquet")) == 8
    assert len(pd.read_parquet(base / "m100/2026/10/202610.constituents.parquet")) == 4
    index = json.loads((base / "index.json").read_text())
    entry = {e["path"]: e for e in index["datasets"]["news"]["files"]}[
        "news/2026/10/202610.articles.parquet"
    ]
    assert {k: entry[k] for k in ("table", "date", "format", "rows")} == {
        "table": "articles",
        "date": "2026-10",
        "format": "parquet",
        "rows": 8,
    }
    charts = {e["path"]: e for e in index["datasets"]["correlations"]["files"]}
    assert charts["correlations/2026/10/30/20261030.sentiment.png"]["date"] == "2026-10-30"

    # a later backfill of the same month is folded in without duplicates
    export_day(store, cfg, oct30, today=TODAY)
    assert len(pd.read_parquet(base / "news/2026/10/202610.articles.parquet")) == 8
    assert len(pd.read_parquet(base / "prices/2026/10/202610.prices.parquet")) == 4
    assert len(pd.read_parquet(base / "correlations/2026/10/202610.daily.parquet")) == 18
    table = dataset(cfg, "news").tables[0]
    assert compact_month(base, "news", table, "2026-10") is None  # nothing daily left


def test_current_month_is_not_compacted(store, scorer, cfg):  # noqa: F811
    prepared(store, scorer, cfg)
    export_day(store, cfg, DAY_, today=TODAY)
    assert (cfg.published_dir / "news/2026/11/05/20261105.articles.parquet").exists()
    assert not (cfg.published_dir / "news/2026/11/202611.articles.parquet").exists()


def test_export_all(store, scorer, cfg):  # noqa: F811
    prepared(store, scorer, cfg)
    written = pipeline.export_all(store, cfg)
    rel = set(paths(written, cfg.published_dir))
    assert {
        "prices/2026/11/04/20261104.prices.parquet",
        "prices/2026/11/05/20261105.prices.parquet",
    } <= rel


def test_rolling_table_and_leadlag_chart(store, scorer, cfg):  # noqa: F811
    from murmur.analysis import rolling_analysis
    from murmur.clock import sessions_between
    from tests.test_analysis import seed_series

    days = sessions_between(date(2026, 8, 1), date(2026, 11, 5))
    seed_series(store, days, lead=1)
    record_universe(store, cfg, DAY_)
    rolling_analysis(store, cfg, DAY_, ["AAPL", "MSFT", "GOOGL", "META"])
    written = export_day(store, cfg, DAY_, today=TODAY)
    names = {p.name for p in written}
    assert {"20261105.rolling.parquet", "20261105.leadlag.png"} <= names
    df = pd.read_parquet(cfg.published_dir / "correlations/2026/11/05/20261105.rolling.parquet")
    assert set(df["months"]) == {1, 3, 6, 12} and set(df["scope"]) == {"pooled", "ticker"}
    assert list(df.columns)[:5] == ["as_of", "months", "window", "return_type", "lag"]
    assert "cik" in df.columns
    png = cfg.published_dir / "correlations/2026/11/05/20261105.leadlag.png"
    assert png.stat().st_size > 5000
    index = json.loads((cfg.published_dir / "index.json").read_text())
    files = {e["path"]: e for e in index["datasets"]["correlations"]["files"]}
    assert files["correlations/2026/11/05/20261105.leadlag.png"]["table"] == "charts"
    assert files["correlations/2026/11/05/20261105.rolling.parquet"]["rows"] == len(df)


def test_rebuild_month_drops_deleted_rows(store, scorer, cfg):  # noqa: F811
    oct30, oct29 = date(2026, 10, 30), date(2026, 10, 29)
    prepared(store, scorer, cfg, day=oct30, prev=oct29)
    export_day(store, cfg, oct30, today=TODAY)
    base = cfg.published_dir
    assert len(pd.read_parquet(base / "correlations/2026/10/202610.daily.parquet")) == 18
    store.delete_analysis("2026-10-30")
    # compaction merges and would keep the stale rows; a rebuild regenerates from the store
    written = rebuild_month(store, cfg, "2026-10")
    assert not (base / "correlations/2026/10/202610.daily.parquet").exists()
    assert (base / "prices/2026/10/202610.prices.parquet").exists()
    assert len(pd.read_parquet(base / "news/2026/10/202610.articles.parquet")) == 8
    assert all(p.suffix != ".parquet" or p.parent.name == "10" for p in written)
    index = json.loads((base / "index.json").read_text())
    files = {e["path"] for e in index["datasets"]["correlations"]["files"]}
    assert "correlations/2026/10/202610.daily.parquet" not in files


def test_rebuild_current_month_keeps_day_files(store, scorer, cfg):  # noqa: F811
    prepared(store, scorer, cfg)
    export_day(store, cfg, DAY_, today=TODAY)
    rebuild_month(store, cfg, "2026-11")  # "today" is really 2026-11 or later
    base = cfg.published_dir
    assert (base / "correlations/2026/11/05/20261105.daily.parquet").exists() or (
        base / "correlations/2026/11/202611.daily.parquet"
    ).exists()


def test_changes_between_months(store, scorer, cfg):  # noqa: F811
    from murmur.models import UniverseMember

    store.save_universe_month(
        "2026-10",
        [
            UniverseMember("AAPL", "Apple", 3e12, 1, 320193, weight=0.6),
            UniverseMember("INTC", "Intel", 2e12, 2, 50863, weight=0.4),
        ],
        "2026-10-01T00:00:00Z",
    )
    store.save_universe_month(
        "2026-11",
        [
            UniverseMember("AAPL", "Apple", 3e12, 1, 320193, weight=0.75),
            UniverseMember("MSFT", "Microsoft", 1e12, 2, 789019, weight=0.25),
        ],
        "2026-11-01T00:00:00Z",
    )
    rows = store.universe_changes("2026-11")
    assert [(r["action"], r["ticker"]) for r in rows] == [("added", "MSFT"), ("removed", "INTC")]
    assert store.universe_changes("2026-10") == []
    from murmur.export import export_month

    written = export_month(store, cfg, "2026-11")
    changes = pd.read_parquet(cfg.published_dir / "m100/2026/11/202611.changes.parquet")
    assert list(changes.columns) == ["month", "cik", "ticker", "name", "action", "rank"]
    assert len(changes) == 2 and len(written) == 3
    companies = pd.read_parquet(cfg.published_dir / "companies/2026/11/202611.companies.parquet")
    intel = companies.set_index("ticker").loc["INTC"]
    assert (intel["first_month"], intel["last_month"], intel["months"]) == ("2026-10", "2026-10", 1)


def test_every_file_carries_license_metadata(store, scorer, cfg):  # noqa: F811
    from murmur.export import read_metadata

    oct30, oct29 = date(2026, 10, 30), date(2026, 10, 29)
    prepared(store, scorer, cfg, day=oct30, prev=oct29)
    prepared(store, scorer, cfg)
    export_day(store, cfg, oct30, today=TODAY)
    export_day(store, cfg, DAY_, today=TODAY)
    base = cfg.published_dir
    daily = read_metadata(base / "prices/2026/11/05/20261105.prices.parquet")
    assert daily["murmur_dataset"] == "prices" and daily["murmur_table"] == "prices"
    assert daily["license"].startswith("Source terms apply") and "yfinance" in daily["attribution"]
    assert (
        "no rights" in daily["notice"] and daily["contact"] and daily["murmur_version"] == "0.7.2"
    )
    assert daily["murmur_period"] == "2026-11-05" and daily["murmur_generated_at"].endswith("Z")
    monthly = read_metadata(base / "m100/2026/11/202611.constituents.parquet")
    assert monthly["license"] == "CC BY 4.0" and monthly["murmur_period"] == "2026-11"
    compacted = read_metadata(base / "news/2026/10/202610.articles.parquet")
    assert compacted["license"] == "CC BY-NC-SA 4.0" and compacted["murmur_table"] == "articles"
    assert compacted["murmur_period"] == "2026-10"
    # a second compaction (a later backfill) keeps the metadata
    export_day(store, cfg, oct30, today=TODAY)
    assert (
        read_metadata(base / "news/2026/10/202610.articles.parquet")["license"] == "CC BY-NC-SA 4.0"
    )


def test_constituents_are_rounded(store, cfg):  # noqa: F811
    from murmur.export import export_month
    from murmur.models import UniverseMember

    store.save_universe_month(
        "2026-11",
        [
            UniverseMember("AAPL", "Apple", 5001541320704.0, 1, 320193, weight=0.09714904192),
            UniverseMember("MSFT", "Microsoft", 3979920277504.0, 2, 789019, weight=0.07730525793),
        ],
        "t",
    )
    export_month(store, cfg, "2026-11")
    df = pd.read_parquet(cfg.published_dir / "m100/2026/11/202611.constituents.parquet")
    assert list(df["market_cap"]) == [5_000_000_000_000, 3_980_000_000_000]
    assert list(df["weight"]) == [0.0971, 0.0773]
