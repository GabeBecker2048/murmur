// Methodology: how every number is made, what it cannot show, how the data is versioned
// and licensed, and who to contact. Static prose; the few live facts come from /ui.

import { api, el, clear, setTitle, link, state } from "../lib.js";

const P = (...parts) => el("p", {}, ...parts);
const H = (id, text) => el("h2", { id, text });
const UL = (...items) => el("ul", {}, items.map((i) => el("li", {}, i)));
const A = (href, text) => el("a", { href }, text);

export async function render(main) {
  setTitle("Methodology");
  const ui = state.ui || {};
  const manifest = await api("/data/index.json").catch(() => ({}));
  const contact = ui.contact || Object.values(manifest.datasets || {}).map((d) => d.license && d.license.contact).find(Boolean);
  const size = (ui.index && ui.index.size) || 100;

  const sections = [
    ["question", "The question"],
    ["companies", "Companies"],
    ["headlines", "Headlines and sentiment"],
    ["windows", "Windows and returns"],
    ["correlations", "Correlations and lags"],
    ["checks", "The checks"],
    ["index", "The Murmur 100"],
    ["limits", "Limitations"],
    ["versions", "Versions and stability"],
    ["licenses", "Licenses"],
    ["contact", "Contact"],
  ];

  const body = el(
    "article",
    { class: "prose" },
    H("question", "The question"),
    P("Does the tone of the news about a company predict its stock's next move, follow the move it just made, or move with it the same day? Murmur measures that every trading day for the largest US companies and publishes the data, the method and the answer."),

    H("companies", "Companies"),
    P(`The ${size} largest US-domiciled companies listed on NYSE or Nasdaq by market capitalisation, one ticker per company (the SEC's CIK is the identity, so Alphabet counts once). The list is rebuilt for each month and takes effect on its first trading day. Companies trading through ADRs and US-listed companies domiciled abroad are excluded. Candidates come from Yahoo's equity screener, identities from the SEC's public ticker list, headquarters from Yahoo.`),
    P("Months before October 2026 were reconstructed after the fact: each candidate's market cap on the month's first trading day is rebuilt from the SEC's reported shares outstanding times that day's close. Those months are flagged reconstructed in the data."),

    H("headlines", "Headlines and sentiment"),
    P("Headlines come from Finnhub's company-news feed (from November 2025) and Yahoo Finance's per-company feed (from 5 November 2026), collected every evening at 18:00 New York time. An article seen in several places is kept once: same address, or the same normalised title within two days, with the earliest publication time and every source that carried it."),
    P("Each headline is scored by FinBERT (ProsusAI/finbert), a language model trained on financial text, on the headline alone. Its score is P(positive) − P(negative), from −1 to +1. A company's sentiment for a window is the mean score of its headlines in that window."),

    H("windows", "Windows and returns"),
    P("Every headline belongs to one trading day and one window, by its publication time on the NYSE calendar (holidays and early closes included):"),
    UL("Overnight: from the previous session's close (16:00) to the open (09:30). Weekend and holiday news falls into the next session's overnight window.", "Intraday: from the open to the close.", "Day: both together, previous close to close."),
    P("Three returns come from daily bars: overnight = open / previous close − 1, intraday = close / open − 1, daily = close / previous close − 1, adjusted for splits."),

    H("correlations", "Correlations and lags"),
    P("Sentiment is paired with returns by company, never by position. Every window is correlated with every return at three lags, where the lag is the return's session minus the headlines' session:"),
    UL("Lag −1: today's headlines against yesterday's return. A link here means news follows prices.", "Lag 0: the same session. A link here means they move together.", "Lag +1: today's headlines against tomorrow's return. A link here would mean news predicts prices."),
    P("Both Pearson's r (with a 95 % confidence interval) and Spearman's ρ are computed: across companies each day, and pooled over every company-day in rolling 1, 3, 6 and 12-month windows, for all sources together and for each source."),

    H("checks", "The checks"),
    P("A pooled correlation can mislead: on a day the whole market rises, the news is mostly good and every stock is up, which looks like a link but says nothing about any one company. Observations on the same day are not independent, so pooled p-values overstate certainty. Every findings snapshot therefore carries four checks:"),
    UL(
      "The shuffle test: each session's headlines are shuffled between companies 1,000 times. Whatever survives the shuffle came from the day, not the company. The real correlation has to clear that range.",
      "Market adjustment: each return minus that session's cap-weighted index return.",
      "Day by day: the correlation across companies is computed for each session alone and then averaged, with its standard error across sessions (a Fama-MacBeth style estimate). It is immune to market-wide days by construction.",
      "Splits by company size and by source.",
    ),
    P("Findings snapshots also restrict each session to the companies in the index that session (the rolling tables use the latest month's list)."),

    H("index", "The Murmur 100"),
    P(`The companies double as a cap-weighted index: each company's weight is its share of the month's total market cap, the level starts at 1,000 and is chained through each session's weighted return, and the weights change at the close of each month's first session. It is published with every company's daily contribution. `, link("/index", "See the index"), "."),

    H("limits", "Limitations"),
    UL(
      "Correlation is not causation. A same-day link fits news reporting moves made during the day, prices reacting to news, or both; daily data cannot separate them.",
      "Survivorship: reconstructed months use today's list of large companies, so firms that shrank or were delisted are missing.",
      "Headlines, not articles: a neutral headline can sit on top of a market-moving story.",
      "Daily bars: windows are cut at the 09:30 and 16:00 bells, so a headline at 15:59 and the move it describes may land on different sides.",
      "The model: FinBERT predates this data and makes systematic mistakes (negation, sarcasm, numbers).",
      "Source mix: the first year is almost entirely Finnhub; Yahoo joins in November 2026.",
      "Nothing here is investment advice, and the effect is too small to trade on.",
    ),

    H("versions", "Versions and stability"),
    P(`The published dataset is v${manifest.data_version || "1.0.0"}, stamped in every file. Its shape never changes: new days are added, but no column, table or key is added, removed or renamed. If that ever has to happen, the whole dataset is republished as a new version that replaces it, announced ahead of time. Corrections to published values are listed in the data changelog. The stable addresses (the manifest, the latest-file URLs, the bundle API) keep working, and frozen findings never change: a new conclusion is a new snapshot. Details: `, A("https://github.com/GabeBecker2048/murmur/blob/main/docs/stability.md", "stability"), " and ", A("https://github.com/GabeBecker2048/murmur/blob/main/docs/data-changelog.md", "data changelog"), "."),

    H("licenses", "Licenses"),
    UL(
      "Code: MIT.",
      "Murmur's own work: CC BY 4.0. That is the companies list, the Murmur 100, the correlations and the findings. Attribute “Murmur”.",
      "Headlines and sentiment (Finnhub and Yahoo Finance) and prices (Yahoo Finance via yfinance): source terms apply. Takedown available by request.",
    ),
    P("Every file carries its license, notice and contact in its metadata. To cite Murmur, see ", link("/", "the Findings page"), " (Use it) or the repository's CITATION.cff."),

    H("contact", "Contact"),
    P("Questions, corrections or anything else: ", contact ? el("a", { href: `mailto:${contact}` }, contact) : "see the contact field in any file's metadata", "."),
  );

  clear(main).append(
    el("div", { class: "page-head" }, el("p", { class: "kicker", text: "Methodology" }), el("h1", { text: "How every number is made" }), el("p", { class: "lede", text: "What Murmur collects, how it scores and pairs it, the checks behind the conclusion, and what it cannot tell you." })),
    el("nav", { class: "toc", "aria-label": "On this page" }, sections.map(([id, label]) => el("a", { href: `#${id}` }, label))),
    el("section", { class: "card" }, body),
  );
}
