// Status: is the pipeline running, and is the data complete? The last runs, the
// services' heartbeats and the last 30 days of sessions.

import { api, qs, el, card, clear, table, fmt, setTitle, badge, link, daysAgo } from "../lib.js";

export async function render(main) {
  setTitle("Status");
  const [health, runs, days] = await Promise.all([
    api("health").catch(() => null),
    api(`runs${qs({ limit: 14 })}`).catch(() => ({ items: [] })),
    api(`days${qs({ from: daysAgo(45) })}`).catch(() => ({ items: [] })),
  ]);
  const services = (health && health.services) || {};
  const svc = (name, label) => {
    const s = services[name];
    if (!s) return null;
    const ok = s.ok !== false;
    return el("div", { class: "tile" }, el("div", { class: "label", text: label }), el("div", { class: "value" }, badge(ok ? "running" : "not responding", ok ? "good" : "critical")), el("div", { class: "faint small", text: s.heartbeat_age === null || s.heartbeat_age === undefined ? "" : `heartbeat ${s.heartbeat_age} s ago` }));
  };
  const lastRun = (runs.items || [])[0];
  const tiles = el(
    "div",
    { class: "grid tiles" },
    el("div", { class: "tile" }, el("div", { class: "label", text: "Dashboard and API" }), el("div", { class: "value" }, badge(health ? "running" : "unreachable", health ? "good" : "critical")), el("div", { class: "faint small", text: health ? `Murmur ${health.version}` : "" })),
    svc("scheduler", "Scheduler"),
    svc("worker", "Worker"),
    el("div", { class: "tile" }, el("div", { class: "label", text: "Last daily run" }), el("div", { class: "value" }, lastRun ? badge(lastRun.ok ? "succeeded" : "failed", lastRun.ok ? "good" : "critical") : "–"), el("div", { class: "faint small", text: lastRun ? `${lastRun.date} · ${fmt.ago(lastRun.finished_at || lastRun.started_at)}` : "" })),
  );

  const runTable = table(
    [
      { label: "Session", value: (r) => r.date },
      { label: "Started", value: (r) => fmt.time(r.started_at) },
      { label: "Took", num: true, value: (r) => (r.finished_at ? fmt.seconds((Date.parse(r.finished_at) - Date.parse(r.started_at)) / 1000) : "running") },
      { label: "Attempt", num: true, value: (r) => r.attempt },
      { label: "Result", value: (r) => (r.ok ? badge("ok", "good") : r.ok === 0 ? badge(`failed: ${(r.stages || []).filter((s) => !s.ok).map((s) => s.name).join(", ")}`, "critical") : badge("running", "accent")) },
    ],
    runs.items || [],
    { compact: true, empty: "No runs recorded yet." },
  );

  const recent = (days.items || []).filter((d) => d.trading_day).slice(-30).reverse();
  const dayTable = table(
    [
      { label: "Session", value: (d) => link(`/news-vs-prices${qs({ day: d.date })}`, d.date) },
      { label: "Headlines", num: true, value: (d) => fmt.int(d.n_articles) },
      { label: "Prices", num: true, value: (d) => (d.has_prices ? "✓" : "–") },
      { label: "Returns", num: true, value: (d) => (d.has_returns ? "✓" : "–") },
      { label: "Analysis", num: true, value: (d) => (d.has_analysis ? "✓" : "–") },
    ],
    recent,
    { compact: true, empty: "No sessions yet." },
  );

  clear(main).append(
    el("div", { class: "page-head" }, el("p", { class: "kicker", text: "Status" }), el("h1", { text: "Is it running?" }), el("p", { class: "lede", text: "The pipeline runs every day at 18:00 New York time: headlines, prices, FinBERT, the analysis, then the published files. A session's lag +1 pairs arrive with the next session." })),
    tiles,
    el("div", { class: "stack", style: "margin-top:14px" }, card("Daily runs", runTable, { sub: "The last two weeks" }), card("Sessions", dayTable, { sub: "Trading days in the last 30 sessions" })),
  );
}
