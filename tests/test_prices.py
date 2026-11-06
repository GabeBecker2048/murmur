from datetime import UTC, date, datetime

import pandas as pd

from murmur.sources.yfinance_prices import parse_download, partial_bar_date

TZ = "America/New_York"
COLS = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]


def frame(symbols, dates, tz_aware=False):
    idx = pd.DatetimeIndex(pd.to_datetime(dates), name="Date")
    if tz_aware:
        idx = idx.tz_localize(TZ)
    columns = pd.MultiIndex.from_product([symbols, COLS])
    data = {}
    for i, sym in enumerate(symbols):
        base = 100 * (i + 1)
        for j, col in enumerate(COLS):
            data[(sym, col)] = [base + j + k for k in range(len(dates))]
    return pd.DataFrame(data, index=idx, columns=columns)


def test_multi_ticker_frame_maps_symbols_back():
    df = frame(["AAPL", "BRK-B"], ["2026-11-03", "2026-11-04"])
    bars = parse_download(df, {"AAPL": "AAPL", "BRK-B": "BRK.B"}, TZ)
    assert [(b.ticker, b.date) for b in bars] == [
        ("AAPL", "2026-11-03"),
        ("AAPL", "2026-11-04"),
        ("BRK.B", "2026-11-03"),
        ("BRK.B", "2026-11-04"),
    ]
    b = bars[2]
    assert (b.open, b.high, b.low, b.close, b.adj_close, b.volume) == (200, 201, 202, 203, 204, 205)


def test_single_ticker_frame_without_multiindex():
    df = frame(["AAPL"], ["2026-11-04"])
    df.columns = df.columns.droplevel(0)
    bars = parse_download(df, {"AAPL": "AAPL"}, TZ)
    assert len(bars) == 1 and bars[0].ticker == "AAPL" and bars[0].close == 103


def test_missing_and_all_nan_tickers_are_skipped():
    df = frame(["AAPL", "XXXX"], ["2026-11-04"])
    df[("XXXX", "Close")] = float("nan")
    bars = parse_download(df, {"AAPL": "AAPL", "XXXX": "XXXX", "YYYY": "YYYY"}, TZ)
    assert [b.ticker for b in bars] == ["AAPL"]


def test_partial_bar_is_dropped_and_tz_aware_index_handled():
    df = frame(["AAPL"], ["2026-11-04", "2026-11-05"], tz_aware=True)
    bars = parse_download(df, {"AAPL": "AAPL"}, TZ, drop_date=date(2026, 11, 5))
    assert [b.date for b in bars] == ["2026-11-04"]


def test_empty_frame():
    assert parse_download(pd.DataFrame(), {"AAPL": "AAPL"}, TZ) == []


def test_partial_bar_date():
    # 15:00 ET = 20:00 UTC on 2026-11-05 (EST)
    assert partial_bar_date(datetime(2026, 11, 5, 20, 0, tzinfo=UTC), TZ) == date(2026, 11, 5)
    # 16:30 ET
    assert partial_bar_date(datetime(2026, 11, 5, 21, 30, tzinfo=UTC), TZ) is None
    # 23:00 ET is already the next UTC day; still not partial
    assert partial_bar_date(datetime(2026, 11, 6, 4, 0, tzinfo=UTC), TZ) is None
