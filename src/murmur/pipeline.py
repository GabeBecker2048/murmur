"""Stage orchestration for one day: news -> prices -> sentiment -> analyze -> export."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from murmur.analysis import DayResult, analyze_day
from murmur.clock import utc_now_iso
from murmur.config import Config
from murmur.export import export_day
from murmur.sentiment import SentimentScorer, score_pending
from murmur.sources.base import NewsSource, PriceSource
from murmur.store import Store
from murmur.universe import record_universe

log = logging.getLogger(__name__)


def default_news_source(config: Config) -> NewsSource:
    from murmur.sources.yahoo_news import YahooNews

    return YahooNews(max_articles=config.news.max_articles)


def default_price_source(config: Config) -> PriceSource:
    from murmur.sources.yfinance_prices import YfinancePrices

    return YfinancePrices(tz=config.timezone)


def default_scorer(config: Config) -> SentimentScorer:
    from murmur.sentiment import FinBertScorer

    return FinBertScorer(config.sentiment.model, config.sentiment.batch_size)


@dataclass
class StageResult:
    name: str
    ok: bool
    summary: str
    seconds: float


def universe_tickers(store: Store, config: Config, day: date) -> list[str]:
    members = store.universe_for(day.isoformat()) or record_universe(store, config, day)
    return [m.ticker for m in members]


def run_news(store: Store, config: Config, day: date, source: NewsSource | None = None) -> str:
    source = source or default_news_source(config)
    tickers = [m.ticker for m in record_universe(store, config, day)]
    seen_at = utc_now_iso()
    new = linked = failed = 0
    for ticker in tickers:
        try:
            articles = source.fetch(ticker)
        except Exception:
            failed += 1
            log.exception("news: %s failed", ticker)
            continue
        for article in articles:
            _, is_new = store.upsert_article(article, ticker, seen_at)
            new += is_new
            linked += 1
    if tickers and failed == len(tickers):
        raise RuntimeError(f"news: every one of {failed} tickers failed")
    return f"{linked} articles across {len(tickers) - failed} tickers, {new} new" + (
        f", {failed} tickers failed" if failed else ""
    )


def run_prices(store: Store, config: Config, day: date, source: PriceSource | None = None) -> str:
    source = source or default_price_source(config)
    tickers = universe_tickers(store, config, day)
    bars = source.fetch(tickers)
    if not bars:
        raise RuntimeError("prices: source returned no bars")
    n = store.upsert_prices(bars, source.name)
    covered = len({b.ticker for b in bars})
    return f"{n} bars for {covered}/{len(tickers)} tickers"


def run_sentiment(store: Store, config: Config, scorer: SentimentScorer | None = None) -> str:
    pending = store.unscored_articles(config.sentiment.model)
    if not pending:
        return "nothing to score"
    scorer = scorer or default_scorer(config)
    if scorer.model_name != config.sentiment.model:
        raise ValueError(
            f"scorer model {scorer.model_name!r} != configured {config.sentiment.model!r}"
        )
    n = score_pending(store, scorer)
    return f"scored {n} headlines with {scorer.model_name}"


def run_analyze(store: Store, config: Config, day: date) -> DayResult:
    return analyze_day(store, config, day, universe_tickers(store, config, day))


def run_export(store: Store, config: Config, day: date) -> list[Path]:
    return export_day(store, config, day)


def _stage(name: str, fn: Callable[[], object], describe: Callable[[object], str]) -> StageResult:
    started = time.perf_counter()
    log.info("%s: starting", name)
    try:
        result = fn()
    except Exception as e:
        log.exception("%s: failed", name)
        return StageResult(name, False, f"{type(e).__name__}: {e}", time.perf_counter() - started)
    summary = describe(result)
    log.info("%s: %s", name, summary)
    return StageResult(name, True, summary, time.perf_counter() - started)


def describe_day(result: DayResult) -> str:
    if not result.trading_day:
        return f"sentiment for {len(result.sentiment)} tickers; {result.note}"
    parts = [
        f"sentiment for {len(result.sentiment)} tickers, returns for {len(result.returns)}, "
        f"{len(result.pairs)} paired"
    ]
    for c in result.correlations:
        parts.append(f"{c.method} {c.estimate:+.3f} (p={c.p_value:.3f}, n={c.n})")
    if result.note:
        parts.append(result.note)
    return "; ".join(parts)


def run_all(
    store: Store,
    config: Config,
    day: date,
    news_source: NewsSource | None = None,
    price_source: PriceSource | None = None,
    scorer: SentimentScorer | None = None,
) -> list[StageResult]:
    """Run every stage, continuing past failures. A non-trading day is not a failure."""
    stages: Iterable[tuple[str, Callable[[], object], Callable[[object], str]]] = (
        ("news", lambda: run_news(store, config, day, news_source), str),
        ("prices", lambda: run_prices(store, config, day, price_source), str),
        ("sentiment", lambda: run_sentiment(store, config, scorer), str),
        ("analyze", lambda: run_analyze(store, config, day), describe_day),
        ("export", lambda: run_export(store, config, day), lambda ps: f"{len(ps)} files"),
    )
    return [_stage(name, fn, describe) for name, fn, describe in stages]
