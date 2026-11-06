// Sources: coverage per news source, overlap, and how each source's own
// correlations compare on the same days.

import { api, qs, el, card, clear, table, fmt, setTitle, pairLabel, MATCHED, daysAgo, badge } from "../app.js";
import { lineChart, chartNode } from "../charts.js";

export async function render(main) {
  setTitle("Sources");
  const data = await api("sources");
  const items = data.items || [];
  const configured = new Set(data.configured || []);

  const coverage = table(
    [
      { label: "Source", value: (r) => el("span", {}, el("span", { class: "mono" }, r.source), " ", configured.has(r.source) ? badge("active", "good") : badge("off")) },
      { label: "Headlines", num: true, value: (r) => fmt.int(r.n_articles) },
      { label: "Shared with another source", num: true, value: (r) => (r.n_articles ? `${fmt.int(r.n_shared)} (${((100 * r.n_shared) / r.n_articles).toFixed(0)}%)` : "–") },
      { label: "Companies", num: true, value: (r) => fmt.int(r.n_tickers) },
      { label: "Sessions", num: true, value: (r) => fmt.int(r.n_days) },
      { label: "First", value: (r) => fmt.date(r.first_day) },
      { label: "Last", value: (r) => fmt.date(r.last_day) },
    ],
    items,
    { compact: true, empty: "No sources have reported yet." },
  );

  // per-source matched pairs at 3 months
  const compareRows = [];
  const pooled = data.pooled_rolling_3m || [];
  const allSources = [{ source: "all", rolling_3m: pooled }, ...items];
  for (const s of allSources) {
    const by = Object.fromEntries((s.rolling_3m || []).filter((r) => r.lag === 0).map((r) => [`${r.window}/${r.return_type}`, r]));
    if (!Object.keys(by).length) continue;
    compareRows.push({ source: s.source, ...Object.fromEntries(MATCHED.map(([w, r]) => [`${w}/${r}`, by[`${w}/${r}`]])) });
  }
  const cell = (r) => (r ? el("span", { class: r.p_value < 0.05 ? (r.estimate > 0 ? "pos" : "neg") : "" }, `${fmt.r(r.estimate)} `, el("span", { class: "faint small" }, `n ${r.n}`)) : "–");
  const compare = table(
    [
      { label: "Source", value: (r) => (r.source === "all" ? "All sources (pooled)" : el("span", { class: "mono" }, r.source)) },
      ...MATCHED.map(([w, ret]) => ({ label: pairLabel(w, ret), num: true, value: (r) => cell(r[`${w}/${ret}`]) })),
    ],
    compareRows,
    { compact: true, empty: "Per-source results appear once more than one source has reported for a while." },
  );

  const seriesNode = chartNode();
  const seriesCard = card("Headlines per session, by source", items.length ? el("div", {}, seriesNode, el("p", { class: "chart-note", text: "Last 60 days. Each source counted on its own, so a shared headline appears under both." })) : el("div", { class: "empty", text: "Nothing to plot yet." }));

  clear(main).append(
    el("div", { class: "page-head" }, el("h1", { text: "Sources" }), el("p", { class: "lede", text: "Every headline carries the sources that reported it. Correlations are computed pooled and per source, so the sources can be compared on the same days and companies." })),
    el("div", { class: "stack" }, card("Coverage", coverage), card("Same-session correlation by source", el("div", {}, compare, el("p", { class: "chart-note", text: `3-month window as of ${data.as_of || "–"}, Pearson, lag 0.` }))), seriesCard),
  );

  if (items.length) {
    const from = daysAgo(60);
    const series = await Promise.all(items.slice(0, 5).map((s) => api(`sources/${s.source}/series${qs({ from })}`).catch(() => ({ items: [] }))));
    lineChart(
      seriesNode,
      series.map((s, i) => ({ name: items[i].source, x: (s.items || []).map((r) => r.date), y: (s.items || []).map((r) => r.n_articles), color: i })),
      { yTitle: "headlines", yFormat: ",d", markers: true },
    );
  }
}
