"""Schema v6: the jobs table, API read queries and deletions."""

import json

import pytest

from murmur.models import Article, PriceBar, SentimentScores, UniverseMember
from murmur.store import SCHEMA_VERSION, Store


def test_job_lifecycle(store):
    jid = store.create_job("news", {"date": "2026-11-05"}, "api", "t0", day="2026-11-05")
    assert store.queued_duplicate("news", {"date": "2026-11-05"}) == jid
    assert store.queued_duplicate("news", {"date": "2026-11-06"}) is None
    row = store.next_job("t1")
    assert row["id"] == jid and row["status"] == "queued" and row["attempt"] == 1
    store.start_job(jid, "t1")
    assert store.job_status(jid) == "running"
    store.update_job(jid, progress=json.dumps({"step": "news", "done": 1, "total": 4}))
    assert json.loads(store.job(jid)["progress"])["done"] == 1
    store.finish_job(jid, "done", "t2", result=json.dumps({"ok": True}), log="line\n")
    j = store.job(jid)
    assert (j["status"], j["finished_at"], j["log"]) == ("done", "t2", "line\n")
    rows, total = store.jobs()
    assert total == 1 and rows[0]["id"] == jid and "log" not in rows[0].keys()
    assert store.jobs(status="queued")[1] == 0


def test_next_job_honours_not_before_and_priority(store):
    later = store.create_job("news", {"a": 1}, "api", "t0", not_before="2026-11-06T00:10:00Z")
    low = store.create_job("export", {"all": True}, "api", "t0", priority=5)
    high = store.create_job(
        "run", {"date": "2026-11-05"}, "schedule", "t0", priority=-10, day="2026-11-05"
    )
    assert store.next_job("2026-11-06T00:00:00Z")["id"] == high
    store.start_job(high, "t")
    store.finish_job(high, "done", "t")
    assert store.next_job("2026-11-06T00:00:00Z")["id"] == low
    store.start_job(low, "t")
    store.finish_job(low, "failed", "t", error="x")
    assert store.next_job("2026-11-06T00:00:00Z") is None  # `later` not due yet
    assert store.next_job("2026-11-06T00:10:00Z")["id"] == later
    assert store.next_queued()["id"] == later


def test_cancel_and_interrupt(store):
    queued = store.create_job("news", {"a": 1}, "api", "t0")
    running = store.create_job("news", {"a": 2}, "api", "t0")
    store.start_job(running, "t1")
    assert store.request_cancel(queued, "t2") == "cancelled"
    assert store.request_cancel(running, "t2") == "cancelling"
    assert store.request_cancel(queued, "t3") is None  # already final
    assert store.request_cancel(999, "t3") is None
    store.finish_job(running, "cancelled", "t3")
    a = store.create_job("news", {"a": 3}, "api", "t0")
    b = store.create_job("news", {"a": 4}, "api", "t0")
    store.start_job(a, "t4")
    assert store.mark_interrupted("t5") == 1
    assert store.job_status(a) == "interrupted"
    assert store.job_status(b) == "cancelled" and store.job(b)["error"] == "dropped on restart"
    assert store.run_jobs_for_day("2026-11-05") == []


def test_migrates_a_version_5_store(tmp_path):
    import sqlite3

    from murmur.store import MIGRATIONS

    path = tmp_path / "v5.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript("".join(MIGRATIONS[v] for v in range(1, 6)) + "PRAGMA user_version = 5;")
    conn.execute(
        """INSERT INTO universe_months (month, rank, ticker, cik, name, market_cap, computed_at)
           VALUES ('2026-11', 1, 'AAPL', 1, 'Apple', 3.0, 't'),
                  ('2026-11', 2, 'MSFT', 2, 'Microsoft', 1.0, 't')"""
    )
    conn.commit()
    conn.close()
    with Store(path) as s:
        assert s.schema_version == SCHEMA_VERSION
        assert s.count("jobs") == 0
        # 0.7.0 computes the index weights for months that already exist
        assert [m.weight for m in s.universe_month("2026-11")] == [0.75, 0.25]
    # the migrated file is refused by an older schema constant (the rollback story)
    import murmur.store as store_mod

    old = store_mod.SCHEMA_VERSION
    store_mod.SCHEMA_VERSION = 5
    try:
        with pytest.raises(RuntimeError, match="newer"):
            Store(path)
    finally:
        store_mod.SCHEMA_VERSION = old


def test_cross_thread_connection_option(tmp_path):
    import threading

    with Store(tmp_path / "x.sqlite", check_same_thread=False) as s:
        out = []
        t = threading.Thread(target=lambda: out.append(s.count("jobs")))
        t.start()
        t.join()
        assert out == [0]


def seed_day(store, model="m"):
    store.save_universe_month(
        "2026-11",
        [
            UniverseMember("AAPL", "Apple", 5e12, 1, 320193, weight=5 / 9),
            UniverseMember("MSFT", "Microsoft", 4e12, 2, 789019, weight=4 / 9),
        ],
        "t",
    )
    for t, (o, c, pc) in {"AAPL": (350, 344, 349), "MSFT": (540, 555, 520)}.items():
        store.upsert_prices(
            [
                PriceBar(t, "2026-11-04", pc, None, None, pc, None, 1),
                PriceBar(t, "2026-11-05", o, o + 1, o - 1, c, None, 100),
            ],
            "t",
        )
        store.upsert_returns(
            [(t, "2026-11-05", "2026-11-04", pc, o, c, o / pc - 1, c / o - 1, c / pc - 1)]
        )
    a1, _ = store.upsert_article(
        Article("https://y/1", "Apple beats", "2026-11-05T15:00:00Z", "yahoo"), "AAPL", "t"
    )
    a2, _ = store.upsert_article(
        Article("https://f/2", "Microsoft misses", "2026-11-05T12:00:00Z", "finnhub"), "MSFT", "t"
    )
    store.set_article_windows([("2026-11-05", "intraday", a1), ("2026-11-05", "overnight", a2)])
    store.insert_scores(
        model,
        [
            (a1, SentimentScores(0.8, 0.1, 0.1, "positive")),
            (a2, SentimentScores(0.1, 0.8, 0.1, "negative")),
        ],
        "t",
    )
    store.upsert_window_sentiment("2026-11-05", "day", model, {"AAPL": (1, 0.7), "MSFT": (1, -0.7)})
    store.upsert_window_sentiment("2026-11-05", "intraday", model, {"AAPL": (1, 0.7)})
    store.upsert_window_sentiment("2026-11-05", "overnight", model, {"MSFT": (1, -0.7)})
    for method in ("pearson", "spearman"):
        store.upsert_correlation(
            {
                "date": "2026-11-05",
                "window": "day",
                "return_type": "daily",
                "lag": 0,
                "scope": "pooled",
                "source": "all",
                "method": method,
                "model": model,
                "n": 2,
                "estimate": 1.0,
                "p_value": 0.0,
                "ci_low": None,
                "ci_high": None,
                "tickers": "AAPL,MSFT",
                "computed_at": "t",
            }
        )
    store.replace_rolling(
        "2026-11-05",
        [
            (1, "day", "daily", 0, "all", "pooled", "", "pearson", 40, 0.3, 0.01, 0.1, 0.5, "t"),
            (1, "day", "daily", 1, "all", "pooled", "", "pearson", 38, 0.05, 0.6, -0.2, 0.3, "t"),
        ],
    )
    rid = store.start_run("2026-11-05", "manual", 1, "t")
    store.finish_run(rid, "t", True, "[]")


def test_api_read_queries(store):
    seed_day(store)
    assert store.latest_analysis_day() == "2026-11-05"
    assert store.latest_rolling_as_of() == "2026-11-05"
    assert store.last_run()["date"] == "2026-11-05" and store.run(1)["ok"] == 1
    days = store.days_summary("2026-11-01", "2026-11-30")
    assert [(d["date"], d["n_prices"], d["n_articles"], d["n_correlations"]) for d in days] == [
        ("2026-11-04", 2, 0, 0),
        ("2026-11-05", 2, 2, 2),
    ]
    corr = store.correlations_between("2026-11-01", "2026-11-30", {"method": "pearson", "lag": 0})
    assert len(corr) == 1 and corr[0]["estimate"] == 1.0
    assert len(store.rolling_rows("2026-11-05", {"lag": 1})) == 1
    hist = store.rolling_history(
        {
            "months": 1,
            "window": "day",
            "return_type": "daily",
            "lag": 0,
            "source": "all",
            "scope": "pooled",
            "ticker": "",
            "method": "pearson",
        }
    )
    assert [(h["as_of"], h["n"]) for h in hist] == [("2026-11-05", 40)]
    series = store.ticker_series("AAPL", "2026-11-01", "2026-11-30")
    assert [s["date"] for s in series] == ["2026-11-04", "2026-11-05"]
    assert series[1]["daily"] == pytest.approx(344 / 349 - 1) and series[1][
        "day_sentiment"
    ] == pytest.approx(0.7)
    assert series[1]["intraday_n"] == 1 and series[1]["overnight_n"] is None
    info = store.ticker_info("AAPL")
    assert info["name"] == "Apple" and info["cik"] == 320193 and info["n_prices"] == 2
    assert info["months"][0]["rank"] == 1 and store.ticker_info("ZZZZ") is None
    cov = store.tickers_coverage("2026-11")
    assert [(c["ticker"], c["n_articles"], c["n_prices"]) for c in cov] == [
        ("AAPL", 1, 2),
        ("MSFT", 1, 2),
    ]
    rows, total = store.articles_query(trading_day="2026-11-05", model="m")
    assert total == 2 and rows[0]["title"] == "Apple beats" and rows[0]["label"] == "positive"
    rows, total = store.articles_query(ticker="MSFT", source="finnhub", model="m")
    assert total == 1 and rows[0]["sources"] == "finnhub"
    rows, total = store.articles_query(
        start="2026-11-01", end="2026-11-30", window="overnight", model="m", limit=1
    )
    assert total == 1 and len(rows) == 1
    stats = {s["source"]: s for s in store.source_stats()}
    assert stats["yahoo"]["n_articles"] == 1 and stats["yahoo"]["n_shared"] == 0
    assert [
        (r["date"], r["n_articles"])
        for r in store.source_series("finnhub", "2026-11-01", "2026-11-30")
    ] == [("2026-11-05", 1)]
    assert store.months_with_data() == ["2026-11"]
    counts = store.table_counts()
    assert counts["articles"] == 2 and counts["jobs"] == 0


def test_deletions(store):
    seed_day(store)
    out = store.delete_analysis("2026-11-05")
    assert out == {
        "returns": 2,
        "window_sentiment": 4,
        "correlations": 2,
        "rolling_correlations": 2,
    }
    assert store.correlations_on("2026-11-05") == []
    seed_day(store)
    out = store.delete_scores("m")
    assert out["sentiment"] == 2 and out["window_sentiment"] == 4 and out["correlations"] == 2
    assert len(store.unscored_articles("m")) == 2
    assert store.delete_universe_month("2026-11") == 2
    assert store.universe_month("2026-11") == []


def test_prune_rolling_tickers_keeps_recent_and_month_ends(store):
    def rows_for(as_of):
        return [
            (3, "day", "daily", 0, "all", "pooled", "", "pearson", 50, 0.1, 0.01, 0.0, 0.2, "t"),
            (3, "day", "daily", 0, "all", "ticker", "AAPL", "pearson", 12, 0.2, 0.1, 0.0, 0.4, "t"),
            (
                3,
                "day",
                "daily",
                0,
                "all",
                "ticker",
                "MSFT",
                "pearson",
                12,
                0.1,
                0.3,
                -0.1,
                0.3,
                "t",
            ),
        ]

    days = [
        "2026-09-28",
        "2026-09-29",
        "2026-09-30",  # September's last session
        "2026-10-01",
        "2026-10-02",
        "2026-10-05",
        "2026-10-06",
    ]
    for d in days:
        store.replace_rolling(d, rows_for(d))
    assert store.count("rolling_correlations") == 21
    deleted = store.prune_rolling_tickers(keep_sessions=3)  # keep 10-02, 10-05, 10-06
    # per-ticker rows dropped for 09-28, 09-29, 10-01 (3 sessions x 2 tickers); 09-30 kept
    assert deleted == 6
    kept = {
        r["as_of"]
        for r in store.conn.execute(
            "SELECT DISTINCT as_of FROM rolling_correlations WHERE scope = 'ticker'"
        )
    }
    assert kept == {"2026-09-30", "2026-10-02", "2026-10-05", "2026-10-06"}
    pooled = {
        r["as_of"]
        for r in store.conn.execute(
            "SELECT DISTINCT as_of FROM rolling_correlations WHERE scope = 'pooled'"
        )
    }
    assert pooled == set(days)  # pooled history is never pruned
    snap = store.rolling_month_snapshot("2026-09")
    assert {r["ticker"] for r in snap} == {"AAPL", "MSFT"}
    assert {r["as_of"] for r in snap} == {"2026-09-30"}
    assert {r["as_of"] for r in store.rolling_month_snapshot("2026-10")} == {"2026-10-06"}
    assert store.rolling_month_snapshot("2026-08") == []
    assert store.prune_rolling_tickers(keep_sessions=3) == 0  # idempotent
