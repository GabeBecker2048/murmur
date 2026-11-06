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
        headline = COMPANIES[ticker][5]  # the intraday headline (published 10:00 ET)
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

    def fetch_range(self, tickers, start, end):
        return self.fetch(tickers)

    def fetch(self, tickers):
        if self.empty:
            return []
        bars = []
        for t in tickers:
            _, prev_close, open_, close, _, _ = COMPANIES[t]
            bars.append(PriceBar(t, "2026-11-04", prev_close, None, None, prev_close, None, 1))
            bars.append(PriceBar(t, "2026-11-05", open_, None, None, close, None, 1))
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
        # the 4th has a bar but nothing before it, so no return and no backfill
        assert store.universe_for("2026-11-04") == []
        # fake articles are all intraday, so the overnight window has no pairs:
        # (intraday + day) x 3 return types x 2 methods, lag 0 only
        assert store.count("correlations") == 12
        assert "intraday/intraday r=" in results[4].summary
        assert "overnight/overnight has 0 pairs" in results[4].summary

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
        for t, (_, _, _, _, _, headline) in COMPANIES.items():  # FakeNews dates everything Nov 5
            # a distinct title: the same title a day apart would be deduplicated into one
            store.upsert_article(
                Article(f"https://e/{t}/4", f"Wednesday: {headline}", "2026-11-04T15:00:00Z", "f"),
                t,
                "t",
            )
        for t, (_, _, _, close, _, _) in COMPANIES.items():  # Nov 3 close != Nov 4 close
            store.upsert_prices([PriceBar(t, "2026-11-03", close, None, None, close, None, 1)], "t")
        pipeline.run_sentiment(store, cfg, FakeScorer())
        with pytest.raises(RuntimeError, match="no price bars"):
            pipeline.run_analyze(store, cfg, nov4)
        assert store.correlations_on("2026-11-04") == []

        # Nov 5's run fetches a month of bars (now including the 4th) and backfills
        results = pipeline.run_all(store, cfg, nov5, FakeNews(), FakePrices(), FakeScorer())
        by_name = {r.name: r for r in results}
        assert all(r.ok for r in results), [r.summary for r in results]
        assert "2026-11-04" in by_name["analyze"].summary.split("backfilled ")[1]
        # Nov 4: intraday return is flat (open == close) so only overnight/daily returns
        # correlate; with Nov 5 analysed, Nov 4 also gets its lag +1 rows
        nov4 = store.correlations_on("2026-11-04")
        assert {r["lag"] for r in nov4} == {0, 1}
        assert "intraday" not in {r["return_type"] for r in nov4 if r["lag"] == 0}
        assert {r["lag"] for r in store.correlations_on("2026-11-05")} == {-1, 0}
        assert (cfg.published_dir / "correlations/2026/11/04/20261104.daily.parquet").exists()
        assert (cfg.published_dir / "correlations/2026/11/05/20261105.daily.parquet").exists()

        # nothing left to backfill
        assert pipeline.backfill_days(store, nov5) == []


def test_news_requires_a_recorded_universe(cfg):
    with Store(cfg.store_path) as store:
        with pytest.raises(RuntimeError, match="no universe recorded"):
            pipeline.run_news(store, cfg, DAY, FakeNews())


def test_one_failing_ticker_is_tolerated_all_failing_is_not(cfg):
    with Store(cfg.store_path) as store:
        pipeline.run_universe(store, cfg, DAY)
        summary = pipeline.run_news(store, cfg, DAY, [FakeNews(fail_for={"META"})])
        assert "1 tickers failed" in summary
        assert store.count("articles") == 4
        with pytest.raises(RuntimeError, match="every source failed"):
            pipeline.run_news(store, cfg, DAY, [FakeNews(fail_for=set(COMPANIES))])


def test_cli_run_and_subcommands(cfg, monkeypatch, capsys):
    monkeypatch.setattr(pipeline, "default_news_sources", lambda c, names: [FakeNews()])
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
    assert cli.main(["-c", str(cfg.source_path), "health", "--file"]) == 1


def test_cli_exit_codes(cfg, monkeypatch, capsys):
    monkeypatch.setattr(pipeline, "default_news_sources", lambda c, names: [FakeNews()])
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
    assert "murmur 0.7.0" in capsys.readouterr().out


def test_previous_session_is_reanalysed_and_reexported(cfg):
    with Store(cfg.store_path) as store:
        # Nov 4 with headlines of its own and a bar the day before, analysed on its own day
        pipeline.run_universe(store, cfg, date(2026, 11, 4))
        for t, (_, _, _, close, _, headline) in COMPANIES.items():
            store.upsert_article(
                Article(f"https://e/{t}/4", f"Wednesday: {headline}", "2026-11-04T15:00:00Z", "f"),
                t,
                "t",
            )
            store.upsert_prices([PriceBar(t, "2026-11-03", close, None, None, close, None, 1)], "t")
        store.upsert_prices(FakePrices().fetch(list(COMPANIES)), "t")
        pipeline.run_sentiment(store, cfg, FakeScorer())
        pipeline.run_analyze(store, cfg, date(2026, 11, 4))
        assert {r["lag"] for r in store.correlations_on("2026-11-04")} == {0}
        results = pipeline.run_all(store, cfg, DAY, FakeNews(), FakePrices(), FakeScorer())
        by_name = {r.name: r for r in results}
        assert "re-analysed 2026-11-04" in by_name["analyze"].summary
        assert {r["lag"] for r in store.correlations_on("2026-11-04")} == {0, 1}
        assert (cfg.published_dir / "correlations/2026/11/04/20261104.daily.parquet").exists()


def test_analyze_all(cfg, monkeypatch, capsys):
    monkeypatch.setattr(pipeline, "default_news_sources", lambda c, names: [FakeNews()])
    monkeypatch.setattr(pipeline, "default_price_source", lambda c: FakePrices())
    monkeypatch.setattr(pipeline, "default_scorer", lambda c: FakeScorer())
    assert cli.main(["-c", str(cfg.source_path), "run", "--date", "2026-11-05"]) == 0
    with Store(cfg.store_path) as store:
        store.conn.execute("DELETE FROM correlations")
    assert cli.main(["-c", str(cfg.source_path), "analyze", "--all"]) == 0
    assert "re-analysed 1 days" in capsys.readouterr().out
    with Store(cfg.store_path) as store:
        assert store.count("correlations") == 12


class FakeRangeNews:
    name = "fakerange"
    supports_range = True

    def __init__(self, fail_for=()):
        self.calls = []
        self.fail_for = set(fail_for)

    def fetch(self, ticker):
        return self.fetch_range(ticker, date(2026, 11, 5), date(2026, 11, 5))

    def fetch_range(self, ticker, start, end):
        if ticker in self.fail_for:
            raise OSError("boom")
        self.calls.append((ticker, start, end))
        return [
            Article(
                f"https://r/{ticker}/{start}",
                f"{ticker} surges {start}",
                f"{start}T15:00:00Z",
                self.name,
            )
        ]


def test_two_news_sources_are_ingested_and_counted(cfg):
    with Store(cfg.store_path) as store:
        pipeline.run_universe(store, cfg, DAY)
        summary = pipeline.run_news(store, cfg, DAY, [FakeNews(), FakeRangeNews()])
        assert "fake-news: 8 articles" in summary and "fakerange: 4 articles, 4 new" in summary
        assert store.sources_seen() == ["fake-news", "fakerange"]
        # one dead source is tolerated when another delivered, and named
        summary = pipeline.run_news(
            store, cfg, DAY, [FakeNews(), FakeRangeNews(fail_for=set(COMPANIES))]
        )
        assert summary.endswith("DEAD: fakerange")
        with pytest.raises(RuntimeError, match="every source failed"):
            pipeline.run_news(
                store,
                cfg,
                DAY,
                [FakeNews(fail_for=set(COMPANIES)), FakeRangeNews(fail_for=set(COMPANIES))],
            )


def test_backfill_news_chunks_by_week_and_needs_a_ranged_source(cfg):
    with Store(cfg.store_path) as store:
        src = FakeRangeNews()
        summary = pipeline.backfill_news(
            store, cfg, date(2026, 10, 1), date(2026, 10, 20), [FakeNews(), src]
        )
        assert summary.startswith("2026-10-01..2026-10-20: fakerange: 12 articles, 12 new")
        assert [c[1:] for c in src.calls if c[0] == "AAPL"] == [
            (date(2026, 10, 1), date(2026, 10, 7)),
            (date(2026, 10, 8), date(2026, 10, 14)),
            (date(2026, 10, 15), date(2026, 10, 20)),
        ]
        with pytest.raises(RuntimeError, match="needs a source with date ranges"):
            pipeline.backfill_news(store, cfg, date(2026, 10, 1), date(2026, 10, 2), [FakeNews()])


def test_backfill_prices_uses_the_range(cfg):
    class RangePrices:
        name = "rp"

        def fetch(self, tickers):
            raise AssertionError("fetch should not be used for a backfill")

        def fetch_range(self, tickers, start, end):
            self.seen = (start, end)
            return [PriceBar(t, "2026-10-02", 1, 1, 1, 2, None, 1) for t in tickers]

    with Store(cfg.store_path) as store:
        src = RangePrices()
        summary = pipeline.backfill_prices(store, cfg, date(2026, 10, 1), date(2026, 10, 2), src)
        assert src.seen == (date(2026, 10, 1), date(2026, 10, 2))
        assert "4 bars for 4/4 tickers" in summary
        assert store.count("prices") == 4


def test_cli_backfill_flags(cfg, monkeypatch, capsys):
    monkeypatch.setattr(pipeline, "default_news_sources", lambda c, names: [FakeRangeNews()])
    monkeypatch.setattr(pipeline, "default_price_source", lambda c: FakePrices())
    assert (
        cli.main(["-c", str(cfg.source_path), "news", "--from", "2026-11-03", "--to", "2026-11-04"])
        == 0
    )
    assert "2026-11-03..2026-11-04" in capsys.readouterr().out
