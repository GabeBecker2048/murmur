"""Which companies are in the index on a given day.

The index is rebuilt once a month: the ``index.size`` largest US-domiciled
companies listed on NYSE/Nasdaq by market cap, one ticker per company, each
with its share of the month's total cap (``weight``). It takes effect on the
first trading day of the month, so days before that use the previous month's
list. The top ``universe.size`` of it is the coverage: the companies whose news
is fetched and scored. Coverage membership is recorded per day in the store,
and analysis only ever reads that record.

A month in the past (a backfill) cannot use today's screener caps, so it is
**reconstructed**: each candidate's cap on the month's first trading day is its
shares outstanding then (the SEC's XBRL frames, public domain, with today's
implied share count as the fallback) times its close that day (yfinance). Such
months are stored with ``method = 'reconstructed'`` and flagged in the dataset.
The candidate pool is today's screener list, so a company that has since
shrunk below the screener floor or been delisted is missed; over a year in the
top 100 that is at most a company or two.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import date, timedelta
from typing import Protocol

from murmur.clock import (
    first_trading_day,
    month_key,
    parse_utc,
    previous_month,
    today_in,
    utc_now_iso,
)
from murmur.config import Config
from murmur.models import UniverseMember
from murmur.sources.sec import SecCompany, fetch_company_tickers, fetch_shares_outstanding
from murmur.sources.yahoo_screener import Candidate, YahooScreener
from murmur.store import Store

log = logging.getLogger(__name__)

US = "United States"
COUNTRY_TTL_DAYS = 365
LIVE = "live"
RECONSTRUCTED = "reconstructed"
STATIC = "static"
SHARES_SANITY = 0.5
"""An SEC share count below this fraction of today's implied count is treated as a
single share class of a multi-class company (Alphabet, Meta) and today's count is used."""


class UniverseSource(Protocol):
    name: str

    def build(self, month: str) -> list[UniverseMember]:
        """The ranked membership for ``month`` (``YYYY-MM``)."""
        ...

    def method(self, month: str) -> str:
        """How ``build(month)`` got its caps: live | reconstructed | static."""
        ...


class StaticUniverse:
    name = "static"

    def __init__(self, tickers: dict[str, str]):
        self.tickers = tickers

    def build(self, month: str) -> list[UniverseMember]:
        return [
            UniverseMember(ticker, name, rank=i + 1)
            for i, (ticker, name) in enumerate(sorted(self.tickers.items()))
        ]

    def method(self, month: str) -> str:
        return STATIC


class Screener(Protocol):
    def candidates(self) -> list[Candidate]: ...

    def country(self, symbol: str) -> str | None: ...


def default_closes(symbols: list[str], day: date, tz: str) -> dict[str, float]:
    from murmur.sources.yfinance_prices import closes_on

    return closes_on(symbols, day, tz)


class MarketCapUniverse:
    name = "marketcap"

    def __init__(
        self,
        store: Store,
        size: int,
        sec_contact: str,
        screener: Screener | None = None,
        min_market_cap: float = 10_000_000_000,
        sec_fetch: Callable[[str], dict[str, SecCompany]] = fetch_company_tickers,
        shares_fetch: Callable[[str, date], dict[int, float]] = fetch_shares_outstanding,
        closes_fetch: Callable[[list[str], date], dict[str, float]] | None = None,
        today: Callable[[], date] | None = None,
        tz: str = "America/New_York",
    ):
        self.store = store
        self.size = size
        self.sec_contact = sec_contact
        self.screener = screener or YahooScreener(min_market_cap=min_market_cap)
        self.sec_fetch = sec_fetch
        self.shares_fetch = shares_fetch
        self.closes_fetch = closes_fetch or (lambda symbols, day: default_closes(symbols, day, tz))
        self._today = today or (lambda: today_in(tz))
        self._candidates: list[Candidate] | None = None

    # -- which path

    def method(self, month: str) -> str:
        return RECONSTRUCTED if month < applicable_month(self._today()) else LIVE

    def build(self, month: str) -> list[UniverseMember]:
        if self.method(month) == RECONSTRUCTED:
            return self.build_historical(month)
        return self.build_live(month)

    def candidates(self) -> list[Candidate]:
        if self._candidates is None:
            self._candidates = self.screener.candidates()
        return self._candidates

    # -- live: today's screener caps, in order

    def build_live(self, month: str) -> list[UniverseMember]:
        sec = self.sec_fetch(self.sec_contact)
        candidates = self.candidates()
        log.info("universe %s: %d candidates from the screener", month, len(candidates))
        members: list[UniverseMember] = []
        for c, company in self._eligible(candidates, sec, month):
            members.append(
                UniverseMember(
                    ticker=c.symbol,
                    name=c.name,
                    market_cap=c.market_cap,
                    rank=len(members) + 1,
                    cik=company.cik,
                    exchange=c.exchange,
                )
            )
            if len(members) == self.size:
                break
        self._check_size(members, month)
        return with_weights(members)

    # -- historical: shares then x close then, re-ranked

    def build_historical(self, month: str) -> list[UniverseMember]:
        year, mm = (int(x) for x in month.split("-"))
        day = first_trading_day(year, mm)
        sec = self.sec_fetch(self.sec_contact)
        candidates = self.candidates()
        shares_then = self.shares_fetch(self.sec_contact, day)
        closes = self.closes_fetch([c.symbol for c in candidates], day)
        log.info(
            "universe %s (reconstructed as of %s): %d candidates, %d SEC share counts, %d closes",
            month,
            day,
            len(candidates),
            len(shares_then),
            len(closes),
        )
        scored: list[UniverseMember] = []
        for c, company in self._eligible(candidates, sec, month):
            close = closes.get(c.symbol)
            if not close or close <= 0:
                continue
            shares = historical_shares(c, shares_then.get(company.cik))
            scored.append(
                UniverseMember(
                    ticker=c.symbol,
                    name=c.name,
                    market_cap=shares * close,
                    cik=company.cik,
                    exchange=c.exchange,
                )
            )
        scored.sort(key=lambda m: (-(m.market_cap or 0), m.ticker))
        members = [replace(m, rank=i + 1) for i, m in enumerate(scored[: self.size])]
        self._check_size(members, month)
        return with_weights(members)

    # -- shared

    def _eligible(self, candidates: list[Candidate], sec: dict[str, SecCompany], month: str):
        """Candidates in cap order that are SEC-registered, one per CIK, US-domiciled."""
        cache = self.store.company_countries()
        now = utc_now_iso()
        seen: set[int] = set()
        skipped_sec = skipped_foreign = 0
        for c in candidates:
            company = sec.get(c.symbol)
            if company is None:
                skipped_sec += 1
                log.debug("universe: %s not in the SEC list", c.symbol)
                continue
            if company.cik in seen:
                continue  # second share class of a company already placed
            seen.add(company.cik)
            country = self._country(company, c, cache, now)
            if country != US:
                skipped_foreign += 1
                log.debug("universe: %s is %s", c.symbol, country)
                continue
            yield c, company
        log.info(
            "universe %s: %d not SEC-registered, %d non-US skipped",
            month,
            skipped_sec,
            skipped_foreign,
        )

    def _check_size(self, members: list[UniverseMember], month: str) -> None:
        if len(members) < self.size:
            if len(members) < self.size // 2:
                raise RuntimeError(f"universe: only {len(members)} of {self.size} members found")
            log.warning("universe %s: only %d of %d members found", month, len(members), self.size)

    def _country(self, company: SecCompany, c: Candidate, cache: dict, now: str) -> str | None:
        cached = cache.get(company.cik)
        if cached and cached[1]:
            age = parse_utc(now) - parse_utc(cached[1])
            if age < timedelta(days=COUNTRY_TTL_DAYS):
                return cached[0]
        country = self.screener.country(c.symbol)
        self.store.upsert_company(company.cik, c.symbol, company.name, country, now, c.exchange)
        return country


def historical_shares(c: Candidate, sec_shares: float | None) -> float:
    """Shares outstanding to use for a past month: the SEC figure when it is plausible,
    else today's implied count (cap / price from the screener)."""
    implied = c.market_cap / c.price if c.price else None
    if sec_shares and sec_shares > 0:
        if implied is None or sec_shares >= SHARES_SANITY * implied:
            return float(sec_shares)
    return float(implied) if implied else 0.0


def with_weights(members: list[UniverseMember]) -> list[UniverseMember]:
    """Each member's share of the total market cap; None when caps are unknown."""
    total = sum(m.market_cap for m in members if m.market_cap)
    if not total:
        return members
    return [replace(m, weight=(m.market_cap / total) if m.market_cap else None) for m in members]


def default_source(config: Config, store: Store) -> UniverseSource:
    if config.universe.source == "static":
        return StaticUniverse(config.universe.static)
    return MarketCapUniverse(
        store,
        size=config.index.size,
        sec_contact=config.sec.contact,
        min_market_cap=config.universe.min_market_cap,
        tz=config.timezone,
    )


def applicable_month(day: date) -> str:
    """The universe month in force on ``day``: this month from its first trading
    day on, otherwise the previous month."""
    if day >= first_trading_day(day.year, day.month):
        return month_key(day)
    return month_key(previous_month(day))


def membership_for(
    store: Store, config: Config, day: date, source: UniverseSource | None = None, rebuild=False
) -> tuple[list[UniverseMember], bool]:
    """Record and return the coverage for ``day``; builds the month's index if needed.

    Returns ``(members, rebuilt)``. ``members`` is the covered subset (the top
    ``universe.size`` of the index); the full ranked index is ``store.universe_month``.
    """
    month = applicable_month(day)
    index = [] if rebuild else store.universe_month(month)
    rebuilt = False
    if not index:
        source = source or default_source(config, store)
        index = source.build(month)
        method = source.method(month) if hasattr(source, "method") else LIVE
        store.save_universe_month(month, index, utc_now_iso(), method=method)
        rebuilt = True
    members = coverage(index, config)
    store.record_universe(day.isoformat(), members, config.universe.source)
    return members, rebuilt


def coverage(index: list[UniverseMember], config: Config) -> list[UniverseMember]:
    """The companies that get news and sentiment: the index's top ``universe.size``
    (a static universe is covered in full)."""
    if config.universe.source == "static":
        return index
    return index[: config.universe.size]


def record_universe(store: Store, config: Config, day: date) -> list[UniverseMember]:
    """0.1.0 compatibility: record the day's membership and return it."""
    return membership_for(store, config, day)[0]
