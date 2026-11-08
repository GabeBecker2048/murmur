// Lead/lag explorer: horizon, method and source filters above everything; the chart,
// the full window x return x lag table, the history of one cell, and one session's
// cross-section.

import { api, qs, el, card, clear, table, fmt, state, setTitle, field, select, segmented, navigate, pairLabel, WINDOW_LABEL, RETURN_LABEL, LAG_LABEL, link } from "../app.js";
import { leadlagChart, lineChart, scatterChart, chartNode } from "../charts.js";

const HORIZONS = [1, 3, 6, 12];
const WINDOWS = ["overnight", "intraday", "day"];
const RETURNS = ["overnight", "intraday", "daily"];

export async function render(main, parts, query) {
  setTitle("Lead or lag");
  const ui = state.ui;
  const f = {
    months: Number(query.get("months") || ui.default_months || 3),
    method: query.get("method") || ui.default_method || "pearson",
    source: query.get("source") || "all",
    window: query.get("window") || "day",
    return_type: query.get("return_type") || "daily",
    lag: Number(query.get("lag") || 0),
    day: query.get("day") || "",
    trend: query.get("trend") === "0" ? "0" : "", // the session scatter's trendline: on unless "0"
  };
  const sources = await api("sources").catch(() => ({ items: [] }));
  const sourceNames = ["all", ...(sources.items || []).map((s) => s.source).filter((s) => s !== "all")];

  const go = () => navigate(`/leadlag${qs(f)}`, { replace: true });
  const filters = el(
    "div",
    { class: "filters" },
    field("Window", segmented(HORIZONS.map((h) => [h, `${h} mo`]), f.months, (v) => { f.months = Number(v); go(); })),
    field("Method", segmented([["pearson", "Pearson"], ["spearman", "Spearman"]], f.method, (v) => { f.method = v; go(); })),
    field("Source", select(sourceNames.map((s) => [s, s === "all" ? "All sources (pooled)" : s]), f.source, (v) => { f.source = v; go(); })),
  );

  const [leadlag, history] = await Promise.all([
    api(`leadlag${qs({ months: f.months, method: f.method, source: f.source, scope: "pooled" })}`),
    api(`leadlag/history${qs({ window: f.window, return_type: f.return_type, lag: f.lag, months: f.months, method: f.method, source: f.source })}`),
  ]);
  const rows = (leadlag.items || []).filter((x) => x.scope === "pooled");

  // ---- chart
  const llNode = chartNode("tall");
  const chartCard = card(
    "The three matched pairs by lag",
    rows.length ? el("div", {}, llNode, el("p", { class: "chart-note", text: `As of ${leadlag.as_of}. Each point pools every company and session in the window; bars are 95 % confidence intervals.` })) : el("div", { class: "empty", text: "No rolling results for this selection yet." }),
  );

  // ---- full table
  const byKey = Object.fromEntries(rows.map((r) => [`${r.window}|${r.return_type}|${r.lag}`, r]));
  const tableRows = [];
  for (const w of WINDOWS) for (const r of RETURNS) for (const lag of [-1, 0, 1]) {
    const row = byKey[`${w}|${r}|${lag}`];
    if (row) tableRows.push(row);
  }
  const cell = (r) => {
    const sig = r.p_value !== null && r.p_value < 0.05;
    return el("span", { class: sig ? (r.estimate > 0 ? "pos" : "neg") : "", style: sig ? "font-weight:600" : "" }, fmt.r(r.estimate));
  };
  const fullTable = table(
    [
      { label: "News window", value: (r) => WINDOW_LABEL[r.window] || r.window },
      { label: "Return", value: (r) => RETURN_LABEL[r.return_type] || r.return_type },
      { label: "Lag", value: (r) => `${r.lag > 0 ? "+" : ""}${r.lag} · ${LAG_LABEL[r.lag]}` },
      { label: f.method === "spearman" ? "Spearman rho" : "Pearson r", num: true, value: cell },
      { label: "95 % CI", num: true, value: (r) => (r.ci_low === null ? "–" : `${fmt.r2(r.ci_low)} … ${fmt.r2(r.ci_high)}`) },
      { label: "p", num: true, value: (r) => fmt.p(r.p_value) },
      { label: "n", num: true, value: (r) => fmt.int(r.n) },
    ],
    tableRows,
    {
      compact: true,
      onRow: (r) => { f.window = r.window; f.return_type = r.return_type; f.lag = r.lag; go(); },
      empty: "No rows for this selection.",
    },
  );
  const tableCard = card("Every window, return and lag", el("div", {}, fullTable, el("p", { class: "chart-note", text: "Bold green or red: p < 0.05. Click a row to see its history below." })), {
    sub: `${f.months}-month window, ${f.source === "all" ? "all sources" : f.source}`,
  });

  // ---- history of the selected cell
  const histNode = chartNode();
  const items = history.items || [];
  const histCard = card(
    `History: ${pairLabel(f.window, f.return_type)}, lag ${f.lag > 0 ? "+" : ""}${f.lag}`,
    items.length >= 2 ? el("div", {}, histNode, el("p", { class: "chart-note", text: `Rolling ${f.months}-month ${f.method} as of each session, with its 95 % band.` })) : el("div", { class: "empty", text: "Fewer than two sessions of history for this cell." }),
  );

  // ---- one session's cross-section
  const dayWrap = el("div", { class: "stack" });
  const dayInput = el("input", { type: "date", value: f.day || leadlag.as_of || "" });
  dayInput.addEventListener("change", () => { f.day = dayInput.value; go(); });
  // Toggling the trendline redraws in place and keeps ?trend=0 in the address bar.
  const trendBox = el("input", { type: "checkbox", checked: f.trend !== "0" || undefined });
  const dayCard = card("One session up close", el("div", {}, el("div", { class: "filters" }, field("Session", dayInput), el("label", { class: "check" }, trendBox, "Show trendline")), dayWrap), { sub: "Per-company sentiment against that day's return" });

  clear(main).append(
    el("div", { class: "page-head" }, el("h1", { text: "Lead or lag" }), el("p", { class: "lede", text: "Correlate each news window with each return at a one-session lead and lag. A positive lag +1 means today's news lines up with tomorrow's return." })),
    filters,
    el("div", { class: "stack" }, chartCard, tableCard, histCard, dayCard),
  );
  if (rows.length) leadlagChart(llNode, rows, { horizon: f.months, method: f.method });
  if (items.length >= 2) {
    lineChart(histNode, [{ name: f.method === "spearman" ? "Spearman ρ" : "Pearson r", x: items.map((i) => i.as_of), y: items.map((i) => i.estimate), ci: items.map((i) => [i.ci_low, i.ci_high]) }], {
      yTitle: f.method === "spearman" ? "Spearman ρ" : "Pearson r",
    });
  }
  renderDay(dayWrap, f.day || leadlag.as_of, f, trendBox);
}

async function renderDay(wrap, day, f, trendBox) {
  if (!day) {
    wrap.append(el("div", { class: "empty", text: "No analysed session yet." }));
    return;
  }
  let d;
  try {
    d = await api(`days/${day}`);
  } catch (e) {
    wrap.append(el("div", { class: "empty", text: e.status === 404 ? `No data for ${day}.` : e.message }));
    return;
  }
  const returns = d.returns || {};
  const ws = (d.window_sentiment || []).filter((x) => x.window === f.window && x.source === (f.source === "all" ? "all" : f.source));
  const points = ws
    .filter((x) => returns[x.ticker] && returns[x.ticker][f.return_type] !== null && returns[x.ticker][f.return_type] !== undefined)
    .map((x) => ({ x: x.mean_score, y: returns[x.ticker][f.return_type], label: `${x.ticker} (${x.n_articles})` }));
  const corr = (d.correlations || []).find((c) => c.window === f.window && c.return_type === f.return_type && c.lag === 0 && c.source === (f.source === "all" ? "all" : f.source) && c.method === f.method);
  const node = chartNode();
  clear(wrap).append(
    points.length >= 3 ? node : el("div", { class: "empty", text: `Fewer than three companies had both ${WINDOW_LABEL[f.window].toLowerCase()} and a ${RETURN_LABEL[f.return_type]} on ${day}.` }),
    el(
      "p",
      { class: "chart-note" },
      corr ? `Same-session ${f.method}: ${fmt.r(corr.estimate)} (n ${corr.n}, p ${fmt.p(corr.p_value)}). ` : "",
      `${points.length} companies. Hover a point for the company. `,
      link(`/tickers`, "Company pages →"),
    ),
  );
  if (points.length < 3) return;
  const draw = () =>
    scatterChart(node, points, { xTitle: `${WINDOW_LABEL[f.window]} sentiment (mean score)`, yTitle: RETURN_LABEL[f.return_type], percentY: true, trend: trendBox.checked });
  trendBox.onchange = () => {
    f.trend = trendBox.checked ? "" : "0";
    history.replaceState(null, "", `/leadlag${qs(f)}`);
    draw();
  };
  draw();
}
