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
    from murmur.offsite import heartbeat_path as offsite_heartbeat
    from murmur.offsite import read_status
    from murmur.scheduler import heartbeat_age_seconds

    sched_age = heartbeat_age_seconds(config, config.heartbeat_path)
    work_age = heartbeat_age_seconds(config, config.worker_heartbeat_path)
    backup_age = heartbeat_age_seconds(config, offsite_heartbeat(config))
    offsite = read_status(config)
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
        "backup": {
            "heartbeat_age": None if backup_age is None else round(backup_age),
            "ok": backup_age is not None and backup_age < STALE_SECONDS,
            "configured": bool(offsite.get("configured")),
            "last_success": offsite.get("last_success"),
            "last_error": offsite.get("last_error"),
        },
    }
    if worker is not None:
        out["worker"]["thread_alive"] = worker.is_alive()
        out["worker"]["error"] = worker.error
        out["worker"]["ok"] = worker.is_alive() and out["worker"]["ok"]
    return out


DISK_MIN_FREE = 5 * 1024**3
"""The report flags the data volume below 5 GiB free (VACUUM alone needs 2x the store)."""


def health_report(store: Store, config: Config, worker: Worker | None, now=None) -> dict:
    """Everything a watcher needs in one document. ``problems`` are worth a
    notification; ``warnings`` are context (a failure that a retry fixed, offsite
    backup not configured yet)."""
    from datetime import UTC, datetime, timedelta
    from zoneinfo import ZoneInfo

    from murmur.jobs import target_day
    from murmur.offsite import finished_snapshots, last_success_age_hours, read_status

    tz = ZoneInfo(config.timezone)
    now = (now or datetime.now(UTC)).astimezone(tz)
    problems: list[str] = []
    warnings: list[str] = []
    svc = services(config, worker)
    for role in ("scheduler", "worker"):
        if not svc[role]["ok"]:
            age = svc[role]["heartbeat_age"]
            problems.append(
                f"{role} heartbeat {'missing' if age is None else f'is {age // 60} min old'}"
            )

    # the daily runs: each slot that passed long enough ago to have finished its retries
    grace = timedelta(minutes=config.schedule.retries * config.schedule.retry_delay_minutes + 60)
    slots = []
    for slot_time, kind in config.schedule.slots:
        hour, minute = (int(x) for x in slot_time.split(":"))
        fire = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if fire > now:
            fire -= timedelta(days=1)
        day = target_day(fire, kind).isoformat()
        ok = store.successful_run_exists(day, kind)
        due = now - fire >= grace
        slots.append({"slot": slot_time, "kind": kind, "date": day, "ok": ok, "due": due})
        if due and not ok and not config.schedule.paused:
            problems.append(f"no successful {kind} run for {day} (slot {slot_time})")
    if config.schedule.paused:
        warnings.append("the scheduler is paused")

    since = (now - timedelta(hours=24)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    failed = [
        {
            "id": r["id"],
            "type": r["type"],
            "status": r["status"],
            "trigger": r["trigger"],
            "error": (r["error"] or "")[:300],
            "finished_at": r["finished_at"],
        }
        for r in store.jobs_finished_since(since, ("failed", "interrupted"))
    ]
    if failed:
        warnings.append(f"{len(failed)} job(s) failed or were interrupted in the last 24 h")

    # local snapshots (the worker's backup job) and the offsite copy
    snaps = finished_snapshots(config)
    snapshot_age = None
    if snaps:
        mtime = datetime.fromtimestamp(snaps[-1].stat().st_mtime, UTC)
        snapshot_age = round((now - mtime).total_seconds() / 3600, 1)
    if config.backup.time and (snapshot_age is None or snapshot_age > 26):
        problems.append(
            "no store snapshot yet"
            if snapshot_age is None
            else f"newest store snapshot is {snapshot_age:.0f} h old"
        )
    offsite = read_status(config)
    offsite_age = last_success_age_hours(offsite, now.astimezone(UTC))
    if offsite.get("configured") and not svc["backup"]["ok"]:
        age = svc["backup"]["heartbeat_age"]
        problems.append(
            f"backup heartbeat {'missing' if age is None else f'is {age // 60} min old'}"
        )
    if not offsite.get("configured"):
        warnings.append("offsite backup is not configured (MURMUR_S3_BUCKET unset)")
    elif offsite_age is None or offsite_age > config.backup.max_age_hours:
        problems.append(
            "offsite backup never succeeded"
            if offsite_age is None
            else f"last offsite backup was {offsite_age:.0f} h ago"
            + (f" ({offsite['last_error']})" if offsite.get("last_error") else "")
        )
    if offsite.get("configured") and offsite.get("last_key"):
        from murmur.offsite import VERIFY_STALE_DAYS

        if offsite.get("last_verify_ok") is False:
            problems.append(f"offsite restore check failed ({offsite.get('last_verify_error')})")
        else:
            ok_at = offsite.get("last_verify_success")
            age = last_success_age_hours({"last_success": ok_at}, now.astimezone(UTC))
            if age is None or age > VERIFY_STALE_DAYS * 24:
                warnings.append(
                    "the offsite copy has never been restore-checked"
                    if age is None
                    else f"last offsite restore check was {age / 24:.0f} days ago"
                )

    usage = shutil.disk_usage(config.data_dir) if config.data_dir.exists() else None
    if usage and usage.free < DISK_MIN_FREE:
        problems.append(f"data volume has {usage.free / 1024**3:.1f} GiB free")

    return {
        "ok": not problems,
        "problems": problems,
        "warnings": warnings,
        "time": utc_now_iso(),
        "version": __version__,
        "services": svc,
        "daily": slots,
        "failed_jobs_24h": failed,
        "backup": {
            "snapshot": snaps[-1].name if snaps else None,
            "snapshot_age_hours": snapshot_age,
            "offsite": {
                "configured": bool(offsite.get("configured")),
                "bucket": offsite.get("bucket"),
                "last_success": offsite.get("last_success"),
                "last_success_age_hours": None if offsite_age is None else round(offsite_age, 1),
                "last_key": offsite.get("last_key"),
                "last_error": offsite.get("last_error"),
                "last_verify": offsite.get("last_verify"),
                "last_verify_ok": offsite.get("last_verify_ok"),
                "last_verify_error": offsite.get("last_verify_error"),
            },
        },
        "disk": {
            "free_bytes": usage.free if usage else None,
            "store_bytes": config.store_path.stat().st_size if config.store_path.exists() else 0,
        },
    }


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


@router.get("/health/report")
def report(
    strict: bool = False,
    worker: Worker | None = Depends(get_worker),
    store: Store = Depends(get_store),
    config: Config = Depends(get_config),
):
    """One JSON document for a watcher (the host's murmur-check cron job). Always 200
    unless ``strict=true``, which answers 503 when there are problems."""
    from fastapi.responses import JSONResponse

    body = health_report(store, config, worker)
    return JSONResponse(body, status_code=503 if strict and not body["ok"] else 200)


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
