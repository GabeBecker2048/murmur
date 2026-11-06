"""The HTTP API through FastAPI's TestClient: every read route, the auth matrix,
jobs, settings, charts and /data."""

import time
from datetime import date

import pytest
from fastapi.testclient import TestClient

from murmur import jobs
from murmur.api.app import create_app
from murmur.config import ConfigProvider
from murmur.store import Store
from tests.test_store_jobs import seed_day

TOKEN = "test-token-1234"


@pytest.fixture
def provider(tmp_path):
    ops = tmp_path / "murmur.toml"
    ops.write_text(
        f'data_dir = "{tmp_path / "data"}"\n[sentiment]\nmodel = "m"\n'
        '[universe]\nsource = "static"\n[universe.static]\nAAPL = "Apple"\nMSFT = "Microsoft"\n'
    )
    provider = ConfigProvider(ops, env={"MURMUR_API_TOKEN": TOKEN})
    with Store(provider.get().store_path) as s:
        seed_day(s)
    return provider


@pytest.fixture
def client(provider):
    with TestClient(create_app(provider, run_worker=False)) as c:
        yield c


@pytest.fixture
def worker_client(provider, monkeypatch):
    behaviour = {"steps": 3, "delay": 0.02}

    def handler(store, config, params):
        for i in range(behaviour["steps"]):
            jobs.report("sleep", i, behaviour["steps"])
            time.sleep(behaviour["delay"])
        return {"ok": True}

    monkeypatch.setitem(jobs.HANDLERS, "sleep", handler)
    monkeypatch.setattr(jobs, "JOB_TYPES", (*jobs.JOB_TYPES, "sleep"))
    import murmur.api.schemas as schemas

    monkeypatch.setattr(schemas.JobCreate.model_fields["type"], "annotation", str)
    schemas.JobCreate.model_rebuild(force=True)
    with TestClient(create_app(provider, run_worker=True, run_scheduler=False)) as c:
        c.behaviour = behaviour
        yield c


AUTH = {"Authorization": f"Bearer {TOKEN}"}


def test_openapi_lists_the_surface(client):
    paths = client.get("/openapi.json").json()["paths"]
    expected = {
        "/api/v1/health",
        "/api/v1/status",
        "/api/v1/latest",
        "/api/v1/logs",
        "/api/v1/runs",
        "/api/v1/runs/{run_id}",
        "/api/v1/jobs",
        "/api/v1/jobs/types",
        "/api/v1/jobs/{job_id}",
        "/api/v1/jobs/{job_id}/log",
        "/api/v1/scheduler/run-now",
        "/api/v1/settings",
        "/api/v1/settings/schema",
        "/api/v1/universe",
        "/api/v1/universe/months",
        "/api/v1/datasets",
        "/api/v1/datasets/{name}",
        "/api/v1/index/{name}",
        "/api/v1/index/{name}/months",
        "/api/v1/index/{name}/changes",
        "/api/v1/index/{name}/history/{key}",
        "/api/v1/companies",
        "/api/v1/companies/{key}",
        "/api/v1/scheduler/pause",
        "/api/v1/scheduler/resume",
        "/api/v1/ui",
        "/api/v1/days",
        "/api/v1/days/{date}",
        "/api/v1/days/{date}/articles",
        "/api/v1/tickers",
        "/api/v1/tickers/{ticker}",
        "/api/v1/tickers/{ticker}/series",
        "/api/v1/tickers/{ticker}/articles",
        "/api/v1/tickers/{ticker}/leadlag",
        "/api/v1/correlations",
        "/api/v1/leadlag",
        "/api/v1/leadlag/history",
        "/api/v1/sources",
        "/api/v1/sources/{source}/series",
        "/api/v1/charts/leadlag.png",
    }
    assert expected <= set(paths)
    assert client.get("/docs").status_code == 200


def test_health_and_status(client):
    assert client.get("/api/v1/health").json()["ok"] is True
    st = client.get("/api/v1/status").json()
    assert st["version"] and st["schema"] >= 6
    assert st["counts"]["articles"] == 2 and st["latest_analysis_day"] == "2026-11-05"
    assert st["config"]["writes_enabled"] is True and st["worker"]["alive"] is False
    assert st["disk"]["db_bytes"] > 0
    latest = client.get("/api/v1/latest").json()
    assert latest["latest_analysis_day"] == "2026-11-05"
    assert [m["method"] for m in latest["matched_pairs"]] == ["pearson"]
    assert latest["day"]["n_articles"] == 2
    assert client.get("/api/v1/logs?lines=5").status_code == 200


def test_runs(client):
    runs = client.get("/api/v1/runs").json()
    assert runs["total"] == 1 and runs["items"][0]["stages"] == []
    assert client.get("/api/v1/runs/1").json()["ok"] == 1
    assert client.get("/api/v1/runs/99").status_code == 404


def test_universe_and_tickers(client):
    assert client.get("/api/v1/universe/months").json() == {"months": ["2026-11"]}
    u = client.get("/api/v1/universe").json()
    assert u["month"] == "2026-11" and [m["ticker"] for m in u["members"]] == ["AAPL", "MSFT"]
    assert client.get("/api/v1/universe?date=2026-11-05").json()["month"] == "2026-11"
    assert client.get("/api/v1/universe?month=2020-01").status_code == 404
    assert client.get("/api/v1/universe?date=nope").status_code == 422
    t = client.get("/api/v1/tickers").json()
    assert t["month"] == "2026-11" and t["items"][0]["n_articles"] == 1
    info = client.get("/api/v1/tickers/aapl").json()
    assert info["name"] == "Apple" and info["cik"] == 320193
    assert client.get("/api/v1/tickers/ZZZZ").status_code == 404
    series = client.get("/api/v1/tickers/AAPL/series?from=2026-11-01&to=2026-11-30").json()
    assert series["total"] == 2 and series["items"][1]["day_sentiment"] == pytest.approx(0.7)
    arts = client.get("/api/v1/tickers/AAPL/articles").json()
    assert arts["total"] == 1 and arts["items"][0]["label"] == "positive"
    ll = client.get("/api/v1/tickers/AAPL/leadlag").json()
    assert ll["as_of"] == "2026-11-05" and ll["items"] == []  # no per-ticker rows seeded


def test_days(client):
    days = client.get("/api/v1/days?from=2026-11-01&to=2026-11-30").json()
    assert [(d["date"], d["trading_day"], d["has_analysis"]) for d in days["items"]] == [
        ("2026-11-04", True, False),
        ("2026-11-05", True, True),
    ]
    day = client.get("/api/v1/days/2026-11-05").json()
    assert day["returns"]["AAPL"]["daily"] == pytest.approx(344 / 349 - 1)
    assert len(day["window_sentiment"]) == 4 and len(day["correlations"]) == 2
    assert client.get("/api/v1/days/2026-11-07").status_code == 404
    assert client.get("/api/v1/days/bad").status_code == 422
    arts = client.get("/api/v1/days/2026-11-05/articles?source=finnhub").json()
    assert arts["total"] == 1 and arts["items"][0]["tickers"] == "MSFT"


def test_correlations_leadlag_sources(client):
    c = client.get("/api/v1/correlations?from=2026-11-01&method=pearson").json()
    assert c["total"] == 1 and c["items"][0]["estimate"] == 1.0
    ll = client.get("/api/v1/leadlag?months=1").json()
    assert ll["as_of"] == "2026-11-05" and ll["total"] == 2
    assert client.get("/api/v1/leadlag?months=0").json()["total"] == 2
    assert client.get("/api/v1/leadlag?as_of=2026-01-01").json()["total"] == 0
    h = client.get("/api/v1/leadlag/history?months=1&window=day&return_type=daily&lag=1").json()
    assert [x["n"] for x in h["items"]] == [38]
    s = client.get("/api/v1/sources").json()
    assert {x["source"] for x in s["items"]} == {"yahoo", "finnhub"}
    assert s["configured"] == ["yahoo"] and s["as_of"] == "2026-11-05"
    ss = client.get("/api/v1/sources/finnhub/series?from=2026-11-01").json()
    assert ss["items"][0]["n_articles"] == 1


def test_leadlag_chart(client):
    # the seeded rolling table has only two lags, so no complete horizon yet
    assert client.get("/api/v1/charts/leadlag.png").status_code == 404
    with Store(client.app.state.provider.get().store_path) as s:
        rows = []
        for w, rt in (("overnight", "overnight"), ("intraday", "intraday"), ("day", "daily")):
            for lag in (-1, 0, 1):
                rows.append(
                    (
                        3,
                        w,
                        rt,
                        lag,
                        "all",
                        "pooled",
                        "",
                        "pearson",
                        50,
                        0.1 * lag,
                        0.5,
                        -0.1,
                        0.3,
                        "t",
                    )
                )
        s.replace_rolling("2026-11-05", rows)
    r = client.get("/api/v1/charts/leadlag.png")
    assert (
        r.status_code == 200 and r.headers["content-type"] == "image/png" and len(r.content) > 5000
    )


def test_data_files(client, provider):
    base = provider.get().published_dir
    (base / "2026" / "11" / "05").mkdir(parents=True)
    (base / "2026" / "11" / "05" / "prices.parquet").write_bytes(b"PAR1")
    (base / "index.json").write_text('{"files": []}')
    r = client.get("/data/index.json")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    r = client.get("/data/2026/11/05/prices.parquet")
    assert r.status_code == 200 and "max-age" in r.headers["cache-control"]
    assert client.get("/data/nope.parquet").status_code == 404


def test_settings_read_and_write(client):
    s = client.get("/api/v1/settings").json()
    assert (
        s["effective"]["sentiment"]["model"] == "m" and s["provenance"]["sentiment.model"] == "file"
    )
    assert s["effective"]["api"]["token"] == "****1234"
    schema = {k["path"]: k for k in client.get("/api/v1/settings/schema").json()["keys"]}
    assert schema["news.sources"]["choices"] == ["yahoo", "finnhub", "gdelt"]
    r = client.put("/api/v1/settings", json={"news": {"max_articles": 9}})
    assert r.status_code == 401
    r = client.put("/api/v1/settings", json={"news": {"max_articles": 9}}, headers=AUTH)
    assert r.status_code == 200 and r.json()["effective"]["news"]["max_articles"] == 9
    assert r.json()["provenance"]["news.max_articles"] == "api"
    assert client.get("/api/v1/settings").json()["effective"]["news"]["max_articles"] == 9
    r = client.put("/api/v1/settings", json={"finnhub": {"api_key": "x"}}, headers=AUTH)
    assert r.status_code == 400 and "cannot be changed" in r.json()["detail"]
    r = client.put("/api/v1/settings", json={"schedule": {"time": "6pm"}}, headers=AUTH)
    assert r.status_code == 400
    r = client.put("/api/v1/settings", json={}, headers=AUTH)
    assert r.status_code == 400


def test_auth_matrix(client, provider, monkeypatch):
    body = {"type": "export", "params": {"all": True}}
    assert client.post("/api/v1/jobs", json=body).status_code == 401
    assert (
        client.post(
            "/api/v1/jobs", json=body, headers={"Authorization": "Bearer wrong"}
        ).status_code
        == 401
    )
    assert client.delete("/api/v1/jobs/1").status_code == 401
    r = client.post("/api/v1/jobs", json=body, headers=AUTH)
    assert r.status_code == 202 and r.json()["status"] == "queued"
    assert client.post("/api/v1/jobs", json=body, headers=AUTH).status_code == 409
    assert client.get("/api/v1/jobs").json()["total"] == 1
    # unset token -> writes fail closed with 503, reads unaffected
    monkeypatch.setattr(provider, "env", {})
    provider.invalidate()
    assert client.post("/api/v1/jobs", json=body, headers=AUTH).status_code == 503
    assert client.get("/api/v1/status").json()["config"]["writes_enabled"] is False


def test_job_requests_validate(client):
    assert client.post("/api/v1/jobs", json={"type": "nope"}, headers=AUTH).status_code == 422
    r = client.post(
        "/api/v1/jobs",
        json={"type": "delete_analysis", "params": {"date": "2026-11-05"}},
        headers=AUTH,
    )
    assert r.status_code == 400 and "confirm" in r.json()["detail"]
    r = client.post(
        "/api/v1/jobs", json={"type": "run", "params": {"date": "yesterday"}}, headers=AUTH
    )
    assert r.status_code == 400
    types = client.get("/api/v1/jobs/types").json()
    assert "delete_scores" in types["destructive"] and types["params"]["news"] == [
        "date",
        "from",
        "to",
    ]
    assert client.get("/api/v1/jobs/999").status_code == 404
    assert client.delete("/api/v1/jobs/999", headers=AUTH).status_code == 404


def test_job_lifecycle_over_http(worker_client):
    c = worker_client
    r = c.post("/api/v1/jobs", json={"type": "sleep", "params": {}}, headers=AUTH)
    assert r.status_code == 202
    jid = r.json()["id"]
    for _ in range(100):
        j = c.get(f"/api/v1/jobs/{jid}").json()
        if j["status"] == "done":
            break
        time.sleep(0.02)
    assert j["status"] == "done" and j["result"] == {"ok": True} and j["progress"]["total"] == 3
    log = c.get(f"/api/v1/jobs/{jid}/log").json()
    assert "started" in log["text"] and log["next"] == len(log["text"])
    tail = c.get(f"/api/v1/jobs/{jid}/log?after={log['next']}").json()
    assert tail["text"] == ""
    assert c.delete(f"/api/v1/jobs/{jid}", headers=AUTH).status_code == 409
    # cancel a long one mid-way
    c.behaviour["steps"], c.behaviour["delay"] = 500, 0.02
    jid = c.post(
        "/api/v1/jobs", json={"type": "sleep", "params": {"long": 1}}, headers=AUTH
    ).json()["id"]
    for _ in range(100):
        if c.get(f"/api/v1/jobs/{jid}").json()["status"] == "running":
            break
        time.sleep(0.02)
    assert c.delete(f"/api/v1/jobs/{jid}", headers=AUTH).json()["status"] == "cancelling"
    for _ in range(200):
        j = c.get(f"/api/v1/jobs/{jid}").json()
        if j["status"] == "cancelled":
            break
        time.sleep(0.02)
    assert j["status"] == "cancelled"
    assert c.get("/api/v1/health").json()["ok"] is True


def test_run_now_enqueues_todays_run(client):
    r = client.post("/api/v1/scheduler/run-now", headers=AUTH)
    assert r.status_code == 202 and r.json()["date"]
    assert client.post("/api/v1/scheduler/run-now", headers=AUTH).status_code == 409


def test_index_and_companies(client):
    idx = client.get("/api/v1/index/m100").json()
    assert idx["title"] == "Murmur 100" and idx["month"] == "2026-11"
    assert idx["effective_date"] == "2026-11-02" and idx["size"] == 2
    assert [(m["rank"], m["ticker"]) for m in idx["items"]] == [(1, "AAPL"), (2, "MSFT")]
    assert client.get("/api/v1/index/m100?date=2026-11-05").json()["month"] == "2026-11"
    assert client.get("/api/v1/index/m100/months").json()["months"] == ["2026-11"]
    changes = client.get("/api/v1/index/m100/changes").json()
    assert changes["added"] == [] and changes["removed"] == []
    hist = client.get("/api/v1/index/m100/history/aapl").json()
    assert [h["month"] for h in hist["items"]] == ["2026-11"]
    assert client.get("/api/v1/index/m100/history/320193").json()["items"][0]["ticker"] == "AAPL"
    assert client.get("/api/v1/index/m500").status_code == 404
    # the 0.6.0 names still answer
    assert client.get("/api/v1/universe").json()["month"] == "2026-11"

    companies = client.get("/api/v1/companies").json()
    assert companies["total"] == 2 and companies["items"][0]["cik"] == 320193
    assert client.get("/api/v1/companies?q=micro").json()["total"] == 1
    one = client.get("/api/v1/companies/789019").json()
    assert one["ticker"] == "MSFT" and one["history"][0]["rank"] == 2
    assert client.get("/api/v1/companies/NOPE").status_code == 404


def test_datasets_and_csv(client, provider):
    from murmur.export import export_day

    with Store(provider.get().store_path) as s:
        export_day(s, provider.get(), date(2026, 11, 5), today=date(2026, 11, 5))
    listing = client.get("/api/v1/datasets").json()
    names = [d["name"] for d in listing["items"]]
    assert names == ["companies", "m100", "prices", "news", "sentiment", "correlations"]
    prices = next(d for d in listing["items"] if d["name"] == "prices")
    assert prices["file_count"] == 1 and prices["last_date"] == "2026-11-05"
    assert "files" not in prices
    assert prices["license"]["name"].startswith("Source terms apply")
    assert prices["license"]["contact"] == "gabebecker2048@gmail.com"  # sec.contact fallback
    companies = next(d for d in listing["items"] if d["name"] == "companies")
    assert companies["license"]["name"] == "CC BY 4.0" and companies["license"]["contact"]
    ds = client.get("/api/v1/datasets/prices").json()
    assert (
        ds["total"] == 1 and ds["files"][0]["path"] == "prices/2026/11/05/20261105.prices.parquet"
    )
    assert client.get("/api/v1/datasets/prices?table=nope").json()["total"] == 0
    assert client.get("/api/v1/datasets/nope").status_code == 404

    url = "/data/sentiment/2026/11/05/20261105.sentiment.parquet"
    parquet = client.get(url)
    assert parquet.status_code == 200 and parquet.headers["cache-control"] == "public, max-age=300"
    csv = client.get(url + "?format=csv")
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    assert csv.headers["content-disposition"] == 'attachment; filename="20261105.sentiment.csv"'
    lines = csv.text.strip().splitlines()
    assert lines[0].startswith('"date","cik","ticker"') and len(lines) == 5
    assert client.get("/data/sentiment/2026/11/05/missing.parquet?format=csv").status_code == 404
    manifest = client.get("/data/index.json").json()
    assert manifest["datasets"]["m100"]["cadence"] == "monthly"

    # prices is public by default (0.7.2) under the source-terms notice
    assert manifest["datasets"]["prices"]["public"] is True
    prices_file = "/data/prices/2026/11/05/20261105.prices.parquet"
    r = client.get(prices_file)
    assert r.status_code == 200
    assert r.headers["x-murmur-license"].startswith("Source terms apply")
    assert (
        r.headers["link"].startswith("<https://legal.yahoo.com")
        and 'rel="license"' in r.headers["link"]
    )
    assert "Yahoo Finance" in r.headers["x-murmur-attribution"]
    # the CSV path carries the same headers (CSV has no footer)
    r = client.get(url + "?format=csv")
    assert (
        r.headers["x-murmur-license"] == "CC BY-NC-SA 4.0"
        and "creativecommons" in r.headers["link"]
    )
    assert parquet.headers["x-murmur-license"] == "CC BY-NC-SA 4.0"


def test_private_datasets_need_the_token(tmp_path):
    from datetime import date as _date

    from murmur.export import export_day

    ops = tmp_path / "murmur.toml"
    ops.write_text(
        f'data_dir = "{tmp_path / "data"}"\n[sentiment]\nmodel = "m"\n'
        '[universe]\nsource = "static"\n[universe.static]\nAAPL = "Apple"\nMSFT = "Microsoft"\n'
        '[datasets]\nprivate = ["prices"]\n'
    )
    provider = ConfigProvider(ops, env={"MURMUR_API_TOKEN": TOKEN})
    with Store(provider.get().store_path) as s:
        seed_day(s)
        export_day(s, provider.get(), _date(2026, 11, 5), today=_date(2026, 11, 5))
    with TestClient(create_app(provider, run_worker=False)) as client:
        manifest = client.get("/data/index.json").json()
        assert manifest["datasets"]["prices"]["public"] is False
        assert manifest["datasets"]["sentiment"]["public"] is True
        private = "/data/prices/2026/11/05/20261105.prices.parquet"
        refused = client.get(private)
        assert refused.status_code == 403 and "not published" in refused.json()["detail"]
        assert client.get(private + "?format=csv").status_code == 403
        assert client.get(private, headers=AUTH).status_code == 200
        assert client.get(private, headers={"Authorization": "Bearer wrong"}).status_code == 403
        assert client.get("/api/v1/ui").json()["private_datasets"] == ["prices"]


def test_scheduler_pause_resume_and_ui(client, provider):
    assert client.post("/api/v1/scheduler/pause").status_code == 401
    r = client.post("/api/v1/scheduler/pause", headers=AUTH)
    assert r.status_code == 200 and r.json()["paused"] is True
    assert provider.get().schedule.paused is True
    assert client.get("/api/v1/status").json()["config"]["schedule"]["paused"] is True
    r = client.post("/api/v1/scheduler/resume", headers=AUTH)
    assert r.json() == {"paused": False, "slots": [["18:00", "full"]]}
    client.put(
        "/api/v1/settings", json={"schedule": {"analysis_time": "06:30"}}, headers=AUTH
    ).raise_for_status()
    st = client.get("/api/v1/status").json()["config"]["schedule"]
    assert st["slots"] == [["18:00", "collect"], ["06:30", "analyze"]]

    ui = client.get("/api/v1/ui").json()
    assert ui["title"] == "Murmur" and ui["default_months"] == 3 and ui["theme"] == "auto"
    assert ui["index"] == {"name": "m100", "title": "Murmur 100", "size": 100}
    assert ui["private_datasets"] == [] and ui["writes_enabled"] is True
    types = client.get("/api/v1/jobs/types").json()
    assert "backfill" in types["types"] and types["backfill_stages"][0] == "news"
    assert types["params"]["run"] == ["date", "kind"]

    # the backfill job validates like the others: regenerate needs confirm
    r = client.post(
        "/api/v1/jobs",
        json={
            "type": "backfill",
            "params": {"from": "2026-11-01", "to": "2026-11-05", "regenerate": True},
        },
        headers=AUTH,
    )
    assert r.status_code == 400 and "confirm" in r.json()["detail"]
    r = client.post(
        "/api/v1/jobs",
        json={"type": "backfill", "params": {"from": "2026-11-05", "stages": "prices,analyze"}},
        headers=AUTH,
    )
    assert r.status_code == 202
    job = client.get(f"/api/v1/jobs/{r.json()['id']}").json()
    assert job["params"] == {
        "from": "2026-11-05",
        "to": "2026-11-05",
        "stages": ["prices", "analyze"],
    }


def test_caps_and_weights_are_rounded_everywhere(client):
    idx = client.get("/api/v1/index/m100").json()
    assert (
        idx["items"][0]["market_cap"] == 5_000_000_000_000 and idx["items"][0]["weight"] == 0.5556
    )
    assert client.get("/api/v1/index/m100/history/AAPL").json()["items"][0]["weight"] == 0.5556
    assert client.get("/api/v1/universe").json()["members"][1]["market_cap"] == 4_000_000_000_000
    assert client.get("/api/v1/tickers").json()["items"][0]["market_cap"] == 5_000_000_000_000
    assert client.get("/api/v1/companies/AAPL").json()["history"][0]["weight"] == 0.5556
