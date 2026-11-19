// Findings (/findings): the answer to the question, as published in a frozen snapshot, with the
// checks behind it, the caveats, how the live numbers compare since, and how to cite,
// embed or download it.

import { api, qs, el, card, clear, fmt, setTitle, link, badge, segmented, codeBlock, copyButton, WINDOW_LABEL, RETURN_LABEL } from "../lib.js";
import { findingsChart, chartNode } from "../charts.js";

const LAGS = [-1, 0, 1];
const WINDOWS = ["overnight", "intraday", "day"];
const RETURNS = ["overnight", "intraday", "daily"];

/** The snapshot to show: ?findings=<id>, else the newest frozen one, else the newest
 * draft, else the live row. */
async function pick(query) {
  const listing = await api("findings").catch(() => ({ items: [] }));
  const items = listing.items || [];
  const wanted = query.get("findings");
  const chosen =
    (wanted && items.find((x) => x.id === wanted)) ||
    items.find((x) => x.kind === "frozen") ||
    items.find((x) => x.kind === "draft") ||
    items.find((x) => x.kind === "live");
  if (!chosen) return { items, doc: null };
  return { items, doc: await api(`findings/${encodeURIComponent(chosen.id)}`) };
}

const dateText = (iso) =>
  new Date(`${iso}T12:00:00Z`).toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });

function rowFor(rows, window, ret, lag) {
  return (rows || []).find((r) => r.window === window && r.return_type === ret && r.lag === lag);
}

export async function render(main, parts, query) {
  setTitle("Findings");
  const { items, doc } = await pick(query);
  if (!doc) {
    clear(main).append(
      el("section", { class: "hero-findings" }, el("p", { class: "kicker", text: "Findings" }), el("h1", { text: "Does news predict stock prices, follow them, or move with them?" }), el("p", { class: "lede", text: "The first findings are computed once a year of data has been analysed. Until then, explore the live numbers." }), el("p", {}, link("/news-vs-prices", "News vs. prices →"))),
    );
    return;
  }
  const live = doc.kind === "live" ? null : await api("findings/live").catch(() => null);
  const text = doc.text || {};
  const w = doc.window;
  const head = doc.headline.lags;

  // ---- hero
  const status =
    doc.kind === "frozen"
      ? badge("published, never changes", "good")
      : doc.kind === "draft"
        ? badge("draft, under review", "warning")
        : badge("live, last 12 months", "accent");
  const hero = el(
    "section",
    { class: "hero-findings" },
    el("p", { class: "kicker", text: doc.kind === "live" ? "Live results" : `Findings · ${text.title || doc.id}` }),
    el("h1", { text: text.headline || "Does news predict stock prices, follow them, or move with them?" }),
    text.summary && text.summary[0] ? el("p", { class: "lede", text: text.summary[0] }) : null,
    el(
      "div",
      { class: "meta-line" },
      status,
      el("span", { text: `Sessions ${dateText(w.start)} to ${dateText(w.end)}` }),
      el("span", { text: `· ${fmt.int(doc.coverage.headlines)} ${doc.meta && doc.meta.source && doc.meta.source !== "all" ? `${doc.meta.source[0].toUpperCase()}${doc.meta.source.slice(1)} ` : ""}headlines · ${doc.coverage.companies} companies` }),
    ),
  );

  // ---- the three answers
  const cardFor = (lag, q, yes, no) => {
    const r = head[String(lag)];
    const shuffle = (doc.placebo || []).find((x) => x.lag === lag);
    const real = r && shuffle && r.r > shuffle.null_high;
    return el(
      "div",
      { class: "lag-card" },
      el("div", { class: "q", text: q }),
      el("div", { class: "r", text: r ? fmt.r(r.r) : "–" }),
      el("div", { class: `verdict ${real ? "yes" : "no"}`, text: real ? yes : no }),
      el("p", { class: "why", text: r ? `95 % interval ${fmt.r(r.ci_low)} to ${fmt.r(r.ci_high)}; n = ${fmt.int(r.n)} company-days` : "" }),
    );
  };
  const cards = el(
    "div",
    { class: "lag-cards" },
    cardFor(-1, "Does news follow prices?", "Yes, a little: headlines trail the previous session's move", "Not detectably"),
    cardFor(0, "Do they move together?", "Yes: the same session's headlines and return line up", "Not detectably"),
    cardFor(1, "Does news predict prices?", "Yes", "No: tomorrow's return is unrelated to today's headlines"),
  );

  // ---- the chart, with its three views
  const node = chartNode("tall");
  const views = {
    pooled: { label: "All company-days", rows: LAGS.map((l) => ({ lag: l, ...head[String(l)] })).filter((r) => r.r !== undefined), note: "Every company and session pooled. Bars: 95 % confidence intervals. Grey: the range of r when each session's headlines are shuffled between companies (1,000 times)." },
    adjusted: { label: "Market move removed", rows: LAGS.map((l) => ({ lag: l, ...rowFor(doc.market_adjusted, "day", "daily", l) })).filter((r) => r.r !== undefined), note: "Each return minus that session's cap-weighted index return, so a good-news day that lifted everything does not count." },
    session: {
      label: "Day by day",
      rows: LAGS.map((l) => {
        const s = rowFor(doc.per_session, "day", "daily", l);
        return s ? { lag: l, r: s.mean_r, ci_low: s.mean_r - 1.96 * s.se, ci_high: s.mean_r + 1.96 * s.se, n: s.sessions } : null;
      }).filter(Boolean),
      note: "The correlation across companies computed for each session separately, then averaged; bars are 95 % intervals across sessions. Robust to days that move together.",
    },
  };
  let view = "pooled";
  const note = el("p", { class: "chart-note" });
  const draw = () => {
    const v = views[view];
    const shuffled = view === "pooled" ? Object.fromEntries((doc.placebo || []).map((x) => [x.lag, [x.null_low, x.null_high]])) : {};
    findingsChart(node, v.rows, { shuffled, yTitle: view === "session" ? "Mean of each session's r" : "Correlation (Pearson r)" });
    note.textContent = v.note;
  };
  const chartCard = card(
    "Headline sentiment against the stock's daily return",
    el("div", {}, el("div", { class: "filters" }, segmented(Object.entries(views).map(([k, v]) => [k, v.label]), view, (k) => { view = k; draw(); })), node, note),
    { sub: "Day headlines (previous close to close) vs the daily return, at a one-session lag either way" },
  );

  // ---- how sure
  const lag0 = head["0"];
  const adj0 = rowFor(doc.market_adjusted, "day", "daily", 0);
  const ses0 = rowFor(doc.per_session, "day", "daily", 0);
  const pl0 = (doc.placebo || []).find((x) => x.lag === 0);
  const ses1 = rowFor(doc.per_session, "day", "daily", 1);
  const check = (ok, what, how, figure) =>
    el("li", {}, el("span", { class: `mark ${ok ? "" : "fail"}`, "aria-hidden": "true", text: ok ? "✓" : "–" }), el("div", {}, el("div", { class: "what", text: what }), el("div", { class: "how", text: how })), el("span", { class: "figure", text: figure }));
  const checks = el(
    "ul",
    { class: "checks" },
    pl0 ? check(pl0.real_r > pl0.null_high, "It is about the right company", `Shuffling headlines between companies within each session gives r between ${fmt.r(pl0.null_low)} and ${fmt.r(pl0.null_high)}.`, `${fmt.r(pl0.real_r)} vs ${fmt.r(pl0.null_mean)}`) : null,
    adj0 ? check(adj0.r > 0 && adj0.ci_low > 0, "It is not the market's mood", "Taking each day's index move out of every return.", fmt.r(adj0.r)) : null,
    ses0 ? check(ses0.t > 3, "It holds day after day", `The same-session correlation, computed each session on its own, is positive on ${Math.round(ses0.share_positive * 100)} % of ${ses0.sessions} sessions (t = ${ses0.t.toFixed(1)}).`, fmt.r(ses0.mean_r)) : null,
    head["1"] && ses1
      ? check(
          head["1"].ci_low <= 0 && head["1"].ci_high >= 0 && Math.abs(ses1.t) < 2,
          "News does not predict tomorrow",
          `Today's headlines against the next session's return: the 95 % interval (${fmt.r(head["1"].ci_low)} to ${fmt.r(head["1"].ci_high)}) includes zero, and so does the day-by-day estimate (t = ${ses1.t.toFixed(1)}).`,
          fmt.r(head["1"].r),
        )
      : null,
  );
  const sureCard = card("How sure are we?", el("div", {}, checks, lag0 ? el("p", { class: "chart-note", text: `r = ${fmt.r(lag0.r)} means headline tone accounts for about ${(lag0.r * lag0.r * 100).toFixed(1)} % of the variation in daily returns: real, and small.` }) : null));

  // ---- every pair (heatmap)
  const heat = heatmap(doc.pairs || [], (r) => navigateTo(r));
  const pairsCard = card("Every news window against every return", el("div", {}, heat, el("p", { class: "chart-note" }, "Pearson r over the whole window. Overnight news runs from the previous close to the open, intraday from open to close. ", link("/news-vs-prices", "Explore these live →"))));

  // ---- splits
  const capRows = (doc.by_cap || []).filter((x) => x.lag === 0);
  const splits = el(
    "div",
    { class: "stack" },
    capRows.length
      ? el("div", {}, el("h3", { text: "By company size" }), el("div", { class: "grid cols-2" }, capRows.map((x) => el("div", { class: "tile" }, el("div", { class: "label", text: `Ranks ${x.ranks}` }), el("div", { class: "value", text: x.r === undefined ? "–" : fmt.r(x.r) }), el("div", { class: "faint small", text: `same session · n = ${fmt.int(x.n)}` })))))
      : null,
  );

  // ---- prose + caveats
  const prose = (text.summary || []).slice(1);
  const proseCard = prose.length ? card("What we found", el("div", { class: "prose" }, prose.map((t) => el("p", { text: t })))) : null;
  const caveats = text.caveats && text.caveats.length ? card("What this does not show", el("ul", { class: "prose" }, text.caveats.map((t) => el("li", { text: t })))) : null;

  // ---- since then (live)
  let sinceCard = null;
  if (live && doc.kind !== "live") {
    const src = (doc.meta && doc.meta.source) || "all";
    const liveSame = (l) =>
      src === "all" ? live.headline.lags[String(l)] : (live.by_source || []).find((x) => x.source === src && x.lag === l && x.r !== undefined);
    const rows = LAGS.map((l) => [l, head[String(l)], liveSame(l), live.headline.lags[String(l)]]);
    sinceCard = card(
      "Since then",
      el(
        "div",
        {},
        el("p", { class: "muted", text: `The same numbers over the last 12 months, recomputed every trading day (to ${dateText(live.window.end)}). The findings above never change; these do. The live figures use every news source; the first column of them uses the same source as the findings, for a like-for-like comparison.` }),
        el(
          "div",
          { class: "table-wrap compact" },
          el(
            "table",
            {},
            el("thead", {}, el("tr", {}, ["", src === "all" ? "Findings" : `Findings (${src})`, ...(src === "all" ? ["Last 12 months"] : [`Last 12 months (${src})`, "Last 12 months (all sources)"])].map((h, i) => el("th", { class: i ? "num" : "", text: h })))),
            el("tbody", {}, rows.map(([l, a, b, c]) => el("tr", {}, el("td", { text: ["News after the move", "Same session", "News before the move"][l + 1] }), el("td", { class: "num", text: a ? fmt.r(a.r) : "–" }), el("td", { class: "num", text: b ? fmt.r(b.r) : "–" }), src === "all" ? null : el("td", { class: "num", text: c ? fmt.r(c.r) : "–" })))),
          ),
        ),
      ),
    );
  }

  // ---- cite, embed, download
  const origin = location.origin;
  const embedUrl = `${origin}/embed/findings${qs({ id: doc.kind === "live" ? "live" : doc.id })}`;
  const imgUrl = `${origin}/api/v1/charts/findings.png${qs({ id: doc.kind === "live" ? "live" : doc.id })}`;
  const iframe = `<iframe src="${embedUrl}" title="Murmur findings: news sentiment vs stock returns" width="100%" height="460" style="border:0" loading="lazy"></iframe>`;
  const imgTag = `<a href="${origin}/"><img src="${imgUrl}" alt="Murmur findings: correlation of headline sentiment with daily stock returns at lags -1, 0 and +1" width="960"></a>`;
  const cite = await api(`citation${qs({ findings: doc.kind === "live" ? undefined : doc.id })}`).catch(() => null);
  const shareCard = card(
    "Use it",
    el(
      "div",
      { class: "grid cols-3" },
      el("div", {}, el("h3", { text: "Cite" }), cite ? el("div", { class: "stack" }, el("p", { class: "small muted", text: cite.apa }), el("div", { class: "actions" }, copyButton(cite.apa, "Copy APA"), copyButton(cite.bibtex, "Copy BibTeX"))) : el("p", { class: "muted", text: "Citation unavailable." })),
      el("div", {}, el("h3", { text: "Embed the chart" }), el("p", { class: "small muted", text: "Interactive, with attribution, follows the reader's theme (add ?theme=dark to force)." }), codeBlock(iframe), el("p", { class: "small muted", style: "margin-top:8px" }, "Or as an image: ", copyButton(imgTag, "Copy image HTML"))),
      el(
        "div",
        {},
        el("h3", { text: "Download" }),
        el("p", { class: "small muted", text: "Every number on this page, with the text and license, as JSON (CC BY 4.0)." }),
        el("div", { class: "actions" }, doc.kind === "frozen" ? el("a", { class: "btn", href: `/data/findings/${doc.id}.json`, download: "" }, "Findings JSON") : el("a", { class: "btn", href: `/api/v1/findings/${doc.id}` }, "Findings JSON"), link("/downloads", "All the data →", { class: "btn" })),
      ),
    ),
  );

  // ---- other snapshots
  const others = items.filter((x) => x.kind === "frozen" && x.id !== doc.id);
  const othersNote = others.length ? el("p", { class: "muted small" }, "Earlier findings: ", others.map((x, i) => [i ? ", " : "", link(`/findings?findings=${x.id}`, `${x.id} (${x.start_day} to ${x.end_day})`)])) : null;

  clear(main).append(
    hero,
    cards,
    el("div", { class: "stack" }, chartCard, el("div", { class: "split" }, sureCard, card("Where it is strongest", splits)), proseCard, pairsCard, caveats, sinceCard, shareCard, othersNote),
  );
  draw();
}

function navigateTo(r) {
  location.assign(`/news-vs-prices${qs({ window: r.window, return_type: r.return_type, lag: r.lag, months: 12 })}`);
}

/** A table heatmap of r for 9 window/return pairs x 3 lags; blue positive, orange
 * negative, saturation by |r| (full at 0.2). Each cell is a button. Shared with the
 * News vs. prices page. */
export function heatmap(rows, onPick, { selected, value = (r) => r.r ?? r.estimate, significant = (r) => (r.p ?? r.p_value) < 0.05 } = {}) {
  const tbody = el("tbody");
  for (const w of WINDOWS) {
    for (const ret of RETURNS) {
      const tr = el("tr", {}, el("th", { class: "row", scope: "row" }, `${WINDOW_LABEL[w]} → ${RETURN_LABEL[ret]}`));
      for (const lag of LAGS) {
        const r = rows.find((x) => x.window === w && x.return_type === ret && x.lag === lag);
        if (!r) {
          tr.append(el("td", {}, el("span", { class: "faint", text: "–" })));
          continue;
        }
        const v = value(r);
        const a = Math.min(1, Math.abs(v) / 0.2) * 0.85;
        const rgb = v >= 0 ? "var(--heat-pos)" : "var(--heat-neg)";
        const on = selected && selected.window === w && selected.return_type === ret && selected.lag === lag;
        const b = el(
          "button",
          { type: "button", class: `cell ${on ? "on" : ""}`, style: `background: rgba(${rgb}, ${a.toFixed(3)})`, "aria-label": `${WINDOW_LABEL[w]} vs ${RETURN_LABEL[ret]}, lag ${lag}: r ${fmt.r(v)}`, "aria-pressed": on ? "true" : "false" },
          fmt.r(v),
          significant(r) ? null : el("span", { class: "ns", text: "not significant" }),
        );
        b.addEventListener("click", () => onPick(r));
        tr.append(el("td", {}, b));
      }
      tbody.append(tr);
    }
  }
  return el(
    "div",
    {},
    el("div", { class: "table-wrap" }, el("table", { class: "heatmap" }, el("thead", {}, el("tr", {}, el("th", { class: "row", text: "News window → return" }), el("th", { text: "News after (−1)" }), el("th", { text: "Same session (0)" }), el("th", { text: "News before (+1)" }))), tbody)),
    el("div", { class: "heat-legend" }, el("span", { text: "negative" }), el("span", { class: "ramp", style: "background: linear-gradient(90deg, rgba(var(--heat-neg), .85), transparent 50%, rgba(var(--heat-pos), .85))" }), el("span", { text: "positive (full colour at |r| ≥ 0.2)" })),
  );
}
