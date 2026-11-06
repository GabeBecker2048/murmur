"""Plain data records passed between sources, the store and the analysis."""

from __future__ import annotations

import re
from dataclasses import dataclass

_NON_WORD = re.compile(r"[^a-z0-9]+")


def normalize_title(title: str) -> str:
    """Lowercase, strip punctuation and collapse whitespace (for cross-source dedup)."""
    return _NON_WORD.sub(" ", title.lower()).strip()


@dataclass(frozen=True)
class Article:
    url: str
    title: str
    published_at: str | None
    """UTC, exactly ``YYYY-MM-DDTHH:MM:SSZ`` so text comparison orders correctly."""
    source: str
    provider: str | None = None
    source_id: str | None = None
    content_type: str | None = None


@dataclass(frozen=True)
class PriceBar:
    ticker: str
    date: str
    """Calendar date of the bar in the exchange's timezone, ``YYYY-MM-DD``."""
    open: float | None
    high: float | None
    low: float | None
    close: float
    adj_close: float | None
    volume: int | None


@dataclass(frozen=True)
class UniverseMember:
    ticker: str
    name: str
    market_cap: float | None = None
    rank: int | None = None
    cik: int | None = None


@dataclass(frozen=True)
class SentimentScores:
    positive: float
    negative: float
    neutral: float
    label: str

    @property
    def score(self) -> float:
        return self.positive - self.negative
