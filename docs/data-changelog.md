# Data changelog

Every version of the published data (the `murmur_data_version` footer key and the
manifest's `data_version`). How versions are numbered and what stays stable:
[stability.md](stability.md). Software releases are in `CHANGELOG.md`.

## 1.0.0 - 2026-11 (Murmur 0.9.0)

The first versioned release of the published tree; everything published before it is
the same data without the version stamp.

- Datasets: `companies`, `m100` (the Murmur 100: constituents, changes, level,
  contributions), `prices`, `news`, `sentiment`, `correlations` (daily, rolling,
  placebo, monthly rolling_tickers). Columns are listed in
  [data-dictionary.md](data-dictionary.md).
- New: `findings/<id>.json`, frozen findings snapshots, and `findings/latest.json`.
- New footer key `murmur_data_version`; new manifest keys `data_version` and `findings`.
