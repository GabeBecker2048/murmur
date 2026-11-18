"""Write the brand files under src/murmur/ui/static/brand/ from murmur.brand.

    uv run python scripts/make_brand.py                  # the SVGs
    <python with playwright> scripts/make_brand.py --png # also the PNGs (headless Chromium)

The PNGs (favicon fallback, touch icon, app icon, the 1200x630 link-preview card) are
rendered by a browser so the wordmark uses the real Space Grotesk; they are committed,
so the image never needs a browser. Re-run after changing murmur.brand.
"""

# ruff: noqa: E501  (inline HTML templates for the rendered cards)
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from murmur import brand  # noqa: E402

OUT = ROOT / "src" / "murmur" / "ui" / "static" / "brand"
FONTS = ROOT / "src" / "murmur" / "ui" / "static" / "vendor" / "fonts"


def svgs() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    files = {
        "mark.svg": brand.mark_svg(),
        "mark-dark.svg": brand.mark_svg(fill=brand.BLUE_DARK),
        "favicon.svg": brand.favicon_svg(),
        "wordmark.svg": brand.wordmark_svg(),
        "wordmark-dark.svg": brand.wordmark_svg(dark=True),
    }
    for name, text in files.items():
        (OUT / name).write_text(text)
        print(f"wrote {name}")


def _page(body: str, width: int, height: int) -> str:
    font = (FONTS / "space-grotesk-latin-600-normal.woff2").as_uri()
    return f"""<!doctype html><html><head><style>
@font-face {{ font-family: "Space Grotesk"; font-weight: 600; src: url("{font}"); }}
html, body {{ margin: 0; width: {width}px; height: {height}px; overflow: hidden; }}
</style></head><body>{body}</body></html>"""


def _icon(dots, size: int, background: str | None, fill: str, pad: float) -> str:
    inner = size * (1 - 2 * pad)
    bg = f"background:{background};border-radius:{size * 0.22}px;" if background else ""
    return _page(
        f'<div style="width:{size}px;height:{size}px;{bg}display:flex;align-items:center;'
        f'justify-content:center"><svg viewBox="0 0 100 100" width="{inner}" height="{inner}">'
        f'<g fill="{fill}">{brand.circles(dots)}</g></svg></div>',
        size,
        size,
    )


def social_card() -> str:
    return _page(
        f"""<div style="width:1200px;height:630px;box-sizing:border-box;padding:84px 96px;
background:#fcfcfb;display:flex;flex-direction:column;justify-content:space-between;
font-family:'Space Grotesk',sans-serif">
<div style="display:flex;align-items:center;gap:28px">
<svg viewBox="0 0 100 100" width="170" height="170"><g fill="{brand.BLUE}" transform="translate(50 50) scale(1.12) translate(-50 -50)">{brand.circles(brand.FLOCK)}</g></svg>
<span style="font-size:118px;font-weight:600;letter-spacing:-4px;color:{brand.INK}">murmur</span></div>
<div style="font-size:50px;font-weight:600;line-height:1.15;letter-spacing:-1px;color:{brand.INK};max-width:960px">
Does news predict stock prices, follow them, or move with them?</div>
<div style="font-size:26px;color:#52514e;font-family:system-ui,sans-serif">
Daily headline sentiment of the 100 largest US companies, against their returns. Open data, CC BY 4.0.</div>
</div>""",
        1200,
        630,
    )


def wordmark_card(dark: bool) -> str:
    fill, ink = (brand.BLUE_DARK, brand.INK_DARK) if dark else (brand.BLUE, brand.INK)
    return _page(
        f"""<div style="width:840px;height:200px;display:flex;align-items:center;gap:22px;
font-family:'Space Grotesk',sans-serif">
<svg viewBox="0 0 100 100" width="200" height="200"><g fill="{fill}">{brand.circles(brand.FLOCK)}</g></svg>
<span style="font-size:124px;font-weight:600;letter-spacing:-4px;color:{ink}">murmur</span></div>""",
        840,
        200,
    )


def pngs() -> None:
    from playwright.sync_api import sync_playwright

    jobs = {
        "favicon-32.png": (_icon(brand.SMALL, 32, None, brand.BLUE, 0.0), 32, 32),
        "apple-touch-icon.png": (_icon(brand.FLOCK, 180, brand.BLUE, "#ffffff", 0.14), 180, 180),
        "icon-512.png": (_icon(brand.FLOCK, 512, brand.BLUE, "#ffffff", 0.14), 512, 512),
        "social-card.png": (social_card(), 1200, 630),
        # for places that cannot load the web font in an SVG (the GitHub README)
        "wordmark.png": (wordmark_card(False), 840, 200),
        "wordmark-dark.png": (wordmark_card(True), 840, 200),
    }
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for name, (html, w, h) in jobs.items():
            page = browser.new_page(viewport={"width": w, "height": h})
            # a file:// page, so the file:// font URL is allowed to load
            with tempfile.TemporaryDirectory() as tmp:
                src = Path(tmp) / "card.html"
                src.write_text(html)
                page.goto(src.as_uri())
                if "Space Grotesk" in html:
                    page.evaluate("document.fonts.load('600 20px \"Space Grotesk\"')")
                    assert page.evaluate("document.fonts.check('600 20px \"Space Grotesk\"')")
                page.evaluate("document.fonts.ready")
                page.screenshot(
                    path=str(OUT / name),
                    omit_background=name == "favicon-32.png" or name.startswith("wordmark"),
                )
            page.close()
            print(f"wrote {name}")
        browser.close()


if __name__ == "__main__":
    svgs()
    if "--png" in sys.argv[1:]:
        pngs()
