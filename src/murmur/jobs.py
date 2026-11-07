"""Jobs: the control plane.

Every long-running or mutating operation is a job row in the store, executed
one at a time by a single worker thread. The daily scheduler is a tick inside
that worker, so a scheduled run and a manual backfill never overlap. Pipeline
code reports progress and checks for cancellation through the module-level
``report()`` / ``check()`` helpers, which find the current job via a
ContextVar and are no-ops outside a job (the CLI).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from murmur.clock import utc_now_iso
from murmur.config import RUN_KINDS, Config, ConfigProvider
from murmur.store import Store

log = logging.getLogger(__name__)

LOG_CAP = 200_000
LOG_FLUSH_SECONDS = 5.0
SCHEDULE_PRIORITY = -10
TRIGGER_API = "api"
TRIGGER_SCHEDULE = "schedule"

DESTRUCTIVE = {"delete_analysis", "delete_scores", "delete_universe_month"}
JOB_TYPES = (
    "run",
    "backfill",
    "universe",
    "news",
    "prices",
    "sentiment",
    "analyze",
    "export",
    "backup",
    "vacuum",
    *sorted(DESTRUCTIVE),
)
BACKFILL_STAGES = ("news", "prices", "sentiment", "analyze", "export")


class JobCancelled(BaseException):
    """Raised inside a job when cancellation was requested. A BaseException so
    that ``pipeline._stage`` (which catches Exception) cannot swallow it."""


@dataclass
class Progress:
    step: str
    done: int | None = None
    total: int | None = None
    message: str | None = None
    updated_at: str = ""

    def json(self) -> str:
        return json.dumps(self.__dict__)


class JobContext:
    def __init__(self, job_id: int, store: Store, cancel: threading.Event):
        self.job_id = job_id
        self.store = store
        self.cancel = cancel
        self._last_check = 0.0

    def report(
        self,
        step: str,
        done: int | None = None,
        total: int | None = None,
        message: str | None = None,
    ) -> None:
        self.store.update_job(
            self.job_id, progress=Progress(step, done, total, message, utc_now_iso()).json()
        )
        self.check()

    def check(self) -> None:
        now = time.monotonic()
        if self.cancel.is_set():
            raise JobCancelled()
        if now - self._last_check >= 1.0:
            self._last_check = now
            if self.store.job_status(self.job_id) == "cancelling":
                self.cancel.set()
                raise JobCancelled()


_current: ContextVar[JobContext | None] = ContextVar("murmur_job", default=None)


def current() -> JobContext | None:
    return _current.get()


def report(
    step: str, done: int | None = None, total: int | None = None, message: str | None = None
) -> None:
    ctx = _current.get()
    if ctx is not None:
        ctx.report(step, done, total, message)


def check() -> None:
    ctx = _current.get()
    if ctx is not None:
        ctx.check()


# -- handlers -----------------------------------------------------------------

Handler = Callable[[Store, Config, dict], Any]
HANDLERS: dict[str, Handler] = {}


def handler(name: str):
    def register(fn: Handler) -> Handler:
        HANDLERS[name] = fn
        return fn

    return register


def _day(params: dict, config: Config) -> date:
    from murmur.clock import today_in

    return date.fromisoformat(params["date"]) if params.get("date") else today_in(config.timezone)


@handler("run")
def _run(store: Store, config: Config, params: dict) -> dict:
    from murmur import pipeline

    results = pipeline.run_day(
        store,
        config,
        _day(params, config),
        params.get("trigger", TRIGGER_API),
        params.get("attempt", 1),
        kind=params.get("kind", "full"),
    )
    out = {"run_id": store.last_run()["id"], "stages": [r.__dict__ for r in results]}
    failed = [r.name for r in results if not r.ok]
    if failed:
        raise RuntimeError(f"stages failed: {', '.join(failed)}")
    return out


@handler("backfill")
def _backfill(store: Store, config: Config, params: dict) -> dict:
    from murmur import pipeline

    return pipeline.backfill(
        store,
        config,
        date.fromisoformat(params["from"]),
        date.fromisoformat(params["to"]),
        params.get("stages", BACKFILL_STAGES),
        regenerate=bool(params.get("regenerate")),
    )


@handler("universe")
def _universe(store: Store, config: Config, params: dict) -> dict:
    from murmur.universe import applicable_month, membership_for

    day = _day(params, config)
    members, rebuilt = membership_for(store, config, day, rebuild=bool(params.get("rebuild")))
    return {"month": applicable_month(day), "tickers": len(members), "rebuilt": rebuilt}


@handler("news")
def _news(store: Store, config: Config, params: dict) -> dict:
    from murmur import pipeline

    if params.get("from"):
        start = date.fromisoformat(params["from"])
        end = date.fromisoformat(params["to"]) if params.get("to") else _day(params, config)
        return {"summary": pipeline.backfill_news(store, config, start, end)}
    return {"summary": pipeline.run_news(store, config, _day(params, config))}


@handler("prices")
def _prices(store: Store, config: Config, params: dict) -> dict:
    from murmur import pipeline

    if params.get("from"):
        start = date.fromisoformat(params["from"])
        end = date.fromisoformat(params["to"]) if params.get("to") else _day(params, config)
        return {"summary": pipeline.backfill_prices(store, config, start, end)}
    return {"summary": pipeline.run_prices(store, config, _day(params, config))}


@handler("sentiment")
def _sentiment(store: Store, config: Config, params: dict) -> dict:
    from murmur import pipeline

    return {"summary": pipeline.run_sentiment(store, config)}


@handler("analyze")
def _analyze(store: Store, config: Config, params: dict) -> dict:
    from murmur import pipeline

    pipeline.run_sentiment(store, config)
    if params.get("all"):
        days = pipeline.analyze_all(store, config)
        return {
            "days": len(days),
            "first": days[0].isoformat() if days else None,
            "last": days[-1].isoformat() if days else None,
        }
    result = pipeline.run_analyze(store, config, _day(params, config))
    return {"summary": pipeline.describe_analyze(result)}


@handler("export")
def _export(store: Store, config: Config, params: dict) -> dict:
    from murmur import export, pipeline

    if params.get("month"):
        paths = export.rebuild_month(store, config, params["month"])
    elif params.get("all"):
        paths = pipeline.export_all(store, config)
    else:
        paths = pipeline.run_export(store, config, _day(params, config))
    return {"files": len(paths)}


@handler("backup")
def _backup(store: Store, config: Config, params: dict) -> dict:
    import sqlite3

    keep = int(params.get("keep") or config.backup.keep)
    out_dir = config.backups_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest = out_dir / f"murmur_{stamp}.sqlite"
    part = dest.with_name(dest.name + ".part")  # the offsite uploader ignores *.part
    part.unlink(missing_ok=True)
    target = sqlite3.connect(part)
    try:
        store.conn.backup(target)
        (ok,) = target.execute("PRAGMA integrity_check").fetchone()
    finally:
        target.close()
    if ok != "ok":
        part.unlink(missing_ok=True)
        raise RuntimeError(f"integrity_check: {ok}")
    part.replace(dest)
    snapshots = sorted(out_dir.glob("murmur_*.sqlite"))
    for old in snapshots[:-keep] if keep > 0 else []:
        old.unlink()
    return {"file": str(dest), "bytes": dest.stat().st_size, "kept": min(len(snapshots), keep)}


@handler("vacuum")
def _vacuum(store: Store, config: Config, params: dict) -> dict:
    """Rebuild the store file to reclaim space after a prune. SQLite needs a temp
    directory that can hold the file: the container's /tmp is a small tmpfs, so point
    it at the data volume for the duration."""
    import os

    tmp = config.data_dir / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    before = config.store_path.stat().st_size
    previous = os.environ.get("SQLITE_TMPDIR")
    os.environ["SQLITE_TMPDIR"] = str(tmp)
    try:
        report("vacuum", message=f"{before / 1e6:.0f} MB before")
        store.vacuum()
    finally:
        if previous is None:
            os.environ.pop("SQLITE_TMPDIR", None)
        else:
            os.environ["SQLITE_TMPDIR"] = previous
    after = config.store_path.stat().st_size
    return {"bytes_before": before, "bytes_after": after, "freed": before - after}


@handler("delete_analysis")
def _delete_analysis(store: Store, config: Config, params: dict) -> dict:
    from murmur.export import rebuild_month

    day = params["date"]
    deleted = store.delete_analysis(day)
    report("rebuild", message=f"rebuilding {day[:7]}")
    rebuild_month(store, config, day[:7])
    return {"deleted": deleted}


@handler("delete_scores")
def _delete_scores(store: Store, config: Config, params: dict) -> dict:
    from murmur.export import rebuild_month

    deleted = store.delete_scores(params["model"])
    months = store.months_with_data()
    for i, month in enumerate(months):
        report("rebuild", i, len(months), month)
        rebuild_month(store, config, month)
    return {"deleted": deleted, "months_rebuilt": len(months)}


@handler("delete_universe_month")
def _delete_universe_month(store: Store, config: Config, params: dict) -> dict:
    return {"deleted": store.delete_universe_month(params["month"])}


# -- validation and enqueue ---------------------------------------------------


class JobError(ValueError):
    """A bad job request (unknown type, bad params, missing confirm)."""


def _iso(value, name: str) -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise JobError(f"{name} must be YYYY-MM-DD") from None


def validate(type_: str, params: dict, confirm: bool) -> tuple[dict, str | None]:
    """Normalise params; return ``(params, day)`` where ``day`` indexes run jobs."""
    if type_ not in JOB_TYPES:
        raise JobError(f"unknown job type {type_!r}; one of {', '.join(JOB_TYPES)}")
    if type_ in DESTRUCTIVE and not confirm:
        raise JobError(f"{type_} is destructive; send confirm=true")
    clean: dict = {}
    day = None
    if type_ in ("run", "universe", "news", "prices", "analyze", "export"):
        if params.get("date"):
            clean["date"] = _iso(params["date"], "date")
            day = clean["date"]
    if type_ == "run":
        kind = str(params.get("kind", "full"))
        if kind not in RUN_KINDS:
            raise JobError(f"kind must be one of {', '.join(RUN_KINDS)}")
        if kind != "full":
            clean["kind"] = kind
    if type_ == "backfill":
        if not params.get("from"):
            raise JobError("from is required")
        clean["from"] = _iso(params["from"], "from")
        clean["to"] = _iso(params.get("to") or params["from"], "to")
        if clean["to"] < clean["from"]:
            raise JobError("to must not be before from")
        stages = params.get("stages") or list(BACKFILL_STAGES)
        if isinstance(stages, str):
            stages = [x.strip() for x in stages.split(",") if x.strip()]
        bad = [x for x in stages if x not in BACKFILL_STAGES]
        if bad or not stages:
            raise JobError(f"stages must be a non-empty subset of {', '.join(BACKFILL_STAGES)}")
        clean["stages"] = [x for x in BACKFILL_STAGES if x in set(stages)]
        if params.get("regenerate"):
            if not confirm:
                raise JobError("regenerate deletes the range's analysis; send confirm=true")
            clean["regenerate"] = True
    if type_ in ("news", "prices"):
        if params.get("from"):
            clean["from"] = _iso(params["from"], "from")
            if params.get("to"):
                clean["to"] = _iso(params["to"], "to")
                if clean["to"] < clean["from"]:
                    raise JobError("to must not be before from")
    if type_ == "universe" and params.get("rebuild"):
        clean["rebuild"] = True
    if type_ in ("analyze", "export") and params.get("all"):
        clean["all"] = True
    if type_ == "export" and params.get("month"):
        if not isinstance(params["month"], str) or len(params["month"]) != 7:
            raise JobError("month must be YYYY-MM")
        clean["month"] = params["month"]
    if type_ == "backup" and params.get("keep") is not None:
        clean["keep"] = int(params["keep"])
        if clean["keep"] < 1:
            raise JobError("keep must be >= 1")
    if type_ == "delete_analysis":
        clean["date"] = _iso(params.get("date"), "date")
    if type_ == "delete_scores":
        if not params.get("model"):
            raise JobError("model is required")
        clean["model"] = str(params["model"])
    if type_ == "delete_universe_month":
        if not isinstance(params.get("month"), str) or len(params["month"]) != 7:
            raise JobError("month must be YYYY-MM")
        clean["month"] = params["month"]
    return clean, day


def enqueue(
    store: Store,
    type_: str,
    params: dict,
    trigger: str = TRIGGER_API,
    *,
    confirm: bool = False,
    priority: int = 0,
    not_before: str | None = None,
    attempt: int = 1,
) -> int:
    """Validate and queue a job. Raises ``JobError`` (400) or ``DuplicateJob`` (409)."""
    clean, day = validate(type_, params, confirm)
    dup = store.queued_duplicate(type_, clean)
    if dup is not None:
        raise DuplicateJob(dup)
    job_id = store.create_job(
        type_,
        clean,
        trigger,
        utc_now_iso(),
        day=day,
        priority=priority,
        not_before=not_before,
        attempt=attempt,
    )
    Worker.wake_all()
    return job_id


class DuplicateJob(Exception):
    def __init__(self, job_id: int):
        super().__init__(f"an identical job is already queued or running: {job_id}")
        self.job_id = job_id


# -- the worker ---------------------------------------------------------------

SETTLED = (16, 5)
"""Local time after which a trading day's bar is complete (same as the price sources)."""


def target_day(at: datetime, kind: str) -> date:
    """Which day a slot firing at ``at`` works on: the full and collect runs take that
    day; the analyze run takes the most recent session whose close is behind the slot
    (yesterday's session for a morning slot, the same day for a slot after 16:05 on a
    trading day)."""
    from murmur.clock import is_trading_day, previous_session

    today = at.date()
    if kind != "analyze":
        return today
    if is_trading_day(today) and (at.hour, at.minute) >= SETTLED:
        return today
    return previous_session(today)


class _JobLogHandler(logging.Handler):
    def __init__(self, thread_ident: int):
        super().__init__(level=logging.INFO)
        self.thread_ident = thread_ident
        self.lines: list[str] = []
        self.size = 0
        self.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S")
        )

    def emit(self, record: logging.LogRecord) -> None:
        if record.thread != self.thread_ident:
            return
        line = self.format(record) + "\n"
        self.lines.append(line)
        self.size += len(line)
        while self.size > LOG_CAP and len(self.lines) > 1:
            self.size -= len(self.lines.pop(0))

    def text(self) -> str:
        return "".join(self.lines)


RESUMABLE = {
    "backfill",
    "analyze",
    "export",
    "news",
    "prices",
    "sentiment",
    "universe",
    "backup",
    "vacuum",
}
"""Job types a restart re-queues when it finds them interrupted: all idempotent, so
re-running picks up where the store already is. ``run`` is not: the scheduler's catch-up
owns it. Destructive jobs are never re-queued."""
HEARTBEAT_SECONDS = 10.0
STALE_SECONDS = 300.0


def write_heartbeat(path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(datetime.now().astimezone().isoformat(timespec="seconds"))


def tick(provider: ConfigProvider, now: datetime | None = None) -> list[int]:
    """One scheduler tick: queue the daily run for each slot whose time has passed and
    has no successful run or active job, then write the scheduler heartbeat. Used by the
    ``murmur scheduler`` process and by the in-process ticker thread of ``murmur serve``.
    Returns the ids of jobs queued."""
    from zoneinfo import ZoneInfo

    config = provider.get()
    tz = ZoneInfo(config.timezone)
    now = (now or datetime.now(tz)).astimezone(tz)
    queued: list[int] = []
    if not config.schedule.paused:
        with Store(config.store_path, migrate=False) as store:
            for slot_time, kind in config.schedule.slots:
                job_id = _queue_slot(store, config, now, slot_time, kind)
                if job_id is not None:
                    queued.append(job_id)
            if config.backup.time:
                job_id = _queue_backup(store, config, now)
                if job_id is not None:
                    queued.append(job_id)
    write_heartbeat(config.heartbeat_path)
    if queued:
        Worker.wake_all()
    return queued


def _queue_slot(store: Store, config: Config, now: datetime, slot: str, kind: str) -> int | None:
    hour, minute = (int(x) for x in slot.split(":"))
    fire = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now < fire:
        return None
    day = target_day(fire, kind).isoformat()  # decided at the slot time, not at `now`
    if store.successful_run_exists(day, kind):
        return None
    jobs = store.run_jobs_for_day(day, kind)
    active = any(j["status"] in ("queued", "running", "cancelling") for j in jobs)
    attempts = sum(1 for j in jobs if j["status"] in ("failed", "interrupted"))
    if active or attempts >= 1 + config.schedule.retries:
        return None
    params = {"date": day, "trigger": TRIGGER_SCHEDULE, "attempt": attempts + 1}
    if kind != "full":
        params["kind"] = kind
    job_id = store.create_job(
        "run",
        params,
        TRIGGER_SCHEDULE,
        utc_now_iso(),
        day=day,
        priority=SCHEDULE_PRIORITY,
        attempt=attempts + 1,
    )
    log.info("scheduler: queued %s run %d for %s (attempt %d)", kind, job_id, day, attempts + 1)
    return job_id


def _queue_backup(store: Store, config: Config, now: datetime) -> int | None:
    """Queue the day's store snapshot once ``backup.time`` has passed, unless one was
    already queued today (local day). A failed snapshot is not retried until tomorrow:
    the health report flags it."""

    hour, minute = (int(x) for x in config.backup.time.split(":"))
    if now < now.replace(hour=hour, minute=minute, second=0, microsecond=0):
        return None
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    if store.latest_job_of_type("backup", midnight.strftime("%Y-%m-%dT%H:%M:%SZ")):
        return None
    job_id = store.create_job("backup", {}, TRIGGER_SCHEDULE, utc_now_iso())
    log.info("scheduler: queued backup %d", job_id)
    return job_id


def scheduler_loop(
    provider: ConfigProvider,
    stop: threading.Event,
    tick_seconds: float = 60.0,
    now: Callable[[], datetime] | None = None,
    once: bool = False,
) -> int:
    """The ``murmur scheduler`` process: tick, sleep, repeat. Returns ticks done."""
    ticks = 0
    while not stop.is_set():
        try:
            tick(provider, now() if now else None)
        except Exception:  # noqa: BLE001 - a bad tick must not kill the scheduler
            log.exception("scheduler tick failed")
        ticks += 1
        if once:
            break
        stop.wait(tick_seconds)
    return ticks


class Worker(threading.Thread):
    """Runs queued jobs one at a time. With ``scheduler=True`` (the single-process
    ``murmur serve``) a side thread ticks the scheduler on its own clock, so a long job
    never delays the daily run; ``murmur scheduler`` is that thread as a process."""

    _instances: list[Worker] = []

    def __init__(
        self,
        provider: ConfigProvider,
        *,
        scheduler: bool = True,
        tick_seconds: float = 60.0,
        poll_seconds: float = 5.0,
        now: Callable[[], datetime] | None = None,
    ):
        super().__init__(name="murmur-worker", daemon=True)
        self.provider = provider
        self.scheduler = scheduler
        self.tick_seconds = tick_seconds
        self.poll_seconds = poll_seconds
        self._now = now
        self._halt = threading.Event()
        self._wake = threading.Event()
        self._cancel = threading.Event()
        self.ready = threading.Event()
        """Set once the boot cleanup is done and the loop is polling."""
        self.booted_at = utc_now_iso()
        self.current_job_id: int | None = None
        self.last_tick: str | None = None
        self.live_log: _JobLogHandler | None = None
        self.error: str | None = None
        self.idle = threading.Event()
        """Set whenever the queue is empty (``murmur worker --once`` waits on it)."""
        murmur_logger = logging.getLogger("murmur")
        if murmur_logger.getEffectiveLevel() > logging.INFO:
            murmur_logger.setLevel(logging.INFO)  # job logs are captured at INFO
        Worker._instances.append(self)

    @classmethod
    def wake_all(cls) -> None:
        for w in cls._instances:
            w._wake.set()

    def stop(self, timeout: float = 60.0) -> None:
        self._halt.set()
        self._cancel.set()
        self._wake.set()
        self.join(timeout)
        if self in Worker._instances:
            Worker._instances.remove(self)

    def now(self) -> datetime:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(self.provider.get().timezone)
        return (self._now() if self._now else datetime.now(tz)).astimezone(tz)

    # -- loop

    def run(self) -> None:
        try:
            with Store(self.provider.get().store_path) as store:
                self._boot(store)
            threading.Thread(
                target=self._heartbeat_loop, name="murmur-heartbeat", daemon=True
            ).start()
            if self.scheduler:
                threading.Thread(target=self._tick_loop, name="murmur-ticker", daemon=True).start()
            self.ready.set()
            while not self._halt.is_set():
                with Store(self.provider.get().store_path, migrate=False) as store:
                    job = store.next_job(utc_now_iso())
                    if job is not None:
                        self.idle.clear()
                        self._execute(store, job)
                        continue
                    self.idle.set()
                self._wake.wait(self.poll_seconds)
                self._wake.clear()
        except Exception as e:  # the thread must never die silently
            self.error = f"{type(e).__name__}: {e}"
            log.exception("worker crashed")
            self.ready.set()
            self.idle.set()

    def _boot(self, store: Store) -> None:
        """Mark what a previous process left running as interrupted and re-queue the
        interrupted jobs that are safe to repeat. Queued jobs are kept as they are."""
        interrupted = store.mark_interrupted(utc_now_iso(), before=self.booted_at)
        for row in interrupted:
            if row["type"] not in RESUMABLE:
                continue
            params = json.loads(row["params"])
            try:
                new_id = enqueue(store, row["type"], params, trigger="restart")
            except (JobError, DuplicateJob) as e:
                log.warning("not re-queuing interrupted job %d: %s", row["id"], e)
                continue
            log.warning(
                "job %d (%s) was interrupted by a restart; re-queued as %d",
                row["id"],
                row["type"],
                new_id,
            )

    def _heartbeat_loop(self) -> None:
        while not self._halt.is_set():
            try:
                write_heartbeat(self.provider.get().worker_heartbeat_path)
            except OSError:
                pass
            self._halt.wait(HEARTBEAT_SECONDS)

    def _tick_loop(self) -> None:
        while not self._halt.is_set():
            try:
                tick(self.provider, self.now())
                self.last_tick = utc_now_iso()
            except Exception:  # noqa: BLE001
                log.exception("scheduler tick failed")
            self._halt.wait(self.tick_seconds)

    def _execute(self, store: Store, job) -> None:
        job_id = job["id"]
        params = json.loads(job["params"])
        config = self.provider.get()
        self._cancel.clear()
        self.current_job_id = job_id
        store.start_job(job_id, utc_now_iso())
        ctx = JobContext(job_id, store, self._cancel)
        token = _current.set(ctx)
        capture = _JobLogHandler(threading.get_ident())
        self.live_log = capture
        logging.getLogger().addHandler(capture)
        last_flush = [time.monotonic()]

        def maybe_flush():
            if time.monotonic() - last_flush[0] >= LOG_FLUSH_SECONDS:
                store.update_job(job_id, log=capture.text())
                last_flush[0] = time.monotonic()

        original_report = ctx.report

        def report_and_flush(*a, **kw):
            original_report(*a, **kw)
            maybe_flush()

        ctx.report = report_and_flush  # type: ignore[method-assign]
        status, result, error = "done", None, None
        log.info("job %d %s %s started", job_id, job["type"], params)
        try:
            out = HANDLERS[job["type"]](store, config, params)
            result = json.dumps(out, default=str)
        except JobCancelled:
            status = "cancelled"
            log.warning("job %d cancelled", job_id)
        except Exception as e:
            status, error = "failed", f"{type(e).__name__}: {e}"
            log.exception("job %d failed", job_id)
        finally:
            logging.getLogger().removeHandler(capture)
            _current.reset(token)
            run_id = None
            if job["type"] == "run":
                last = store.last_run()
                run_id = last["id"] if last else None
            # Queue a scheduled run's retry BEFORE marking it failed: the scheduler ticks
            # on its own thread or process, and between the two it would otherwise see a
            # failed run with nothing active and queue an immediate attempt of its own.
            if job["type"] == "run" and job["trigger"] == TRIGGER_SCHEDULE and status == "failed":
                self._queue_retry(store, config, job, params)
            store.finish_job(
                job_id,
                status,
                utc_now_iso(),
                result=result,
                error=error,
                log=capture.text(),
                run_id=run_id,
            )
            self.live_log = None
            self.current_job_id = None

    def _queue_retry(self, store: Store, config: Config, job, params: dict) -> None:
        if job["attempt"] > config.schedule.retries:
            return
        from datetime import UTC

        delay = timedelta(minutes=config.schedule.retry_delay_minutes)
        not_before = (datetime.now(UTC) + delay).strftime("%Y-%m-%dT%H:%M:%SZ")
        store.create_job(
            "run",
            {**params, "attempt": job["attempt"] + 1},
            TRIGGER_SCHEDULE,
            utc_now_iso(),
            day=job["day"],
            priority=SCHEDULE_PRIORITY,
            not_before=not_before,
            attempt=job["attempt"] + 1,
        )
        log.warning(
            "run for %s failed; retry %d queued for %s", job["day"], job["attempt"] + 1, not_before
        )


def worker_main(provider: ConfigProvider, once: bool = False, poll_seconds: float = 5.0) -> int:
    """The ``murmur worker`` process: one Worker, no scheduler, SIGTERM stops it cleanly.
    ``once`` returns as soon as the queue is empty (CI, ad-hoc draining)."""
    import signal

    worker = Worker(provider, scheduler=False, poll_seconds=poll_seconds)
    stopping = threading.Event()

    def on_signal(*_):
        log.info("stop requested; finishing the current step")
        stopping.set()
        worker._wake.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    worker.start()
    worker.ready.wait()
    try:
        while worker.is_alive() and not stopping.is_set():
            if once and worker.idle.wait(0.5) and worker.current_job_id is None:
                with Store(provider.get().store_path, migrate=False) as store:
                    if store.next_queued() is None:
                        break
            stopping.wait(0.5)
    finally:
        worker.stop(timeout=55)
    return 0 if worker.error is None else 1


def scheduler_main(provider: ConfigProvider, once: bool = False, tick_seconds: float = 60.0) -> int:
    """The ``murmur scheduler`` process."""
    import signal

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    config = provider.get()
    with Store(config.store_path):  # create or migrate the store before the first tick
        pass
    log.info(
        "scheduler: slots %s in %s, %d retries %d min apart",
        config.schedule.slots,
        config.timezone,
        config.schedule.retries,
        config.schedule.retry_delay_minutes,
    )
    scheduler_loop(provider, stop, tick_seconds=tick_seconds, once=once)
    return 0
