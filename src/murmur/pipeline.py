"""Stage orchestration for one day:
universe -> news -> prices -> sentiment -> analyze -> export."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

from murmur.analysis import DayResult, analyze_day, returns_available, rolling_analysis
from murmur.clock import is_trading_day, previous_session, utc_now_iso
from murmur.config import Config
from murmur.export import export_day
from murmur.sentiment import SentimentScorer, score_pending
from murmur.sources.base import NewsSource, PriceSource
from murmur.store import Store
from murmur.universe import UniverseSource, applicable_month, membership_for

log = logging.getLogger(__name__)

BACKFILL_DAYS = 7
"""How many calendar days back the analyze stage looks for trading days whose
correlation is missing but whose price bars have since arrived. Yahoo's daily
bar for a day can vanish for a while in the evening, so the day's own run may
fail analyze; the next day's run completes it."""


def default_news_source(config: Config) -> NewsSource:
    from murmur.sources.yahoo_news import YahooNews

    return YahooNews(max_articles=config.news.max_articles)


def default_price_source(config: Config) -> PriceSource:
    from murmur.sources.yfinance_prices import YfinancePrices

    return YfinancePrices(tz=config.timezone)


def default_scorer(config: Config) -> SentimentScorer:
    from murmur.sentiment import FinBertScorer

    return FinBertScorer(
        config.sentiment.model, config.sentiment.batch_size, config.sentiment.device
    )


@dataclass
class StageResult:
    name: str
    ok: bool
    summary: str
    seconds: float


def universe_tickers(store: Store, day: date) -> list[str]:
    tickers = [m.ticker for m in store.universe_for(day.isoformat())]
    if not tickers:
        raise RuntimeError(f"no universe recorded for {day} (did the universe stage fail?)")
    return tickers


def run_universe(
    store: Store, config: Config, day: date, source: UniverseSource | None = None
) -> str:
    members, rebuilt = membership_for(store, config, day, source)
    month = applicable_month(day)
    return f"{len(members)} tickers for {month} ({'rebuilt' if rebuilt else 'cached'})"


def run_news(store: Store, config: Config, day: date, source: NewsSource | None = None) -> str:
    source = source or default_news_source(config)
    tickers = universe_tickers(store, day)
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
    if failed == len(tickers):
        raise RuntimeError(f"news: every one of {failed} tickers failed")
    return f"{linked} articles across {len(tickers) - failed} tickers, {new} new" + (
        f", {failed} tickers failed" if failed else ""
    )


def run_prices(store: Store, config: Config, day: date, source: PriceSource | None = None) -> str:
    source = source or default_price_source(config)
    tickers = universe_tickers(store, day)
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


@dataclass
class AnalyzeResult:
    today: DayResult
    backfilled: list[date]
    reanalyzed: list[date] = field(default_factory=list)
    """The previous session, re-analysed now that its lag +1 returns exist."""
    rolling_rows: int = 0

    @property
    def exported_with_today(self) -> list[date]:
        return sorted({*self.backfilled, *self.reanalyzed})


def backfill_days(store: Store, day: date) -> list[date]:
    """Trading days in the last ``BACKFILL_DAYS`` before ``day`` with no
    correlation row yet whose returns can now be computed (a bar for the day
    and one before it)."""
    out = []
    for back in range(1, BACKFILL_DAYS + 1):
        prev = day - timedelta(days=back)
        if not is_trading_day(prev) or store.correlations_on(prev.isoformat()):
            continue
        if not returns_available(store, prev):
            continue
        out.append(prev)
    return sorted(out)


def run_analyze(store: Store, config: Config, day: date) -> AnalyzeResult:
    backfilled = []
    for prev in backfill_days(store, day):
        members, _ = membership_for(store, config, prev)
        analyze_day(store, config, prev, [m.ticker for m in members])
        backfilled.append(prev)
        log.info("analyze: backfilled %s", prev)
    tickers = universe_tickers(store, day)
    today = analyze_day(store, config, day, tickers)
    reanalyzed = []
    if today.trading_day:
        # Second pass: the previous session (and any day backfilled above) gains its
        # lag +1 pairs now that the following session's returns exist, and its
        # article file picks up headlines that only arrived today.
        again = sorted({*backfilled, previous_session(day)})
        for d in again:
            if returns_available(store, d):
                members, _ = membership_for(store, config, d)
                analyze_day(store, config, d, [m.ticker for m in members])
                reanalyzed.append(d)
    rolling_rows = len(rolling_analysis(store, config, day, tickers)) if today.trading_day else 0
    return AnalyzeResult(today, backfilled, reanalyzed, rolling_rows)


def analyze_all(store: Store, config: Config) -> list[date]:
    """Recompute the daily analysis for every day with returns, oldest first,
    then the rolling analysis as of the last one. For after a schema change."""
    days = [date.fromisoformat(d) for d in store.days_with_returns()]
    for d in days:
        members, _ = membership_for(store, config, d)
        analyze_day(store, config, d, [m.ticker for m in members])
    if days:
        rolling_analysis(store, config, days[-1], universe_tickers(store, days[-1]))
    return days


def run_export(store: Store, config: Config, day: date, also: list[date] = ()) -> list[Path]:
    written = []
    for d in [*also, day]:
        written += export_day(store, config, d)
    return written


def export_all(store: Store, config: Config) -> list[Path]:
    """Rebuild the whole published tree from the store (every known day)."""
    written = []
    for iso in store.known_days():
        written += export_day(store, config, date.fromisoformat(iso))
    return written


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
        return result.note or "not a trading day"
    day_scores = result.sentiment.get("day", {})
    parts = [f"returns for {len(result.returns)} tickers, sentiment for {len(day_scores)}"]
    for c in result.matched():
        parts.append(f"{c.window}/{c.return_type} r={c.estimate:+.3f} (p={c.p_value:.3f}, n={c.n})")
    parts.append(f"{len(result.correlations)} correlation rows")
    if result.note:
        parts.append(result.note)
    return "; ".join(parts)


def describe_analyze(result: AnalyzeResult) -> str:
    text = describe_day(result.today)
    if result.rolling_rows:
        text += f"; rolling {result.rolling_rows} rows"
    if result.reanalyzed:
        text += "; re-analysed " + ", ".join(d.isoformat() for d in result.reanalyzed)
    if result.backfilled:
        text += "; backfilled " + ", ".join(d.isoformat() for d in result.backfilled)
    return text


def run_all(
    store: Store,
    config: Config,
    day: date,
    news_source: NewsSource | None = None,
    price_source: PriceSource | None = None,
    scorer: SentimentScorer | None = None,
    universe_source: UniverseSource | None = None,
) -> list[StageResult]:
    """Run every stage, continuing past failures. A non-trading day is not a failure."""
    backfilled: list[date] = []

    def analyze() -> AnalyzeResult:
        result = run_analyze(store, config, day)
        backfilled.extend(result.exported_with_today)
        return result

    stages: Iterable[tuple[str, Callable[[], object], Callable[[object], str]]] = (
        ("universe", lambda: run_universe(store, config, day, universe_source), str),
        ("news", lambda: run_news(store, config, day, news_source), str),
        ("prices", lambda: run_prices(store, config, day, price_source), str),
        ("sentiment", lambda: run_sentiment(store, config, scorer), str),
        ("analyze", analyze, describe_analyze),
        (
            "export",
            lambda: run_export(store, config, day, backfilled),
            lambda ps: f"{len(ps)} files",
        ),
    )
    return [_stage(name, fn, describe) for name, fn, describe in stages]


def run_day(
    store: Store,
    config: Config,
    day: date,
    trigger: str,
    attempt: int = 1,
    **sources,
) -> list[StageResult]:
    """``run_all`` wrapped in a row of the ``runs`` table."""
    run_id = store.start_run(day.isoformat(), trigger, attempt, utc_now_iso())
    results = run_all(store, config, day, **sources)
    ok = all(r.ok for r in results)
    store.finish_run(run_id, utc_now_iso(), ok, json.dumps([asdict(r) for r in results]))
    return results


def format_results(results: list[StageResult]) -> str:
    return "\n".join(
        f"{'ok  ' if r.ok else 'FAIL'} {r.name:<9} {r.seconds:6.1f}s  {r.summary}" for r in results
    )
