"""Daily OHLCV bars through ``yfinance.download`` (one request for all tickers)."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pandas as pd

from murmur.models import PriceBar
from murmur.sources.base import yahoo_symbol

log = logging.getLogger(__name__)

SOURCE = "yfinance"
MARKET_CLOSE_SETTLED = time(16, 5)


def partial_bar_date(now: datetime, tz: str) -> date | None:
    """Today's date if the market has not closed yet (so today's bar is partial), else None."""
    local = now.astimezone(ZoneInfo(tz))
    return local.date() if local.time() < MARKET_CLOSE_SETTLED else None


def _float(value) -> float | None:
    return None if pd.isna(value) else float(value)


def parse_download(
    df: pd.DataFrame, symbols: dict[str, str], tz: str, drop_date: date | None = None
) -> list[PriceBar]:
    """``yf.download(group_by="ticker", auto_adjust=False)`` frame -> bars.

    ``symbols`` maps Yahoo symbol -> SEC ticker. Symbols missing from the frame
    or with no closes are skipped with a warning.
    """
    if df is None or df.empty:
        log.warning("price download returned no data")
        return []
    if not isinstance(df.columns, pd.MultiIndex):
        only = next(iter(symbols))
        df = df.copy()
        df.columns = pd.MultiIndex.from_product([[only], df.columns])
    present = set(df.columns.get_level_values(0))
    bars: list[PriceBar] = []
    for symbol, ticker in symbols.items():
        if symbol not in present:
            log.warning("%s: no price data returned", ticker)
            continue
        sub = df[symbol].dropna(subset=["Close"])
        if sub.empty:
            log.warning("%s: no price data returned", ticker)
            continue
        for ts, row in sub.iterrows():
            stamp = pd.Timestamp(ts)
            day = (stamp.tz_convert(tz) if stamp.tzinfo is not None else stamp).date()
            if drop_date is not None and day == drop_date:
                continue
            volume = row.get("Volume")
            bars.append(
                PriceBar(
                    ticker=ticker,
                    date=day.isoformat(),
                    open=_float(row.get("Open")),
                    high=_float(row.get("High")),
                    low=_float(row.get("Low")),
                    close=float(row["Close"]),
                    adj_close=_float(row.get("Adj Close")),
                    volume=None if pd.isna(volume) else int(volume),
                )
            )
    return bars


class YfinancePrices:
    name = SOURCE

    def __init__(self, tz: str, period: str = "1mo"):
        self.tz = tz
        self.period = period

    def fetch(self, tickers: Iterable[str]) -> list[PriceBar]:
        return self._download(tickers, period=self.period)

    def fetch_range(self, tickers: Iterable[str], start: date, end: date) -> list[PriceBar]:
        from datetime import timedelta

        # yfinance's end is exclusive
        return self._download(
            tickers, start=start.isoformat(), end=(end + timedelta(days=1)).isoformat()
        )

    def _download(self, tickers: Iterable[str], **when) -> list[PriceBar]:
        import yfinance as yf

        symbols = {yahoo_symbol(t): t for t in tickers}
        df = yf.download(
            list(symbols),
            interval="1d",
            auto_adjust=False,
            actions=False,
            group_by="ticker",
            progress=False,
            threads=True,
            **when,
        )
        drop = partial_bar_date(datetime.now(ZoneInfo(self.tz)), self.tz)
        if drop is not None:
            log.info("market not closed yet; ignoring today's partial bar (%s)", drop)
        return parse_download(df, symbols, self.tz, drop)
