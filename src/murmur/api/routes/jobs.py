from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Response

from murmur import jobs as jobctl
from murmur.api.auth import require_token
from murmur.api.deps import get_config, get_store, get_worker
from murmur.api.schemas import JobCreate
from murmur.clock import today_in, utc_now_iso
from murmur.config import Config
from murmur.store import Store

router = APIRouter(tags=["jobs"])
write = APIRouter(tags=["jobs"], dependencies=[Depends(require_token)])


def _row(row, with_log: bool = False) -> dict:
    out = dict(row)
    out["params"] = json.loads(out["params"]) if out.get("params") else {}
    out["progress"] = json.loads(out["progress"]) if out.get("progress") else None
    out["result"] = json.loads(out["result"]) if out.get("result") else None
    if not with_log:
        out.pop("log", None)
    return out


@router.get("/jobs/types")
def job_types():
    return {
        "types": list(jobctl.JOB_TYPES),
        "destructive": sorted(jobctl.DESTRUCTIVE),
        "params": {
            "run": ["date"],
            "universe": ["date", "rebuild"],
            "news": ["date", "from", "to"],
            "prices": ["date", "from", "to"],
            "sentiment": [],
            "analyze": ["date", "all"],
            "export": ["date", "all", "month"],
            "backup": ["keep"],
            "delete_analysis": ["date"],
            "delete_scores": ["model"],
            "delete_universe_month": ["month"],
        },
    }


@router.get("/jobs")
def list_jobs(
    store: Store = Depends(get_store), status: str | None = None, limit: int = 50, offset: int = 0
):
    limit = max(1, min(limit, 500))
    rows, total = store.jobs(status=status, limit=limit, offset=offset)
    return {"items": [_row(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/jobs/{job_id}")
def get_job(job_id: int, store: Store = Depends(get_store)):
    row = store.job(job_id)
    if row is None:
        raise HTTPException(404, "no such job")
    return _row(row)


@router.get("/jobs/{job_id}/log")
def job_log(
    job_id: int,
    after: int = 0,
    store: Store = Depends(get_store),
    worker=Depends(get_worker),
):
    """The job's captured log from byte offset ``after``; poll with the returned
    ``next`` to tail a running job."""
    row = store.job(job_id)
    if row is None:
        raise HTTPException(404, "no such job")
    text = row["log"] or ""
    if worker is not None and worker.current_job_id == job_id and worker.live_log is not None:
        text = worker.live_log.text()
    after = max(0, after)
    return {
        "id": job_id,
        "status": row["status"],
        "text": text[after:],
        "next": len(text),
    }


@write.post("/jobs", status_code=202)
def create_job(
    body: JobCreate, store: Store = Depends(get_store), config: Config = Depends(get_config)
):
    try:
        job_id = jobctl.enqueue(store, body.type, body.params, confirm=body.confirm)
    except jobctl.JobError as e:
        raise HTTPException(400, str(e)) from None
    except jobctl.DuplicateJob as e:
        raise HTTPException(409, str(e)) from None
    return {"id": job_id, "status": "queued"}


@write.delete("/jobs/{job_id}")
def cancel_job(job_id: int, store: Store = Depends(get_store)):
    new = store.request_cancel(job_id, utc_now_iso())
    if new is None:
        if store.job(job_id) is None:
            raise HTTPException(404, "no such job")
        raise HTTPException(409, "job is already finished")
    return {"id": job_id, "status": new}


@write.post("/scheduler/run-now", status_code=202)
def run_now(store: Store = Depends(get_store), config: Config = Depends(get_config)):
    day = today_in(config.timezone).isoformat()
    try:
        job_id = jobctl.enqueue(store, "run", {"date": day})
    except jobctl.DuplicateJob as e:
        raise HTTPException(409, str(e)) from None
    return {"id": job_id, "status": "queued", "date": day}


router.include_router(write)


@router.options("/jobs")
def jobs_options():
    return Response(status_code=204)
