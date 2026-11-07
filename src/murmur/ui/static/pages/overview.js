// Overview: the question, today's numbers, the lead/lag chart, and the history of
// the headline correlation.

import { api, qs, el, card, tile, clear, link, fmt, state, setTitle, pairLabel, MATCHED, daysAgo } from "../app.js";
import { leadlagChart, lineChart, chartNode } from "../charts.js";

export async function render(main, parts, query) {
  setTitle("");
  const ui = state.ui;
  const months = Number(query.get("months") || ui.default_months || 3);
  const method = query.get("method") || ui.default_method || "pearson";

  const [latest, leadlag, days, placebo] = await Promise.all([
    api("latest"),
    api(`leadlag${qs({ months, method, source: "all", scope: "pooled" })}`),
    api(`days${qs({ from: daysAgo(30) })}`),
    api("placebo").catch(() => null), // 404 until the first analysis with 0.8.0
  ]);

  const head = el(
    "div",
    { class: "page-head" },
    el("h1", { text: ui.tagline || "Does news predict prices, follow them, or move with them?" }),
  );

  // ---- today's numbers
  const pairs = Object.fromEntries((latest.matched_pairs || []).map((r) => [`${r.window}/${r.return_type}`, r]));
  const headline = pairs["day/daily"];
  const tiles = el("div", { class: "grid tiles" });
  const day = latest.latest_analysis_day;
  tiles.append(
    tile("Latest analysed session", day ? fmt.date(day) : "–", {
      note: latest.last_run ? `last run ${fmt.ago(latest.last_run.finished_at || latest.last_run.started_at)}` : undefined,
    }),
  );
  for (const [w, r] of MATCHED) {
    const row = pairs[`${w}/${r}`];
    tiles.append(
      tile(pairLabel(w, r), row ? fmt.r(row.estimate) : "–", {
        delta: row ? `n = ${row.n} · p ${fmt.p(row.p_value)}` : "no pairs that day",
        deltaClass: row && row.p_value < 0.05 ? (row.estimate > 0 ? "up" : "down") : "",
        note: "same session, that day",
      }),
    );
  }
  if (latest.day) {
    tiles.append(tile("Headlines that session", fmt.int(latest.day.n_articles), { note: `${fmt.int(latest.day.n_prices)} companies priced` }));
  }

  // ---- lead/lag
  const pooled = (leadlag.items || []).filter((x) => x.scope === "pooled");
  const llNode = chartNode("tall");
  const llCard = card(
    "Lead or lag",
    el(
      "div",
      {},
      pooled.length ? llNode : el("div", { class: "empty", text: "Not enough paired days yet for a rolling window. Check back after a few more sessions." }),
      el(
        "p",
        { class: "chart-note" },
        `Pooled over companies and days, ${months}-month window to ${leadlag.as_of || "–"}, ${method === "spearman" ? "Spearman" : "Pearson"} with 95 % confidence bars. ` +
          "Lag −1 correlates a session's news with the previous session's return, +1 with the next. ",
        link(`/leadlag${qs({ months, method })}`, "Explore every window, source and horizon →"),
      ),
      shuffleNote(placebo, months),
    ),
    { sub: headline ? `Headline today: ${fmt.r(headline.estimate)} (n ${headline.n})` : undefined },
  );

  // ---- history of the headline correlation
  const histNode = chartNode();
  const histCard = card(
    "How the same-session correlation has moved",
    el("div", {}, histNode, el("p", { class: "chart-note", text: `Day news vs daily return, lag 0, rolling ${months}-month window as of each session, with its confidence band.` })),
  );

  // ---- recent days table
  const recent = (days.items || []).filter((d) => d.trading_day).slice(-10).reverse();
  const daysCard = card(
    "Recent sessions",
    el(
      "div",
      { class: "table-wrap compact" },
      el(
        "table",
        {},
        el("thead", {}, el("tr", {}, ["Session", "Headlines", "Prices", "Returns", "Analysis"].map((h, i) => el("th", { class: i ? "num" : "" }, h)))),
        el(
          "tbody",
          {},
          recent.map((d) =>
            el(
              "tr",
              {},
              el("td", { class: "nowrap" }, link(`/leadlag${qs({ day: d.date })}`, d.date)),
              el("td", { class: "num" }, fmt.int(d.n_articles)),
              el("td", { class: "num" }, d.has_prices ? "✓" : "–"),
              el("td", { class: "num" }, d.has_returns ? "✓" : "–"),
              el("td", { class: "num" }, d.has_analysis ? "✓" : "–"),
            ),
          ),
        ),
      ),
    ),
    { sub: "Trading days in the last 30 days" },
  );

  clear(main).append(head, tiles, el("div", { class: "stack", style: "margin-top:14px" }, llCard, histCard, daysCard));

  if (pooled.length) leadlagChart(llNode, pooled, { horizon: months, method });
  api(`leadlag/history${qs({ window: "day", return_type: "daily", lag: 0, months, method, source: "all" })}`)
    .then((h) => {
      const items = h.items || [];
      if (items.length < 2) {
        histNode.replaceWith(el("div", { class: "empty", text: "The history needs a few sessions of rolling results." }));
        return;
      }
      lineChart(histNode, [{ name: "Pearson r", x: items.map((i) => i.as_of), y: items.map((i) => i.estimate), ci: items.map((i) => [i.ci_low, i.ci_high]) }], {
        yTitle: method === "spearman" ? "Spearman ρ" : "Pearson r",
      });
    })
    .catch(() => histNode.replaceWith(el("div", { class: "empty", text: "History unavailable." })));
}

// The shuffle (placebo) test for the selected horizon, lag 0: is the correlation tied to
// the right company on the right day, or would any pairing of that day's numbers do?
function shuffleNote(placebo, months) {
  const row = (placebo?.items || []).find((x) => x.months === months && x.lag === 0);
  if (!row) return null;
  const real = el("strong", { text: fmt.r(row.real_r) });
  const z = row.z === null ? "" : `, z = ${row.z.toFixed(1)}`;
  return el(
    "p",
    { class: "chart-note", "data-test": "shuffle-note" },
    "Shuffle test, lag 0: the real r is ",
    real,
    `; with each session's headlines shuffled across companies ${row.permutations} times it is ` +
      `${fmt.r(row.null_mean)} ± ${row.null_sd.toFixed(3)} (95 % of shuffles between ` +
      `${fmt.r(row.null_low)} and ${fmt.r(row.null_high)}${z}). ` +
      (row.real_r > row.null_high || row.real_r < row.null_low
        ? "The signal belongs to the right company on the right day."
        : "Not distinguishable from shuffled pairings at this horizon."),
  );
}
