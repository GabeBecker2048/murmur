"""Chart rendering shared by the exporter and the API. Pure functions from rows
to PNG bytes using Figure + FigureCanvasAgg, so request threads and the job
worker never touch pyplot's global state."""

from __future__ import annotations

import io
from collections.abc import Sequence

LAGS = (-1, 0, 1)
MATCHED = (("overnight", "overnight"), ("intraday", "intraday"), ("day", "daily"))
COLORS = {"overnight": "#264653", "intraday": "#2a9d8f", "day": "#e76f51"}


def _figure(width: float, height: float):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    fig = Figure(figsize=(width, height), dpi=120)
    FigureCanvasAgg(fig)
    return fig


def _png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    return buf.getvalue()


def sentiment_bar(tickers: Sequence[str], scores: Sequence[float], title: str) -> bytes:
    fig = _figure(max(8, 0.22 * len(tickers)), 4)
    ax = fig.add_subplot()
    ax.bar(tickers, scores, color=["#2a9d8f" if s >= 0 else "#e76f51" for s in scores])
    ax.axhline(0, color="black", linewidth=0.6)
    ax.set_ylabel("mean sentiment (positive − negative)")
    ax.set_title(title)
    ax.tick_params(axis="x", rotation=80, labelsize=7)
    fig.tight_layout()
    return _png(fig)


def scatter(pairs: Sequence[tuple[float, float, str]], title: str, subtitle: str) -> bytes:
    """``pairs`` are (sentiment, return, ticker); returns are plotted in percent."""
    import numpy as np

    xs = [p[0] for p in pairs]
    ys = [p[1] * 100 for p in pairs]
    fig = _figure(8, 5)
    ax = fig.add_subplot()
    ax.scatter(xs, ys, color="#264653")
    for x, y, t in zip(xs, ys, (p[2] for p in pairs), strict=True):
        ax.annotate(t, (x, y), fontsize=6, xytext=(2, 2), textcoords="offset points")
    if len(set(xs)) > 1:
        slope, intercept = np.polyfit(xs, ys, 1)
        grid = np.linspace(min(xs), max(xs), 2)
        ax.plot(grid, slope * grid + intercept, color="#e76f51", linewidth=1)
    ax.set_xlabel("mean sentiment, previous close to close")
    ax.set_ylabel("daily return (%)")
    ax.set_title(f"{title}\n{subtitle}")
    fig.tight_layout()
    return _png(fig)


def leadlag(
    rows: dict[tuple[str, str, int], dict], horizon: int, as_of: str, method: str = "pearson"
) -> bytes:
    """``rows`` maps (window, return_type, lag) -> {estimate, ci_low, ci_high, n} for one
    horizon, pooled."""
    fig = _figure(7, 4.5)
    ax = fig.add_subplot()
    for i, (window, return_type) in enumerate(MATCHED):
        xs = [lag + (i - 1) * 0.08 for lag in LAGS]
        ys = [rows[(window, return_type, lag)]["estimate"] for lag in LAGS]
        lo = [
            ys[j] - (rows[(window, return_type, lag)]["ci_low"] or ys[j])
            for j, lag in enumerate(LAGS)
        ]
        hi = [
            (rows[(window, return_type, lag)]["ci_high"] or ys[j]) - ys[j]
            for j, lag in enumerate(LAGS)
        ]
        n = rows[(window, return_type, 0)]["n"]
        ax.errorbar(
            xs,
            ys,
            yerr=[lo, hi],
            fmt="o-",
            capsize=3,
            color=COLORS[window],
            label=f"{window} news vs {return_type} return (n={n})",
        )
    ax.axhline(0, color="black", linewidth=0.6)
    ax.set_xticks(list(LAGS))
    ax.set_xticklabels(
        ["-1\nnews after return\n(follows)", "0\nsame session", "+1\nnews before return\n(leads)"]
    )
    ax.set_ylabel(f"{method.capitalize()} r, pooled over tickers and days")
    ax.set_title(f"Does news lead or follow prices?  last {horizon} month(s) to {as_of}")
    ax.legend(fontsize=8)
    fig.tight_layout()
    return _png(fig)


def pick_horizon(pooled_rows: Sequence[dict], horizons: Sequence[int]) -> tuple[int, dict] | None:
    """The longest horizon that has every matched pair at every lag."""
    by_h: dict[int, dict[tuple[str, str, int], dict]] = {}
    for r in pooled_rows:
        by_h.setdefault(r["months"], {})[(r["window"], r["return_type"], r["lag"])] = r
    for h in sorted(horizons, reverse=True):
        rows = by_h.get(h)
        if rows and all((w, t, lag) in rows for w, t in MATCHED for lag in LAGS):
            return h, rows
    return None
