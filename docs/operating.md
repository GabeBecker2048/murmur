# Operating Murmur

How the pipeline works and how to run, configure, deploy and develop it. The project's
front page is the [README](../README.md); the published data is described in
[data-dictionary.md](data-dictionary.md), every setting in
[configuration.md](configuration.md) and every route in [api.md](api.md).

## How it works

For one day, `murmur run` does six stages in order and keeps going if one
fails:

| stage | what it does | source | table |
|---|---|---|---|
| universe | the companies in the index that day (built once a month, see below) | SEC + Yahoo screener | `universe_months`, `universe_membership` |
| news | recent headlines for every ticker in the universe, from each configured source | Yahoo (yfinance), Finnhub, GDELT | `articles`, `article_tickers`, `article_sources` |
| prices | daily OHLCV bars for the last month | yfinance (one request) or Polygon (one request per day) | `prices` |
| sentiment | scores every headline not yet scored | FinBERT (`ProsusAI/finbert`), CPU | `sentiment` |
| analyze | windows for new articles, the three returns, per-window sentiment, correlations at lags −1/0/+1 across tickers, and the rolling lead/lag results | scipy | `returns`, `window_sentiment`, `correlations`, `rolling_correlations` |
| export | the day's tables as Parquet (CSV for the small ones), two charts and `index.json` | | `<data_dir>/published/` |

Every write is an upsert, so running a day twice stores an article once and
never rescores it, and every run is logged (`murmur runs`). Dates are calendar
days in `America/New_York`; article timestamps are UTC. On a weekend or NYSE
holiday the analysis stores sentiment and skips the correlation without
failing. On a trading day with no price bars (run before the close, or the
prices stage failed) it fails, so the scheduler retries.

### The universe

The 100 largest US-domiciled companies listed on NYSE or Nasdaq by market cap,
one ticker per company (Alphabet counts once; the larger share class is kept).
Candidates come from Yahoo's equity screener, the company identity (CIK) from
the SEC's public ticker list, and the headquarters country from yfinance,
cached for a year. OTC and preferred shares are excluded, and so are foreign
companies traded through ADRs. The list is rebuilt for each month and takes
effect on its first trading day; days before that use the previous month.
Membership is recorded per day, so any past day's universe can be
reconstructed, and `murmur universe` prints the list in force on a day.

### Sources

| source | kind | needs | date ranges | notes |
|---|---|---|---|---|
| `yahoo` | news | nothing | no | the latest ~20 headlines per ticker; the default |
| `finnhub` | news | API key | yes, ~1 year | company news by ticker and date; 60 calls a minute |
| `gdelt` | news | nothing | yes, to 2015 | global news by company name; one request every 5 s. **Currently refuses every request** with a rate-limit message, from two different networks; keep it off until that changes |
| `yfinance` | prices | nothing | yes | the default; one request for all tickers |
| `polygon` | prices | API key | yes, 2 years | grouped daily bars, one request per trading day; 5 calls a minute. The free plan refuses today's bar until the day is over, so it serves backfills, not the 18:00 run |

`news.sources` lists the news sources the daily run queries; `prices.source`
picks the price source. Keys go in `[finnhub]`/`[polygon] api_key` or the
`MURMUR_FINNHUB_API_KEY`/`MURMUR_POLYGON_API_KEY` environment variables.

**Splits.** Polygon delivers unadjusted prices, so a 10-for-1
split would look like a 90 % loss. `prices.splits` names a source of split
ratios (`polygon`'s reference endpoint, one request for the whole market;
`auto` uses it when the key is set) and `compute_returns` divides the previous
close by the ratio on the split's execution date. The ratio is published as
`split_ratio` in the prices dataset. Yahoo restates its history instead; the
adjustment detects that and is not applied twice.

An article reported by several sources is stored once (same URL, or the same
normalized title within two days), with the earliest publication time and
every reporting source recorded. Sentiment and correlations are computed
pooled over sources (`source = "all"`) and, once more than one source has
reported, for each source separately, so sources can be compared.

### Backfilling history

```bash
uv run murmur prices --from 2026-08-01 --to 2026-10-31   # bars for the range
uv run murmur news   --from 2026-08-01 --to 2026-10-31   # through finnhub/gdelt, a week at a time
uv run murmur analyze --all                               # score, then recompute every day
uv run murmur export --all
```

Yahoo has no date-range query, so `news --from` needs `finnhub` or `gdelt`
in `news.sources`; Finnhub's free plan reaches back one year, so that is the
practical horizon for news (prices go back decades). Scoring a backlog runs
FinBERT once per headline; the server does about eight a second. Headlines
already stored are never touched by a backfill: a headline both sources carried
is kept once with both sources recorded.

**Past months are reconstructed.** The index for a month in the past cannot
use today's caps, so each candidate's cap on that month's first trading day is
rebuilt from its shares outstanding then (the SEC's XBRL frames, public
domain; today's implied count when a company reports per share class) times
its close that day. Those months carry `reconstructed = true` in the index
dataset. The candidate pool is today's screener list, so a company that has
since shrunk below the floor or been delisted is missed.

Through the API (and the admin UI) the same thing is one `backfill` job: a
date range, the stages to run (any of `news`, `prices`, `sentiment`,
`analyze`, `export`, in pipeline order) and optionally `regenerate`, which
deletes the range's analysis rows first and rebuilds the months' files, so the
result is a clean slate rather than a merge. Articles are never deleted; a
re-fetched headline is deduplicated.

```bash
curl -s -X POST localhost:8100/api/v1/jobs -H "Authorization: Bearer $MURMUR_API_TOKEN" \
     -H 'content-type: application/json' \
     -d '{"type":"backfill","params":{"from":"2026-10-01","to":"2026-10-31","stages":["prices","analyze","export"],"regenerate":true},"confirm":true}'
```

### The daily job and the job queue

Three processes share one image and one data directory (0.8.0): `murmur
serve` is the API and the dashboard, `murmur scheduler` queues the daily run
on its own clock, and `murmur worker` is the only process that executes
**jobs**, one at a time (it is also the only process that runs FinBERT).
Exactly one worker per store: SQLite has one writer. Each process has its own
health check (the API answers HTTP, the other two write a heartbeat file every
10 seconds that `murmur health --role scheduler|worker` reads), so the
dashboard restarting never interrupts a job, and the scheduler never waits for
a busy worker. `murmur serve` without flags still runs all three in one
process for a laptop.

The daily run is a job the scheduler queues at 18:00 Eastern (every day,
weekends included); a failed run is re-queued after 20 minutes, up to 3 times.
If the scheduler starts after the slot and the day has no successful run yet,
the run is queued immediately, so a restart never loses a day. A worker
restart marks the job it was running `interrupted` and re-queues it when the
job is safe to repeat (backfills, analysis, exports, fetches); queued jobs are
kept.

With `schedule.analysis_time` set (for a price source whose bar for a day is
not available at 18:00, like Polygon's free plan) the day splits into two slots: at
`schedule.time` a **collect** run fetches, scores and publishes the day's
news, and at `analysis_time` (say 06:30) an **analyze** run fetches prices and
computes and publishes the analysis for the previous session. Each slot has
its own success record and retries. `POST /api/v1/scheduler/pause` and
`/resume` stop and restart the queueing (persisted in settings; manual jobs
still run). Each run also completes any trading day from the
past week whose analysis is still missing but whose price bars have since
arrived (Yahoo's bar for a day sometimes disappears for a while in the
evening), and re-analyses the previous session.

Everything else that takes time or changes data is a job too, so a manual
backfill can never collide with the daily run: `run` (`kind`: full, collect
or analyze), `backfill` (see above), `universe`, `news` and `prices` (one day
or a date range), `sentiment`, `analyze` (one day or all), `export` (one day,
all, or rebuild a month), `backup`, and the destructive
`delete_analysis`, `delete_scores` and `delete_universe_month`, which need
`confirm: true`. A job reports progress, keeps a log you can tail, and can be
cancelled. `murmur schedule` still exists as a scheduler-only fallback with
no HTTP and no jobs.

### Windows and returns

Every article is assigned to one trading day and one news window by its
publication time, on the NYSE calendar (holidays and early closes included):

| window | from | to |
|---|---|---|
| overnight(t) | previous session's close (16:00 ET) | today's open (09:30 ET) |
| intraday(t) | open | close |

News after the close, over a weekend or on a holiday belongs to the next
session's overnight window. "day" is both windows together. A ticker's
sentiment for a window is the mean of `positive − negative` over its headlines
in that window. Three returns are computed from the daily bars:

| return | formula |
|---|---|
| overnight | open_t / close_t−1 − 1 |
| intraday | close_t / open_t − 1 |
| daily | close_t / close_t−1 − 1 |

### Lead or lag

Every window is correlated with every return at three lags. The lag is the
return's session minus the news's session:

| lag | pairs | reads as |
|---|---|---|
| +1 | today's news, tomorrow's return | news **leads** prices (predicts) |
| 0 | today's news, today's return | news and prices move together |
| −1 | today's news, yesterday's return | news **follows** prices (reports) |

Two aggregations, both Pearson and Spearman: *daily* (across the universe on
one day, written to `correlations`) and *rolling* (as of each day, over every
ticker-day observation in the last 1, 3, 6 and 12 months, pooled and per
ticker, written to `rolling_correlations`). The rolling results are the
answer to the question; the daily ones are its noisy ingredients. Each run
also re-analyses the previous session, which only then has a tomorrow.
`leadlag.png` in each day's folder plots the pooled Pearson by lag for the
three matched pairs with confidence intervals.

**The shuffle test.** A pooled correlation can come from market-wide days alone
(good news on days the whole market rises). So each analysis also shuffles every
session's day sentiment across that session's companies 200 times and recomputes
the pooled r of day sentiment against the daily return, for each horizon and lag.
The shuffles keep each day's numbers and destroy only which company they belong
to. A real r above the shuffled range means the signal belongs to the right
company on the right day. The Findings page reports it; the
results are the `placebo` table of the `correlations` dataset and
`GET /api/v1/placebo`.

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync            # creates .venv with CPU-only torch on Linux
uv run murmur --version
```

The first sentiment run downloads FinBERT (about 440 MB) into the Hugging
Face cache (`HF_HOME` to relocate it).

## The web UI

`murmur serve` also serves the dashboard at `/`: static HTML and JavaScript
from the package, Plotly.js vendored, no build step, talking only to
`/api/v1` and `/data`. Light and dark themes, usable on a phone.

| page | what it shows |
|---|---|
| Overview (`/`) | what Murmur is, the six free datasets and what each contains, the question and the frozen answer, where to go next |
| Findings (`/findings`) | the frozen conclusion: the answer, r at lags −1/0/+1 with the shuffled range (all company-days, market move removed, day by day), the checks, every window × return, splits by size and source, caveats, the live last 12 months next to it, cite / embed / download |
| News vs. prices | horizon, method and source filters; the matched pairs by lag; a heatmap of every window × return × lag; the history of one cell; one session's per-company scatter |
| Companies | the covered list for a month; a company's sentiment, returns, close, its own lead/lag and latest headlines, optionally against a second company (`?vs=`) |
| Murmur 100 | the daily level against the S&P 500, constituents with rank and weight, changes, downloads |
| Headlines | every scored headline, searchable by words, company, tone, source and date |
| Data (`/downloads`) | a bundle of any date range (size estimate, curl/pandas/DuckDB snippets), monthly archives, the stable latest URLs, every file with its license and columns |
| Methodology | how every number is made, the checks, limitations, the dataset version, licenses, contact and takedown |
| Story | how the project started, who built what, the prototype and the rebuild |
| Status | services, the last two weeks of runs, the last 30 sessions' coverage |
| Admin (footer link) | sign in with the API token: jobs, backfill form, scheduler, every setting as a form, the process log |

Old addresses (`/leadlag`, `/tickers`, `/sources`, `/?findings=`) lead to their new pages.
`/embed/findings?id=&theme=` is the Findings chart alone, for an iframe; each page's
title and link-preview tags are filled in on the server. The mark is the "murmuration"
flock (`src/murmur/brand.py`; `scripts/make_brand.py` writes the SVG and PNG files).

The admin token is kept in the tab's session storage only. On the public
hostname, restrict `/admin` and non-GET requests to trusted networks at the
reverse proxy; the API enforces the token either way.

## The API

Every route and parameter is in [`docs/api.md`](docs/api.md) (generated from the
OpenAPI document; `/docs` on a running instance is the interactive version).

```
GET  /api/v1/health, /status, /latest, /logs
GET  /api/v1/health/report[?strict=true] problems + warnings for a watcher (503 with strict when not ok)
GET  /api/v1/runs[/{id}]                 the daily run log
GET  /api/v1/jobs[/{id}[/log?after=]]    the job queue; POST /api/v1/jobs {type, params, confirm}
DELETE /api/v1/jobs/{id}                 cancel; POST /api/v1/scheduler/run-now|pause|resume
GET  /api/v1/ui                          what the dashboard needs first: title, defaults, index, theme
GET  /api/v1/settings[/schema]           effective config with provenance; PUT /api/v1/settings
GET  /api/v1/datasets[/{name}]           the published datasets and their files
GET  /api/v1/index/m100[/months|/changes|/history/{key}]   the Murmur 100: constituents, weights
GET  /api/v1/index/m100/level[?from&to], /level/{date|latest}   the daily level; one session's contributions
GET  /api/v1/index/m100/benchmark[?from&to]                     S&P 500 closes for the chart (not published)
GET  /api/v1/companies[/{key}]           every company ever ranked, by ticker or CIK
GET  /api/v1/days[/{date}[/articles]]    per-day cross-section
GET  /api/v1/tickers[/{t}[/series|/articles|/leadlag]]
GET  /api/v1/correlations                the daily correlations as a series
GET  /api/v1/leadlag[/history]           the rolling results and their history
GET  /api/v1/placebo[?as_of=]            the shuffle test as of a session
GET  /api/v1/sources[/{s}/series]        source coverage and comparison
GET  /api/v1/charts/leadlag.png          the one chart the API draws
GET  /data/index.json, /data/<dataset>/YYYY/MM/DD/YYYYMMDD.<table>.parquet[?format=csv]
GET  /docs                               OpenAPI UI
GET  /api/v1/findings[/{id|latest|live}]  the frozen conclusions and the live 12 months
GET  /api/v1/bundle?from=&to=&datasets=   any date range as one .tar.gz
GET  /api/v1/articles?q=                 headline search
GET  /api/v1/citation[?findings=]        BibTeX, APA, CITATION.cff
GET  /data/<dataset>/latest.<table>.parquet, /data/findings/latest.json, /data/archives/
GET  /feed.xml                           Atom: weekly results and new findings
GET  /, /news-vs-prices, /companies/{t}, /index, /headlines, /downloads, /methodology, /status, /admin   the dashboard
GET  /embed/findings                     the Findings chart for an iframe
```

Reads need nothing. `POST`, `PUT` and `DELETE` need
`Authorization: Bearer <MURMUR_API_TOKEN>`; with no token set on the server
they answer 503. Settings written through the API land in
`<data_dir>/settings.toml`, a layer above the ops file and below the
environment; secrets and the data directory can only be set in the
environment.

```bash
curl -s localhost:8100/api/v1/latest | jq .matched_pairs
curl -s -X POST localhost:8100/api/v1/jobs -H "Authorization: Bearer $MURMUR_API_TOKEN" \
     -H 'content-type: application/json' -d '{"type":"news","params":{"from":"2026-10-01","to":"2026-10-31"}}'
curl -s localhost:8100/api/v1/jobs/12/log | jq -r .text
```

## Use

```bash
uv run murmur run                      # today: universe -> news -> prices -> sentiment -> analyze -> export
uv run murmur run --date 2026-11-04    # a specific day
uv run murmur universe                 # the list in force today (--rebuild to recompute the month)
uv run murmur news                     # one stage at a time
uv run murmur news --from 2026-10-01   # backfill a date range (finnhub/gdelt)
uv run murmur prices
uv run murmur analyze                  # scores pending headlines, then the day's analysis
uv run murmur analyze --all            # recompute every day (after a schema change)
uv run murmur export
uv run murmur runs                     # recent runs and which stage failed
uv run murmur serve                    # the API + UI, plus the scheduler and the worker in-process
uv run murmur serve --no-worker        # the API + UI only (the server's `murmur` service)
uv run murmur scheduler                # queues the daily run(s); --once ticks and exits
uv run murmur worker                   # runs jobs, one at a time; --once drains the queue and exits
uv run murmur offsite                  # the backup service: upload new snapshots to S3; --once
uv run murmur health --role worker     # exit 0 if that process's heartbeat is fresh (api|scheduler|worker|backup)
uv run murmur schedule                 # the pre-0.6.0 scheduler loop alone, no HTTP (fallback)
uv run murmur -v run                   # debug logging
```

`run` prints one line per stage and exits 1 if any stage raised.

## Configure

Defaults live in `src/murmur/default_config.toml`. Copy
`murmur.example.toml` to `./murmur.toml` (gitignored), or point `--config` or
`MURMUR_CONFIG` at a file, and set only what you change. `MURMUR_DATA_DIR`
overrides `data_dir`. Every key, its default and the environment variables are in
[`docs/configuration.md`](docs/configuration.md).

```toml
data_dir = "data"              # store at data/murmur.sqlite, exports at data/published/
timezone = "America/New_York"

[news]
max_articles = 20              # per ticker per fetch (Yahoo)
sources = ["yahoo"]            # any of "yahoo", "finnhub", "gdelt"

[prices]
source = "yfinance"            # or "polygon" (needs schedule.analysis_time)
splits = "auto"                # split ratios: polygon when its key is set; "polygon" or "none"

[finnhub]
api_key = ""                   # or MURMUR_FINNHUB_API_KEY

[polygon]
api_key = ""                   # or MURMUR_POLYGON_API_KEY

[sentiment]
model = "ProsusAI/finbert"
batch_size = 32
device = "cpu"                 # "cuda" or "auto" with a GPU build of torch

[sec]
contact = "you@example.com"    # required by the SEC for the monthly ticker list

[index]
name = "m100"                  # the published cap-weighted list: "m100" -> Murmur 100
size = 100

[universe]
source = "marketcap"           # or "static" for the fixed list below
size = 100                     # coverage: who gets news and sentiment (<= index.size)

[schedule]
time = "18:00"                 # in `timezone`, every day
analysis_time = ""             # e.g. "06:30": prices + analysis for the previous session
paused = false
retries = 3
retry_delay_minutes = 20

[ui]
title = "Murmur"
tagline = "Does news predict prices, follow them, or move with them?"
default_months = 3             # 1, 3, 6 or 12
default_method = "pearson"
theme = "auto"                 # "light" or "dark"

[datasets]
private = ["prices"]           # served only with the API token; flagged public = false

[universe.static]              # used by source = "static"; replaces the packaged list wholesale
AAPL = "Apple"
"BRK-B" = "Berkshire Hathaway" # SEC/Yahoo form: a dash for share classes
```

Tickers everywhere use the SEC and Yahoo form with a dash (`BRK-B`).

## Data

`data/murmur.sqlite` is the source of truth. `murmur export` writes the
published datasets under `data/published/` (served at `/data/`);
`murmur export --all` rebuilds the whole tree from the store. Six datasets,
each its own directory, every file named after its date and table. Every table and
column is described in [`docs/data-dictionary.md`](docs/data-dictionary.md):

```
index.json                                        the manifest: datasets, tables, columns, keys, licenses, files
companies/2026/11/202611.companies.parquet        every company ever ranked: CIK, ticker, name, exchange, country
m100/2026/11/202611.constituents.parquet          the Murmur 100: month, effective date, rank, CIK, ticker, cap, weight
m100/2026/11/202611.changes.parquet               companies added and removed versus the previous month
m100/2026/11/05/20261105.level.parquet            the daily level (base 1000) and the index's return
m100/2026/11/05/20261105.contributions.parquet    each member's weight, return and contribution that day
prices/2026/11/05/20261105.prices.parquet         OHLCV bar plus prev_close, overnight, intraday, daily
news/2026/11/05/20261105.articles.parquet         one row per headline x company, sources, window, FinBERT scores
sentiment/2026/11/05/20261105.sentiment.parquet   per company x window x source: headline count, mean score
correlations/2026/11/05/20261105.daily.parquet    the day's correlations (window x return x lag x source)
correlations/2026/11/05/20261105.rolling.parquet  1/3/6/12-month pooled results as of the day
correlations/2026/11/202611.rolling_tickers.parquet  per-company rolling results as of the month's last session
correlations/2026/11/05/20261105.leadlag.png      + sentiment.png, correlation.png
prices/2026/10/202610.prices.parquet              a finished month, compacted into one file per table
```

Daily files are `{dataset}/{YYYY}/{MM}/{DD}/{YYYYMMDD}.{table}.parquet`;
monthly files and compacted months are
`{dataset}/{YYYY}/{MM}/{YYYYMM}.{table}.parquet`. Once a month is over its
day files are folded into the month file (deduplicated by the table's key) and
removed, so a glob never double counts; the charts stay in their day
directory. A table with no rows is never written. Every row that names a
company carries both `cik` and `ticker`. Any Parquet URL answers as CSV with
`?format=csv`; nothing is stored twice.

```python
import pandas as pd

m100 = pd.read_parquet("http://192.168.1.10:8100/data/m100/2026/11/202611.constituents.parquet")
news = pd.read_parquet("data/published/news/2026/11/")  # a whole month from the tree
```

**The Murmur 100.** The universe is published as a cap-weighted index: the
`index.size` largest US-domiciled companies on NYSE/Nasdaq, rebalanced on the
first trading day of each month, each with `weight = market_cap / total`.
Coverage (`universe.size`, who gets news and sentiment) is the top of the
index and may be smaller than it; a Murmur 250 is `[index] name = "m250"`,
`size = 250` in the config.

**Licenses.** Stated per dataset in `index.json`, stamped into every Parquet
footer (`license`, `license_url`, `attribution`, `notice`, `contact`) and sent as
`Link`/`X-Murmur-*` headers on every `/data` response, so a CSV download carries
them too. Everything Murmur compiles or computes is CC BY 4.0: `companies`, `m100`,
`correlations`, and also `news` and `sentiment`. The scores in those two come from
FinBERT, whose CC BY-NC-SA license governs the model weights (run non-commercially,
never redistributed) and not the model's outputs, which is Creative Commons' own
position; the notice credits the model. `prices` (bars and returns) carries **no
license from Murmur**: the bars are Yahoo Finance's facts collected under their
terms and republished as-is for non-commercial research; use remains subject to
the source's terms and the exchanges' policies, and a takedown contact is in the
notice. Market caps are rounded to three significant figures and weights to four
decimals everywhere, so the index cannot be read back into prices.

## Release and deploy

A release is a tag. After the version bump and `CHANGELOG.md` entry are
merged, run `./scripts/release.sh X.Y.Z` from an up-to-date `main`: it checks
everything, pushes `vX.Y.Z`, and the release workflow runs the suite, builds
the image, pushes it to `ghcr.io/gabebecker2048/murmur` as `X.Y.Z` and
`latest`, and creates the GitHub release from the changelog entry. Pull
requests and merges only build and smoke-test the image.

The server pulls `latest` every morning, so a release is what deploys.
`docker-compose.yml` runs four services from the one image, `murmur` (the API
and UI, port 8100 on the LAN, 512 MB), `murmur-scheduler` (256 MB),
`murmur-worker` (2 GB, a 60 s stop grace period for a job in flight) and
`murmur-backup` (256 MB, see below), and is
written for the server's conventions: a stack directory with `./data` (store,
exports, API-written settings, heartbeats, Hugging Face cache) and
`./config/murmur.toml` (ops overrides, may be empty, read-only), uid 1000,
read-only root filesystems, and `.env` holding `MURMUR_API_TOKEN` and the
source keys. Each service has its own health check; the scheduler and worker
wait for the API to be healthy because it creates and migrates the store.

```bash
mkdir -p ~/murmur/data/offsite ~/murmur/config && cd ~/murmur
touch config/murmur.toml                      # or set overrides
curl -O https://raw.githubusercontent.com/GabeBecker2048/murmur/main/docker-compose.yml
docker login ghcr.io -u <github user>            # once; the package is private
docker compose pull && docker compose up -d
docker compose logs -f murmur-worker          # first job downloads FinBERT into ./data/hf-cache
docker compose exec murmur murmur runs        # the run log
```

### Backups and monitoring

Every day at `backup.time` (04:30) the scheduler queues a `backup` job: the
worker copies the store with SQLite's online backup API (the DB is in WAL mode,
so never `cp` it), checks its integrity and keeps `backup.keep` (3) snapshots in
`data/backups`. The `murmur-backup` service (`murmur offsite`) uploads each new
snapshot, gzipped with its SHA-256 in the object metadata, to
`s3://$MURMUR_S3_BUCKET/<backup.prefix>/store/`, plus `settings.toml`, and
checks the size the bucket reports. It never opens the store: its data mount
is read-only except `data/offsite` (status, heartbeat, staging). It is the only
service that reads `s3.env`:

```bash
MURMUR_S3_BUCKET=...         # unset: the service idles and the report says "not configured"
MURMUR_S3_REGION=us-east-2
AWS_ACCESS_KEY_ID=...        # an IAM user allowed s3:PutObject, s3:GetObject (for the size
AWS_SECRET_ACCESS_KEY=...    #   check) and s3:ListBucket on the prefix; nothing else
# MURMUR_S3_ENDPOINT=...     # for an S3-compatible provider
```

Old copies are deleted by a lifecycle rule on the bucket (30 days), so the key
cannot delete anything. The published tree is not uploaded: `murmur export
--all` regenerates it from the store. To restore: download a snapshot,
`gunzip`, check `sha256sum` against the object's `sha256` metadata, stop the
stack, put it at `data/murmur.sqlite` (remove any `-wal`/`-shm`), start.
Once a week the service also runs a restore drill on the newest copy (download, SHA-256
check, `PRAGMA integrity_check`, open read-only); `murmur offsite --verify` runs it on
demand and the health report flags a failure. `deploy/murmur-backup.sh` is the separate host-side snapshot (cron, 04:40, to
the server's storage disk, 14 kept).

`GET /api/v1/health/report` is one JSON document for a watcher: `problems`
(a stale scheduler, worker or backup heartbeat, a daily run that has no success
after its retries, a snapshot older than a day, an offsite copy older than
`backup.max_age_hours`, under 5 GiB free) and `warnings` (failed or interrupted
jobs in the last 24 h, the scheduler paused, offsite not configured).
`deploy/murmur-check.sh` runs from cron on the host at 09:05 and 20:15 Eastern
and sends any problems through the homelab's `ntfy-send`; the
containers hold no notification token. `MURMUR_LOG_FORMAT=json` makes the
process log one JSON object per line.

## Contributing

Work happens on branches and lands through pull requests; `main` is never
pushed to directly. Pull requests are squash-merged (the title and body become
the commit) and the branch is deleted on merge. Only the maintainers (Gabe,
Wyatt, Sam) can merge. Releases are tags cut from `main` with
`scripts/release.sh`.

## Develop

```bash
uv sync --all-groups
uv run ruff check . && uv run ruff format --check .
uv run pytest
MURMUR_TEST_MODEL=1 uv run pytest tests/test_sentiment.py   # also runs the real FinBERT
```

Tests never hit the network or download the model. CI runs lint and tests on
every push and pull request.
