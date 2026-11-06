"""Companies: the join table behind every other dataset (CIK is the identity)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from murmur.api.deps import get_store
from murmur.publish import public
from murmur.store import Store

router = APIRouter(tags=["companies"])


@router.get("/companies")
def list_companies(
    store: Store = Depends(get_store), q: str | None = None, limit: int = 100, offset: int = 0
):
    """Every company ever ranked, newest ticker and name first; ``q`` matches ticker,
    name or CIK."""
    limit = max(1, min(limit, 1000))
    rows, total = store.companies_query(q, limit, offset)
    return {"items": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/companies/{key}")
def get_company(key: str, store: Store = Depends(get_store)):
    """One company by ticker or CIK, with its month-by-month index history."""
    key = key.upper()
    rows, _ = store.companies_query(key, 1000, 0)
    match = next(
        (r for r in rows if r["ticker"] == key or (r["cik"] is not None and str(r["cik"]) == key)),
        None,
    )
    if match is None:
        raise HTTPException(404, f"no company {key!r}")
    return {**dict(match), "history": public([dict(r) for r in store.ticker_history(key)])}
