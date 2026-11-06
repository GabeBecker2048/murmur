from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from murmur.api.deps import get_config, get_store
from murmur.clock import parse_date, today_in
from murmur.config import Config
from murmur.store import Store

router = APIRouter(tags=["sources"])

MATCHED = {("overnight", "overnight"), ("intraday", "intraday"), ("day", "daily")}


@router.get("/sources")
def sources(store: Store = Depends(get_store), config: Config = Depends(get_config)):
    """Per source: coverage, overlap with other sources, and the latest matched-pair
    rolling numbers so sources can be compared on the same days and tickers."""
    stats = [dict(r) for r in store.source_stats()]
    as_of = store.latest_rolling_as_of()
    rolling = {}
    if as_of:
        for r in store.rolling_rows(as_of, {"scope": "pooled", "method": "pearson", "months": 3}):
            if (r["window"], r["return_type"]) in MATCHED:
                rolling.setdefault(r["source"], []).append(dict(r))
    for s in stats:
        s["rolling_3m"] = rolling.get(s["source"], [])
    return {
        "configured": list(config.news.sources),
        "items": stats,
        "pooled_rolling_3m": rolling.get("all", []),
        "as_of": as_of,
    }


@router.get("/sources/{source}/series")
def source_series(
    source: str,
    store: Store = Depends(get_store),
    config: Config = Depends(get_config),
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
):
    try:
        end = parse_date(to).isoformat() if to else today_in(config.timezone).isoformat()
        start = parse_date(from_).isoformat() if from_ else "0000-01-01"
    except ValueError:
        raise HTTPException(422, "dates must be YYYY-MM-DD") from None
    rows = store.source_series(source, start, end)
    return {"source": source, "items": [dict(r) for r in rows], "total": len(rows)}
