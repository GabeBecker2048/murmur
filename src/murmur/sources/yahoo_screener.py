"""Yahoo's equity screener (via yfinance) ranked by market cap, plus the
per-ticker country lookup used to keep only US-domiciled companies."""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Yahoo exchange codes for NYSE, NYSE American and the three Nasdaq tiers.
# OTC (PNK, OQX, OID, OQB) is excluded.
MAJOR_EXCHANGES = {"NYQ", "ASE", "NMS", "NGM", "NCM"}
PAGE_SIZE = 250


@dataclass(frozen=True)
class Candidate:
    symbol: str
    name: str
    market_cap: float
    exchange: str
    volume: float | None


def parse_screen(payload: dict) -> list[Candidate]:
    """One ``yf.screen`` response -> candidates on a major exchange with a market cap."""
    out: list[Candidate] = []
    for q in payload.get("quotes", []):
        cap = q.get("marketCap")
        if q.get("quoteType") != "EQUITY" or q.get("exchange") not in MAJOR_EXCHANGES or not cap:
            continue
        name = q.get("longName") or q.get("shortName") or q["symbol"]
        out.append(
            Candidate(
                symbol=str(q["symbol"]).upper(),
                name=str(name),
                market_cap=float(cap),
                exchange=str(q["exchange"]),
                volume=q.get("averageDailyVolume3Month") or q.get("regularMarketVolume"),
            )
        )
    return out


class YahooScreener:
    """Pages through US-region equities in descending market-cap order."""

    def __init__(self, min_market_cap: float = 10_000_000_000, max_pages: int = 4):
        self.min_market_cap = min_market_cap
        self.max_pages = max_pages

    def candidates(self) -> list[Candidate]:
        import yfinance as yf

        query = yf.EquityQuery(
            "and",
            [
                yf.EquityQuery("eq", ["region", "us"]),
                yf.EquityQuery("gt", ["intradaymarketcap", self.min_market_cap]),
            ],
        )
        out: list[Candidate] = []
        for page in range(self.max_pages):
            payload = yf.screen(
                query,
                offset=page * PAGE_SIZE,
                size=PAGE_SIZE,
                sortField="intradaymarketcap",
                sortAsc=False,
            )
            quotes = payload.get("quotes", [])
            out += parse_screen(payload)
            log.debug("screener page %d: %d quotes, %d kept", page, len(quotes), len(out))
            if len(quotes) < PAGE_SIZE:
                break
        # The API sorts, but be safe: sort again, ties by symbol for determinism.
        out.sort(key=lambda c: (-c.market_cap, c.symbol))
        return out

    def country(self, symbol: str) -> str | None:
        import yfinance as yf

        info = yf.Ticker(symbol).info or {}
        return info.get("country")
