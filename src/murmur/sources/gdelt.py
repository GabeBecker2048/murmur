"""GDELT DOC 2.0 API (``mode=ArtList``): global news by company name and date
range, no key, back to 2015. GDELT asks for at most one request every five
seconds per IP, so a full universe takes about nine minutes; it is off by
default and meant for backfills and comparison."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from murmur.clock import to_utc_iso
from murmur.models import Article
from murmur.sources.base import RateLimiter, get_json

log = logging.getLogger(__name__)

SOURCE = "gdelt"
URL = "https://api.gdeltproject.org/api/v2/doc/doc"
MAX_RECORDS = 250
RECENT_DAYS = 2


def _stamp(text: str | None) -> str | None:
    if not text:
        return None
    try:
        return to_utc_iso(datetime.strptime(text, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC))
    except ValueError:
        return None


def parse_payload(payload: dict) -> list[Article]:
    out = []
    for item in payload.get("articles", []) or []:
        title = (item.get("title") or "").strip()
        url = item.get("url")
        if not title or not url:
            continue
        out.append(
            Article(
                url=url,
                title=title,
                published_at=_stamp(item.get("seendate")),
                source=SOURCE,
                provider=item.get("domain") or None,
                source_id=None,
                content_type=None,
            )
        )
    return out


def company_query(name: str) -> str:
    """A phrase query for the company; the legal suffix only adds noise."""
    for suffix in (
        ", Inc.",
        " Inc.",
        " Inc",
        " Corporation",
        " Corp.",
        " Corp",
        " Company",
        " Co.",
    ):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return f'"{name.strip()}" sourcelang:english'


class GdeltNews:
    name = SOURCE
    supports_range = True

    def __init__(self, names: dict[str, str], limiter: RateLimiter | None = None, today=None):
        """``names`` maps ticker -> company name (the universe's names)."""
        self.names = names
        self.limiter = limiter or RateLimiter(calls=1, period=5.5)
        self._today = today or (lambda: datetime.now(UTC).date())

    def fetch(self, ticker: str) -> list[Article]:
        end = self._today()
        return self.fetch_range(ticker, end - timedelta(days=RECENT_DAYS), end)

    def fetch_range(self, ticker: str, start: date, end: date) -> list[Article]:
        name = self.names.get(ticker)
        if not name:
            log.warning("gdelt: no company name for %s; skipped", ticker)
            return []
        self.limiter.wait()
        payload = get_json(
            URL,
            {
                "query": company_query(name),
                "mode": "ArtList",
                "format": "json",
                "maxrecords": MAX_RECORDS,
                "sort": "DateDesc",
                "startdatetime": f"{start:%Y%m%d}000000",
                "enddatetime": f"{end:%Y%m%d}235959",
            },
        )
        articles = parse_payload(payload if isinstance(payload, dict) else {})
        log.debug("gdelt %s %s..%s: %d articles", ticker, start, end, len(articles))
        return articles
