# Stability

What you can rely on when you build on Murmur's published data, and how changes are
announced. This file is written by hand (unlike the generated pages next to it).

## Versions

The data has its own version, separate from the software's. Every file carries it in its
Parquet footer (`murmur_data_version`), and so do the manifest (`/data/index.json`,
`data_version`) and every findings snapshot (`meta.data_version`). It follows semantic
versioning:

| Change | Version |
|---|---|
| a column, table or dataset removed or renamed; a key changed; a method change that alters values already published | **major** (2.0.0) |
| a new column, table or dataset | **minor** (1.1.0) |
| a fix that does not change the schema (wrong values corrected, a missing day filled) | **patch** (1.0.1) |

Every version is listed, with what changed, in [data-changelog.md](data-changelog.md).

## The promise

Within one major version:

- **Paths stay where they are.** Daily files are
  `{dataset}/{YYYY}/{MM}/{DD}/{YYYYMMDD}.{table}.parquet`; monthly files and finished
  months are `{dataset}/{YYYY}/{MM}/{YYYYMM}.{table}.parquet`. A finished month is
  compacted into its monthly file once and is then only rewritten if its data are
  corrected (a patch version).
- **Columns and keys stay.** No published column is removed or renamed, no type
  changes, and every table keeps its key. New columns may appear; read columns by name,
  not by position.
- **The stable URLs keep working**: `/data/index.json`, `/data/{dataset}/latest.{table}.parquet`
  (with `?format=csv`), `/data/findings/latest.json`, and the `/api/v1` routes listed in
  [api.md](api.md).
- **Frozen findings never change.** A findings snapshot (`/data/findings/<id>.json`),
  once frozen, keeps every number and word; new conclusions are new snapshots.

A major version is announced in the data changelog **at least 90 days** before it is
published, with what breaks and how to migrate.

## Not covered

- Rows of the current month and of the latest days: the next session's analysis adds
  lag +1 pairs to the previous session and late headlines to its article file.
- The `live` findings row and every `/api/v1` response field not documented in
  [api.md](api.md) or [data-dictionary.md](data-dictionary.md).
- The web dashboard's layout and URLs other than the stable ones above.

The promise is enforced in the repository by `tests/test_schema_stability.py`, which
fails when a published column, table or key disappears without a major version.
