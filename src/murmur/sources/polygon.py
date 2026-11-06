"""Polygon grouped daily bars: one request returns every US stock's bar for a
date. Free tier: 5 calls a minute, two years of history, and **today's bar is
refused until the day is over** (HTTP 403 NOT_AUTHORIZED), so on the free
plan this source cannot feed the 18:00 run; it is for backfills. Needs an API
key."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date

from murmur.clock import is_trading_day, sessions_between, today_in
from murmur.models import PriceBar
from murmur.sources.base import HttpError, RateLimiter, get_json, yahoo_symbol
from murmur.sources.yfinance_prices import partial_bar_date

log = logging.getLogger(__name__)

SOURCE = "polygon"
URL = "https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{date}"
RECENT_SESSIONS = 5


def parse_grouped(payload: dict, day: date, wanted: dict[str, str]) -> list[PriceBar]:
    """``wanted`` maps Polygon symbol -> our ticker."""
    out = []
    for r in payload.get("results", []) or []:
        ticker = wanted.get(r.get("T"))
        if ticker is None or r.get("c") is None:
            continue
        out.append(
            PriceBar(
                ticker=ticker,
                date=day.isoformat(),
                open=r.get("o"),
                high=r.get("h"),
                low=r.get("l"),
                close=float(r["c"]),
                adj_close=None,
                volume=int(r["v"]) if r.get("v") is not None else None,
            )
        )
    return out


class PolygonPrices:
    name = SOURCE

    def __init__(self, api_key: str, tz: str, limiter: RateLimiter | None = None, now=None):
        if not api_key:
            raise ValueError(
                "polygon needs an API key (MURMUR_POLYGON_API_KEY or [polygon] api_key)"
            )
        self.api_key = api_key
        self.tz = tz
        self.limiter = limiter or RateLimiter(calls=5, period=60)
        self._now = now

    def fetch(self, tickers: Iterable[str]) -> list[PriceBar]:
        from datetime import datetime
        from zoneinfo import ZoneInfo

        now = self._now() if self._now else datetime.now(ZoneInfo(self.tz))
        end = today_in(self.tz)
        drop = partial_bar_date(now, self.tz)
        sessions = [d for d in sessions_between(end.replace(day=1), end) if d != drop][
            -RECENT_SESSIONS:
        ]
        return self._fetch_days(list(tickers), sessions)

    def fetch_range(self, tickers: Iterable[str], start: date, end: date) -> list[PriceBar]:
        return self._fetch_days(list(tickers), sessions_between(start, end))

    def _fetch_days(self, tickers: list[str], days: list[date]) -> list[PriceBar]:
        wanted = {yahoo_symbol(t).replace("-", "."): t for t in tickers}  # Polygon uses BRK.B
        bars: list[PriceBar] = []
        for day in days:
            if not is_trading_day(day):
                continue
            self.limiter.wait()
            try:
                payload = get_json(
                    URL.format(date=day.isoformat()), {"adjusted": "false", "apiKey": self.api_key}
                )
            except HttpError as e:
                if e.status == 403 and "end of day" in str(e):
                    log.warning("polygon: %s not available yet on this plan; skipped", day)
                    continue
                raise
            got = parse_grouped(payload if isinstance(payload, dict) else {}, day, wanted)
            log.debug("polygon %s: %d of %d tickers", day, len(got), len(tickers))
            bars += got
        return bars
