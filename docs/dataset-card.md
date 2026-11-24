---
# Hugging Face dataset card header, for the 1.0 mirror (the layout on HF will match /data).
pretty_name: Murmur
license: other  # CC BY 4.0 for Murmur's own tables; source terms for news, sentiment, prices
language:
  - en
task_categories:
  - text-classification
  - time-series-forecasting
tags:
  - finance
  - sentiment-analysis
  - news
  - stock-returns
  - finbert
size_categories:
  - 100K<n<1M
---

# Murmur: news-headline sentiment and stock returns

A daily dataset of news headlines about the largest US-listed companies, each scored for
sentiment with FinBERT, next to the companies' daily stock returns, the correlation
between the two at a one-session lead and lag, and a cap-weighted index of the
companies (the Murmur 100). It exists to answer one question: **does news predict
prices, follow them, or move with them?**

This card follows the structure of *Datasheets for Datasets* (Gebru et al.). How to read
the files: [quickstart.md](quickstart.md). Every column: [data-dictionary.md](data-dictionary.md).
What stays stable: [stability.md](stability.md). Versions: [data-changelog.md](data-changelog.md).

## Motivation

- **Purpose.** To measure, every trading day and in the open, how the tone of news about
  a company relates to its stock's move: before it (news leads), the same session, or
  after it (news follows).
- **Creators.** The Murmur contributors (see `CITATION.cff`). Unfunded, non-commercial.

## Composition

- **Companies.** The Murmur 100: the 100 largest US-domiciled companies listed on NYSE or
  Nasdaq by market cap, one ticker per company (the SEC's CIK is the identity),
  rebalanced on each month's first trading day. Foreign companies trading through ADRs
  and US-listed companies domiciled abroad (such as Chubb) are excluded.
- **Instances.**
  - `news/articles`: one row per headline × company it was reported for: title, URL,
    publisher, publication time (UTC), the trading day and window it belongs to, the
    sources that carried it, and FinBERT's positive/negative/neutral probabilities.
  - `sentiment`: per company, trading day, window (overnight, intraday, day) and source,
    the mean score (positive − negative) and headline count.
  - `prices`: the daily bar and three returns (overnight, intraday, daily).
  - `correlations`: daily cross-company correlations, rolling 1/3/6/12-month results,
    the shuffle test, per-company rolling results (monthly).
  - `m100`: constituents with weights, monthly changes, the daily level and each
    company's contribution. `companies`: every company ever ranked.
  - `findings/<id>.json`: frozen conclusions over a window, with their robustness checks.
- **Size.** About 250,000 headlines a year at 100 companies; the full Parquet tree is
  about 120 MB a year.
- **Coverage.** From 3 November 2025. Headlines from Finnhub (back to that date) and,
  from 5 November 2026, Yahoo Finance. Headlines only, never article bodies.
- **Missing data.** A day whose price bar is missing has no returns or correlations; a
  company with no headlines in a window has no sentiment row for it.
- **Personal data.** None. Headlines are about companies; no individual is a subject.

## Collection

- **Headlines.** Finnhub's company-news API (by ticker and date) and Yahoo Finance's
  per-ticker feed (the latest ~20 headlines; collected daily, not recoverable for past
  days). An article seen by several sources is stored once (same URL, or the same
  normalised title within two days), keeping the earliest publication time.
- **Prices.** Daily bars from Yahoo Finance (via yfinance), with split adjustment.
- **Universe.** The SEC's public ticker list and Yahoo's equity screener; headquarters
  country from Yahoo. Months before October 2026 were **reconstructed**: market caps on
  each month's first trading day rebuilt from the SEC's XBRL share counts × that day's
  close, from today's candidate list (`reconstructed = true` in the index).
- **Timing.** The pipeline runs every day at 18:00 America/New_York. Each headline is
  assigned to a trading day and window on the NYSE calendar: *overnight* is the previous
  close to the open (weekends and holidays fold into the next session), *intraday* is
  open to close, *day* is both.

## Preprocessing

- Sentiment: FinBERT (`ProsusAI/finbert`) on the headline text, on CPU; the score is
  P(positive) − P(negative), from −1 to +1.
- Returns: overnight = open / previous close − 1, intraday = close / open − 1,
  daily = close / previous close − 1.
- Correlations: Pearson (with a 95 % Fisher interval) and Spearman, joined by ticker; lag
  = the return's session minus the headlines' session.
- Market caps are rounded to 3 significant figures and weights to 4 decimals in
  everything published.

## Uses

- **Intended.** Research and teaching on news, sentiment and markets; reproducing or
  challenging the findings; building and testing other sentiment models against the
  same headlines.
- **Not intended.** Trading. The effect measured is small (r ≈ 0.16 on the same day) and
  does not predict the next session; nothing here is investment advice.

## Known limitations and biases

- **Survivorship.** Reconstructed months use today's list of large companies, so firms
  that shrank or were delisted during the year are missing.
- **Source mix.** The first year is almost entirely Finnhub; Yahoo joins on
  5 November 2026, so per-source and pooled figures change character there.
- **Headlines, not articles.** FinBERT reads the headline only; a neutral headline can
  sit on top of a market-moving article.
- **Daily bars.** Windows are cut at the 09:30 and 16:00 bells; a headline at 15:59 and
  the move it describes may straddle them.
- **Model.** FinBERT was trained on financial text from before this period; its errors
  are not random (sarcasm, negation, numbers).
- **Correlation is not causation.**

## Distribution and licenses

- Served from `/data/` as Parquet (CSV on request with `?format=csv`), as monthly
  `.tar.gz` archives and as range bundles (`/api/v1/bundle`). `index.json` is the
  manifest.
- **CC BY 4.0** for Murmur's own work: companies, the index, correlations and findings.
- **Source terms apply** to `news` and `sentiment` (headlines from Finnhub and Yahoo
  Finance, scored with FinBERT) and to `prices` (Yahoo Finance via yfinance).
  Republished for non-commercial research, as-is. Takedown available by request.
- Every file carries its license, notice and contact in its Parquet footer.

## Maintenance

- Updated every trading day; finished months are compacted and archived.
- Versioned: the data version is in every file. Breaking changes are announced in
  [data-changelog.md](data-changelog.md) at least 90 days ahead.
- **Contact:** gabebecker2048@gmail.com (also the `contact` field in every file's
  footer), or the repository's issue tracker.
