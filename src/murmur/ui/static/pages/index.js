// The index (Murmur 100): the daily level and the latest session's biggest moves,
// constituents with rank and weight, this month's changes.

import { api, qs, el, card, tile, clear, table, fmt, state, setTitle, field, select, link, navigate } from "../app.js";
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
  const [data, changes, level, latest] = await Promise.all([
    api(`index/${idx.name}${qs({ month })}`),
    api(`index/${idx.name}/changes${qs({ month })}`),
    api(`index/${idx.name}/level`).catch(() => ({ items: [] })),
    api(`index/${idx.name}/level/latest`).catch(() => null), // 404 until the first level
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
  const levelCard = levelSection(idx, level, latest, levelNode);

  clear(main).append(head, filters, el("div", { class: "stack" }, levelCard, card("Top 20 by weight", el("div", {}, weightsNode, el("p", { class: "chart-note", text: `Together ${fmt.pct0(top.reduce((a, r) => a + (r.weight || 0), 0))} of the index.` }))), changesCard, card(`All ${items.length} constituents`, el("div", {}, constituents, downloads))));
  barChart(weightsNode, top.map((r) => ({ label: r.ticker, value: r.weight })), { percent: true, horizontal: true });
  const points = level.items || [];
  if (points.length >= 2) {
    lineChart(levelNode, [{ name: idx.title, x: points.map((r) => r.date), y: points.map((r) => r.level) }], { yTitle: "Level", zero: false });
  }
}

// The level: three numbers, the chart, and the day's five biggest moves each way.
function levelSection(idx, level, latest, node) {
  const points = level.items || [];
  if (points.length < 2 || !latest) {
    return card("The level", el("div", { class: "empty", text: "The daily level appears after the first analysis with prices." }));
  }
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
  return card(
    "The level",
    el(
      "div",
      {},
      tiles,
      node,
      el("p", { class: "chart-note", text: `Base ${fmt.int(level.base_level)} at the close of ${first.date}. Each session moves by the members' daily returns weighted by their holdings; holdings drift with prices and reset to the month's weights at the close of its first session.` }),
      el(
        "div",
        { class: "grid cols-2" },
        el("div", {}, el("h3", { text: `Pushed it up on ${latest.date}` }), up.length ? moveTable(up) : el("p", { class: "faint small", text: "none" })),
        el("div", {}, el("h3", { text: `Pulled it down on ${latest.date}` }), down.length ? moveTable(down) : el("p", { class: "faint small", text: "none" })),
      ),
    ),
  );
}
