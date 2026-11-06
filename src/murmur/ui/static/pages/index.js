// The index (Murmur 100): constituents with rank and weight, this month's changes,
// and a company's history.

import { api, qs, el, card, clear, table, fmt, state, setTitle, field, select, link, navigate } from "../app.js";
import { barChart, chartNode } from "../charts.js";

export async function render(main, parts, query) {
  const idx = state.ui.index || { name: "m100", title: "Murmur 100", size: 100 };
  setTitle(idx.title);
  const months = await api(`index/${idx.name}/months`).catch(() => ({ months: [] }));
  const month = query.get("month") || (months.months || []).slice(-1)[0];
  if (!month) {
    clear(main).append(card(idx.title, el("div", { class: "empty", text: "No constituents yet. The index is built on the first run of each month." })));
    return;
  }
  const [data, changes] = await Promise.all([api(`index/${idx.name}${qs({ month })}`), api(`index/${idx.name}/changes${qs({ month })}`)]);
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

  clear(main).append(head, filters, el("div", { class: "stack" }, card("Top 20 by weight", el("div", {}, weightsNode, el("p", { class: "chart-note", text: `Together ${fmt.pct0(top.reduce((a, r) => a + (r.weight || 0), 0))} of the index.` }))), changesCard, card(`All ${items.length} constituents`, el("div", {}, constituents, downloads))));
  barChart(weightsNode, top.map((r) => ({ label: r.ticker, value: r.weight })), { percent: true, horizontal: true });
}
