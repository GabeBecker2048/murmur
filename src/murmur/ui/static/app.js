// The dashboard shell: theme toggle, router (History API), boot. Shared helpers live in
// lib.js; pages live in ./pages/ and export render(main, parts, query).

import { api, el, clear, link, spinner, state, loadToken, currentTheme, navigate, setTitle } from "./lib.js";

export * from "./lib.js";

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
  findings: () => import("./pages/findings.js"),
  "news-vs-prices": () => import("./pages/newsvsprices.js"),
  companies: () => import("./pages/companies.js"),
  index: () => import("./pages/index.js"),
  headlines: () => import("./pages/headlines.js"),
  downloads: () => import("./pages/downloads.js"),
  methodology: () => import("./pages/methodology.js"),
  status: () => import("./pages/status.js"),
  story: () => import("./pages/story.js"),
  admin: () => import("./pages/admin.js"),
};

/** Old addresses (0.7-0.8) and where they live now; the server answers them with a 301
 * too, this covers links followed inside the page. */
export const ALIASES = { leadlag: "news-vs-prices", tickers: "companies", sources: "news-vs-prices" };

function aliased() {
  const parts = location.pathname.split("/").filter(Boolean);
  const to = ALIASES[parts[0]];
  if (!to) return false;
  const rest = parts[0] === "sources" ? [] : parts.slice(1);
  navigate(`/${[to, ...rest].join("/")}${location.search}`, { replace: true });
  return true;
}

function notFound(main) {
  setTitle("Not found");
  clear(main).append(
    el(
      "section",
      { class: "card not-found" },
      el("p", { class: "kicker", text: "404" }),
      el("h1", { text: "No such page" }),
      el("p", { class: "muted" }, "The address may be from an older version of the dashboard. "),
      el("p", {}, link("/", "Start at the overview"), " · ", link("/findings", "Read the findings"), " · ", link("/downloads", "Get the data")),
    ),
  );
}

let currentPage = null;
let navToken = 0;

async function render() {
  if (aliased()) return;
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
    notFound(main);
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
  // an address with #section opens at that section; anything else at the top
  const target = location.hash && document.getElementById(decodeURIComponent(location.hash.slice(1)));
  if (target) target.scrollIntoView();
  else window.scrollTo({ top: 0 });
}

function setupLinks() {
  document.addEventListener("click", (ev) => {
    const a = ev.target.closest("a[data-link]");
    if (!a || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button !== 0) return;
    const url = new URL(a.href, location.href);
    if (url.origin !== location.origin) return;
    ev.preventDefault();
    navigate(url.pathname + url.search + url.hash);
  });
  window.addEventListener("popstate", render);
  window.addEventListener("murmur:navigate", render);
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
    document.getElementById("nav-index").textContent = (state.ui.index && state.ui.index.title) || "Index";
    document.getElementById("foot-version").textContent = `version ${state.ui.version}`;
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
