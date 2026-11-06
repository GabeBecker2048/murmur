from datetime import date

import pytest

from murmur.clock import (
    DAY,
    INTRADAY,
    OVERNIGHT,
    assign_window,
    next_session,
    parse_utc,
    previous_session,
    session_bounds,
    to_utc_iso,
    window_range,
)

TZ = "America/New_York"


def test_round_trip():
    assert to_utc_iso(parse_utc("2026-11-05T23:26:23Z")) == "2026-11-05T23:26:23Z"


@pytest.mark.parametrize(
    ("published_at", "expected"),
    [
        ("2026-11-05T14:00:00Z", (date(2026, 11, 5), OVERNIGHT)),  # 09:00 ET Thu, before open
        ("2026-11-05T14:30:00Z", (date(2026, 11, 5), INTRADAY)),  # the open itself
        ("2026-11-05T15:00:00Z", (date(2026, 11, 5), INTRADAY)),  # 10:00 ET
        ("2026-11-05T20:59:59Z", (date(2026, 11, 5), INTRADAY)),  # 15:59:59 ET
        ("2026-11-05T21:00:00Z", (date(2026, 11, 6), OVERNIGHT)),  # the close -> next session
        ("2026-11-06T03:00:00Z", (date(2026, 11, 6), OVERNIGHT)),  # 22:00 ET Thu
        ("2026-11-06T22:00:00Z", (date(2026, 11, 9), OVERNIGHT)),  # Fri after close -> Mon
        ("2026-11-07T15:00:00Z", (date(2026, 11, 9), OVERNIGHT)),  # Saturday -> Monday
        ("2026-11-26T15:00:00Z", (date(2026, 11, 27), OVERNIGHT)),  # Thanksgiving -> Friday
        ("2026-11-27T17:00:00Z", (date(2026, 11, 27), INTRADAY)),  # 12:00 ET, early-close day
        ("2026-11-27T18:30:00Z", (date(2026, 11, 30), OVERNIGHT)),  # 13:30 ET, after 13:00 close
        ("2026-07-02T13:00:00Z", (date(2026, 7, 2), OVERNIGHT)),  # 09:00 EDT (DST)
        ("2026-07-02T13:30:00Z", (date(2026, 7, 2), INTRADAY)),  # 09:30 EDT
        ("2027-01-01T12:00:00Z", (date(2027, 1, 4), OVERNIGHT)),  # New Year's Day (Fri) -> Mon
    ],
)
def test_assign_window(published_at, expected):
    assert assign_window(published_at, TZ) == expected


def test_window_ranges():
    assert window_range(date(2026, 11, 5), OVERNIGHT) == (
        "2026-11-04T21:00:00Z",
        "2026-11-05T14:30:00Z",
    )
    assert window_range(date(2026, 11, 5), INTRADAY) == (
        "2026-11-05T14:30:00Z",
        "2026-11-05T21:00:00Z",
    )
    assert window_range(date(2026, 11, 5), DAY) == ("2026-11-04T21:00:00Z", "2026-11-05T21:00:00Z")
    # Monday's overnight window spans the whole weekend
    assert window_range(date(2026, 11, 9), OVERNIGHT) == (
        "2026-11-06T21:00:00Z",
        "2026-11-09T14:30:00Z",
    )
    # early close
    assert window_range(date(2026, 11, 27), INTRADAY)[1] == "2026-11-27T18:00:00Z"
    with pytest.raises(ValueError):
        window_range(date(2026, 11, 7), OVERNIGHT)


def test_sessions():
    assert previous_session(date(2026, 11, 9)) == date(2026, 11, 6)
    assert previous_session(date(2026, 11, 8)) == date(2026, 11, 6)  # from a Sunday
    assert next_session(date(2026, 11, 6)) == date(2026, 11, 9)
    assert next_session(date(2026, 11, 7)) == date(2026, 11, 9)
    open_at, close_at = session_bounds(date(2026, 11, 5))
    assert to_utc_iso(open_at) == "2026-11-05T14:30:00Z"
    assert to_utc_iso(close_at) == "2026-11-05T21:00:00Z"
