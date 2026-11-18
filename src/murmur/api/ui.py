"""The web UI: static files from the package (``murmur/ui/static``) and a single-page
app whose routes all resolve to ``index.html``, with each page's title, description and
link-preview tags filled in on the server (so a shared link previews properly without
running the page). The UI talks only to ``/api/v1`` and ``/data``; nothing here reads
the store."""

from __future__ import annotations

import html
from importlib import resources
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from starlette.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from starlette.staticfiles import StaticFiles

UI_ROUTES = (
    "",
    "findings",
    "news-vs-prices",
    "companies",
    "index",
    "headlines",
    "downloads",
    "methodology",
    "status",
    "story",
    "admin",
)
"""First path segments the app handles client-side (``/companies/AAPL`` included)."""
ALIASES = {"leadlag": "news-vs-prices", "tickers": "companies", "sources": "news-vs-prices"}
"""Addresses of 0.7-0.8 pages, answered with a permanent redirect (query string kept)."""
EMBEDS = ("findings",)
RESERVED = ("api", "data", "static", "docs", "redoc", "openapi.json", "feed.xml")

SITE_DESCRIPTION = (
    "Does news predict stock prices, follow them, or move with them? Daily headline "
    "sentiment of the 100 largest US companies against their returns, as open data."
)
PAGES: dict[str, tuple[str, str]] = {
    "": ("Overview", SITE_DESCRIPTION),
    "findings": (
        "Findings",
        "What a year of headlines and returns shows: news moves with stock prices on the same "
        "day and trails them a little, but does not predict the next day. With the checks.",
    ),
    "story": (
        "Story",
        "How three friends' side project became Murmur: the prototype, who built what, and "
        "the rebuild into a daily pipeline.",
    ),
    "news-vs-prices": (
        "News vs. prices",
        "Every company's headline sentiment against its stock's return, a session before, "
        "the same session and a session after. Live, recomputed every trading day.",
    ),
    "companies": (
        "Companies",
        "Headline sentiment, returns and the lead or lag of each of the largest US companies.",
    ),
    "index": (
        "Murmur 100",
        "The cap-weighted index of the 100 largest US companies: constituents, weights, "
        "monthly changes and the daily level.",
    ),
    "headlines": ("Headlines", "Search every scored headline by words, company, tone and date."),
    "downloads": (
        "Data",
        "Murmur's open data: any date range as one download, monthly archives, stable "
        "latest URLs and every file. CC BY 4.0.",
    ),
    "methodology": (
        "Methodology",
        "How Murmur collects headlines, scores them with FinBERT, pairs them with returns, "
        "and checks the conclusion.",
    ),
    "status": ("Status", "Whether the daily pipeline is running and the data complete."),
    "admin": ("Admin", "Murmur administration."),
}
NOT_FOUND = ("Not found", SITE_DESCRIPTION)


def static_dir() -> Path:
    return Path(str(resources.files("murmur.ui").joinpath("static")))


class UiStatic(StaticFiles):
    """Long cache for the vendored files (versions pinned in vendor/README.md); our own
    files are revalidated on every load (ETag/304), so a release never leaves a browser
    running a stale module next to a new shell."""

    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        path = str(args[0]) if args else ""
        if "/vendor/" in path or path.startswith("vendor/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response


def page_meta(path: str) -> tuple[str, str]:
    parts = [p for p in path.split("/") if p]
    first = parts[0] if parts else ""
    title, description = PAGES.get(first, NOT_FOUND)
    if first == "companies" and len(parts) > 1:
        ticker = parts[1].upper()[:12]
        title = f"{ticker} · Companies"
        description = f"Headline sentiment and stock returns for {ticker}, scored daily by Murmur."
    return title, description


def render_shell(template: str, request: Request, path: str) -> str:
    """``index.html`` with the page's <title>, description, canonical URL and Open Graph /
    Twitter tags. Every value is escaped; nothing from the request reaches the page except
    its own URL."""
    title, description = page_meta(path)
    full_title = "Murmur" if title == "Overview" else f"{title} · Murmur"
    base = str(request.base_url).rstrip("/")
    url = base + "/" + path.lstrip("/")
    image = f"{base}/static/brand/social-card.png"
    e = html.escape
    tags = "\n".join(
        [
            f'<link rel="canonical" href="{e(url)}">',
            '<meta property="og:site_name" content="Murmur">',
            '<meta property="og:type" content="website">',
            f'<meta property="og:title" content="{e(full_title)}">',
            f'<meta property="og:description" content="{e(description)}">',
            f'<meta property="og:url" content="{e(url)}">',
            f'<meta property="og:image" content="{e(image)}">',
            '<meta property="og:image:width" content="1200">',
            '<meta property="og:image:height" content="630">',
            '<meta name="twitter:card" content="summary_large_image">',
        ]
    )
    out = template.replace("<title>Murmur</title>", f"<title>{e(full_title)}</title>", 1)
    out = out.replace(
        f'<meta name="description" content="{SITE_DESCRIPTION}">',
        f'<meta name="description" content="{e(description)}">',
        1,
    )
    return out.replace("<!--murmur:meta-->", tags, 1)


def mount_ui(app: FastAPI) -> None:
    root = static_dir()
    app.mount("/static", UiStatic(directory=str(root), check_dir=False), name="static")
    template = (root / "index.html").read_text(encoding="utf-8")
    embed = root / "embed.html"

    @app.get("/embed/{name}", include_in_schema=False)
    def embed_page(name: str):
        if name not in EMBEDS:
            raise HTTPException(404, "not found")
        return FileResponse(embed, headers={"Cache-Control": "no-cache"})

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str, request: Request):
        first = path.split("/", 1)[0]
        if first in RESERVED or "." in path.rsplit("/", 1)[-1]:
            raise HTTPException(404, "not found")
        if first in ALIASES:
            rest = "" if first == "sources" else path[len(first) :]
            target = f"/{ALIASES[first]}{rest}"
            if request.url.query:
                target += f"?{request.url.query}"
            return RedirectResponse(target, status_code=301)
        status = 200 if first in UI_ROUTES else 404
        return HTMLResponse(
            render_shell(template, request, path),
            status_code=status,
            headers={"Cache-Control": "no-cache"},
        )
