"""Finnhub, GDELT, Polygon parsers, the rate limiter, and source selection."""

from datetime import date

import pytest

from murmur.config import load_config
from murmur.sources.base import RateLimiter
from murmur.sources.finnhub import FinnhubNews, parse_items
from murmur.sources.gdelt import GdeltNews, company_query, parse_payload
from murmur.sources.polygon import PolygonPrices, parse_grouped
from tests.conftest import load_fixture


def test_finnhub_parse():
    arts = parse_items(load_fixture("finnhub_news.json"))
    assert [a.title[:12] for a in arts] == ["Apple beats ", "Apple (AAPL)", "Undated note"]
    assert arts[0].published_at == "2026-11-05T15:00:00Z"
    assert arts[0].source == "finnhub" and arts[0].provider == "Reuters"
    assert arts[0].source_id == "140001" and arts[0].content_type == "company"
    assert arts[2].published_at is None
    with pytest.raises(ValueError):
        FinnhubNews("")


def test_gdelt_parse_and_query():
    arts = parse_payload(load_fixture("gdelt_artlist.json"))
    assert [a.provider for a in arts] == ["reuters.com", "example.com", "example.com"]
    assert arts[0].published_at == "2026-11-05T15:15:00Z"
    assert arts[2].published_at is None  # bogus seendate
    assert arts[0].source == "gdelt"
    assert company_query("Apple Inc.") == '"Apple" sourcelang:english'
    assert company_query("Berkshire Hathaway Inc.") == '"Berkshire Hathaway" sourcelang:english'
    assert company_query("Walmart") == '"Walmart" sourcelang:english'
    assert (
        GdeltNews({"AAPL": "Apple Inc."}).fetch_range("MSFT", date(2026, 11, 1), date(2026, 11, 5))
        == []
    )


def test_polygon_parse_maps_symbols_and_skips_unknown():
    bars = parse_grouped(
        load_fixture("polygon_grouped.json"),
        date(2026, 11, 5),
        {"AAPL": "AAPL", "BRK.B": "BRK-B", "MSFT": "MSFT"},
    )
    assert [(b.ticker, b.date, b.close) for b in bars] == [
        ("AAPL", "2026-11-05", 344.17),
        ("BRK-B", "2026-11-05", 572.33),
    ]
    assert bars[0].open == 353.89 and bars[0].volume == 74173346 and bars[0].adj_close is None
    with pytest.raises(ValueError):
        PolygonPrices("", tz="America/New_York")


def test_rate_limiter_sleeps_when_over_budget():
    now = [0.0]
    slept = []

    def clock():
        return now[0]

    def sleep(s):
        slept.append(s)
        now[0] += s

    rl = RateLimiter(calls=2, period=10, sleep=sleep, clock=clock)
    rl.wait()
    rl.wait()
    assert slept == []
    rl.wait()  # third call within 10 s must wait for the first to age out
    assert slept == [10.0]
    now[0] += 11
    rl.wait()
    assert slept == [10.0]


def test_config_sources_and_keys(tmp_path):
    user = tmp_path / "c.toml"
    user.write_text(
        '[news]\nsources = ["yahoo", "finnhub"]\n[prices]\nsource = "polygon"\n'
        '[finnhub]\napi_key = "f"\n'
    )
    cfg = load_config(user, env={"MURMUR_POLYGON_API_KEY": "p-env"})
    assert cfg.news.sources == ("yahoo", "finnhub")
    assert cfg.prices.source == "polygon"
    assert cfg.keys.finnhub == "f" and cfg.keys.polygon == "p-env"
    cfg = load_config(user, env={"MURMUR_FINNHUB_API_KEY": "f-env"})
    assert cfg.keys.finnhub == "f-env"  # env wins over the file
    bad = tmp_path / "bad.toml"
    bad.write_text('[news]\nsources = ["bloomberg"]\n')
    from murmur.config import ConfigError

    with pytest.raises(ConfigError):
        load_config(bad, env={})
    bad.write_text('[prices]\nsource = "iex"\n')
    with pytest.raises(ConfigError):
        load_config(bad, env={})
