# Quick start: reading Murmur's data

Everything below uses the public endpoints of a Murmur server; replace `$MURMUR` with
its address (for example `https://murmur.gabebecker.com`). What each column means:
[data-dictionary.md](data-dictionary.md).

## The answer, in one request

```bash
curl -s $MURMUR/data/findings/latest.json | python3 -m json.tool | head -40
```

`headline.lags` holds the correlation of day headlines with the daily return at lags
-1 (news follows), 0 (same session) and +1 (news leads); `market_adjusted`,
`per_session` and `placebo` are the checks behind it; `text` is the prose.

## The latest session

These URLs always hold the newest session (or month) of a table, whatever file it lives
in (`Content-Location` names the dated file):

```python
import pandas as pd

base = "https://murmur.gabebecker.com/data"
articles = pd.read_parquet(f"{base}/news/latest.articles.parquet")
sentiment = pd.read_parquet(f"{base}/sentiment/latest.sentiment.parquet")
index = pd.read_parquet(f"{base}/m100/latest.constituents.parquet")
```

Add `?format=csv` for CSV.

## A date range

One `.tar.gz` with one file per table, the licenses and checksums:

```bash
curl -o murmur.tar.gz "$MURMUR/api/v1/bundle?from=2026-01-01&to=2026-06-30&datasets=sentiment,prices"
tar -xzf murmur.tar.gz
```

`datasets` and `tables` are optional (all public ones by default), `format=csv` is
allowed (up to 93 days of `news`). `&dry_run=true` returns what would be built and an
estimated size. Every finished month is also ready-made at
`/data/archives/murmur-YYYYMM.tar.gz` (listed in `/data/index.json` under `archives`).

## Straight from the files with DuckDB

DuckDB reads Parquet over HTTP without downloading whole files first:

```sql
INSTALL httpfs; LOAD httpfs;

-- same-session correlation of each day, from the daily correlation files of October
SELECT date, estimate AS r, n
FROM read_parquet('https://murmur.gabebecker.com/data/correlations/2026/10/202610.daily.parquet')
WHERE "window" = 'day' AND return_type = 'daily' AND lag = 0
  AND source = 'all' AND method = 'pearson'
ORDER BY date;
```

The manifest lists every file, so a script can find what to read:

```python
import requests

manifest = requests.get("https://murmur.gabebecker.com/data/index.json").json()
files = [f["path"] for f in manifest["datasets"]["sentiment"]["files"]]
```

## Joining

Sentiment and returns join on `ticker` + `date` (`trading_day` in `news/articles`).
`cik` is the durable company identity across ticker changes. A lag of +1 pairs the
headlines of session *t* with the return of the next trading session, not the next
calendar day: use the session order in `prices`, not date arithmetic.

## Licenses and citing

`companies`, `m100`, `correlations` and the findings are CC BY 4.0; `news`, `sentiment`
and `prices` follow their sources' terms. Every file says so in its
Parquet footer. To cite: `CITATION.cff` in the repository, or
`$MURMUR/api/v1/citation` (`?findings=latest` for the findings).
