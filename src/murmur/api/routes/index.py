"""The cap-weighted index (``m100`` by default): constituents, changes, history."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from murmur.api.deps import get_config, get_store
from murmur.clock import first_trading_day, parse_date
from murmur.config import Config
from murmur.publish import public
from murmur.store import Store
from murmur.universe import applicable_month

router = APIRouter(tags=["index"])


def _check(name: str, config: Config) -> None:
    if name != config.index.name:
        raise HTTPException(
            404, f"no index {name!r}; this instance publishes {config.index.name!r}"
        )


def _month(store: Store, date: str | None, month: str | None) -> str:
    if date:
        try:
            month = applicable_month(parse_date(date))
        except ValueError:
            raise HTTPException(422, "date must be YYYY-MM-DD") from None
    if not month:
        available = store.universe_months()
        if not available:
            raise HTTPException(404, "no index yet")
        month = available[-1]
    return month


@router.get("/index/{name}")
def constituents(
    name: str,
    store: Store = Depends(get_store),
    config: Config = Depends(get_config),
    date: str | None = None,
    month: str | None = None,
):
    """The constituents in force for a month (or on a date), with rank and weight."""
    _check(name, config)
    month = _month(store, date, month)
    members = store.universe_month(month)
    if not members:
        raise HTTPException(404, f"no constituents for {month}")
    year, mm = (int(x) for x in month.split("-"))
    return {
        "name": name,
        "title": config.index.title,
        "month": month,
        "effective_date": first_trading_day(year, mm).isoformat(),
        "size": len(members),
        "method": store.universe_month_method(month),
        "covered": len(members)
        if config.universe.source == "static"
        else min(len(members), config.universe.size),
        "items": public([m.__dict__ for m in members]),
    }


@router.get("/index/{name}/months")
def months(name: str, store: Store = Depends(get_store), config: Config = Depends(get_config)):
    _check(name, config)
    return {"name": name, "months": store.universe_months()}


@router.get("/index/{name}/changes")
def changes(
    name: str,
    store: Store = Depends(get_store),
    config: Config = Depends(get_config),
    month: str | None = None,
):
    """Companies added to and removed from the index in a month (default: the latest)."""
    _check(name, config)
    month = _month(store, None, month)
    rows = store.universe_changes(month)
    return {
        "name": name,
        "month": month,
        "added": [dict(r) for r in rows if r["action"] == "added"],
        "removed": [dict(r) for r in rows if r["action"] == "removed"],
    }


@router.get("/index/{name}/history/{key}")
def history(
    name: str, key: str, store: Store = Depends(get_store), config: Config = Depends(get_config)
):
    """A company's rank, weight and cap in every month it was a constituent. ``key`` is a
    ticker or a CIK."""
    _check(name, config)
    rows = store.ticker_history(key.upper())
    if not rows:
        raise HTTPException(404, f"{key} has never been in {name}")
    return {"name": name, "key": key.upper(), "items": public([dict(r) for r in rows])}
