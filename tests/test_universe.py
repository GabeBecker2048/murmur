from datetime import date

from murmur.config import load_config
from murmur.universe import record_universe


def test_static_universe_is_recorded_per_day(store, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = load_config(env={})
    members = record_universe(store, cfg, date(2026, 11, 5))
    assert len(members) == 50
    assert [m.ticker for m in members[:3]] == ["AAPL", "ABBV", "ABT"]
    assert len(store.universe_for("2026-11-05")) == 50
    assert store.universe_for("2026-11-06") == []
    record_universe(store, cfg, date(2026, 11, 5))  # idempotent
    assert store.count("universe_membership") == 50
