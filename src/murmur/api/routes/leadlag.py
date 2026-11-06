from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from murmur.api.deps import get_store
from murmur.clock import parse_date
from murmur.store import Store

router = APIRouter(tags=["leadlag"])


@router.get("/leadlag")
def leadlag(
    store: Store = Depends(get_store),
    as_of: str | None = None,
    months: int | None = 3,
    source: str | None = "all",
    scope: str | None = "pooled",
    ticker: str | None = None,
    method: str | None = "pearson",
):
    """The rolling lead/lag table as of a day. Pass ``months=0`` / ``source=`` /
    ``method=`` empty to get every horizon, source or method."""
    if as_of:
        try:
            as_of = parse_date(as_of).isoformat()
        except ValueError:
            raise HTTPException(422, "as_of must be YYYY-MM-DD") from None
    else:
        as_of = store.latest_rolling_as_of()
        if not as_of:
            raise HTTPException(404, "no rolling results yet")
    filters = {
        "months": months or None,
        "source": source or None,
        "scope": scope or None,
        "method": method or None,
    }
    if scope == "ticker" and ticker:
        filters["ticker"] = ticker.upper()
    rows = store.rolling_rows(as_of, filters)
    return {"as_of": as_of, "items": [dict(r) for r in rows], "total": len(rows)}


@router.get("/leadlag/history")
def leadlag_history(
    store: Store = Depends(get_store),
    months: int = 3,
    window: str = "day",
    return_type: str = "daily",
    lag: int = 1,
    source: str = "all",
    method: str = "pearson",
    scope: str = "pooled",
    ticker: str = "",
):
    """How one rolling number moved day by day."""
    rows = store.rolling_history(
        {
            "months": months,
            "window": window,
            "return_type": return_type,
            "lag": lag,
            "source": source,
            "scope": scope,
            "ticker": ticker.upper(),
            "method": method,
        }
    )
    return {"items": [dict(r) for r in rows], "total": len(rows)}
