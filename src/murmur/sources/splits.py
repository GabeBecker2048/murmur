"""Stock split ratios, so returns across a split day are right when the price
source is unadjusted (Polygon). Polygon's reference endpoint lists
every split in the market for a date range; the free plan covers it."""

from __future__ import annotations

import logging
from datetime import date
from typing import Protocol

from murmur.models import Split
from murmur.sources.base import RateLimiter, get_json

log = logging.getLogger(__name__)

POLYGON_URL = "https://api.polygon.io/v3/reference/splits"


class SplitsSource(Protocol):
    name: str

    def fetch_range(self, start: date, end: date) -> list[Split]:
        """Splits executing in ``[start, end]`` (inclusive), any ticker."""
        ...


def parse_polygon(payload: dict) -> list[Split]:
    out = []
    for r in payload.get("results", []) or []:
        try:
            frm, to = float(r["split_from"]), float(r["split_to"])
        except (KeyError, TypeError, ValueError):
            continue
        if frm <= 0 or to <= 0 or not r.get("execution_date") or not r.get("ticker"):
            continue
        out.append(
            Split(
                ticker=str(r["ticker"]).upper().replace(".", "-"),
                execution_date=str(r["execution_date"])[:10],
                ratio=to / frm,
            )
        )
    return out


class PolygonSplits:
    name = "polygon"

    def __init__(self, api_key: str, limiter: RateLimiter | None = None, get=get_json):
        if not api_key:
            raise ValueError("polygon splits need an API key (MURMUR_POLYGON_API_KEY)")
        self.api_key = api_key
        self.limiter = limiter or RateLimiter(calls=5, period=60)
        self._get = get

    def fetch_range(self, start: date, end: date) -> list[Split]:
        params = {
            "execution_date.gte": start.isoformat(),
            "execution_date.lte": end.isoformat(),
            "limit": 1000,
            "apiKey": self.api_key,
        }
        out: list[Split] = []
        url: str | None = POLYGON_URL
        while url:
            self.limiter.wait()
            payload = self._get(url, params)
            params = {"apiKey": self.api_key}  # next_url carries the cursor
            out += parse_polygon(payload if isinstance(payload, dict) else {})
            url = payload.get("next_url") if isinstance(payload, dict) else None
        log.info("polygon splits %s..%s: %d", start, end, len(out))
        return out


class NoSplits:
    name = "none"

    def fetch_range(self, start: date, end: date) -> list[Split]:
        return []
