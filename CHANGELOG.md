# Changelog

All notable changes to Murmur. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/). Planned versions are in `ROADMAP.md`.

## [Unreleased]

### Added

- Per-client rate limits on range bundles (`api.bundles_per_hour`, 10) and headline
  search (`api.searches_per_minute`, 60): 429 with `Retry-After`; the API token is
  exempt. `api.trusted_proxies` lets the limits see visitors behind a reverse proxy.
- The contact address in the dashboard's footer and on the Methodology page.

### Changed

- Licenses: `news`, `sentiment` and `prices` carry a short "source terms apply"
  notice (takedown available by request) instead of CC BY 4.0; `companies`, the index,
  `correlations` and the findings stay CC BY 4.0. Notices only: the schema and the
  dataset version (v1.0.0) are unchanged.
- Offsite copies go to one key (`<prefix>/store/murmur.sqlite.gz`) in a versioned
  bucket, so the lifecycle rule can keep a number of snapshots rather than an age:
  uploads that stop for a while no longer let every copy expire.
- The offsite restore check uses per-process file names (a manual `murmur offsite
  --verify` running alongside the service's own check broke both) and removes the
  `-wal`/`-shm` files that opening the WAL-mode copy leaves behind.
- The Docker image builds its dependency layer from version-blanked lock files, so a
  release no longer ships a new 530 MB layer when the dependencies did not change.

## [0.9.2] - 2026-11-18

### Added

- When each dataset is delivered: a schedule on the Data page and a "Delivered" line on
  every Overview dataset card.

### Changed

- Findings: no per-source split; the one-source caveat only says Yahoo was not used.
- Story: the rebuild's concrete findings (the prototype's were inconclusive), and all
  three friends' agreement to publish the project openly.

## [0.9.1] - 2026-11-18

An introduction, the story, a README worth reading, and findings from one source over
one full year.

### Added

- **Overview** is the new home page (`/`): what Murmur is in plain words, live counts,
  the six free datasets and what each contains (for people who only want headlines or
  an index), the question and the frozen answer, and where to go next. Findings moved to
  `/findings` (`/?findings=<id>` links go there).
- **Story** (`/story`): how three friends' 2023 prototype became Murmur, who built
  what, and the prototype next to today.
- Findings can be restricted to one news source (`murmur findings --source`, job param
  `source`); `murmur findings --delete ID` removes a draft (never a frozen snapshot).
- `docs/operating.md`: the pipeline, configuration, backfills, jobs, deployment and
  backups, moved from the README. PNG wordmarks for places that cannot load the web font.

### Changed

- The first findings snapshot is now `first-year`: Finnhub headlines only, 11 November
  2025 (Finnhub's first day in the store) to 10 November 2026, one full year measured
  the same way throughout (Yahoo headlines, collected from 5 November 2026, are left
  out). The `2026-10` draft is replaced.
- The Findings page's "does not predict" check rests on the confidence interval and the
  day-by-day estimate, and "Since then" compares like with like (the same source, plus
  all sources).
- The dataset is **v1.0.0** and its schema is fixed: a change would republish the whole
  dataset as a new version (`docs/stability.md`, `docs/data-changelog.md`, the pages).
- README rewritten as the project's front page: logo, badges, screenshots, what it is,
  what it found, how to get the data or run it, where the docs are.
- The citation lists the three authors by name.
- ROADMAP.md now lists only what is still planned.
- The footer no longer links to the admin area; it stays at `/admin` for whoever has the
  token.

## [0.9.0] - 2026-11-18

Ready to show: the answer stated and checked, the data versioned, a dashboard rebuilt
around both. Three pull requests, one release.

### Added

- **Findings**: the conclusion over a window, with its numbers and reviewed prose stored
  together (`findings` table, schema v13). A *frozen* snapshot is published once and
  never changes; a *draft* can be replaced; a *live* row covering the last 12 months is
  recomputed after every trading day's analysis (best effort, it never fails the run).
  Each snapshot carries the robustness checks the conclusion rests on: the same
  correlations with each return minus that session's cap-weighted index return, the
  mean of each session's cross-company correlation with its t-statistic across sessions
  (pooled p-values overstate certainty because a day's observations are not
  independent), the within-session shuffle test (1000 shuffles), and splits by source
  and by rank (1-50 vs 51-100). Unlike the rolling tables, every session is restricted
  to that session's index members. `murmur findings --id --from --to [--freeze]`
  (`--live`, `--list`), a `findings` job (`freeze` needs `confirm`), and the reviewed
  text in `src/murmur/findings_text/<id>.toml`. The first snapshot, `2026-10`, covers
  the first year: 3 November 2025 to 30 October 2026.
- `GET /api/v1/findings`, `/findings/{id}` (`latest` = newest frozen, `live`), and
  `GET /api/v1/charts/findings.png?id=&theme=` for embeds and link previews.
- Frozen snapshots are published as `findings/<id>.json` (with their CC BY license
  block) and the newest as `findings/latest.json`; the manifest lists them under
  `findings`.
- **Data versioning**: `DATA_VERSION` 1.0.0 in every Parquet footer
  (`murmur_data_version`), the manifest (`data_version`) and every snapshot, with
  `docs/data-changelog.md`, the promise in `docs/stability.md`, and
  `tests/test_schema_stability.py`, which fails when a published table, column or key
  disappears within a major version or the schema changes without a new version.
- **Bundles**: `GET /api/v1/bundle?from=&to=&datasets=&tables=&format=` streams any date
  range as one `.tar.gz`: one file per table with the range's rows (gathered from day
  and month files), `README.txt` with every license notice, `index.json` and
  `SHA256SUMS`. Staged under `<data_dir>/tmp`, one build at a time (429), private
  datasets only with the token, CSV of `news` up to 93 days, `dry_run=true` for the plan
  and an estimated size.
- **Monthly archives**: `archives/murmur-YYYYMM.tar.gz` for every finished month,
  (re)written after the daily export, a full export or a month rebuild when the month's
  files changed; listed in the manifest under `archives`.
- **Stable latest URLs**: `/data/{dataset}/latest.{table}.parquet` (and `?format=csv`)
  serve the newest session (or month) of a table, with `Content-Location` naming the
  dated file.
- **Headline search**: `GET /api/v1/articles?q=&ticker=&from=&to=&source=&window=&sentiment=`
  (every word of `q` in the title).
- **Atom feed** at `/feed.xml`: one entry per week (the week's mean same-session r,
  headlines, the index's move) and one per frozen findings snapshot.
- **Restore drill**: the backup service downloads the newest offsite copy once a week,
  checks its SHA-256, runs SQLite's integrity check and opens it; `murmur offsite
  --verify` runs it now. The health report flags a failed check and warns after 8 days
  without one.
- `CITATION.cff` (generated from `src/murmur/citation.py` by `scripts/gen_docs.py`),
  `GET /api/v1/citation?findings=` (BibTeX, APA, CFF), `docs/dataset-card.md` (a
  datasheet with a Hugging Face header for the 1.0 mirror) and `docs/quickstart.md`
  (pandas, DuckDB, latest URLs, bundles).
- **The dashboard, rebuilt** around the answer. Findings (`/`, the newest frozen
  snapshot: the answer, r at lags -1/0/+1 with the shuffled range in three views, the
  checks, every window x return as a heatmap, splits by size and source, caveats, the
  live last 12 months, cite/embed/download), News vs. prices (`/news-vs-prices`, the
  explorer with a heatmap in place of the 27-row table), Companies (`/companies`, with a
  second company to compare, `?vs=`), Headlines (search), Data (`/downloads`: the bundle
  builder with a size estimate and curl/pandas/DuckDB snippets, monthly archives, latest
  URLs, every file), Methodology and Status. Sources is gone (source stays a filter).
  `/leadlag`, `/tickers` and `/sources` redirect (301). Unknown pages get a 404 page.
- **The logo**: the "murmuration" flock in the header, favicon (a 7-dot version below
  48 px), touch and app icons and a 1200x630 link-preview card (`src/murmur/brand.py`,
  `scripts/make_brand.py`), with Space Grotesk (OFL, self-hosted) for the wordmark and
  headings.
- Each page's `<title>`, description, canonical URL and Open Graph/Twitter tags are
  filled in on the server, so shared links preview without running the page.
- `/embed/findings?id=&theme=`: the Findings chart alone, with attribution, for an iframe.

### Changed

- `app.js` is split: `lib.js` holds the side-effect-free helpers every page, chart and
  embed imports; `app.js` is only the shell.
- The lead/lag chart's legend sits above the plot (it overlapped the lag labels on
  narrow cards).

### Fixed

- `/api/v1/datasets` reported a dataset made private through the settings as public
  until the next export rewrote the manifest; it now follows the live setting.
- A link to a section (`/methodology#licenses`) opens at that section.

## [0.8.2] - 2026-11-08

### Added

- A "Show trendline" checkbox on the lead/lag page's "One session up close"
  scatter, on by default. It draws the ordinary least-squares line through
  that session's companies, and hovering the line gives its slope.
  `?trend=0` keeps it off across reloads and date changes.

### Changed

- The scatter's legend sits above the plot, so on phones it no longer
  overlaps the x-axis title.

## [0.8.1] - 2026-11-07

The index chart takes any date range and can show the S&P 500 for comparison.

### Added

- The index page's level chart takes any range: 1M, 3M, 6M, YTD, 1Y and All
  presets, or any from/to dates. The range is kept in the address bar
  (`?range=3m`, `?from=…&to=…`), so a view can be linked.
- A toggleable S&P 500 line on that chart. With it on, both lines show the
  change since the first session in the range, on one axis. The closes
  (`^GSPC`) are fetched from Yahoo with the prices, whichever price source is
  configured. The first run fetches back to the first price on record; after
  that each run re-fetches the last week. A failed fetch only logs a warning.
  They are stored in `benchmark_closes` (schema v12) and served by
  `GET /api/v1/index/{name}/benchmark`. They are never written to the
  published tree. `index.benchmark` (empty = no line) and
  `index.benchmark_name` choose the line.
- Date axes longer than about 300 days label ticks with month and year.

### Fixed

- `murmur.__version__` is read from the package metadata, so `pyproject.toml` is
  the only place a version is written. The first 0.8.1 tag failed its image
  check because a second copy in `murmur/__init__.py` still said 0.8.0.

## [0.8.0] - 2026-11-07

Hardening: four services, offsite backup, a health report, the shuffle test, the daily
Murmur 100 level and generated docs.

### Added

- Per-company rolling results are pruned: the store keeps them for the latest
  `analysis.keep_ticker_rolling_sessions` sessions (default 30) plus each
  month's last session; pooled results are never pruned. The daily
  `correlations/rolling` file now holds pooled rows only, and a new monthly
  `correlations/rolling_tickers` file holds the per-company snapshot as of each
  month's last session (the current month's is rewritten daily). The year-one
  store was 2.2 GB, 10 million of its rows this table.
- A `vacuum` job (admin page: "Vacuum store") rebuilds the store file after a
  prune, with SQLite's temp directory pointed at the data volume.
- Tables inside a dataset may have their own cadence; the manifest says which.
- The API, the scheduler and the worker are separate processes: `murmur serve
  --no-worker`, `murmur scheduler` and `murmur worker`, three services from
  one image in `docker-compose.yml`, each with its own health check (`murmur
  health --role api|scheduler|worker` reads the HTTP endpoint or that
  process's heartbeat file, written every 10 s, stale after 5 min). A
  dashboard deploy no longer interrupts a job. `murmur serve` alone still runs
  everything in one process. `/status` and `/health` report the two heartbeats
  (`services`) in either mode.
- A worker restart re-queues the job it interrupted when the job is safe to
  repeat (backfill, analyze, export, news, prices, sentiment, universe,
  backup, vacuum), with trigger `restart`. Queued jobs survive a restart (they
  used to be dropped).
- `--once` on `murmur scheduler` (one tick) and `murmur worker` (drain the
  queue); the image smoke test runs both.
- Offsite backup. The scheduler queues a `backup` job daily at `backup.time`
  (04:30; `backup.keep` snapshots kept, default 3); snapshots are written as
  `*.part` and renamed when integrity-checked. A fourth service,
  `murmur-backup` (`murmur offsite`), uploads each new snapshot gzipped to
  `s3://$MURMUR_S3_BUCKET/<backup.prefix>/store/` with its SHA-256, checks the
  stored size, and records the result in `data/offsite/status.json`. It is the
  only service with the S3 keys (`s3.env`) and mounts the data volume
  read-only except `data/offsite`. Unconfigured, it idles. `boto3` is a new
  dependency, loaded only by this service.
- `GET /api/v1/health/report` (`?strict=true` answers 503 when not ok):
  problems (stale heartbeats, a daily run with no success after its retries, a
  stale snapshot or offsite copy, low disk) and warnings (failed jobs in the
  last 24 h, paused, offsite not configured). `deploy/murmur-check.sh`, run from
  cron on the host twice a day, reads it and notifies through `ntfy-send`.
- The shuffle (placebo) test runs with every analysis: for each horizon and lag
  of day sentiment vs the daily return, the real pooled r next to 200 shuffles
  of each session's sentiment across its companies (`null_mean`, `null_sd`,
  2.5/97.5 % range, `z`, permutation p). Stored in `placebo` (schema v10),
  published as `correlations/.../YYYYMMDD.placebo.parquet`, served at
  `GET /api/v1/placebo`, and summarised under the overview's lead/lag chart.
- `MURMUR_LOG_FORMAT=json`: the process log as one JSON object per line.
- The daily Murmur 100 level. Holdings reset to the month's weights at the close
  of its first session (no look-ahead) and drift with the members' split-adjusted
  daily returns; base `index.base_level` (1000) at the close of `index.base_date`
  (default: the first index month's first session, 2025-11-03 on the server).
  Recomputed in full after each analysis. Published as daily `level` and
  `contributions` tables in the index dataset (CC BY 4.0; each member's
  start-of-day weight, return, contribution and points), served at
  `/api/v1/index/m100/level` and `/level/{date}`, and charted on the Murmur 100
  page with the session's five biggest moves each way. Schema v11.
- A monthly dataset may hold daily tables (export, compaction and the manifest
  follow each table's cadence).
- `docs/`: `configuration.md`, `api.md` and `data-dictionary.md`, generated by
  `scripts/gen_docs.py` from the settings schema, the OpenAPI document and the
  dataset registry with a description for every column (`murmur.dictionary`).
  A test fails when they are stale.
- `murmur health --role backup`; `/status` and `/health` report the backup
  service's heartbeat and last upload. The image smoke test now also runs the
  services with a read-only root filesystem.

### Fixed

- The scheduler ticked only when the worker was idle, so during a long queue
  (the one-year backfill, 2026-11-06) the daily run was not queued until the
  queue drained and the heartbeat went stale. The tick now runs on its own
  timer, in its own process on the server.
- A failed scheduled run's retry is queued before the run is marked failed, so
  a concurrent scheduler tick can no longer add an immediate extra attempt.
- Store migrations are serialised by a lock file, so services opening a new or
  older store at the same moment cannot apply a migration twice; the scheduler
  creates or migrates the store when it starts.

## [0.7.2] - 2026-11-06

Licensing made explicit, and prices published.

### Added

- Every Parquet file carries its license in the footer: `license`,
  `license_url`, `attribution`, `notice`, `contact`, plus `murmur_dataset`,
  `murmur_table`, `murmur_period`, `murmur_version`, `murmur_generated_at`.
  Compaction keeps it. `/data` responses send the same as `Link` (rel
  license) and `X-Murmur-*` headers, so CSV downloads carry it too.
- The prices table (bars and returns) is published under a **source terms
  apply** notice rather than CC BY: Murmur grants no rights in it, the bars
  come from Yahoo Finance via yfinance, non-commercial research, no warranty,
  takedown contact. `datasets.private` defaults to empty. The other five
  datasets are CC BY 4.0, now including `news` and `sentiment`: FinBERT's
  CC BY-NC-SA license governs the model weights, which Murmur runs
  non-commercially and does not redistribute, not the model's outputs
  (Creative Commons' position); the notice says so and still credits the model.
- `datasets.contact` (default `sec.contact`) printed in every notice.
- Market caps are rounded to three significant figures and weights to four
  decimals wherever they are served or exported, so the index can never stand
  in for a close.
- Finnhub retries transient network errors (TLS handshake timeouts) twice
  before giving up on a company.

## [0.7.1] - 2026-11-06

Backfills get real history.

### Added

- Past months are **reconstructed** instead of copying today's list: each
  candidate's cap on the month's first trading day is its shares outstanding
  then (SEC XBRL frames, public domain, with today's implied count as the
  fallback for per-class filers) times its close that day. Stored with
  `method = 'reconstructed'` (schema v9), flagged `reconstructed` in the
  index dataset and `method` in `GET /index/{name}`. Candidate pool: today's
  screener list.
- `backfill` runs news and prices month by month so each month uses its own
  constituents.
- Finnhub waits out a 429 and retries (twice) instead of failing the ticker.
- A test pins that a backfill never touches existing headlines: Yahoo rows
  keep their id and source, a headline both sources carried records both.

## [0.7.0] - 2026-11-06

Datasets, control, the web UI.

### Added

- The web UI at `/`: overview, lead/lag explorer, companies, sources, the
  index, downloads, and an admin area (jobs, backfill, scheduler, settings,
  logs) behind the API token. Plain ES modules with Plotly.js vendored, no
  build step, light and dark themes, phone layout. `GET /` and the page
  routes serve the shell; `/static` serves the files.

- The published tree is six datasets, dataset first, every file named after
  its date and table: `companies`, `m100` (the Murmur 100 with `weight` and a
  monthly `changes` table), `prices` (bar + returns), `news` (article x
  ticker + scores), `sentiment`, `correlations` (+ charts). `index.json` is a
  manifest with tables, columns, keys and a license per dataset (CC BY 4.0;
  CC BY-NC-SA 4.0 where FinBERT scores are involved).
- `?format=csv` on any Parquet URL under `/data` converts on request.
- `[index] name, size`: the index is ranked and weighted at `index.size`;
  `universe.size` is coverage and may be smaller. Schema v7 adds `weight` and
  `exchange` to the monthly universe and computes weights for existing months.
- `GET /api/v1/datasets[/{name}]`, `/api/v1/index/{name}[/months|/changes|
  /history/{key}]`, `/api/v1/companies[/{key}]`. Every company row in the API
  and the files carries `cik`.

- A `backfill` job: date range, any subset of news/prices/sentiment/analyze/
  export in pipeline order, and `regenerate` (confirm required) for a clean
  slate with the months' files rebuilt.
- A split schedule: `schedule.analysis_time` adds a morning `analyze` run for
  the previous session while `schedule.time` becomes a news-only `collect`
  run, for price sources whose bar is not available at 18:00. Runs carry a
  `kind` (schema v8); `murmur run --kind`.
- Split ratios (`prices.splits`, Polygon's reference endpoint, `splits` table)
  applied in `compute_returns`, so unadjusted sources give correct returns
  across a split; `split_ratio` in the prices dataset.
- `POST /api/v1/scheduler/pause` and `/resume` (`schedule.paused`, persisted).
- `[ui]` settings (title, tagline, default horizon and method, theme) and
  `GET /api/v1/ui` for the dashboard.
- `[datasets] private`: datasets served from `/data` only with the API token
  and flagged `public: false` in the manifest. `prices` is private by default
  until its source's terms allow publishing.

### Changed

- `/api/v1/universe` and `/universe/months` remain as deprecated aliases of
  `/api/v1/index/{name}`.
- The published tree on an existing deployment is rebuilt once with
  `export --all` (the old date-first files are removed by hand).

## [0.6.0] - 2026-11-06

The API. One process for everything, and a control plane for the web UI.

### Added

- `murmur serve`: FastAPI on port 8100 with `/api/v1` (health, status,
  latest, runs, jobs, settings, universe, days, tickers, correlations,
  lead/lag, sources, logs, one on-demand chart), `/data` serving the
  published tree, and `/docs`.
- Jobs: every long or mutating operation is a queued job executed one at a
  time by a worker thread, with JSON progress, a tailable log, cooperative
  cancellation, retries with `not_before`, and interruption marking on
  restart. The daily scheduler is a tick inside the worker, so scheduled runs
  and manual backfills never overlap. Destructive jobs need `confirm: true`.
- Settings editable through `PUT /api/v1/settings`, stored in
  `<data_dir>/settings.toml` above the ops file; `GET /settings/schema`
  describes every key for a form; provenance per key.
- Bearer-token auth (`MURMUR_API_TOKEN`) on writes; reads open. CORS via
  `MURMUR_CORS_ORIGINS`.
- `export.rebuild_month` regenerates a month from the store so deletions
  reach the published tree; `index.json` entries carry `bytes` and
  `modified`.
- Schema v6 (`jobs`). The Docker image runs `murmur serve` with an HTTP
  health check; compose publishes 8100 and sets a 60 s stop grace period.

### Changed

- `murmur health` checks the running server over HTTP (`--file` for the
  heartbeat). `murmur schedule` remains as the no-HTTP fallback.
- Charts are rendered through `matplotlib.figure.Figure`, never pyplot.

## [0.5.1] - 2026-11-06

What the first live run of the new sources taught.

### Fixed

- A news source that fails for every ticker no longer fails the stage when
  another source delivered; it is logged as an error and named as `DEAD` in
  the summary. The stage fails only when every source failed. (GDELT's API
  currently answers every request with a rate-limit message.)
- Polygon on the free plan refuses today's bar until the day is over; that
  day is now skipped with a warning instead of failing the fetch, so the
  source is usable for backfills. It still cannot feed the 18:00 run on that
  plan; yfinance stays the daily price source.

## [0.5.0] - 2026-11-06

Sources. News and prices from more than one place, compared.

### Added

- Finnhub company news and GDELT (DOC API) as news sources, Polygon grouped
  daily bars as a price source, all behind the `NewsSource`/`PriceSource`
  interfaces with rate limiting. `news.sources` and `prices.source` select
  them; keys from config or `MURMUR_FINNHUB_API_KEY`/`MURMUR_POLYGON_API_KEY`.
- Cross-source deduplication: an article seen through several sources is one
  row (same URL, or the same normalized title within two days), keeping the
  earliest publication time and recording every source in `article_sources`
  (schema v5).
- Per-source analysis: `window_sentiment`, `correlations` and
  `rolling_correlations` carry a `source` dimension (`all` = pooled over
  sources) and are computed for each source once more than one has reported.
- Date-range backfills: `murmur news --from/--to` (finnhub, gdelt) and
  `murmur prices --from/--to`, a week at a time for news.
- `sources` column on exported articles.

### Changed

- The news stage reports per source and fails only if a source fails for
  every ticker.

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
