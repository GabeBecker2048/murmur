"""The SEC's public-domain ticker/CIK list (``company_tickers.json``).

SEC fair-access rules require a User-Agent naming a contact, so the fetch
refuses to run without ``[sec] contact`` in the config.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass

from murmur import __version__

log = logging.getLogger(__name__)

URL = "https://www.sec.gov/files/company_tickers.json"


@dataclass(frozen=True)
class SecCompany:
    cik: int
    ticker: str
    name: str


def parse_company_tickers(payload: dict) -> dict[str, SecCompany]:
    """``{"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}``
    -> ``ticker -> SecCompany``. Tickers are upper-case with dashes for share
    classes (``BRK-B``), which is also Yahoo's form."""
    out: dict[str, SecCompany] = {}
    for entry in payload.values():
        ticker = str(entry["ticker"]).upper().strip()
        if ticker:
            out[ticker] = SecCompany(int(entry["cik_str"]), ticker, str(entry["title"]).strip())
    return out


def user_agent(contact: str) -> str:
    return f"Murmur/{__version__} ({contact})"


def fetch_company_tickers(contact: str, timeout: float = 30) -> dict[str, SecCompany]:
    if not contact or "@" not in contact:
        raise ValueError("[sec] contact must be an email address (SEC fair-access policy)")
    req = urllib.request.Request(
        URL, headers={"User-Agent": user_agent(contact), "Accept-Encoding": "gzip, deflate"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https URL)
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            import gzip

            raw = gzip.decompress(raw)
    companies = parse_company_tickers(json.loads(raw))
    log.info("sec: %d tickers", len(companies))
    return companies
