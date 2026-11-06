from datetime import date

from murmur.clock import parse_utc, to_utc_iso, window_bounds


def test_window_bounds_in_eastern_time():
    # EST (UTC-5) on 2026-11-05
    assert window_bounds(date(2026, 11, 5), "America/New_York") == (
        "2026-11-05T05:00:00Z",
        "2026-11-06T05:00:00Z",
    )
    # EDT (UTC-4) in July
    assert window_bounds(date(2026, 7, 1), "America/New_York") == (
        "2026-07-01T04:00:00Z",
        "2026-07-02T04:00:00Z",
    )


def test_round_trip():
    assert to_utc_iso(parse_utc("2026-11-05T23:26:23Z")) == "2026-11-05T23:26:23Z"
