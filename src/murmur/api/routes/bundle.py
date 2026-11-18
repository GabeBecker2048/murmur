"""Bundles: a date range of the published datasets as one streamed .tar.gz."""

from __future__ import annotations

import threading
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from murmur import bundle
from murmur.api.auth import has_token
from murmur.api.deps import get_config
from murmur.config import Config

router = APIRouter(tags=["data"])

_BUILDING = threading.Lock()
"""One bundle at a time: the API container has one CPU and 512 MB."""


def _split(value: str | None) -> list[str] | None:
    items = [x.strip() for x in (value or "").split(",") if x.strip()]
    return items or None


def _day(value: str, name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, f"{name} must be YYYY-MM-DD") from None


@router.get(
    "/bundle",
    responses={200: {"content": {"application/gzip": {}, "application/json": {}}}},
)
def bundle_download(
    config: Config = Depends(get_config),
    authorized: bool = Depends(has_token),
    from_: str = Query(..., alias="from"),
    to: str = Query(...),
    datasets: str | None = None,
    tables: str | None = None,
    format: str = "parquet",
    dry_run: bool = False,
):
    """Any date range of the published datasets as one ``.tar.gz``: one file per table
    (rows of the range, gathered from the day and month files), ``README.txt`` with
    every license notice, ``index.json`` and ``SHA256SUMS``. ``datasets`` and ``tables``
    are comma-separated (empty = all public ones; a table may be ``dataset/table``).
    ``format=csv`` is limited to 93 days for ``news``. ``dry_run=true`` answers with
    the plan and an estimated size instead. One bundle is built at a time (429)."""
    try:
        p = bundle.plan(
            config,
            _day(from_, "from"),
            _day(to, "to"),
            datasets=_split(datasets),
            tables=_split(tables),
            fmt=format,
            include_private=authorized,
        )
    except bundle.BundleError as e:
        status = 403 if str(e).startswith("private") else 400
        raise HTTPException(status, str(e)) from None
    if dry_run:
        return p.describe()
    if not _BUILDING.acquire(blocking=False):
        raise HTTPException(
            429, "another bundle is being built; try again shortly", headers={"Retry-After": "30"}
        )

    def body():
        try:
            yield from bundle.stream(config, p)
        finally:
            _BUILDING.release()

    return StreamingResponse(
        body(),
        media_type="application/gzip",
        headers={
            "Content-Disposition": f'attachment; filename="{p.name}.tar.gz"',
            "Cache-Control": "no-store",
        },
    )
