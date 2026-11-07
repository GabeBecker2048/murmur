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


def services(config: Config, worker: Worker | None) -> dict:
    """Liveness of the scheduler and the worker from their heartbeat files (the split
    deployment) or from the in-process thread (single-process ``murmur serve``)."""
    from murmur.jobs import STALE_SECONDS
    from murmur.scheduler import heartbeat_age_seconds

    sched_age = heartbeat_age_seconds(config, config.heartbeat_path)
    work_age = heartbeat_age_seconds(config, config.worker_heartbeat_path)
    out = {
        "mode": "single-process" if worker is not None else "split",
        "scheduler": {
            "heartbeat_age": None if sched_age is None else round(sched_age),
            "ok": sched_age is not None and sched_age < STALE_SECONDS,
        },
        "worker": {
            "heartbeat_age": None if work_age is None else round(work_age),
            "ok": work_age is not None and work_age < STALE_SECONDS,
        },
    }
    if worker is not None:
        out["worker"]["thread_alive"] = worker.is_alive()
        out["worker"]["error"] = worker.error
        out["worker"]["ok"] = worker.is_alive() and out["worker"]["ok"]
        if not worker.scheduler:
            out["scheduler"]["ok"] = out["scheduler"]["ok"]  # an external scheduler may exist
    return out


@router.get("/health")
def health(
    worker: Worker | None = Depends(get_worker),
    store: Store = Depends(get_store),
    config: Config = Depends(get_config),
):
    """503 when the store is unreachable or an in-process worker thread died. In the split
    deployment the scheduler and worker containers have their own health checks; their
    heartbeat ages are reported here but do not fail the API's own check."""
    store.conn.execute("SELECT 1")
    if worker is not None and not worker.is_alive():
        raise HTTPException(
            503, "worker thread is dead" + (f": {worker.error}" if worker.error else "")
        )
    return {
        "ok": True,
        "version": __version__,
        "time": utc_now_iso(),
        "services": services(config, worker),
    }


@router.get("/status")
def status(
    config: Config = Depends(get_config),
    store: Store = Depends(get_store),
    worker: Worker | None = Depends(get_worker),
):
    usage = shutil.disk_usage(config.data_dir) if config.data_dir.exists() else None
    svc = services(config, worker)
    db = config.store_path
    wal = db.with_name(db.name + "-wal")
    running = store.running_job()
    return {
        "version": __version__,
        "schema": SCHEMA_VERSION,
        "time": utc_now_iso(),
        "config": {
            "ops_file": str(config.source_path) if config.source_path else None,
            "settings_file": str(config.settings_path),
            "data_dir": str(config.data_dir),
            "index": {"name": config.index.name, "size": config.index.size},
            "coverage": config.universe.size,
            "news_sources": list(config.news.sources),
            "price_source": config.prices.source,
            "schedule": {
                "time": config.schedule.time,
                "analysis_time": config.schedule.analysis_time or None,
                "slots": config.schedule.slots,
                "paused": config.schedule.paused,
                "timezone": config.timezone,
            },
            "private_datasets": list(config.datasets.private),
            "writes_enabled": bool(config.api.token),
        },
        "worker": {
            "alive": svc["worker"]["ok"],
            "scheduler": svc["scheduler"]["ok"],
            "last_tick": worker.last_tick if worker else None,
            "error": worker.error if worker else None,
            "mode": svc["mode"],
            "heartbeats": {
                "scheduler": svc["scheduler"]["heartbeat_age"],
                "worker": svc["worker"]["heartbeat_age"],
            },
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


@router.get("/ui")
def ui(config: Config = Depends(get_config)):
    """What the dashboard needs before its first data call."""
    return {
        "version": __version__,
        "title": config.ui.title,
        "tagline": config.ui.tagline,
        "default_months": config.ui.default_months,
        "default_method": config.ui.default_method,
        "theme": config.ui.theme,
        "index": {
            "name": config.index.name,
            "title": config.index.title,
            "size": config.index.size,
        },
        "coverage": config.universe.size,
        "private_datasets": list(config.datasets.private),
        "writes_enabled": bool(config.api.token),
    }


@router.get("/logs")
def logs(request: Request, lines: int = 200):
    ring = request.app.state.ring_log
    items = list(ring.lines)[-max(1, min(lines, 2000)) :]
    return {"lines": items}
