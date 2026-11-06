"""Analysis.

Daily (cross-sectional): for a trading day, assign articles to windows, compute
the three returns, aggregate sentiment per window, and correlate every window
with every return at lags -1, 0 and +1 across the universe. ``lag`` is the
return's day minus the sentiment's day in sessions: +1 means today's news
against tomorrow's return (news leads), -1 means today's news against
yesterday's return (news follows).

Rolling: as of a day, the same correlations pooled over every (ticker, day)
observation in the last 1, 3, 6 and 12 months, and per ticker over its own
series.

Both are computed for every source as well as pooled over sources (``source ==
'all'``) once more than one source has reported articles. Sentiment and
returns are always joined **by ticker**.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date

from murmur.clock import (
    DAY,
    INTRADAY,
    OVERNIGHT,
    assign_window,
    is_trading_day,
    months_before,
    previous_session,
    session_offset,
    sessions_between,
    utc_now_iso,
)
from murmur.config import Config
from murmur.store import Store

log = logging.getLogger(__name__)

MIN_PAIRS = 3
MIN_POOLED = 30
MIN_TICKER = 10
SCOPE = "pooled"
SOURCE = "all"
"""The pooled-over-sources value of the ``source`` dimension."""
POOLED = ""
"""``ticker`` value of a pooled rolling row (NULL would break the primary key)."""

WINDOWS_ALL = (OVERNIGHT, INTRADAY, DAY)
RETURN_TYPES = ("overnight", "intraday", "daily")
LAGS = (-1, 0, 1)
HORIZONS = (1, 3, 6, 12)
"""Rolling windows in months."""

MATCHED_PAIRS: tuple[tuple[str, str], ...] = (
    (OVERNIGHT, "overnight"),
    (INTRADAY, "intraday"),
    (DAY, "daily"),
)
"""The window/return pairs that cover the same clock time (the headline numbers)."""

WINDOW_MEMBERS = {OVERNIGHT: (OVERNIGHT,), INTRADAY: (INTRADAY,), DAY: (OVERNIGHT, INTRADAY)}


@dataclass(frozen=True)
class Return:
    ticker: str
    prev_date: str
    prev_close: float
    open: float
    close: float

    @property
    def overnight(self) -> float:
        return self.open / self.prev_close - 1

    @property
    def intraday(self) -> float:
        return self.close / self.open - 1

    @property
    def daily(self) -> float:
        return self.close / self.prev_close - 1

    def by_type(self, return_type: str) -> float:
        return getattr(self, return_type)


@dataclass(frozen=True)
class Pair:
    ticker: str
    n_articles: int
    sentiment: float
    ret: float


@dataclass(frozen=True)
class Correlation:
    window: str
    return_type: str
    lag: int
    method: str
    n: int
    estimate: float
    p_value: float
    ci_low: float | None
    ci_high: float | None
    tickers: tuple[str, ...] = ()
    source: str = SOURCE


@dataclass
class DayResult:
    day: date
    model: str
    trading_day: bool
    returns: dict[str, Return] = field(default_factory=dict)
    sentiment: dict[str, dict[str, tuple[int, float]]] = field(default_factory=dict)
    """window -> ticker -> (n_articles, mean_score), pooled over sources"""
    pairs: dict[tuple[str, str, int], list[Pair]] = field(default_factory=dict)
    """(window, return_type, lag) -> pairs, pooled over sources"""
    by_source: dict[str, dict[str, dict[str, tuple[int, float]]]] = field(default_factory=dict)
    """source -> window -> ticker -> (n_articles, mean_score), per source"""
    correlations: list[Correlation] = field(default_factory=list)
    """every source; ``source == 'all'`` is the pooled set"""
    note: str | None = None

    def matched(self, method: str = "pearson", source: str = SOURCE) -> list[Correlation]:
        """The lag-0 matched pairs, for one-line summaries."""
        return [
            c
            for c in self.correlations
            if c.lag == 0
            and c.method == method
            and c.source == source
            and (c.window, c.return_type) in MATCHED_PAIRS
        ]


# -- daily ------------------------------------------------------------------


def assign_windows(store: Store, tz: str) -> int:
    """Give every article with a timestamp but no window its trading day and window."""
    pending = store.articles_without_window()
    if not pending:
        return 0
    rows = []
    for article_id, published_at in pending:
        day, window = assign_window(published_at, tz)
        rows.append((day.isoformat(), window, article_id))
    store.set_article_windows(rows)
    log.debug("windows assigned to %d articles", len(rows))
    return len(rows)


def returns_available(store: Store, day: date) -> bool:
    """Bars for ``day`` and for the previous session exist."""
    if not is_trading_day(day) or not store.prices_on(day.isoformat()):
        return False
    return bool(store.prices_on(previous_session(day).isoformat()))


def compute_returns(store: Store, day: date) -> dict[str, Return]:
    """Overnight, intraday and daily returns for every ticker with a bar on
    ``day`` and on the previous session; stored in ``returns``. A split taking
    effect on ``day`` divides the previous close by its ratio, so unadjusted
    sources give the right return across the split."""
    prev_day = previous_session(day)
    today = store.bars_on(day.isoformat())
    prev = store.bars_on(prev_day.isoformat())
    splits = store.splits_on(day.isoformat())
    out: dict[str, Return] = {}
    for ticker, bar in today.items():
        before = prev.get(ticker)
        if before is None or bar["open"] is None or not before["close"] or not bar["open"]:
            continue
        if before["close"] <= 0 or bar["open"] <= 0:
            continue
        prev_close = before["close"]
        ratio = splits.get(ticker)
        if ratio and ratio > 0 and not _already_adjusted(prev_close, bar["open"], ratio):
            prev_close = prev_close / ratio
        out[ticker] = Return(ticker, prev_day.isoformat(), prev_close, bar["open"], bar["close"])
    store.upsert_returns(
        (
            r.ticker,
            day.isoformat(),
            r.prev_date,
            r.prev_close,
            r.open,
            r.close,
            r.overnight,
            r.intraday,
            r.daily,
        )
        for r in out.values()
    )
    return out


def _already_adjusted(prev_close: float, open_: float, ratio: float) -> bool:
    """True when the stored previous close already reflects the split (a source that
    restates history, like Yahoo): the raw gap then looks like a normal day while
    dividing again would fabricate a ratio-sized jump."""
    raw_gap = abs(open_ / prev_close - 1)
    adjusted_gap = abs(open_ / (prev_close / ratio) - 1)
    return raw_gap < adjusted_gap


def stored_returns(store: Store, day: date) -> dict[str, dict[str, float]]:
    """``ticker -> {overnight, intraday, daily}`` from the ``returns`` table."""
    return {r["ticker"]: {k: r[k] for k in RETURN_TYPES} for r in store.returns_on(day.isoformat())}


def window_sentiment(
    store: Store, day: date, model: str, source: str = SOURCE
) -> dict[str, dict[str, tuple[int, float]]]:
    iso = day.isoformat()
    return {
        w: store.ticker_scores(iso, members, model, source) for w, members in WINDOW_MEMBERS.items()
    }


def sources_for_analysis(store: Store) -> list[str]:
    """``'all'`` plus every source that has reported an article, once more than one has."""
    seen = store.sources_seen()
    return [SOURCE, *seen] if len(seen) > 1 else [SOURCE]


def _fisher_ci(res, n: int) -> tuple[float | None, float | None]:
    if n <= 3:
        return None, None
    ci = res.confidence_interval(0.95)
    return float(ci.low), float(ci.high)


def correlate_xy(
    x: list[float], y: list[float]
) -> list[tuple[str, float, float, float | None, float | None]]:
    """``(method, estimate, p, ci_low, ci_high)`` for Pearson and Spearman, skipping
    undefined (constant input) results."""
    import warnings

    from scipy import stats

    n = len(x)
    out = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", stats.ConstantInputWarning)
        pearson = stats.pearsonr(x, y)
        spearman = stats.spearmanr(x, y)
    if math.isfinite(pearson.statistic):
        lo, hi = _fisher_ci(pearson, n)
        out.append(("pearson", float(pearson.statistic), float(pearson.pvalue), lo, hi))
    if math.isfinite(spearman.statistic):
        out.append(("spearman", float(spearman.statistic), float(spearman.pvalue), None, None))
    return out


def correlate(
    window: str, return_type: str, lag: int, pairs: list[Pair], source: str = SOURCE
) -> list[Correlation]:
    if len(pairs) < MIN_PAIRS:
        return []
    tickers = tuple(p.ticker for p in pairs)
    return [
        Correlation(window, return_type, lag, method, len(pairs), est, p, lo, hi, tickers, source)
        for method, est, p, lo, hi in correlate_xy(
            [p.sentiment for p in pairs], [p.ret for p in pairs]
        )
    ]


def analyze_day(store: Store, config: Config, day: date, tickers: list[str]) -> DayResult:
    """Compute and store the day's returns, window sentiment and correlations
    at every lag whose return day has returns, pooled and per source.

    ``tickers`` is the universe for the day; anything outside it is ignored.
    """
    model = config.sentiment.model
    universe = set(tickers)
    iso = day.isoformat()
    assign_windows(store, config.timezone)

    result = DayResult(day=day, model=model, trading_day=is_trading_day(day))
    if not result.trading_day:
        result.note = f"{iso} is not a trading day; its news belongs to the next session"
        log.info(result.note)
        return result
    if not store.prices_on(iso):
        raise RuntimeError(
            f"no price bars for trading day {iso} "
            "(market not closed yet, or the prices stage failed)"
        )

    result.returns = {t: r for t, r in compute_returns(store, day).items() if t in universe}

    # returns by lag: lag 0 from this run, the others from the returns table
    returns_at: dict[int, dict[str, dict[str, float]]] = {
        0: {t: {k: r.by_type(k) for k in RETURN_TYPES} for t, r in result.returns.items()}
    }
    for lag in LAGS:
        if lag:
            returns_at[lag] = stored_returns(store, session_offset(day, lag))

    computed_at = utc_now_iso()
    thin = []
    for source in sources_for_analysis(store):
        sentiment = {}
        for window, scores in window_sentiment(store, day, model, source).items():
            kept = {t: v for t, v in scores.items() if t in universe}
            sentiment[window] = kept
            store.upsert_window_sentiment(iso, window, model, kept, source)
        if source == SOURCE:
            result.sentiment = sentiment
        else:
            result.by_source[source] = sentiment
        for window in WINDOWS_ALL:
            scores = sentiment[window]
            for lag in LAGS:
                rets = {t: v for t, v in returns_at[lag].items() if t in universe}
                for return_type in RETURN_TYPES:
                    pairs = [
                        Pair(t, scores[t][0], scores[t][1], rets[t][return_type])
                        for t in sorted(universe)
                        if t in scores and t in rets
                    ]
                    if source == SOURCE:
                        result.pairs[(window, return_type, lag)] = pairs
                    found = correlate(window, return_type, lag, pairs, source)
                    if (
                        not found
                        and source == SOURCE
                        and lag == 0
                        and (window, return_type) in MATCHED_PAIRS
                    ):
                        thin.append(f"{window}/{return_type} has {len(pairs)} pairs")
                    result.correlations += found
                    for c in found:
                        store.upsert_correlation(
                            {
                                "date": iso,
                                "window": c.window,
                                "return_type": c.return_type,
                                "lag": c.lag,
                                "scope": SCOPE,
                                "source": c.source,
                                "method": c.method,
                                "model": model,
                                "n": c.n,
                                "estimate": c.estimate,
                                "p_value": c.p_value,
                                "ci_low": c.ci_low,
                                "ci_high": c.ci_high,
                                "tickers": ",".join(c.tickers),
                                "computed_at": computed_at,
                            }
                        )
    if thin:
        result.note = "; ".join(thin)
        log.info(result.note)
    return result


# -- rolling ----------------------------------------------------------------


@dataclass(frozen=True)
class RollingRow:
    months: int
    window: str
    return_type: str
    lag: int
    source: str
    scope: str
    ticker: str
    method: str
    n: int
    estimate: float
    p_value: float
    ci_low: float | None
    ci_high: float | None


def rolling_analysis(
    store: Store, config: Config, as_of: date, tickers: list[str]
) -> list[RollingRow]:
    """Correlations over the last 1/3/6/12 months as of ``as_of``: pooled over
    every (ticker, day) observation, and per ticker over its own series, for
    every source and pooled over sources. Rows for ``as_of`` are replaced."""
    import pandas as pd

    model = config.sentiment.model
    universe = set(tickers)
    start = months_before(as_of, max(HORIZONS))
    sessions = sessions_between(months_before(start, 1), as_of)
    index = {d.isoformat(): i for i, d in enumerate(sessions)}

    rets = pd.DataFrame(
        [tuple(r) for r in store.returns_between(sessions[0].isoformat(), as_of.isoformat())],
        columns=["ticker", "date", *RETURN_TYPES],
    )
    rows: list[RollingRow] = []
    if not rets.empty:
        rets["s"] = rets["date"].map(index)
        rets = rets.dropna(subset=["s"])
        for source in sources_for_analysis(store):
            from murmur import jobs as jobctl

            jobctl.report("rolling", message=f"{as_of} {source}")
            ws = pd.DataFrame(
                [
                    tuple(r)
                    for r in store.window_sentiment_between(
                        start.isoformat(), as_of.isoformat(), model, source
                    )
                ],
                columns=["date", "ticker", "window", "n_articles", "mean_score"],
            )
            if ws.empty:
                continue
            ws = ws[ws["ticker"].isin(universe)].copy()
            ws["s"] = ws["date"].map(index)
            ws = ws.dropna(subset=["s"])
            rows += _rolling_rows(ws, rets, as_of, source)
    computed_at = utc_now_iso()
    store.replace_rolling(
        as_of.isoformat(),
        (
            (
                r.months,
                r.window,
                r.return_type,
                r.lag,
                r.source,
                r.scope,
                r.ticker,
                r.method,
                r.n,
                r.estimate,
                r.p_value,
                r.ci_low,
                r.ci_high,
                computed_at,
            )
            for r in rows
        ),
    )
    log.info("rolling as of %s: %d rows", as_of, len(rows))
    return rows


def _rolling_rows(ws, rets, as_of: date, source: str) -> list[RollingRow]:
    rows: list[RollingRow] = []
    for months in HORIZONS:
        horizon_start = months_before(as_of, months).isoformat()
        w_all = ws[ws["date"] >= horizon_start]
        for window in WINDOWS_ALL:
            w = w_all[w_all["window"] == window]
            if w.empty:
                continue
            for lag in LAGS:
                # the return at session s + lag pairs with sentiment at session s
                r = rets.assign(s=rets["s"] - lag)
                merged = w.merge(r, on=["ticker", "s"], suffixes=("", "_r"))
                if merged.empty:
                    continue
                for return_type in RETURN_TYPES:
                    x = merged["mean_score"].tolist()
                    y = merged[return_type].tolist()
                    if len(x) >= MIN_POOLED:
                        for method, est, p, lo, hi in correlate_xy(x, y):
                            rows.append(
                                RollingRow(
                                    months,
                                    window,
                                    return_type,
                                    lag,
                                    source,
                                    "pooled",
                                    POOLED,
                                    method,
                                    len(x),
                                    est,
                                    p,
                                    lo,
                                    hi,
                                )
                            )
                    for ticker, grp in merged.groupby("ticker"):
                        if len(grp) < MIN_TICKER:
                            continue
                        for method, est, p, lo, hi in correlate_xy(
                            grp["mean_score"].tolist(), grp[return_type].tolist()
                        ):
                            rows.append(
                                RollingRow(
                                    months,
                                    window,
                                    return_type,
                                    lag,
                                    source,
                                    "ticker",
                                    str(ticker),
                                    method,
                                    len(grp),
                                    est,
                                    p,
                                    lo,
                                    hi,
                                )
                            )
    return rows
