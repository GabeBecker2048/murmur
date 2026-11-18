"""Findings snapshots: the robustness checks behave as designed on planted data, the
membership of each session is respected, frozen snapshots never change, and the job,
CLI, API and published file agree."""

import json
from datetime import date

import numpy as np
import pytest
from fastapi.testclient import TestClient

from murmur import findings
from murmur.clock import DAY, INTRADAY, OVERNIGHT, sessions_between
from murmur.config import ConfigProvider, load_config
from murmur.jobs import JobError, validate
from murmur.models import PriceBar, UniverseMember
from murmur.store import Store

MODEL = "fake/keywords"
TICKERS = [f"T{i:02d}" for i in range(12)]
START, END = date(2026, 8, 3), date(2026, 10, 30)


@pytest.fixture
def cfg(tmp_path):
    user = tmp_path / "c.toml"
    lines = [f'data_dir = "{tmp_path / "data"}"', "[sentiment]", f'model = "{MODEL}"']
    lines += ["[universe]", 'source = "static"', "[universe.static]"]
    lines += [f'{t} = "{t}"' for t in TICKERS]
    user.write_text("\n".join(lines) + "\n")
    return load_config(user, env={})


def seed(store, *, mood_only=False, outsider=False, seed=3):
    """Returns = market move + company move. Day/overnight sentiment tracks the company
    move (``mood_only``: the market move instead, identical for every company that
    day); intraday sentiment is noise. With ``outsider``, an extra ticker with perfectly
    correlated sentiment is stored but never a member of the universe."""
    rng = np.random.default_rng(seed)
    days = sessions_between(date(2026, 7, 30), date(2026, 11, 3))
    tickers = TICKERS + (["ZZZ"] if outsider else [])
    market = rng.normal(0, 0.01, len(days))
    own = rng.normal(0, 0.01, (len(days), len(tickers)))
    for i, d in enumerate(days):
        iso = d.isoformat()
        store.record_universe(
            iso,
            [UniverseMember(t, t, 1e11 * (20 - j), j + 1, j + 1) for j, t in enumerate(TICKERS)],
            "test",
        )
        rets = market[i] + own[i]
        store.upsert_returns(
            (t, iso, iso, 100.0, 100.0, 100.0, r / 2, r / 2, r)
            for t, r in zip(tickers, rets, strict=True)
        )
        store.upsert_prices([PriceBar(t, iso, 1, 1, 1, 1, 1, 1) for t in tickers], "t")
        if mood_only:
            score = {t: (2, float(market[i] * 50)) for t in tickers}
        else:
            score = {
                t: (2, float(own[i, j] * 50 + rng.normal(0, 0.2))) for j, t in enumerate(tickers)
            }
        if outsider:
            score["ZZZ"] = (2, float(rets[-1] * 50))
        store.upsert_window_sentiment(iso, DAY, MODEL, score)
        store.upsert_window_sentiment(iso, OVERNIGHT, MODEL, score)
        store.upsert_window_sentiment(
            iso, INTRADAY, MODEL, {t: (1, float(rng.normal())) for t in tickers}
        )
    return days


def _pair(rows, window, return_type, lag):
    return next(
        r for r in rows if (r["window"], r["return_type"], r["lag"]) == (window, return_type, lag)
    )


def test_company_signal_survives_every_check(store, cfg):
    seed(store)
    p = findings.compute(store, cfg, START, END, permutations=200)
    head = p["headline"]["lags"]
    assert head["0"]["r"] > 0.5
    assert abs(head["1"]["r"]) < 0.2 and abs(head["-1"]["r"]) < 0.2
    # removing the market move keeps (here: strengthens) a company-specific link
    assert _pair(p["market_adjusted"], DAY, "daily", 0)["r"] > head["0"]["r"]
    ps = _pair(p["per_session"], DAY, "daily", 0)
    assert ps["mean_r"] > 0.5 and ps["t"] > 10 and ps["share_positive"] > 0.9
    assert ps["sessions"] == p["coverage"]["sessions"]
    placebo = {r["lag"]: r for r in p["placebo"]}
    assert placebo[0]["real_r"] > placebo[0]["null_high"]
    assert placebo[0]["permutations"] == 200
    # noise in the intraday window
    assert abs(_pair(p["pairs"], INTRADAY, "intraday", 0)["r"]) < 0.2
    assert {r["ranks"] for r in p["by_cap"]} == {"1-50", "51-100"}
    assert p["coverage"]["companies"] == len(TICKERS)
    assert p["meta"]["data_version"]


def test_market_mood_alone_is_not_company_evidence(store, cfg):
    """Sentiment equal to the market's move: the pooled r is high, but the market
    adjustment, the per-session view and the shuffle test all see nothing."""
    seed(store, mood_only=True)
    p = findings.compute(store, cfg, START, END, permutations=200)
    assert p["headline"]["lags"]["0"]["r"] > 0.5
    assert abs(_pair(p["market_adjusted"], DAY, "daily", 0)["r"]) < 0.15
    # a sentiment constant within each session has no cross-sectional correlation
    assert not [r for r in p["per_session"] if (r["window"], r["lag"]) == (DAY, 0)]
    placebo = {r["lag"]: r for r in p["placebo"]}
    # shuffling a per-session constant changes nothing: the real r IS the null
    assert placebo[0]["real_r"] == pytest.approx(placebo[0]["null_mean"])
    assert placebo[0]["z"] is None or abs(placebo[0]["z"]) < 2


def test_only_that_sessions_members_count(store, cfg):
    seed(store, outsider=True)
    p = findings.compute(store, cfg, START, END, permutations=50)
    assert p["coverage"]["companies"] == len(TICKERS)
    n = p["headline"]["lags"]["0"]["n"]
    assert n == p["coverage"]["sessions"] * len(TICKERS)


def test_no_data_is_an_error(store, cfg):
    with pytest.raises(findings.FindingsError):
        findings.compute(store, cfg, START, END)
    with pytest.raises(findings.FindingsError):
        findings.compute(store, cfg, END, START)


def test_frozen_snapshots_never_change(store, cfg, monkeypatch):
    seed(store)
    monkeypatch.setattr(findings, "PERMUTATIONS", 50)
    with pytest.raises(findings.FindingsError, match="no reviewed text"):
        findings.create(store, cfg, "test-x", START, END, freeze=True, permutations=50)
    draft = findings.create(store, cfg, "test-x", START, END, permutations=50)
    assert draft["kind"] == "draft" and draft["text"] is None
    findings.create(store, cfg, "test-x", START, END, permutations=50)  # drafts are replaced
    monkeypatch.setattr(
        findings,
        "text_for",
        lambda _id: {"title": "T", "headline": "H", "summary": ["S"], "caveats": ["C"]},
    )
    frozen = findings.create(store, cfg, "test-x", START, END, freeze=True, permutations=50)
    assert frozen["kind"] == "frozen" and frozen["text"]["headline"] == "H"
    with pytest.raises(findings.FindingsError, match="frozen"):
        findings.create(store, cfg, "test-x", START, END, permutations=50)
    with pytest.raises(ValueError):
        store.put_findings("test-x", "draft", "a", "b", "1", "{}")
    assert store.latest_frozen_findings()["id"] == "test-x"
    for bad in ("", "live", "a b", "x" * 41):
        with pytest.raises(findings.FindingsError):
            findings.create(store, cfg, bad, START, END)


def test_live_row_is_replaced_daily(store, cfg):
    seed(store)
    first = findings.refresh_live(store, cfg, date(2026, 10, 30))
    assert first["kind"] == "live" and first["meta"]["permutations"] == findings.LIVE_PERMUTATIONS
    findings.refresh_live(store, cfg, date(2026, 11, 2))
    rows = [dict(r) for r in store.findings_list()]
    assert [(r["id"], r["end_day"]) for r in rows] == [("live", "2026-11-02")]
    assert findings.refresh_live(Store(":memory:"), cfg, date(2026, 11, 2)) is None


def test_the_packaged_first_year_text_is_complete():
    text = findings.text_for("first-year")
    assert text["title"] and text["headline"]
    assert len(text["summary"]) >= 3 and len(text["caveats"]) >= 3
    assert findings.text_for("nope") is None


def test_job_validation():
    clean, _ = validate(
        "findings", {"id": "2026-10", "from": "2025-11-03", "to": "2026-10-30"}, False
    )
    assert clean == {"id": "2026-10", "from": "2025-11-03", "to": "2026-10-30"}
    with pytest.raises(JobError, match="confirm"):
        validate(
            "findings", {"id": "a", "from": "2026-01-01", "to": "2026-02-01", "freeze": 1}, False
        )
    clean, _ = validate(
        "findings", {"id": "a", "from": "2026-01-01", "to": "2026-02-01", "freeze": 1}, True
    )
    assert clean["freeze"] is True
    for bad in (
        {},
        {"id": "live", "from": "2026-01-01", "to": "2026-01-02"},
        {"id": "a", "from": "2026-02-01", "to": "2026-01-01"},
    ):
        with pytest.raises(JobError):
            validate("findings", bad, False)
    assert validate("findings", {"live": True}, False)[0] == {"live": True}


def test_api_published_file_and_chart(tmp_path, monkeypatch):
    from murmur.api.app import create_app
    from murmur.export import export_findings, manifest

    ops = tmp_path / "murmur.toml"
    lines = [f'data_dir = "{tmp_path / "data"}"', "[sentiment]", f'model = "{MODEL}"']
    lines += ["[universe]", 'source = "static"', "[universe.static]"]
    lines += [f'{t} = "{t}"' for t in TICKERS]
    ops.write_text("\n".join(lines) + "\n")
    provider = ConfigProvider(ops, env={})
    config = provider.get()
    monkeypatch.setattr(
        findings,
        "text_for",
        lambda _id: {"title": "T", "headline": "H", "summary": ["S"], "caveats": ["C"]},
    )
    with Store(config.store_path) as s:
        seed(s)
        findings.create(s, config, "draft-1", START, END, permutations=50)
        findings.create(s, config, "2026-10", START, END, freeze=True, permutations=50)
        findings.refresh_live(s, config, END)
        written = export_findings(s, config)
        assert export_findings(s, config) == []  # unchanged files are left alone
    names = sorted(p.name for p in written)
    assert names == ["2026-10.json", "latest.json"]  # never drafts or the live row
    doc = json.loads((config.published_dir / "findings" / "2026-10.json").read_text())
    assert doc["license"]["name"] == "CC BY 4.0" and doc["text"]["headline"] == "H"
    m = manifest(config.published_dir, config)
    assert m["data_version"] and [f["id"] for f in m["findings"]["files"]] == ["2026-10"]

    with TestClient(create_app(provider, run_worker=False)) as client:
        listed = client.get("/api/v1/findings").json()
        assert {r["id"]: r["kind"] for r in listed["items"]} == {
            "2026-10": "frozen",
            "draft-1": "draft",
            "live": "live",
        }
        assert client.get("/api/v1/findings/latest").json()["id"] == "2026-10"
        assert client.get("/api/v1/findings/live").json()["kind"] == "live"
        assert client.get("/api/v1/findings/nope").status_code == 404
        png = client.get("/api/v1/charts/findings.png?theme=dark")
        assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
        assert client.get("/api/v1/charts/findings.png?theme=x").status_code == 422
        assert client.get("/data/findings/latest.json").json()["id"] == "2026-10"


def test_one_source_only(store, cfg, monkeypatch):
    """A snapshot restricted to one news source uses only that source's sentiment: here
    finnhub carries the company signal and yahoo is noise."""
    days = seed(store)
    rng = np.random.default_rng(11)
    for d in days:
        iso = d.isoformat()
        rows = store.window_sentiment_between(iso, iso, MODEL, "all")
        signal = {r["ticker"]: (2, r["mean_score"]) for r in rows if r["window"] == DAY}
        store.upsert_window_sentiment(iso, DAY, MODEL, signal, source="finnhub")
        noise = {t: (1, float(rng.normal())) for t in TICKERS}
        store.upsert_window_sentiment(iso, DAY, MODEL, noise, source="yahoo")
    monkeypatch.setattr(findings, "sources_for_analysis", lambda s: ["all", "finnhub", "yahoo"])
    fin = findings.compute(store, cfg, START, END, permutations=50, source="finnhub")
    yah = findings.compute(store, cfg, START, END, permutations=50, source="yahoo")
    assert fin["meta"]["source"] == "finnhub" and fin["coverage"]["sources"] == ["finnhub"]
    assert fin["headline"]["lags"]["0"]["r"] > 0.5
    assert abs(yah["headline"]["lags"]["0"]["r"]) < 0.2
    assert {r["source"] for r in fin["by_source"]} == {"finnhub", "yahoo"}  # comparison kept
    with pytest.raises(findings.FindingsError, match="no scored nope headlines"):
        findings.compute(store, cfg, START, END, source="nope")


def test_only_drafts_can_be_deleted(store, cfg, monkeypatch):
    seed(store)
    findings.create(store, cfg, "d1", START, END, permutations=50)
    findings.delete_draft(store, "d1")
    assert store.findings_get("d1") is None
    monkeypatch.setattr(
        findings,
        "text_for",
        lambda _id: {"title": "T", "headline": "H", "summary": [], "caveats": []},
    )
    findings.create(store, cfg, "f1", START, END, freeze=True, permutations=50)
    findings.refresh_live(store, cfg, END)
    for sid in ("f1", "live", "nope"):
        with pytest.raises(findings.FindingsError):
            findings.delete_draft(store, sid)
    store.delete_findings("f1")  # the store refuses too
    assert store.findings_get("f1")["kind"] == "frozen"
    clean, _ = validate(
        "findings",
        {"id": "a", "from": "2026-01-01", "to": "2026-02-01", "source": "finnhub"},
        False,
    )
    assert clean["source"] == "finnhub"
    with pytest.raises(JobError):
        validate(
            "findings",
            {"id": "a", "from": "2026-01-01", "to": "2026-02-01", "source": "a b"},
            False,
        )
