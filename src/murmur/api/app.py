"""The FastAPI application: `/api/v1/...` queries and control, `/data/...` files,
`/docs`. ``create_app`` wires the config provider, the job worker (with the
scheduler tick) and the routers."""

from __future__ import annotations

import logging
import os
from collections import deque
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, Response
from starlette.staticfiles import StaticFiles

from murmur import __version__
from murmur.config import ConfigProvider
from murmur.jobs import Worker
from murmur.store import Store

log = logging.getLogger(__name__)

API_PREFIX = "/api/v1"
LOG_LINES = 2000


class _RingLog(logging.Handler):
    def __init__(self, maxlen: int):
        super().__init__(level=logging.INFO)
        self.lines: deque[str] = deque(maxlen=maxlen)
        self.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
            )
        )

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


class PublishedFiles(StaticFiles):
    """The published tree. ``index.json`` must always be revalidated; day files are
    rewritten by the second analysis pass, so they get a short max-age. Any
    ``.parquet`` file is also served as CSV with ``?format=csv`` (converted on
    request; CSV is never stored). Files of a dataset listed in
    ``[datasets] private`` are refused (403) unless the request carries the API
    token, so a dataset whose source terms are unsettled stays in the manifest
    but off the public internet."""

    def __init__(self, *args, provider: ConfigProvider | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.provider = provider

    async def get_response(self, path: str, scope) -> Response:
        dataset = path.split("/", 1)[0]
        if self.provider is not None and dataset in self.provider.get().datasets.private:
            if not self._has_token(scope):
                return JSONResponse(
                    {
                        "detail": f"the {dataset!r} dataset is not published yet; "
                        "its source's terms are being confirmed"
                    },
                    status_code=403,
                    headers={"Cache-Control": "no-store"},
                )
        query = parse_qs(scope.get("query_string", b"").decode())
        csv = query.get("format", [""])[0] == "csv"
        parts = path.split("/")
        if (
            len(parts) == 2
            and parts[1].startswith("latest.")
            and parts[1].endswith(".parquet")
            and self.provider is not None
        ):
            return self._latest_response(parts[0], parts[1][len("latest.") : -len(".parquet")], csv)
        if path.endswith(".parquet") and csv:
            return self._csv_response(path)
        return await super().get_response(path, scope)

    def _latest_response(self, dataset: str, table: str, csv: bool) -> Response:
        """``/data/{dataset}/latest.{table}.parquet``: the newest session (or month) of a
        table, whatever file holds it; ``Content-Location`` names the dated file."""
        import pyarrow.csv as pcsv
        import pyarrow.parquet as pq

        from murmur import bundle

        found = bundle.latest(self.provider.get(), dataset, table)
        if found is None:
            return JSONResponse({"detail": f"no {dataset}/{table} published"}, status_code=404)
        data, dated = found
        buf = BytesIO()
        if csv:
            pcsv.write_csv(data.replace_schema_metadata(None), buf)
            media, ext = "text/csv", "csv"
        else:
            pq.write_table(data, buf)
            media, ext = "application/vnd.apache.parquet", "parquet"
        return Response(
            buf.getvalue(),
            media_type=media,
            headers={
                "Content-Location": f"/data/{dated}",
                "Content-Disposition": f'attachment; filename="{dataset}.latest.{table}.{ext}"',
                "Cache-Control": "public, max-age=300",
                **self._license_headers(dated),
            },
        )

    def _license_headers(self, path: str) -> dict[str, str]:
        if self.provider is None:
            return {}
        from murmur.export import dataset

        ds = dataset(self.provider.get(), path.split("/", 1)[0])
        if ds is None:
            return {}
        lic = ds.license
        return {
            "Link": f'<{lic["url"]}>; rel="license"',
            "X-Murmur-License": lic["name"],
            "X-Murmur-Attribution": lic["attribution"],
            "X-Murmur-Contact": lic.get("contact", ""),
        }

    def _has_token(self, scope) -> bool:
        import hmac

        token = self.provider.get().api.token if self.provider else ""
        if not token:
            return False
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                scheme, _, given = value.decode().partition(" ")
                return scheme.lower() == "bearer" and hmac.compare_digest(given.strip(), token)
        return False

    def _csv_response(self, path: str) -> Response:
        full = Path(self.directory) / path
        try:
            full.resolve().relative_to(Path(self.directory).resolve())
        except ValueError:
            return Response("not found", status_code=404)
        if not full.is_file():
            return Response("not found", status_code=404)
        import pyarrow.csv as pcsv
        import pyarrow.parquet as pq

        buf = BytesIO()
        pcsv.write_csv(pq.read_table(full), buf)
        filename = full.name[: -len(".parquet")] + ".csv"
        return Response(
            buf.getvalue(),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "public, max-age=300",
                **self._license_headers(path),
            },
        )

    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        full = str(args[0]) if args else ""  # Starlette passes the filesystem path
        try:
            rel = os.path.relpath(full, self.directory)
        except ValueError:
            rel = full
        if rel.endswith("index.json"):
            response.headers["Cache-Control"] = "no-cache"
        else:
            response.headers["Cache-Control"] = "public, max-age=300"
            for k, v in self._license_headers(rel).items():
                if v:
                    response.headers[k] = v
        return response


def create_app(
    provider: ConfigProvider | None = None,
    *,
    run_worker: bool = True,
    run_scheduler: bool = True,
) -> FastAPI:
    provider = provider or ConfigProvider()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config = provider.get()
        with Store(config.store_path):  # create + migrate once, before any other connection
            pass
        app.state.worker = None
        if run_worker:
            worker = Worker(provider, scheduler=run_scheduler)
            worker.start()
            app.state.worker = worker
        log.info("murmur %s serving data from %s", __version__, config.data_dir)
        try:
            yield
        finally:
            if app.state.worker is not None:
                app.state.worker.stop(timeout=55)

    app = FastAPI(
        title="Murmur",
        version=__version__,
        description=(
            "News-sentiment vs stock-return pipeline. `/api/v1` answers queries and runs "
            "jobs; `/data` serves the published datasets (Parquet, or CSV with "
            "`?format=csv`); `/` is the dashboard. Mutating routes need "
            "`Authorization: Bearer <MURMUR_API_TOKEN>`."
        ),
        lifespan=lifespan,
    )
    app.state.provider = provider
    ring = _RingLog(LOG_LINES)
    logging.getLogger().addHandler(ring)
    app.state.ring_log = ring

    origins = provider.get().api.cors_origins
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(origins),
            allow_methods=["*"],
            allow_headers=["*"],
        )

    from murmur.api.routes import (
        articles,
        bundle,
        charts,
        companies,
        correlations,
        datasets,
        days,
        feed,
        findings,
        index,
        jobs,
        leadlag,
        runs,
        settings,
        sources,
        status,
        tickers,
        universe,
    )

    for module in (
        status,
        runs,
        jobs,
        settings,
        datasets,
        index,
        companies,
        universe,
        days,
        tickers,
        correlations,
        leadlag,
        findings,
        articles,
        bundle,
        sources,
        charts,
    ):
        app.include_router(module.router, prefix=API_PREFIX)
    app.include_router(feed.router)  # /feed.xml, outside the API prefix

    published = provider.get().published_dir
    app.mount(
        "/data",
        PublishedFiles(directory=str(published), check_dir=False, provider=provider),
        name="data",
    )
    Path(published).mkdir(parents=True, exist_ok=True)
    from murmur.api.ui import mount_ui

    mount_ui(app)  # last: its catch-all route must sit behind the API, /data and /docs
    return app
