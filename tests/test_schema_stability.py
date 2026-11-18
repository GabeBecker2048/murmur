"""The schema-stability promise (docs/stability.md), enforced.

``tests/fixtures/published_schema.json`` records every published dataset, table, column
and key of the dataset version ``DATA_VERSION`` (v1.0.0). The schema of a version never
changes: any addition, removal or rename means a whole new dataset version that replaces
the old one. To publish one:

1. bump ``murmur.publish.DATA_VERSION`` to the next major (2.0.0),
2. add the version to ``docs/data-changelog.md``,
3. ``uv run python tests/test_schema_stability.py --write`` (refuses without step 1).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "published_schema.json"
CHANGELOG = Path(__file__).parent.parent / "docs" / "data-changelog.md"


def current_schema() -> dict:
    from murmur.config import load_config
    from murmur.export import datasets, table_cadence

    config = load_config(None, env={})
    return {
        ds.name: {
            t.name: {
                "columns": list(t.columns),
                "key": list(t.key),
                "cadence": table_cadence(ds, t),
            }
            for t in ds.tables
        }
        for ds in datasets(config)
    }


def _major(version: str) -> int:
    return int(version.split(".")[0])


def removals(old: dict, new: dict) -> list[str]:
    out = []
    for ds, tables in old.items():
        for table, spec in tables.items():
            cur = new.get(ds, {}).get(table)
            if cur is None:
                out.append(f"{ds}/{table} removed")
                continue
            out += [f"{ds}/{table}.{c} removed" for c in spec["columns"] if c not in cur["columns"]]
            if cur["key"] != spec["key"]:
                out.append(f"{ds}/{table} key changed {spec['key']} -> {cur['key']}")
            if cur["cadence"] != spec["cadence"]:
                out.append(f"{ds}/{table} cadence changed")
    return out


def test_nothing_published_is_removed_within_a_major_version():
    from murmur.publish import DATA_VERSION

    recorded = json.loads(FIXTURE.read_text())
    gone = removals(recorded["datasets"], current_schema())
    if gone and _major(DATA_VERSION) == _major(recorded["data_version"]):
        raise AssertionError(
            "published schema broken within data version "
            f"{recorded['data_version']} (bump the major, see docs/stability.md): {gone}"
        )


def test_the_schema_matches_its_recorded_data_version():
    from murmur.publish import DATA_VERSION

    recorded = json.loads(FIXTURE.read_text())
    assert recorded["datasets"] == current_schema(), (
        "the published schema changed: that is a new dataset version (bump DATA_VERSION to "
        "the next major, note it in docs/data-changelog.md, then run "
        "`uv run python tests/test_schema_stability.py --write`)"
    )
    assert recorded["data_version"] == DATA_VERSION, (
        "DATA_VERSION changed: run `uv run python tests/test_schema_stability.py --write`"
    )


def test_every_data_version_is_in_the_changelog():
    from murmur.publish import DATA_VERSION

    assert f"## {DATA_VERSION}" in CHANGELOG.read_text(), (
        f"docs/data-changelog.md has no section for data version {DATA_VERSION}"
    )


def write() -> int:
    from murmur.publish import DATA_VERSION

    schema = current_schema()
    if FIXTURE.exists():
        recorded = json.loads(FIXTURE.read_text())
        if recorded["datasets"] != schema and recorded["data_version"] == DATA_VERSION:
            print(
                f"refusing: the schema changed but DATA_VERSION is still {DATA_VERSION}; "
                "bump it first (docs/stability.md)",
                file=sys.stderr,
            )
            return 1
    FIXTURE.write_text(
        json.dumps({"data_version": DATA_VERSION, "datasets": schema}, indent=1) + "\n"
    )
    print(f"wrote {FIXTURE} for data version {DATA_VERSION}")
    return 0


if __name__ == "__main__":
    sys.exit(write() if "--write" in sys.argv[1:] else 0)
