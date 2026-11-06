"""Stage orchestration for one day:
universe -> news -> prices -> sentiment -> analyze -> export,
plus the date-range backfills behind ``murmur news/prices --from``."""

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

BACKFILL_CHUNK_DAYS = 7
"""Date-range news backfills ask each source for one week at a time."""


def default_news_sources(config: Config, names: dict[str, str]) -> list[NewsSource]:
    """The configured news sources; ``names`` maps ticker -> company name (GDELT
    searches by name)."""
    out: list[NewsSource] = []
    for name in config.news.sources:
        if name == "yahoo":
            from murmur.sources.yahoo_news import YahooNews

            out.append(YahooNews(max_articles=config.news.max_articles))
        elif name == "finnhub":
            from murmur.sources.finnhub import FinnhubNews

            out.append(FinnhubNews(config.keys.finnhub))
        elif name == "gdelt":
            from murmur.sources.gdelt import GdeltNews

            out.append(GdeltNews(names))
        else:
            raise ValueError(f"unknown news source {name!r}")
    return out


def default_price_source(config: Config) -> PriceSource:
    if config.prices.source == "polygon":
        from murmur.sources.polygon import PolygonPrices

        return PolygonPrices(config.keys.polygon, tz=config.timezone)
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


def universe_members(store: Store, day: date):
    members = store.universe_for(day.isoformat())
    if not members:
        raise RuntimeError(f"no universe recorded for {day} (did the universe stage fail?)")
    return members


def universe_tickers(store: Store, day: date) -> list[str]:
    return [m.ticker for m in universe_members(store, day)]


def run_universe(
    store: Store, config: Config, day: date, source: UniverseSource | None = None
) -> str:
    members, rebuilt = membership_for(store, config, day, source)
    month = applicable_month(day)
    return f"{len(members)} tickers for {month} ({'rebuilt' if rebuilt else 'cached'})"


# -- news ---------------------------------------------------------------------


def _ingest(store: Store, sources: list[NewsSource], tickers: list[str], fetch: Callable) -> str:
    """Run ``fetch(source, ticker)`` for every source and ticker, storing what
    comes back. A ticker failing on one source is tolerated, and so is a source
    failing on every ticker as long as another source delivered (it is logged
    as an error and named in the summary); the stage fails only when every
    source failed."""
    seen_at = utc_now_iso()
    parts = []
    dead = []
    for source in sources:
        new = linked = failed = 0
        for ticker in tickers:
            try:
                articles = fetch(source, ticker)
            except Exception:
                failed += 1
                log.exception("news: %s failed for %s", source.name, ticker)
                continue
            for article in articles:
                _, is_new = store.upsert_article(article, ticker, seen_at)
                new += is_new
                linked += 1
        if tickers and failed == len(tickers):
            dead.append(source.name)
            log.error("news: %s failed for every ticker", source.name)
        parts.append(
            f"{source.name}: {linked} articles, {new} new"
            + (f", {failed} tickers failed" if failed else "")
        )
    if dead and len(dead) == len(sources):
        raise RuntimeError(f"news: every source failed ({'; '.join(parts)})")
    if dead:
        parts.append(f"DEAD: {', '.join(dead)}")
    return "; ".join(parts)


def run_news(
    store: Store, config: Config, day: date, sources: list[NewsSource] | None = None
) -> str:
    members = universe_members(store, day)
    tickers = [m.ticker for m in members]
    sources = sources or default_news_sources(config, {m.ticker: m.name for m in members})
    return _ingest(store, sources, tickers, lambda src, t: src.fetch(t))


def backfill_news(
    store: Store,
    config: Config,
    start: date,
    end: date,
    sources: list[NewsSource] | None = None,
) -> str:
    """Fetch articles published in ``[start, end]`` from every range-capable
    source, a week at a time. Uses the universe in force on ``end``."""
    members, _ = membership_for(store, config, end)
    tickers = [m.ticker for m in members]
    sources = sources or default_news_sources(config, {m.ticker: m.name for m in members})
    ranged = [s for s in sources if getattr(s, "supports_range", False)]
    if not ranged:
        raise RuntimeError(
            "news backfill needs a source with date ranges (finnhub or gdelt); "
            f"configured: {[s.name for s in sources]}"
        )
    chunks = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=BACKFILL_CHUNK_DAYS - 1), end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)

    def fetch(source, ticker):
        out = []
        for a, b in chunks:
            out += source.fetch_range(ticker, a, b)
        return out

    return f"{start}..{end}: " + _ingest(store, ranged, tickers, fetch)


# -- prices -------------------------------------------------------------------


def run_prices(store: Store, config: Config, day: date, source: PriceSource | None = None) -> str:
    source = source or default_price_source(config)
    tickers = universe_tickers(store, day)
    bars = source.fetch(tickers)
    if not bars:
        raise RuntimeError("prices: source returned no bars")
    n = store.upsert_prices(bars, source.name)
    covered = len({b.ticker for b in bars})
    return f"{n} bars for {covered}/{len(tickers)} tickers"


def backfill_prices(
    store: Store, config: Config, start: date, end: date, source: PriceSource | None = None
) -> str:
    source = source or default_price_source(config)
    members, _ = membership_for(store, config, end)
    tickers = [m.ticker for m in members]
    bars = source.fetch_range(tickers, start, end)
    if not bars:
        raise RuntimeError("prices: source returned no bars")
    n = store.upsert_prices(bars, source.name)
    return f"{start}..{end}: {n} bars for {len({b.ticker for b in bars})}/{len(tickers)} tickers"


# -- sentiment, analysis, export ---------------------------------------------


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


# -- orchestration ------------------------------------------------------------


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
    sources = sorted({c.source for c in result.correlations} - {"all"})
    parts.append(
        f"{len(result.correlations)} correlation rows"
        + (f" (per source: {', '.join(sources)})" if sources else "")
    )
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
    news_sources: list[NewsSource] | None = None,
) -> list[StageResult]:
    """Run every stage, continuing past failures. A non-trading day is not a failure."""
    backfilled: list[date] = []
    sources = news_sources or ([news_source] if news_source else None)

    def analyze() -> AnalyzeResult:
        result = run_analyze(store, config, day)
        backfilled.extend(result.exported_with_today)
        return result

    stages: Iterable[tuple[str, Callable[[], object], Callable[[object], str]]] = (
        ("universe", lambda: run_universe(store, config, day, universe_source), str),
        ("news", lambda: run_news(store, config, day, sources), str),
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
