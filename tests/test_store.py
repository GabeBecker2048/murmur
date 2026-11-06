from datetime import date

import pytest

from murmur.clock import window_bounds
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


def test_last_two_closes(store):
    bars = [
        PriceBar("AAPL", "2026-11-03", None, None, None, 10, None, None),
        PriceBar("AAPL", "2026-11-04", None, None, None, 11, None, None),
        PriceBar("AAPL", "2026-11-05", None, None, None, 12, None, None),
        PriceBar("MSFT", "2026-11-05", None, None, None, 50, None, None),
    ]
    store.upsert_prices(bars, "yfinance")
    closes = store.last_two_closes("2026-11-05")
    assert closes["AAPL"] == [("2026-11-05", 12), ("2026-11-04", 11)]
    assert closes["MSFT"] == [("2026-11-05", 50)]
    assert store.last_two_closes("2026-11-04")["AAPL"] == [("2026-11-04", 11), ("2026-11-03", 10)]


def test_unscored_and_scores(store):
    a1, _ = store.upsert_article(article(), "AAPL", "t")
    a2, _ = store.upsert_article(article(url="https://x/2", title="Other"), "AAPL", "t")
    assert store.unscored_articles("m") == [(a1, "Apple beats"), (a2, "Other")]
    store.insert_scores("m", [(a1, SentimentScores(0.7, 0.1, 0.2, "positive"))], "t")
    assert store.unscored_articles("m") == [(a2, "Other")]
    assert store.unscored_articles("other-model") == [(a1, "Apple beats"), (a2, "Other")]
    start, end = window_bounds(date(2026, 11, 5), "America/New_York")
    assert store.ticker_scores_in_window(start, end, "m") == {"AAPL": (1, pytest.approx(0.6))}


def test_universe_membership(store):
    store.record_universe("2026-11-05", [UniverseMember("MSFT", "Microsoft")], "static")
    store.record_universe("2026-11-05", [UniverseMember("AAPL", "Apple")], "static")
    assert [m.ticker for m in store.universe_for("2026-11-05")] == ["AAPL", "MSFT"]
    assert store.universe_for("2026-11-04") == []


def test_cascade_delete_removes_links(store):
    aid, _ = store.upsert_article(article(), "AAPL", "t")
    store.insert_scores("m", [(aid, SentimentScores(0.7, 0.1, 0.2, "positive"))], "t")
    store.conn.execute("DELETE FROM articles WHERE id = ?", (aid,))
    assert store.count("article_tickers") == 0
    assert store.count("sentiment") == 0


def test_count_rejects_unknown_table(store):
    with pytest.raises(ValueError):
        store.count("sqlite_master")
