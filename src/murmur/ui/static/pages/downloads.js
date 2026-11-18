// Data: build a bundle of any date range, take a ready-made monthly archive, use the
// stable latest URLs, or browse every file. The builder's choices live in the URL.

import { api, qs, el, card, clear, table, fmt, setTitle, field, segmented, badge, navigate, link, codeBlock, state } from "../lib.js";
import { DELIVERY } from "./overview.js";

const iso = (d) => d.toISOString().slice(0, 10);
function minus(dateIso, { months = 0, days = 0 }) {
  const d = new Date(`${dateIso}T12:00:00Z`);
  d.setUTCMonth(d.getUTCMonth() - months);
  d.setUTCDate(d.getUTCDate() - days);
  return iso(d);
}

export async function render(main, parts, query) {
  setTitle("Data");
  const [listing, manifest] = await Promise.all([api("datasets"), api("/data/index.json").catch(() => ({}))]);
  const all = listing.items || [];
  const open = all.filter((d) => d.public !== false && d.last_date);
  // daily datasets give real first/last days (monthly ones only YYYY-MM)
  const daily = open.filter((d) => d.cadence === "daily");
  const first = (daily.length ? daily : open).map((d) => d.first_date).filter(Boolean).sort()[0];
  const last = (daily.length ? daily : open).map((d) => d.last_date).filter(Boolean).sort().slice(-1)[0];
  const lastDay = last && last.length === 7 ? `${last}-28` : last;

  const presets = {
    "1m": ["1 month", () => [minus(lastDay, { months: 1 }), lastDay]],
    "3m": ["3 months", () => [minus(lastDay, { months: 3 }), lastDay]],
    ytd: ["Year to date", () => [`${lastDay.slice(0, 4)}-01-01`, lastDay]],
    "1y": ["1 year", () => [minus(lastDay, { months: 12 }), lastDay]],
    all: ["Everything", () => [first.length === 7 ? `${first}-01` : first, lastDay]],
  };
  const b = {
    datasets: (query.get("datasets") || "sentiment,prices").split(",").filter((x) => open.some((d) => d.name === x)),
    range: query.get("range") || (query.get("from") ? "" : "3m"),
    from: query.get("from") || "",
    to: query.get("to") || "",
    format: query.get("format") === "csv" ? "csv" : "parquet",
  };
  if (b.range && presets[b.range] && lastDay) [b.from, b.to] = presets[b.range][1]();
  const remember = () => history.replaceState(null, "", `/downloads${qs({ datasets: b.datasets.join(","), range: b.range || undefined, from: b.range ? undefined : b.from, to: b.range ? undefined : b.to, format: b.format === "csv" ? "csv" : undefined })}`);

  // ---- builder
  const chips = el(
    "div",
    { class: "chips", role: "group", "aria-label": "Datasets" },
    open.map((d) => {
      const box = el("input", { type: "checkbox", value: d.name, checked: b.datasets.includes(d.name) || undefined });
      box.addEventListener("change", () => {
        b.datasets = [...chips.querySelectorAll("input:checked")].map((x) => x.value);
        update();
      });
      return el("label", { class: "chip" }, box, d.title);
    }),
  );
  const fromIn = el("input", { type: "date", value: b.from, min: first && first.length === 10 ? first : undefined, "aria-label": "From" });
  const toIn = el("input", { type: "date", value: b.to, "aria-label": "To" });
  const custom = () => {
    b.range = "";
    b.from = fromIn.value;
    b.to = toIn.value;
    update();
  };
  fromIn.addEventListener("change", custom);
  toIn.addEventListener("change", custom);
  const rangeSeg = segmented(Object.entries(presets).map(([k, [label]]) => [k, label]), b.range, (k) => {
    b.range = k;
    [b.from, b.to] = presets[k][1]();
    fromIn.value = b.from;
    toIn.value = b.to;
    update();
  });
  const formatSeg = segmented([["parquet", "Parquet"], ["csv", "CSV"]], b.format, (v) => {
    b.format = v;
    update();
  });
  const estimate = el("div", { class: "muted small", "aria-live": "polite" });
  const download = el("a", { class: "btn primary", href: "#" }, "Download .tar.gz");
  const snippets = el("div", { class: "stack" });
  let seq = 0;
  async function update() {
    remember();
    const params = { from: b.from, to: b.to, datasets: b.datasets.join(","), format: b.format === "csv" ? "csv" : undefined };
    const url = `/api/v1/bundle${qs(params)}`;
    const full = `${location.origin}${url}`;
    download.setAttribute("href", url);
    const mine = ++seq;
    if (!b.datasets.length || !b.from || !b.to) {
      estimate.textContent = "Pick at least one dataset and a range.";
      download.classList.add("disabled");
      download.setAttribute("aria-disabled", "true");
      return;
    }
    estimate.textContent = "Checking…";
    try {
      const plan = await api(`bundle${qs({ ...params, dry_run: true })}`);
      if (mine !== seq) return;
      download.classList.remove("disabled");
      download.removeAttribute("aria-disabled");
      clear(estimate).append(
        el("span", { class: "size-note", text: `up to ${fmt.bytes(plan.estimated_bytes)}` }),
        ` · ${plan.tables.length} table${plan.tables.length === 1 ? "" : "s"} (${plan.tables.map((t) => `${t.dataset}/${t.table}`).join(", ")}) · ${plan.name}`,
      );
    } catch (e) {
      if (mine !== seq) return;
      estimate.textContent = e.message;
      download.classList.add("disabled");
      download.setAttribute("aria-disabled", "true");
    }
    const firstTable = b.datasets[0] === "news" ? "articles" : b.datasets[0];
    clear(snippets).append(
      el("h3", { text: "Or from a script" }),
      codeBlock(`curl -o murmur.tar.gz "${full}"\ntar -xzf murmur.tar.gz`),
      codeBlock(
        `import io, tarfile, urllib.request\nimport pandas as pd\n\nblob = urllib.request.urlopen("${full}").read()\nwith tarfile.open(fileobj=io.BytesIO(blob)) as tar:\n    frames = {m.name.split("/", 1)[1]: pd.read_${b.format}(tar.extractfile(m))\n              for m in tar.getmembers() if m.name.endswith(".${b.format}")}`,
      ),
      codeBlock(
        `-- DuckDB, straight from the stable URL of the newest session\nINSTALL httpfs; LOAD httpfs;\nSELECT * FROM read_parquet('${location.origin}/data/${b.datasets[0]}/latest.${firstTable}.parquet');`,
      ),
    );
  }
  download.addEventListener("click", (ev) => {
    if (download.classList.contains("disabled")) ev.preventDefault();
  });

  const builder = card(
    "Build a bundle",
    el(
      "div",
      { class: "stack" },
      field("Datasets", chips),
      el("div", { class: "filters", style: "margin:0" }, field("Range", rangeSeg), field("From", fromIn), field("To", toIn), field("Format", formatSeg)),
      el("div", { class: "actions" }, download, estimate),
      el("p", { class: "help", text: "One .tar.gz: a file per table with that range's rows, a README with every license, the manifest and SHA-256 checksums. CSV of news is limited to 93 days." }),
      snippets,
    ),
    { sub: "Any date range of any datasets, in one file" },
  );

  // ---- ready-made archives
  const archives = ((manifest.archives || {}).files || []).slice().reverse();
  const archivesCard = card(
    "Monthly archives",
    archives.length
      ? el("div", { class: "chips" }, archives.map((a) => el("a", { class: "chip", href: `/data/${a.path}`, download: "" }, `${a.month} · ${fmt.bytes(a.bytes)}`)))
      : el("div", { class: "empty", text: "The first archive is written when a month is over." }),
    { sub: "Every public dataset of a finished month" },
  );

  // ---- latest URLs
  const latestRows = [];
  for (const d of open) {
    const ds = manifest.datasets && manifest.datasets[d.name];
    for (const t of Object.keys((ds && ds.tables) || {})) latestRows.push({ dataset: d.name, table: t });
  }
  const idxName = (state.ui && state.ui.index && state.ui.index.name) || "m100";
  const scheduleRows = [
    ...all.map((d) => ({ what: d.title, cadence: d.cadence, when: DELIVERY[d.name === idxName ? "m100" : d.name] || "" })),
    { what: "Monthly archives", cadence: "monthly", when: DELIVERY.archives },
    { what: "Findings", cadence: "snapshot", when: DELIVERY.findings },
  ];
  const scheduleCard = card(
    "When the data arrives",
    el(
      "div",
      {},
      el("p", { class: "muted small", text: "The pipeline runs every evening at 18:00 New York time (America/New_York), weekends included, and publishes within about 15 minutes. Weekends and market holidays add headlines to the next trading session; they get no prices or analysis of their own." }),
      table(
        [
          { label: "Dataset", value: (r) => r.what },
          { label: "Cadence", value: (r) => r.cadence },
          { label: "Delivered", value: (r) => r.when },
        ],
        scheduleRows,
        { compact: true },
      ),
    ),
  );
  const latestCard = card(
    "Always the newest",
    el(
      "div",
      {},
      el("p", { class: "muted small", text: "These addresses never change; each serves the newest session (or month) of its table. Add ?format=csv for CSV." }),
      table(
        [
          { label: "Dataset", value: (r) => r.dataset },
          { label: "Table", value: (r) => r.table },
          { label: "URL", value: (r) => el("span", {}, el("a", { href: `/data/${r.dataset}/latest.${r.table}.parquet`, class: "mono small" }, `/data/${r.dataset}/latest.${r.table}.parquet`), " · ", el("a", { href: `/data/${r.dataset}/latest.${r.table}.parquet?format=csv` }, "CSV")) },
        ],
        latestRows,
        { compact: true },
      ),
      el("p", { class: "muted small", style: "margin-top:8px" }, "The newest findings: ", el("a", { href: "/data/findings/latest.json", class: "mono" }, "/data/findings/latest.json"), ". The manifest of everything: ", el("a", { href: "/data/index.json", class: "mono" }, "/data/index.json"), "."),
    ),
  );

  // ---- browse every file
  const browse = el(
    "div",
    {},
    all.map((d) => {
      const body = el("div", {}, el("div", { class: "empty faint", text: "Loading…" }));
      const det = el("details", { class: "more" }, el("summary", {}, `${d.title} `, licenseBadge(d), el("span", { class: "faint small", text: ` · ${d.cadence} · ${d.file_count} files · ${fmt.bytes(d.bytes)}` })), body);
      let loaded = false;
      det.addEventListener("toggle", () => {
        if (det.open && !loaded) {
          loaded = true;
          renderDataset(body, d.name);
        }
      });
      return det;
    }),
  );

  clear(main).append(
    el(
      "div",
      { class: "page-head" },
      el("p", { class: "kicker", text: "Data" }),
      el("h1", { text: "Take the data" }),
      el(
        "p",
        { class: "lede" },
        `Everything Murmur publishes, from ${first || "–"} to ${last || "–"}: free to use under CC BY 4.0 (prices: the source's terms). Dataset ${manifest.data_version ? `v${manifest.data_version}` : "v1.0.0"}; see `,
        link("/methodology#versions", "what stays stable"),
        ". How to read it: ",
        el("a", { href: "https://github.com/GabeBecker2048/murmur/blob/main/docs/quickstart.md" }, "quick start"),
        ".",
      ),
    ),
    el("div", { class: "stack" }, builder, scheduleCard, el("div", { class: "split" }, latestCard, archivesCard), card("Every file", browse, { sub: "Day files, monthly files and compacted months, with each dataset's columns and license" })),
  );
  if (lastDay) update();
}

function licenseBadge(d) {
  if (d.public === false) return badge("private", "warning");
  const name = (d.license && d.license.name) || "open";
  if (/^source terms/i.test(name)) return badge("source terms", "warning");
  return badge(name, "good");
}

async function renderDataset(wrap, name) {
  let d;
  try {
    d = await api(`datasets/${name}${qs({ limit: 2000 })}`);
  } catch (e) {
    clear(wrap).append(el("div", { class: "empty", text: e.message }));
    return;
  }
  const lic = d.license || {};
  const tables = Object.entries(d.tables || {});
  clear(wrap).append(
    el("p", { class: "muted", text: d.description }),
    d.public === false ? el("div", { class: "notice warn", text: "This dataset is not published yet: its source's terms do not allow redistribution. The files exist and are used by the analysis, but downloads answer 403." }) : null,
    /^source terms/i.test(lic.name || "") ? el("div", { class: "notice warn", text: "This table carries no license from Murmur. It is republished for non-commercial research under the source's terms; see the notice in every file's metadata." }) : null,
    el("p", { class: "small muted" }, "License: ", lic.url ? el("a", { href: lic.url, target: "_blank", rel: "noopener" }, lic.name) : lic.name || "–", lic.attribution ? ` · attribute "${lic.attribution}"` : "", lic.contact ? ` · contact: ${lic.contact}` : ""),
    el("div", { class: "stack" }, tables.map(([t, spec]) => el("div", {}, el("h3", {}, el("span", { class: "mono" }, t), el("span", { class: "faint small", text: ` · key: ${(spec.key || []).join(", ")}` })), el("p", { class: "small mono muted", style: "word-break:break-word", text: (spec.columns || []).join(", ") })))),
    table(
      [
        { label: "Date", key: "date" },
        { label: "Table", key: "table" },
        { label: "Rows", num: true, value: (f) => (f.rows === null ? "" : fmt.int(f.rows)) },
        { label: "Size", num: true, value: (f) => fmt.bytes(f.bytes) },
        { label: "Download", value: (f) => (f.format === "parquet" ? el("span", {}, el("a", { href: `/data/${f.path}` }, "Parquet"), " · ", el("a", { href: `/data/${f.path}?format=csv` }, "CSV")) : el("a", { href: `/data/${f.path}` }, f.format.toUpperCase())) },
      ],
      (d.files || []).slice().reverse(),
      { compact: true, empty: "No files yet." },
    ),
  );
}
