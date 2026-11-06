"""The Databento price source: record parsing, symbol mapping, availability clamping
and request shape. No network: the HTTP call is injected."""

import json
from datetime import date

import pytest

from murmur.models import PriceBar
from murmur.sources.databento import DatabentoPrices, databento_symbol, parse_records


def record(symbol, day, o, h, lo, c, v):
    return json.dumps(
        {
            "hd": {"ts_event": f"{day}T00:00:00.000000000Z", "rtype": 35, "instrument_id": 1},
            "open": f"{o:.9f}",
            "high": f"{h:.9f}",
            "low": f"{lo:.9f}",
            "close": f"{c:.9f}",
            "volume": str(v),
            "symbol": symbol,
        }
    )


def test_parse_records_maps_symbols_and_prices():
    lines = [
        record("AAPL", "2026-11-05", 270.5, 272, 269, 271.25, 41000000),
        record("BRK.B", "2026-11-05", 490, 495, 488, 492.1, 3000000),
        record("ZZZZ", "2026-11-05", 1, 1, 1, 1, 1),  # not wanted
        "",
        json.dumps({"hd": {"ts_event": "2026-11-05T00:00:00Z"}, "symbol": "AAPL", "close": None}),
    ]
    bars = parse_records(lines, {"AAPL": "AAPL", "BRK.B": "BRK-B"})
    assert bars == [
        PriceBar("AAPL", "2026-11-05", 270.5, 272.0, 269.0, 271.25, None, 41000000),
        PriceBar("BRK-B", "2026-11-05", 490.0, 495.0, 488.0, 492.1, None, 3000000),
    ]


def test_integer_nano_prices_and_null_sentinel():
    rec = {
        "hd": {"ts_event": "2026-11-05T00:00:00Z"},
        "open": 270500000000,
        "high": 9223372036854775807,
        "low": 269000000000,
        "close": 271250000000,
        "volume": 10,
        "symbol": "AAPL",
    }
    (bar,) = parse_records([json.dumps(rec)], {"AAPL": "AAPL"})
    assert bar.open == 270.5 and bar.high is None and bar.close == 271.25


def test_symbol_form():
    assert databento_symbol("BRK-B") == "BRK.B" and databento_symbol("aapl") == "AAPL"


class FakeHttp:
    def __init__(self, end="2026-11-06T00:00:00Z"):
        self.calls = []
        self.end = end

    def __call__(self, method, url, params):
        self.calls.append((method, url.rsplit("/", 1)[1], dict(params)))
        if url.endswith("metadata.get_dataset_range"):
            return json.dumps(
                {
                    "start": "2018-05-01T00:00:00Z",
                    "end": self.end,
                    "schema": {"ohlcv-1d": {"end": self.end}},
                }
            )
        symbols = params["symbols"].split(",")
        out = []
        for d in ("2026-11-04", "2026-11-05"):
            if params["start"] <= d < params["end"]:
                for sym in symbols:
                    out.append(record(sym, d, 10, 11, 9, 10.5, 100))
        return "\n".join(out)


def test_fetch_range_clamps_to_availability_and_posts_symbols():
    http = FakeHttp(end="2026-11-06T00:00:00Z")  # exclusive end -> last bar is 2026-11-05
    src = DatabentoPrices("key", tz="America/New_York", http=http, today=lambda: date(2026, 11, 6))
    bars = src.fetch_range(["AAPL", "BRK-B"], date(2026, 11, 4), date(2026, 11, 9))
    assert {(b.ticker, b.date) for b in bars} == {
        ("AAPL", "2026-11-04"),
        ("AAPL", "2026-11-05"),
        ("BRK-B", "2026-11-04"),
        ("BRK-B", "2026-11-05"),
    }
    assert http.calls[0][:2] == ("GET", "metadata.get_dataset_range")
    method, endpoint, params = http.calls[1]
    assert (method, endpoint) == ("POST", "timeseries.get_range")
    assert params["symbols"] == "AAPL,BRK.B" and params["schema"] == "ohlcv-1d"
    assert params["dataset"] == "EQUS.SUMMARY" and params["stype_in"] == "raw_symbol"
    assert (params["start"], params["end"]) == ("2026-11-04", "2026-11-06")  # clamped, exclusive


def test_fetch_takes_the_recent_sessions_and_nothing_before_availability():
    http = FakeHttp(end="2026-11-05T00:00:00Z")  # only up to the 4th
    src = DatabentoPrices("key", tz="America/New_York", http=http, today=lambda: date(2026, 11, 6))
    bars = src.fetch(["AAPL"])
    assert {b.date for b in bars} == {"2026-11-04"}
    assert src.fetch_range([], date(2026, 11, 4), date(2026, 11, 5)) == []


def test_needs_a_key():
    with pytest.raises(ValueError, match="API key"):
        DatabentoPrices("", tz="America/New_York")
