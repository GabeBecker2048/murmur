# Murmur

Does news predict prices, follow them, or move with them?

Murmur is a daily pipeline that scores news-headline sentiment for the largest
US companies, stores it next to their daily prices, and correlates the two. It
is built to run every day on a server and to publish the data and the findings.
`ROADMAP.md` has the full design and the order things ship in.

**Status: 0.1.0, pre-public.** This version is the package: pure Python, a
SQLite store, a CLI, tests and CI. The universe is a fixed list of 50 tickers;
the market-cap universe, the scheduler and the published API arrive in later
versions. The repository stays private until the original contributors agree
to publish it, and the code is never published to PyPI.

## How it works

For one day, `murmur run` does five stages in order and keeps going if one
fails:

| stage | what it does | source | table |
|---|---|---|---|
| news | recent headlines for every ticker in the universe | Yahoo Finance via yfinance | `articles`, `article_tickers` |
| prices | daily OHLCV bars for the last month, one request for all tickers | yfinance | `prices` |
| sentiment | scores every headline not yet scored | FinBERT (`ProsusAI/finbert`), CPU | `sentiment` |
| analyze | per-ticker mean sentiment for the day, daily return, Pearson + Spearman across tickers | scipy | `daily_sentiment`, `correlations` |
| export | the day's tables as CSV plus two charts and `index.json` | | `<data_dir>/published/` |

Every write is an upsert, so running a day twice stores an article once and
never rescores it. Dates are calendar days in `America/New_York`; article
timestamps are UTC. A day without price bars (weekend, holiday) stores
sentiment and skips the correlation without failing.

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
uv run murmur run                      # today: news -> prices -> sentiment -> analyze -> export
uv run murmur run --date 2026-11-04    # a specific day
uv run murmur news                     # one stage at a time
uv run murmur prices
uv run murmur analyze                  # scores pending headlines, then the day's analysis
uv run murmur export
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

[universe]
source = "static"              # 0.2.0 adds "marketcap"

[universe.static]              # replaces the packaged list wholesale
AAPL = "Apple"
"BRK.B" = "Berkshire Hathaway" # SEC-style tickers; sources map to their own symbols
```

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
