"""What leaves the building: per-dataset license notices, the metadata stamped into
every published file, and the rounding applied to market caps and weights before
they are served or exported.

Everything Murmur compiles or computes is CC BY 4.0, including the tables that carry
FinBERT scores: the model's CC BY-NC-SA license governs the weights, not their
outputs. Prices (with the returns computed from them) are someone else's facts
collected under their terms, so that table carries no license from Murmur, only a
notice. Caps and weights are rounded
so a published index can never stand in for a close: a cap to three significant
figures and a weight to four decimals recover a price to about a tenth of a percent
at best, the precision of a newspaper, not of a feed.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from murmur import __version__
from murmur.clock import utc_now_iso

DATA_VERSION = "1.0.0"
"""The version of the published data, separate from the software's. Semantic: a major
bump when a column or table is removed or renamed, or a method change alters values
already published; a minor bump for a new column, table or dataset; a patch for a fix.
Stamped into every file (``murmur_data_version``), the manifest and every findings
snapshot. ``docs/data-changelog.md`` records each version; ``docs/stability.md`` is the
promise; ``tests/test_schema_stability.py`` enforces it."""

CAP_SIG_FIGS = 3
WEIGHT_DECIMALS = 4
ROUNDED_KEYS = ("market_cap", "weight")

CC_BY_URL = "https://creativecommons.org/licenses/by/4.0/"
YAHOO_TERMS_URL = "https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html"
MURMUR = "Murmur (github.com/GabeBecker2048/murmur)"


def cc_by(contact: str) -> dict:
    return {
        "name": "CC BY 4.0",
        "url": CC_BY_URL,
        "attribution": MURMUR,
        "note": (
            "Murmur's compilation, analysis and derived figures, licensed CC BY 4.0: use, share "
            "and adapt freely with attribution. Market caps and weights are rounded "
            f"(caps to {CAP_SIG_FIGS} significant figures, weights to {WEIGHT_DECIMALS} decimals)."
        ),
        "contact": contact,
    }


def cc_by_scored(contact: str) -> dict:
    """The tables that carry FinBERT scores: CC BY 4.0 on Murmur's compilation. The model
    is CC BY-NC-SA 4.0, but that license governs the weights, which Murmur runs
    non-commercially and does not redistribute; it does not extend to the model's
    outputs (Creative Commons' own position on model outputs), and a score is factual
    data with no authorship of the model's publisher in it."""
    return {
        "name": "CC BY 4.0",
        "url": CC_BY_URL,
        "attribution": f"{MURMUR}; scores produced with ProsusAI/finbert",
        "note": (
            "Murmur's compilation, licensed CC BY 4.0. Sentiment scores were produced with "
            "FinBERT (ProsusAI/finbert, CC BY-NC-SA 4.0); the model is not redistributed and "
            "its license does not extend to its outputs. Headlines and URLs are quoted for "
            "identification; they remain their publishers'."
        ),
        "contact": contact,
    }


def source_terms(contact: str, source: str = "Yahoo Finance via yfinance") -> dict:
    """The prices table: no license from Murmur, the source's terms apply."""
    return {
        "name": "Source terms apply (not openly licensed)",
        "url": YAHOO_TERMS_URL,
        "attribution": f"Prices: {source} (unofficial, as-is)",
        "note": (
            "Murmur grants no rights in this table. The bars are collected from "
            f"{source} and republished for non-commercial research; use remains subject "
            "to the source's terms and the exchanges' data policies. Returns and split "
            "adjustments are computed by Murmur from those bars. No warranty; not official "
            "exchange data. Takedown requests: see contact."
        ),
        "contact": contact,
    }


def file_metadata(dataset: str, table: str, license: Mapping, extra: Mapping | None = None) -> dict:
    """The key/value metadata written into every Parquet footer, so provenance and
    terms travel with the file."""
    meta = {
        "murmur_dataset": dataset,
        "murmur_table": table,
        "murmur_version": __version__,
        "murmur_data_version": DATA_VERSION,
        "murmur_generated_at": utc_now_iso(),
        "license": str(license.get("name", "")),
        "license_url": str(license.get("url", "")),
        "attribution": str(license.get("attribution", "")),
        "notice": str(license.get("note", "")),
        "contact": str(license.get("contact", "")),
    }
    if extra:
        meta.update({str(k): str(v) for k, v in extra.items()})
    return meta


# -- rounding ------------------------------------------------------------------


def round_cap(value: float | int | None) -> int | None:
    if value is None:
        return None
    value = float(value)
    if value == 0 or math.isnan(value):
        return 0
    digits = CAP_SIG_FIGS - int(math.floor(math.log10(abs(value)))) - 1
    return int(round(value, digits))


def round_weight(value: float | None) -> float | None:
    return None if value is None else round(float(value), WEIGHT_DECIMALS)


def public(obj: Any) -> Any:
    """Round ``market_cap`` and ``weight`` wherever they appear in a JSON-shaped value."""
    if isinstance(obj, Mapping):
        out = {}
        for k, v in obj.items():
            if k == "market_cap":
                out[k] = round_cap(v)
            elif k == "weight":
                out[k] = round_weight(v)
            else:
                out[k] = public(v)
        return out
    if isinstance(obj, list):
        return [public(x) for x in obj]
    return obj
