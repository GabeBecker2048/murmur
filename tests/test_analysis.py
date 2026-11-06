"""The old R pipeline joined sentiment and prices by row position, with stock
rows sorted by ticker and sentiment rows sorted by company name. These tests use
companies whose names sort differently from their tickers and prove each
ticker meets its own return."""

from datetime import date

import pytest
from scipy import stats

from murmur.analysis import analyze_day, daily_returns
from murmur.config import load_config
from murmur.models import Article, PriceBar
from murmur.sentiment import score_pending

DAY = date(2026, 11, 5)  # Thursday, a trading day
SATURDAY = date(2026, 11, 7)
TZ = "America/New_York"

# ticker -> (name, headline, prev_close, close). Names sort: Alphabet, Apple, Meta,
# Microsoft; tickers sort: AAPL, GOOGL, META, MSFT. A positional join misaligns them.
COMPANIES = {
    "GOOGL": ("Alphabet", "Alphabet plunges after lawsuit", 100.0, 90.0),
    "AAPL": ("Apple", "Apple beats estimates, shares surge", 200.0, 210.0),
    "MSFT": ("Microsoft", "Microsoft holds steady", 400.0, 400.0),
    "META": ("Meta", "Meta falls on downgrade", 500.0, 475.0),
}


@pytest.fixture
def cfg(tmp_path):
    user = tmp_path / "c.toml"
    lines = ["[sentiment]", 'model = "fake/keywords"', "[universe]", 'source = "static"']
    lines += ["[universe.static]"]
    lines += [f'{t} = "{v[0]}"' for t, v in COMPANIES.items()]
    user.write_text("\n".join(lines) + "\n")
    return load_config(user, env={})


def seed(store, scorer, tickers=COMPANIES, with_prices=True, day=DAY):
    # insert in an order that matches neither tickers nor names
    for ticker in ["MSFT", "GOOGL", "META", "AAPL"]:
        if ticker not in tickers:
            continue
        name, headline, prev_close, close = tickers[ticker]
        store.upsert_article(
            Article(f"https://e/{ticker}", headline, f"{day}T15:00:00Z", "yahoo"),
            ticker,
            f"{day}T23:00:00Z",
        )
        if with_prices:
            store.upsert_prices(
                [
                    PriceBar(ticker, "2026-11-04", None, None, None, prev_close, None, None),
                    PriceBar(ticker, "2026-11-05", None, None, None, close, None, None),
                ],
                "test",
            )
    score_pending(store, scorer)


def test_each_ticker_meets_its_own_return(store, scorer, cfg):
    seed(store, scorer)
    result = analyze_day(store, cfg, DAY, list(COMPANIES))
    assert result.trading_day
    by_ticker = {p.ticker: p for p in result.pairs}
    assert set(by_ticker) == set(COMPANIES)
    for ticker, (_, _, prev_close, close) in COMPANIES.items():
        pair = by_ticker[ticker]
        assert pair.ret == pytest.approx(close / prev_close - 1)
        assert (pair.prev_close, pair.close) == (prev_close, close)
    # positive headline -> positive return, negative -> negative, under the fake scorer
    assert by_ticker["AAPL"].sentiment > 0 and by_ticker["AAPL"].ret > 0
    assert by_ticker["GOOGL"].sentiment < 0 and by_ticker["GOOGL"].ret < 0
    assert by_ticker["META"].sentiment < 0 and by_ticker["META"].ret < 0
    assert by_ticker["MSFT"].sentiment == 0 and by_ticker["MSFT"].ret == 0


def test_correlations_match_scipy_on_named_pairs(store, scorer, cfg):
    seed(store, scorer)
    result = analyze_day(store, cfg, DAY, list(COMPANIES))
    x = [p.sentiment for p in result.pairs]
    y = [p.ret for p in result.pairs]
    expected = {"pearson": stats.pearsonr(x, y), "spearman": stats.spearmanr(x, y)}
    assert {c.method for c in result.correlations} == {"pearson", "spearman"}
    for c in result.correlations:
        assert c.n == 4
        assert c.estimate == pytest.approx(expected[c.method].statistic)
        assert c.p_value == pytest.approx(expected[c.method].pvalue)
        assert c.tickers == ("AAPL", "GOOGL", "META", "MSFT")
    pearson = next(c for c in result.correlations if c.method == "pearson")
    assert pearson.estimate > 0.9
    assert pearson.ci_low is not None and pearson.ci_low < pearson.estimate < pearson.ci_high

    stored = store.correlations_on("2026-11-05")
    assert {r["method"]: r["estimate"] for r in stored} == {
        c.method: pytest.approx(c.estimate) for c in result.correlations
    }
    assert all(r["window"] == "day" and r["scope"] == "pooled" for r in stored)
    assert len(store.daily_sentiment_on("2026-11-05", cfg.sentiment.model)) == 4


def test_rerun_is_idempotent(store, scorer, cfg):
    seed(store, scorer)
    analyze_day(store, cfg, DAY, list(COMPANIES))
    analyze_day(store, cfg, DAY, list(COMPANIES))
    assert store.count("correlations") == 2
    assert store.count("daily_sentiment") == 4


def test_non_trading_day_stores_sentiment_but_no_correlation(store, scorer, cfg):
    seed(store, scorer, with_prices=False, day=SATURDAY)
    result = analyze_day(store, cfg, SATURDAY, list(COMPANIES))
    assert not result.trading_day
    assert "not a trading day" in result.note
    assert len(result.sentiment) == 4
    assert result.correlations == [] and result.pairs == []
    assert store.count("daily_sentiment") == 4
    assert store.count("correlations") == 0


def test_trading_day_without_bars_is_an_error(store, scorer, cfg):
    seed(store, scorer, with_prices=False)
    with pytest.raises(RuntimeError, match="no price bars for trading day"):
        analyze_day(store, cfg, DAY, list(COMPANIES))
    assert store.count("daily_sentiment") == 4  # sentiment was still stored first


def test_too_few_pairs(store, scorer, cfg):
    two = {t: COMPANIES[t] for t in ("AAPL", "GOOGL")}
    seed(store, scorer, tickers=two)
    result = analyze_day(store, cfg, DAY, list(two))
    assert result.trading_day and len(result.pairs) == 2
    assert result.correlations == []
    assert "only 2 tickers" in result.note


def test_tickers_outside_universe_are_ignored(store, scorer, cfg):
    seed(store, scorer)
    result = analyze_day(store, cfg, DAY, ["AAPL", "GOOGL", "META"])
    assert set(result.sentiment) == {"AAPL", "GOOGL", "META"}
    assert [p.ticker for p in result.pairs] == ["AAPL", "GOOGL", "META"]


def test_daily_returns_require_a_bar_on_the_day(store):
    store.upsert_prices(
        [
            PriceBar("AAPL", "2026-11-03", None, None, None, 10, None, None),
            PriceBar("AAPL", "2026-11-04", None, None, None, 11, None, None),
            PriceBar("MSFT", "2026-11-04", None, None, None, 50, None, None),
        ],
        "test",
    )
    assert daily_returns(store, date(2026, 11, 4)) == {"AAPL": (10, 11)}  # MSFT has no prev
    assert daily_returns(store, date(2026, 11, 5)) == {}  # no bar on the 5th


def test_articles_outside_the_local_day_are_excluded(store, scorer, cfg):
    # 03:00 UTC on the 8th is still 22:00 ET on Saturday the 7th; 05:00 UTC is the 8th in ET.
    store.upsert_article(
        Article("https://e/1", "Apple beats", "2026-11-08T03:00:00Z", "y"), "AAPL", "t"
    )
    store.upsert_article(
        Article("https://e/2", "Apple misses", "2026-11-08T05:00:00Z", "y"), "AAPL", "t"
    )
    store.upsert_article(Article("https://e/3", "Apple misses", None, "y"), "AAPL", "t")
    score_pending(store, scorer)
    result = analyze_day(store, cfg, SATURDAY, ["AAPL"])
    assert result.sentiment == {"AAPL": (1, pytest.approx(0.7))}
