"""The Murmur mark (logo concept A, "murmuration", chosen 2026-11-18): a flock of dots
rising like a line chart, a crowd of headlines forming a signal. One source for every
brand file; ``scripts/make_brand.py`` writes them under ``ui/static/brand/``.

``FLOCK`` is the full mark (40 dots) for 48 px and up; ``SMALL`` is the same flock
thinned to 7 dots for favicons, where 40 dots would blur into a smear.
Coordinates are in a 100 x 100 box: ``(cx, cy, r)``.
"""

from __future__ import annotations

FLOCK: tuple[tuple[float, float, float], ...] = (
    (9.6, 76.9, 1.1),
    (11.8, 93.1, 1.0),
    (13.5, 92.6, 0.9),
    (14.9, 75.5, 0.9),
    (19.0, 60.6, 1.0),
    (22.5, 81.0, 1.5),
    (19.7, 68.4, 1.5),
    (24.5, 96.2, 1.2),
    (26.2, 78.7, 1.3),
    (26.0, 76.6, 1.5),
    (27.8, 66.6, 1.5),
    (32.0, 73.5, 1.4),
    (31.7, 64.4, 1.5),
    (34.8, 57.9, 1.5),
    (35.1, 66.9, 1.6),
    (39.2, 52.0, 1.9),
    (40.9, 57.0, 2.0),
    (45.9, 60.6, 2.0),
    (48.5, 56.7, 1.6),
    (46.4, 43.3, 2.0),
    (50.1, 50.1, 2.1),
    (52.1, 36.5, 2.0),
    (52.3, 48.4, 2.1),
    (57.1, 37.1, 2.2),
    (56.3, 25.6, 2.3),
    (63.1, 32.8, 2.3),
    (64.2, 37.4, 2.1),
    (66.4, 31.7, 2.4),
    (68.9, 31.4, 2.3),
    (70.2, 29.2, 2.4),
    (72.0, 19.9, 2.6),
    (74.2, 28.5, 2.5),
    (78.2, 22.0, 2.8),
    (79.4, 26.9, 2.5),
    (82.0, 26.0, 2.7),
    (83.4, 25.5, 2.7),
    (86.0, 23.6, 3.0),
    (87.2, 24.5, 2.9),
    (89.7, 24.8, 3.1),
    (91.4, 25.2, 3.0),
)

SMALL: tuple[tuple[float, float, float], ...] = (
    (11.8, 85.9, 8.1),
    (24.6, 82.4, 8.6),
    (37.3, 67.6, 9.2),
    (50.0, 43.6, 9.8),
    (62.7, 23.5, 10.4),
    (75.4, 15.7, 11.0),
    (88.2, 14.0, 11.6),
)

BLUE = "#2a78d6"  # --accent, light theme
BLUE_DARK = "#3987e5"  # --accent, dark theme
INK = "#0b0b0b"
INK_DARK = "#f0efec"


def circles(dots, fill: str | None = None) -> str:
    attr = f' fill="{fill}"' if fill else ""
    return "".join(f'<circle cx="{x}" cy="{y}" r="{r}"{attr}/>' for x, y, r in dots)


def mark_svg(dots=FLOCK, fill: str = BLUE, size: int | None = None) -> str:
    dims = f' width="{size}" height="{size}"' if size else ""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"{dims}>'
        f'<title>Murmur</title><g fill="{fill}">{circles(dots)}</g></svg>\n'
    )


def favicon_svg() -> str:
    """The small mark, blue on light browser chrome and the lighter blue on dark."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        f"<style>g{{fill:{BLUE}}}@media (prefers-color-scheme:dark){{g{{fill:{BLUE_DARK}}}}}"
        f"</style><g>{circles(SMALL)}</g></svg>\n"
    )


def wordmark_svg(dark: bool = False) -> str:
    """Mark + "murmur" in Space Grotesk 600. Text, not outlines: it needs the font
    (served with the UI); renderers without it fall back to a sans."""
    fill, ink = (BLUE_DARK, INK_DARK) if dark else (BLUE, INK)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 420 100">'
        f'<title>Murmur</title><g fill="{fill}">{circles(FLOCK)}</g>'
        f'<text x="112" y="70" fill="{ink}" font-family="Space Grotesk, system-ui, sans-serif" '
        'font-weight="600" font-size="62" letter-spacing="-1.8">murmur</text></svg>\n'
    )
