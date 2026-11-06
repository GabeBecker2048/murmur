import pytest

from murmur.models import Article, PriceBar, SentimentScores, UniverseMember
from murmur.store import SCHEMA_VERSION, Store


def article(url="https://x/1", title="Apple beats", published="2026-11-05T14:00:00Z"):
    return Article(url=url, title=title, published_at=published, source="yahoo", provider="P")


def test_schema_version_and_file_store(tmp_path):
    with Store(tmp_path / "sub" / "m.sqlite") as s:
        assert s.schema_version == SCHEMA_VERSION
    with Store(tmp_path / "sub" / "m.sqlite") as s:  # reopening does not re-migrate
        assert s.schema_version == SCHEMA_VERSION


def test_article_upsert_is_idempotent_and_keeps_earliest(store):
    aid, new = store.upsert_article(article(), "AAPL", "2026-11-05T20:00:00Z")
    assert new
    aid2, new2 = store.upsert_article(
        article(published="2026-11-05T13:00:00Z", title="changed"), "MSFT", "2026-11-06T20:00:00Z"
    )
    assert aid2 == aid and not new2
    assert store.count("articles") == 1
    assert store.count("article_tickers") == 2
    row = store.conn.execute("SELECT * FROM articles").fetchone()
    assert row["published_at"] == "2026-11-05T13:00:00Z"  # earliest wins
    assert row["title"] == "Apple beats"  # first title kept
    assert row["first_seen_at"] == "2026-11-05T20:00:00Z"  # never overwritten
    assert row["title_norm"] == "apple beats"


def test_null_published_at_does_not_clobber(store):
    store.upsert_article(article(), "AAPL", "t")
    store.upsert_article(article(published=None), "AAPL", "t")
    assert store.conn.execute("SELECT published_at FROM articles").fetchone()[0] == (
        "2026-11-05T14:00:00Z"
    )


def test_price_upsert_overwrites(store):
    bar = PriceBar("AAPL", "2026-11-05", 1, 2, 0.5, 1.5, 1.5, 100)
    store.upsert_prices([bar], "yfinance")
    store.upsert_prices([PriceBar("AAPL", "2026-11-05", 1, 2, 0.5, 1.7, 1.7, 120)], "yfinance")
    assert store.count("prices") == 1
    assert store.prices_on("2026-11-05")[0]["close"] == 1.7


def test_bars_on_and_known_days(store):
    bars = [
        PriceBar("AAPL", "2026-11-04", None, None, None, 11, None, None),
        PriceBar("AAPL", "2026-11-05", None, None, None, 12, None, None),
        PriceBar("MSFT", "2026-11-05", None, None, None, 50, None, None),
    ]
    store.upsert_prices(bars, "yfinance")
    assert set(store.bars_on("2026-11-05")) == {"AAPL", "MSFT"}
    assert store.bars_on("2026-11-05")["AAPL"]["close"] == 12
    store.upsert_article(article(), "AAPL", "t")
    store.set_article_windows([("2026-11-06", "overnight", 1)])
    assert store.known_days() == ["2026-11-04", "2026-11-05", "2026-11-06"]


def test_unscored_and_scores(store):
    a1, _ = store.upsert_article(article(), "AAPL", "t")
    a2, _ = store.upsert_article(article(url="https://x/2", title="Other"), "AAPL", "t")
    assert store.unscored_articles("m") == [(a1, "Apple beats"), (a2, "Other")]
    store.insert_scores("m", [(a1, SentimentScores(0.7, 0.1, 0.2, "positive"))], "t")
    assert store.unscored_articles("m") == [(a2, "Other")]
    assert store.unscored_articles("other-model") == [(a1, "Apple beats"), (a2, "Other")]
    assert store.articles_without_window() == [
        (a1, "2026-11-05T14:00:00Z"),
        (a2, "2026-11-05T14:00:00Z"),
    ]
    store.set_article_windows([("2026-11-05", "overnight", a1), ("2026-11-05", "intraday", a2)])
    assert store.articles_without_window() == []
    assert store.ticker_scores("2026-11-05", ("overnight",), "m") == {
        "AAPL": (1, pytest.approx(0.6))
    }
    assert store.ticker_scores("2026-11-05", ("overnight", "intraday"), "m") == {
        "AAPL": (1, pytest.approx(0.6))
    }  # a2 has no score
    assert [r["window"] for r in store.articles_on("2026-11-05")] == ["overnight", "intraday"]


def test_universe_membership_is_replaced_per_day(store):
    store.record_universe("2026-11-05", [UniverseMember("MSFT", "Microsoft")], "static")
    store.record_universe(
        "2026-11-05", [UniverseMember("AAPL", "Apple", 1.0, 1, 320193)], "marketcap"
    )
    assert [m.ticker for m in store.universe_for("2026-11-05")] == ["AAPL"]
    assert store.universe_for("2026-11-05")[0].cik == 320193
    assert store.universe_for("2026-11-04") == []


def test_universe_months_and_companies(store):
    members = [UniverseMember("AAPL", "Apple", 2.0, 1, 1), UniverseMember("MSFT", "MS", 1.0, 2, 2)]
    store.save_universe_month("2026-11", members, "t")
    store.save_universe_month("2026-11", members[:1], "t2")  # replaced, not appended
    assert [m.ticker for m in store.universe_month("2026-11")] == ["AAPL"]
    assert store.universe_month("2026-10") == []
    store.upsert_company(1, "AAPL", "Apple", "United States", "t")
    store.upsert_company(1, "AAPL", "Apple Inc.", "United States", "t2")
    assert store.company_countries() == {1: ("United States", "t2")}


def test_runs(store):
    rid = store.start_run("2026-11-05", "manual", 1, "t0")
    assert not store.successful_run_exists("2026-11-05")
    store.finish_run(rid, "t1", True, "[]")
    assert store.successful_run_exists("2026-11-05")
    r = store.recent_runs()[0]
    assert (r["id"], r["ok"], r["finished_at"]) == (rid, 1, "t1")


def test_migrates_a_version_1_store(tmp_path):
    import sqlite3

    from murmur.store import MIGRATIONS

    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(MIGRATIONS[1] + "PRAGMA user_version = 1;")
    conn.execute(
        "INSERT INTO universe_membership (date, ticker, name, source) "
        "VALUES ('d', 'AAPL', 'A', 's')"
    )
    conn.commit()
    conn.close()
    with Store(path) as s:
        assert s.schema_version == SCHEMA_VERSION
        assert s.count("runs") == 0
        assert s.universe_for("d")[0].cik is None


def test_migrates_a_version_2_store_keeping_correlations(tmp_path):
    import sqlite3

    from murmur.store import MIGRATIONS

    path = tmp_path / "v2.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(MIGRATIONS[1] + MIGRATIONS[2] + "PRAGMA user_version = 2;")
    conn.execute(
        """INSERT INTO correlations (date, window, scope, source, method, model, n, estimate,
                                     p_value, ci_low, ci_high, tickers, computed_at)
           VALUES ('2026-11-05', 'day', 'pooled', 'all', 'pearson', 'm', 98, 0.372, 0.0002,
                   0.19, 0.53, 'AAPL,MSFT', 't')"""
    )
    conn.execute(
        "INSERT INTO daily_sentiment (date, ticker, model, n_articles, mean_score) "
        "VALUES ('2026-11-05', 'AAPL', 'm', 3, 0.1)"
    )
    conn.commit()
    conn.close()
    with Store(path) as s:
        assert s.schema_version == SCHEMA_VERSION
        rows = s.correlations_on("2026-11-05")
        assert len(rows) == 1
        r = rows[0]
        assert (r["window"], r["return_type"], r["lag"], r["estimate"]) == (
            "day",
            "daily",
            0,
            0.372,
        )
        assert s.count("window_sentiment") == 0 and s.count("returns") == 0
        with pytest.raises(ValueError):
            s.count("daily_sentiment")


def test_cascade_delete_removes_links(store):
    aid, _ = store.upsert_article(article(), "AAPL", "t")
    store.insert_scores("m", [(aid, SentimentScores(0.7, 0.1, 0.2, "positive"))], "t")
    store.conn.execute("DELETE FROM articles WHERE id = ?", (aid,))
    assert store.count("article_tickers") == 0
    assert store.count("sentiment") == 0


def test_count_rejects_unknown_table(store):
    with pytest.raises(ValueError):
        store.count("sqlite_master")


def test_rolling_rows_and_ranges(store):
    store.upsert_returns([("AAPL", "2026-11-04", "2026-11-03", 1, 1, 1, 0.1, 0.2, 0.3)])
    store.upsert_returns([("AAPL", "2026-11-05", "2026-11-04", 1, 1, 1, 0.1, 0.2, 0.3)])
    assert [r["date"] for r in store.returns_between("2026-11-05", "2026-11-30")] == ["2026-11-05"]
    assert store.days_with_returns() == ["2026-11-04", "2026-11-05"]
    store.upsert_window_sentiment("2026-11-05", "day", "m", {"AAPL": (2, 0.4)})
    assert len(store.window_sentiment_between("2026-11-01", "2026-11-05", "m")) == 1
    assert store.window_sentiment_between("2026-11-01", "2026-11-05", "other") == []
    n = store.replace_rolling(
        "2026-11-05",
        [(3, "day", "daily", 1, "pooled", "", "pearson", 40, 0.5, 0.01, 0.2, 0.7, "t")],
    )
    assert n == 1 and store.rolling_on("2026-11-05")[0]["lag"] == 1
    store.replace_rolling("2026-11-05", [])
    assert store.rolling_on("2026-11-05") == []


def test_migrates_a_version_3_store(tmp_path):
    import sqlite3

    from murmur.store import MIGRATIONS

    path = tmp_path / "v3.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(MIGRATIONS[1] + MIGRATIONS[2] + MIGRATIONS[3] + "PRAGMA user_version = 3;")
    conn.close()
    with Store(path) as s:
        assert s.schema_version == SCHEMA_VERSION
        assert s.count("rolling_correlations") == 0
