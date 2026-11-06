# Murmur

Does news predict prices, follow them, or move with them?

Murmur is a daily pipeline that scores news-headline sentiment for the largest
US companies, stores it next to their daily prices, and correlates the two. It
is built to run every day on a server and to publish the data and the findings.
`ROADMAP.md` has the full design and the order things ship in.

**Status: 0.6.0, pre-public.** One process serves an HTTP API, runs the daily
job and executes queued work: `/api/v1` answers queries about days, tickers,
sources and the lead/lag results, runs and cancels jobs (runs, backfills,
rebuilds, deletions), and edits settings; `/data` serves the published Parquet
tree; `/docs` is the OpenAPI UI. The pipeline underneath is unchanged: a
market-cap universe of the 100 largest US companies, headlines from Yahoo,
Finnhub and GDELT, prices from yfinance or Polygon, overnight/intraday windows
on the NYSE calendar, three returns, and correlations at lags −1, 0 and +1
daily and over rolling horizons, per source and pooled. The web UI is 0.7.0.
The repository stays private until the original contributors agree to publish
it, and the code is never published to PyPI.

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
in `news.sources`. Scoring a backlog runs FinBERT once per headline; the
server does about eight a second.

### The daily job and the job queue

`murmur serve` is the process: it serves the API and runs one worker thread
that executes **jobs** one at a time. The daily run is a job the worker queues
itself at 18:00 Eastern (every day, weekends included); a failed run is
re-queued after 20 minutes, up to 3 times. If the process starts after the
slot and the day has no successful run yet, the run is queued immediately, so
a restart never loses a day. Each run also completes any trading day from the
past week whose analysis is still missing but whose price bars have since
arrived (Yahoo's bar for a day sometimes disappears for a while in the
evening), and re-analyses the previous session.

Everything else that takes time or changes data is a job too, so a manual
backfill can never collide with the daily run: `run`, `universe`, `news` and
`prices` (one day or a date range), `sentiment`, `analyze` (one day or all),
`export` (one day, all, or rebuild a month), `backup`, and the destructive
`delete_analysis`, `delete_scores` and `delete_universe_month`, which need
`confirm: true`. A job reports progress, keeps a log you can tail, and can be
cancelled. `murmur schedule` still exists as a scheduler-only fallback with
no HTTP.

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

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync            # creates .venv with CPU-only torch on Linux
uv run murmur --version
```

The first sentiment run downloads FinBERT (about 440 MB) into the Hugging
Face cache (`HF_HOME` to relocate it).

## The API

```
GET  /api/v1/health, /status, /latest, /logs
GET  /api/v1/runs[/{id}]                 the daily run log
GET  /api/v1/jobs[/{id}[/log?after=]]    the job queue; POST /api/v1/jobs {type, params, confirm}
DELETE /api/v1/jobs/{id}                 cancel; POST /api/v1/scheduler/run-now
GET  /api/v1/settings[/schema]           effective config with provenance; PUT /api/v1/settings
GET  /api/v1/universe[/months]           the companies in force on a date or month
GET  /api/v1/days[/{date}[/articles]]    per-day cross-section
GET  /api/v1/tickers[/{t}[/series|/articles|/leadlag]]
GET  /api/v1/correlations                the daily correlations as a series
GET  /api/v1/leadlag[/history]           the rolling results and their history
GET  /api/v1/sources[/{s}/series]        source coverage and comparison
GET  /api/v1/charts/leadlag.png          the one chart the API draws
GET  /data/index.json, /data/YYYY/MM/DD/<table>.parquet
GET  /docs                               OpenAPI UI
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
uv run murmur serve                    # the API + daily job + job worker (the server's main process)
uv run murmur schedule                 # the scheduler alone, no HTTP (fallback)
uv run murmur -v run                   # debug logging
```

`run` prints one line per stage and exits 1 if any stage raised.

## Configure

Defaults live in `src/murmur/default_config.toml`. Copy
`murmur.example.toml` to `./murmur.toml` (gitignored), or point `--config` or
`MURMUR_CONFIG` at a file, and set only what you change. `MURMUR_DATA_DIR`
overrides `data_dir`.

```toml
data_dir = "data"              # store at data/murmur.sqlite, exports at data/published/
timezone = "America/New_York"

[news]
max_articles = 20              # per ticker per fetch (Yahoo)
sources = ["yahoo"]            # any of "yahoo", "finnhub", "gdelt"

[prices]
source = "yfinance"            # or "polygon"

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

[universe]
source = "marketcap"           # or "static" for the fixed list below
size = 100

[schedule]
time = "18:00"                 # in `timezone`, every day
retries = 3
retry_delay_minutes = 20

[universe.static]              # used by source = "static"; replaces the packaged list wholesale
AAPL = "Apple"
"BRK-B" = "Berkshire Hathaway" # SEC/Yahoo form: a dash for share classes
```

Tickers everywhere use the SEC and Yahoo form with a dash (`BRK-B`).

## Data

`data/murmur.sqlite` is the source of truth. `murmur export` writes a trading
day's files under `data/published/`, date first, one Parquet file per table;
`murmur export --all` rebuilds the whole tree from the store.

```
index.json                              every file: table, date, path, format, rows
2026/11/universe.parquet                the month's universe (rank, ticker, name, market cap, CIK)
2026/11/05/articles.parquet             the trading day's articles, with window and reporting sources
2026/11/05/prices.parquet               OHLCV bars
2026/11/05/returns.parquet              overnight, intraday, daily per ticker
2026/11/05/sentiment.parquet            per-headline scores with ticker and window
2026/11/05/window_sentiment.parquet     per-ticker, per-window mean sentiment and returns, per source
2026/11/05/correlations.parquet         the day's correlations (window x return x lag x source)
2026/11/05/rolling_correlations.parquet rolling 1/3/6/12-month results as of the day, pooled/per ticker, per source
2026/11/05/sentiment.png, correlation.png, leadlag.png
2026/10/prices.parquet ...              a finished month, compacted into one file per table
```

Once a month is over its daily files are folded into one file per table in
the month directory (a later backfill is merged in on the next export); the
charts stay in the day directories. A file with no rows is never written, and
articles with no publication time are stored but not exported or analysed.
`pandas.read_parquet` or DuckDB read the tree directly.

## Release and deploy

A release is a tag. After the version bump and `CHANGELOG.md` entry are
merged, run `./scripts/release.sh X.Y.Z` from an up-to-date `main`: it checks
everything, pushes `vX.Y.Z`, and the release workflow runs the suite, builds
the image, pushes it to `ghcr.io/gabebecker2048/murmur` as `X.Y.Z` and
`latest`, and creates the GitHub release from the changelog entry. Pull
requests and merges only build and smoke-test the image.

The server pulls `latest` every morning, so a release is what deploys.
`docker-compose.yml` is written for the server's conventions: a stack
directory with `./data` (store, exports, API-written settings, Hugging Face
cache) and `./config/murmur.toml` (ops overrides, may be empty, read-only),
uid 1000, read-only root filesystem, port 8100 on the LAN, a 60 s stop grace
period for a job in flight, and `.env` holding `MURMUR_API_TOKEN` and the
source keys.

```bash
mkdir -p ~/murmur/data ~/murmur/config && cd ~/murmur
touch config/murmur.toml                      # or set overrides
curl -O https://raw.githubusercontent.com/GabeBecker2048/murmur/main/docker-compose.yml
docker login ghcr.io -u <github user>            # once; the package is private
docker compose pull && docker compose up -d
docker compose logs -f                        # first run downloads FinBERT into ./data/hf-cache
docker compose exec murmur murmur runs        # the run log
```

`deploy/murmur-backup.sh` snapshots the SQLite file with the online backup
API (the DB is in WAL mode, so never `cp` it), checks integrity and keeps the
newest 14 copies. The full offsite backup is 0.8.0.

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

## Contributors

- Gabe Becker ([@GabeBecker2048](https://github.com/GabeBecker2048))
- Wyatt Burch-Celentano ([@wyatthbc](https://github.com/wyatthbc))
- Sam Chapoton ([@samchapoton](https://github.com/samchapoton))

## License

Code is MIT, see `LICENSE`. Published data will carry its own license (see
`ROADMAP.md`).
