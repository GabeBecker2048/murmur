"""The web UI: static files from the package (``murmur/ui/static``) and a
single-page app whose routes all resolve to ``index.html``. The UI talks only
to ``/api/v1`` and ``/data``; nothing here reads the store."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from fastapi import FastAPI, HTTPException
from starlette.responses import FileResponse, Response
from starlette.staticfiles import StaticFiles

UI_ROUTES = ("", "leadlag", "tickers", "sources", "index", "downloads", "admin")
"""First path segments the app handles client-side (``/tickers/AAPL`` included)."""
RESERVED = ("api", "data", "static", "docs", "redoc", "openapi.json")


def static_dir() -> Path:
    return Path(str(resources.files("murmur.ui").joinpath("static")))


class UiStatic(StaticFiles):
    """Long cache for the vendored library (its name carries the version), a short
    one for our own files, which change with every release."""

    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        path = str(args[0]) if args else ""
        if "/vendor/" in path or path.startswith("vendor/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "public, max-age=300"
        return response


def mount_ui(app: FastAPI) -> None:
    root = static_dir()
    app.mount("/static", UiStatic(directory=str(root), check_dir=False), name="static")
    index = root / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        first = path.split("/", 1)[0]
        if first in RESERVED:
            raise HTTPException(404, "not found")
        if first not in UI_ROUTES or ("." in path.rsplit("/", 1)[-1]):
            raise HTTPException(404, "not found")
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
