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
