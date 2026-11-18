"""Findings: the conclusion over one window, computed once and kept.

A *frozen* findings snapshot is the published answer to the question, with the numbers
and the prose that explains them stored together and never recomputed: a citation of
"Murmur findings 2026-10" means the same thing forever. A *draft* is a snapshot that can
still be replaced (written while the prose is reviewed); the *live* row is recomputed
after every trading day's analysis over the last 12 months, so the dashboard can show
"since then" next to the frozen numbers without computing anything per request.

Every figure is computed from the store, never from the rolling tables, and unlike the
rolling analysis each session is restricted to **that session's** universe members (a
year spans twelve rebalances). Beyond the plain pooled correlations it carries the
robustness checks the conclusion rests on:

- ``market_adjusted``: each return minus that session's cap-weighted mean return of the
  index members, so a day when everything rose and the news was good does not count as
  company-specific evidence.
- ``per_session``: the mean of each session's cross-sectional correlation with its
  standard error across sessions (Fama-MacBeth). Observations on one day are not
  independent, so pooled p-values overstate the certainty; this does not. A
  cross-sectional correlation ignores a per-session constant, so it is market-neutral
  by construction.
- ``placebo``: headlines shuffled across companies within each session.
- ``by_source`` and ``by_cap`` splits.
"""

from __future__ import annotations

import json
import logging
import math
import tomllib
from datetime import date
from importlib import resources

from murmur.analysis import (
    DAY,
    LAGS,
    MATCHED_PAIRS,
    MIN_POOLED,
    RETURN_TYPES,
    SOURCE,
    WINDOWS_ALL,
    correlate_xy,
    shuffled_null,
    sources_for_analysis,
)
from murmur.clock import next_session, previous_session, utc_now_iso
from murmur.config import Config
from murmur.store import Store

log = logging.getLogger(__name__)

PERMUTATIONS = 1000
MIN_SESSION_PAIRS = 10
"""A session's cross-sectional r enters the per-session mean only with this many pairs."""
LIVE_ID = "live"
LIVE_MONTHS = 12
LIVE_PERMUTATIONS = 200
"""The live row is recomputed daily on the server's CPU; 200 shuffles are plenty for a
comparison box (the frozen snapshots use 1000)."""
KINDS = ("draft", "frozen", "live")
HEADLINE = (DAY, "daily")
CAP_GROUPS = ((1, 50), (51, 100))


class FindingsError(Exception):
    pass


def _r(x, y) -> dict | None:
    """Pearson (with 95 % CI) and Spearman for one sample, or None when undefined."""
    if len(x) < MIN_POOLED:
        return None
    out: dict = {"n": len(x)}
    for method, est, p, lo, hi in correlate_xy(list(x), list(y)):
        if method == "pearson":
            out.update(r=est, p=p, ci_low=lo, ci_high=hi)
        else:
            out.update(rho=est, rho_p=p)
    return out if "r" in out else None


def _per_session(m, col: str) -> dict | None:
    import numpy as np
    from scipy import stats

    rs = []
    for _, g in m.groupby("s"):
        if len(g) < MIN_SESSION_PAIRS or g["mean_score"].nunique() < 2 or g[col].nunique() < 2:
            continue
        rs.append(float(np.corrcoef(g["mean_score"], g[col])[0, 1]))
    if len(rs) < 3:
        return None
    a = np.array(rs)
    sd = float(a.std(ddof=1))
    se = sd / math.sqrt(len(a))
    t = float(a.mean() / se) if se > 0 else None
    p = float(2 * stats.t.sf(abs(t), len(a) - 1)) if t is not None else None
    return {
        "sessions": len(a),
        "mean_r": float(a.mean()),
        "sd": sd,
        "se": se,
        "t": t,
        "p": p,
        "share_positive": float((a > 0).mean()),
    }


def _frames(store: Store, config: Config, start: date, end: date):
    """``(sentiment by source, returns)``: sentiment rows for sessions in the window,
    each restricted to that session's universe; returns from the session before
    ``start`` to the one after ``end`` (so every lag has its pair), with a session
    ordinal ``s`` and market-adjusted columns."""
    import pandas as pd

    first = previous_session(start).isoformat()
    last = next_session(end).isoformat()
    mem = pd.DataFrame(
        [tuple(r) for r in store.membership_between(first, last)],
        columns=["date", "ticker", "market_cap", "rank"],
    )
    rets = pd.DataFrame(
        [tuple(r) for r in store.returns_between(first, last)],
        columns=["ticker", "date", *RETURN_TYPES],
    )
    if rets.empty or mem.empty:
        return {}, rets
    sessions = sorted(rets["date"].unique())
    index = {d: i for i, d in enumerate(sessions)}
    rets["s"] = rets["date"].map(index)
    rets = rets.merge(mem[["date", "ticker", "market_cap"]], on=["date", "ticker"], how="left")
    for col in RETURN_TYPES:
        w = rets["market_cap"].where(rets[col].notna() & rets["market_cap"].notna(), 0.0)
        num = (rets[col].fillna(0) * w).groupby(rets["date"]).transform("sum")
        den = w.groupby(rets["date"]).transform("sum")
        rets[f"{col}_adj"] = rets[col] - num / den.where(den > 0)
    model = config.sentiment.model
    by_source = {}
    for source in sources_for_analysis(store):
        ws = pd.DataFrame(
            [
                tuple(r)
                for r in store.window_sentiment_between(
                    start.isoformat(), end.isoformat(), model, source
                )
            ],
            columns=["date", "ticker", "window", "n_articles", "mean_score"],
        )
        if ws.empty:
            continue
        ws = ws.merge(mem[["date", "ticker", "rank"]], on=["date", "ticker"])
        ws["s"] = ws["date"].map(index)
        by_source[source] = ws.dropna(subset=["s", "mean_score"])
    return by_source, rets


def _pairs(ws, rets, window: str, lag: int, col: str):
    """Sentiment at session s paired with the ``col`` return at session s + lag."""
    w = ws[ws["window"] == window]
    r = rets[["ticker", "s", col]].assign(s=rets["s"] - lag)
    return w.merge(r, on=["ticker", "s"]).dropna(subset=[col])


def compute(
    store: Store,
    config: Config,
    start: date,
    end: date,
    *,
    permutations: int = PERMUTATIONS,
) -> dict:
    """Every figure of a findings snapshot over sentiment sessions ``[start, end]``."""
    import numpy as np

    if end < start:
        raise FindingsError("end is before start")
    by_source, rets = _frames(store, config, start, end)
    ws = by_source.get(SOURCE)
    if ws is None or ws.empty:
        raise FindingsError(f"no scored headlines with returns between {start} and {end}")

    pairs, adjusted, per_session = [], [], []
    for window in WINDOWS_ALL:
        for return_type in RETURN_TYPES:
            for lag in LAGS:
                key = {"window": window, "return_type": return_type, "lag": lag}
                m = _pairs(ws, rets, window, lag, return_type)
                if (row := _r(m["mean_score"], m[return_type])) is not None:
                    pairs.append({**key, **row})
                adj = f"{return_type}_adj"
                ma = _pairs(ws, rets, window, lag, adj)
                if (row := _r(ma["mean_score"], ma[adj])) is not None:
                    adjusted.append({**key, **row})
                if (row := _per_session(m, return_type)) is not None:
                    per_session.append({**key, **row})

    window, return_type = HEADLINE
    rng = np.random.default_rng(int(end.strftime("%Y%m%d")))
    placebo = []
    for lag in LAGS:
        m = _pairs(ws, rets, window, lag, return_type)
        if len(m) < MIN_POOLED or m["mean_score"].std() == 0:
            continue
        x, y = m["mean_score"].to_numpy(), m[return_type].to_numpy()
        real = float(np.corrcoef(x, y)[0, 1])
        null = shuffled_null(x, y, m["s"].to_numpy(), permutations, rng)
        sd = float(null.std())
        placebo.append(
            {
                "window": window,
                "return_type": return_type,
                "lag": lag,
                "n": len(m),
                "real_r": real,
                "null_mean": float(null.mean()),
                "null_sd": sd,
                "null_low": float(np.percentile(null, 2.5)),
                "null_high": float(np.percentile(null, 97.5)),
                "z": (real - float(null.mean())) / sd if sd > 1e-9 else None,
                "p_perm": float((np.sum(np.abs(null) >= abs(real)) + 1) / (permutations + 1)),
                "permutations": permutations,
            }
        )

    split_source = []
    for source, frame in by_source.items():
        if source == SOURCE:
            continue
        for lag in LAGS:
            m = _pairs(frame, rets, window, lag, return_type)
            row = _r(m["mean_score"], m[return_type]) if len(m) else None
            split_source.append({"source": source, "lag": lag, "n": len(m), **(row or {})})

    split_cap = []
    for lo, hi in CAP_GROUPS:
        sub = ws[ws["rank"].between(lo, hi)]
        for lag in LAGS:
            m = _pairs(sub, rets, window, lag, return_type)
            row = _r(m["mean_score"], m[return_type]) if len(m) else None
            ps = _per_session(m, return_type) if len(m) else None
            split_cap.append(
                {
                    "ranks": f"{lo}-{hi}",
                    "lag": lag,
                    "n": len(m),
                    **(row or {}),
                    "per_session_mean_r": ps["mean_r"] if ps else None,
                }
            )

    cov = store.findings_coverage(start.isoformat(), end.isoformat(), config.sentiment.model)
    head = {
        str(lag): next(
            (p for p in pairs if (p["window"], p["return_type"], p["lag"]) == (*HEADLINE, lag)),
            None,
        )
        for lag in LAGS
    }
    from murmur import __version__
    from murmur.publish import DATA_VERSION

    return {
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "headline": {"window": window, "return_type": return_type, "lags": head},
        "matched_pairs": [{"window": w, "return_type": r} for w, r in MATCHED_PAIRS],
        "pairs": pairs,
        "market_adjusted": adjusted,
        "per_session": per_session,
        "placebo": placebo,
        "by_source": split_source,
        "by_cap": split_cap,
        "coverage": {
            **cov,
            "sources": sorted(s for s in by_source if s != SOURCE),
            "pairs_day_daily_lag0": head["0"]["n"] if head["0"] else 0,
        },
        "meta": {
            "murmur_version": __version__,
            "data_version": DATA_VERSION,
            "model": config.sentiment.model,
            "computed_at": utc_now_iso(),
            "permutations": permutations,
            "min_session_pairs": MIN_SESSION_PAIRS,
        },
    }


# -- prose ---------------------------------------------------------------------


def text_for(snapshot_id: str) -> dict | None:
    """The reviewed prose for a snapshot, ``findings_text/<id>.toml`` in the package:
    ``title``, ``headline``, ``summary`` (paragraphs), ``caveats`` (list)."""
    res = resources.files("murmur") / "findings_text" / f"{snapshot_id}.toml"
    if not res.is_file():
        return None
    data = tomllib.loads(res.read_text(encoding="utf-8"))
    return {
        "title": str(data.get("title", "")),
        "headline": str(data.get("headline", "")),
        "summary": [str(p) for p in data.get("summary", [])],
        "caveats": [str(c) for c in data.get("caveats", [])],
    }


# -- snapshots -----------------------------------------------------------------


def create(
    store: Store,
    config: Config,
    snapshot_id: str,
    start: date,
    end: date,
    *,
    freeze: bool = False,
    permutations: int = PERMUTATIONS,
) -> dict:
    """Compute and store a draft (or frozen) snapshot. A frozen id is never replaced."""
    if not snapshot_id or snapshot_id == LIVE_ID or not _valid_id(snapshot_id):
        raise FindingsError("id must be letters, digits, '-' or '_' (and not 'live')")
    existing = store.findings_get(snapshot_id)
    if existing is not None and existing["kind"] == "frozen":
        raise FindingsError(f"findings {snapshot_id} are frozen and cannot be replaced")
    payload = compute(store, config, start, end, permutations=permutations)
    text = text_for(snapshot_id)
    if freeze and text is None:
        raise FindingsError(f"no reviewed text: findings_text/{snapshot_id}.toml")
    kind = "frozen" if freeze else "draft"
    payload = {"id": snapshot_id, "kind": kind, "text": text, **payload}
    store.put_findings(
        snapshot_id,
        kind,
        start.isoformat(),
        end.isoformat(),
        payload["meta"]["data_version"],
        json.dumps(payload),
    )
    log.info("findings %s (%s) %s..%s stored", snapshot_id, kind, start, end)
    return payload


def refresh_live(store: Store, config: Config, as_of: date) -> dict | None:
    """Recompute the live row over the 12 months to ``as_of``; None when there is no data."""
    from murmur.clock import months_before

    start = months_before(as_of, LIVE_MONTHS)
    try:
        payload = compute(store, config, start, as_of, permutations=LIVE_PERMUTATIONS)
    except FindingsError as exc:
        log.info("live findings skipped: %s", exc)
        return None
    payload = {"id": LIVE_ID, "kind": "live", "text": None, **payload}
    store.put_findings(
        LIVE_ID,
        "live",
        start.isoformat(),
        as_of.isoformat(),
        payload["meta"]["data_version"],
        json.dumps(payload),
    )
    return payload


def _valid_id(value: str) -> bool:
    return len(value) <= 40 and all(c.isalnum() or c in "-_" for c in value)
