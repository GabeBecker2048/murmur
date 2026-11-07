"""The daily job: run the pipeline at ``schedule.time`` every day, retry on
failure, catch up after a restart, and keep a heartbeat file for health checks.

0.6.0 moves this loop into the API process; the logic stays the same.
"""

from __future__ import annotations

import logging
import signal
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from murmur.config import Config
from murmur.store import Store

log = logging.getLogger(__name__)

TICK_SECONDS = 60
Runner = Callable[[date, int], bool]
"""``(day, attempt) -> ok``"""


class Scheduler:
    def __init__(
        self,
        config: Config,
        runner: Runner,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        heartbeat: Callable[[], None] | None = None,
    ):
        self.config = config
        self.runner = runner
        self.zone = ZoneInfo(config.timezone)
        self._now = now or (lambda: datetime.now(self.zone))
        self._sleep = sleep
        self._heartbeat = heartbeat or (lambda: None)
        self.stop_requested = False

    # -- timing ---------------------------------------------------------------

    def now(self) -> datetime:
        return self._now().astimezone(self.zone)

    def fire_time(self, day: date) -> datetime:
        hour, minute = self.config.schedule.hour_minute
        return datetime(day.year, day.month, day.day, hour, minute, tzinfo=self.zone)

    def next_fire(self, now: datetime) -> datetime:
        today = self.fire_time(now.date())
        return today if today > now else self.fire_time(now.date() + timedelta(days=1))

    def due_day(self, now: datetime, done: Callable[[date], bool]) -> date | None:
        """Today, if its slot has passed and no successful run exists yet."""
        today = now.date()
        if now >= self.fire_time(today) and not done(today):
            return today
        return None

    # -- running --------------------------------------------------------------

    def run_with_retries(self, day: date) -> bool:
        attempts = 1 + self.config.schedule.retries
        for attempt in range(1, attempts + 1):
            log.info("scheduled run for %s, attempt %d/%d", day, attempt, attempts)
            if self.runner(day, attempt):
                log.info("run for %s succeeded", day)
                return True
            if attempt < attempts and not self.stop_requested:
                delay = self.config.schedule.retry_delay_minutes * 60
                log.warning("run for %s failed; retrying in %d min", day, delay // 60)
                self._wait(delay)
            if self.stop_requested:
                break
        log.error("run for %s failed after %d attempts", day, attempts)
        return False

    def _wait(self, seconds: float) -> None:
        deadline = self.now() + timedelta(seconds=seconds)
        while not self.stop_requested:
            self._heartbeat()
            remaining = (deadline - self.now()).total_seconds()
            if remaining <= 0:
                return
            self._sleep(min(TICK_SECONDS, remaining))

    def request_stop(self, *_args) -> None:
        log.info("stop requested; finishing the current step")
        self.stop_requested = True

    def loop(self, done: Callable[[date], bool], max_runs: int | None = None) -> int:
        """Run forever (or ``max_runs`` runs, for tests). Returns the number of runs."""
        runs = 0
        while not self.stop_requested:
            self._heartbeat()
            now = self.now()
            day = self.due_day(now, done)
            if day is not None:
                self.run_with_retries(day)
                runs += 1
                if max_runs is not None and runs >= max_runs:
                    break
                continue
            nxt = self.next_fire(now)
            log.info("next run at %s", nxt.isoformat(timespec="minutes"))
            self._wait((nxt - now).total_seconds())
        return runs


def heartbeat_writer(config: Config) -> Callable[[], None]:
    def write() -> None:
        config.data_dir.mkdir(parents=True, exist_ok=True)
        config.heartbeat_path.write_text(datetime.now(UTC_ZONE).isoformat(timespec="seconds"))

    return write


UTC_ZONE = ZoneInfo("UTC")


def heartbeat_age_seconds(config: Config, path=None) -> float | None:
    path = path or config.heartbeat_path
    try:
        stamp = datetime.fromisoformat(path.read_text().strip())
    except (OSError, ValueError):
        return None
    return (datetime.now(UTC_ZONE) - stamp).total_seconds()


def serve(config: Config) -> int:
    """Entry point for ``murmur schedule``."""
    from murmur import pipeline

    def runner(day: date, attempt: int) -> bool:
        with Store(config.store_path) as store:
            results = pipeline.run_day(store, config, day, "schedule", attempt)
        log.info("\n%s", pipeline.format_results(results))
        return all(r.ok for r in results)

    def done(day: date) -> bool:
        with Store(config.store_path) as store:
            return store.successful_run_exists(day.isoformat())

    scheduler = Scheduler(config, runner, heartbeat=heartbeat_writer(config))
    signal.signal(signal.SIGTERM, scheduler.request_stop)
    signal.signal(signal.SIGINT, scheduler.request_stop)
    log.info(
        "scheduler: daily at %s %s, %d retries %d min apart",
        config.schedule.time,
        config.timezone,
        config.schedule.retries,
        config.schedule.retry_delay_minutes,
    )
    scheduler.loop(done)
    return 0
