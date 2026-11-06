"""The job worker: lifecycle, progress, cancellation, logs, the scheduler tick, retries."""

import json
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from murmur import jobs
from murmur.config import ConfigProvider
from murmur.store import Store

ET = ZoneInfo("America/New_York")


@pytest.fixture
def provider(tmp_path):
    ops = tmp_path / "murmur.toml"
    ops.write_text(
        f'data_dir = "{tmp_path / "data"}"\n'
        '[universe]\nsource = "static"\n[universe.static]\nAAPL = "Apple"\n'
        "[schedule]\nretries = 1\nretry_delay_minutes = 1\n"
    )
    provider = ConfigProvider(ops, env={})
    with Store(provider.get().store_path):
        pass  # create and migrate the file once, as the app does before serving
    return provider


@pytest.fixture
def sleep_job(monkeypatch):
    """A test-only job type that reports progress and can be cancelled or made to fail."""
    behaviour = {"steps": 5, "fail": False, "delay": 0.02}

    def handler(store, config, params):
        import logging

        logging.getLogger("murmur.test").info("hello from the job")
        for i in range(behaviour["steps"]):
            jobs.report("sleep", i, behaviour["steps"], f"step {i}")
            time.sleep(behaviour["delay"])
        if behaviour["fail"]:
            raise RuntimeError("boom")
        return {"steps": behaviour["steps"]}

    monkeypatch.setitem(jobs.HANDLERS, "sleep", handler)
    monkeypatch.setattr(jobs, "JOB_TYPES", (*jobs.JOB_TYPES, "sleep"))
    return behaviour


def wait_for(store_path, job_id, statuses, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with Store(store_path) as s:
            st = s.job_status(job_id)
        if st in statuses:
            return st
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not reach {statuses} (is {st})")


def test_job_runs_with_progress_result_and_log(provider, sleep_job):
    cfg = provider.get()
    worker = jobs.Worker(provider, scheduler=False, tick_seconds=0.05)
    worker.start()
    try:
        with Store(cfg.store_path) as s:
            jid = jobs.enqueue(s, "sleep", {})
            with pytest.raises(jobs.DuplicateJob):
                jobs.enqueue(s, "sleep", {})
        assert wait_for(cfg.store_path, jid, {"done"}) == "done"
        with Store(cfg.store_path) as s:
            j = s.job(jid)
        assert json.loads(j["result"]) == {"steps": 5}
        assert json.loads(j["progress"])["done"] == 4
        assert "hello from the job" in j["log"] and "started" in j["log"]
        assert j["started_at"] and j["finished_at"]
    finally:
        worker.stop()


def test_cancel_running_job(provider, sleep_job):
    sleep_job["steps"], sleep_job["delay"] = 200, 0.02
    cfg = provider.get()
    worker = jobs.Worker(provider, scheduler=False, tick_seconds=0.05)
    worker.start()
    try:
        with Store(cfg.store_path) as s:
            jid = jobs.enqueue(s, "sleep", {})
        wait_for(cfg.store_path, jid, {"running"})
        time.sleep(0.1)
        with Store(cfg.store_path) as s:
            assert s.request_cancel(jid, "t") == "cancelling"
        assert wait_for(cfg.store_path, jid, {"cancelled"}) == "cancelled"
        with Store(cfg.store_path) as s:
            assert json.loads(s.job(jid)["progress"])["done"] < 200
            assert "cancelled" in s.job(jid)["log"]
    finally:
        worker.stop()


def test_failed_job_and_validation(provider, sleep_job):
    sleep_job["fail"] = True
    cfg = provider.get()
    worker = jobs.Worker(provider, scheduler=False, tick_seconds=0.05)
    worker.start()
    try:
        with Store(cfg.store_path) as s:
            jid = jobs.enqueue(s, "sleep", {})
        assert wait_for(cfg.store_path, jid, {"failed"}) == "failed"
        with Store(cfg.store_path) as s:
            assert s.job(jid)["error"] == "RuntimeError: boom"
            with pytest.raises(jobs.JobError, match="unknown job type"):
                jobs.enqueue(s, "nope", {})
            with pytest.raises(jobs.JobError, match="destructive"):
                jobs.enqueue(s, "delete_analysis", {"date": "2026-11-05"})
            with pytest.raises(jobs.JobError, match="YYYY-MM-DD"):
                jobs.enqueue(s, "run", {"date": "yesterday"})
            with pytest.raises(jobs.JobError, match="before"):
                jobs.enqueue(s, "news", {"from": "2026-11-05", "to": "2026-11-01"})
    finally:
        worker.stop()


def test_validate_normalises_params():
    assert jobs.validate("run", {"date": "2026-11-05"}, False) == (
        {"date": "2026-11-05"},
        "2026-11-05",
    )
    assert jobs.validate("analyze", {"all": True, "date": "2026-11-05"}, False)[0] == {
        "date": "2026-11-05",
        "all": True,
    }
    assert jobs.validate("news", {"from": "2026-10-01", "to": "2026-10-31"}, False)[0] == {
        "from": "2026-10-01",
        "to": "2026-10-31",
    }
    assert jobs.validate("delete_scores", {"model": "m"}, True)[0] == {"model": "m"}
    assert jobs.validate("backup", {}, False)[0] == {"keep": 7}


def test_scheduler_tick_queues_the_daily_run_and_retries(provider, monkeypatch):
    cfg = provider.get()
    calls = []

    def fake_run(store, config, params):
        calls.append(params)
        raise RuntimeError("stages failed: prices")

    monkeypatch.setitem(jobs.HANDLERS, "run", fake_run)
    clock = {"now": datetime(2026, 11, 5, 17, 59, tzinfo=ET)}
    worker = jobs.Worker(provider, scheduler=True, tick_seconds=0.05, now=lambda: clock["now"])
    worker.start()
    try:
        time.sleep(0.2)
        with Store(cfg.store_path) as s:
            assert s.run_jobs_for_day("2026-11-05") == []  # before the slot
            assert cfg.heartbeat_path.exists()
        clock["now"] = datetime(2026, 11, 5, 18, 0, 30, tzinfo=ET)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with Store(cfg.store_path) as s:
                rows = s.run_jobs_for_day("2026-11-05")
            if any(r["status"] == "failed" for r in rows) and len(rows) >= 2:
                break
            time.sleep(0.05)
        with Store(cfg.store_path) as s:
            rows = s.run_jobs_for_day("2026-11-05")
            assert [(r["status"], r["attempt"]) for r in rows] == [("failed", 1), ("queued", 2)]
            retry = s.job(rows[1]["id"])
            assert retry["not_before"] > retry["created_at"]  # delayed by retry_delay_minutes
            assert json.loads(retry["params"])["attempt"] == 2
        assert calls[0]["trigger"] == "schedule" and calls[0]["attempt"] == 1
        time.sleep(0.2)
        with Store(cfg.store_path) as s:
            assert (
                len(s.run_jobs_for_day("2026-11-05")) == 2
            )  # tick does not add a third while one is queued
    finally:
        worker.stop()


def test_boot_marks_stale_jobs(provider):
    cfg = provider.get()
    old = "2020-01-01T00:00:00Z"  # created and started before this process booted
    with Store(cfg.store_path) as s:
        a = s.create_job("sleep", {}, "api", old)
        s.start_job(a, old)
        b = s.create_job("sleep", {"x": 1}, "api", old)
    worker = jobs.Worker(provider, scheduler=False, tick_seconds=0.05)
    worker.start()
    try:
        worker.ready.wait(5)
        with Store(cfg.store_path) as s:
            assert s.job_status(a) == "interrupted" and s.job_status(b) == "cancelled"
    finally:
        worker.stop()


def test_report_and_check_are_noops_outside_a_job():
    jobs.report("x", 1, 2)
    jobs.check()
    assert jobs.current() is None


def test_worker_stop_cancels_running_job(provider, sleep_job):
    sleep_job["steps"], sleep_job["delay"] = 500, 0.02
    cfg = provider.get()
    worker = jobs.Worker(provider, scheduler=False, tick_seconds=0.05)
    worker.start()
    with Store(cfg.store_path) as s:
        jid = jobs.enqueue(s, "sleep", {})
    wait_for(cfg.store_path, jid, {"running"})
    t = time.monotonic()
    worker.stop(timeout=5)
    assert time.monotonic() - t < 3
    with Store(cfg.store_path) as s:
        assert s.job_status(jid) == "cancelled"
    assert not any(th.name == "murmur-worker" and th.is_alive() for th in threading.enumerate())
