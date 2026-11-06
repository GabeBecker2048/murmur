"""Source interfaces and shared helpers. Concrete sources live next to this
module; the pipeline only talks to these protocols."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from datetime import date
from typing import Protocol

from murmur import __version__
from murmur.models import Article, PriceBar

log = logging.getLogger(__name__)

USER_AGENT = f"Murmur/{__version__} (https://github.com/GabeBecker2048/murmur)"


class NewsSource(Protocol):
    name: str
    supports_range: bool

    def fetch(self, ticker: str) -> list[Article]:
        """Recent articles about one ticker (SEC-style symbol, e.g. ``BRK-B``)."""
        ...

    def fetch_range(self, ticker: str, start: date, end: date) -> list[Article]:
        """Articles published in ``[start, end]`` (inclusive, UTC dates). Only
        for sources with ``supports_range``."""
        ...


class PriceSource(Protocol):
    name: str

    def fetch(self, tickers: Iterable[str]) -> list[PriceBar]:
        """Recent daily bars for all tickers; partial bars for today are excluded."""
        ...

    def fetch_range(self, tickers: Iterable[str], start: date, end: date) -> list[PriceBar]:
        """Daily bars in ``[start, end]`` (inclusive)."""
        ...


def yahoo_symbol(ticker: str) -> str:
    """Canonical tickers already use the SEC/Yahoo dash form (``BRK-B``); a dotted
    ticker from an older config is mapped for safety."""
    return ticker.upper().replace(".", "-")


class RateLimiter:
    """At most ``calls`` requests per ``period`` seconds, by sleeping."""

    def __init__(self, calls: int, period: float, sleep=time.sleep, clock=time.monotonic):
        self.calls = calls
        self.period = period
        self._sleep = sleep
        self._clock = clock
        self._stamps: list[float] = []

    def wait(self) -> None:
        now = self._clock()
        self._stamps = [t for t in self._stamps if now - t < self.period]
        if len(self._stamps) >= self.calls:
            delay = self.period - (now - self._stamps[0])
            if delay > 0:
                self._sleep(delay)
                now = self._clock()
        self._stamps.append(now)


class HttpError(RuntimeError):
    def __init__(self, status: int, url: str, body: str):
        super().__init__(f"HTTP {status} from {url.split('?')[0]}: {body[:200]}")
        self.status = status


def get_json(
    url: str, params: dict | None = None, headers: dict | None = None, timeout: float = 30
):
    """GET a JSON document with urllib; raises ``HttpError`` on a non-2xx status."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise HttpError(e.code, url, e.read().decode("utf-8", "replace")) from None
