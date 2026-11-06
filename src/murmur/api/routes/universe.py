from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from murmur.api.deps import get_store
from murmur.clock import parse_date
from murmur.store import Store
from murmur.universe import applicable_month

router = APIRouter(tags=["universe"])


@router.get("/universe/months")
def months(store: Store = Depends(get_store)):
    return {"months": store.universe_months()}


@router.get("/universe")
def universe(store: Store = Depends(get_store), date: str | None = None, month: str | None = None):
    """The universe in force on a date (default: the latest month)."""
    if date:
        try:
            month = applicable_month(parse_date(date))
        except ValueError:
            raise HTTPException(422, "date must be YYYY-MM-DD") from None
    if not month:
        available = store.universe_months()
        if not available:
            raise HTTPException(404, "no universe yet")
        month = available[-1]
    members = store.universe_month(month)
    if not members:
        raise HTTPException(404, f"no universe for {month}")
    return {"month": month, "members": [m.__dict__ for m in members]}
