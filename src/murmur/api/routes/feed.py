"""The Atom feed (``/feed.xml``): one entry per week of trading sessions and one per
published findings snapshot, built from the store on request."""

from __future__ import annotations

import json
from datetime import date, timedelta
from xml.etree import ElementTree as ET

from fastapi import APIRouter, Depends, Request, Response

from murmur.api.deps import get_config, get_store
from murmur.config import Config
from murmur.store import Store

router = APIRouter(tags=["feed"])

ATOM = "http://www.w3.org/2005/Atom"
WEEKS = 12


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def weekly(store: Store, config: Config, upto: date, weeks: int = WEEKS) -> list[dict]:
    """Per ISO week with an analysed session, newest first: the mean same-session r
    (day headlines vs the daily return, all sources), headlines, the index change."""
    first = _week_start(upto) - timedelta(weeks=weeks - 1)
    corr = store.correlations_between(
        first.isoformat(),
        upto.isoformat(),
        {
            "window": "day",
            "return_type": "daily",
            "lag": 0,
            "source": "all",
            "method": "pearson",
            "model": config.sentiment.model,
        },
    )
    days = {
        r["date"]: r["n_articles"] for r in store.days_summary(first.isoformat(), upto.isoformat())
    }
    levels = {
        r["date"]: r["level"]
        for r in store.index_levels_between(
            (first - timedelta(days=7)).isoformat(), upto.isoformat()
        )
    }
    out = []
    for k in range(weeks):
        start = first + timedelta(weeks=k)
        end = start + timedelta(days=6)
        rows = [r for r in corr if start.isoformat() <= r["date"] <= end.isoformat()]
        if not rows:
            continue
        sessions = sorted(r["date"] for r in rows)
        before = [d for d in sorted(levels) if d < sessions[0]]
        inside = [d for d in sorted(levels) if sessions[0] <= d <= sessions[-1]]
        change = levels[inside[-1]] / levels[before[-1]] - 1 if before and inside else None
        out.append(
            {
                "week": f"{start.isocalendar().year}-W{start.isocalendar().week:02d}",
                "start": start,
                "sessions": sessions,
                "mean_r": sum(r["estimate"] for r in rows) / len(rows),
                "headlines": sum(
                    days.get((start + timedelta(days=i)).isoformat(), 0) for i in range(7)
                ),
                "index_change": change,
                "index_name": config.index.name,
            }
        )
    return sorted(out, key=lambda w: w["start"], reverse=True)


def _sub(parent, tag: str, text: str | None = None, **attrs) -> ET.Element:
    el = ET.SubElement(parent, f"{{{ATOM}}}{tag}", attrs)
    if text is not None:
        el.text = text
    return el


def _week_text(w: dict, title_name: str) -> str:
    parts = [
        f"Sessions {w['sessions'][0]} to {w['sessions'][-1]} ({len(w['sessions'])}).",
        f"Same-session correlation of headline sentiment with the daily return, averaged "
        f"over the week's sessions: r = {w['mean_r']:+.3f}.",
        f"{w['headlines']:,} headlines scored.",
    ]
    if w["index_change"] is not None:
        parts.append(f"The {title_name} moved {w['index_change'] * 100:+.2f} %.")
    return " ".join(parts)


@router.get("/feed.xml", responses={200: {"content": {"application/atom+xml": {}}}})
def feed(
    request: Request,
    store: Store = Depends(get_store),
    config: Config = Depends(get_config),
):
    """Atom: a weekly summary of the sessions analysed, and each new findings snapshot."""
    from murmur.clock import today_in

    ET.register_namespace("", ATOM)
    base = str(request.base_url).rstrip("/")
    latest = store.latest_analysis_day()
    upto = date.fromisoformat(latest) if latest else today_in(config.timezone)
    title_name = f"Murmur {config.index.size}"
    root = ET.Element(f"{{{ATOM}}}feed")
    _sub(root, "title", config.ui.title or "Murmur")
    _sub(root, "subtitle", config.ui.tagline)
    _sub(root, "id", f"{base}/feed.xml")
    _sub(root, "link", href=f"{base}/feed.xml", rel="self", type="application/atom+xml")
    _sub(root, "link", href=f"{base}/", rel="alternate", type="text/html")
    author = _sub(root, "author")
    _sub(author, "name", "Murmur")
    _sub(root, "rights", "Data CC BY 4.0, Murmur (github.com/GabeBecker2048/murmur)")
    entries: list[tuple[str, ET.Element]] = []

    for w in weekly(store, config, upto):
        updated = f"{w['sessions'][-1]}T21:30:00Z"
        e = ET.Element(f"{{{ATOM}}}entry")
        _sub(e, "title", f"Week {w['week']}: same-session r = {w['mean_r']:+.3f}")
        _sub(e, "id", f"{base}/feed/week/{w['week']}")
        _sub(e, "updated", updated)
        _sub(e, "link", href=f"{base}/news-vs-prices?day={w['sessions'][-1]}", rel="alternate")
        _sub(e, "summary", _week_text(w, title_name))
        entries.append((updated, e))

    for r in store.findings_list():
        if r["kind"] != "frozen":
            continue
        doc = json.loads(store.findings_get(r["id"])["payload"])
        text = doc.get("text") or {}
        updated = r["created_at"]
        e = ET.Element(f"{{{ATOM}}}entry")
        _sub(e, "title", f"Findings: {text.get('title') or r['id']}")
        _sub(e, "id", f"{base}/feed/findings/{r['id']}")
        _sub(e, "updated", updated)
        _sub(e, "link", href=f"{base}/findings?findings={r['id']}", rel="alternate")
        _sub(e, "summary", text.get("headline") or "")
        entries.append((updated, e))

    entries.sort(key=lambda x: x[0], reverse=True)
    _sub(root, "updated", entries[0][0] if entries else f"{upto.isoformat()}T00:00:00Z")
    # Atom wants <updated> before the entries; ElementTree keeps insertion order
    for _, e in entries:
        root.append(e)
    body = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return Response(
        body,
        media_type="application/atom+xml",
        headers={"Cache-Control": "public, max-age=3600"},
    )
