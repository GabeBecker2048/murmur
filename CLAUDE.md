# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Murmur: a daily pipeline that scores news-headline sentiment (FinBERT) for the largest US
companies, stores it with daily prices in SQLite, and correlates sentiment with returns.
`ROADMAP.md` is the design document and is authoritative on scope: read its "Decisions"
section before changing schema, timing, sources or the published layout. `CHANGELOG.md`
records what shipped. The repo is private and is never published to PyPI
(`Private :: Do Not Upload` classifier).

## Repo rules

Never push to `main`; every change is a branch and a pull request (branch protection cannot
be enabled on this private repo without GitHub Pro, so this is convention). PRs are
squash-merged, title + body become the commit, and the branch is deleted on merge. Only
the three maintainers can merge. `dockerized` is an old branch kept on purpose.

## Releasing

`./scripts/release.sh X.Y.Z` from an up-to-date `main` (after the PR that bumps
`pyproject.toml` and adds the `## [X.Y.Z]` CHANGELOG section has merged). It pushes the tag;
`.github/workflows/release.yml` tests, builds, pushes `ghcr.io/gabebecker2048/murmur:X.Y.Z`
and `:latest`, and creates the GitHub release. `image.yml` only builds and smoke-tests on PRs
and `main`. The server's autopatch pulls `:latest` at 05:15, so a release deploys next morning.
`pyproject.toml` is the only place the version is written: `murmur.__version__` reads the
installed metadata (run `uv lock` after the bump; tests compare against pyproject).

## The API and jobs (0.6.0, extended in 0.7.0; split into services in 0.8.0)

- `murmur serve` = uvicorn + `api.app.create_app(provider, worker=, scheduler=)`; lifespan
  opens/migrates the store once and, unless `--no-worker`, starts one in-process
  `jobs.Worker` (55 s join on stop). The server runs the split: `serve --no-worker`,
  `murmur scheduler` (`jobs.scheduler_main` → `scheduler_loop` → `tick()` every 60 s, writes
  `heartbeat_path`) and `murmur worker` (`jobs.worker_main`; the Worker polls every 5 s and
  writes `worker_heartbeat_path` every 10 s from a side thread; `STALE_SECONDS` 300).
  `health --role api|scheduler|worker` is each container's check (`--file` = scheduler).
  `api.routes.status.services()` reports both heartbeats; the in-process Worker ticks from
  its own thread so a long job never delays the daily run (0.7.x bug).
- `src/murmur/jobs.py` is core (no FastAPI): `JobContext`/`report()`/`check()` are no-ops
  outside a job; `JobCancelled` is a BaseException; handlers in `HANDLERS` keyed by type;
  `enqueue()` validates (`JobError` → 400, `DuplicateJob` → 409). `jobs.tick()` walks
  `config.schedule.slots` (`[(time, kind)]`: one `full` slot, or `collect` at `time` +
  `analyze` at `analysis_time`), and queues a `run` job (priority -10, `params.kind`) for
  `target_day(slot_time, kind)` when the slot passed and no successful run of that kind
  (`runs.kind`, schema v8) or active job exists; nothing while `schedule.paused`. Failed
  scheduled runs re-queue with `not_before`. The worker's boot marks pre-boot running jobs
  `interrupted` and re-queues the `RESUMABLE` ones (trigger `restart`); queued jobs are kept. `pipeline.KIND_STAGES` says what
  each kind runs; `pipeline.backfill` is the `backfill` job (range, stage subset,
  `regenerate` needs `confirm`).
- `src/murmur/api/`: `deps.get_store` opens a per-request Store with `check_same_thread=False`
  (FastAPI may switch threads between dependency and handler); DB handlers are plain `def`.
  `auth.require_token` on the write routers only. Routes under `routes/`. `charts.py` renders
  PNG bytes via `Figure` + `FigureCanvasAgg`, shared with the exporter.
- Settings: `config.write_settings` applies a TOML-shaped patch to `<data_dir>/settings.toml`
  (validate first, atomic replace); `LOCKED_KEYS` are refused; `ConfigProvider.get()` reloads
  when either file changes. Tests: `tests/test_api.py`, `test_jobs.py`, `test_settings.py`.
- Backups (0.8.0): `tick()` also queues one `backup` job a day after `backup.time`
  (`_queue_backup`, `store.latest_job_of_type`); the job writes `backups/*.sqlite.part`
  and renames it. `src/murmur/offsite.py` is the murmur-backup service (`murmur offsite`):
  never opens the Store (a test checks), env-only S3 (`S3Settings.from_env`), boto3 imported
  inside `S3Target`, a `Target` protocol so tests use a fake, status + heartbeat in
  `config.offsite_dir`. `api.routes.status.health_report()` is `/health/report`;
  `deploy/murmur-check.sh` (host, ntfy-send) consumes it.
- The shuffle test: `analysis.placebo_analysis` (after `rolling_analysis` in `run_analyze`,
  `analyze_all` and per day in `analyze_range`), `shuffled_null` (vectorised within-session
  permutations, seeded by the date), table `placebo` (v10), export table `placebo`.
- The index level: `src/murmur/index_level.py` (`compute_levels`, `update_levels` replaces
  the whole history in `index_levels`/`index_contributions`, schema v11) runs after every
  analysis path; a month's weights take effect at the close of its first session.
- The comparison line (0.8.1): `pipeline._ingest_benchmark` runs inside `run_prices` and
  `backfill_prices` (best effort, never fails the stage). It fetches `index.benchmark`
  (`^GSPC`) through `YfinanceBenchmark` whatever the price source, into `benchmark_closes`
  (v12). `/index/{name}/benchmark` serves it. It is not a dataset: S&P index values stay
  out of the published tree. `tests/conftest.py` stubs `default_benchmark_source` to
  None (autouse); pass a fake `benchmark=` to test it. The index page
  (`pages/index.js` `levelChart`) filters the full level history client-side, keeps the
  range in the query string via `history.replaceState`, and switches the y-axis to
  "change since the first session" when the comparison is on.
- Findings (0.9.0): `src/murmur/findings.py` (`compute`, `create`, `refresh_live`),
  table `findings` (v13; kinds draft|frozen|live; `store.put_findings` refuses to replace
  a frozen row), prose in `src/murmur/findings_text/<id>.toml` (required to freeze),
  `pipeline.refresh_live_findings` after rolling/placebo/level (best effort),
  `routes/findings.py`, `export.export_findings` (frozen only, never rewritten when
  unchanged). Each session uses that session's universe members; market adjustment is
  computed in memory (not from `index_levels`, which is updated after the analysis).
- Data versioning (0.9.0): `publish.DATA_VERSION` (semver of the published data, not the
  software) is in every footer, the manifest and every snapshot. Changing a published
  table, column or key: bump it, add a `docs/data-changelog.md` section, then
  `uv run python tests/test_schema_stability.py --write` (refuses without the bump).
  `docs/stability.md` and `docs/data-changelog.md` are hand-written.
- `docs/*.md` are generated: after changing settings, routes, datasets or columns run
  `uv run python scripts/gen_docs.py` (`tests/test_docs.py` fails otherwise). A new
  published column needs a line in `murmur.dictionary.COLUMNS`.
- Never add a second writer thread: SQLite is single-writer and FinBERT is one CPU job.
- The web UI (0.7.0) is `src/murmur/ui/static/`: plain ES modules, no build step, Plotly
  3.1.0 basic vendored under `vendor/` (MIT; version noted in `vendor/README.md`).
  `api/ui.py` mounts `/static` and a catch-all that serves `index.html` for `UI_ROUTES`
  (registered last in `create_app`). `app.js` = router (History API), `api()` client, token
  in sessionStorage, `el()` DOM builder (textContent only; a test forbids innerHTML), theme
  toggle (`data-theme` + localStorage, `murmur:theme` event re-themes live charts).
  `charts.js` wraps Plotly with the CSS tokens (one y-axis per chart, legend for >= 2
  series, 2px lines, >= 8px markers, category x-axis for <= 10 points). Pages under
  `pages/` export `render(main, parts, query)` and may return `{destroy}` to stop timers.
  The series colors are the validated reference palette (blue/orange/aqua for the three
  matched pairs; status colors only for job states). Verify in a browser before shipping UI
  changes: `scripts/ui_shots.py` (screenshots of every page, both themes, desktop + phone)
  and `scripts/ui_review.py` (clicks through every control and admin flow; drives a visible
  Brave/Chrome over CDP on port 9222 when one is running, else headless Chromium). Our
  static files are served `no-cache` so a release never leaves stale modules in a browser.

## Commands

```bash
uv sync --all-groups                      # .venv with CPU-only torch (Linux) + dev tools
uv run murmur run [--date YYYY-MM-DD]     # universe -> news -> prices -> sentiment -> analyze -> export
uv run murmur universe|news|prices|analyze|export  # one stage; universe --rebuild recomputes the month
uv run murmur serve                       # API + UI (+ scheduler + worker in-process unless --no-worker)
uv run murmur scheduler|worker|offsite [--once]   # the other services; health --role api|scheduler|worker|backup
uv run murmur schedule                    # legacy scheduler loop, no HTTP, no jobs
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
  `universe` (source marketcap|static, size = coverage, min_market_cap), `index` (name,
  size; coverage <= index size), `schedule`.
- `store.py` SQLite. `MIGRATIONS` is keyed by `PRAGMA user_version`; add a new entry for a
  schema change, never edit a shipped one. All writes are upserts. `upsert_article` merges
  by URL or by `title_norm` within `DEDUP_WINDOW_DAYS`, keeps the earliest `published_at`,
  never overwrites `first_seen_*`, and records each reporting source in `article_sources`.
  `ticker_scores(..., source)` restricts to a source; `upsert_window_sentiment` replaces the
  whole (date, window, source) group, so pass every ticker in one call.
- `clock.py` the only place dates become timestamps, plus the NYSE calendar
  (`exchange_calendars` XNYS, lazily built with a 3-year horizon so a long-lived scheduler
  never outruns it): `is_trading_day`, `first_trading_day`, `previous_session`,
  `next_session`, `session_bounds` (early closes included), `assign_window(published_at,
  tz) -> (trading_day, overnight|intraday)` and `window_range`. Windows: overnight = previous
  close to open (weekends/holidays fold into the next session's overnight), intraday = open to
  close, `DAY` = both.
- `sources/` `base.py` has the `NewsSource`/`PriceSource` protocols (`fetch` for recent,
  `fetch_range(start, end)` where `supports_range`), `RateLimiter`, and `get_json` (urllib).
  Tickers use the SEC/Yahoo dash form (`BRK-B`). `yahoo_news.py` parses `Ticker.get_news()`
  items (no ranges); `finnhub.py` company-news by date (key, 60/min); `gdelt.py` DOC API
  ArtList by quoted company name (no key, 1 request / 5.5 s, 250 records max per query);
  `yfinance_prices.py` one `yf.download()` frame for all tickers (drops today's partial bar
  before 16:05 ET; `fetch_range` uses start/end); `polygon.py` grouped daily bars, one
  request per trading day (key, 5/min, symbols use dots: `BRK.B`); `splits.py`
  `SplitsSource` (`PolygonSplits` over `/v3/reference/splits`, `NoSplits`) feeding the
  `splits` table that `analysis.compute_returns` applies (`_already_adjusted` guards sources
  that restate history); `sec.py` fetches
  `company_tickers.json` (needs the contact User-Agent or 403); `yahoo_screener.py` pages
  `yf.screen` by market cap and looks up a ticker's country via `Ticker.info`.
  `pipeline.default_news_sources(config, names)` builds the configured list.
- `universe.py` `MarketCapUniverse.build(month)`: screener candidates in cap order, keep
  those on NYSE/Nasdaq with an SEC CIK, one per CIK, country == "United States" (cached in
  `companies` for a year), top `index.size`, each with `weight` (cap / total) and
  `exchange`. A month before the current applicable one is **reconstructed**
  (`build_historical`): SEC `frames` share counts (`sec.fetch_shares_outstanding`,
  `frame_for` = last quarter end 45 days before the month start) or today's implied count
  (`historical_shares`, `SHARES_SANITY`) times `yfinance_prices.closes_on` that day, then
  re-ranked; `source.method(month)` -> `universe_months.method` (schema v9). This is the
  index (`universe_months`, published as `m100`); the coverage
  (`universe.size`, at most `index.size`) is its top slice and is what `membership_for`
  records per day in `universe_membership` and what analysis reads. `applicable_month(day)`
  is this month from its first trading day, else the previous month. `StaticUniverse` for
  tests/offline (covered in full). Months built before 0.7.0 have `exchange = NULL` until
  `universe --rebuild`; the v7 migration computes their weights.
- `scheduler.py` `Scheduler` with injectable clock/sleep for tests: the pre-0.6.0 loop,
  kept for `murmur schedule` (full runs only; no slots). `pipeline.run_day(kind=)` wraps
  `run_all(stages_wanted=KIND_STAGES[kind])` in a `runs` row.
- `sentiment.py` `FinBertScorer` imports torch/transformers inside `__init__`;
  `score_pending()` scores articles with no row for the configured model.
- `analysis.py` everything below runs for `sources_for_analysis()` = `'all'` plus each source
  once more than one has reported (`Correlation.source`, `DayResult.by_source`).
  `assign_windows` (articles missing trading_day/window), `compute_returns`
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
- `export.py` Parquet only (pandas + pyarrow), organised as datasets. `datasets(config)` is
  the registry: `Dataset(name, cadence daily|monthly, tables, license)` with
  `Table(name, columns, key, query)`; the index dataset is named after `config.index.name`.
  Layout `{dataset}/YYYY/MM/DD/YYYYMMDD.{table}.parquet` (daily) and
  `{dataset}/YYYY/MM/YYYYMM.{table}.parquet` (monthly tables and compacted months); charts
  live under `correlations/`. `day_path`/`month_path` are the only places file names are
  built. `export_day` writes the daily datasets for the day plus the monthly ones for its
  month, compacts finished months (`compact_finished_months`, dedup by `Table.key`, day
  files removed, empty dirs pruned) and rewrites `index.json` (`manifest()`: per dataset
  title, cadence, tables with columns and keys, license, files `{table, date, path,
  format, rows, bytes, modified}`). `rebuild_month` regenerates from the store. A table
  with no rows is not written. CSV is never stored: `api.app.PublishedFiles` converts a
  `.parquet` URL with `?format=csv` on request. Every company row carries `cik` + `ticker`
  (`store.ticker_ciks()`). A `Table` may carry its own `cadence` (the monthly
  `rolling_tickers` snapshot lives inside the daily `correlations` dataset; `table_cadence()`
  decides which loop writes it). The daily `rolling` table is pooled rows only; per-ticker
  rolling rows are pruned by `store.prune_rolling_tickers` after each `rolling_analysis`
  (`analysis.keep_ticker_rolling_sessions`, month-end sessions kept) and the `vacuum` job
  reclaims the file space (SQLITE_TMPDIR on /data: the container's /tmp is a small tmpfs).
  `publish.py` owns the license notices (`cc_by`, `cc_by_scored` for the tables with
  FinBERT scores, `source_terms` for prices), the footer metadata every file gets (`file_metadata`, written by
  `_write_frame`, kept through compaction, read back with `read_metadata`) and the rounding of
  caps/weights (`round_cap` 3 s.f., `round_weight` 4 dp, `public()` on API responses). `/data`
  responses add `Link` + `X-Murmur-*` license headers (`PublishedFiles._license_headers`).

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
