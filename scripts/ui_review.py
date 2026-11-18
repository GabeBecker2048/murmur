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

    # ---- findings (home)
    page.goto(BASE + "/")
    settle(page)
    step(
        "findings hero states the answer", len(page.locator(".hero-findings h1").inner_text()) > 30
    )
    step("three lag cards", page.locator(".lag-card").count() == 3)
    step("findings chart drawn", page.locator(".card .chart .plotly").count() >= 1)
    before = page.locator(".chart-note").first.inner_text()
    page.click(".seg button:has-text('Market move removed')")
    page.wait_for_timeout(400)
    step("market-adjusted view switches", page.locator(".chart-note").first.inner_text() != before)
    page.click(".seg button:has-text('Day by day')")
    page.wait_for_timeout(400)
    step("day-by-day view draws", "each session" in page.locator(".chart-note").first.inner_text())
    step("four checks listed", page.locator(".checks li").count() == 4)
    step("embed snippet offered", "/embed/findings" in page.locator(".code pre").first.inner_text())
    shot(page, "01-findings")
    page.locator(".heatmap button.cell").nth(4).click()  # overnight news -> intraday return, lag 0
    settle(page)
    step(
        "heatmap cell opens it in News vs. prices",
        "/news-vs-prices?" in page.url
        and "lag=0" in page.url
        and "return_type=intraday" in page.url,
        page.url,
    )

    # ---- news vs. prices
    page.click(".seg button:has-text('6 mo')")
    settle(page)
    step("6-month filter updates URL", "months=6" in page.url, page.url)
    step(
        "6-month chart y-title says 6-month", "6-month" in page.locator(".chart").first.inner_text()
    )
    page.click(".seg button:has-text('Spearman')")
    settle(page)
    step(
        "Spearman filter updates URL and heatmap note",
        "method=spearman" in page.url and page.locator(".sub:has-text('Spearman')").count() >= 1,
    )
    cells = page.locator(".heatmap button.cell")
    step("heatmap has 27 cells", cells.count() == 27, f"{cells.count()}")
    cells.nth(3).click()  # overnight news vs intraday return, lag -1
    settle(page)
    hist_title = page.locator("h2:has-text('History')").inner_text()
    step(
        "cell click changes the history",
        "Overnight news vs intraday return" in hist_title and "lag -1" in hist_title,
        hist_title,
    )
    step("picked cell is marked", page.locator(".heatmap button.cell.on").count() == 1)
    page.fill("input[type=date]", "2026-11-04")
    page.dispatch_event("input[type=date]", "change")
    settle(page)
    page.wait_for_selector(
        "section.card:last-of-type .chart .main-svg", state="attached", timeout=8000
    )
    step(
        "session picker reloads the scatter",
        "day=2026-11-04" in page.url
        and ("companies" in page.locator("section.card:last-of-type").inner_text()),
    )
    shot(page, "02-news-vs-prices")

    # ---- old addresses
    page.goto(BASE + "/leadlag?months=6")
    settle(page)
    step("/leadlag redirects", "/news-vs-prices?months=6" in page.url, page.url)
    page.goto(BASE + "/tickers/AAPL")
    settle(page)
    step("/tickers/AAPL redirects", page.url.endswith("/companies/AAPL"), page.url)

    # ---- theme toggle + mobile menu
    page.click("#theme-toggle")
    page.wait_for_timeout(500)
    theme = page.evaluate("document.documentElement.dataset.theme")
    step("theme toggle sets data-theme", theme in ("dark", "light"), theme)
    page.set_viewport_size({"width": 390, "height": 800})
    page.wait_for_timeout(500)
    step(
        "no horizontal scroll on a phone",
        page.evaluate("document.documentElement.scrollWidth <= 390"),
    )
    page.click("#menu-toggle")
    page.wait_for_timeout(300)
    step("phone menu opens", page.locator("#nav.open").count() == 1)
    shot(page, "03-phone-menu")
    page.click("#nav >> text=Companies")
    settle(page)
    step(
        "phone menu closes after navigation",
        page.locator("#nav.open").count() == 0 and page.url.endswith("/companies"),
    )
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
    step("row click opens the company page", page.url.endswith("/companies/NVDA"), page.url)
    step("company page shows headlines", page.locator(".article").count() > 0)
    page.select_option("select[aria-label='Compare with']", "MSFT")
    settle(page)
    step(
        "compare adds a second company",
        "vs=MSFT" in page.url and "MSFT" in page.locator(".chart").first.inner_text(),
        page.url,
    )
    page.select_option("select", index=0)  # 30 days
    settle(page)
    step("range keeps the comparison", "from=" in page.url and "vs=MSFT" in page.url, page.url)
    shot(page, "04-company-compare")

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

    # ---- headlines
    page.goto(BASE + "/headlines")
    settle(page)
    page.fill("input[type=search]", "earnings beat")
    page.click("button:has-text('Search')")
    settle(page)
    n = page.locator(".results .article").count()
    step("search finds headlines", "q=earnings+beat" in page.url and n > 0, f"{n} on the page")
    page.select_option("select[aria-label=Tone]", "negative")
    page.wait_for_url("**sentiment=negative**", timeout=8000)
    page.wait_for_selector(".results .score-chip.negative", timeout=8000)
    settle(page)
    chips = page.locator(".results .score-chip")
    neg = page.locator(".results .score-chip.negative").count()
    step(
        "tone filter keeps only negative",
        "sentiment=negative" in page.url and chips.count() == neg > 0,
        f"{neg}",
    )
    shot(page, "05-headlines")

    # ---- data (prices made private for the private-dataset steps; restored at the end)
    AUTH = {"authorization": f"Bearer {TOKEN}", "content-type": "application/json"}
    page.request.put(
        BASE + "/api/v1/settings", headers=AUTH, data='{"datasets": {"private": ["prices"]}}'
    )
    page.goto(BASE + "/downloads")
    settle(page, 1200)
    step(
        "private prices are not offered in the builder",
        page.locator(".chips label:has-text('Prices')").count() == 0,
    )
    page.click(".chips label:has-text('News')")
    page.click(".seg button:has-text('1 month')")
    page.wait_for_timeout(1200)
    step(
        "estimate shown",
        "up to" in page.locator("[aria-live=polite]").inner_text(),
        page.locator("[aria-live=polite]").inner_text(),
    )
    href = page.locator("a:has-text('Download .tar.gz')").get_attribute("href")
    r = page.request.get(BASE + href)
    step(
        "bundle downloads",
        r.status == 200
        and r.headers.get("content-type") == "application/gzip"
        and len(r.body()) > 1000,
        f"{href} {r.status} {len(r.body())} bytes",
    )
    step("builder state in the URL", "range=1m" in page.url and "news" in page.url, page.url)
    archive = page.locator(".chips a.chip").first.get_attribute("href")
    r = page.request.get(BASE + archive)
    step("monthly archive downloads", r.status == 200, archive)
    latest = page.request.get(BASE + "/data/sentiment/latest.sentiment.parquet")
    step(
        "latest URL serves",
        latest.status == 200
        and latest.headers.get("content-location", "").startswith("/data/sentiment/"),
    )
    page.click("summary:has-text('Prices')")
    settle(page)
    priv = page.locator(".notice.warn:has-text('not published')").count()
    step("prices shows the private notice", priv == 1)
    pq = page.locator("details[open] a:has-text('Parquet')").first.get_attribute("href")
    r = page.request.get(BASE + pq)
    step("private prices file refused without token", r.status == 403, f"{r.status}")
    shot(page, "06-data")

    # ---- methodology, status, embed, feed
    page.goto(BASE + "/methodology#licenses")
    settle(page)
    step("methodology opens at its anchor", page.evaluate("window.scrollY > 300"))
    page.goto(BASE + "/status")
    settle(page)
    step("status shows the services", page.locator(".tile .badge").count() >= 3)
    page.goto(BASE + "/embed/findings?theme=dark")
    settle(page)
    step(
        "embed draws the chart with attribution",
        page.locator(".plotly").count() == 1
        and "CC BY 4.0" in page.locator(".credit").inner_text(),
    )
    shot(page, "07-embed")
    r = page.request.get(BASE + "/feed.xml")
    step("feed serves Atom", r.status == 200 and "<feed" in r.text())

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
    # wait for the newest job to be done (the worker polls every 5 s; a snapshot of a
    # real-sized store takes ~10 s)
    for _ in range(90):
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
    page.goto(BASE + "/downloads")
    settle(page)
    page.click("summary:has-text('Prices')")
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
    step(
        "unknown route gets the 404 page", page.locator("h1:has-text('No such page')").count() == 1
    )

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
