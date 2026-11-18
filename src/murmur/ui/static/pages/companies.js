// Companies: the covered list for a month, and one company's sentiment, returns,
// lead/lag and headlines.

import { api, qs, el, card, clear, table, fmt, state, setTitle, field, select, link, navigate, daysAgo, badge } from "../lib.js";
import { leadlagChart, lineChart, chartNode } from "../charts.js";

export async function render(main, parts, query) {
  if (parts[0]) return renderTicker(main, parts[0].toUpperCase(), query);
  setTitle("Companies");
  const months = await api("universe/months").catch(() => ({ months: [] }));
  const month = query.get("month") || (months.months || []).slice(-1)[0];
  const data = month ? await api(`tickers${qs({ month })}`) : { items: [] };
  const search = el("input", { type: "text", placeholder: "Filter by ticker or name", "aria-label": "Filter companies" });
  const wrap = el("div");
  const draw = () => {
    const needle = search.value.trim().toLowerCase();
    const items = (data.items || []).filter((i) => !needle || i.ticker.toLowerCase().includes(needle) || (i.name || "").toLowerCase().includes(needle));
    clear(wrap).append(
      table(
        [
          { label: "#", num: true, key: "rank" },
          { label: "Ticker", value: (r) => link(`/companies/${r.ticker}`, r.ticker, { class: "mono" }) },
          { label: "Company", key: "name" },
          { label: "Market cap", num: true, value: (r) => fmt.compact(r.market_cap) },
          { label: "Headlines this month", num: true, value: (r) => fmt.int(r.n_articles) },
          { label: "Priced days", num: true, value: (r) => fmt.int(r.n_prices) },
        ],
        items,
        { compact: true, onRow: (r) => navigate(`/companies/${r.ticker}`), empty: "No companies match." },
      ),
    );
  };
  search.addEventListener("input", draw);
  draw();
  clear(main).append(
    el("div", { class: "page-head" }, el("p", { class: "kicker", text: "Companies" }), el("h1", { text: "One company at a time" }), el("p", { class: "lede", text: `The ${state.ui.coverage || ""} companies whose headlines are scored, with this month's coverage. Pick one for its sentiment, returns, its own lead or lag and its latest headlines, or compare two.` })),
    el(
      "div",
      { class: "filters" },
      field("Month", select((months.months || []).map((m) => [m, m]), month, (v) => navigate(`/companies${qs({ month: v })}`))),
      field("Search", search),
    ),
    card("", wrap),
  );
}

async function renderTicker(main, ticker, query) {
  setTitle(ticker);
  let info;
  try {
    info = await api(`tickers/${ticker}`);
  } catch (e) {
    clear(main).append(card("Not found", el("p", {}, `${ticker} has never been in the index. `, link("/companies", "All companies"))));
    return;
  }
  const from = query.get("from") || daysAgo(120);
  const vs = (query.get("vs") || "").toUpperCase();
  const [series, articles, leadlag, other, peers] = await Promise.all([
    api(`tickers/${ticker}/series${qs({ from })}`),
    api(`tickers/${ticker}/articles${qs({ limit: 40 })}`),
    api(`tickers/${ticker}/leadlag`).catch(() => ({ items: [] })),
    vs && vs !== ticker ? api(`tickers/${vs}/series${qs({ from })}`).catch(() => null) : null,
    api("tickers").catch(() => ({ items: [] })),
  ]);
  const otherRows = other ? (other.items || []) : [];
  const otherDays = otherRows.filter((r) => r.day_sentiment !== null && r.day_sentiment !== undefined);
  const latestMonth = (info.months || []).slice(-1)[0] || {};
  const rows = series.items || [];

  const head = el(
    "div",
    { class: "page-head" },
    el("h1", {}, el("span", { class: "mono" }, ticker), " ", el("span", { class: "muted", style: "font-weight:400" }, info.name)),
    el("div", {}, badge(`rank ${latestMonth.rank ?? "–"}`, "accent"), " ", el("span", { class: "muted small" }, `cap ${fmt.compact(latestMonth.market_cap)} · CIK ${info.cik ?? "–"} · ${fmt.int(info.n_articles)} headlines on record`)),
  );

  const sentNode = chartNode("short");
  const retNode = chartNode("short");
  const priceNode = chartNode("short");
  const llNode = chartNode();
  const dayRows = rows.filter((r) => r.day_sentiment !== null && r.day_sentiment !== undefined);
  const charts = el(
    "div",
    { class: "stack" },
    card("Headline sentiment, previous close to close", dayRows.length >= 2 ? el("div", {}, sentNode, el("p", { class: "chart-note", text: "Mean FinBERT score (−1 to +1) of the day window's headlines; hover for the count." })) : el("div", { class: "empty", text: "Fewer than two sessions with scored headlines in this range." })),
    card("Daily return", rows.length >= 2 ? retNode : el("div", { class: "empty", text: "No returns in this range." })),
    card("Close", rows.length >= 2 ? priceNode : el("div", { class: "empty", text: "No prices in this range." })),
    card(
      "This company's lead or lag",
      (leadlag.items || []).length ? el("div", {}, llNode, el("p", { class: "chart-note", text: `Per-company rolling results as of ${leadlag.as_of}; needs at least 10 paired sessions per horizon.` })) : el("div", { class: "empty", text: "Not enough paired sessions yet for a per-company estimate (10 needed)." }),
    ),
  );

  const arts = el(
    "div",
    {},
    (articles.items || []).map((a) =>
      el(
        "div",
        { class: "article" },
        el("div", {}, el("a", { href: a.url, target: "_blank", rel: "noopener" }, a.title), el("div", { class: "meta" }, `${fmt.time(a.published_at)} · ${a.window || "–"} window · ${a.provider || a.first_seen_source || ""}${a.sources && a.sources !== a.first_seen_source ? ` · via ${a.sources}` : ""}`)),
        el("div", { class: `score ${a.score > 0.2 ? "pos" : a.score < -0.2 ? "neg" : "muted"}` }, a.label ? `${a.label} ${fmt.r2(a.score)}` : "unscored"),
      ),
    ),
  );
  const artCard = card("Latest headlines", (articles.items || []).length ? arts : el("div", { class: "empty", text: "No headlines yet." }), { sub: `${fmt.int(articles.total)} on record` });

  const rangeSel = select([[daysAgo(30), "30 days"], [daysAgo(120), "120 days"], [daysAgo(365), "1 year"], ["2000-01-01", "All"]], from, (v) => navigate(`/companies/${ticker}${qs({ from: v, vs: vs || undefined })}`));
  const peerOptions = [["", "nobody"], ...(peers.items || []).filter((x) => x.ticker !== ticker).map((x) => [x.ticker, `${x.ticker} · ${x.name}`])];
  const vsSel = select(peerOptions, vs, (v) => navigate(`/companies/${ticker}${qs({ from: query.get("from"), vs: v })}`), { "aria-label": "Compare with" });
  const compareNote = vs && !otherRows.length ? el("span", { class: "muted small", text: `No data for ${vs} in this range.` }) : null;
  clear(main).append(head, el("div", { class: "filters" }, field("Range", rangeSel), field("Compare with", vsSel), compareNote, el("span", { class: "muted small" }, link("/companies", "← all companies"))), el("div", { class: "hero" }, charts, artCard));

  if (dayRows.length >= 2) {
    const series = [{ name: ticker, x: dayRows.map((r) => r.date), y: dayRows.map((r) => r.day_sentiment) }];
    if (otherDays.length) series.push({ name: vs, x: otherDays.map((r) => r.date), y: otherDays.map((r) => r.day_sentiment) });
    lineChart(sentNode, series, { yTitle: "mean score", yFormat: "+.2f", markers: true });
  }
  if (rows.length >= 2) {
    const series = [{ name: ticker, x: rows.map((r) => r.date), y: rows.map((r) => r.daily) }];
    if (otherRows.length) series.push({ name: vs, x: otherRows.map((r) => r.date), y: otherRows.map((r) => r.daily) });
    lineChart(retNode, series, { percent: true, markers: rows.length < 40 });
    lineChart(priceNode, [{ name: "Close", x: rows.map((r) => r.date), y: rows.map((r) => r.close) }], { yFormat: ",.2f", zero: false });
  }
  if ((leadlag.items || []).length) {
    const items = leadlag.items.filter((x) => x.method === "pearson");
    const horizons = [...new Set(items.map((x) => x.months))].sort((a, b) => b - a);
    const h = horizons.find((m) => items.filter((x) => x.months === m).length >= 9) || horizons[0];
    leadlagChart(llNode, items.filter((x) => x.months === h), { horizon: h });
  }
}
