"""The findings: the frozen conclusion snapshots and the live 12-month comparison."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from murmur.api import charts
from murmur.api.deps import get_store
from murmur.store import Store

router = APIRouter(tags=["findings"])

LATEST = "latest"


def _payload(store: Store, findings_id: str) -> dict:
    if findings_id == LATEST:
        row = store.latest_frozen_findings()
        if row is None:
            raise HTTPException(404, "no findings have been published yet")
    else:
        row = store.findings_get(findings_id)
        if row is None:
            raise HTTPException(404, f"no findings {findings_id!r}")
    return json.loads(row["payload"])


@router.get("/findings")
def findings_list(store: Store = Depends(get_store)):
    """Every findings snapshot (without numbers): ``frozen`` ones are published and never
    change, ``draft`` ones are under review, ``live`` is recomputed daily."""
    rows = [dict(r) for r in store.findings_list()]
    return {"items": rows, "total": len(rows)}


@router.get("/findings/{findings_id}")
def findings_get(findings_id: str, store: Store = Depends(get_store)):
    """One snapshot with every figure and its prose. ``latest`` = the newest frozen one,
    ``live`` = the last 12 months, recomputed after each trading day."""
    return _payload(store, findings_id)


@router.get("/charts/findings.png", responses={200: {"content": {"image/png": {}}}})
def findings_png(store: Store = Depends(get_store), id: str = LATEST, theme: str = "light"):
    """The Findings chart as a PNG, for embedding where an iframe is not allowed."""
    if theme not in ("light", "dark"):
        raise HTTPException(422, "theme must be light or dark")
    png = charts.findings(_payload(store, id), theme)
    return Response(png, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})


@router.get("/citation")
def citation(request: Request, store: Store = Depends(get_store), findings: str | None = None):
    """How to cite Murmur, or one findings snapshot (``findings=<id>`` or ``latest``):
    BibTeX, APA and the CITATION.cff text."""
    from murmur import __version__
    from murmur import citation as cite
    from murmur.publish import DATA_VERSION

    doc = _payload(store, findings) if findings else None
    url = str(request.base_url).rstrip("/") + "/" if doc else ""
    return {
        "bibtex": cite.bibtex(__version__, DATA_VERSION, doc, url),
        "apa": cite.apa(__version__, DATA_VERSION, doc, url),
        "cff": cite.cff(__version__, DATA_VERSION),
    }
