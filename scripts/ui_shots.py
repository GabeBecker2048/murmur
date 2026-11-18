"""Visual check of the dashboard: load every page in headless Chromium, collect
console errors and failed API requests, and screenshot each page in light and dark
at desktop and phone widths. Not part of the test suite (needs a browser build).

    uv venv /tmp/pw && uv pip install --python /tmp/pw/bin/python playwright
    PLAYWRIGHT_BROWSERS_PATH=/tmp/pw/browsers /tmp/pw/bin/python -m playwright install chromium
    uv run murmur serve &            # against any data dir
    PLAYWRIGHT_BROWSERS_PATH=/tmp/pw/browsers /tmp/pw/bin/python scripts/ui_shots.py <token>

Screenshots land in ./ui-shots/; the JSON report on stdout must show no errors."""

import json
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("MURMUR_UI_BASE", "http://127.0.0.1:8100")
OUT = Path.cwd() / "ui-shots"
OUT.mkdir(exist_ok=True)
PAGES = [
    ("overview", "/"),
    ("findings", "/findings"),
    ("story", "/story"),
    ("news-vs-prices", "/news-vs-prices"),
    ("companies", "/companies"),
    ("company-aapl", "/companies/AAPL?vs=MSFT"),
    ("index", "/index"),
    ("headlines", "/headlines?q=earnings"),
    ("data", "/downloads"),
    ("methodology", "/methodology"),
    ("status", "/status"),
    ("not-found", "/nope"),
    ("embed", "/embed/findings"),
    ("admin-login", "/admin"),
]
TOKEN = sys.argv[1] if len(sys.argv) > 1 else "t"
report = {}

with sync_playwright() as p:
    browser = p.chromium.launch()
    for theme in ("light", "dark"):
        for width, tag in ((1280, "desktop"), (390, "phone")):
            ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=theme)
            page = ctx.new_page()
            errors, failed = [], []

            def on_console(m, errors=errors):
                if m.type == "error":
                    errors.append(m.text)

            def on_response(r, failed=failed):
                if r.status >= 400 and "/api/v1/" in r.url and "nope" not in r.url:
                    failed.append(f"{r.status} {r.url}")

            page.on("console", on_console)
            page.on("pageerror", lambda e, errors=errors: errors.append(f"pageerror: {e}"))
            page.on("requestfailed", lambda r, failed=failed: failed.append(f"{r.url} {r.failure}"))
            page.on("response", on_response)
            for name, path in PAGES:
                page.goto(BASE + path, wait_until="networkidle")
                page.wait_for_timeout(600)
                page.screenshot(path=str(OUT / f"{name}-{theme}-{tag}.png"), full_page=True)
            # admin: sign in and visit tabs (desktop light only)
            if theme == "light" and tag == "desktop":
                page.goto(BASE + "/admin", wait_until="networkidle")
                page.fill("input[type=password]", TOKEN)
                page.click("button[type=submit]")
                page.wait_for_url("**/admin/jobs", timeout=8000)
                page.wait_for_timeout(800)
                page.screenshot(path=str(OUT / "admin-jobs.png"), full_page=True)
                for tab in ("backfill", "scheduler", "settings", "logs"):
                    page.goto(BASE + f"/admin/{tab}", wait_until="networkidle")
                    page.wait_for_timeout(600)
                    page.screenshot(path=str(OUT / f"admin-{tab}.png"), full_page=True)
                page.goto(BASE + "/admin/jobs/1", wait_until="networkidle")
                page.wait_for_timeout(600)
                page.screenshot(path=str(OUT / "admin-job-detail.png"), full_page=True)
            report[f"{theme}-{tag}"] = {"console_errors": errors, "failed_requests": failed}
            ctx.close()
    browser.close()

print(json.dumps(report, indent=1))
