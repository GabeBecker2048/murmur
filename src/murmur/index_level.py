"""The daily index level: one number per session for the cap-weighted index.

Holdings method, equivalent to a divisor index. At the close of each month's first
trading day the holdings are reset to that month's weights (the caps of that day, so
there is no look-ahead: the first day's own return still uses the previous month's
holdings). Each session's index return is the holdings-weighted mean of the members'
split-adjusted daily returns; the holdings then drift with those returns. The level
starts at ``index.base_level`` at the close of the base session and compounds.

A member without a return on a session counts as unchanged (``priced`` and
``weight_priced`` say how much of the index had a price); a session where no member
has a return leaves the level unchanged with ``priced = 0``. The history runs to the
latest session with returns.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from murmur.clock import first_trading_day, sessions_between
from murmur.config import Config
from murmur.store import Store

log = logging.getLogger(__name__)


@dataclass
class LevelDay:
    date: str
    level: float
    ret: float | None
    constituents: int
    priced: int
    weight_priced: float | None
    rebalance: bool
    contributions: list[tuple] = field(default_factory=list)
    """(ticker, cik, start-of-day weight, return, contribution, points)"""


def _month_of(day: date) -> str:
    """The index month whose weights are held during ``day``: a month's weights
    take effect at the close of its first session."""
    first = first_trading_day(day.year, day.month)
    if day > first:
        return f"{day:%Y-%m}"
    prev = date(day.year - 1, 12, 1) if day.month == 1 else date(day.year, day.month - 1, 1)
    return f"{prev:%Y-%m}"


def base_session(store: Store, config: Config) -> date | None:
    if config.index.base_date:
        return date.fromisoformat(config.index.base_date)
    months = store.universe_months()
    if not months:
        return None
    year, month = (int(x) for x in months[0].split("-"))
    return first_trading_day(year, month)


def compute_levels(store: Store, config: Config, upto: date) -> list[LevelDay]:
    base = base_session(store, config)
    if base is None or base > upto:
        return []
    sessions = sessions_between(base, upto)
    if not sessions:
        return []
    returns: dict[str, dict[str, float]] = {}
    for r in store.returns_between(sessions[0].isoformat(), upto.isoformat()):
        if r["daily"] is not None:
            returns.setdefault(r["date"], {})[r["ticker"]] = float(r["daily"])
    members_cache: dict[str, dict[str, tuple[float, int | None]]] = {}

    def weights(month: str) -> dict[str, tuple[float, int | None]]:
        if month not in members_cache:
            members_cache[month] = {
                m.ticker: (float(m.weight), m.cik) for m in store.universe_month(month) if m.weight
            }
        return members_cache[month]

    def reset(month: str) -> dict[str, float]:
        w = weights(month)
        total = sum(v for v, _ in w.values())
        return {t: v / total for t, (v, _) in w.items()} if total else {}

    start = sessions[0]
    holdings = reset(
        f"{start:%Y-%m}"
        if start == first_trading_day(start.year, start.month)
        else _month_of(start)
    )
    if not holdings:
        log.info("index level: no weights for the base session %s", start)
        return []
    level = config.index.base_level
    out = [LevelDay(start.isoformat(), level, None, len(holdings), 0, None, True)]
    for day in sessions[1:]:
        iso = day.isoformat()
        day_returns = returns.get(iso, {})
        total = sum(holdings.values())
        ciks = {t: c for t, (_, c) in weights(_month_of(day)).items()}
        contribs = []
        index_ret = 0.0
        priced_weight = 0.0
        for ticker, h in sorted(holdings.items()):
            w = h / total
            r = day_returns.get(ticker)
            c = w * r if r is not None else None
            if r is not None:
                index_ret += c
                priced_weight += w
            contribs.append((ticker, ciks.get(ticker), w, r, c, None if c is None else c * level))
        level *= 1 + index_ret
        holdings = {t: h * (1 + day_returns.get(t, 0.0)) for t, h in holdings.items()}
        rebalance = day == first_trading_day(day.year, day.month)
        if rebalance:
            new = reset(f"{day:%Y-%m}")
            if new:
                holdings = new
            else:
                rebalance = False  # no weights for the month yet: keep drifting
        out.append(
            LevelDay(
                iso,
                level,
                index_ret,
                len(contribs),
                sum(1 for x in contribs if x[3] is not None),
                priced_weight if day_returns else None,
                rebalance,
                contribs,
            )
        )
    return out


def update_levels(store: Store, config: Config, upto: date | None = None) -> int:
    """Recompute the whole level history and replace it in the store. ``upto``
    defaults to the latest session with returns, so a backfill of an older range
    never truncates the history."""
    if upto is None:
        days_with = store.days_with_returns()
        if not days_with:
            return 0
        upto = date.fromisoformat(days_with[-1])
    days = compute_levels(store, config, upto)
    store.replace_index_levels(
        [
            (d.date, d.level, d.ret, d.constituents, d.priced, d.weight_priced, int(d.rebalance))
            for d in days
        ],
        [(d.date, *c) for d in days for c in d.contributions],
    )
    if days:
        log.info("index level: %d sessions, %s = %.2f", len(days), days[-1].date, days[-1].level)
    return len(days)
