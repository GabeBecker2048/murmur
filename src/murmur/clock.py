"""Dates and windows. ``date`` values are calendar dates in the configured
exchange timezone; article timestamps are UTC strings."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def utc_now_iso() -> str:
    return datetime.now(UTC).strftime(UTC_FORMAT)


def to_utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("naive datetime")
    return dt.astimezone(UTC).strftime(UTC_FORMAT)


def parse_utc(text: str) -> datetime:
    return datetime.strptime(text, UTC_FORMAT).replace(tzinfo=UTC)


def today_in(tz: str) -> date:
    return datetime.now(ZoneInfo(tz)).date()


def parse_date(text: str) -> date:
    return date.fromisoformat(text)


def window_bounds(day: date, tz: str) -> tuple[str, str]:
    """UTC bounds ``[start, end)`` of the local calendar day.

    0.3.0 replaces this with overnight/intraday windows; nothing else should
    turn dates into timestamps.
    """
    zone = ZoneInfo(tz)
    start = datetime(day.year, day.month, day.day, tzinfo=zone)
    end = start + timedelta(days=1)
    return to_utc_iso(start), to_utc_iso(end)
