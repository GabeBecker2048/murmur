"""Click through the whole dashboard in a real browser: every page, every control, the
admin flows end to end (jobs, backfill, scheduler, settings round trip, sign out).
Connects to a visible Chromium-family browser over CDP when one listens on 9222
(e.g. ``brave-browser --remote-debugging-port=9222 --remote-allow-origins='*'
--user-data-dir=/tmp/review-profile``), else falls back to headless Chromium. Same
setup as scripts/ui_shots.py; prints one line per step and a JSON report; screenshots
go to ./ui-review/. The admin steps queue a backup and a two-day prices+export
backfill and flip a setting back and forth, so point it at a scratch data dir."""

import json
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("MURMUR_UI_BASE", "http://127.0.0.1:8100")
TOKEN = sys.argv[1] if len(sys.argv) > 1 else "t"
OUT = Path.cwd() / "ui-review"
OUT.mkdir(exist_ok=True)
CDP = os.environ.get("CDP", "http://127.0.0.1:9222")

report = []
errors = []
bad = []


def step(name, ok=True, note=""):
    report.append((name, ok, note))
    print(("ok   " if ok else "FAIL ") + name + (f"  [{note}]" if note else ""), flush=True)


def shot(page, name):
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)


def settle(page, ms=700):
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(ms)


with sync_playwright() as p:
    browser = None
    try:
        browser = p.chromium.connect_over_cdp(CDP, timeout=3000)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})  # fresh cache
        page = ctx.new_page()
        mode = "brave over CDP"
    except Exception as e:  # noqa: BLE001
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        mode = f"headless chromium ({type(e).__name__})"
    print("mode:", mode, flush=True)

    def on_console(m):
        if m.type == "error":
            errors.append(m.text)

    def on_response(r):
        if r.status >= 400 and "/api/v1/" in r.url:
            bad.append(f"{r.status} {r.request.method} {r.url}")

    page.on("console", on_console)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("response", on_response)
    page.set_viewport_size({"width": 1280, "height": 900})

    # ---- overview
    page.goto(BASE + "/")
    settle(page)
    tiles = page.locator(".tile").count()
    step("overview loads with stat tiles", tiles >= 4, f"{tiles} tiles")
    step("overview lead/lag chart drawn", page.locator(".card .chart .plotly").count() >= 1)
    shot(page, "01-overview")
    page.click("text=Explore every window, source and horizon")
    settle(page)
    step(
        "link navigates to /leadlag",
        page.url.endswith("/leadlag") or "/leadlag?" in page.url,
        page.url,
    )

    # ---- lead/lag explorer
    page.click(".seg button:has-text('6 mo')")
    settle(page)
    step("6-month filter updates URL", "months=6" in page.url, page.url)
    step(
        "6-month chart y-title says 6-month", "6-month" in page.locator(".chart").first.inner_text()
    )
    page.click(".seg button:has-text('Spearman')")
    settle(page)
    step(
        "Spearman filter updates URL and table header",
        "method=spearman" in page.url and page.locator("th:has-text('Spearman rho')").count() == 1,
    )
    rows = page.locator("table tbody tr.clickable")
    n_rows = rows.count()
    step("full table has 27 rows", n_rows == 27, f"{n_rows}")
    rows.nth(3).click()  # overnight news vs intraday return, lag -1
    settle(page)
    hist_title = page.locator("h2:has-text('History')").inner_text()
    step(
        "row click changes history cell",
        "Overnight news vs intraday return" in hist_title and "lag -1" in hist_title,
        hist_title,
    )
    page.fill("input[type=date]", "2026-11-04")
    page.dispatch_event("input[type=date]", "change")
    settle(page)
    step(
        "session picker reloads the scatter",
        "day=2026-11-04" in page.url
        and ("companies" in page.locator("section.card:last-of-type").inner_text()),
    )
    shot(page, "02-leadlag-filtered")

    # ---- theme toggle + mobile menu
    page.click("#theme-toggle")
    page.wait_for_timeout(500)
    theme = page.evaluate("document.documentElement.dataset.theme")
    step("theme toggle sets data-theme", theme in ("dark", "light"), theme)
    shot(page, "03-leadlag-theme-toggled")
    page.set_viewport_size({"width": 390, "height": 800})
    page.wait_for_timeout(500)
    page.click("#menu-toggle")
    page.wait_for_timeout(300)
    step("phone menu opens", page.locator("#nav.open").count() == 1)
    shot(page, "04-phone-menu")
    page.click("#nav >> text=Companies")
    settle(page)
    step(
        "phone menu closes after navigation",
        page.locator("#nav.open").count() == 0 and page.url.endswith("/tickers"),
    )
    shot(page, "05-phone-companies")
    page.set_viewport_size({"width": 1280, "height": 900})
    page.click("#theme-toggle")  # back
    page.wait_for_timeout(300)

    # ---- companies
    page.fill("input[placeholder^='Filter']", "nvid")
    page.wait_for_timeout(300)
    visible = page.locator("table tbody tr").count()
    step(
        "company search filters the table",
        visible == 1 and "NVDA" in page.locator("table tbody").inner_text(),
        f"{visible} rows",
    )
    page.click("table tbody tr")
    settle(page)
    step("row click opens the company page", page.url.endswith("/tickers/NVDA"), page.url)
    step(
        "company page shows headlines",
        page.locator(".article").count() > 0,
        f"{page.locator('.article').count()} articles",
    )
    page.select_option("select", index=0)  # 30 days
    settle(page)
    step("range select updates URL", "from=" in page.url, page.url)
    shot(page, "06-company-nvda")

    # ---- sources
    page.goto(BASE + "/sources")
    settle(page)
    step("sources page lists yahoo", "yahoo" in page.locator("table").first.inner_text())
    step("sources chart drawn", page.locator(".chart .plotly").count() >= 1)

    # ---- index
    page.goto(BASE + "/index")
    settle(page)
    step(
        "index page lists 100 constituents",
        page.locator("h2:has-text('All 100 constituents')").count() == 1,
    )
    href = page.locator("a:has-text('CSV')").first.get_attribute("href")
    r = page.request.get(BASE + href)
    step(
        "index CSV link downloads",
        r.status == 200 and "text/csv" in r.headers.get("content-type", ""),
        href,
    )
    page.select_option("select", "2026-10")
    settle(page)
    step("month select updates URL", "month=2026-10" in page.url, page.url)
    shot(page, "07-index")

    # ---- downloads (prices made private for the private-dataset steps; restored at the end)
    AUTH = {"authorization": f"Bearer {TOKEN}", "content-type": "application/json"}
    page.request.put(
        BASE + "/api/v1/settings", headers=AUTH, data='{"datasets": {"private": ["prices"]}}'
    )
    page.goto(BASE + "/downloads")
    settle(page)
    page.click("section.card:has-text('News')")
    settle(page)
    step(
        "dataset card selects news",
        "dataset=news" in page.url and page.locator("h2:has-text('News')").count() >= 1,
    )
    csv = page.locator("a:has-text('CSV')").first.get_attribute("href")
    r = page.request.get(BASE + csv)
    step("news CSV downloads", r.status == 200, csv)
    page.click("section.card:has-text('Prices')")
    settle(page)
    priv = page.locator(".notice.warn:has-text('not published')").count()
    step("prices shows the private notice", priv == 1)
    pq = page.locator("a:has-text('Parquet')").first.get_attribute("href")
    r = page.request.get(BASE + pq)
    step("private prices file refused without token", r.status == 403, f"{r.status}")
    shot(page, "08-downloads-prices")

    # ---- admin
    page.goto(BASE + "/admin")
    settle(page)
    page.fill("input[type=password]", "wrong-token")
    page.click("button[type=submit]")
    page.wait_for_timeout(1200)
    step("wrong token is refused", "refused" in page.locator(".help").inner_text().lower())
    page.fill("input[type=password]", TOKEN)
    page.click("button[type=submit]")
    page.wait_for_url("**/admin/jobs", timeout=8000)
    settle(page)
    step("sign-in lands on jobs")
    page.click("button:has-text('Backup store')")
    page.wait_for_timeout(1500)
    msg = page.locator(".help").first.inner_text()
    step("backup job queued from the button", "job" in msg.lower(), msg)
    # wait for the newest job to be done
    for _ in range(30):
        settle(page, 300)
        first = page.locator("table tbody tr").first.inner_text()
        if "done" in first:
            break
    step("newest job reaches done in the table", "done" in first, first.split("\n")[0][:80])
    page.locator("table tbody tr").first.click()
    settle(page)
    step(
        "job detail opens with a log",
        "/admin/jobs/" in page.url and len(page.locator("pre.log").inner_text()) > 20,
        page.url,
    )
    shot(page, "09-admin-job-detail")

    # backfill form
    page.goto(BASE + "/admin/backfill")
    settle(page)
    for s in ("news", "sentiment", "analyze"):
        page.uncheck(f"input[value={s}]")
    page.fill("input[type=date] >> nth=0", "2026-11-04")
    page.fill("input[type=date] >> nth=1", "2026-11-05")
    page.click("button:has-text('Queue backfill')")
    page.wait_for_url("**/admin/jobs/*", timeout=8000)
    settle(page)
    step("backfill form queues a job and opens it", "/admin/jobs/" in page.url, page.url)
    for _ in range(40):
        settle(page, 400)
        if any(w in page.locator(".badge").first.inner_text() for w in ("done", "failed")):
            break
    status = page.locator(".badge").first.inner_text()
    params = page.locator("dl.kv").inner_text()
    step("backfill job finished", status == "done", status)
    step(
        "backfill ran only prices + export",
        "stages=prices,export" in params,
        params.split("\n")[1] if "\n" in params else params,
    )
    shot(page, "10-admin-backfill-job")

    # regenerate needs confirm dialog
    page.goto(BASE + "/admin/backfill")
    settle(page)
    page.check("input[type=checkbox] >> nth=-1")  # regenerate
    page.once("dialog", lambda d: d.dismiss())
    page.click("button:has-text('Queue backfill')")
    page.wait_for_timeout(800)
    step(
        "regenerate asks for confirmation and dismiss cancels", page.url.endswith("/admin/backfill")
    )

    # scheduler
    page.goto(BASE + "/admin/scheduler")
    settle(page)
    page.click("button:has-text('Pause scheduler')")
    settle(page)
    step("pause shows paused", "paused" in page.locator("dl.kv").inner_text())
    page.click("button:has-text('Resume scheduler')")
    settle(page)
    step("resume shows active", "active" in page.locator("dl.kv").inner_text())
    step("recent runs table present", page.locator("table tbody tr").count() >= 1)
    shot(page, "11-admin-scheduler")

    # settings round trip
    page.goto(BASE + "/admin/settings")
    settle(page)
    tagline = page.locator("section.card:has(h2:has-text('ui')) input[type=text]").nth(1)
    old = tagline.input_value()
    tagline.fill("Review tagline")
    page.click("button:has-text('Save changes')")
    page.wait_for_timeout(1500)
    settle(page)
    saved = page.locator(".notice").first.inner_text()
    api_tagline = page.request.get(BASE + "/api/v1/ui").json()["tagline"]
    step(
        "settings save round-trips to the API and says so",
        "Saved 1" in saved and api_tagline == "Review tagline",
        f"{saved[:40]} / {api_tagline}",
    )
    settle(page)
    tagline = page.locator("section.card:has(h2:has-text('ui')) input[type=text]").nth(1)
    tagline.fill(old)
    page.click("button:has-text('Save changes')")
    page.wait_for_timeout(1500)
    step("settings restored", page.request.get(BASE + "/api/v1/ui").json()["tagline"] == old)
    bad_input = page.locator("section.card:has(h2:has-text('schedule')) input[type=text]").first
    bad_input.fill("25:99")
    page.click("button:has-text('Save changes')")
    page.wait_for_timeout(600)
    blocked = page.evaluate("document.querySelector('form').checkValidity()") is False
    step("invalid schedule time is blocked by the form's pattern", blocked)
    page.goto(BASE + "/admin/settings")
    settle(page)
    size = page.locator("section.card:has(h2:has-text('universe')) input[type=number]").first
    size.fill("500")
    page.click("button:has-text('Save changes')")
    page.wait_for_timeout(1500)
    err = page.locator(".actions .help").inner_text()
    step(
        "coverage above the index size is rejected with the API's message",
        "cannot exceed" in err,
        err[:80],
    )
    page.goto(BASE + "/admin/settings")
    settle(page)
    shot(page, "12-admin-settings")

    # logs + sign out
    page.goto(BASE + "/admin/logs")
    settle(page)
    step("process log shows lines", len(page.locator("pre.log").inner_text()) > 50)
    page.click("button:has-text('Sign out')")
    settle(page)
    step("sign out returns to the login form", page.locator("input[type=password]").count() == 1)
    page.goto(BASE + "/downloads?dataset=prices")
    settle(page)
    step(
        "private notice for visitors after sign-out",
        page.locator(".notice.warn:has-text('not published')").count() == 1,
    )
    page.request.put(
        BASE + "/api/v1/settings", headers=AUTH, data='{"datasets": {"private": null}}'
    )

    # 404 page
    page.goto(BASE + "/nothing-here")
    settle(page)
    step("unknown route gives the API's 404, not the shell", "not found" in page.content().lower())

    if "brave" not in mode:
        browser.close()

print()
print(
    json.dumps(
        {
            "mode": mode,
            "console_errors": errors,
            "api_errors": bad,
            "failed_steps": [r for r in report if not r[1]],
        },
        indent=1,
    )
)
