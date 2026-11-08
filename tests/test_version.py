"""The version comes from pyproject.toml alone."""

import tomllib
from pathlib import Path

import murmur


def test_version_matches_pyproject():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    assert murmur.__version__ == tomllib.loads(pyproject.read_text())["project"]["version"]
