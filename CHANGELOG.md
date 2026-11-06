# Changelog

All notable changes to Murmur. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/). Planned versions are in `ROADMAP.md`.

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
