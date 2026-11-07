"""One line per published column, for the data dictionary (``docs/data-dictionary.md``).

Column names mean the same thing in every table they appear in; where a table needs a
qualifier, ``TABLE_COLUMNS`` overrides the shared text. ``tests/test_docs.py`` fails if
a published column has no description here.
"""

from __future__ import annotations

COLUMNS: dict[str, str] = {
    # identity
    "cik": "SEC Central Index Key: the durable company identifier (tickers change).",
    "ticker": "Ticker in the SEC/Yahoo dash form (`BRK-B`), as of the row's date or month.",
    "name": "Company name.",
    "exchange": "Listing exchange (NYSE or Nasdaq); empty for months built before 0.7.0.",
    "country": "Country of domicile (the index keeps United States only).",
    "first_month": "First index month (YYYY-MM) the company was ranked in.",
    "last_month": "Last index month (YYYY-MM) the company was ranked in, up to the snapshot.",
    "months": "Number of months (companies: months ranked; correlations: the rolling horizon).",
    "snapshot_month": "The month this file describes the universe up to.",
    # the index
    "month": "Index month (YYYY-MM).",
    "effective_date": "First trading day of the month: the day the month's list applies from.",
    "rank": "Rank by market cap within the month (1 = largest).",
    "market_cap": "Market cap in USD on the month's first trading day, 3 significant figures.",
    "weight": "Share of the index: market cap over the month's total (4 decimals).",
    "reconstructed": "True when the month was rebuilt after the fact from SEC share counts "
    "times that day's close.",
    "action": "`added` or `removed` versus the previous month on record.",
    "level": "Index level at the session's close (base level at the close of the base session).",
    "return": "Daily return (close over the previous close, split-adjusted), as a fraction.",
    "constituents": "Members held during the session.",
    "priced": "Members with a daily return that session (0: no prices; the level is unchanged).",
    "weight_priced": "Share of the start-of-day weight that had a return that session.",
    "rebalance": "True on a month's first session: the new weights take effect at its close.",
    "contribution": "Start-of-day weight times return: the member's part of the index return.",
    "points": "Contribution in index points (contribution times the previous level).",
    # prices
    "date": "NYSE trading day (YYYY-MM-DD).",
    "open": "Opening price, USD.",
    "high": "High price, USD.",
    "low": "Low price, USD.",
    "close": "Closing price, USD (not adjusted).",
    "adj_close": "Close adjusted for splits and dividends by the price source.",
    "volume": "Shares traded.",
    "prev_date": "The previous trading day the returns are measured from.",
    "prev_close": "The previous session's close, split-adjusted when `split_ratio` is set.",
    "split_ratio": "Split ratio executed on this date (new shares per old), if any.",
    "overnight": "Overnight return: open over the previous close, minus 1.",
    "intraday": "Intraday return: close over open, minus 1.",
    "daily": "Daily return: close over the previous close, minus 1.",
    # news and sentiment
    "article_id": "Murmur's id for the headline (stable within a published file).",
    "title": "The headline as published.",
    "url": "Link to the article.",
    "publisher": "Publisher named by the source.",
    "content_type": "Item type reported by the source (e.g. STORY, VIDEO), when given.",
    "sources": "Every news source that reported the headline, comma-separated.",
    "published_at": "Publication time, UTC (YYYY-MM-DDTHH:MM:SSZ); the earliest seen.",
    "first_seen_at": "When Murmur first collected the headline, UTC.",
    "trading_day": "NYSE trading day the headline is assigned to.",
    "window": "`overnight` (previous close to open; weekends and holidays fold into the next "
    "session), `intraday` (open to close) or `day` (both).",
    "positive": "FinBERT probability that the headline is positive.",
    "negative": "FinBERT probability that the headline is negative.",
    "neutral": "FinBERT probability that the headline is neutral.",
    "label": "The most probable of positive, negative, neutral.",
    "score": "positive minus negative, from -1 to +1.",
    "model": "The sentiment model that produced the scores (Hugging Face id).",
    "n_articles": "Headlines in the window.",
    "mean_score": "Mean `score` of the window's headlines.",
    "source": "Where the row comes from: the price source (prices) or the news source the "
    "result is restricted to, `all` for every source pooled.",
    # correlations
    "return_type": "`overnight`, `intraday` or `daily` return.",
    "lag": "Return session minus news session: +1 = news before the return (leads), 0 = same "
    "session, -1 = news after the return (follows).",
    "scope": "`pooled` across companies (daily results are always pooled).",
    "method": "`pearson` or `spearman`.",
    "n": "Number of (company, session) pairs.",
    "estimate": "The correlation coefficient.",
    "p_value": "Two-sided p-value of the correlation.",
    "ci_low": "Lower bound of the 95 % confidence interval (Fisher z; Pearson only).",
    "ci_high": "Upper bound of the 95 % confidence interval (Fisher z; Pearson only).",
    "tickers": "The companies paired that day, comma-separated.",
    "computed_at": "When the row was computed, UTC.",
    "as_of": "The session the rolling window ends on.",
    "real_r": "The pooled Pearson r of day sentiment and the return.",
    "null_mean": "Mean r over the shuffles (each session's sentiment shuffled across its "
    "companies).",
    "null_sd": "Standard deviation of r over the shuffles.",
    "null_low": "2.5th percentile of the shuffled r.",
    "null_high": "97.5th percentile of the shuffled r.",
    "z": "(real_r - null_mean) / null_sd.",
    "p_perm": "Permutation p-value: share of shuffles with |r| at least |real_r| "
    "(with the +1 correction).",
    "permutations": "Number of shuffles.",
}

TABLE_COLUMNS: dict[tuple[str, str], str] = {
    ("contributions", "weight"): "Start-of-day weight: the month's weight drifted with the "
    "member's returns since the last rebalance (4 decimals).",
    ("contributions", "return"): "The member's daily return that session (empty: no price).",
    ("level", "return"): "The index's return that session.",
}


def describe(table: str, column: str) -> str | None:
    return TABLE_COLUMNS.get((table, column)) or COLUMNS.get(column)
