// Murmur dashboard: router, API client, theme, DOM helpers. Pages live in ./pages/.
// Only /api/v1 and /data are used; nothing here knows about the store.

export const API = "/api/v1";
export const state = {
  ui: null, // GET /ui
  token: null, // admin bearer token (sessionStorage)
};

// ---- API -----------------------------------------------------------------------

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `HTTP ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

export async function api(path, { method = "GET", body, auth = false, raw = false } = {}) {
  const headers = {};
  if (body !== undefined) headers["content-type"] = "application/json";
  if (auth || state.token) {
    if (auth && !state.token) throw new ApiError(401, "no token");
    headers.authorization = `Bearer ${state.token}`;
  }
  const res = await fetch(path.startsWith("/") ? path : `${API}/${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (raw) return res;
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }
  if (!res.ok) throw new ApiError(res.status, data && data.detail ? String(data.detail) : text);
  return data;
}

export function qs(params) {
  const out = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") out.set(k, v);
  }
  const s = out.toString();
  return s ? `?${s}` : "";
}

// ---- token ---------------------------------------------------------------------

export function loadToken() {
  try {
    state.token = sessionStorage.getItem("murmur.token") || null;
  } catch {
    state.token = null;
  }
  return state.token;
}

export function saveToken(token) {
  state.token = token || null;
  try {
    if (token) sessionStorage.setItem("murmur.token", token);
    else sessionStorage.removeItem("murmur.token");
  } catch {
    /* private mode: the token lives for this page load only */
  }
}

// ---- DOM helpers (textContent only: everything shown comes from the API) ------

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (k === "text") node.textContent = v;
    else if (v === true) node.setAttribute(k, "");
    else node.setAttribute(k, v);
  }
  append(node, children);
  return node;
}

export function append(node, children) {
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

export function link(href, text, attrs = {}) {
  return el("a", { href, "data-link": true, ...attrs }, text);
}

export function card(title, body, { sub, cls = "", actions } = {}) {
  const head = el(
    "div",
    { class: "card-head" },
    el("div", {}, el("h2", { text: title }), sub ? el("div", { class: "sub" }, sub) : null),
    actions || null,
  );
  return el("section", { class: `card ${cls}` }, title ? head : null, body);
}

export function tile(label, value, { delta, deltaClass = "", note } = {}) {
  return el(
    "div",
    { class: "tile" },
    el("div", { class: "label", text: label }),
    el("div", { class: "value", text: value }),
    delta ? el("div", { class: `delta ${deltaClass}`, text: delta }) : null,
    note ? el("div", { class: "faint small", text: note }) : null,
  );
}

export function table(columns, rows, { compact = false, onRow, empty = "Nothing here yet." } = {}) {
  if (!rows.length) return el("div", { class: "empty", text: empty });
  const thead = el(
    "thead",
    {},
    el(
      "tr",
      {},
      columns.map((c) => el("th", { class: c.num ? "num" : "", text: c.label })),
    ),
  );
  const tbody = el("tbody");
  for (const row of rows) {
    const tr = el(
      "tr",
      { class: onRow ? "clickable" : "" },
      columns.map((c) => {
        const v = typeof c.value === "function" ? c.value(row) : row[c.key];
        return el("td", { class: c.num ? "num" : "" }, v instanceof Node ? v : fmtCell(v, c));
      }),
    );
    if (onRow) tr.addEventListener("click", () => onRow(row));
    tbody.append(tr);
  }
  return el("div", { class: `table-wrap ${compact ? "compact" : ""}` }, el("table", {}, thead, tbody));
}

function fmtCell(v, c) {
  if (v === null || v === undefined) return "";
  if (c.format) return c.format(v);
  return String(v);
}

export function badge(text, kind = "") {
  return el("span", { class: `badge ${kind}` }, el("span", { class: "dot" }), text);
}

export function select(options, value, onChange, attrs = {}) {
  const s = el("select", attrs);
  for (const o of options) {
    const [v, label] = Array.isArray(o) ? o : [o, o];
    s.append(el("option", { value: v, selected: String(v) === String(value) || undefined }, label));
  }
  s.addEventListener("change", () => onChange(s.value));
  return s;
}

export function field(label, control) {
  return el("label", { class: "field" }, el("span", { text: label }), control);
}

export function segmented(options, value, onChange) {
  const wrap = el("div", { class: "seg", role: "group" });
  for (const o of options) {
    const [v, label] = Array.isArray(o) ? o : [o, o];
    const b = el("button", { type: "button", class: String(v) === String(value) ? "on" : "" }, label);
    b.addEventListener("click", () => {
      for (const x of wrap.children) x.classList.remove("on");
      b.classList.add("on");
      onChange(v);
    });
    wrap.append(b);
  }
  return wrap;
}

export function notice(text, kind = "") {
  return el("div", { class: `notice ${kind}`, text });
}

export function spinner(text = "Loading…") {
  return el("div", { class: "empty faint", text });
}

// ---- formatting ------------------------------------------------------------------

export const fmt = {
  r: (v) => (v === null || v === undefined || Number.isNaN(v) ? "–" : (v >= 0 ? "+" : "") + v.toFixed(3)),
  r2: (v) => (v === null || v === undefined ? "–" : (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(2)),
  p: (v) => {
    if (v === null || v === undefined) return "–";
    if (v < 0.001) return "<0.001";
    return v.toFixed(3);
  },
  pct: (v, digits = 2) =>
    v === null || v === undefined ? "–" : `${v >= 0 ? "+" : "−"}${(Math.abs(v) * 100).toFixed(digits)}%`,
  pct0: (v) => (v === null || v === undefined ? "–" : `${(v * 100).toFixed(2)}%`),
  int: (v) => (v === null || v === undefined ? "–" : Number(v).toLocaleString()),
  compact: (v) => {
    if (v === null || v === undefined) return "–";
    const n = Number(v);
    if (n >= 1e12) return `${(n / 1e12).toFixed(2)}T`;
    if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
    if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
    if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
    return n.toLocaleString();
  },
  bytes: (v) => {
    if (v === null || v === undefined) return "–";
    const n = Number(v);
    if (n >= 1 << 30) return `${(n / (1 << 30)).toFixed(2)} GB`;
    if (n >= 1 << 20) return `${(n / (1 << 20)).toFixed(1)} MB`;
    if (n >= 1 << 10) return `${(n / (1 << 10)).toFixed(0)} KB`;
    return `${n} B`;
  },
  price: (v) => (v === null || v === undefined ? "–" : Number(v).toFixed(2)),
  date: (v) => (v ? String(v).slice(0, 10) : "–"),
  time: (v) => {
    if (!v) return "–";
    const d = new Date(v);
    return Number.isNaN(d.getTime()) ? String(v) : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  },
  ago: (v) => {
    if (!v) return "–";
    const s = (Date.now() - new Date(v).getTime()) / 1000;
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    return `${Math.round(s / 86400)} d ago`;
  },
  seconds: (v) => (v === null || v === undefined ? "–" : v < 10 ? `${v.toFixed(1)} s` : `${Math.round(v)} s`),
};

export const WINDOW_LABEL = { overnight: "Overnight news", intraday: "Intraday news", day: "Day news" };
export const RETURN_LABEL = { overnight: "overnight return", intraday: "intraday return", daily: "daily return" };
export const MATCHED = [
  ["overnight", "overnight"],
  ["intraday", "intraday"],
  ["day", "daily"],
];
export const LAG_LABEL = { "-1": "news after return (follows)", 0: "same session", 1: "news before return (leads)" };

export function pairLabel(window, ret) {
  return `${WINDOW_LABEL[window] || window} vs ${RETURN_LABEL[ret] || ret}`;
}

export function daysAgo(n) {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - n);
  return d.toISOString().slice(0, 10);
}

export function today() {
  return new Date().toISOString().slice(0, 10);
}

// ---- theme -----------------------------------------------------------------------

export function currentTheme() {
  const forced = document.documentElement.dataset.theme;
  if (forced === "light" || forced === "dark") return forced;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function setupTheme() {
  const btn = document.getElementById("theme-toggle");
  btn.addEventListener("click", () => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("murmur.theme", next);
    } catch {
      /* ignore */
    }
    window.dispatchEvent(new CustomEvent("murmur:theme"));
  });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (!document.documentElement.dataset.theme) window.dispatchEvent(new CustomEvent("murmur:theme"));
  });
}

// ---- router -----------------------------------------------------------------------

const routes = {
  "": () => import("./pages/overview.js"),
  leadlag: () => import("./pages/leadlag.js"),
  tickers: () => import("./pages/tickers.js"),
  sources: () => import("./pages/sources.js"),
  index: () => import("./pages/index.js"),
  downloads: () => import("./pages/downloads.js"),
  admin: () => import("./pages/admin.js"),
};

let currentPage = null;
let navToken = 0;

export function navigate(href, { replace = false } = {}) {
  if (replace) history.replaceState(null, "", href);
  else history.pushState(null, "", href);
  render();
}

export function setTitle(text) {
  const base = (state.ui && state.ui.title) || "Murmur";
  document.title = text ? `${text} · ${base}` : base;
}

async function render() {
  const main = document.getElementById("main");
  const parts = location.pathname.split("/").filter(Boolean);
  const first = parts[0] || "";
  const loader = routes[first];
  document.querySelectorAll(".nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === first));
  document.getElementById("nav").classList.remove("open");
  const mine = ++navToken;
  if (currentPage && currentPage.destroy) {
    try {
      currentPage.destroy();
    } catch {
      /* ignore */
    }
    currentPage = null;
  }
  if (!loader) {
    clear(main).append(el("div", { class: "card" }, el("h1", { text: "Not found" }), el("p", {}, "No such page. ", link("/", "Back to the overview"))));
    setTitle("Not found");
    return;
  }
  clear(main).append(spinner());
  try {
    const mod = await loader();
    if (mine !== navToken) return;
    const page = await mod.render(main, parts.slice(1), new URLSearchParams(location.search));
    if (mine !== navToken) return;
    currentPage = page || null;
  } catch (e) {
    if (mine !== navToken) return;
    clear(main).append(
      el("div", { class: "card" }, el("h1", { text: "Something went wrong" }), el("p", { class: "muted", text: e.message || String(e) })),
    );
  }
  main.focus({ preventScroll: true });
  window.scrollTo({ top: 0 });
}

function setupLinks() {
  document.addEventListener("click", (ev) => {
    const a = ev.target.closest("a[data-link]");
    if (!a || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button !== 0) return;
    const url = new URL(a.href, location.href);
    if (url.origin !== location.origin) return;
    ev.preventDefault();
    navigate(url.pathname + url.search);
  });
  window.addEventListener("popstate", render);
  const nav = document.querySelector(".nav");
  nav.id = "nav";
  const menu = document.getElementById("menu-toggle");
  menu.addEventListener("click", () => {
    const open = nav.classList.toggle("open");
    menu.setAttribute("aria-expanded", String(open));
  });
}

async function boot() {
  loadToken();
  setupTheme();
  setupLinks();
  try {
    state.ui = await api("ui");
    document.getElementById("brand-title").textContent = state.ui.title || "Murmur";
    document.getElementById("nav-index").textContent = (state.ui.index && state.ui.index.title) || "Index";
    document.getElementById("foot-version").textContent = `Murmur ${state.ui.version}`;
    if (state.ui.theme && state.ui.theme !== "auto" && !document.documentElement.dataset.theme) {
      let stored = null;
      try {
        stored = localStorage.getItem("murmur.theme");
      } catch {
        /* ignore */
      }
      if (!stored) document.documentElement.dataset.theme = state.ui.theme;
    }
  } catch (e) {
    state.ui = { title: "Murmur", default_months: 3, default_method: "pearson", index: { name: "m100", title: "Murmur 100", size: 100 } };
    document.getElementById("foot-version").textContent = "API unreachable";
  }
  render();
}

boot();
