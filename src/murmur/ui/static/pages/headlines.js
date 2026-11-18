// Headlines: search every scored headline by words, company, tone, source and date.
// Every filter lives in the query string, so a search can be linked.

import { api, qs, el, clear, fmt, setTitle, field, select, navigate, link, card } from "../lib.js";

const PAGE = 50;

export async function render(main, parts, query) {
  setTitle("Headlines");
  const f = {
    q: query.get("q") || "",
    ticker: query.get("ticker") || "",
    sentiment: query.get("sentiment") || "",
    source: query.get("source") || "",
    from: query.get("from") || "",
    to: query.get("to") || "",
    page: Math.max(1, Number(query.get("page") || 1)),
  };
  // a new filter starts again from the first page (page 1 is the default, so it is dropped)
  const go = (changes = {}) => navigate(`/headlines${qs({ ...f, ...changes, page: undefined })}`);

  const [companies, sources] = await Promise.all([api("tickers").catch(() => ({ items: [] })), api("sources").catch(() => ({ items: [] }))]);

  const search = el("input", { type: "search", value: f.q, placeholder: "Words in the headline, e.g. earnings beat", "aria-label": "Search headlines", style: "min-width:min(100%, 340px)" });
  const form = el("form", { class: "filters", role: "search" });
  form.addEventListener("submit", (ev) => {
    ev.preventDefault();
    go({ q: search.value.trim() });
  });
  const company = select([["", "Any company"], ...(companies.items || []).map((c) => [c.ticker, `${c.ticker} · ${c.name}`])], f.ticker, (v) => go({ ticker: v }), { "aria-label": "Company" });
  const tone = select([["", "Any tone"], ["positive", "Positive"], ["negative", "Negative"], ["neutral", "Neutral"]], f.sentiment, (v) => go({ sentiment: v }), { "aria-label": "Tone" });
  const source = select([["", "Any source"], ...(sources.items || []).map((s) => [s.source, s.source])], f.source, (v) => go({ source: v }), { "aria-label": "Source" });
  const fromIn = el("input", { type: "date", value: f.from, "aria-label": "From" });
  const toIn = el("input", { type: "date", value: f.to, "aria-label": "To" });
  fromIn.addEventListener("change", () => go({ from: fromIn.value }));
  toIn.addEventListener("change", () => go({ to: toIn.value }));
  form.append(
    field("Search", search),
    el("button", { type: "submit", class: "btn primary" }, "Search"),
    field("Company", company),
    field("Tone", tone),
    field("Source", source),
    field("From", fromIn),
    field("To", toIn),
  );

  const results = el("div", { class: "results" });
  clear(main).append(
    el("div", { class: "page-head" }, el("p", { class: "kicker", text: "Headlines" }), el("h1", { text: "Every headline, scored" }), el("p", { class: "lede", text: "Each headline Murmur has collected, with FinBERT's reading of its tone: the score is P(positive) − P(negative), from −1 to +1. Every word you type must appear in the title." })),
    form,
    card("", results),
  );

  if (f.q && f.q.length < 2) {
    results.append(el("div", { class: "empty", text: "Type at least two characters." }));
    return;
  }
  let data;
  try {
    data = await api(`articles${qs({ q: f.q, ticker: f.ticker, sentiment: f.sentiment, source: f.source, from: f.from, to: f.to, limit: PAGE, offset: (f.page - 1) * PAGE })}`);
  } catch (e) {
    results.append(el("div", { class: "empty", text: e.message }));
    return;
  }
  const items = data.items || [];
  const pages = Math.max(1, Math.ceil(data.total / PAGE));
  results.append(
    el("p", { class: "muted small", text: `${fmt.int(data.total)} headline${data.total === 1 ? "" : "s"}${f.q ? ` matching “${f.q}”` : ""}, newest first.` }),
    items.length
      ? el("div", { class: "list" }, items.map((a) =>
          el(
            "div",
            { class: "article" },
            el("span", { class: `score-chip ${a.label || "neutral"}`, title: a.label ? `${a.label}: positive ${fmt.pct0(a.positive)}, negative ${fmt.pct0(a.negative)}` : "not scored yet", text: a.score === null || a.score === undefined ? "–" : fmt.r2(a.score) }),
            el(
              "div",
              {},
              el("a", { href: a.url, target: "_blank", rel: "noopener" }, a.title),
              el(
                "div",
                { class: "meta" },
                (a.tickers || "").split(",").filter(Boolean).map((t, i) => [i ? ", " : "", link(`/companies/${t}`, t, { class: "mono" })]),
                ` · ${fmt.time(a.published_at)} · ${a.window || "–"} window of ${a.trading_day || "–"}`,
                a.provider ? ` · ${a.provider}` : "",
                a.sources ? ` · via ${a.sources}` : "",
              ),
            ),
          ),
        ))
      : el("div", { class: "empty", text: "No headlines match." }),
  );
  if (pages > 1) {
    const prev = el("button", { type: "button", class: "btn small", disabled: f.page <= 1 || undefined }, "← Newer");
    const next = el("button", { type: "button", class: "btn small", disabled: f.page >= pages || undefined }, "Older →");
    prev.addEventListener("click", () => navigate(`/headlines${qs({ ...f, page: f.page - 1 })}`));
    next.addEventListener("click", () => navigate(`/headlines${qs({ ...f, page: f.page + 1 })}`));
    results.append(el("div", { class: "pager" }, prev, el("span", { class: "muted small", text: `Page ${f.page} of ${fmt.int(pages)}` }), next));
  }
}
