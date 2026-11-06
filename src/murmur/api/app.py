"""The FastAPI application: `/api/v1/...` queries and control, `/data/...` files,
`/docs`. ``create_app`` wires the config provider, the job worker (with the
scheduler tick) and the routers."""

from __future__ import annotations

import logging
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response
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
    rewritten by the second analysis pass, so they get a short max-age."""

    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        path = str(args[0]) if args else ""
        if path.endswith("index.json"):
            response.headers["Cache-Control"] = "no-cache"
        else:
            response.headers["Cache-Control"] = "public, max-age=300"
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
            "jobs; `/data` serves the published Parquet tree. Mutating routes need "
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
        charts,
        correlations,
        days,
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
        universe,
        days,
        tickers,
        correlations,
        leadlag,
        sources,
        charts,
    ):
        app.include_router(module.router, prefix=API_PREFIX)

    published = provider.get().published_dir
    app.mount("/data", PublishedFiles(directory=str(published), check_dir=False), name="data")
    Path(published).mkdir(parents=True, exist_ok=True)
    return app
