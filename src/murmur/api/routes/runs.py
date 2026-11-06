from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException

from murmur.api.deps import get_store
from murmur.store import Store

router = APIRouter(tags=["runs"])


def _run(row) -> dict:
    out = dict(row)
    out["stages"] = json.loads(out["stages"]) if out.get("stages") else []
    return out


@router.get("/runs")
def list_runs(store: Store = Depends(get_store), limit: int = 20, offset: int = 0):
    limit = max(1, min(limit, 500))
    total = store.count("runs")
    rows = store.conn.execute(
        "SELECT * FROM runs ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
    ).fetchall()
    return {"items": [_run(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/runs/{run_id}")
def get_run(run_id: int, store: Store = Depends(get_store)):
    row = store.run(run_id)
    if row is None:
        raise HTTPException(404, "no such run")
    return _run(row)
