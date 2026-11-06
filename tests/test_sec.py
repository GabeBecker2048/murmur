import pytest

from murmur.sources.sec import fetch_company_tickers, parse_company_tickers, user_agent
from tests.conftest import load_fixture


def test_parse_real_payload():
    companies = parse_company_tickers(load_fixture("sec_company_tickers.json"))
    assert companies["AAPL"].cik == 320193
    assert companies["AAPL"].name == "Apple Inc."
    assert companies["BRK-B"].cik == companies["BRK-B"].cik and "-" in "BRK-B"
    assert companies["GOOG"].cik == companies["GOOGL"].cik  # one company, two classes
    assert "TSM" in companies  # foreign registrants are in the list too


def test_user_agent_and_contact_guard():
    assert user_agent("a@b.c").startswith("Murmur/") and "(a@b.c)" in user_agent("a@b.c")
    with pytest.raises(ValueError):
        fetch_company_tickers("")
    with pytest.raises(ValueError):
        fetch_company_tickers("no-email-here")


def test_frame_for_waits_out_the_filing_lag():
    from datetime import date

    from murmur.sources.sec import frame_for

    assert frame_for(date(2026, 3, 2)) == "CY2025Q4I"  # Jan 15 cutoff -> Q4 2025 on file
    assert frame_for(date(2026, 2, 2)) == "CY2025Q3I"  # Dec 19 cutoff -> Q3 2025
    assert frame_for(date(2026, 11, 2)) == "CY2026Q2I"  # Sep 18 cutoff -> Q2 2026
    assert frame_for(date(2026, 1, 5)) == "CY2025Q3I"


def test_parse_shares_frame():
    from murmur.sources.sec import parse_shares_frame

    payload = {
        "data": [
            {"cik": 320193, "val": 14776000000, "end": "2026-07-18"},
            {"cik": 789019, "val": "7430000000"},
            {"cik": 1, "val": 0},
            {"cik": "x", "val": 5},
        ]
    }
    assert parse_shares_frame(payload) == {320193: 14776000000.0, 789019: 7430000000.0}
