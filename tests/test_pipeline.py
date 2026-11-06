import json
from datetime import date

import pytest

from murmur import cli, pipeline
from murmur.config import load_config
from murmur.models import Article, PriceBar
from murmur.store import Store
from tests.conftest import FakeScorer
from tests.test_analysis import COMPANIES

DAY = date(2026, 11, 5)


class FakeNews:
    name = "fake-news"

    def __init__(self, fail_for=()):
        self.fail_for = set(fail_for)

    def fetch(self, ticker):
        if ticker in self.fail_for:
            raise OSError("boom")
        _, headline, _, _ = COMPANIES[ticker]
        return [
            Article(f"https://e/{ticker}/1", headline, "2026-11-05T15:00:00Z", "fake-news"),
            Article(
                "https://e/shared", "Markets fall broadly", "2026-11-05T16:00:00Z", "fake-news"
            ),
        ]


class FakePrices:
    name = "fake-prices"

    def __init__(self, empty=False):
        self.empty = empty

    def fetch(self, tickers):
        if self.empty:
            return []
        bars = []
        for t in tickers:
            _, _, prev_close, close = COMPANIES[t]
            bars.append(PriceBar(t, "2026-11-04", None, None, None, prev_close, None, None))
            bars.append(PriceBar(t, "2026-11-05", None, None, None, close, None, None))
        return bars


@pytest.fixture
def cfg(tmp_path):
    user = tmp_path / "c.toml"
    lines = [f'data_dir = "{tmp_path / "data"}"', "[sentiment]", 'model = "fake/keywords"']
    lines += ["[universe]", 'source = "static"', "[universe.static]"]
    lines += [f'{t} = "{v[0]}"' for t, v in COMPANIES.items()]
    user.write_text("\n".join(lines) + "\n")
    return load_config(user, env={})


def test_run_all_end_to_end_and_idempotent(cfg):
    with Store(cfg.store_path) as store:
        results = pipeline.run_all(store, cfg, DAY, FakeNews(), FakePrices(), FakeScorer())
        assert [r.name for r in results] == [
            "universe",
            "news",
            "prices",
            "sentiment",
            "analyze",
            "export",
        ]
        assert results[0].summary == "4 tickers for 2026-11 (rebuilt)"
        assert all(r.ok for r in results), [r.summary for r in results]
        assert store.count("articles") == 5  # 4 own + 1 shared, deduplicated by url
        assert store.count("article_tickers") == 8
        assert store.count("prices") == 8
        assert store.count("sentiment") == 5
        assert len(store.universe_for("2026-11-05")) == 4
        # the 4th had bars and no correlation, so it was backfilled (membership recorded too)
        assert len(store.universe_for("2026-11-04")) == 4
        assert store.count("correlations") == 2
        assert "pearson" in results[4].summary

        scorer = FakeScorer()
        again = pipeline.run_all(store, cfg, DAY, FakeNews(), FakePrices(), scorer)
        assert all(r.ok for r in again)
        assert scorer.calls == []  # nothing rescored
        assert again[0].summary.endswith("(cached)")
        assert again[1].summary.endswith("0 new")
        assert store.count("articles") == 5
        assert store.count("sentiment") == 5
    assert (cfg.published_dir / "index.json").exists()


def test_failed_stage_does_not_stop_the_others(cfg):
    with Store(cfg.store_path) as store:
        results = pipeline.run_all(
            store, cfg, DAY, FakeNews(), FakePrices(empty=True), FakeScorer()
        )
        by_name = {r.name: r for r in results}
        assert not by_name["prices"].ok and "no bars" in by_name["prices"].summary
        assert by_name["news"].ok and by_name["sentiment"].ok
        # a trading day with no bars is a failure too, so retries kick in
        assert not by_name["analyze"].ok and "no price bars" in by_name["analyze"].summary
        assert by_name["export"].ok


def test_run_day_records_the_run(cfg):
    with Store(cfg.store_path) as store:
        pipeline.run_day(
            store,
            cfg,
            DAY,
            "manual",
            news_source=FakeNews(),
            price_source=FakePrices(),
            scorer=FakeScorer(),
        )
        pipeline.run_day(
            store,
            cfg,
            DAY,
            "schedule",
            2,
            news_source=FakeNews(),
            price_source=FakePrices(empty=True),
            scorer=FakeScorer(),
        )
        runs = store.recent_runs()
        assert [(r["trigger"], r["attempt"], r["ok"]) for r in runs] == [
            ("schedule", 2, 0),
            ("manual", 1, 1),
        ]
        assert store.successful_run_exists("2026-11-05")
        assert not store.successful_run_exists("2026-11-06")
        stages = json.loads(runs[0]["stages"])
        assert [s["name"] for s in stages][:2] == ["universe", "news"]


def test_analyze_backfills_a_day_whose_bars_arrived_late(cfg):
    """Yahoo's daily bar can vanish in the evening; the next day's run finishes
    the analysis once bars are back."""
    nov4, nov5 = date(2026, 11, 4), date(2026, 11, 5)
    with Store(cfg.store_path) as store:
        # Nov 4: everything but the bars for the 4th itself -> analyze fails that day
        pipeline.run_universe(store, cfg, nov4)
        pipeline.run_news(store, cfg, nov4, FakeNews())
        for t, (_, _, prev_close, _) in COMPANIES.items():
            store.upsert_prices(
                [PriceBar(t, "2026-11-03", None, None, None, prev_close, None, None)], "t"
            )
        pipeline.run_sentiment(store, cfg, FakeScorer())
        with pytest.raises(RuntimeError, match="no price bars"):
            pipeline.run_analyze(store, cfg, nov4)
        assert store.correlations_on("2026-11-04") == []

        # Nov 5's run fetches a month of bars (now including the 4th) and backfills
        results = pipeline.run_all(store, cfg, nov5, FakeNews(), FakePrices(), FakeScorer())
        by_name = {r.name: r for r in results}
        assert all(r.ok for r in results), [r.summary for r in results]
        assert "2026-11-04" in by_name["analyze"].summary.split("backfilled ")[1]
        assert len(store.correlations_on("2026-11-04")) == 2
        assert len(store.correlations_on("2026-11-05")) == 2
        assert (cfg.published_dir / "analysis/daily/2026/11/04.csv").exists()
        assert (cfg.published_dir / "analysis/daily/2026/11/05.csv").exists()

        # nothing left to backfill
        assert pipeline.backfill_days(store, nov5) == []


def test_news_requires_a_recorded_universe(cfg):
    with Store(cfg.store_path) as store:
        with pytest.raises(RuntimeError, match="no universe recorded"):
            pipeline.run_news(store, cfg, DAY, FakeNews())


def test_one_failing_ticker_is_tolerated_all_failing_is_not(cfg):
    with Store(cfg.store_path) as store:
        pipeline.run_universe(store, cfg, DAY)
        summary = pipeline.run_news(store, cfg, DAY, FakeNews(fail_for={"META"}))
        assert "1 tickers failed" in summary
        assert store.count("articles") == 4
        with pytest.raises(RuntimeError):
            pipeline.run_news(store, cfg, DAY, FakeNews(fail_for=set(COMPANIES)))


def test_cli_run_and_subcommands(cfg, monkeypatch, capsys):
    monkeypatch.setattr(pipeline, "default_news_source", lambda c: FakeNews())
    monkeypatch.setattr(pipeline, "default_price_source", lambda c: FakePrices())
    monkeypatch.setattr(pipeline, "default_scorer", lambda c: FakeScorer())
    assert cli.main(["-c", str(cfg.source_path), "run", "--date", "2026-11-05"]) == 0
    out = capsys.readouterr().out
    assert out.count("ok  ") == 6 and "FAIL" not in out

    assert cli.main(["-c", str(cfg.source_path), "universe", "--date", "2026-11-05"]) == 0
    assert "2026-11: 4 tickers (cached)" in capsys.readouterr().out
    assert cli.main(["-c", str(cfg.source_path), "news", "--date", "2026-11-05"]) == 0
    assert cli.main(["-c", str(cfg.source_path), "prices", "--date", "2026-11-05"]) == 0
    assert cli.main(["-c", str(cfg.source_path), "analyze", "--date", "2026-11-05"]) == 0
    assert "pearson" in capsys.readouterr().out
    assert cli.main(["-c", str(cfg.source_path), "export", "--date", "2026-11-05"]) == 0
    assert "index.json" in capsys.readouterr().out
    assert cli.main(["-c", str(cfg.source_path), "runs"]) == 0
    assert "manual" in capsys.readouterr().out
    assert cli.main(["-c", str(cfg.source_path), "health"]) == 1


def test_cli_exit_codes(cfg, monkeypatch, capsys):
    monkeypatch.setattr(pipeline, "default_news_source", lambda c: FakeNews())
    monkeypatch.setattr(pipeline, "default_price_source", lambda c: FakePrices(empty=True))
    monkeypatch.setattr(pipeline, "default_scorer", lambda c: FakeScorer())
    assert cli.main(["-c", str(cfg.source_path), "run", "--date", "2026-11-05"]) == 1
    out = capsys.readouterr().out
    assert "FAIL prices" in out and "FAIL analyze" in out
    assert cli.main(["-c", "/nonexistent.toml", "news"]) == 2
    assert cli.main(["-c", str(cfg.source_path), "news", "--date", "yesterday"]) == 2
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    assert "murmur 0.2.0" in capsys.readouterr().out
