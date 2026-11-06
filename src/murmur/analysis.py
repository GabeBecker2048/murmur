"""Daily analysis: per-ticker sentiment, daily return, and their correlation
across the universe. Sentiment and returns are joined **by ticker**.

0.3.0 adds overnight/intraday windows and returns; 0.4.0 adds lags and rolling
windows. This module only knows one window ("day") and one lag (0).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date

from murmur.clock import utc_now_iso, window_bounds
from murmur.config import Config
from murmur.store import Store

log = logging.getLogger(__name__)

MIN_PAIRS = 3
WINDOW = "day"
SCOPE = "pooled"
SOURCE = "all"


@dataclass(frozen=True)
class Correlation:
    method: str
    n: int
    estimate: float
    p_value: float
    ci_low: float | None
    ci_high: float | None
    tickers: tuple[str, ...]


@dataclass(frozen=True)
class Pair:
    ticker: str
    n_articles: int
    sentiment: float
    prev_close: float
    close: float

    @property
    def ret(self) -> float:
        return self.close / self.prev_close - 1


@dataclass
class DayResult:
    day: date
    model: str
    trading_day: bool
    sentiment: dict[str, tuple[int, float]] = field(default_factory=dict)
    """ticker -> (n_articles, mean_score) for tickers with at least one article."""
    returns: dict[str, tuple[float, float]] = field(default_factory=dict)
    """ticker -> (prev_close, close) for tickers with a bar on the day and one before."""
    pairs: list[Pair] = field(default_factory=list)
    correlations: list[Correlation] = field(default_factory=list)
    note: str | None = None


def daily_sentiment(store: Store, day: date, tz: str, model: str) -> dict[str, tuple[int, float]]:
    start, end = window_bounds(day, tz)
    return store.ticker_scores_in_window(start, end, model)


def daily_returns(store: Store, day: date) -> dict[str, tuple[float, float]]:
    """``ticker -> (prev_close, close)`` for tickers whose latest bar is on ``day``."""
    iso = day.isoformat()
    out: dict[str, tuple[float, float]] = {}
    for ticker, rows in store.last_two_closes(iso).items():
        if len(rows) < 2 or rows[0][0] != iso:
            continue
        (_, close), (_, prev_close) = rows
        if prev_close and prev_close > 0:
            out[ticker] = (prev_close, close)
    return out


def correlate(pairs: list[Pair]) -> list[Correlation]:
    if len(pairs) < MIN_PAIRS:
        return []
    from scipy import stats

    x = [p.sentiment for p in pairs]
    y = [p.ret for p in pairs]
    tickers = tuple(p.ticker for p in pairs)
    n = len(pairs)
    out: list[Correlation] = []

    pearson = stats.pearsonr(x, y)
    if math.isfinite(pearson.statistic):
        ci_low = ci_high = None
        if n > 3:
            ci = pearson.confidence_interval(0.95)
            ci_low, ci_high = float(ci.low), float(ci.high)
        out.append(
            Correlation(
                "pearson",
                n,
                float(pearson.statistic),
                float(pearson.pvalue),
                ci_low,
                ci_high,
                tickers,
            )
        )
    else:
        log.warning("pearson undefined (constant input) for %d pairs", n)

    spearman = stats.spearmanr(x, y)
    if math.isfinite(spearman.statistic):
        out.append(
            Correlation(
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
        log.warning("spearman undefined (constant input) for %d pairs", n)
    return out


def analyze_day(store: Store, config: Config, day: date, tickers: list[str]) -> DayResult:
    """Compute and store the day's per-ticker sentiment and pooled correlations.

    ``tickers`` is the universe for the day; anything outside it is ignored.
    """
    model = config.sentiment.model
    universe = set(tickers)
    iso = day.isoformat()

    sentiment = {
        t: v
        for t, v in daily_sentiment(store, day, config.timezone, model).items()
        if t in universe
    }
    store.upsert_daily_sentiment(iso, model, sentiment)
    result = DayResult(day=day, model=model, trading_day=bool(store.prices_on(iso)))
    result.sentiment = sentiment
    if not result.trading_day:
        result.note = f"{iso} is not a trading day (no price bars); correlation skipped"
        log.info(result.note)
        return result

    result.returns = {t: v for t, v in daily_returns(store, day).items() if t in universe}
    result.pairs = [
        Pair(t, sentiment[t][0], sentiment[t][1], *result.returns[t])
        for t in sorted(universe)
        if t in sentiment and t in result.returns
    ]
    result.correlations = correlate(result.pairs)
    if not result.correlations:
        result.note = f"only {len(result.pairs)} tickers have both sentiment and a return"
        log.info(result.note)
    computed_at = utc_now_iso()
    for c in result.correlations:
        store.upsert_correlation(
            {
                "date": iso,
                "window": WINDOW,
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
    return result
