"""Daily analysis for a trading day: assign articles to news windows, compute
the three returns from open and close, aggregate sentiment per window, and
correlate each window with its matching return across the universe (lag 0).

0.4.0 adds lags -1/+1, the full window x return cross product and rolling
windows. Sentiment and returns are always joined **by ticker**.
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
    previous_session,
    utc_now_iso,
)
from murmur.config import Config
from murmur.store import Store

log = logging.getLogger(__name__)

MIN_PAIRS = 3
SCOPE = "pooled"
SOURCE = "all"
LAG = 0

WINDOW_PAIRS: tuple[tuple[str, str], ...] = (
    (OVERNIGHT, "overnight"),
    (INTRADAY, "intraday"),
    (DAY, "daily"),
)
"""(news window, return type) pairs correlated at lag 0."""

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
    method: str
    n: int
    estimate: float
    p_value: float
    ci_low: float | None
    ci_high: float | None
    tickers: tuple[str, ...]
    lag: int = LAG


@dataclass
class DayResult:
    day: date
    model: str
    trading_day: bool
    returns: dict[str, Return] = field(default_factory=dict)
    sentiment: dict[str, dict[str, tuple[int, float]]] = field(default_factory=dict)
    """window -> ticker -> (n_articles, mean_score)"""
    pairs: dict[str, list[Pair]] = field(default_factory=dict)
    """window -> pairs with the matching return"""
    correlations: list[Correlation] = field(default_factory=list)
    note: str | None = None


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
    ``day`` and on the previous session; stored in ``returns``."""
    prev_day = previous_session(day)
    today = store.bars_on(day.isoformat())
    prev = store.bars_on(prev_day.isoformat())
    out: dict[str, Return] = {}
    for ticker, bar in today.items():
        before = prev.get(ticker)
        if before is None or bar["open"] is None or not before["close"] or not bar["open"]:
            continue
        if before["close"] <= 0 or bar["open"] <= 0:
            continue
        out[ticker] = Return(
            ticker, prev_day.isoformat(), before["close"], bar["open"], bar["close"]
        )
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


def window_sentiment(
    store: Store, day: date, model: str
) -> dict[str, dict[str, tuple[int, float]]]:
    iso = day.isoformat()
    return {w: store.ticker_scores(iso, members, model) for w, members in WINDOW_MEMBERS.items()}


def _fisher_ci(res, n: int) -> tuple[float | None, float | None]:
    if n <= 3:
        return None, None
    ci = res.confidence_interval(0.95)
    return float(ci.low), float(ci.high)


def correlate(window: str, return_type: str, pairs: list[Pair]) -> list[Correlation]:
    if len(pairs) < MIN_PAIRS:
        return []
    import warnings

    from scipy import stats

    x = [p.sentiment for p in pairs]
    y = [p.ret for p in pairs]
    tickers = tuple(p.ticker for p in pairs)
    n = len(pairs)
    out: list[Correlation] = []
    with warnings.catch_warnings():
        # a constant input (every ticker flat) yields NaN, which is reported below
        warnings.simplefilter("ignore", stats.ConstantInputWarning)
        pearson = stats.pearsonr(x, y)
        spearman = stats.spearmanr(x, y)
    if math.isfinite(pearson.statistic):
        lo, hi = _fisher_ci(pearson, n)
        out.append(
            Correlation(
                window,
                return_type,
                "pearson",
                n,
                float(pearson.statistic),
                float(pearson.pvalue),
                lo,
                hi,
                tickers,
            )
        )
    else:
        log.warning("%s/%s pearson undefined (constant input) for %d pairs", window, return_type, n)
    if math.isfinite(spearman.statistic):
        out.append(
            Correlation(
                window,
                return_type,
                "spearman",
                n,
                float(spearman.statistic),
                float(spearman.pvalue),
                None,
                None,
                tickers,
            )
        )
    else:
        log.warning(
            "%s/%s spearman undefined (constant input) for %d pairs", window, return_type, n
        )
    return out


def analyze_day(store: Store, config: Config, day: date, tickers: list[str]) -> DayResult:
    """Compute and store the day's returns, window sentiment and correlations.

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
    for window, scores in window_sentiment(store, day, model).items():
        kept = {t: v for t, v in scores.items() if t in universe}
        result.sentiment[window] = kept
        store.upsert_window_sentiment(iso, window, model, kept)

    computed_at = utc_now_iso()
    thin = []
    for window, return_type in WINDOW_PAIRS:
        scores = result.sentiment[window]
        pairs = [
            Pair(t, scores[t][0], scores[t][1], result.returns[t].by_type(return_type))
            for t in sorted(universe)
            if t in scores and t in result.returns
        ]
        result.pairs[window] = pairs
        found = correlate(window, return_type, pairs)
        if not found:
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
                    "source": SOURCE,
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
