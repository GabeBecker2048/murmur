# Murmur

Does news predict prices, follow them, or move with them?

Murmur is a daily pipeline that scores news-headline sentiment for the largest
US companies, stores it next to their daily prices, and correlates the two. It
is built to run every day on a server and to publish the data and the findings.
`ROADMAP.md` has the full design and the order things ship in.

**Status: 0.2.0, pre-public.** The pipeline runs daily: a market-cap universe
of the 100 largest US companies rebalanced monthly, a scheduler with retries
and a run log, and a Docker image for the server. Timing windows, lead/lag
analysis, the API and the web UI arrive in later versions. The repository
stays private until the original contributors agree to publish it, and the
code is never published to PyPI.

## How it works

For one day, `murmur run` does six stages in order and keeps going if one
fails:

| stage | what it does | source | table |
|---|---|---|---|
| universe | the companies in the index that day (built once a month, see below) | SEC + Yahoo screener | `universe_months`, `universe_membership` |
| news | recent headlines for every ticker in the universe | Yahoo Finance via yfinance | `articles`, `article_tickers` |
| prices | daily OHLCV bars for the last month, one request for all tickers | yfinance | `prices` |
| sentiment | scores every headline not yet scored | FinBERT (`ProsusAI/finbert`), CPU | `sentiment` |
| analyze | per-ticker mean sentiment for the day, daily return, Pearson + Spearman across tickers | scipy | `daily_sentiment`, `correlations` |
| export | the day's tables as CSV plus two charts and `index.json` | | `<data_dir>/published/` |

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
SIGTERM.

In 0.1.0 the day's sentiment for a ticker is the mean of `positive − negative`
over its headlines published that calendar day, and the return is
`close / previous close − 1`. The overnight/intraday windows and the lead/lag
analysis are 0.3.0 and 0.4.0.

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

`data/murmur.sqlite` is the source of truth. `murmur export` writes the day's
files under `data/published/` in the layout the roadmap fixes for the public
dataset (CSV now, Parquet from 0.3.0):

```
index.json
universe/2026-11.csv
articles/2026/11/05.csv
prices/2026/11/05.csv
sentiment/2026/11/05.csv
analysis/daily/2026/11/05.csv              correlations for the day
analysis/daily/2026/11/05-tickers.csv      per-ticker sentiment and return
analysis/daily/2026/11/05-sentiment.png
analysis/daily/2026/11/05-correlation.png
```

## Deploy

The image is built by CI and pushed to `ghcr.io/gabebecker2048/murmur` on
every push to `main` (and `vX.Y.Z` tags). `docker-compose.yml` is written for
the server's conventions: a stack directory with `./data` (store, exports,
heartbeat, Hugging Face cache) and `./config/murmur.toml` (may be empty),
uid 1000, read-only root filesystem, no ports.

```bash
mkdir -p ~/murmur/data ~/murmur/config && cd ~/murmur
touch config/murmur.toml                      # or set overrides
curl -O https://raw.githubusercontent.com/GabeBecker2048/murmur/main/docker-compose.yml
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
