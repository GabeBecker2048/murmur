// The index (Murmur 100): the daily level over any range (optionally against the S&P
// 500) and the latest session's biggest moves, constituents with rank and weight, this
// month's changes.

import { api, qs, el, card, tile, clear, table, fmt, state, setTitle, field, select, segmented, link, navigate } from "../app.js";
import { barChart, lineChart, chartNode } from "../charts.js";

export async function render(main, parts, query) {
  const idx = state.ui.index || { name: "m100", title: "Murmur 100", size: 100 };
  setTitle(idx.title);
  const months = await api(`index/${idx.name}/months`).catch(() => ({ months: [] }));
  const month = query.get("month") || (months.months || []).slice(-1)[0];
  if (!month) {
    clear(main).append(card(idx.title, el("div", { class: "empty", text: "No constituents yet. The index is built on the first run of each month." })));
    return;
  }
  const [data, changes, level, latest, bench] = await Promise.all([
    api(`index/${idx.name}${qs({ month })}`),
    api(`index/${idx.name}/changes${qs({ month })}`),
    api(`index/${idx.name}/level`).catch(() => ({ items: [] })),
    api(`index/${idx.name}/level/latest`).catch(() => null), // 404 until the first level
    api(`index/${idx.name}/benchmark`).catch(() => ({ items: [] })),
  ]);
  const items = data.items || [];
  const top = items.slice(0, 20);

  const weightsNode = chartNode("tall");
  const head = el(
    "div",
    { class: "page-head" },
    el("h1", { text: data.title || idx.title }),
    el("p", { class: "lede", text: `The ${data.size} largest US-domiciled companies on NYSE and Nasdaq by market cap, rebalanced on the first trading day of each month. Weight is each company's share of the total cap. ${data.covered < data.size ? `The top ${data.covered} are covered by the news pipeline.` : ""}` }),
  );
  const filters = el("div", { class: "filters" }, field("Month", select((months.months || []).map((m) => [m, m]), month, (v) => navigate(`/index${qs({ month: v })}`))), el("span", { class: "muted small" }, `in force from ${data.effective_date}`));

  const changeList = (rows, label) =>
    el("div", {}, el("h3", { text: `${label} (${rows.length})` }), rows.length ? el("ul", { style: "margin:4px 0 0 18px; padding:0" }, rows.map((r) => el("li", {}, link(`/tickers/${r.ticker}`, r.ticker, { class: "mono" }), ` ${r.name}`, el("span", { class: "faint" }, ` · rank ${r.rank}`)))) : el("p", { class: "faint small", text: "none" }));
  const changesCard = card("Changes this month", el("div", { class: "grid cols-2" }, changeList(changes.added || [], "Added"), changeList(changes.removed || [], "Removed")), { sub: "versus the previous month on record" });

  const constituents = table(
    [
      { label: "#", num: true, key: "rank" },
      { label: "Ticker", value: (r) => link(`/tickers/${r.ticker}`, r.ticker, { class: "mono" }) },
      { label: "Company", key: "name" },
      { label: "CIK", num: true, value: (r) => (r.cik ?? "–") },
      { label: "Market cap", num: true, value: (r) => fmt.compact(r.market_cap) },
      { label: "Weight", num: true, value: (r) => fmt.pct0(r.weight) },
    ],
    items,
    { compact: true, onRow: (r) => navigate(`/tickers/${r.ticker}`) },
  );

  const downloads = el(
    "p",
    { class: "chart-note" },
    "Download this month: ",
    el("a", { href: `/data/${idx.name}/${month.slice(0, 4)}/${month.slice(5, 7)}/${month.replace("-", "")}.constituents.parquet` }, "Parquet"),
    " · ",
    el("a", { href: `/data/${idx.name}/${month.slice(0, 4)}/${month.slice(5, 7)}/${month.replace("-", "")}.constituents.parquet?format=csv` }, "CSV"),
    " · CC BY 4.0. ",
    link("/downloads", "All datasets →"),
  );

  const levelNode = chartNode();
  const { card: levelCard, draw: drawLevel } = levelSection(idx, level, latest, bench, levelNode, query);

  clear(main).append(head, filters, el("div", { class: "stack" }, levelCard, card("Top 20 by weight", el("div", {}, weightsNode, el("p", { class: "chart-note", text: `Together ${fmt.pct0(top.reduce((a, r) => a + (r.weight || 0), 0))} of the index.` }))), changesCard, card(`All ${items.length} constituents`, el("div", {}, constituents, downloads))));
  barChart(weightsNode, top.map((r) => ({ label: r.ticker, value: r.weight })), { percent: true, horizontal: true });
  drawLevel();
}

const PRESETS = [
  ["1m", "1M"],
  ["3m", "3M"],
  ["6m", "6M"],
  ["ytd", "YTD"],
  ["1y", "1Y"],
  ["all", "All"],
];

// The first day of a preset range ending on `last` (YYYY-MM-DD), or null for all.
function presetStart(key, last) {
  const d = new Date(`${last}T00:00:00Z`);
  if (key === "ytd") return `${last.slice(0, 4)}-01-01`;
  const months = { "1m": 1, "3m": 3, "6m": 6, "1y": 12 }[key];
  if (!months) return null;
  d.setUTCMonth(d.getUTCMonth() - months);
  return d.toISOString().slice(0, 10);
}

// Keep the chart's range and comparison in the address bar without re-rendering.
function remember(params) {
  const url = new URL(location.href);
  for (const [k, v] of Object.entries(params)) {
    if (v === null || v === undefined || v === "") url.searchParams.delete(k);
    else url.searchParams.set(k, v);
  }
  history.replaceState(null, "", url.pathname + url.search);
}

// The level chart over a chosen range. With the comparison on, both lines become the
// change since the range's first session, so they share one axis from the same start.
function levelChart(idx, points, benchItems, node, query) {
  const first = points[0].date;
  const last = points[points.length - 1].date;
  const benchName = benchItems.title || "S&P 500";
  const closes = benchItems.items || [];
  // A preset, or "custom" when the dates were typed (then no preset is lit).
  let range = query.get("from") || query.get("to") ? "custom" : query.get("range") || "all";
  if (range !== "custom" && !PRESETS.some(([k]) => k === range)) range = "all";
  let from = query.get("from") || first;
  let to = query.get("to") || last;
  let compare = closes.length > 1 && query.get("compare") !== "0";

  const fromInput = el("input", { type: "date", min: first, max: last, value: from, "aria-label": "From" });
  const toInput = el("input", { type: "date", min: first, max: last, value: to, "aria-label": "To" });
  const summary = el("p", { class: "chart-note" });
  const presets = segmented(PRESETS, range, (v) => {
    range = v;
    from = presetStart(v, last) || first;
    to = last;
    if (from < first) from = first;
    fromInput.value = from;
    toInput.value = to;
    remember({ range: v === "all" ? null : v, from: null, to: null });
    draw();
  });
  const onDate = () => {
    from = fromInput.value || first;
    to = toInput.value || last;
    if (from > to) [from, to] = [to, from];
    range = "custom";
    for (const b of presets.children) b.classList.remove("on");
    remember({ range: null, from, to });
    draw();
  };
  fromInput.addEventListener("change", onDate);
  toInput.addEventListener("change", onDate);
  const compareBox = el("input", { type: "checkbox", checked: compare || undefined });
  compareBox.addEventListener("change", () => {
    compare = compareBox.checked;
    remember({ compare: compare ? null : "0" });
    draw();
  });
  if (range !== "custom") {
    from = presetStart(range, last) || first;
    if (from < first) from = first;
    to = last;
    fromInput.value = from;
    toInput.value = to;
  }

  const controls = el(
    "div",
    { class: "filters", style: "margin:12px 0 4px" },
    presets,
    field("From", fromInput),
    field("To", toInput),
    closes.length > 1 ? el("label", { class: "check" }, compareBox, `Compare with the ${benchName}`) : null,
  );

  function draw() {
    const shown = points.filter((p) => p.date >= from && p.date <= to);
    if (shown.length < 2) {
      window.Plotly?.purge(node);
      node.textContent = "";
      node.append(el("div", { class: "empty", text: "Pick a range with at least two sessions." }));
      summary.textContent = "";
      return;
    }
    if (node.querySelector(".empty")) node.textContent = ""; // leaving the empty state
    const start = shown[0];
    const end = shown[shown.length - 1];
    const idxChange = end.level / start.level - 1;
    if (!compare) {
      lineChart(node, [{ name: idx.title, x: shown.map((r) => r.date), y: shown.map((r) => r.level) }], { yTitle: "Level", yFormat: ".2f", zero: false });
      summary.textContent = `${start.date} to ${end.date}: ${idx.title} ${fmt.pct(idxChange)} (${start.level.toFixed(2)} to ${end.level.toFixed(2)}).`;
      return;
    }
    // The benchmark's base is its close on the range's first session (or the last
    // close before it), so both lines start at 0 %.
    const base = [...closes].reverse().find((c) => c.date <= start.date) || closes[0];
    const inRange = closes.filter((c) => c.date >= start.date && c.date <= end.date);
    const benchEnd = inRange.length ? inRange[inRange.length - 1] : base;
    const benchChange = benchEnd.close / base.close - 1;
    lineChart(
      node,
      [
        { name: idx.title, x: shown.map((r) => r.date), y: shown.map((r) => r.level / start.level - 1) },
        { name: benchName, x: inRange.map((c) => c.date), y: inRange.map((c) => c.close / base.close - 1) },
      ],
      { yTitle: `Change since ${start.date}`, percent: true },
    );
    const gap = (idxChange - benchChange) * 100;
    summary.textContent = `${start.date} to ${end.date}: ${idx.title} ${fmt.pct(idxChange)}, ${benchName} ${fmt.pct(benchChange)} (${gap >= 0 ? "ahead by" : "behind by"} ${Math.abs(gap).toFixed(2)} percentage points). Both lines are the change since the first session shown; the ${benchName} closes come from Yahoo Finance and are shown for comparison only.`;
  }

  return { controls, summary, draw };
}

// The level: three numbers, the chart, and the day's five biggest moves each way.
function levelSection(idx, level, latest, bench, node, query) {
  const points = level.items || [];
  if (points.length < 2 || !latest) {
    return { card: card("The level", el("div", { class: "empty", text: "The daily level appears after the first analysis with prices." })), draw: () => {} };
  }
  const chart = levelChart(idx, points, bench || { items: [] }, node, query);
  const first = points[0];
  const tiles = el(
    "div",
    { class: "grid tiles" },
    tile(`${idx.title} on ${fmt.date(latest.date)}`, latest.level.toFixed(2), {
      delta: `${fmt.pct(latest.return)} that session`,
      deltaClass: latest.return > 0 ? "up" : latest.return < 0 ? "down" : "",
    }),
    tile(`Since ${fmt.date(first.date)}`, fmt.pct(latest.level / first.level - 1), { note: `base ${fmt.int(level.base_level)}` }),
    tile("Priced that session", `${latest.priced} of ${latest.constituents}`, { note: `${fmt.pct0(latest.weight_priced)} of the weight` }),
  );
  const moves = latest.contributions || [];
  const up = moves.filter((c) => (c.contribution ?? 0) > 0).slice(0, 5);
  const down = moves.filter((c) => (c.contribution ?? 0) < 0).slice(-5).reverse();
  const moveTable = (rows) =>
    table(
      [
        { label: "Ticker", value: (r) => link(`/tickers/${r.ticker}`, r.ticker, { class: "mono" }) },
        { label: "Weight", num: true, value: (r) => fmt.pct0(r.weight) },
        { label: "Return", num: true, value: (r) => fmt.pct(r.return) },
        { label: "Points", num: true, value: (r) => (r.points >= 0 ? "+" : "−") + Math.abs(r.points).toFixed(2) },
      ],
      rows,
      { compact: true },
    );
  const levelCard = card(
    "The level",
    el(
      "div",
      {},
      tiles,
      chart.controls,
      node,
      chart.summary,
      el("p", { class: "chart-note", text: `Base ${fmt.int(level.base_level)} at the close of ${first.date}. Each session moves by the members' daily returns weighted by their holdings; holdings drift with prices and reset to the month's weights at the close of its first session.` }),
      el(
        "div",
        { class: "grid cols-2" },
        el("div", {}, el("h3", { text: `Pushed it up on ${latest.date}` }), up.length ? moveTable(up) : el("p", { class: "faint small", text: "none" })),
        el("div", {}, el("h3", { text: `Pulled it down on ${latest.date}` }), down.length ? moveTable(down) : el("p", { class: "faint small", text: "none" })),
      ),
    ),
  );
  return { card: levelCard, draw: chart.draw };
}
