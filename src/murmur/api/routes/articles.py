"""Headline search across every company and day."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from murmur.api.deps import get_config, get_store
from murmur.clock import parse_date
from murmur.config import Config
from murmur.store import Store

router = APIRouter(tags=["articles"])


def _date(value: str, name: str) -> str:
    try:
        return parse_date(value).isoformat()
    except ValueError:
        raise HTTPException(422, f"{name} must be YYYY-MM-DD") from None


@router.get("/articles")
def articles(
    store: Store = Depends(get_store),
    config: Config = Depends(get_config),
    q: str | None = None,
    ticker: str | None = None,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    window: str | None = None,
    source: str | None = None,
    sentiment: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """Headlines, newest first, with their FinBERT scores. ``q`` matches words in the
    title (case-insensitive, at least 2 characters); ``sentiment`` = positive,
    negative or neutral (FinBERT's label)."""
    if q is not None and len(q.strip()) < 2:
        raise HTTPException(422, "q needs at least 2 characters")
    if sentiment not in (None, "", "positive", "negative", "neutral"):
        raise HTTPException(422, "sentiment must be positive, negative or neutral")
    limit = max(1, min(limit, 200))
    rows, total = store.articles_query(
        q=q.strip() if q else None,
        ticker=ticker.upper() if ticker else None,
        start=_date(from_, "from") if from_ else None,
        end=_date(to, "to") if to else None,
        window=window or None,
        source=source or None,
        label=sentiment or None,
        model=config.sentiment.model,
        limit=limit,
        offset=max(0, offset),
    )
    return {"items": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}
