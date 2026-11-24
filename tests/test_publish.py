"""Rounding and notices applied to everything that leaves the building."""

from murmur.publish import (
    cc_by,
    file_metadata,
    news_terms,
    public,
    round_cap,
    round_weight,
    source_terms,
)


def test_round_cap_three_significant_figures():
    assert round_cap(5001541320704) == 5_000_000_000_000
    assert round_cap(3979920277504) == 3_980_000_000_000
    assert round_cap(123_456_789) == 123_000_000
    assert round_cap(99_950_000_000) == 100_000_000_000
    assert round_cap(0) == 0 and round_cap(None) is None


def test_round_weight_and_public_walks_nested_values():
    assert round_weight(0.09714904192) == 0.0971 and round_weight(None) is None
    out = public(
        {"items": [{"ticker": "A", "market_cap": 1234567890.0, "weight": 0.123456, "n": 7}]}
    )
    assert out == {
        "items": [{"ticker": "A", "market_cap": 1_230_000_000, "weight": 0.1235, "n": 7}]
    }


def test_notices_and_metadata():
    st = source_terms("me@example.com")
    assert st["name"] == "Source terms apply" and "Takedown available by request" in st["note"]
    assert st["contact"] == "me@example.com" and "dataset-card" in st["url"]
    assert "yfinance" in st["attribution"]
    nt = news_terms("me@example.com")
    assert nt["name"] == "Source terms apply" and "Finnhub" in nt["attribution"]
    assert "Takedown available by request" in nt["note"]
    meta = file_metadata("prices", "prices", st, {"murmur_period": "2026-11-05"})
    assert meta["license"] == st["name"] and meta["murmur_period"] == "2026-11-05"
    assert all(isinstance(v, str) for v in meta.values())
    assert "attribution" in cc_by("x@y.z") and cc_by("x@y.z")["name"] == "CC BY 4.0"
