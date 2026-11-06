from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from murmur.api.deps import get_config, get_store
from murmur.clock import is_trading_day, parse_date, today_in
from murmur.config import Config
from murmur.store import Store

router = APIRouter(tags=["days"])


def _date(text: str) -> str:
    try:
        return parse_date(text).isoformat()
    except ValueError:
        raise HTTPException(422, f"{text!r} is not YYYY-MM-DD") from None


@router.get("/days")
def list_days(
    store: Store = Depends(get_store),
    config: Config = Depends(get_config),
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
):
    end = _date(to) if to else today_in(config.timezone).isoformat()
    start = _date(from_) if from_ else "0000-01-01"
    rows = store.days_summary(start, end)
    items = []
    for r in rows:
        d = dict(r)
        d["trading_day"] = is_trading_day(parse_date(d["date"]))
        d["has_prices"] = d.pop("n_prices") > 0
        d["has_returns"] = d.pop("n_returns") > 0
        d["has_analysis"] = d.pop("n_correlations") > 0
        items.append(d)
    return {"items": items, "total": len(items)}


@router.get("/days/{date}")
def get_day(date: str, store: Store = Depends(get_store), config: Config = Depends(get_config)):
    iso = _date(date)
    summary = store.days_summary(iso, iso)
    if not summary:
        raise HTTPException(404, f"no data for {iso}")
    model = config.sentiment.model
    returns = {r["ticker"]: dict(r) for r in store.returns_on(iso)}
    sentiment = [dict(r) for r in store.window_sentiment_on(iso, model)]
    return {
        "date": iso,
        "trading_day": is_trading_day(parse_date(iso)),
        "summary": dict(summary[0]),
        "returns": returns,
        "window_sentiment": sentiment,
        "correlations": [dict(r) for r in store.correlations_on(iso)],
    }


@router.get("/days/{date}/articles")
def day_articles(
    date: str,
    store: Store = Depends(get_store),
    config: Config = Depends(get_config),
    ticker: str | None = None,
    window: str | None = None,
    source: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    iso = _date(date)
    limit = max(1, min(limit, 500))
    rows, total = store.articles_query(
        trading_day=iso,
        ticker=ticker.upper() if ticker else None,
        window=window,
        source=source,
        model=config.sentiment.model,
        limit=limit,
        offset=offset,
    )
    return {"items": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}
