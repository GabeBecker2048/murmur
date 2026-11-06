from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from murmur.config import load_config
from murmur.scheduler import Scheduler, heartbeat_age_seconds, heartbeat_writer

ET = ZoneInfo("America/New_York")


def make(tmp_path, retries=2, delay=1):
    user = tmp_path / "c.toml"
    user.write_text(
        f'data_dir = "{tmp_path / "data"}"\n'
        f"[schedule]\nretries = {retries}\nretry_delay_minutes = {delay}\n"
    )
    return load_config(user, env={})


class Clock:
    def __init__(self, start):
        self.t = start
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += timedelta(seconds=s)


def test_next_fire_and_due(tmp_path):
    cfg = make(tmp_path)
    sched = Scheduler(
        cfg, runner=lambda d, a: True, now=lambda: datetime(2026, 11, 5, 12, tzinfo=ET)
    )
    now = datetime(2026, 11, 5, 12, tzinfo=ET)
    assert sched.next_fire(now) == datetime(2026, 11, 5, 18, tzinfo=ET)
    assert sched.next_fire(datetime(2026, 11, 5, 18, 0, 1, tzinfo=ET)) == datetime(
        2026, 11, 6, 18, tzinfo=ET
    )
    assert sched.due_day(now, done=lambda d: False) is None
    late = datetime(2026, 11, 5, 19, tzinfo=ET)
    assert sched.due_day(late, done=lambda d: False) == date(2026, 11, 5)  # catch-up after restart
    assert sched.due_day(late, done=lambda d: True) is None


def test_loop_runs_at_the_slot_and_retries(tmp_path):
    cfg = make(tmp_path, retries=2, delay=1)
    clock = Clock(datetime(2026, 11, 5, 17, 58, tzinfo=ET))
    outcomes = {date(2026, 11, 5): [False, True]}  # first attempt fails, second succeeds
    calls = []

    def runner(day, attempt):
        calls.append((day, attempt))
        ok = outcomes[day].pop(0)
        if ok:
            successes.add(day)
        return ok

    successes = set()
    beats = []
    sched = Scheduler(
        cfg, runner, now=clock.now, sleep=clock.sleep, heartbeat=lambda: beats.append(clock.t)
    )
    runs = sched.loop(done=lambda d: d in successes, max_runs=1)
    assert runs == 1
    assert calls == [(date(2026, 11, 5), 1), (date(2026, 11, 5), 2)]
    assert clock.t >= datetime(2026, 11, 5, 18, 1, tzinfo=ET)  # slot + 1 min retry delay
    assert max(clock.slept) <= 60  # always interruptible
    assert len(beats) >= 2


def test_gives_up_after_retries_and_stop_interrupts(tmp_path):
    cfg = make(tmp_path, retries=1, delay=30)
    clock = Clock(datetime(2026, 11, 5, 18, 30, tzinfo=ET))
    attempts = []

    def runner(day, attempt):
        attempts.append(attempt)
        if attempt == 1:
            sched.request_stop()  # SIGTERM during the retry wait
        return False

    sched = Scheduler(cfg, runner, now=clock.now, sleep=clock.sleep)
    assert sched.run_with_retries(date(2026, 11, 5)) is False
    assert attempts == [1]  # stop prevented the retry
    assert clock.slept == []


def test_heartbeat(tmp_path):
    cfg = make(tmp_path)
    assert heartbeat_age_seconds(cfg) is None
    heartbeat_writer(cfg)()
    age = heartbeat_age_seconds(cfg)
    assert age is not None and 0 <= age < 5
