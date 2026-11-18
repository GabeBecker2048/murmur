# Stability

What you can rely on when you build on Murmur's published data. This file is written by
hand (unlike the generated pages next to it).

## One version: v1.0.0

The published dataset is **v1.0.0**: the datasets, tables, columns and keys described in
[data-dictionary.md](data-dictionary.md), as published on the first day of the public
release. Every file carries the version in its Parquet footer (`murmur_data_version`),
and so do the manifest (`/data/index.json`, `data_version`) and every findings snapshot
(`meta.data_version`).

v1.0.0 does not change shape. New days are added every trading day; no column, table,
dataset or key is added, removed or renamed within it. If the schema ever has to change,
the whole dataset is republished as a new version (v2.0.0) that replaces v1.0.0, announced
in [data-changelog.md](data-changelog.md) ahead of time, with what changes and how to
move over.

## The promise

Within v1.0.0:

- **Paths stay where they are.** Daily files are
  `{dataset}/{YYYY}/{MM}/{DD}/{YYYYMMDD}.{table}.parquet`; monthly files and finished
  months are `{dataset}/{YYYY}/{MM}/{YYYYMM}.{table}.parquet`, and each finished month is
  also an archive at `archives/murmur-YYYYMM.tar.gz`.
- **Columns, types and keys stay.** Read columns by name; their meaning is fixed.
- **The stable URLs keep working**: `/data/index.json`,
  `/data/{dataset}/latest.{table}.parquet` (with `?format=csv`),
  `/data/findings/latest.json`, `/api/v1/bundle`, and the `/api/v1` routes in
  [api.md](api.md).
- **Frozen findings never change.** A findings snapshot (`/data/findings/<id>.json`),
  once published, keeps every number and word; a new conclusion is a new snapshot.
- **Corrections are announced.** If published values are ever wrong (a bad price bar, a
  day analysed twice), they are fixed in place and the fix is listed in the data
  changelog.

## Not covered

- Rows of the latest sessions: the next session's analysis adds the lag +1 pairs to the
  previous session and late headlines to its article file.
- The `live` findings row and `/api/v1` response fields not documented in
  [api.md](api.md) or [data-dictionary.md](data-dictionary.md).
- The web dashboard's layout and any address other than the stable ones above.

The repository enforces this with `tests/test_schema_stability.py`, which fails if a
published dataset, table, column or key changes without a new version.
