"""Dates, windows and the NYSE calendar. ``date`` values are calendar dates in
the configured exchange timezone; article timestamps are UTC strings."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
CALENDAR = "XNYS"


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


def month_key(day: date) -> str:
    return f"{day:%Y-%m}"


def window_bounds(day: date, tz: str) -> tuple[str, str]:
    """UTC bounds ``[start, end)`` of the local calendar day.

    0.3.0 replaces this with overnight/intraday windows; nothing else should
    turn dates into timestamps.
    """
    zone = ZoneInfo(tz)
    start = datetime(day.year, day.month, day.day, tzinfo=zone)
    end = start + timedelta(days=1)
    return to_utc_iso(start), to_utc_iso(end)


@lru_cache(maxsize=1)
def _calendar():
    import exchange_calendars

    return exchange_calendars.get_calendar(CALENDAR)


def is_trading_day(day: date) -> bool:
    return bool(_calendar().is_session(day.isoformat()))


def first_trading_day(year: int, month: int) -> date:
    start = date(year, month, 1)
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    sessions = _calendar().sessions_in_range(start.isoformat(), end.isoformat())
    if len(sessions) == 0:
        raise ValueError(f"no trading days in {year}-{month:02d}")
    return sessions[0].date()


def previous_month(day: date) -> date:
    return (day.replace(day=1) - timedelta(days=1)).replace(day=1)
