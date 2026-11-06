"""The web UI: the SPA shell is served for its routes and nothing else, static files
come from the package with the right cache headers, and the app shell references
files that exist."""

import re

from murmur.api.ui import UI_ROUTES, static_dir
from tests.test_api import (
    client,  # noqa: F401
    provider,  # noqa: F401
)


def test_shell_for_ui_routes_only(client):  # noqa: F811
    for path in (
        "/",
        "/leadlag",
        "/tickers",
        "/tickers/AAPL",
        "/index",
        "/downloads",
        "/admin",
        "/admin/jobs/3",
    ):
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.headers["content-type"].startswith("text/html")
        assert r.headers["cache-control"] == "no-cache"
        assert '<script type="module" src="/static/app.js">' in r.text
    for path in (
        "/nope",
        "/nope.png",
        "/leadlag/x.js",
        "/api/v1/nope",
        "/static/missing.js",
        "/data/missing.parquet",
    ):
        assert client.get(path).status_code == 404, path
    # the API and docs are untouched by the catch-all
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_static_files_and_cache_headers(client):  # noqa: F811
    app = client.get("/static/app.js")
    assert app.status_code == 200 and "javascript" in app.headers["content-type"]
    assert app.headers["cache-control"] == "no-cache"
    vendor = client.get("/static/vendor/plotly-basic.min.js")
    assert vendor.status_code == 200 and "immutable" in vendor.headers["cache-control"]
    assert vendor.text.startswith("/**") and "plotly.js" in vendor.text[:120]
    assert client.get("/static/app.css").status_code == 200


def test_shell_references_exist():
    root = static_dir()
    html = (root / "index.html").read_text(encoding="utf-8")
    for ref in re.findall(r'(?:src|href)="/static/([^"]+)"', html):
        assert (root / ref).is_file(), ref
    app = (root / "app.js").read_text(encoding="utf-8")
    pages = re.findall(r'import\("\./pages/([a-z]+)\.js"\)', app)
    assert set(pages) == {r for r in UI_ROUTES if r} | {"overview"} - {""}
    for page in pages:
        assert (root / "pages" / f"{page}.js").is_file()
    # every page module imports only from the app core, the chart module or siblings
    for f in (root / "pages").glob("*.js"):
        for mod in re.findall(r'from "([^"]+)"', f.read_text(encoding="utf-8")):
            assert mod.startswith("../") or mod.startswith("./"), (f.name, mod)
    assert "Plotly" in (root / "charts.js").read_text(encoding="utf-8")


def test_no_inline_html_from_data():
    """Everything shown comes from the API; the DOM is built with textContent, never
    innerHTML from data."""
    root = static_dir()
    for f in [root / "app.js", root / "charts.js", *(root / "pages").glob("*.js")]:
        text = f.read_text(encoding="utf-8")
        assert "innerHTML" not in text and "insertAdjacentHTML" not in text, f.name
