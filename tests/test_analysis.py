"""The old R pipeline joined sentiment and prices by row position, with stock
rows sorted by ticker and sentiment rows sorted by company name. These tests use
companies whose names sort differently from their tickers and prove each
ticker meets its own return, per window."""

from datetime import date

import pytest
from scipy import stats

from murmur.analysis import (
    analyze_day,
    assign_windows,
    compute_returns,
    returns_available,
    rolling_analysis,
)
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
    by = {
        (w, rt): {p.ticker: p for p in result.pairs[(w, rt, 0)]}
        for w, rt in ((OVERNIGHT, "overnight"), (INTRADAY, "intraday"), (DAY, "daily"))
    }
    assert by[(OVERNIGHT, "overnight")]["GOOGL"].ret == pytest.approx(-0.05)
    assert by[(INTRADAY, "intraday")]["GOOGL"].ret == pytest.approx(90 / 95 - 1)
    assert by[(DAY, "daily")]["GOOGL"].ret == pytest.approx(-0.10)
    assert by[(DAY, "daily")]["AAPL"].sentiment > 0 and by[(DAY, "daily")]["AAPL"].ret > 0
    assert by[(DAY, "daily")]["META"].sentiment < 0 and by[(DAY, "daily")]["META"].ret < 0
    # the cross product is there too: overnight news against the daily return
    assert (
        by[(OVERNIGHT, "overnight")]["AAPL"].sentiment
        == result.pairs[(OVERNIGHT, "daily", 0)][0].sentiment
    )
    rows = store.window_sentiment_on("2026-11-05", cfg.sentiment.model)
    assert len(rows) == 12  # 4 tickers x 3 windows
    art = store.conn.execute("SELECT trading_day, window FROM articles ORDER BY id").fetchall()
    assert {(a[0], a[1]) for a in art} == {("2026-11-05", OVERNIGHT), ("2026-11-05", INTRADAY)}


def test_correlations_cross_product_match_scipy(store, scorer, cfg):
    seed(store, scorer)
    result = analyze_day(store, cfg, DAY_, list(COMPANIES))
    # only lag 0 has returns (no session before the 4th, none after the 5th yet):
    # 3 windows x 3 return types x 2 methods
    assert len(result.correlations) == 18
    assert {c.lag for c in result.correlations} == {0}
    for c in result.correlations:
        pairs = result.pairs[(c.window, c.return_type, c.lag)]
        x = [p.sentiment for p in pairs]
        y = [p.ret for p in pairs]
        expected = stats.pearsonr(x, y) if c.method == "pearson" else stats.spearmanr(x, y)
        assert c.estimate == pytest.approx(expected.statistic)
        assert c.p_value == pytest.approx(expected.pvalue)
        assert c.n == 4
        assert c.tickers == ("AAPL", "GOOGL", "META", "MSFT")
    assert {(c.window, c.return_type) for c in result.matched()} == {
        (OVERNIGHT, "overnight"),
        (INTRADAY, "intraday"),
        (DAY, "daily"),
    }
    stored = store.correlations_on("2026-11-05")
    assert len(stored) == 18
    pearson_day = next(c for c in result.matched() if c.window == DAY)
    assert pearson_day.estimate > 0.9
    assert pearson_day.ci_low < pearson_day.estimate < pearson_day.ci_high


def test_lagged_pairs_use_the_neighbouring_sessions(store, scorer, cfg):
    """Thursday's news against Friday's return is lag +1; against Wednesday's, lag -1."""
    wed, thu, fri = date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 6)
    # bars for Tue..Fri so Wed, Thu and Fri all have returns; articles on Thu only
    for i, (ticker, (_, prev_close, open_, close, _, _)) in enumerate(COMPANIES.items()):
        # per-ticker gaps so no return type is constant across tickers on any day
        g1, g2 = 1 + 0.01 * (i + 1), 1 + 0.02 * (i + 1)
        store.upsert_prices(
            [
                PriceBar(ticker, "2026-11-03", prev_close, None, None, prev_close * g2, None, 1),
                PriceBar(ticker, "2026-11-04", prev_close * g1, None, None, prev_close, None, 1),
                PriceBar(ticker, "2026-11-05", open_, None, None, close, None, 1),  # as COMPANIES
                PriceBar(ticker, "2026-11-06", close / g1, None, None, prev_close, None, 1),
            ],
            "t",
        )
    seed(store, scorer, with_prices=False)  # Thursday's articles
    analyze_day(store, cfg, wed, list(COMPANIES))
    analyze_day(store, cfg, fri, list(COMPANIES))
    result = analyze_day(store, cfg, thu, list(COMPANIES))
    assert {c.lag for c in result.correlations} == {-1, 0, 1}
    assert len(result.correlations) == 54  # 3 x 3 x 3 lags x 2 methods
    lead = {p.ticker: p for p in result.pairs[(DAY, "daily", 1)]}
    lagged = {p.ticker: p for p in result.pairs[(DAY, "daily", -1)]}
    _, prev_close, _, close, _, _ = COMPANIES["AAPL"]
    assert lead["AAPL"].ret == pytest.approx(prev_close / close - 1)  # Friday's daily return
    assert lagged["AAPL"].ret == pytest.approx(1 / 1.04 - 1)  # Wednesday's (AAPL is index 1)
    assert lead["AAPL"].sentiment == lagged["AAPL"].sentiment  # Thursday's sentiment both times
    stored = store.correlations_on("2026-11-05")
    assert {r["lag"] for r in stored} == {-1, 0, 1}


def test_rerun_is_idempotent(store, scorer, cfg):
    seed(store, scorer)
    analyze_day(store, cfg, DAY_, list(COMPANIES))
    analyze_day(store, cfg, DAY_, list(COMPANIES))
    assert store.count("correlations") == 18
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
    assert [p.ticker for p in result.pairs[(DAY, "daily", 0)]] == ["AAPL", "GOOGL", "META"]
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


def seed_series(
    store, days, tickers=("AAPL", "MSFT", "GOOGL", "META"), lead=1, model="fake/keywords"
):
    """Synthetic history: each ticker's `day` sentiment on session i equals its
    daily return on session i + lead (news leads prices by `lead` sessions);
    the intraday window carries the opposite sign."""
    import random

    rng = random.Random(7)
    rets = {t: [rng.uniform(-0.05, 0.05) for _ in days] for t in tickers}
    for t in tickers:
        store.upsert_returns(
            (t, d.isoformat(), d.isoformat(), 100.0, 100.0, 100.0, r / 2, r / 2, r)
            for d, r in zip(days, rets[t], strict=True)
        )
        store.upsert_prices([PriceBar(t, d.isoformat(), 1, 1, 1, 1, 1, 1) for d in days], "t")
    for i, d in enumerate(days):
        if i + lead >= len(days):
            continue
        scores = {t: rets[t][i + lead] for t in tickers}
        store.upsert_window_sentiment(
            d.isoformat(), DAY, model, {t: (3, v) for t, v in scores.items()}
        )
        store.upsert_window_sentiment(
            d.isoformat(), OVERNIGHT, model, {t: (1, v) for t, v in scores.items()}
        )
        store.upsert_window_sentiment(
            d.isoformat(), INTRADAY, model, {t: (2, -v) for t, v in scores.items()}
        )


def test_rolling_finds_the_lead(store, cfg):
    from murmur.clock import sessions_between

    days = sessions_between(date(2026, 8, 1), date(2026, 11, 5))  # ~68 sessions
    seed_series(store, days, lead=1)
    rows = rolling_analysis(store, cfg, date(2026, 11, 5), ["AAPL", "MSFT", "GOOGL", "META"])
    by = {(r.months, r.window, r.return_type, r.lag, r.scope, r.ticker, r.method): r for r in rows}
    # pooled, 3 months, day window vs daily return: lag +1 is the planted relationship
    assert by[(3, DAY, "daily", 1, "pooled", "", "pearson")].estimate == pytest.approx(1.0)
    assert abs(by[(3, DAY, "daily", 0, "pooled", "", "pearson")].estimate) < 0.5
    assert abs(by[(3, DAY, "daily", -1, "pooled", "", "pearson")].estimate) < 0.5
    # the intraday window was planted with the opposite sign
    assert by[(3, INTRADAY, "daily", 1, "pooled", "", "pearson")].estimate == pytest.approx(-1.0)
    # per ticker too, and Spearman agrees
    assert by[(3, DAY, "daily", 1, "ticker", "AAPL", "pearson")].estimate == pytest.approx(1.0)
    assert by[(3, DAY, "daily", 1, "ticker", "AAPL", "spearman")].estimate == pytest.approx(1.0)
    # the 1-month horizon only sees ~24 sessions x 4 tickers = enough pooled, enough per ticker
    assert (1, DAY, "daily", 1, "pooled", "", "pearson") in by
    assert (
        by[(1, DAY, "daily", 1, "pooled", "", "pearson")].n
        < by[(3, DAY, "daily", 1, "pooled", "", "pearson")].n
    )
    # 12 months pooled covers everything we seeded (sessions from August)
    assert (
        by[(12, DAY, "daily", 1, "pooled", "", "pearson")].n
        == by[(6, DAY, "daily", 1, "pooled", "", "pearson")].n
    )
    stored = store.rolling_on("2026-11-05")
    assert len(stored) == len(rows)
    assert {r["months"] for r in stored} == {1, 3, 6, 12}


def test_rolling_respects_minimum_n(store, cfg):
    from murmur.clock import sessions_between

    days = sessions_between(date(2026, 10, 26), date(2026, 11, 5))  # 9 sessions
    seed_series(store, days, tickers=("AAPL", "MSFT"), lead=0)
    rows = rolling_analysis(store, cfg, date(2026, 11, 5), ["AAPL", "MSFT"])
    assert rows == []  # 18 pooled observations < 30, 9 per ticker < 10
    assert store.rolling_on("2026-11-05") == []


def test_rolling_replaces_rows_for_the_day(store, cfg):
    from murmur.clock import sessions_between

    days = sessions_between(date(2026, 8, 1), date(2026, 11, 5))
    seed_series(store, days, lead=1)
    first = rolling_analysis(store, cfg, date(2026, 11, 5), ["AAPL", "MSFT", "GOOGL", "META"])
    second = rolling_analysis(store, cfg, date(2026, 11, 5), ["AAPL", "MSFT"])
    assert len(second) < len(first)
    assert store.count("rolling_correlations") == len(second)


def test_per_source_correlations_once_two_sources_report(store, scorer, cfg):
    seed(store, scorer)  # everything from "yahoo"
    result = analyze_day(store, cfg, DAY_, list(COMPANIES))
    assert {c.source for c in result.correlations} == {"all"}
    # a second source reports the same (deduplicated) headlines for two tickers,
    # plus one of its own
    for ticker in ("AAPL", "GOOGL"):
        _, _, _, _, overnight, _ = COMPANIES[ticker]
        store.upsert_article(
            Article(f"https://f/{ticker}", overnight, f"{DAY_}{OVERNIGHT_AT}", "finnhub"),
            ticker,
            "t",
        )
    store.upsert_article(
        Article("https://f/META/x", "Meta plunges", f"{DAY_}{INTRADAY_AT}", "finnhub"), "META", "t"
    )
    score_pending(store, scorer)
    result = analyze_day(store, cfg, DAY_, list(COMPANIES))
    assert {c.source for c in result.correlations} == {"all", "yahoo", "finnhub"}
    assert set(result.by_source) == {"yahoo", "finnhub"}
    assert set(result.by_source["finnhub"][DAY]) == {"AAPL", "GOOGL", "META"}
    assert set(result.by_source["yahoo"][DAY]) == set(COMPANIES)
    assert store.count("articles") == 9  # 8 + META's extra; the two shared ones merged
    rows = store.window_sentiment_on("2026-11-05", cfg.sentiment.model)
    assert {r["source"] for r in rows} == {"all", "yahoo", "finnhub"}
    stored = store.correlations_on("2026-11-05")
    assert {r["source"] for r in stored} == {"all", "yahoo", "finnhub"}
    assert len(result.matched(source="all")) == 3


def test_rolling_analysis_prunes_old_per_ticker_rows(store, cfg, tmp_path):
    from murmur.clock import sessions_between
    from murmur.config import load_config

    user = tmp_path / "k.toml"
    user.write_text(
        cfg.source_path.read_text() + "\n[analysis]\nkeep_ticker_rolling_sessions = 2\n"
    )
    cfg2 = load_config(user, env={})
    days = sessions_between(date(2026, 8, 1), date(2026, 11, 5))
    seed_series(store, days, lead=1)
    tickers = ["AAPL", "MSFT", "GOOGL", "META"]
    for d in days[-4:]:
        rolling_analysis(store, cfg2, d, tickers)
    as_ofs = sorted(
        r["as_of"]
        for r in store.conn.execute(
            "SELECT DISTINCT as_of FROM rolling_correlations WHERE scope = 'ticker'"
        )
    )
    pooled = sorted(
        r["as_of"]
        for r in store.conn.execute(
            "SELECT DISTINCT as_of FROM rolling_correlations WHERE scope = 'pooled'"
        )
    )
    assert len(pooled) == 4  # pooled history complete
    # the two latest sessions always; an older one only if it is its month's last session
    assert pooled[-2:] == as_ofs[-2:] and len(as_ofs) <= 3


def test_placebo_separates_the_planted_signal_from_shuffles(store, cfg):
    from murmur.analysis import placebo_analysis
    from murmur.clock import sessions_between

    days = sessions_between(date(2026, 8, 1), date(2026, 11, 5))
    seed_series(store, days, lead=1)
    tickers = ["AAPL", "MSFT", "GOOGL", "META"]
    rows = placebo_analysis(store, cfg, date(2026, 11, 5), tickers)
    by = {(r.months, r.lag): r for r in rows}
    assert {r.window for r in rows} == {DAY} and {r.return_type for r in rows} == {"daily"}
    planted = by[(3, 1)]
    assert planted.real_r == pytest.approx(1.0)
    assert planted.null_high < 0.9 and planted.z > 5 and planted.p_perm == pytest.approx(1 / 201)
    assert abs(by[(3, 0)].z) < 4  # no signal planted at lag 0
    again = placebo_analysis(store, cfg, date(2026, 11, 5), tickers)
    assert [r.null_mean for r in again] == [r.null_mean for r in rows]  # seeded by the date
    assert len(store.placebo_on("2026-11-05")) == len(rows)


def test_shuffled_null_stays_within_sessions():
    import numpy as np

    from murmur.analysis import shuffled_null

    rng = np.random.default_rng(0)
    # one company per session: every shuffle is the identity, so the null equals the real r
    x = rng.normal(size=50)
    y = x + rng.normal(size=50)
    null = shuffled_null(x, y, np.arange(50), 20, rng)
    assert np.allclose(null, np.corrcoef(x, y)[0, 1])
