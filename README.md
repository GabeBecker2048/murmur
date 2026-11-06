# Murmur

Does news predict prices, follow them, or move with them?

Murmur is a daily pipeline that scores news-headline sentiment for the largest
US companies, stores it next to their daily prices, and correlates the two. It
is built to run every day on a server and to publish the data and the findings.
`ROADMAP.md` has the full design and the order things ship in.

**Status: 0.3.0, pre-public.** The pipeline runs daily on the server with a
market-cap universe of the 100 largest US companies. Headlines are bucketed
into overnight and intraday news windows on the NYSE calendar, three returns
are computed from open and close, and the published dataset is Parquet with
monthly compaction. The lead/lag analysis (0.4.0), more sources, the API and
the web UI arrive in later versions. The repository stays private until the
original contributors agree to publish it, and the code is never published to
PyPI.

## How it works

For one day, `murmur run` does six stages in order and keeps going if one
fails:

| stage | what it does | source | table |
|---|---|---|---|
| universe | the companies in the index that day (built once a month, see below) | SEC + Yahoo screener | `universe_months`, `universe_membership` |
| news | recent headlines for every ticker in the universe | Yahoo Finance via yfinance | `articles`, `article_tickers` |
| prices | daily OHLCV bars for the last month, one request for all tickers | yfinance | `prices` |
| sentiment | scores every headline not yet scored | FinBERT (`ProsusAI/finbert`), CPU | `sentiment` |
| analyze | windows for new articles, the three returns, per-window sentiment, Pearson + Spearman across tickers per window | scipy | `returns`, `window_sentiment`, `correlations` |
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

### The daily job

`murmur schedule` runs the pipeline at 18:00 Eastern every day including
weekends. A failed run is retried (3 more times, 20 minutes apart, by
default). If the process starts after the slot and the day has no successful
run yet, it runs immediately, so a restart never loses a day. It writes a
heartbeat file every minute that `murmur health` checks, and stops cleanly on
SIGTERM. Each run also completes any trading day from the past week whose
analysis is still missing but whose price bars have since arrived (Yahoo's
bar for a day sometimes disappears for a while in the evening).

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

0.3.0 correlates each window with its matching return at lag 0 (overnight
with overnight, intraday with intraday, day with daily) across the universe,
Pearson and Spearman. The lags and rolling windows that answer the lead/lag
question are 0.4.0.

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync            # creates .venv with CPU-only torch on Linux
uv run murmur --version
```

The first sentiment run downloads FinBERT (about 440 MB) into the Hugging
Face cache (`HF_HOME` to relocate it).

## Use

```bash
uv run murmur run                      # today: universe -> news -> prices -> sentiment -> analyze -> export
uv run murmur run --date 2026-11-04    # a specific day
uv run murmur universe                 # the list in force today (--rebuild to recompute the month)
uv run murmur news                     # one stage at a time
uv run murmur prices
uv run murmur analyze                  # scores pending headlines, then the day's analysis
uv run murmur export
uv run murmur runs                     # recent runs and which stage failed
uv run murmur schedule                 # the daily job, forever
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
max_articles = 20              # per ticker per fetch

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
2026/11/05/articles.parquet             the trading day's articles, with window
2026/11/05/prices.parquet               OHLCV bars
2026/11/05/returns.parquet              overnight, intraday, daily per ticker
2026/11/05/sentiment.parquet            per-headline scores with ticker and window
2026/11/05/window_sentiment.parquet     per-ticker, per-window mean sentiment and returns
2026/11/05/correlations.parquet         the day's correlations (window x return)
2026/11/05/sentiment.png, correlation.png
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
directory with `./data` (store, exports, heartbeat, Hugging Face cache) and
`./config/murmur.toml` (may be empty), uid 1000, read-only root filesystem,
no ports.

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
