from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from murmur.api.deps import get_config, get_store
from murmur.clock import parse_date, today_in
from murmur.config import Config
from murmur.store import Store

router = APIRouter(tags=["correlations"])


@router.get("/correlations")
def correlations(
    store: Store = Depends(get_store),
    config: Config = Depends(get_config),
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    window: str | None = None,
    return_type: str | None = None,
    lag: int | None = None,
    source: str | None = None,
    method: str | None = None,
):
    """The daily cross-sectional correlations as a series, filterable on every key."""
    try:
        end = parse_date(to).isoformat() if to else today_in(config.timezone).isoformat()
        start = parse_date(from_).isoformat() if from_ else "0000-01-01"
    except ValueError:
        raise HTTPException(422, "dates must be YYYY-MM-DD") from None
    rows = store.correlations_between(
        start,
        end,
        {
            "window": window,
            "return_type": return_type,
            "lag": lag,
            "source": source,
            "method": method,
        },
    )
    return {"items": [dict(r) for r in rows], "total": len(rows)}
