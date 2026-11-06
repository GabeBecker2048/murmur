"""Yahoo Finance per-ticker news through yfinance."""

from __future__ import annotations

import logging
from datetime import datetime

from murmur.clock import to_utc_iso
from murmur.models import Article
from murmur.sources.base import yahoo_symbol

log = logging.getLogger(__name__)

SOURCE = "yahoo"


def _parse_timestamp(text: str | None) -> str | None:
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        log.warning("unparseable timestamp %r", text)
        return None
    if dt.tzinfo is None:
        return None
    return to_utc_iso(dt)


def parse_news_item(item: dict) -> Article | None:
    """One element of ``Ticker.get_news()`` (yfinance >= 0.2.50 shape) -> Article.

    Returns None for items without a title or any URL.
    """
    content = item.get("content") or {}
    title = (content.get("title") or "").strip()
    url = None
    for key in ("canonicalUrl", "clickThroughUrl"):
        value = content.get(key)
        if isinstance(value, dict) and value.get("url"):
            url = value["url"]
            break
    if not title or not url:
        log.warning("skipping news item without title/url: %s", item.get("id"))
        return None
    provider = content.get("provider") or {}
    return Article(
        url=url,
        title=title,
        published_at=_parse_timestamp(content.get("pubDate") or content.get("displayTime")),
        source=SOURCE,
        provider=provider.get("displayName") if isinstance(provider, dict) else None,
        source_id=content.get("id") or item.get("id"),
        content_type=content.get("contentType"),
    )


class YahooNews:
    name = SOURCE
    supports_range = False

    def __init__(self, max_articles: int = 20):
        self.max_articles = max_articles

    def fetch_range(self, ticker: str, start, end) -> list[Article]:
        raise NotImplementedError("Yahoo's feed has no date range; use finnhub or gdelt")

    def fetch(self, ticker: str) -> list[Article]:
        import yfinance as yf

        items = yf.Ticker(yahoo_symbol(ticker)).get_news(count=self.max_articles)
        articles = [a for a in (parse_news_item(i) for i in items) if a is not None]
        log.debug("%s: %d news items, %d usable", ticker, len(items), len(articles))
        return articles
