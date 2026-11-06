# Changelog

All notable changes to Murmur. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/). Planned versions are in `ROADMAP.md`.

## [0.4.0] - 2026-11-06

Lead/lag analysis. The first version that can say something about the
central question, once enough days have accumulated.

### Added

- Daily correlations for every window × return type at lags −1, 0 and +1
  (lag = return session − news session), Pearson and Spearman.
- Rolling correlations as of each day over the last 1, 3, 6 and 12 months:
  pooled over every ticker-day observation and per ticker; stored in
  `rolling_correlations` (schema v4) and exported as
  `rolling_correlations.parquet`, with a `leadlag.png` chart per day.
- Each run re-analyses the previous session (which only now has a lag +1
  return) and re-exports it, so late-arriving headlines reach the published
  file.
- `murmur analyze --all` recomputes every day with returns, for after a
  schema change.

### Changed

- `murmur analyze` lists every lag; the one-line summary shows the matched
  lag-0 pairs and the row count.

## [0.3.0] - 2026-11-05

Timing and returns. The dataset now has the shape the lead/lag analysis
needs.

### Added

- News windows on the NYSE calendar: every article gets a trading day and a
  window (`overnight`: previous close to open, including weekends and
  holidays; `intraday`: open to close), with early closes handled.
- Overnight, intraday and daily returns from open and close, stored in a
  `returns` table.
- Per-window sentiment (`window_sentiment`) and correlations per
  window/return pair at lag 0 (`correlations` now keyed by window,
  return type and lag).
- Parquet export, date first: `YYYY/MM/DD/<table>.parquet` for articles,
  prices, returns, sentiment, window_sentiment and correlations, plus the two
  charts; `YYYY/MM/universe.parquet` per month; finished months compacted into
  `YYYY/MM/<table>.parquet`. No CSV anywhere. `murmur export --all` rebuilds
  the tree.
- `murmur analyze` prints every correlation with its confidence interval.
- Releases: `scripts/release.sh X.Y.Z` pushes a tag; the release workflow
  tests, builds, pushes the image as `X.Y.Z` and `latest`, and creates the
  GitHub release from this file. Merges to `main` no longer publish an image.

### Changed

- Schema version 3 (migrates automatically); `daily_sentiment` is gone and
  existing correlation rows become `day`/`daily`/lag 0.
- Calendar-day bucketing is gone: a non-trading day stores no analysis, its
  news belongs to the next session.
- Articles are partitioned in the published tree by trading day, not by
  publication date.

### Removed

- CSV output and the `table/YYYY/MM/DD` layout from 0.1.0.

## [0.2.0] - 2026-11-05

Running daily. From this version the store is meant to grow every day.

### Added

- The market-cap universe: the 100 largest US-domiciled companies on NYSE or
  Nasdaq, one ticker per SEC CIK, from the SEC ticker list and Yahoo's
  screener with the headquarters country from yfinance (cached a year).
  Rebuilt monthly, in force from the month's first trading day; days before
  that use the previous month. `murmur universe` shows the list for a day.
- NYSE trading calendar (`exchange-calendars`). A weekend or holiday skips
  the correlation; a trading day without price bars is now a failure so the
  scheduler retries it.
- `murmur schedule`: the daily job at `schedule.time` (18:00 ET) every day,
  retries on failure, catch-up after a restart, heartbeat file, clean stop on
  SIGTERM. `murmur health` checks the heartbeat; `murmur runs` lists the run
  log (new `runs` table).
- Backfill: the analyze stage also completes any trading day in the last week
  that has no correlation yet but whose price bars have since arrived, and
  exports it. Yahoo's daily bar for a day can disappear for a while in the
  evening, which made that day's own run fail analyze.
- `sentiment.device`: `cpu` (default), `cuda` or `auto`.
- `Dockerfile`, `docker-compose.yml` and a CI workflow that builds the image
  and pushes it to ghcr.io; `deploy/murmur-backup.sh` for the server.
- Tests refuse network sockets, so an accidental live call fails fast.

### Changed

- The default universe source is `marketcap`; the fixed list from 0.1.0 is
  still available as `source = "static"`.
- Tickers use the SEC/Yahoo dash form (`BRK-B`) instead of `BRK.B`.
- `[sec] contact` (an email) is required for the monthly SEC fetch.
- Schema version 2 (migrates automatically): `companies`, `universe_months`,
  `runs`, and a `cik` column on `universe_membership`.
- The pipeline has a `universe` stage before `news`; `news` and `prices`
  fail if no universe is recorded for the day.

## [0.1.0] - 2026-11-05

The project becomes Murmur. This release is the package; the daily job,
the market-cap universe and the published API follow.

### Added

- `src/murmur/` package with `pyproject.toml`, a uv lock file, MIT license,
  ruff, pytest and a CI workflow.
- `murmur` CLI: `run`, `news`, `prices`, `analyze`, `export`, each with
  `--date`; `--config` / `MURMUR_CONFIG` / `./murmur.toml` for settings and
  `MURMUR_DATA_DIR` for the data directory.
- SQLite store with versioned migrations. Articles are deduplicated by URL and
  keep the earliest `published_at`; prices, scores and results are upserts, so
  a day can be rerun.
- Per-day universe membership recorded in the store, seeded from the static
  list in the config (the 50 tickers from the old `settings.json`).
- Yahoo Finance per-ticker news and yfinance daily bars behind `NewsSource`
  and `PriceSource` interfaces. Today's bar is ignored before 16:05 ET.
- FinBERT (`ProsusAI/finbert`) headline sentiment with per-class
  probabilities and `score = positive − negative`, scored once per article.
- Daily analysis: per-ticker mean sentiment, daily return, pooled Pearson
  (with 95% CI) and Spearman across the universe, stored per day.
- Export of the day's tables as CSV in the published layout from the roadmap,
  with `index.json` and the sentiment bar and sentiment-vs-return charts.

### Changed

- Pure Python. R, the Tkinter GUI, Google News (`gnews`) and the Bing lexicon
  are gone.
- Configuration moved from `lib/settings.json` to TOML merged over packaged
  defaults.

### Fixed

- Sentiment and prices are joined by ticker. The R script paired them by row
  position (stock rows sorted by ticker, sentiment rows sorted by company
  name) and also divided the unsorted price difference by the sorted
  previous close, so both the pairing and the returns were wrong whenever the
  two orders differed.
- Pipeline stages no longer swallow exceptions; `run` reports each stage and
  exits non-zero if one failed.

### Removed

- `lib/`, `main.py`, `start.sh`, `start.bat`, the R scripts, the GUI, the
  example CSV/PNG outputs and the icons.
