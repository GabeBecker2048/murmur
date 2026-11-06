from __future__ import annotations

import json
import shutil

from fastapi import APIRouter, Depends, HTTPException, Request

from murmur import __version__
from murmur.api.deps import get_config, get_store, get_worker
from murmur.clock import utc_now_iso
from murmur.config import Config
from murmur.jobs import Worker
from murmur.store import SCHEMA_VERSION, Store

router = APIRouter(tags=["status"])


def _job_row(row) -> dict | None:
    if row is None:
        return None
    out = dict(row)
    out["params"] = json.loads(out["params"]) if out.get("params") else {}
    out["progress"] = json.loads(out["progress"]) if out.get("progress") else None
    out["result"] = json.loads(out["result"]) if out.get("result") else None
    out.pop("log", None)
    return out


@router.get("/health")
def health(worker: Worker | None = Depends(get_worker), store: Store = Depends(get_store)):
    store.conn.execute("SELECT 1")
    problems = []
    if worker is not None:
        if not worker.is_alive():
            problems.append("worker thread is dead" + (f": {worker.error}" if worker.error else ""))
        elif worker.scheduler and worker.last_tick is None and worker.current_job_id is None:
            problems.append("scheduler has not ticked yet")
    if problems:
        raise HTTPException(503, "; ".join(problems))
    return {"ok": True, "version": __version__, "time": utc_now_iso()}


@router.get("/status")
def status(
    config: Config = Depends(get_config),
    store: Store = Depends(get_store),
    worker: Worker | None = Depends(get_worker),
):
    usage = shutil.disk_usage(config.data_dir) if config.data_dir.exists() else None
    db = config.store_path
    wal = db.with_name(db.name + "-wal")
    running = store.job(worker.current_job_id) if worker and worker.current_job_id else None
    return {
        "version": __version__,
        "schema": SCHEMA_VERSION,
        "time": utc_now_iso(),
        "config": {
            "ops_file": str(config.source_path) if config.source_path else None,
            "settings_file": str(config.settings_path),
            "data_dir": str(config.data_dir),
            "news_sources": list(config.news.sources),
            "price_source": config.prices.source,
            "schedule": {"time": config.schedule.time, "timezone": config.timezone},
            "writes_enabled": bool(config.api.token),
        },
        "worker": {
            "alive": bool(worker and worker.is_alive()),
            "scheduler": bool(worker and worker.scheduler),
            "last_tick": worker.last_tick if worker else None,
            "error": worker.error if worker else None,
        },
        "running_job": _job_row(running),
        "next_queued_job": _job_row(store.next_queued()),
        "last_run": dict(store.last_run()) if store.last_run() else None,
        "counts": store.table_counts(),
        "latest_analysis_day": store.latest_analysis_day(),
        "latest_rolling_as_of": store.latest_rolling_as_of(),
        "disk": {
            "free_bytes": usage.free if usage else None,
            "total_bytes": usage.total if usage else None,
            "db_bytes": db.stat().st_size if db.exists() else 0,
            "wal_bytes": wal.stat().st_size if wal.exists() else 0,
        },
    }


@router.get("/latest")
def latest(config: Config = Depends(get_config), store: Store = Depends(get_store)):
    """The overview page in one call."""
    day = store.latest_analysis_day()
    as_of = store.latest_rolling_as_of()
    matched = []
    if day:
        for r in store.correlations_on(day):
            if (
                r["lag"] == 0
                and r["source"] == "all"
                and r["method"] == "pearson"
                and (r["window"], r["return_type"])
                in {("overnight", "overnight"), ("intraday", "intraday"), ("day", "daily")}
            ):
                matched.append(dict(r))
    summary = store.days_summary(day, day)[0] if day else None
    return {
        "latest_analysis_day": day,
        "latest_rolling_as_of": as_of,
        "matched_pairs": matched,
        "day": dict(summary) if summary else None,
        "last_run": dict(store.last_run()) if store.last_run() else None,
        "model": config.sentiment.model,
    }


@router.get("/logs")
def logs(request: Request, lines: int = 200):
    ring = request.app.state.ring_log
    items = list(ring.lines)[-max(1, min(lines, 2000)) :]
    return {"lines": items}
