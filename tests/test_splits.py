"""Split ratios: the Polygon source, the store, split-adjusted returns and the
pipeline's ingestion alongside prices."""

from datetime import date

from murmur import pipeline
from murmur.analysis import compute_returns
from murmur.models import PriceBar, Split
from murmur.sources.splits import NoSplits, PolygonSplits, parse_polygon
from murmur.universe import record_universe
from tests.test_analysis import cfg  # noqa: F401

URL = "https://api.polygon.io/v3/reference/splits"


def test_parse_polygon_ratio_and_ticker_form():
    payload = {
        "results": [
            {"ticker": "NVDA", "execution_date": "2024-06-10", "split_from": 1, "split_to": 10},
            {"ticker": "BRK.B", "execution_date": "2026-11-05", "split_from": 50, "split_to": 1},
            {"ticker": "BAD", "execution_date": "2026-11-05", "split_from": 0, "split_to": 1},
        ]
    }
    assert parse_polygon(payload) == [
        Split("NVDA", "2024-06-10", 10.0),
        Split("BRK-B", "2026-11-05", 0.02),
    ]


def test_polygon_splits_follows_next_url():
    pages = {
        URL: {
            "results": [
                {"ticker": "A", "execution_date": "2026-11-05", "split_from": 1, "split_to": 2}
            ],
            "next_url": URL + "?cursor=x",
        },
        URL + "?cursor=x": {
            "results": [
                {"ticker": "B", "execution_date": "2026-11-06", "split_from": 1, "split_to": 3}
            ]
        },
    }
    calls = []

    def get(url, params):
        calls.append((url, params))
        return pages[url]

    out = PolygonSplits("k", get=get).fetch_range(date(2026, 11, 5), date(2026, 11, 6))
    assert [(s.ticker, s.ratio) for s in out] == [("A", 2.0), ("B", 3.0)]
    assert calls[0][1]["execution_date.gte"] == "2026-11-05" and calls[1][1] == {"apiKey": "k"}


def test_store_roundtrip(store):
    assert store.upsert_splits([Split("NVDA", "2026-11-05", 10.0)], "polygon") == 1
    store.upsert_splits([Split("NVDA", "2026-11-05", 4.0)], "test")  # upsert
    assert store.splits_on("2026-11-05") == {"NVDA": 4.0}
    assert store.splits_on("2026-11-04") == {}
    assert [dict(r)["source"] for r in store.splits_between("2026-11-01", "2026-11-30")] == ["test"]


def bars(store, ticker, prev_close, open_, close):
    store.upsert_prices(
        [
            PriceBar(ticker, "2026-11-04", prev_close, None, None, prev_close, None, 1),
            PriceBar(ticker, "2026-11-05", open_, None, None, close, None, 1),
        ],
        "test",
    )


def test_returns_divide_the_previous_close_by_the_ratio(store):
    # a 10-for-1 split: raw prev close 1000, trades at 101 next morning, closes 105
    bars(store, "NVDA", 1000.0, 101.0, 105.0)
    bars(store, "AAPL", 200.0, 202.0, 204.0)  # no split
    store.upsert_splits([Split("NVDA", "2026-11-05", 10.0)], "test")
    out = compute_returns(store, date(2026, 11, 5))
    assert round(out["NVDA"].overnight, 4) == 0.01 and round(out["NVDA"].daily, 4) == 0.05
    assert round(out["AAPL"].overnight, 4) == 0.01
    stored = {r["ticker"]: r for r in store.returns_on("2026-11-05")}
    assert stored["NVDA"]["prev_close"] == 100.0  # the adjusted value is what is published


def test_already_restated_history_is_not_adjusted_twice(store):
    # a source that restates history (Yahoo) already shows the previous close at 100
    bars(store, "NVDA", 100.0, 101.0, 105.0)
    store.upsert_splits([Split("NVDA", "2026-11-05", 10.0)], "test")
    out = compute_returns(store, date(2026, 11, 5))
    assert round(out["NVDA"].overnight, 4) == 0.01


class FakeSplits:
    name = "fake"

    def __init__(self):
        self.calls = []

    def fetch_range(self, start, end):
        self.calls.append((start, end))
        return [Split("AAPL", "2026-11-05", 2.0)]


class FakePrices:
    name = "fake-prices"

    def fetch(self, tickers):
        return [
            PriceBar("AAPL", "2026-11-04", 400.0, None, None, 400.0, None, 1),
            PriceBar("AAPL", "2026-11-05", 201.0, None, None, 202.0, None, 1),
        ]

    def fetch_range(self, tickers, start, end):
        return self.fetch(tickers)


def test_run_prices_ingests_splits_for_the_bars_range(store, cfg):  # noqa: F811
    record_universe(store, cfg, date(2026, 11, 5))
    splits = FakeSplits()
    summary = pipeline.run_prices(store, cfg, date(2026, 11, 5), FakePrices(), splits)
    assert summary.endswith("1 splits")
    assert splits.calls == [(date(2026, 11, 4), date(2026, 11, 5))]
    out = compute_returns(store, date(2026, 11, 5))
    assert round(out["AAPL"].overnight, 4) == 0.005


def test_default_splits_source_follows_config_and_key(cfg, tmp_path):  # noqa: F811
    from murmur.config import load_config

    assert isinstance(pipeline.default_splits_source(cfg), NoSplits)  # auto, no key
    with_key = load_config(cfg.source_path, env={"MURMUR_POLYGON_API_KEY": "k"})
    assert pipeline.default_splits_source(with_key).name == "polygon"
    user = tmp_path / "none.toml"
    user.write_text(cfg.source_path.read_text() + '\n[prices]\nsplits = "none"\n')
    assert (
        pipeline.default_splits_source(load_config(user, env={"MURMUR_POLYGON_API_KEY": "k"})).name
        == "none"
    )
