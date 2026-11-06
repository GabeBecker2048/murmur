# Roadmap

What is planned, in order. Dates are intentions, not commitments, and the
contents of a version may change before it ships. Shipped versions will be in
`CHANGELOG.md`.

The project becomes **Murmur**: a daily pipeline that scores news
sentiment for the largest US companies, correlates it with their stock returns
at a one-day lead and lag, and publishes the data and the findings. The
question it exists to answer: **does news predict prices, follow them, or
move with them?**

The pipeline runs on Gabe's server. Every day it is not running is a day of
history lost, so the order below gets a daily job collecting as early as
possible and builds the analysis and UI on top of a growing store.

## Decisions

These are settled. Each milestone is built against them.

**Universe.** Our own index, not a licensed one: the 100 largest US-listed
companies by market cap. Rebalanced on the first trading day of each month.
Membership is recorded per day, so any past day's universe can be
reconstructed. The monthly universe file lists each company's ticker, name,
market cap and rank. Ticker/CIK list from the SEC's public-domain
`company_tickers.json`; market cap from the price source.

**Timing.** All timestamps stored in UTC. Trading days follow the NYSE
calendar. Each trading day *t* has two news windows:

- *overnight(t)*: previous close (16:00 ET, day *t−1*) to today's open
  (09:30 ET). Weekend and holiday news falls into the next trading day's
  overnight window.
- *intraday(t)*: open to close.

And three returns: *overnight* (open_t / close_t−1 − 1), *intraday*
(close_t / open_t − 1), and *daily* (close_t / close_t−1 − 1). The lead/lag
question is answered by correlating each return with each window at lags
−1, 0 and +1. The daily job runs after the close (18:00 ET) every day,
including weekends, so news is captured close to publication.

**Store.** SQLite is the operational store: articles, prices, universe
membership, sentiment scores, and analysis results. It makes reruns
idempotent (an article seen twice is stored once). Parquet files are
*exported* from it for publishing; they are never the source of truth. The
server's disk is the source of truth; offsite storage is a backup.

**Published files.** One Parquet file per day per table, compacted monthly
into one file per month. A `index.json` lists what exists. CSV alongside
Parquet for the small tables (universe, daily correlations). Layout, served
by the API and later mirrored unchanged to Hugging Face:

```
data/
  index.json
  universe/2026-11.csv
  articles/2026/11/05.parquet      articles/2026/11.parquet (after compaction)
  prices/2026/11/05.parquet
  sentiment/2026/11/05.parquet
  analysis/daily/2026/11/05.csv
  analysis/rolling/latest.csv
```

Store URLs and headlines, not article bodies. Expected growth: about 1 GB a
year at 100 tickers.

**Sources.** Every article carries a `source` column, and correlations are
computed per source as well as pooled. Articles are deduplicated across
sources by normalized title, keeping the earliest `published_at` and a
`first_seen_source`. Sources sit behind a `NewsSource` interface; prices
behind a `PriceSource` interface. Starting set: Yahoo per-ticker news and
yfinance prices (what works today), then Finnhub, GDELT, Polygon. Reddit and
SEC 8-K filings are candidates after 1.0.

**Sentiment.** One finance-tuned model (FinBERT) scores every headline
regardless of source, so sources are comparable. A source's own score, where
it has one (GDELT tone), is kept as an extra column.

**Pure Python.** R is removed entirely in 0.1.0. The three R scripts map to:
sentiment analysis → FinBERT via `transformers`; the correlation test →
`scipy.stats`; the graphs → Plotly in the web UI, with `matplotlib` only for
PNGs exported alongside the data. Nothing in the pipeline shells out.

**Analysis.** Pearson and Spearman, both reported. Two aggregations: *pooled*
(all tickers over a window) and *per-ticker* (one ticker over a window).
Windows: 1, 3, 6 and 12 months, recomputed daily and stored, so the UI reads
results rather than computing them.

**Delivery.** One FastAPI process serves the bulk files (`/data/...`), the
query API (`/api/v1/...`) and the web UI. The UI is static HTML/JS in the
package with Plotly.js vendored, and uses only the public query API.

**Deployment.** One Docker container running the scheduler and the server,
with a compose file, one volume for the store and published files, and a
nightly rsync of that volume to offsite storage.

**Licensing.** Code is MIT. The repository stays private until the original
contributors agree to publishing. Data, when published, gets its own license
(CC BY-NC 4.0 is the current intent). Sources used under personal-use terms
are replaced before any public release.

## 0.1.0 - the package

- `src/murmur/` with `pyproject.toml`, `LICENSE` (MIT), `CHANGELOG.md`,
  ruff, pytest, and a CI workflow that runs the tests.
- The existing pipeline ported into the package: news via Yahoo per-ticker
  news, prices via yfinance, sentiment via FinBERT, written to the SQLite
  store. The Tkinter GUI and the R scripts are removed.
- The correlation join is keyed on ticker (the current row-position join
  pairs companies with the wrong scores), with a test that proves it.
- A `murmur` CLI: `run` (one full day), `news`, `prices`, `analyze`,
  `export`.
- Fixed universe for now: the tickers in today's `settings.json`.

## 0.2.0 - running daily

- The universe module: SEC ticker list, market-cap ranking, monthly rebalance,
  per-day membership, the monthly universe file.
- The scheduler: the daily job at 18:00 ET, with a market calendar, retries,
  and a log of each run.
- Dockerfile and compose file; deployed to the server. **From here the store
  grows every day.**

## 0.3.0 - timing and returns

- Articles bucketed into overnight/intraday windows by `published_at`.
- Overnight, intraday and daily returns computed from open/close.
- The Parquet exporter with the published layout, daily files and monthly
  compaction, and `index.json`.

## 0.4.0 - lead/lag analysis

- Correlations at lags −1, 0, +1 for each return/window pair, Pearson and
  Spearman, pooled and per-ticker, over 1/3/6/12-month rolling windows.
- Results stored and exported. This is the version that can first say
  something about the central question, once enough days have accumulated.

## 0.5.0 - sources

- `NewsSource` and `PriceSource` interfaces; Finnhub and GDELT news; Polygon
  prices (grouped daily bars, one request for the whole market).
- Cross-source deduplication and per-source correlations.

## 0.6.0 - the API

- FastAPI: `/data/...` serves the published files, `/api/v1/...` answers
  queries (latest correlations, a ticker's series, rolling results, universe
  for a date, run status). OpenAPI docs served by the app.
- The scheduler moves into the same process.

## 0.7.0 - the web UI

- Static HTML/JS with Plotly.js, served by the API. Pages: overview (the
  lead/lag chart and today's numbers), lead/lag explorer, ticker detail,
  sources comparison, universe, downloads.
- Light and dark themes, usable on a phone.

## 0.8.0 - hardening

- Nightly offsite backup of the data volume (Backblaze B2 or S3).
- Health endpoint, structured logs, read-only container with the data volume
  as the only writable path.
- A `docs/` folder: configuration, API reference, data dictionary.

## 1.0.0 - public release

- Only after the original contributors have agreed to publishing.
- Personal-use sources replaced; data license added; dataset card written.
- Hugging Face mirror: the daily job pushes the day's files; the layout on
  HF matches `/data/`. A Zenodo DOI for the 1.0 snapshot.
- README leads with the lead/lag chart and the current answer to the question.

## After 1.0

- Reddit and SEC 8-K as sources.
- A larger universe (250, then 500) once the pipeline is boring.
- Intraday prices, if a source allows it, to tighten the windows.
