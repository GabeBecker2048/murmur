"""The web UI: the SPA shell is served for its routes (with each page's title and
link-preview tags), old addresses redirect, unknown pages get the shell with a 404, the
embed is served on its own, static files come from the package with the right cache
headers, and the shell references files that exist."""

import re

from murmur.api.ui import ALIASES, UI_ROUTES, static_dir
from tests.test_api import (
    client,  # noqa: F401
    provider,  # noqa: F401
)

PAGE_FILES = {
    "": "overview",
    "findings": "findings",
    "story": "story",
    "news-vs-prices": "newsvsprices",
    "companies": "companies",
    "index": "index",
    "headlines": "headlines",
    "downloads": "downloads",
    "methodology": "methodology",
    "status": "status",
    "admin": "admin",
}


def test_shell_for_ui_routes_only(client):  # noqa: F811
    for path in (
        "/",
        "/findings",
        "/story",
        "/news-vs-prices",
        "/companies",
        "/companies/AAPL",
        "/index",
        "/headlines",
        "/downloads",
        "/methodology",
        "/status",
        "/admin",
        "/admin/jobs/3",
    ):
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.headers["content-type"].startswith("text/html")
        assert r.headers["cache-control"] == "no-cache"
        assert '<script type="module" src="/static/app.js">' in r.text
    # an unknown page gets the shell (it draws the 404 page) with a 404 status
    nope = client.get("/nope")
    assert nope.status_code == 404 and "/static/app.js" in nope.text
    for path in (
        "/nope.png",
        "/news-vs-prices/x.js",
        "/api/v1/nope",
        "/static/missing.js",
        "/data/missing.parquet",
        "/embed/nope",
    ):
        assert client.get(path).status_code == 404, path
    # the API, the docs and the feed are untouched by the catch-all
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/feed.xml").status_code == 200


def test_each_page_has_its_title_and_preview_tags(client):  # noqa: F811
    home = client.get("/").text
    assert "<title>Murmur</title>" in home
    assert (
        '<meta property="og:image" content="http://testserver/static/brand/social-card.png">'
        in home
    )
    assert '<link rel="canonical" href="http://testserver/">' in home
    assert "<!--murmur:meta-->" not in home
    assert "<title>Findings · Murmur</title>" in client.get("/findings").text
    assert "<title>Story · Murmur</title>" in client.get("/story").text
    data = client.get("/downloads").text
    assert "<title>Data · Murmur</title>" in data and "CC BY 4.0" in data
    company = client.get("/companies/aapl").text
    assert "<title>AAPL · Companies · Murmur</title>" in company
    # nothing from the request but its own (escaped) URL reaches the page
    odd = client.get('/companies/"><script>x</script>').text
    assert "<script>x</script>" not in odd


def test_old_addresses_redirect(client):  # noqa: F811
    assert set(ALIASES) == {"leadlag", "tickers", "sources"}
    for old, new in (
        ("/leadlag?months=6&lag=1", "/news-vs-prices?months=6&lag=1"),
        ("/tickers", "/companies"),
        ("/tickers/AAPL?from=2026-01-01", "/companies/AAPL?from=2026-01-01"),
        ("/sources", "/news-vs-prices"),
    ):
        r = client.get(old, follow_redirects=False)
        assert r.status_code == 301 and r.headers["location"] == new, old


def test_embed_page(client):  # noqa: F811
    r = client.get("/embed/findings?id=latest&theme=dark")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert '<script type="module" src="/static/embed.js">' in r.text
    assert "/static/app.js" not in r.text  # no shell, no navigation


def test_static_files_and_cache_headers(client):  # noqa: F811
    app = client.get("/static/app.js")
    assert app.status_code == 200 and "javascript" in app.headers["content-type"]
    assert app.headers["cache-control"] == "no-cache"
    vendor = client.get("/static/vendor/plotly-basic.min.js")
    assert vendor.status_code == 200 and "immutable" in vendor.headers["cache-control"]
    assert vendor.text.startswith("/**") and "plotly.js" in vendor.text[:120]
    font = client.get("/static/vendor/fonts/space-grotesk-latin-600-normal.woff2")
    assert font.status_code == 200 and "immutable" in font.headers["cache-control"]
    assert client.get("/static/app.css").status_code == 200
    favicon = client.get("/static/brand/favicon.svg")
    assert favicon.status_code == 200 and "<circle" in favicon.text


def test_shell_references_exist():
    root = static_dir()
    for shell in ("index.html", "embed.html"):
        html = (root / shell).read_text(encoding="utf-8")
        for ref in re.findall(r'(?:src|href)="/static/([^"]+)"', html):
            assert (root / ref).is_file(), (shell, ref)
    css = (root / "app.css").read_text(encoding="utf-8")
    for ref in re.findall(r'url\("/static/([^"]+)"\)', css):
        assert (root / ref).is_file(), ref
    app = (root / "app.js").read_text(encoding="utf-8")
    routes = dict(re.findall(r'"?([a-z-]*)"?: \(\) => import\("\./pages/([a-z]+)\.js"\)', app))
    assert routes == PAGE_FILES
    assert set(routes) == set(UI_ROUTES)
    for page in routes.values():
        assert (root / "pages" / f"{page}.js").is_file()
    # page modules import only from the shared core, the chart module or siblings, and
    # never the shell (app.js boots the page; lib.js has no side effects)
    for f in [*(root / "pages").glob("*.js"), root / "charts.js", root / "embed.js"]:
        for mod in re.findall(r'from "([^"]+)"', f.read_text(encoding="utf-8")):
            assert mod.startswith("../") or mod.startswith("./"), (f.name, mod)
            assert not mod.endswith("app.js"), (f.name, mod)
    assert "Plotly" in (root / "charts.js").read_text(encoding="utf-8")


def test_brand_files_match_the_brand_module():
    from murmur import brand

    root = static_dir() / "brand"
    assert (root / "mark.svg").read_text() == brand.mark_svg()
    assert (root / "favicon.svg").read_text() == brand.favicon_svg()
    assert (root / "wordmark.svg").read_text() == brand.wordmark_svg()
    for png in ("favicon-32.png", "apple-touch-icon.png", "icon-512.png", "social-card.png"):
        assert (root / png).read_bytes()[:4] == b"\x89PNG", png


def test_no_inline_html_from_data():
    """Everything shown comes from the API; the DOM is built with textContent, never
    innerHTML from data."""
    root = static_dir()
    for f in [
        root / "app.js",
        root / "lib.js",
        root / "charts.js",
        root / "embed.js",
        *(root / "pages").glob("*.js"),
    ]:
        text = f.read_text(encoding="utf-8")
        assert "innerHTML" not in text and "insertAdjacentHTML" not in text, f.name
