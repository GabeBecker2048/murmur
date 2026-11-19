// Overview (/): a polite introduction for someone who has never heard of Murmur. What it
// is, the free datasets (for people who only want headlines or an index), why it exists,
// and where to go next.

import { api, el, clear, fmt, setTitle, link, badge, state, navigate } from "../lib.js";

/** When each dataset is delivered. The pipeline runs every evening at 18:00 New York
 * time and publishes within about 15 minutes. Shared with the Data page. */
export const DELIVERY = {
  news: "Every trading day by about 18:15 New York time. The previous session's file also picks up late headlines the next evening.",
  sentiment: "Every trading day by about 18:15 New York time.",
  prices: "Every trading day after the close, by about 18:15 New York time.",
  correlations: "Every trading day by about 18:15 New York time; a session gains its next-day (lag +1) pairs the following trading day. Per-company results: one file per month, updated daily, final after the month's last session.",
  m100: "Constituents and changes: on the evening of each month's first trading day. Daily level and contributions: every trading day by about 18:15 New York time.",
  companies: "On the evening of each month's first trading day.",
  archives: "Each month's archive on the evening of the 1st of the following month.",
  findings: "A snapshot when a new conclusion is published; never changed afterwards.",
};

/** What each dataset is for, in the words of someone choosing one. The registry's own
 * descriptions (shown on the Data page) are more technical. */
function guide(indexName, indexTitle) {
  return [
    {
      name: "news",
      title: "News headlines",
      pitch: "Every headline collected about each company, scored for tone.",
      rows: ["title, link, publisher and publication time", "the companies it is about", "FinBERT's positive, negative and neutral probabilities and a score from −1 to +1", "which sources carried it, and its trading day and window"],
      for: "A ready-made, labelled corpus of financial headlines.",
    },
    {
      name: "sentiment",
      title: "Daily sentiment",
      pitch: "One number per company and trading day: how positive its news was.",
      rows: ["mean score and headline count per company", "for the overnight, intraday and whole-day windows", "pooled over sources and per source"],
      for: "A daily sentiment signal for the largest US companies.",
    },
    {
      name: indexName,
      title: indexTitle,
      pitch: "A free, cap-weighted index of the 100 largest US companies, rebuilt every month.",
      rows: ["monthly constituents with rank, market cap and weight", "who joined and left each month", "the daily index level (base 1,000)", "each company's daily contribution to the move"],
      for: "A transparent large-cap index you can reproduce and use.",
    },
    {
      name: "prices",
      title: "Prices and returns",
      pitch: "The daily bar for every company in the index, and three returns.",
      rows: ["open, high, low, close, volume", "overnight, intraday and daily returns", "split ratios"],
      for: "Returns already lined up with the headlines. Published under the source's terms.",
    },
    {
      name: "correlations",
      title: "Correlations",
      pitch: "How sentiment and returns move together, recomputed every day.",
      rows: ["each day's cross-company correlations at lags −1, 0, +1", "rolling 1, 3, 6 and 12-month results, Pearson and Spearman", "the shuffle test", "per-company results each month"],
      for: "The analysis itself, if you want to check or extend it.",
    },
    {
      name: "companies",
      title: "Companies",
      pitch: "Every company that has ever been in the index.",
      rows: ["SEC CIK (a permanent identity) and ticker", "name and exchange", "first and last month in the index"],
      for: "A clean ticker-to-company map for US large caps.",
    },
  ];
}

const dateText = (iso) =>
  iso ? new Date(`${iso.length === 7 ? `${iso}-01` : iso}T12:00:00Z`).toLocaleDateString(undefined, { day: iso.length === 7 ? undefined : "numeric", month: "long", year: "numeric", timeZone: "UTC" }) : "–";

export async function render(main, parts, query) {
  // links from before 0.9.1 pointed at /?findings=<id>
  if (query.get("findings")) return navigate(`/findings?findings=${encodeURIComponent(query.get("findings"))}`, { replace: true });
  setTitle("");
  const ui = state.ui || {};
  const idx = ui.index || { name: "m100", title: "Murmur 100", size: 100 };
  const [listing, status, latest, answer] = await Promise.all([
    api("datasets").catch(() => ({ items: [] })),
    api("status").catch(() => null),
    api("latest").catch(() => null),
    api("findings/latest").catch(() => null),
  ]);
  const byName = Object.fromEntries((listing.items || []).map((d) => [d.name, d]));
  const daily = (listing.items || []).filter((d) => d.cadence === "daily" && d.first_date);
  const since = daily.map((d) => d.first_date).sort()[0];
  const counts = (status && status.counts) || {};

  // ---- hero
  const facts = el(
    "div",
    { class: "facts" },
    [
      [fmt.int(counts.articles), "headlines scored"],
      [String(idx.size || 100), "companies, every day"],
      [since ? dateText(since) : "–", "collecting since"],
      [latest && latest.last_run ? fmt.ago(latest.last_run.finished_at || latest.last_run.started_at) : "–", "last updated"],
    ].map(([v, l]) => el("div", { class: "fact" }, el("div", { class: "v", text: v }), el("div", { class: "l", text: l }))),
  );
  const hero = el(
    "section",
    { class: "hero-overview" },
    el("p", { class: "kicker", text: "Welcome to Murmur" }),
    el("h1", { text: "What the news says about America's largest companies, and what their stocks did next." }),
    el(
      "p",
      { class: "lede" },
      `Every evening Murmur collects the day's news headlines about the ${idx.size || 100} largest US companies, has a finance-trained language model read each one for tone, and lines that up with how each company's stock moved. Everything it collects and computes is published here as free, open data.`,
    ),
    el("div", { class: "actions" }, link("/downloads", "Get the data", { class: "btn primary" }), link("/findings", "See what we found", { class: "btn" }), link("/story", "Read the story", { class: "btn" })),
    facts,
  );

  // ---- the datasets
  const cards = guide(idx.name, idx.title).map((g) => {
    const d = byName[g.name];
    const lic = d && d.license && d.license.name;
    const firstTable = d && d.tables ? Object.keys(d.tables)[0] : null;
    return el(
      "article",
      { class: "dataset-card" },
      el("div", { class: "card-head" }, el("h3", { text: g.title }), lic ? badge(/^source terms/i.test(lic) ? "source terms" : lic, /^source terms/i.test(lic) ? "warning" : "good") : null),
      el("p", { class: "pitch", text: g.pitch }),
      el("ul", {}, g.rows.map((r) => el("li", { text: r }))),
      el("p", { class: "for", text: g.for }),
      el("p", { class: "delivery small" }, el("strong", { text: "Delivered: " }), DELIVERY[g.name === idx.name ? "m100" : g.name] || ""),
      el(
        "p",
        { class: "faint small" },
        d && d.first_date ? `${d.cadence === "daily" ? "Daily" : "Monthly"} · ${dateText(d.first_date)} to ${dateText(d.last_date)} · ` : "",
        firstTable ? el("a", { href: `/data/${g.name}/latest.${firstTable}.parquet` }, "latest file") : null,
        firstTable ? " · " : null,
        link(`/downloads?datasets=${g.name}`, "any range"),
      ),
    );
  });
  const datasets = el(
    "section",
    { class: "section" },
    el("p", { class: "kicker", text: "Free datasets" }),
    el("h2", { class: "section-title", text: "Take what you need" }),
    el(
      "p",
      { class: "lede" },
      "You do not have to care about our question to use the data. Six datasets, updated every trading day, free to use under CC BY 4.0 (prices: the source's terms). Download any date range in one file, a whole month at a time, or always the newest day from a fixed address. Dataset v1.0.0. ",
      el("a", { href: "https://github.com/GabeBecker2048/murmur/blob/main/docs/quickstart.md" }, "Quick start for pandas and DuckDB"),
      ".",
    ),
    el("div", { class: "dataset-grid" }, cards),
    el("p", { class: "muted small" }, "Plus the findings as JSON (", el("a", { href: "/data/findings/latest.json" }, "/data/findings/latest.json"), "), a weekly ", el("a", { href: "/feed.xml" }, "Atom feed"), " and an ", el("a", { href: "/docs" }, "HTTP API"), "."),
  );

  // ---- why it exists
  const text = answer && answer.text;
  const why = el(
    "section",
    { class: "section why" },
    el("p", { class: "kicker", text: "Why it exists" }),
    el("h2", { class: "section-title", text: "Does news predict stock prices, follow them, or move with them?" }),
    el("p", { class: "lede", text: "Three friends wanted to know. Murmur measures it every trading day, a session before, during and after the news, and checks the answer against the obvious ways it could be wrong: the whole market moving at once, too much trust in one day's numbers, chance." }),
    text && text.headline ? el("blockquote", { class: "answer" }, el("p", { text: text.headline }), el("footer", { text: `Murmur findings “${text.title || answer.id}”, ${dateText(answer.window.start)} to ${dateText(answer.window.end)}` })) : null,
    el("div", { class: "actions" }, link("/findings", "The findings →", { class: "btn primary" }), link("/story", "How it started →", { class: "btn" }), link("/methodology", "How it works →", { class: "btn" })),
  );

  // ---- explore
  const tiles = [
    ["/news-vs-prices", "News vs. prices", "The live numbers: every news window against every return, at every lag, with history."],
    ["/companies", "Companies", "One company's headlines, sentiment and returns, or two side by side."],
    ["/index", idx.title, "The index level against the S&P 500, its members and their weights."],
    ["/headlines", "Headlines", "Search every scored headline by words, company, tone and date."],
  ];
  const explore = el(
    "section",
    { class: "section" },
    el("p", { class: "kicker", text: "Explore" }),
    el("h2", { class: "section-title", text: "Look around" }),
    el("div", { class: "grid cols-4 explore" }, tiles.map(([href, title, body]) => el("a", { href, "data-link": true, class: "explore-tile" }, el("strong", { text: title }), el("span", { text: body })))),
  );

  clear(main).append(hero, datasets, why, explore);
}
