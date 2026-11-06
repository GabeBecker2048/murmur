import json
from datetime import date

from murmur.analysis import analyze_day
from murmur.export import export_day
from murmur.universe import record_universe
from tests.test_analysis import COMPANIES, cfg, seed  # noqa: F401

DAY = date(2026, 11, 5)


def test_export_layout(store, scorer, cfg, tmp_path):  # noqa: F811
    seed(store, scorer)
    record_universe(store, cfg, DAY)
    analyze_day(store, cfg, DAY, list(COMPANIES))
    written = export_day(store, cfg, DAY)
    base = cfg.published_dir
    rel = sorted(p.relative_to(base).as_posix() for p in written)
    assert rel == [
        "analysis/daily/2026/11/05-correlation.png",
        "analysis/daily/2026/11/05-sentiment.png",
        "analysis/daily/2026/11/05-tickers.csv",
        "analysis/daily/2026/11/05.csv",
        "articles/2026/11/05.csv",
        "index.json",
        "prices/2026/11/05.csv",
        "sentiment/2026/11/05.csv",
        "universe/2026-11.csv",
    ]
    assert (base / "universe/2026-11.csv").read_text().splitlines()[0] == (
        "ticker,name,market_cap,rank"
    )
    assert (base / "prices/2026/11/05.csv").read_text().count("\n") == 5  # header + 4
    assert (base / "articles/2026/11/05.csv").read_text().count("\n") == 5

    index = json.loads((base / "index.json").read_text())
    assert index["murmur_version"] == "0.1.0"
    entries = {e["path"]: e for e in index["files"]}
    assert "index.json" not in entries
    assert entries["prices/2026/11/05.csv"] == {
        "table": "prices",
        "date": "2026-11-05",
        "path": "prices/2026/11/05.csv",
        "format": "csv",
        "rows": 4,
    }
    assert entries["analysis/daily/2026/11/05.csv"]["table"] == "analysis/daily"
    assert entries["analysis/daily/2026/11/05.csv"]["rows"] == 2
    assert entries["analysis/daily/2026/11/05-sentiment.png"]["format"] == "png"
    assert entries["universe/2026-11.csv"]["date"] == "2026-11"
    assert (base / "analysis/daily/2026/11/05-correlation.png").stat().st_size > 1000


def test_export_without_prices_skips_scatter(store, scorer, cfg):  # noqa: F811
    seed(store, scorer, with_prices=False)
    record_universe(store, cfg, DAY)
    analyze_day(store, cfg, DAY, list(COMPANIES))
    written = export_day(store, cfg, DAY)
    names = {p.name for p in written}
    assert "05-sentiment.png" in names and "05-correlation.png" not in names
    assert (cfg.published_dir / "analysis/daily/2026/11/05.csv").read_text().count("\n") == 1


def test_export_is_rerunnable(store, scorer, cfg):  # noqa: F811
    seed(store, scorer)
    record_universe(store, cfg, DAY)
    export_day(store, cfg, DAY)
    export_day(store, cfg, DAY)
    index = json.loads((cfg.published_dir / "index.json").read_text())
    assert len(index["files"]) == len({e["path"] for e in index["files"]})
