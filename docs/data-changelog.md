# Data changelog

The versions of Murmur's published data (the `murmur_data_version` footer key and the
manifest's `data_version`), and any correction made to published values. What stays
stable: [stability.md](stability.md). Software releases are in `CHANGELOG.md`.

## 1.0.0

The dataset as published on day one of the public release.

- Datasets: `companies`, `m100` (the Murmur 100: constituents, changes, level,
  contributions), `prices`, `news`, `sentiment`, `correlations` (daily, rolling,
  placebo, monthly rolling_tickers). Every column is in
  [data-dictionary.md](data-dictionary.md).
- Frozen findings snapshots at `findings/<id>.json`, the newest at `findings/latest.json`.
- Monthly archives at `archives/murmur-YYYYMM.tar.gz`.
