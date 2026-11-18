// The embeddable Findings chart (/embed/findings?id=&theme=): the chart, the window and
// the attribution CC BY asks for, nothing else. Uses lib.js and charts.js without the
// dashboard shell.

import { api, el, clear, qs } from "./lib.js";
import { findingsChart } from "./charts.js";

const params = new URLSearchParams(location.search);
const theme = params.get("theme");
if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;

async function main() {
  const id = params.get("id") || "latest";
  const node = document.getElementById("embed-chart");
  let doc;
  try {
    doc = await api(`findings/${encodeURIComponent(id)}`);
  } catch (e) {
    node.textContent = e.status === 404 ? "These findings are not published yet." : "Could not load the findings.";
    return;
  }
  const head = doc.headline.lags;
  const rows = [-1, 0, 1].map((l) => ({ lag: l, ...head[String(l)] })).filter((r) => r.r !== undefined);
  const shuffled = Object.fromEntries((doc.placebo || []).map((x) => [x.lag, [x.null_low, x.null_high]]));
  document.getElementById("embed-title").textContent = (doc.text && doc.text.headline) || "Headline sentiment vs the stock's daily return";
  document.getElementById("embed-sub").textContent =
    `Correlation of headline sentiment with the daily return, ${doc.coverage.companies} large US companies, ${doc.window.start} to ${doc.window.end}. Grey: the range shuffled headlines produce.`;
  const site = location.origin;
  clear(document.getElementById("embed-credit")).append(
    el("a", { href: `${site}/findings${doc.kind === "frozen" ? qs({ findings: doc.id }) : ""}`, target: "_blank", rel: "noopener" }, `Murmur findings ${doc.kind === "live" ? "(live)" : doc.id}`),
    " · ",
    el("a", { href: "https://creativecommons.org/licenses/by/4.0/", target: "_blank", rel: "noopener" }, "CC BY 4.0"),
  );
  findingsChart(node, rows, { shuffled, compact: node.clientWidth < 560 });
}

main();
