from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response

from murmur.analysis import HORIZONS
from murmur.api import charts
from murmur.api.deps import get_store
from murmur.clock import parse_date
from murmur.store import Store

router = APIRouter(tags=["charts"])


@router.get("/charts/leadlag.png", responses={200: {"content": {"image/png": {}}}})
def leadlag_png(
    store: Store = Depends(get_store),
    as_of: str | None = None,
    months: int | None = None,
    source: str = "all",
    method: str = "pearson",
):
    """The lead/lag chart rendered on demand (the one PNG the API draws; the UI
    draws everything else from JSON)."""
    if as_of:
        try:
            as_of = parse_date(as_of).isoformat()
        except ValueError:
            raise HTTPException(422, "as_of must be YYYY-MM-DD") from None
    else:
        as_of = store.latest_rolling_as_of()
        if not as_of:
            raise HTTPException(404, "no rolling results yet")
    rows = [
        dict(r)
        for r in store.rolling_rows(as_of, {"scope": "pooled", "method": method, "source": source})
    ]
    horizons = [months] if months else HORIZONS
    picked = charts.pick_horizon(rows, horizons)
    if picked is None:
        raise HTTPException(404, f"no complete lead/lag table as of {as_of} for {source}/{method}")
    horizon, table = picked
    png = charts.leadlag(table, horizon, as_of, method)
    return Response(png, media_type="image/png", headers={"Cache-Control": "public, max-age=300"})
