"""The daily index level: base, chaining, drift, rebalancing without look-ahead,
missing prices, and where the history stops."""

from datetime import date

import pytest

from murmur.clock import sessions_between
from murmur.config import load_config
from murmur.index_level import compute_levels, update_levels
from murmur.models import UniverseMember
from murmur.store import Store

RET = {"A": 0.01, "B": -0.01, "C": 0.02}


@pytest.fixture
def cfg(tmp_path):
    user = tmp_path / "c.toml"
    user.write_text(
        f'data_dir = "{tmp_path / "data"}"\n'
        '[universe]\nsource = "static"\n[universe.static]\nA = "A"\nB = "B"\nC = "C"\n'
    )
    return load_config(user, env={})


@pytest.fixture
def store():
    with Store(":memory:") as s:
        s.save_universe_month(
            "2026-09",
            [UniverseMember("A", "A", 6e11, 1, 1, 0.6), UniverseMember("B", "B", 4e11, 2, 2, 0.4)],
            "t",
        )
        s.save_universe_month(
            "2026-10",
            [
                UniverseMember("A", "A", 5e11, 1, 1, 0.5),
                UniverseMember("B", "B", 3e11, 2, 2, 0.3),
                UniverseMember("C", "C", 2e11, 3, 3, 0.2),
            ],
            "t",
        )
        days = sessions_between(date(2026, 9, 1), date(2026, 10, 5))
        rows = []
        for d in days[1:]:
            for t, r in RET.items():
                if t == "B" and d == date(2026, 9, 4):
                    continue  # no bar for B that day
                rows.append((t, d.isoformat(), "p", 100.0, 100.0, 100.0, 0.0, 0.0, r))
        s.upsert_returns(rows)
        yield s


def test_level_chains_drifts_and_rebalances(store, cfg):
    days = {d.date: d for d in compute_levels(store, cfg, date(2026, 10, 5))}
    base = days["2026-09-01"]  # first index month's first session
    assert base.level == 1000 and base.ret is None and base.rebalance
    assert days["2026-09-02"].ret == pytest.approx(0.6 * 0.01 + 0.4 * -0.01)
    assert days["2026-09-02"].level == pytest.approx(1002)
    # holdings drift: A grew, B shrank
    a, b = 0.6 * 1.01, 0.4 * 0.99
    assert days["2026-09-03"].ret == pytest.approx((a * 0.01 + b * -0.01) / (a + b))
    # B missing on 09-04: counts as unchanged, and the coverage says so
    d4 = days["2026-09-04"]
    assert d4.priced == 1 and d4.constituents == 2 and d4.weight_priced < 1
    # the rebalance day still uses September's holdings (C is not in them) ...
    oct1 = days["2026-10-01"]
    assert oct1.rebalance and {c[0] for c in oct1.contributions} == {"A", "B"}
    # ... and October's weights apply from the next session
    assert days["2026-10-02"].ret == pytest.approx(0.5 * 0.01 + 0.3 * -0.01 + 0.2 * 0.02)
    # contributions add up to the return, points to the change in level
    for prev, cur in zip(list(days.values()), list(days.values())[1:], strict=False):
        assert sum(c[4] or 0 for c in cur.contributions) == pytest.approx(cur.ret)
        assert sum(c[5] or 0 for c in cur.contributions) == pytest.approx(cur.level - prev.level)


def test_a_session_without_any_returns_leaves_the_level_unchanged(store, cfg):
    store.conn.execute("DELETE FROM returns WHERE date = '2026-09-10'")
    days = {d.date: d for d in compute_levels(store, cfg, date(2026, 10, 5))}
    gap = days["2026-09-10"]
    assert gap.priced == 0 and gap.ret == 0 and gap.weight_priced is None
    assert gap.level == days["2026-09-09"].level
    assert days["2026-10-05"].level != days["2026-09-09"].level  # and it carries on after
    assert max(days) == "2026-10-05"


def test_update_levels_stores_the_whole_history(store, cfg):
    n = update_levels(store, cfg)
    assert n == len(sessions_between(date(2026, 9, 1), date(2026, 10, 5)))
    assert store.latest_index_level_day() == "2026-10-05"
    contribs = store.index_contributions_on("2026-10-02")
    assert [c["ticker"] for c in contribs] == ["A", "C", "B"]  # largest contribution first
    assert store.index_level_on("2026-10-01")["rebalance"] == 1


def test_base_date_and_level_from_config(store, tmp_path):
    user = tmp_path / "b.toml"
    user.write_text(
        '[universe]\nsource = "static"\n[universe.static]\nA = "A"\n'
        '[index]\nbase_date = "2026-09-15"\nbase_level = 100\n'
    )
    days = compute_levels(store, load_config(user, env={}), date(2026, 10, 5))
    assert days[0].date == "2026-09-15" and days[0].level == 100


def test_no_index_months_means_no_level(cfg):
    with Store(":memory:") as s:
        assert compute_levels(s, cfg, date(2026, 10, 5)) == []
        assert update_levels(s, cfg) == 0
