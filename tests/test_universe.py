from datetime import date

import pytest

from murmur.config import load_config
from murmur.models import UniverseMember
from murmur.sources.sec import SecCompany
from murmur.sources.yahoo_screener import Candidate
from murmur.universe import (
    MarketCapUniverse,
    StaticUniverse,
    applicable_month,
    membership_for,
    record_universe,
)


@pytest.fixture
def static_cfg(tmp_path):
    user = tmp_path / "c.toml"
    user.write_text('[universe]\nsource = "static"\n')
    return load_config(user, env={})


def test_static_universe_is_recorded_per_day(store, static_cfg):
    members = record_universe(store, static_cfg, date(2026, 11, 5))
    assert len(members) == 50
    assert [m.ticker for m in members[:3]] == ["AAPL", "ABBV", "ABT"]
    assert members[0].rank == 1
    assert len(store.universe_for("2026-11-05")) == 50
    assert store.universe_for("2026-11-06") == []
    record_universe(store, static_cfg, date(2026, 11, 5))  # idempotent
    assert store.count("universe_membership") == 50
    assert store.universe_months() == ["2026-11"]


def test_applicable_month_uses_first_trading_day():
    # Nov 2026: the 1st is a Sunday, first session is Mon the 2nd
    assert applicable_month(date(2026, 11, 1)) == "2026-10"
    assert applicable_month(date(2026, 11, 2)) == "2026-11"
    assert applicable_month(date(2026, 11, 30)) == "2026-11"
    # Jan 2027: Jan 1 is a holiday (Friday); first session Mon Jan 4
    assert applicable_month(date(2027, 1, 1)) == "2026-12"
    assert applicable_month(date(2027, 1, 3)) == "2026-12"
    assert applicable_month(date(2027, 1, 4)) == "2027-01"


SEC = {
    "AAPL": SecCompany(320193, "AAPL", "Apple Inc."),
    "GOOGL": SecCompany(1652044, "GOOGL", "Alphabet Inc."),
    "GOOG": SecCompany(1652044, "GOOG", "Alphabet Inc."),
    "TSM": SecCompany(1046179, "TSM", "TAIWAN SEMICONDUCTOR"),
    "BRK-B": SecCompany(1067983, "BRK-B", "BERKSHIRE HATHAWAY INC"),
    "JPM": SecCompany(19617, "JPM", "JPMORGAN CHASE & CO"),
}

CANDIDATES = [
    Candidate("AAPL", "Apple Inc.", 5.0e12, "NMS", 1, price=250.0),
    Candidate("GOOGL", "Alphabet Inc.", 3.5e12, "NMS", 1, price=250.0),
    Candidate("GOOG", "Alphabet Inc.", 3.4e12, "NMS", 1, price=250.0),  # same CIK as GOOGL
    Candidate("TSM", "Taiwan Semiconductor", 1.8e12, "NYQ", 1, price=300.0),  # foreign
    Candidate("SPCX", "Space Exploration", 1.6e12, "NMS", 1, price=100.0),  # not in SEC list
    Candidate("BRK-B", "Berkshire Hathaway Inc.", 1.2e12, "NYQ", 1, price=500.0),
    Candidate("JPM", "JPMorgan Chase", 0.8e12, "NYQ", 1, price=320.0),
]

COUNTRIES = {
    "AAPL": "United States",
    "GOOGL": "United States",
    "TSM": "Taiwan",
    "BRK-B": "United States",
    "JPM": "United States",
}


class FakeScreener:
    def __init__(self):
        self.country_calls = []

    def candidates(self):
        return list(CANDIDATES)

    def country(self, symbol):
        self.country_calls.append(symbol)
        return COUNTRIES[symbol]


def test_market_cap_universe_selection(store):
    screener = FakeScreener()
    src = MarketCapUniverse(
        store, size=3, sec_contact="x@y.z", screener=screener, sec_fetch=lambda c: SEC
    )
    members = src.build("2026-11")
    assert [(m.rank, m.ticker, m.cik) for m in members] == [
        (1, "AAPL", 320193),
        (2, "GOOGL", 1652044),
        (3, "BRK-B", 1067983),
    ]
    assert members[0].name == "Apple Inc." and members[0].market_cap == 5.0e12
    assert members[0].exchange == "NMS" and members[2].exchange == "NYQ"
    total = 5.0e12 + 3.5e12 + 1.2e12
    assert [round(m.weight, 6) for m in members] == [
        round(5.0e12 / total, 6),
        round(3.5e12 / total, 6),
        round(1.2e12 / total, 6),
    ]
    assert abs(sum(m.weight for m in members) - 1) < 1e-9
    # GOOG skipped as a duplicate CIK without a country lookup; SPCX not SEC-registered
    assert screener.country_calls == ["AAPL", "GOOGL", "TSM", "BRK-B"]

    # the country cache means a second build does no lookups
    screener.country_calls.clear()
    src.build("2026-12")
    assert screener.country_calls == []
    assert store.count("companies") == 4


def test_market_cap_universe_too_small_is_an_error(store):
    src = MarketCapUniverse(
        store, size=20, sec_contact="x@y.z", screener=FakeScreener(), sec_fetch=lambda c: SEC
    )
    with pytest.raises(RuntimeError, match="only 4 of 20"):
        src.build("2026-11")


def test_membership_for_builds_once_per_month_and_records_days(store, tmp_path):
    user = tmp_path / "c.toml"
    user.write_text('[universe]\nsource = "marketcap"\nsize = 2\n')
    cfg = load_config(user, env={})
    calls = []

    class Src:
        name = "fake"

        def build(self, month):
            calls.append(month)
            return [
                UniverseMember("AAPL", "Apple", 1.0, 1, 1),
                UniverseMember("JPM", "JPM", 0.5, 2, 2),
            ]

    members, rebuilt = membership_for(store, cfg, date(2026, 11, 5), Src())
    assert rebuilt and [m.ticker for m in members] == ["AAPL", "JPM"]
    members, rebuilt = membership_for(store, cfg, date(2026, 11, 6), Src())
    assert not rebuilt
    assert calls == ["2026-11"]
    # Nov 1 is before the first trading day, so it needs October's universe
    members, rebuilt = membership_for(store, cfg, date(2026, 11, 1), Src())
    assert rebuilt and calls == ["2026-11", "2026-10"]
    assert store.universe_months() == ["2026-10", "2026-11"]
    assert len(store.universe_for("2026-11-06")) == 2
    assert store.universe_for("2026-11-06")[0].cik == 1
    # rebuild on demand
    membership_for(store, cfg, date(2026, 11, 6), Src(), rebuild=True)
    assert calls[-1] == "2026-11"


def test_coverage_is_the_top_of_the_index(store, tmp_path):
    user = tmp_path / "c.toml"
    user.write_text('[index]\nsize = 3\n[universe]\nsource = "marketcap"\nsize = 2\n')
    cfg = load_config(user, env={})

    class Src:
        name = "fake"

        def build(self, month):
            return [
                UniverseMember("AAPL", "Apple", 3.0, 1, 1, weight=0.5),
                UniverseMember("MSFT", "Microsoft", 2.0, 2, 2, weight=0.33),
                UniverseMember("JPM", "JPM", 1.0, 3, 3, weight=0.17),
            ]

    members, _ = membership_for(store, cfg, date(2026, 11, 5), Src())
    assert [m.ticker for m in members] == ["AAPL", "MSFT"]  # coverage
    assert [m.ticker for m in store.universe_month("2026-11")] == ["AAPL", "MSFT", "JPM"]  # index
    assert [m.ticker for m in store.universe_for("2026-11-05")] == ["AAPL", "MSFT"]


def test_coverage_cannot_exceed_the_index(tmp_path):
    from murmur.config import ConfigError

    user = tmp_path / "c.toml"
    user.write_text('[index]\nsize = 50\n[universe]\nsource = "marketcap"\nsize = 100\n')
    with pytest.raises(ConfigError, match="cannot exceed"):
        load_config(user, env={})


def test_static_source_names():
    members = StaticUniverse({"MSFT": "Microsoft", "AAPL": "Apple"}).build("2026-11")
    assert [(m.rank, m.ticker) for m in members] == [(1, "AAPL"), (2, "MSFT")]


def test_historical_reconstruction_reranks_from_shares_and_closes(store):
    from datetime import date

    from murmur.universe import RECONSTRUCTED, historical_shares

    screener = FakeScreener()
    # a month ago Berkshire traded far above today's price and Alphabet far below:
    closes = {
        "AAPL": 200.0,
        "GOOGL": 100.0,
        "GOOG": 100.0,
        "TSM": 150.0,
        "SPCX": 50.0,
        "BRK-B": 600.0,
        "JPM": 250.0,
    }
    # SEC frame knows Apple and JPM; Alphabet reports per class (absent); Berkshire's count
    # is a single class far below today's implied count -> today's count is used instead
    shares = {320193: 15e9, 19617: 2.8e9, 1067983: 1.0e6}
    calls = []

    def shares_fetch(contact, day):
        calls.append(day)
        return shares

    src = MarketCapUniverse(
        store,
        size=3,
        sec_contact="x@y.z",
        screener=screener,
        sec_fetch=lambda c: SEC,
        shares_fetch=shares_fetch,
        closes_fetch=lambda symbols, day: closes,
        today=lambda: date(2026, 11, 6),
    )
    assert src.method("2026-11") == "live" and src.method("2026-10") == RECONSTRUCTED
    members = src.build("2026-10")
    assert calls == [date(2026, 10, 1)]  # the month's first trading day
    caps = {m.ticker: m.market_cap for m in members}
    # AAPL: SEC 15e9 x 200 = 3.0e12; BRK-B: implied 2.4e9 x 600 = 1.44e12 (SEC count rejected
    # as a single class); GOOGL: implied 1.4e10 x 100 = 1.4e12; JPM: SEC 2.8e9 x 250 = 7e11
    assert [(m.rank, m.ticker) for m in members] == [(1, "AAPL"), (2, "BRK-B"), (3, "GOOGL")]
    assert caps["AAPL"] == 15e9 * 200.0 and round(caps["BRK-B"]) == round(2.4e9 * 600.0)
    assert abs(sum(m.weight for m in members) - 1) < 1e-9

    c = Candidate("X", "X", market_cap=1.0e12, exchange="NMS", volume=1, price=100.0)
    assert historical_shares(c, 9.9e9) == 9.9e9  # plausible SEC figure wins
    assert historical_shares(c, 1.0e6) == 1.0e10  # single class of a multi-class filer: implied
    assert historical_shares(c, None) == 1.0e10


def test_membership_for_a_past_month_is_marked_reconstructed(store, tmp_path):
    from datetime import date

    user = tmp_path / "c.toml"
    user.write_text('[universe]\nsource = "marketcap"\nsize = 2\n')
    cfg = load_config(user, env={})

    class Src:
        name = "fake"

        def build(self, month):
            return [
                UniverseMember("AAPL", "Apple", 1.0, 1, 1),
                UniverseMember("JPM", "JPM", 0.5, 2, 2),
            ]

        def method(self, month):
            return "reconstructed" if month < "2026-11" else "live"

    membership_for(store, cfg, date(2026, 10, 15), Src())
    membership_for(store, cfg, date(2026, 11, 5), Src())
    assert store.universe_month_method("2026-10") == "reconstructed"
    assert store.universe_month_method("2026-11") == "live"
    assert store.universe_month_method("2026-09") is None
