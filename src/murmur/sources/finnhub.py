"""Finnhub company news (``/company-news``): per ticker, by date range. Free
tier: 60 calls a minute, about a year of history. Needs an API key."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from murmur.clock import to_utc_iso
from murmur.models import Article
from murmur.sources.base import RateLimiter, get_json, yahoo_symbol

log = logging.getLogger(__name__)

SOURCE = "finnhub"
URL = "https://finnhub.io/api/v1/company-news"
RECENT_DAYS = 3


def parse_items(items: list[dict]) -> list[Article]:
    out = []
    for item in items:
        title = (item.get("headline") or "").strip()
        url = item.get("url")
        if not title or not url:
            continue
        stamp = item.get("datetime")
        published = to_utc_iso(datetime.fromtimestamp(int(stamp), tz=UTC)) if stamp else None
        out.append(
            Article(
                url=url,
                title=title,
                published_at=published,
                source=SOURCE,
                provider=item.get("source") or None,
                source_id=str(item["id"]) if item.get("id") is not None else None,
                content_type=item.get("category") or None,
            )
        )
    return out


class FinnhubNews:
    name = SOURCE
    supports_range = True

    def __init__(self, api_key: str, limiter: RateLimiter | None = None, today=None):
        if not api_key:
            raise ValueError(
                "finnhub needs an API key (MURMUR_FINNHUB_API_KEY or [finnhub] api_key)"
            )
        self.api_key = api_key
        self.limiter = limiter or RateLimiter(calls=55, period=60)
        self._today = today or (lambda: datetime.now(UTC).date())

    def fetch(self, ticker: str) -> list[Article]:
        end = self._today()
        return self.fetch_range(ticker, end - timedelta(days=RECENT_DAYS), end)

    def fetch_range(self, ticker: str, start: date, end: date) -> list[Article]:
        self.limiter.wait()
        payload = get_json(
            URL,
            {
                "symbol": yahoo_symbol(ticker),
                "from": start.isoformat(),
                "to": end.isoformat(),
                "token": self.api_key,
            },
        )
        articles = parse_items(payload if isinstance(payload, list) else [])
        log.debug("finnhub %s %s..%s: %d articles", ticker, start, end, len(articles))
        return articles
