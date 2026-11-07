"""The generated docs/ pages match the code, and every published column is described."""

import importlib.util
from pathlib import Path

from murmur.config import load_config
from murmur.dictionary import COLUMNS, describe
from murmur.export import datasets

ROOT = Path(__file__).resolve().parents[1]


def _gen_docs():
    spec = importlib.util.spec_from_file_location("gen_docs", ROOT / "scripts" / "gen_docs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_published_column_is_described(tmp_path):
    user = tmp_path / "c.toml"
    user.write_text('[universe]\nsource = "static"\n[universe.static]\nA = "A"\n')
    missing = [
        f"{ds.name}.{t.name}.{c}"
        for ds in datasets(load_config(user, env={}))
        for t in ds.tables
        for c in t.columns
        if not describe(t.name, c)
    ]
    assert missing == []


def test_no_stale_descriptions(tmp_path):
    user = tmp_path / "c.toml"
    user.write_text('[universe]\nsource = "static"\n[universe.static]\nA = "A"\n')
    used = {c for ds in datasets(load_config(user, env={})) for t in ds.tables for c in t.columns}
    assert set(COLUMNS) - used == set()


def test_docs_are_up_to_date():
    # if this fails: uv run python scripts/gen_docs.py
    assert _gen_docs().main(["--check"]) == 0
