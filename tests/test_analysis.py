"""The old R pipeline joined sentiment and prices by row position, with stock
rows sorted by ticker and sentiment rows sorted by company name. These tests use
companies whose names sort differently from their tickers and prove each
ticker meets its own return, per window."""

from datetime import date

import pytest
from scipy import stats

from murmur.analysis import analyze_day, assign_windows, compute_returns, returns_available
from murmur.clock import DAY, INTRADAY, OVERNIGHT
from murmur.config import load_config
from murmur.models import Article, PriceBar
from murmur.sentiment import score_pending

DAY_ = date(2026, 11, 5)  # Thursday, a trading day; previous session Wed the 4th
SATURDAY = date(2026, 11, 7)
TZ = "America/New_York"

# ticker -> (name, prev_close, open, close, overnight headline, intraday headline).
# Names sort Alphabet, Apple, Meta, Microsoft; tickers sort AAPL, GOOGL, META, MSFT.
COMPANIES = {
    "GOOGL": ("Alphabet", 100.0, 95.0, 90.0, "Alphabet plunges after lawsuit", "Alphabet falls"),
    "AAPL": ("Apple", 200.0, 205.0, 210.0, "Apple beats estimates", "Apple surges on record"),
    "MSFT": ("Microsoft", 400.0, 400.0, 400.0, "Microsoft holds steady", "Microsoft unchanged"),
    "META": ("Meta", 500.0, 490.0, 475.0, "Meta falls on downgrade", "Meta misses"),
}
OVERNIGHT_AT = "T12:00:00Z"  # 07:00 ET, before the open
INTRADAY_AT = "T16:00:00Z"  # 11:00 ET


@pytest.fixture
def cfg(tmp_path):
    user = tmp_path / "c.toml"
    lines = [f'data_dir = "{tmp_path / "data"}"', "[sentiment]", 'model = "fake/keywords"']
    lines += ["[universe]", 'source = "static"', "[universe.static]"]
    lines += [f'{t} = "{v[0]}"' for t, v in COMPANIES.items()]
    user.write_text("\n".join(lines) + "\n")
    return load_config(user, env={})


def seed(store, scorer, tickers=COMPANIES, with_prices=True, day=DAY_, prev=date(2026, 11, 4)):
    # insert in an order that matches neither tickers nor names
    for ticker in ["MSFT", "GOOGL", "META", "AAPL"]:
        if ticker not in tickers:
            continue
        _, prev_close, open_, close, overnight, intraday = tickers[ticker]
        store.upsert_article(
            Article(f"https://e/{ticker}/o", overnight, f"{day}{OVERNIGHT_AT}", "yahoo"),
            ticker,
            f"{day}T23:00:00Z",
        )
        store.upsert_article(
            Article(f"https://e/{ticker}/i", intraday, f"{day}{INTRADAY_AT}", "yahoo"),
            ticker,
            f"{day}T23:00:00Z",
        )
        if with_prices:
            store.upsert_prices(
                [
                    PriceBar(ticker, prev.isoformat(), prev_close, None, None, prev_close, None, 1),
                    PriceBar(ticker, day.isoformat(), open_, None, None, close, None, 1),
                ],
                "test",
            )
    score_pending(store, scorer)


def test_returns_per_ticker_from_open_and_close(store, scorer, cfg):
    seed(store, scorer)
    result = analyze_day(store, cfg, DAY_, list(COMPANIES))
    assert result.trading_day
    assert set(result.returns) == set(COMPANIES)
    for ticker, (_, prev_close, open_, close, _, _) in COMPANIES.items():
        r = result.returns[ticker]
        assert r.overnight == pytest.approx(open_ / prev_close - 1)
        assert r.intraday == pytest.approx(close / open_ - 1)
        assert r.daily == pytest.approx(close / prev_close - 1)
        assert r.prev_date == "2026-11-04"
    stored = {r["ticker"]: r for r in store.returns_on("2026-11-05")}
    assert stored["GOOGL"]["overnight"] == pytest.approx(-0.05)
    assert stored["GOOGL"]["daily"] == pytest.approx(-0.10)


def test_windows_and_pairs(store, scorer, cfg):
    seed(store, scorer)
    result = analyze_day(store, cfg, DAY_, list(COMPANIES))
    assert set(result.sentiment) == {OVERNIGHT, INTRADAY, DAY}
    assert result.sentiment[OVERNIGHT]["AAPL"] == (1, pytest.approx(0.7))
    assert result.sentiment[DAY]["AAPL"] == (2, pytest.approx(0.7))
    assert result.sentiment[DAY]["MSFT"] == (2, pytest.approx(0.0))
    by = {w: {p.ticker: p for p in pairs} for w, pairs in result.pairs.items()}
    assert by[OVERNIGHT]["GOOGL"].ret == pytest.approx(-0.05)
    assert by[INTRADAY]["GOOGL"].ret == pytest.approx(90 / 95 - 1)
    assert by[DAY]["GOOGL"].ret == pytest.approx(-0.10)
    assert by[DAY]["AAPL"].sentiment > 0 and by[DAY]["AAPL"].ret > 0
    assert by[DAY]["META"].sentiment < 0 and by[DAY]["META"].ret < 0
    rows = store.window_sentiment_on("2026-11-05", cfg.sentiment.model)
    assert len(rows) == 12  # 4 tickers x 3 windows
    art = store.conn.execute("SELECT trading_day, window FROM articles ORDER BY id").fetchall()
    assert {(a[0], a[1]) for a in art} == {("2026-11-05", OVERNIGHT), ("2026-11-05", INTRADAY)}


def test_correlations_per_window_match_scipy(store, scorer, cfg):
    seed(store, scorer)
    result = analyze_day(store, cfg, DAY_, list(COMPANIES))
    assert len(result.correlations) == 6  # 3 window/return pairs x 2 methods
    for c in result.correlations:
        pairs = result.pairs[c.window]
        x = [p.sentiment for p in pairs]
        y = [p.ret for p in pairs]
        expected = stats.pearsonr(x, y) if c.method == "pearson" else stats.spearmanr(x, y)
        assert c.estimate == pytest.approx(expected.statistic)
        assert c.p_value == pytest.approx(expected.pvalue)
        assert c.n == 4 and c.lag == 0
        assert c.tickers == ("AAPL", "GOOGL", "META", "MSFT")
    pairs = {(c.window, c.return_type) for c in result.correlations}
    assert pairs == {(OVERNIGHT, "overnight"), (INTRADAY, "intraday"), (DAY, "daily")}
    stored = store.correlations_on("2026-11-05")
    assert len(stored) == 6
    assert {r["return_type"] for r in stored} == {"overnight", "intraday", "daily"}
    pearson_day = next(c for c in result.correlations if c.window == DAY and c.method == "pearson")
    assert pearson_day.estimate > 0.9
    assert pearson_day.ci_low < pearson_day.estimate < pearson_day.ci_high


def test_rerun_is_idempotent(store, scorer, cfg):
    seed(store, scorer)
    analyze_day(store, cfg, DAY_, list(COMPANIES))
    analyze_day(store, cfg, DAY_, list(COMPANIES))
    assert store.count("correlations") == 6
    assert store.count("window_sentiment") == 12
    assert store.count("returns") == 4


def test_non_trading_day_assigns_windows_but_stores_nothing(store, scorer, cfg):
    seed(store, scorer, with_prices=False, day=SATURDAY)
    result = analyze_day(store, cfg, SATURDAY, list(COMPANIES))
    assert not result.trading_day and "not a trading day" in result.note
    assert result.correlations == [] and result.sentiment == {}
    assert store.count("window_sentiment") == 0 and store.count("returns") == 0
    # Saturday's articles belong to Monday's overnight window
    art = store.conn.execute("SELECT DISTINCT trading_day, window FROM articles").fetchall()
    assert [tuple(a) for a in art] == [("2026-11-09", OVERNIGHT)]


def test_trading_day_without_bars_is_an_error(store, scorer, cfg):
    seed(store, scorer, with_prices=False)
    with pytest.raises(RuntimeError, match="no price bars for trading day"):
        analyze_day(store, cfg, DAY_, list(COMPANIES))


def test_ticker_without_previous_bar_has_no_return(store, scorer, cfg):
    seed(store, scorer)
    store.conn.execute("DELETE FROM prices WHERE ticker = 'MSFT' AND date = '2026-11-04'")
    result = analyze_day(store, cfg, DAY_, list(COMPANIES))
    assert "MSFT" not in result.returns
    assert [p.ticker for p in result.pairs[DAY]] == ["AAPL", "GOOGL", "META"]
    assert all(c.n == 3 for c in result.correlations)


def test_returns_available_and_compute_returns_direct(store):
    store.upsert_prices([PriceBar("AAPL", "2026-11-04", 10, None, None, 10, None, 1)], "t")
    assert not returns_available(store, DAY_)
    store.upsert_prices([PriceBar("AAPL", "2026-11-05", 11, None, None, 12, None, 1)], "t")
    assert returns_available(store, DAY_)
    assert not returns_available(store, SATURDAY)
    r = compute_returns(store, DAY_)["AAPL"]
    assert (r.overnight, r.intraday, r.daily) == (
        pytest.approx(0.1),
        pytest.approx(12 / 11 - 1),
        pytest.approx(0.2),
    )
    assert compute_returns(store, date(2026, 11, 4)) == {}  # no bar before the 4th


def test_too_few_pairs(store, scorer, cfg):
    two = {t: COMPANIES[t] for t in ("AAPL", "GOOGL")}
    seed(store, scorer, tickers=two)
    result = analyze_day(store, cfg, DAY_, list(two))
    assert result.correlations == []
    assert "has 2 pairs" in result.note


def test_assign_windows_skips_undated(store):
    store.upsert_article(Article("https://e/1", "x", None, "y"), "AAPL", "t")
    store.upsert_article(Article("https://e/2", "x", "2026-11-05T15:00:00Z", "y"), "AAPL", "t")
    assert assign_windows(store, TZ) == 1
    assert assign_windows(store, TZ) == 0
