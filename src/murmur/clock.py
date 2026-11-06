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


OVERNIGHT = "overnight"
"""Previous session's close to this session's open; weekend/holiday news included."""
INTRADAY = "intraday"
"""This session's open to its close."""
DAY = "day"
"""Overnight + intraday: previous close to this close. Matches the daily return."""
WINDOWS = (OVERNIGHT, INTRADAY)


@lru_cache(maxsize=1)
def _calendar():
    """The NYSE calendar, built once per process with a horizon long enough
    that a scheduler process running for years never falls off its end."""
    import exchange_calendars

    today = date.today()
    return exchange_calendars.get_calendar(
        CALENDAR,
        start=today.replace(year=today.year - 5).isoformat(),
        end=today.replace(year=today.year + 3).isoformat(),
    )


def is_trading_day(day: date) -> bool:
    return bool(_calendar().is_session(day.isoformat()))


def previous_session(day: date) -> date:
    """The trading day before ``day`` (``day`` itself need not be one)."""
    cal = _calendar()
    if cal.is_session(day.isoformat()):
        return cal.previous_session(day.isoformat()).date()
    return cal.date_to_session(day.isoformat(), direction="previous").date()


def next_session(day: date) -> date:
    """The trading day after ``day`` (``day`` itself need not be one)."""
    cal = _calendar()
    if cal.is_session(day.isoformat()):
        return cal.next_session(day.isoformat()).date()
    return cal.date_to_session(day.isoformat(), direction="next").date()


def session_bounds(day: date) -> tuple[datetime, datetime]:
    """UTC open and close of a trading day (early closes included)."""
    cal = _calendar()
    iso = day.isoformat()
    if not cal.is_session(iso):
        raise ValueError(f"{iso} is not a trading day")
    return cal.session_open(iso).to_pydatetime(), cal.session_close(iso).to_pydatetime()


def assign_window(published_at: str, tz: str) -> tuple[date, str]:
    """Which trading day and news window an article belongs to.

    Before the open: that day's overnight window. Between open and close:
    intraday. After the close, or on a weekend/holiday: the next trading
    day's overnight window.
    """
    ts = parse_utc(published_at)
    local_day = ts.astimezone(ZoneInfo(tz)).date()
    if is_trading_day(local_day):
        open_at, close_at = session_bounds(local_day)
        if ts < open_at:
            return local_day, OVERNIGHT
        if ts < close_at:
            return local_day, INTRADAY
        return next_session(local_day), OVERNIGHT
    return next_session(local_day), OVERNIGHT


def window_range(day: date, window: str) -> tuple[str, str]:
    """UTC ``[start, end)`` of a window of trading day ``day``."""
    open_at, close_at = session_bounds(day)
    _, prev_close = session_bounds(previous_session(day))
    if window == OVERNIGHT:
        return to_utc_iso(prev_close), to_utc_iso(open_at)
    if window == INTRADAY:
        return to_utc_iso(open_at), to_utc_iso(close_at)
    if window == DAY:
        return to_utc_iso(prev_close), to_utc_iso(close_at)
    raise ValueError(window)


def first_trading_day(year: int, month: int) -> date:
    start = date(year, month, 1)
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    sessions = _calendar().sessions_in_range(start.isoformat(), end.isoformat())
    if len(sessions) == 0:
        raise ValueError(f"no trading days in {year}-{month:02d}")
    return sessions[0].date()


def previous_month(day: date) -> date:
    return (day.replace(day=1) - timedelta(days=1)).replace(day=1)
