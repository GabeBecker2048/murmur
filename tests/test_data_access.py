"""Getting the data out: range bundles, monthly archives, stable latest URLs, headline
search, the Atom feed and the citation."""

import gzip
import hashlib
import io
import json
import tarfile
from datetime import date
from xml.etree import ElementTree as ET

import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from murmur import bundle
from murmur.api.app import create_app
from murmur.config import ConfigProvider, load_config
from murmur.export import export_day, update_archives
from murmur.store import Store
from tests.test_analysis import cfg  # noqa: F401
from tests.test_api import TOKEN, client, provider  # noqa: F401, F811
from tests.test_export import TODAY, prepared

OCT29, OCT30, NOV4, NOV5 = (
    date(2026, 10, 29),
    date(2026, 10, 30),
    date(2026, 11, 4),
    date(2026, 11, 5),
)


@pytest.fixture
def tree(store, scorer, cfg):  # noqa: F811
    """October 30 (compacted into month files) and November 5 (day files)."""
    prepared(store, scorer, cfg, day=OCT30, prev=OCT29)
    export_day(store, cfg, OCT30, today=TODAY)
    november(store)
    prepared(store, scorer, cfg, day=NOV5, prev=NOV4)
    export_day(store, cfg, NOV5, today=TODAY)
    return cfg


def november(store):
    """The shared seed reuses its article URLs (dedup would fold November into October),
    so November gets headlines of its own."""
    from murmur.models import Article

    for t in ("AAPL", "MSFT"):
        store.upsert_article(
            Article(f"https://e/{t}/nov5", f"{t} rallies", "2026-11-05T15:00:00Z", "yahoo"),
            t,
            "2026-11-05T23:00:00Z",
        )


def _members(blob: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        return {m.name: tar.extractfile(m).read() for m in tar.getmembers()}


def _rows(data: bytes, column: str) -> list:
    return pq.read_table(io.BytesIO(data)).column(column).to_pylist()


def test_a_bundle_spans_month_files_and_day_files(tree):
    p = bundle.plan(tree, OCT30, NOV5, datasets=["news", "prices"])
    assert p.describe()["estimated_bytes"] > 0
    blob = b"".join(bundle.stream(tree, p))
    files = _members(blob)
    root = "murmur_2026-10-30_2026-11-05"
    assert set(files) == {
        f"{root}/news/articles.parquet",
        f"{root}/prices/prices.parquet",
        f"{root}/README.txt",
        f"{root}/index.json",
        f"{root}/SHA256SUMS",
    }
    days = set(_rows(files[f"{root}/news/articles.parquet"], "trading_day"))
    assert days == {"2026-10-30", "2026-11-05"}
    # checksums cover every data member
    for line in files[f"{root}/SHA256SUMS"].decode().strip().splitlines():
        digest, name = line.split("  ")
        assert hashlib.sha256(files[f"{root}/{name}"]).hexdigest() == digest
    readme = files[f"{root}/README.txt"].decode()
    assert "Source terms apply" in readme and "Takedown available by request" in readme
    meta = pq.read_metadata(io.BytesIO(files[f"{root}/news/articles.parquet"])).metadata
    assert meta[b"murmur_data_version"] and meta[b"murmur_period"] == b"2026-10-30..2026-11-05"
    index = json.loads(files[f"{root}/index.json"])
    assert set(index["datasets"]) == {"news", "prices"}


def test_rows_are_cut_to_the_range(tree):
    p = bundle.plan(tree, NOV4, NOV5, datasets=["news"], tables=["articles"])
    files = _members(b"".join(bundle.stream(tree, p)))
    data = files["murmur_2026-11-04_2026-11-05/news/articles.parquet"]
    assert set(_rows(data, "trading_day")) == {"2026-11-05"}
    csv = bundle.plan(tree, OCT30, OCT30, datasets=["prices"], fmt="csv")
    files = _members(b"".join(bundle.stream(tree, csv)))
    text = files["murmur_2026-10-30_2026-10-30/prices/prices.csv"].decode()
    assert '"date"' in text.splitlines()[0] and "2026-11-05" not in text


def test_plan_refusals(tree):
    with pytest.raises(bundle.BundleError, match="limited"):
        bundle.plan(tree, date(2026, 1, 1), NOV5, datasets=["news"], fmt="csv")
    with pytest.raises(bundle.BundleError, match="format"):
        bundle.plan(tree, OCT30, NOV5, fmt="xlsx")
    with pytest.raises(bundle.BundleError, match="unknown"):
        bundle.plan(tree, OCT30, NOV5, datasets=["nope"])
    with pytest.raises(bundle.BundleError, match="before"):
        bundle.plan(tree, NOV5, OCT30)
    with pytest.raises(bundle.BundleError, match="nothing"):
        bundle.plan(tree, date(2020, 1, 1), date(2020, 1, 31))


def test_private_datasets_stay_out(tree, tmp_path):
    user = tree.source_path
    user.write_text(user.read_text() + '\n[datasets]\nprivate = ["prices"]\n')
    private = load_config(user, env={})
    everything = bundle.plan(private, OCT30, NOV5)
    assert "prices" not in {m.dataset for m in everything.members}
    with pytest.raises(bundle.BundleError, match="private"):
        bundle.plan(private, OCT30, NOV5, datasets=["prices"])
    allowed = bundle.plan(private, OCT30, NOV5, datasets=["prices"], include_private=True)
    assert [m.dataset for m in allowed.members] == ["prices"]


def test_monthly_archives(tree):
    written = update_archives(tree, today=TODAY)
    archive = tree.published_dir / "archives" / "murmur-202610.tar.gz"
    assert archive in written and archive.exists()
    assert not (tree.published_dir / "archives" / "murmur-202611.tar.gz").exists()  # unfinished
    names = _members(archive.read_bytes())
    assert "murmur_2026-10-01_2026-10-31/news/articles.parquet" in names
    assert update_archives(tree, today=TODAY) == []  # unchanged month: left alone
    manifest = json.loads((tree.published_dir / "index.json").read_text())
    assert [a["month"] for a in manifest["archives"]["files"]] == ["2026-10"]


def test_latest_is_one_session_whatever_file_holds_it(store, scorer, cfg):  # noqa: F811
    prepared(store, scorer, cfg, day=OCT30, prev=OCT29)
    export_day(store, cfg, OCT30, today=TODAY)  # only a compacted October exists
    data, path = bundle.latest(cfg, "news", "articles")
    assert path == "news/2026/10/202610.articles.parquet"
    assert set(data.column("trading_day").to_pylist()) == {"2026-10-30"}
    november(store)
    prepared(store, scorer, cfg, day=NOV5, prev=NOV4)
    export_day(store, cfg, NOV5, today=TODAY)
    data, path = bundle.latest(cfg, "news", "articles")
    assert path == "news/2026/11/05/20261105.articles.parquet"
    monthly, path = bundle.latest(cfg, "m100", "constituents")
    assert path == "m100/2026/11/202611.constituents.parquet" and monthly.num_rows
    assert bundle.latest(cfg, "news", "nope") is None
    assert bundle.latest(cfg, "nope", "articles") is None


def test_bundle_and_latest_over_http(tree):
    own = ConfigProvider(tree.source_path, env={"MURMUR_API_TOKEN": TOKEN})
    with TestClient(create_app(own, run_worker=False)) as c:
        plan = c.get("/api/v1/bundle?from=2026-10-30&to=2026-11-05&datasets=news&dry_run=true")
        assert plan.status_code == 200 and plan.json()["tables"][0]["table"] == "articles"
        r = c.get("/api/v1/bundle?from=2026-10-30&to=2026-11-05&datasets=news,prices")
        assert r.status_code == 200 and r.headers["content-type"] == "application/gzip"
        assert "murmur_2026-10-30_2026-11-05.tar.gz" in r.headers["content-disposition"]
        assert gzip.decompress(r.content)  # a complete gzip stream
        assert len(_members(r.content)) == 5
        assert c.get("/api/v1/bundle?from=2026-11-05&to=2026-10-30").status_code == 400
        assert c.get("/api/v1/bundle?from=x&to=2026-10-30").status_code == 422
        from murmur.api.routes import bundle as route

        route._BUILDING.acquire()
        try:
            busy = c.get("/api/v1/bundle?from=2026-10-30&to=2026-11-05")
            assert busy.status_code == 429 and busy.headers["retry-after"]
        finally:
            route._BUILDING.release()

        latest = c.get("/data/news/latest.articles.parquet")
        assert latest.status_code == 200
        assert (
            latest.headers["content-location"] == "/data/news/2026/11/05/20261105.articles.parquet"
        )
        assert latest.headers["x-murmur-license"] == "Source terms apply"
        assert set(_rows(latest.content, "trading_day")) == {"2026-11-05"}
        csv = c.get("/data/prices/latest.prices.parquet?format=csv")
        assert csv.headers["content-type"].startswith("text/csv") and "2026-11-05" in csv.text
        assert c.get("/data/news/latest.nope.parquet").status_code == 404


def test_headline_search(client):  # noqa: F811
    hits = client.get("/api/v1/articles?q=apple").json()
    assert hits["total"] == 1 and hits["items"][0]["title"] == "Apple beats"
    assert client.get("/api/v1/articles?q=APPLE beats").json()["total"] == 1
    assert client.get("/api/v1/articles?q=apple misses").json()["total"] == 0  # every word
    assert client.get("/api/v1/articles?q=%25%25").json()["total"] == 0  # % is literal
    assert client.get("/api/v1/articles?q=a").status_code == 422
    neg = client.get("/api/v1/articles?sentiment=negative").json()
    assert [i["title"] for i in neg["items"]] == ["Microsoft misses"]
    assert client.get("/api/v1/articles?sentiment=angry").status_code == 422
    both = client.get("/api/v1/articles?from=2026-11-05&to=2026-11-05&limit=1").json()
    assert both["total"] == 2 and len(both["items"]) == 1
    assert client.get("/api/v1/articles?ticker=msft").json()["total"] == 1


def test_feed_is_valid_atom(client, provider):  # noqa: F811
    r = client.get("/feed.xml")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/atom+xml")
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(r.content)
    assert root.tag == "{http://www.w3.org/2005/Atom}feed"
    for tag in ("title", "id", "updated"):
        assert root.find(f"a:{tag}", ns) is not None
    entries = root.findall("a:entry", ns)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.find("a:title", ns).text == "Week 2026-W45: same-session r = +1.000"
    assert "2 headlines" in entry.find("a:summary", ns).text
    for tag in ("id", "updated", "link"):
        assert entry.find(f"a:{tag}", ns) is not None


def test_citation(client):  # noqa: F811
    c = client.get("/api/v1/citation").json()
    assert c["bibtex"].startswith("@misc{murmur,") and "Burch-Celentano, Wyatt" in c["bibtex"]
    assert c["apa"].startswith("Becker, G., Burch-Celentano, W., & Chapoton, S.")
    assert "cff-version: 1.2.0" in c["cff"] and "Murmur" in c["apa"]
    assert client.get("/api/v1/citation?findings=nope").status_code == 404


def test_citation_cff_in_the_repo_is_generated():
    from pathlib import Path

    from murmur import __version__
    from murmur.citation import cff
    from murmur.publish import DATA_VERSION

    text = (Path(__file__).parents[1] / "CITATION.cff").read_text()
    assert text == cff(__version__, DATA_VERSION)


def test_unused_store_is_fine():
    with Store(":memory:") as s:
        assert s.findings_list() == []


def test_rate_limits_per_client(tree, tmp_path):
    user = tmp_path / "limits.toml"
    user.write_text(
        tree.source_path.read_text() + "\n[api]\nbundles_per_hour = 1\nsearches_per_minute = 2\n"
    )
    own = ConfigProvider(user, env={"MURMUR_API_TOKEN": TOKEN})
    with TestClient(create_app(own, run_worker=False)) as c:
        url = "/api/v1/bundle?from=2026-11-05&to=2026-11-05&datasets=prices"
        assert c.get(url).status_code == 200
        assert c.get(url + "&dry_run=true").status_code == 200  # dry runs are free
        over = c.get(url)
        assert over.status_code == 429 and int(over.headers["retry-after"]) > 0
        assert "1 per hour" in over.json()["detail"]
        auth = {"Authorization": f"Bearer {TOKEN}"}
        assert c.get(url, headers=auth).status_code == 200  # the token is exempt

        assert c.get("/api/v1/articles?q=apple").status_code == 200
        assert c.get("/api/v1/articles").status_code == 200
        assert c.get("/api/v1/articles?q=apple").status_code == 429
        assert c.get("/api/v1/articles?q=apple", headers=auth).status_code == 200


def test_rate_limiter_window():
    from murmur.api.ratelimit import RateLimiter

    now = [0.0]
    rl = RateLimiter(clock=lambda: now[0])
    assert rl.hit(("x", "a"), 2, 60) == 0 and rl.hit(("x", "a"), 2, 60) == 0
    assert rl.hit(("x", "a"), 2, 60) == 60
    assert rl.hit(("x", "b"), 2, 60) == 0  # per client
    now[0] = 61
    assert rl.hit(("x", "a"), 2, 60) == 0  # the window slid
