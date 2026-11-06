"""Source interfaces. Concrete sources live next to this module; the pipeline
only talks to these protocols so 0.5.0 can add Finnhub, GDELT and Polygon."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from murmur.models import Article, PriceBar


class NewsSource(Protocol):
    name: str

    def fetch(self, ticker: str) -> list[Article]:
        """Recent articles about one ticker (SEC-style symbol, e.g. ``BRK.B``)."""
        ...


class PriceSource(Protocol):
    name: str

    def fetch(self, tickers: Iterable[str]) -> list[PriceBar]:
        """Recent daily bars for all tickers; partial bars for today are excluded."""
        ...


def yahoo_symbol(ticker: str) -> str:
    """Canonical tickers already use the SEC/Yahoo dash form (``BRK-B``); a dotted
    ticker from an older config is mapped for safety."""
    return ticker.upper().replace(".", "-")
