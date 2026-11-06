"""Which companies are in the index on a given day.

The universe is rebuilt once a month: the ``size`` largest US-domiciled
companies listed on NYSE/Nasdaq by market cap, one ticker per company. It
takes effect on the first trading day of the month, so days before that use
the previous month's list. Membership is recorded per day in the store, and
analysis only ever reads that record.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, timedelta
from typing import Protocol

from murmur.clock import first_trading_day, month_key, parse_utc, previous_month, utc_now_iso
from murmur.config import Config
from murmur.models import UniverseMember
from murmur.sources.sec import SecCompany, fetch_company_tickers
from murmur.sources.yahoo_screener import Candidate, YahooScreener
from murmur.store import Store

log = logging.getLogger(__name__)

US = "United States"
COUNTRY_TTL_DAYS = 365


class UniverseSource(Protocol):
    name: str

    def build(self, month: str) -> list[UniverseMember]:
        """The ranked membership for ``month`` (``YYYY-MM``)."""
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


class Screener(Protocol):
    def candidates(self) -> list[Candidate]: ...

    def country(self, symbol: str) -> str | None: ...


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
    ):
        self.store = store
        self.size = size
        self.sec_contact = sec_contact
        self.screener = screener or YahooScreener(min_market_cap=min_market_cap)
        self.sec_fetch = sec_fetch

    def build(self, month: str) -> list[UniverseMember]:
        sec = self.sec_fetch(self.sec_contact)
        candidates = self.screener.candidates()
        log.info("universe %s: %d candidates from the screener", month, len(candidates))
        cache = self.store.company_countries()
        now = utc_now_iso()
        seen: set[int] = set()
        members: list[UniverseMember] = []
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
            country = self._country(company, c.symbol, cache, now)
            if country != US:
                skipped_foreign += 1
                log.debug("universe: %s is %s", c.symbol, country)
                continue
            members.append(
                UniverseMember(
                    ticker=c.symbol,
                    name=c.name,
                    market_cap=c.market_cap,
                    rank=len(members) + 1,
                    cik=company.cik,
                )
            )
            if len(members) == self.size:
                break
        log.info(
            "universe %s: %d members (%d not SEC-registered, %d non-US skipped)",
            month,
            len(members),
            skipped_sec,
            skipped_foreign,
        )
        if len(members) < self.size:
            if len(members) < self.size // 2:
                raise RuntimeError(f"universe: only {len(members)} of {self.size} members found")
            log.warning("universe: only %d of %d members found", len(members), self.size)
        return members

    def _country(self, company: SecCompany, symbol: str, cache: dict, now: str) -> str | None:
        cached = cache.get(company.cik)
        if cached and cached[1]:
            age = parse_utc(now) - parse_utc(cached[1])
            if age < timedelta(days=COUNTRY_TTL_DAYS):
                return cached[0]
        country = self.screener.country(symbol)
        self.store.upsert_company(company.cik, symbol, company.name, country, now)
        return country


def default_source(config: Config, store: Store) -> UniverseSource:
    if config.universe.source == "static":
        return StaticUniverse(config.universe.static)
    return MarketCapUniverse(
        store,
        size=config.universe.size,
        sec_contact=config.sec.contact,
        min_market_cap=config.universe.min_market_cap,
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
    """Record and return the universe for ``day``; builds the month if needed.

    Returns ``(members, rebuilt)``.
    """
    month = applicable_month(day)
    members = [] if rebuild else store.universe_month(month)
    rebuilt = False
    if not members:
        source = source or default_source(config, store)
        members = source.build(month)
        store.save_universe_month(month, members, utc_now_iso())
        rebuilt = True
    store.record_universe(day.isoformat(), members, config.universe.source)
    return members, rebuilt


def record_universe(store: Store, config: Config, day: date) -> list[UniverseMember]:
    """0.1.0 compatibility: record the day's membership and return it."""
    return membership_for(store, config, day)[0]
