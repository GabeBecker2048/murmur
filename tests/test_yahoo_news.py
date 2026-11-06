from murmur.sources.base import yahoo_symbol
from murmur.sources.yahoo_news import parse_news_item
from tests.conftest import load_fixture


def test_parses_real_payload():
    items = load_fixture("yahoo_news_aapl.json")
    articles = [parse_news_item(i) for i in items]
    assert len(articles) == 5 and all(articles)
    first = articles[0]
    assert first.source == "yahoo"
    assert first.url.startswith("https://")
    assert first.published_at == items[0]["content"]["pubDate"]
    assert first.provider == items[0]["content"]["provider"]["displayName"]
    assert first.source_id == items[0]["id"]
    assert first.content_type == "STORY"
    assert first.title


def test_timestamps_are_normalised_to_utc_z():
    item = {
        "id": "x",
        "content": {
            "title": "T",
            "pubDate": "2026-11-05T19:30:00-04:00",
            "canonicalUrl": {"url": "https://e/1"},
        },
    }
    assert parse_news_item(item).published_at == "2026-11-05T23:30:00Z"


def test_display_time_fallback_and_missing_date():
    base = {"title": "T", "canonicalUrl": {"url": "https://e/1"}}
    assert parse_news_item(
        {"content": {**base, "displayTime": "2026-01-01T00:00:00Z"}}
    ).published_at == ("2026-01-01T00:00:00Z")
    assert parse_news_item({"content": base}).published_at is None


def test_click_through_fallback_and_skips():
    item = {"id": "a", "content": {"title": "T", "clickThroughUrl": {"url": "https://c/1"}}}
    assert parse_news_item(item).url == "https://c/1"
    assert parse_news_item({"id": "b", "content": {"title": "T"}}) is None
    assert parse_news_item({"id": "c", "content": {"canonicalUrl": {"url": "https://c"}}}) is None
    assert parse_news_item({"id": "d"}) is None


def test_symbol_mapping():
    assert yahoo_symbol("BRK-B") == "BRK-B"
    assert yahoo_symbol("brk.b") == "BRK-B"  # old dotted form still maps
    assert yahoo_symbol("AAPL") == "AAPL"
