"""Databento US Equities Summary (``EQUS.SUMMARY``): the official consolidated
end-of-day bar for every US stock, from Nasdaq's end-of-day feed, through
Databento's historical HTTP API.

Why this source: it is licensed for redistribution with attribution ("Data
Provided by Databento"), subject to the venue's terms, and historical access
needs no exchange license or subscription. Usage-priced by volume: a year of
daily bars for 500 symbols is a few megabytes.

Two things shape the pipeline around it: the final summary for a session is
published at 20:15 ET, after the 18:00 run, so prices for day *t* are fetched
the next morning (``schedule.analysis_time``); and prices are **unadjusted**
for splits and dividends, so ``compute_returns`` applies the ``splits`` table.
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from datetime import date, timedelta

from murmur.clock import is_trading_day, sessions_between, today_in
from murmur.models import PriceBar
from murmur.sources.base import USER_AGENT, HttpError, RateLimiter, yahoo_symbol

log = logging.getLogger(__name__)

SOURCE = "databento"
ATTRIBUTION = "Data Provided by Databento"
BASE = "https://hist.databento.com/v0"
DATASET = "EQUS.SUMMARY"
SCHEMA = "ohlcv-1d"
RECENT_SESSIONS = 5
MAX_SYMBOLS = 2000


def databento_symbol(ticker: str) -> str:
    """Databento uses Nasdaq's dotted share-class form (``BRK.B``)."""
    return yahoo_symbol(ticker).replace("-", ".")


def _px(value) -> float | None:
    """Prices arrive as decimal strings with ``pretty_px`` (or as nano-scaled
    integers without it). ``None`` for Databento's null sentinel."""
    if value is None:
        return None
    if isinstance(value, str):
        return float(value) if value not in ("", "null") else None
    if isinstance(value, int):
        return None if value == 9223372036854775807 else value / 1e9
    return float(value)


def parse_records(lines: Iterable[str], wanted: dict[str, str]) -> list[PriceBar]:
    """JSON-lines records -> bars. ``wanted`` maps Databento symbol -> our ticker."""
    out: list[PriceBar] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        ticker = wanted.get(rec.get("symbol", ""))
        if ticker is None:
            continue
        close = _px(rec.get("close"))
        if close is None or close <= 0:
            continue
        ts = str(rec.get("hd", {}).get("ts_event", ""))
        if len(ts) < 10:
            continue
        volume = rec.get("volume")
        out.append(
            PriceBar(
                ticker=ticker,
                date=ts[:10],
                open=_px(rec.get("open")),
                high=_px(rec.get("high")),
                low=_px(rec.get("low")),
                close=close,
                adj_close=None,  # unadjusted source; see compute_returns
                volume=int(volume) if volume not in (None, "") else None,
            )
        )
    return out


class DatabentoPrices:
    name = SOURCE

    def __init__(
        self,
        api_key: str,
        tz: str,
        dataset: str = DATASET,
        limiter: RateLimiter | None = None,
        http=None,
        today=None,
    ):
        if not api_key:
            raise ValueError(
                "databento needs an API key (MURMUR_DATABENTO_API_KEY or [databento] api_key)"
            )
        self.api_key = api_key
        self.tz = tz
        self.dataset = dataset
        self.limiter = limiter or RateLimiter(calls=30, period=60)
        self._http = http or self._request
        self._today = today or (lambda: today_in(tz))

    # -- PriceSource

    def fetch(self, tickers: Iterable[str]) -> list[PriceBar]:
        """The last few sessions up to what the dataset has published."""
        end = self._today()
        start = end - timedelta(days=RECENT_SESSIONS * 2 + 4)
        return self.fetch_range(tickers, start, end)

    def fetch_range(self, tickers: Iterable[str], start: date, end: date) -> list[PriceBar]:
        tickers = list(tickers)
        if not tickers:
            return []
        available_end = self.available_end()
        if available_end is not None and end > available_end:
            log.info(
                "databento: %s has bars up to %s; clamping %s", self.dataset, available_end, end
            )
            end = available_end
        if end < start or not any(
            is_trading_day(d) for d in sessions_between(start, end) or [start]
        ):
            return []
        wanted = {databento_symbol(t): t for t in tickers}
        bars: list[PriceBar] = []
        symbols = list(wanted)
        for i in range(0, len(symbols), MAX_SYMBOLS):
            chunk = symbols[i : i + MAX_SYMBOLS]
            self.limiter.wait()
            text = self._http(
                "POST",
                f"{BASE}/timeseries.get_range",
                {
                    "dataset": self.dataset,
                    "schema": SCHEMA,
                    "symbols": ",".join(chunk),
                    "stype_in": "raw_symbol",
                    "start": start.isoformat(),
                    "end": (end + timedelta(days=1)).isoformat(),  # exclusive
                    "encoding": "json",
                    "pretty_px": "true",
                    "pretty_ts": "true",
                    "map_symbols": "true",
                },
            )
            bars += parse_records(text.splitlines(), wanted)
        log.info(
            "databento %s..%s: %d bars for %d/%d tickers",
            start,
            end,
            len(bars),
            len({b.ticker for b in bars}),
            len(tickers),
        )
        return bars

    # -- helpers

    def available_end(self) -> date | None:
        """The last session the dataset has a daily bar for (``metadata.get_dataset_range``)."""
        try:
            text = self._http(
                "GET", f"{BASE}/metadata.get_dataset_range", {"dataset": self.dataset}
            )
        except HttpError as e:
            log.warning("databento: dataset range unavailable (%s); not clamping", e)
            return None
        payload = json.loads(text) if text else {}
        end = (payload.get("schema", {}).get(SCHEMA) or {}).get("end") or payload.get("end")
        if not end:
            return None
        # the range end is exclusive and may carry a time; the last bar is the day before
        return date.fromisoformat(str(end)[:10]) - timedelta(days=1)

    def _request(self, method: str, url: str, params: dict) -> str:
        auth = base64.b64encode(f"{self.api_key}:".encode()).decode()
        headers = {"User-Agent": USER_AGENT, "Authorization": f"Basic {auth}"}
        body = urllib.parse.urlencode(params).encode()
        if method == "GET":
            req = urllib.request.Request(f"{url}?{body.decode()}", headers=headers)
        else:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            raise HttpError(e.code, url, e.read().decode("utf-8", "replace")) from None
