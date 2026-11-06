"""The SEC's public-domain ticker/CIK list (``company_tickers.json``).

SEC fair-access rules require a User-Agent naming a contact, so the fetch
refuses to run without ``[sec] contact`` in the config.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta

from murmur import __version__

log = logging.getLogger(__name__)

URL = "https://www.sec.gov/files/company_tickers.json"
FRAMES_URL = "https://data.sec.gov/api/xbrl/frames/dei/EntityCommonStockSharesOutstanding/shares/{frame}.json"
FILING_LAG_DAYS = 45
"""A quarter's share counts are on file about this long after the quarter ends (10-Q due dates)."""


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


def _get(url: str, contact: str, timeout: float = 30) -> dict:
    if not contact or "@" not in contact:
        raise ValueError("[sec] contact must be an email address (SEC fair-access policy)")
    req = urllib.request.Request(
        url, headers={"User-Agent": user_agent(contact), "Accept-Encoding": "gzip, deflate"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https hosts)
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            import gzip

            raw = gzip.decompress(raw)
    return json.loads(raw)


def fetch_company_tickers(contact: str, timeout: float = 30) -> dict[str, SecCompany]:
    companies = parse_company_tickers(_get(URL, contact, timeout))
    log.info("sec: %d tickers", len(companies))
    return companies


def frame_for(day: date) -> str:
    """The XBRL instantaneous frame whose share counts were on file by ``day``: the last
    calendar quarter end at least ``FILING_LAG_DAYS`` before it (``CY2025Q4I``)."""
    cutoff = day - timedelta(days=FILING_LAG_DAYS)
    quarter = (cutoff.month - 1) // 3  # 0..3 = the quarter cutoff is in; its end is later
    year, q = (cutoff.year, quarter) if quarter > 0 else (cutoff.year - 1, 4)
    return f"CY{year}Q{q}I"


def parse_shares_frame(payload: dict) -> dict[int, float]:
    """``{"data": [{"cik": 320193, "val": 14776000000, ...}, ...]}`` -> CIK -> shares."""
    out: dict[int, float] = {}
    for row in payload.get("data", []) or []:
        try:
            cik, val = int(row["cik"]), float(row["val"])
        except (KeyError, TypeError, ValueError):
            continue
        if val > 0:
            out[cik] = val
    return out


def fetch_shares_outstanding(contact: str, day: date, timeout: float = 30) -> dict[int, float]:
    """Shares outstanding per CIK as of the last quarter on file before ``day`` (one
    request for every filer; public domain). Multi-class companies that report per
    class with a dimension are absent, which the caller treats as unknown."""
    frame = frame_for(day)
    shares = parse_shares_frame(_get(FRAMES_URL.format(frame=frame), contact, timeout))
    log.info("sec: %s share counts in frame %s", len(shares), frame)
    return shares
