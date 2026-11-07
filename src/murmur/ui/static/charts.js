// Plotly wrappers that read the page's design tokens, so every chart follows the
// theme: transparent surfaces, hairline grid, thin 2px lines, 8px+ markers, a legend
// whenever there are two or more series, one y-axis per chart (never two scales).

import { el, currentTheme, MATCHED, pairLabel } from "./app.js";

function token(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function palette() {
  return {
    series: [token("--series-1"), token("--series-2"), token("--series-3"), token("--series-4"), token("--series-5")],
    text1: token("--text-1"),
    text2: token("--text-2"),
    text3: token("--text-3"),
    grid: token("--grid"),
    axis: token("--axis"),
    surface: token("--surface-1"),
    accent: token("--accent"),
    good: token("--good"),
    critical: token("--critical"),
  };
}

function baseLayout(p, overrides = {}) {
  return {
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { family: getComputedStyle(document.body).fontFamily, color: p.text2, size: 12 },
    margin: { l: 48, r: 16, t: 8, b: 40 },
    hovermode: "x unified",
    hoverlabel: { bgcolor: p.surface, bordercolor: p.grid, font: { color: p.text1, size: 12 } },
    legend: { orientation: "h", y: -0.18, x: 0, font: { color: p.text2 }, bgcolor: "rgba(0,0,0,0)" },
    xaxis: { gridcolor: p.grid, zerolinecolor: p.axis, linecolor: p.axis, tickcolor: p.axis, automargin: true },
    yaxis: { gridcolor: p.grid, zerolinecolor: p.axis, linecolor: p.axis, tickcolor: p.axis, automargin: true },
    ...overrides,
  };
}

const CONFIG = {
  responsive: true,
  displaylogo: false,
  displayModeBar: "hover",
  modeBarButtonsToRemove: ["select2d", "lasso2d", "autoScale2d", "zoomIn2d", "zoomOut2d"],
  toImageButtonOptions: { format: "png", scale: 2, filename: "murmur" },
};

const live = new Set();

function plot(node, traces, layout) {
  if (!window.Plotly) {
    node.textContent = "Charts need JavaScript and the Plotly library.";
    return node;
  }
  node.classList.remove("loading");
  window.Plotly.react(node, traces, layout, CONFIG);
  live.add(node);
  return node;
}

// Re-theme every live chart when the theme flips.
window.addEventListener("murmur:theme", () => {
  for (const node of [...live]) {
    if (!node.isConnected) {
      live.delete(node);
      continue;
    }
    if (node._murmurRedraw) node._murmurRedraw();
  }
});

function register(node, redraw) {
  node._murmurRedraw = redraw;
  redraw();
  return node;
}

// ---- the lead/lag chart ----------------------------------------------------------

/** rows: rolling rows (pooled, one method, one source, one horizon). Draws the three
 * matched pairs across lags -1, 0, +1 with 95 % CI error bars. */
export function leadlagChart(node, rows, { horizon, method = "pearson" } = {}) {
  return register(node, () => {
    const p = palette();
    const traces = [];
    MATCHED.forEach(([w, r], i) => {
      const mine = rows.filter((x) => x.window === w && x.return_type === r);
      if (!mine.length) return;
      const byLag = Object.fromEntries(mine.map((x) => [x.lag, x]));
      const lags = [-1, 0, 1].filter((l) => byLag[l]);
      const n = Math.max(...lags.map((l) => byLag[l].n || 0));
      traces.push({
        type: "scatter",
        mode: "lines+markers",
        name: `${pairLabel(w, r)} (n=${n})`,
        x: lags.map((l) => l + (i - 1) * 0.06),
        y: lags.map((l) => byLag[l].estimate),
        customdata: lags.map((l) => [byLag[l].n, byLag[l].p_value, byLag[l].ci_low, byLag[l].ci_high, l]),
        error_y: {
          type: "data",
          symmetric: false,
          array: lags.map((l) => (byLag[l].ci_high ?? byLag[l].estimate) - byLag[l].estimate),
          arrayminus: lags.map((l) => byLag[l].estimate - (byLag[l].ci_low ?? byLag[l].estimate)),
          color: p.series[i],
          thickness: 1.5,
          width: 4,
        },
        line: { color: p.series[i], width: 2 },
        marker: { color: p.series[i], size: 9, line: { color: p.surface, width: 2 } },
        hovertemplate:
          "%{y:+.3f} <span style='opacity:.7'>r</span><br>n=%{customdata[0]} · p=%{customdata[1]:.3f}<br>CI %{customdata[2]:+.2f} to %{customdata[3]:+.2f}<extra>%{fullData.name}</extra>",
      });
    });
    const narrow = node.clientWidth > 0 && node.clientWidth < 560;
    const layout = baseLayout(p, {
      hovermode: "closest",
      showlegend: traces.length > 1,
      margin: { l: 48, r: 16, t: 8, b: narrow ? 56 : 70 },
      legend: { ...baseLayout(p).legend, y: narrow ? -0.22 : -0.18 },
      xaxis: {
        ...baseLayout(p).xaxis,
        tickvals: [-1, 0, 1],
        ticktext: narrow
          ? ["−1<br><span style='font-size:11px'>follows</span>", "0<br><span style='font-size:11px'>same</span>", "+1<br><span style='font-size:11px'>leads</span>"]
          : ["−1<br><span style='font-size:11px'>news after return</span>", "0<br><span style='font-size:11px'>same session</span>", "+1<br><span style='font-size:11px'>news before return</span>"],
        range: [-1.5, 1.5],
        zeroline: false,
        showgrid: false,
      },
      yaxis: { ...baseLayout(p).yaxis, title: { text: `${method === "spearman" ? "Spearman ρ" : "Pearson r"}${horizon ? `, ${horizon}-month window` : ""}`, font: { size: 12 } }, zeroline: true },
    });
    plot(node, traces, layout);
  });
}

// ---- generic line / area series ------------------------------------------------

/** series: [{name, x, y, ci?: [[low, high], ...], color?: index}] */
export function lineChart(node, series, { yTitle, yFormat, percent = false, zero = true, hover = "x unified", markers = false } = {}) {
  return register(node, () => {
    const p = palette();
    const traces = [];
    series.forEach((s, i) => {
      const color = p.series[s.color ?? i] || p.series[0];
      if (s.ci) {
        traces.push({
          type: "scatter",
          x: [...s.x, ...[...s.x].reverse()],
          y: [...s.ci.map((c) => c[1]), ...[...s.ci].map((c) => c[0]).reverse()],
          fill: "toself",
          fillcolor: hexAlpha(color, 0.12),
          line: { width: 0 },
          hoverinfo: "skip",
          showlegend: false,
          name: `${s.name} CI`,
        });
      }
      traces.push({
        type: "scatter",
        mode: markers || s.x.length < 3 ? "lines+markers" : "lines",
        name: s.name,
        x: s.x,
        y: s.y,
        line: { color, width: 2, shape: "linear" },
        marker: { color, size: 8, line: { color: p.surface, width: 2 } },
        connectgaps: false,
        hovertemplate: percent ? "%{y:+.2%}<extra>%{fullData.name}</extra>" : "%{y:" + (yFormat || "+.3f") + "}<extra>%{fullData.name}</extra>",
      });
    });
    const nPoints = Math.max(0, ...series.map((s) => s.x.length));
    const layout = baseLayout(p, {
      hovermode: hover,
      showlegend: series.length > 1,
      xaxis: {
        ...baseLayout(p).xaxis,
        ...(nPoints <= 10
          ? { type: "category" }
          : { type: "date", tickformat: spanDays(series) > 300 ? "%b %Y" : "%b %-d", hoverformat: "%Y-%m-%d" }),
      },
      yaxis: {
        ...baseLayout(p).yaxis,
        title: yTitle ? { text: yTitle, font: { size: 12 } } : undefined,
        tickformat: percent ? "+.1%" : undefined,
        zeroline: zero,
      },
    });
    plot(node, traces, layout);
  });
}

// Days between the earliest and latest x of date series (0 when not dates).
function spanDays(series) {
  const xs = series.flatMap((s) => [s.x[0], s.x[s.x.length - 1]]).filter(Boolean).map((x) => Date.parse(x)).filter((t) => !Number.isNaN(t));
  return xs.length ? (Math.max(...xs) - Math.min(...xs)) / 86400000 : 0;
}

/** One series of bars (identity: one hue). items: [{label, value, hover?}] */
export function barChart(node, items, { yTitle, horizontal = false, percent = false, color = 0 } = {}) {
  return register(node, () => {
    const p = palette();
    const hue = p.series[color];
    const trace = {
      type: "bar",
      x: horizontal ? items.map((i) => i.value) : items.map((i) => i.label),
      y: horizontal ? items.map((i) => i.label) : items.map((i) => i.value),
      orientation: horizontal ? "h" : "v",
      marker: { color: hue, line: { color: p.surface, width: 2 } },
      hovertemplate: (percent ? "%{" + (horizontal ? "x" : "y") + ":.2%}" : "%{" + (horizontal ? "x" : "y") + ":,.3~f}") + "<extra>%{" + (horizontal ? "y" : "x") + "}</extra>",
    };
    const axisVal = { ...baseLayout(p).yaxis, title: yTitle ? { text: yTitle, font: { size: 12 } } : undefined, tickformat: percent ? ".1%" : undefined };
    const axisCat = { ...baseLayout(p).xaxis, showgrid: false, automargin: true, autorange: horizontal ? "reversed" : true, type: "category" };
    const layout = baseLayout(p, {
      hovermode: "closest",
      showlegend: false,
      bargap: 0.35,
      xaxis: horizontal ? axisVal : axisCat,
      yaxis: horizontal ? axisCat : axisVal,
      margin: { l: horizontal ? 70 : 48, r: 16, t: 8, b: 40 },
    });
    plot(node, [trace], layout);
  });
}

/** Scatter of (x, y) with labels; a single series in slot 1. */
export function scatterChart(node, points, { xTitle, yTitle, percentY = false } = {}) {
  return register(node, () => {
    const p = palette();
    const trace = {
      type: "scatter",
      mode: "markers",
      x: points.map((q) => q.x),
      y: points.map((q) => q.y),
      text: points.map((q) => q.label),
      marker: { color: p.series[0], size: 9, line: { color: p.surface, width: 2 }, opacity: 0.9 },
      hovertemplate: "<b>%{text}</b><br>sentiment %{x:+.2f} · return %{y:+.2%}<extra></extra>",
    };
    const layout = baseLayout(p, {
      hovermode: "closest",
      showlegend: false,
      xaxis: { ...baseLayout(p).xaxis, title: { text: xTitle, font: { size: 12 } }, zeroline: true },
      yaxis: { ...baseLayout(p).yaxis, title: { text: yTitle, font: { size: 12 } }, tickformat: percentY ? "+.1%" : undefined, zeroline: true },
    });
    plot(node, [trace], layout);
  });
}

export function chartNode(cls = "") {
  return el("div", { class: `chart loading ${cls}` });
}

export function legendKeys(names) {
  const p = palette();
  return el(
    "div",
    { class: "legend-keys" },
    names.map((n, i) => el("span", { class: "key" }, el("span", { class: "swatch", style: `background:${p.series[i]}` }), n)),
  );
}

function hexAlpha(hex, alpha) {
  const h = hex.replace("#", "");
  const n = parseInt(h.length === 3 ? h.split("").map((c) => c + c).join("") : h, 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

export { currentTheme };
