"""What leaves the building: per-dataset license notices, the metadata stamped into
every published file, and the rounding applied to market caps and weights before
they are served or exported.

Murmur's own work (the companies list, the index, the correlations and the findings)
is CC BY 4.0. Headlines, the sentiment computed from them and prices come from
third-party sources, so those tables carry a short "source terms apply" notice
instead of a license from Murmur (the same treatment for all three).
Caps and weights are rounded
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
"""The version of the published data (shown as v1.0.0), separate from the software's. A
version's schema never changes: if a column, table or dataset is ever added, removed or
renamed, the whole dataset is republished as the next major version, replacing this one.
Stamped into every file (``murmur_data_version``), the manifest and every findings
snapshot. ``docs/stability.md`` is the promise; ``tests/test_schema_stability.py``
enforces it."""

CAP_SIG_FIGS = 3
WEIGHT_DECIMALS = 4
ROUNDED_KEYS = ("market_cap", "weight")

CC_BY_URL = "https://creativecommons.org/licenses/by/4.0/"
TERMS_URL = "https://github.com/GabeBecker2048/murmur/blob/main/docs/dataset-card.md#distribution-and-licenses"
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


NEWS_SOURCES = "Finnhub and Yahoo Finance"
PRICE_SOURCE = "Yahoo Finance via yfinance"


def source_terms(
    contact: str, what: str = "prices", source: str = PRICE_SOURCE, credit: str = ""
) -> dict:
    """The tables built from third-party data (headlines, their sentiment, prices): no
    license from Murmur; the sources' terms apply and a takedown is available by
    request. Kept short on purpose."""
    return {
        "name": "Source terms apply",
        "url": TERMS_URL,
        "attribution": f"{what.capitalize()}: {source}{credit}; compiled by {MURMUR}",
        "note": (
            f"The {what} come from {source}, whose terms apply. Republished for "
            "non-commercial research, as-is. Takedown available by request."
        ),
        "contact": contact,
    }


def news_terms(contact: str) -> dict:
    """The headline and sentiment tables."""
    return source_terms(
        contact, "headlines", NEWS_SOURCES, "; sentiment scores produced with ProsusAI/finbert"
    )


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
