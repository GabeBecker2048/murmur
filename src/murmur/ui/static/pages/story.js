// Story (/story): how Murmur came about, who built what, and how a weekend prototype
// became a daily pipeline. Static content.

import { el, clear, setTitle, link } from "../lib.js";

const PEOPLE = [
  {
    name: "Gabe",
    role: "Data engineer at a FinTech company",
    built: "Wrote the framework that connected everything in the prototype, then rebuilt Murmur from the ground up.",
  },
  {
    name: "Wyatt",
    role: "Support engineer at the same company",
    built: "Wrote the price fetching for the prototype, in Python.",
  },
  {
    name: "Sam",
    role: "Business analytics student",
    built: "Brought the idea from a class on sentiment analysis, and wrote the prototype's sentiment analysis in R.",
  },
];

const TIMELINE = [
  {
    when: "Since middle school",
    title: "Friends first",
    body: ["Gabe, Wyatt and Sam have been friends since middle school."],
  },
  {
    when: "2023",
    title: "A fun FinTech project",
    body: [
      "Gabe and Wyatt were working at the same FinTech company; Sam was studying business analytics. The three agreed they should build a FinTech project together, for fun.",
      "Sam had taken a class on sentiment analysis and wanted to compare how people feel about a company with what its stock price does. Measuring how people feel about a company is the hard part, so they settled on a proxy: the tone of the news headlines written about it.",
    ],
  },
  {
    when: "November 2023 to April 2024",
    title: "The prototype",
    body: [
      "They built it together. Sam wrote the sentiment analysis in R, Wyatt wrote the code that fetched stock prices in Python, and Gabe wrote the framework that connected the pieces into one program.",
      "It ran from the command line, with a small Tkinter window on top, and it worked: it pulled headlines from Google News, scored them, fetched prices and compared the two. It could only collect going forward, though; a day it did not run was a day lost for good.",
    ],
  },
  {
    when: "After that",
    title: "Life moves on",
    body: ["It made a cool prototype. Then work and school took over, and all three moved on to other things."],
  },
  {
    when: "November 2026",
    title: "Murmur",
    now: true,
    body: [
      "Gabe, a data engineer by trade, picked the project back up and rebuilt it from scratch as a production pipeline: a daily job on a home server, a year of history backfilled, a finance-trained language model reading every headline, open datasets anyone can download, and this dashboard. Version 0.1.0 to 0.9.0 took two weeks.",
      "The question is the same one the three friends started with.",
    ],
  },
];

const THEN_NOW = [
  ["News", "Google News search results", "Finnhub and Yahoo company news, deduplicated across sources"],
  ["Reading the tone", "Word lists, in R", "FinBERT, a language model trained on financial text"],
  ["History", "From the day it was run", "A year backfilled, growing every day"],
  ["Running it", "By hand", "Every evening on its own, with health checks and backups"],
  ["The analysis", "Sentiment against price", "Every news window against every return, a session before, during and after, with robustness checks"],
  ["Results", "CSV files and charts on one computer", "Open datasets, an API, a feed and this dashboard"],
];

export async function render(main) {
  setTitle("Story");
  const people = el(
    "div",
    { class: "people" },
    PEOPLE.map((p) => el("article", { class: "person" }, el("span", { class: "avatar", "aria-hidden": "true", text: p.name[0] }), el("h3", { text: p.name }), el("p", { class: "role", text: p.role }), el("p", { class: "built", text: p.built }))),
  );
  const timeline = el(
    "ol",
    { class: "timeline" },
    TIMELINE.map((t) => el("li", { class: t.now ? "now" : "" }, el("div", { class: "when", text: t.when }), el("h3", { text: t.title }), t.body.map((b) => el("p", { text: b })))),
  );
  const thenNow = el(
    "div",
    { class: "table-wrap" },
    el(
      "table",
      {},
      el("thead", {}, el("tr", {}, el("th", { text: "" }), el("th", { text: "The prototype (2023)" }), el("th", { text: "Murmur (2026)" }))),
      el("tbody", {}, THEN_NOW.map(([what, then, now]) => el("tr", {}, el("th", { class: "row", scope: "row", text: what }), el("td", { class: "muted", text: then }), el("td", { text: now })))),
    ),
  );

  clear(main).append(
    el(
      "section",
      { class: "hero-overview" },
      el("p", { class: "kicker", text: "The story" }),
      el("h1", { text: "Three friends and a question" }),
      el("p", { class: "lede", text: "Murmur started as a side project between friends who wanted to know whether the news about a company says anything about its stock. This is how it came about, who built what, and how a prototype became a daily pipeline." }),
    ),
    el("section", { class: "section" }, el("h2", { class: "section-title", text: "Who" }), people),
    el("section", { class: "section" }, el("h2", { class: "section-title", text: "How it happened" }), timeline),
    el("section", { class: "section" }, el("h2", { class: "section-title", text: "Then and now" }), el("section", { class: "card" }, thenNow)),
    el(
      "section",
      { class: "section" },
      el("div", { class: "actions" }, link("/findings", "What we found →", { class: "btn primary" }), link("/methodology", "How it works →", { class: "btn" }), el("a", { class: "btn", href: "https://github.com/GabeBecker2048/murmur" }, "The code →")),
    ),
  );
}
