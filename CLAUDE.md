# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Murmur: a daily pipeline that scores news-headline sentiment (FinBERT) for the largest US
companies, stores it with daily prices in SQLite, and correlates sentiment with returns.
`ROADMAP.md` is the design document and is authoritative on scope: read its "Decisions"
section before changing schema, timing, sources or the published layout. `CHANGELOG.md`
records what shipped. The repo is private and is never published to PyPI
(`Private :: Do Not Upload` classifier).

## Releasing

`./scripts/release.sh X.Y.Z` from an up-to-date `main` (after the PR that bumps
`pyproject.toml` and adds the `## [X.Y.Z]` CHANGELOG section has merged). It pushes the tag;
`.github/workflows/release.yml` tests, builds, pushes `ghcr.io/gabebecker2048/murmur:X.Y.Z`
and `:latest`, and creates the GitHub release. `image.yml` only builds and smoke-tests on PRs
and `main`. The server's autopatch pulls `:latest` at 05:15, so a release deploys next morning.

## Commands

```bash
uv sync --all-groups                      # .venv with CPU-only torch (Linux) + dev tools
uv run murmur run [--date YYYY-MM-DD]     # universe -> news -> prices -> sentiment -> analyze -> export
uv run murmur universe|news|prices|analyze|export  # one stage; universe --rebuild recomputes the month
uv run murmur schedule                    # the daily job (container main process); runs / health
uv run murmur -v ...                      # debug logging; -c PATH for a config file
uv run ruff check . && uv run ruff format --check .
uv run pytest                             # no network, no model download
MURMUR_TEST_MODEL=1 uv run pytest tests/test_sentiment.py   # also the real FinBERT test
```

Paths are not relative to the repo root anymore: `data_dir` (default `./data`, override
with `MURMUR_DATA_DIR`) holds `murmur.sqlite` and `published/`. `data/` and `murmur.toml`
are gitignored.

## Architecture (`src/murmur/`)

- `cli.py` argparse entry point (`murmur = murmur.cli:main`); `main()` returns the exit code.
- `pipeline.py` the stages. `run_all` runs each stage in a try/except, logs the traceback,
  continues, and the CLI exits 1 if any stage raised. `default_*_source`/`default_scorer`
  are module-level factories so tests monkeypatch them.
- `config.py` TOML merged over the packaged `default_config.toml`. Resolution:
  `--config` > `MURMUR_CONFIG` > `./murmur.toml` > defaults. Every table merges key by key
  except `[universe.static]`, which a user file replaces wholesale. Sections: `news`,
  `sentiment` (model, batch_size, device), `sec` (contact email, required by the SEC),
  `universe` (source marketcap|static, size, min_market_cap), `schedule`.
- `store.py` SQLite. `MIGRATIONS` is keyed by `PRAGMA user_version`; add a new entry for a
  schema change, never edit a shipped one. All writes are upserts. Article upserts keep the
  earliest `published_at` and never overwrite `first_seen_*`.
- `clock.py` the only place dates become timestamps, plus the NYSE calendar
  (`exchange_calendars` XNYS, lazily built with a 3-year horizon so a long-lived scheduler
  never outruns it): `is_trading_day`, `first_trading_day`, `previous_session`,
  `next_session`, `session_bounds` (early closes included), `assign_window(published_at,
  tz) -> (trading_day, overnight|intraday)` and `window_range`. Windows: overnight = previous
  close to open (weekends/holidays fold into the next session's overnight), intraday = open to
  close, `DAY` = both.
- `sources/` `base.py` has the `NewsSource`/`PriceSource` protocols. Tickers use the
  SEC/Yahoo dash form (`BRK-B`). `yahoo_news.py` parses `Ticker.get_news()` items;
  `yfinance_prices.py` parses one `yf.download()` frame for all tickers and drops today's
  partial bar before 16:05 ET; `sec.py` fetches `company_tickers.json` (needs the contact
  User-Agent or the SEC returns 403); `yahoo_screener.py` pages `yf.screen` by market cap
  and looks up a ticker's country via `Ticker.info`.
- `universe.py` `MarketCapUniverse.build(month)`: screener candidates in cap order, keep
  those on NYSE/Nasdaq with an SEC CIK, one per CIK, country == "United States" (cached in
  `companies` for a year), top `size`. `applicable_month(day)` is this month from its first
  trading day, else the previous month. `membership_for` builds the month if the store has
  no row for it and records the day's membership. `StaticUniverse` for tests/offline.
- `scheduler.py` `Scheduler` with injectable clock/sleep for tests: fires at
  `schedule.time`, retries, catches up a missed day on start, heartbeat file, SIGTERM stop.
  `serve()` is `murmur schedule`. `pipeline.run_day` wraps `run_all` in a `runs` row.
- `sentiment.py` `FinBertScorer` imports torch/transformers inside `__init__`;
  `score_pending()` scores articles with no row for the configured model.
- `analysis.py` `assign_windows` (articles missing trading_day/window), `compute_returns`
  (overnight/intraday/daily from the day's bar and the previous session's close, needs both),
  `window_sentiment` per window. `analyze_day` correlates every window × return type at
  `LAGS` (-1, 0, +1; lag = return session − news session, returns for other sessions read
  from the `returns` table) with Pearson (+CI) and Spearman, join **by ticker**; `MATCHED_PAIRS`
  are the headline lag-0 pairs. `rolling_analysis(as_of)` builds pandas frames of
  window_sentiment and returns over 12 months, shifts returns by session index per lag,
  and computes pooled (n ≥ `MIN_POOLED`) and per-ticker (n ≥ `MIN_TICKER`) rows for each
  `HORIZONS` month count; rows for `as_of` are replaced. A non-trading day stores nothing; a
  trading day with no bars raises so the scheduler retries.
- `pipeline.run_analyze` order: backfill missing days → today → a second pass over the
  previous session and the backfilled days (they gain lag +1 now) → rolling as of today.
  Everything analysed is exported. `analyze_all` is `murmur analyze --all`.
- `export.py` Parquet only (pandas + pyarrow). Date-first layout: `YYYY/MM/DD/<table>.parquet`
  for every table in `DAY_TABLES` plus the PNGs (sentiment, correlation, leadlag),
  `YYYY/MM/universe.parquet` per month.
  `compact_finished_months` folds every month before the current one into
  `YYYY/MM/<table>.parquet` (dedup by `KEYS`), leaving only charts in the day dirs. A table
  with no rows is not written (stale file removed). `index.json` entries
  `{table, date, path, format, rows}`. `export_day(..., today=)` is injectable for tests.

## Rules that tests enforce

- `murmur.cli`, `murmur.pipeline`, the sources and `murmur.export` must import without
  loading torch, transformers or matplotlib (`tests/test_sentiment.py`).
- Sentiment/price pairing is by ticker (`tests/test_analysis.py`, companies whose names
  sort differently from their tickers).
- Reruns are idempotent: same article count, nothing rescored (`tests/test_pipeline.py`).
- Tests use `FakeScorer` (`tests/conftest.py`), fake sources and payload fixtures in
  `tests/fixtures/`. An autouse fixture makes any socket connect raise, so a test that hits
  the network fails fast instead of silently calling Yahoo (which once took 50 s per test).
  Test configs must say `[universe] source = "static"` or they trigger the real universe.

## Gotchas

- `date` columns are NYSE trading days; `published_at` is UTC `YYYY-MM-DDTHH:MM:SSZ`.
  Bucketing happens once, in Python (`assign_windows`), and is stored on the article row;
  queries filter on `trading_day`/`window`, never on timestamps. `window` is a SQL keyword
  but SQLite accepts it as a column name.
- The analysis uses `config.sentiment.model` to select score rows, so an injected scorer
  must have the same `model_name` (the pipeline raises otherwise).
- `torch` resolves from the PyTorch CPU index on Linux only (`[tool.uv.sources]`); after
  changing dependencies check `uv.lock` has no `nvidia-*` packages.
- A `[universe.static]` table in a user config replaces the packaged 50 tickers.
- Yahoo's daily bar for the current day can vanish from chart data in the evening while
  they rebuild end-of-day figures (2026-11-05: present at 18:07 and 19:53 ET, gone at 20:20,
  back with a NaN close at 20:22). The parser drops NaN closes; analyze fails for the day;
  the next run's `backfill_days` (pipeline.py) completes it. Do not "fix" this by relaxing
  the trading-day check.
- Running `murmur run --date <past day>` cannot recover that day's news: Yahoo's per-ticker
  feed is only the latest ~20 headlines. Past-day runs are for prices/analysis only.
- The packaged default `[sec] contact` is Gabe's email; the SEC blocks requests without one.
- Chubb (CB) is Swiss-domiciled and so excluded by the US-only rule even though it is in the
  S&P 500. That is the rule working as decided, not a bug.
- No Docker on the laptop: the image is built and smoke-tested by `.github/workflows/image.yml`.
  `docker-compose.yml` follows gabe-server's conventions (uid 1000, read-only, mem_limit);
  deployment there needs Gabe's approval (homelab rules).

## Other branches

`main` before `release/0.1.0` was the 2024 Python + R + Tkinter tool (see git history). Two
stale remote branches predate the rewrite and are not a base for anything:
`origin/dockerized` (Feb 2025, standalone stage scripts + a Hugging Face sentiment model)
and `origin/SentiStreet_rework` (Sep 2025, unfinished rename).
