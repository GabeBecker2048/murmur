# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Murmur: a daily pipeline that scores news-headline sentiment (FinBERT) for the largest US
companies, stores it with daily prices in SQLite, and correlates sentiment with returns.
`ROADMAP.md` is the design document and is authoritative on scope: read its "Decisions"
section before changing schema, timing, sources or the published layout. `CHANGELOG.md`
records what shipped. The repo is private and is never published to PyPI
(`Private :: Do Not Upload` classifier).

## Commands

```bash
uv sync --all-groups                      # .venv with CPU-only torch (Linux) + dev tools
uv run murmur run [--date YYYY-MM-DD]     # news -> prices -> sentiment -> analyze -> export
uv run murmur news|prices|analyze|export  # one stage
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
  except `[universe.static]`, which a user file replaces wholesale.
- `store.py` SQLite. `MIGRATIONS` is keyed by `PRAGMA user_version`; add a new entry for a
  schema change, never edit a shipped one. All writes are upserts. Article upserts keep the
  earliest `published_at` and never overwrite `first_seen_*`.
- `clock.py` the only place dates become timestamps. `window_bounds(day, tz)` gives the UTC
  bounds of a local calendar day; 0.3.0 replaces it with overnight/intraday windows.
- `sources/` `base.py` has the `NewsSource`/`PriceSource` protocols and `yahoo_symbol()`
  (`BRK.B` -> `BRK-B`; tickers are stored SEC-style). `yahoo_news.py` parses
  `Ticker.get_news()` items; `yfinance_prices.py` parses one `yf.download()` frame for all
  tickers and drops today's partial bar before 16:05 ET.
- `universe.py` membership for a day, recorded in `universe_membership`. 0.1.0 only has
  the static list; 0.2.0 adds the market-cap source without changing anything downstream.
- `sentiment.py` `FinBertScorer` imports torch/transformers inside `__init__`;
  `score_pending()` scores articles with no row for the configured model.
- `analysis.py` per-ticker sentiment for the day, `close/prev_close - 1`, join **by ticker**,
  Pearson (+CI) and Spearman. A day with no price bars is "not a trading day": sentiment is
  stored, correlation skipped, not a failure.
- `export.py` CSVs in the roadmap's published layout, `index.json`, two matplotlib PNGs
  (`Agg`, imported lazily).

## Rules that tests enforce

- `murmur.cli`, `murmur.pipeline`, the sources and `murmur.export` must import without
  loading torch, transformers or matplotlib (`tests/test_sentiment.py`).
- Sentiment/price pairing is by ticker (`tests/test_analysis.py`, companies whose names
  sort differently from their tickers).
- Reruns are idempotent: same article count, nothing rescored (`tests/test_pipeline.py`).
- Tests use `FakeScorer` (`tests/conftest.py`), fake sources and the Yahoo payload fixture
  in `tests/fixtures/`; they never touch the network.

## Gotchas

- `date` columns are calendar dates in `config.timezone` (America/New_York);
  `published_at` is UTC `YYYY-MM-DDTHH:MM:SSZ`. Compare timestamps as text; never bucket in
  SQL.
- The analysis uses `config.sentiment.model` to select score rows, so an injected scorer
  must have the same `model_name` (the pipeline raises otherwise).
- `torch` resolves from the PyTorch CPU index on Linux only (`[tool.uv.sources]`); after
  changing dependencies check `uv.lock` has no `nvidia-*` packages.
- A `[universe.static]` table in a user config replaces the packaged 50 tickers.

## Other branches

`main` before `release/0.1.0` was the 2024 Python + R + Tkinter tool (see git history). Two
stale remote branches predate the rewrite and are not a base for anything:
`origin/dockerized` (Feb 2025, standalone stage scripts + a Hugging Face sentiment model)
and `origin/SentiStreet_rework` (Sep 2025, unfinished rename).
