"""The published datasets: what exists, as the manifest describes it."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query

from murmur.api.deps import get_config
from murmur.config import Config
from murmur.export import datasets as registry
from murmur.export import manifest

router = APIRouter(tags=["datasets"])


def _manifest(config: Config) -> dict:
    path = config.published_dir / "index.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return manifest(config.published_dir, config)


@router.get("/datasets")
def list_datasets(config: Config = Depends(get_config)):
    """Every dataset with its tables, license and coverage, without the file lists."""
    m = _manifest(config)
    items = [
        {"name": name, **{k: v for k, v in ds.items() if k != "files"}}
        for name, ds in m["datasets"].items()
    ]
    return {
        "murmur_version": m["murmur_version"],
        "generated_at": m["generated_at"],
        "layout": m.get("layout"),
        "items": items,
    }


@router.get("/datasets/{name}")
def get_dataset(
    name: str,
    config: Config = Depends(get_config),
    table: str | None = None,
    from_: str | None = Query(None, alias="from"),
    to: str | None = None,
    limit: int = 500,
    offset: int = 0,
):
    """One dataset with its files (newest first), filterable by table and date."""
    m = _manifest(config)
    ds = m["datasets"].get(name)
    if ds is None:
        known = [d.name for d in registry(config)]
        raise HTTPException(404, f"no dataset {name!r}; datasets are {known}")
    files = ds["files"]
    if table:
        files = [f for f in files if f["table"] == table]
    if from_:
        files = [f for f in files if f["date"] >= from_[: len(f["date"])]]
    if to:
        files = [f for f in files if f["date"] <= to[: len(f["date"])]]
    files = sorted(files, key=lambda f: (f["date"], f["path"]), reverse=True)
    limit = max(1, min(limit, 5000))
    return {
        "name": name,
        **{k: v for k, v in ds.items() if k != "files"},
        "files": files[offset : offset + limit],
        "total": len(files),
        "limit": limit,
        "offset": offset,
    }
