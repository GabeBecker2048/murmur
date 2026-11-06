from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from murmur.api.deps import get_config, get_store
from murmur.clock import parse_date, today_in
from murmur.config import Config
from murmur.publish import public
from murmur.store import Store
from murmur.universe import applicable_month

router = APIRouter(tags=["tickers"])


def _date(text: str) -> str:
    try:
        return parse_date(text).isoformat()
    except ValueError:
        raise HTTPException(422, f"{text!r} is not YYYY-MM-DD") from None


@router.get("/tickers")
def list_tickers(
    store: Store = Depends(get_store), date: str | None = None, month: str | None = None
):
    """The universe of a month with article and price coverage for that month."""
    if date:
        month = applicable_month(parse_date(_date(date)))
    if not month:
        available = store.universe_months()
        if not available:
            raise HTTPException(404, "no universe yet")
        month = available[-1]
    rows = store.tickers_coverage(month)
    if not rows:
        raise HTTPException(404, f"no universe for {month}")
    return {"month": month, "items": public([dict(r) for r in rows])}


@router.get("/tickers/{ticker}")
def get_ticker(ticker: str, store: Store = Depends(get_store)):
    info = store.ticker_info(ticker.upper())
    if info is None:
        raise HTTPException(404, f"{ticker.upper()} has never been in the universe")
    return public(info)


@router.get("/tickers/{ticker}/series")
def ticker_series(
    ticker: str,
    store: Store = Depends(get_store),
    config: Config = Depends(get_config),
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
):
    end = _date(to) if to else today_in(config.timezone).isoformat()
    start = _date(from_) if from_ else "0000-01-01"
    rows = store.ticker_series(ticker.upper(), start, end)
    return {"ticker": ticker.upper(), "items": [dict(r) for r in rows], "total": len(rows)}


@router.get("/tickers/{ticker}/articles")
def ticker_articles(
    ticker: str,
    store: Store = Depends(get_store),
    config: Config = Depends(get_config),
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    window: str | None = None,
    source: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    limit = max(1, min(limit, 500))
    rows, total = store.articles_query(
        ticker=ticker.upper(),
        start=_date(from_) if from_ else None,
        end=_date(to) if to else None,
        window=window,
        source=source,
        model=config.sentiment.model,
        limit=limit,
        offset=offset,
    )
    return {"items": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/tickers/{ticker}/leadlag")
def ticker_leadlag(
    ticker: str,
    store: Store = Depends(get_store),
    as_of: str | None = None,
    months: int | None = None,
    method: str = "pearson",
):
    as_of = _date(as_of) if as_of else store.latest_rolling_as_of()
    if not as_of:
        raise HTTPException(404, "no rolling results yet")
    rows = store.rolling_rows(
        as_of, {"scope": "ticker", "ticker": ticker.upper(), "months": months, "method": method}
    )
    return {"ticker": ticker.upper(), "as_of": as_of, "items": [dict(r) for r in rows]}
