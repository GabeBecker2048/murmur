// Downloads: the six datasets, their licenses, and every file as Parquet or CSV.

import { api, qs, el, card, clear, table, fmt, state, setTitle, field, select, badge, navigate } from "../app.js";

export async function render(main, parts, query) {
  setTitle("Downloads");
  const listing = await api("datasets");
  const items = listing.items || [];
  const picked = query.get("dataset") || (items[0] && items[0].name);
  const canSeePrivate = Boolean(state.token);

  const cards = el(
    "div",
    { class: "grid cols-3" },
    items.map((d) =>
      el(
        "section",
        { class: "card", style: "cursor:pointer", onclick: () => navigate(`/downloads${qs({ dataset: d.name })}`) },
        el("div", { class: "card-head" }, el("h2", { text: d.title }), licenseBadge(d)),
        el("p", { class: "muted small", text: d.description }),
        el("p", { class: "faint small", text: `${d.cadence} · ${d.file_count} files · ${fmt.bytes(d.bytes)}${d.first_date ? ` · ${d.first_date} to ${d.last_date}` : ""}` }),
      ),
    ),
  );

  const detailWrap = el("div");
  clear(main).append(
    el("div", { class: "page-head" }, el("h1", { text: "Downloads" }), el("p", { class: "lede", text: "Everything the pipeline publishes, as Parquet files (CSV on request). Daily files are one per trading day; finished months are compacted into one file per table. The manifest at /data/index.json lists it all." })),
    cards,
    el("div", { style: "margin-top:14px" }, detailWrap),
  );
  if (picked) await renderDataset(detailWrap, picked, canSeePrivate);
}

function licenseBadge(d) {
  if (d.public === false) return badge("private", "warning");
  const name = (d.license && d.license.name) || "open";
  if (/^source terms/i.test(name)) return badge("source terms", "warning");
  return badge(name, "good");
}

async function renderDataset(wrap, name, canSeePrivate) {
  let d;
  try {
    d = await api(`datasets/${name}${qs({ limit: 2000 })}`);
  } catch (e) {
    clear(wrap).append(el("div", { class: "empty", text: e.message }));
    return;
  }
  const tables = Object.entries(d.tables || {});
  const tableSel = el("select");
  tableSel.append(el("option", { value: "" }, "All tables"));
  for (const [t] of tables) tableSel.append(el("option", { value: t }, t));
  const files = el("div");
  const draw = () => {
    const t = tableSel.value;
    const rows = (d.files || []).filter((f) => !t || f.table === t);
    clear(files).append(
      table(
        [
          { label: "Date", key: "date" },
          { label: "Table", key: "table" },
          { label: "Rows", num: true, value: (f) => (f.rows === null ? "" : fmt.int(f.rows)) },
          { label: "Size", num: true, value: (f) => fmt.bytes(f.bytes) },
          {
            label: "Download",
            value: (f) =>
              f.format === "parquet"
                ? el("span", {}, el("a", { href: `/data/${f.path}` }, "Parquet"), " · ", el("a", { href: `/data/${f.path}?format=csv` }, "CSV"))
                : el("a", { href: `/data/${f.path}` }, f.format.toUpperCase()),
          },
        ],
        rows,
        { compact: true, empty: "No files yet." },
      ),
    );
  };
  tableSel.addEventListener("change", draw);
  draw();

  const dict = el(
    "div",
    { class: "stack" },
    tables.map(([t, spec]) =>
      el("div", {}, el("h3", {}, el("span", { class: "mono" }, t), el("span", { class: "faint small" }, ` · key: ${(spec.key || []).join(", ")}`)), el("p", { class: "small mono muted", style: "word-break:break-word", text: (spec.columns || []).join(", ") })),
    ),
  );
  const lic = d.license || {};
  const licenseNote = el(
    "p",
    { class: "small muted" },
    "License: ",
    lic.url ? el("a", { href: lic.url, target: "_blank", rel: "noopener" }, lic.name) : lic.name || "–",
    lic.attribution ? ` · attribute "${lic.attribution}"` : "",
    lic.note ? ` · ${lic.note}` : "",
    lic.contact ? ` · contact: ${lic.contact}` : "",
  );
  const terms = /^source terms/i.test(lic.name || "") ? el("div", { class: "notice warn", text: "This table carries no license from Murmur. It is republished for non-commercial research under the source's terms; see the notice above and in every file's metadata." }) : null;
  const privateNote = d.public === false ? el("div", { class: "notice warn", text: canSeePrivate ? "This dataset is private: links work for you because you are signed in to the admin area; visitors get 403." : "This dataset is not published yet: its source's terms do not allow redistribution. The files exist and are used by the analysis, but downloads answer 403." }) : null;

  clear(wrap).append(
    card(d.title, el("div", { class: "stack" }, privateNote, terms, el("p", { class: "muted", text: d.description }), licenseNote, el("div", { class: "grid cols-2" }, el("div", {}, el("h3", { text: "Tables and columns" }), dict), el("div", {}, el("h3", { text: "Layout" }), el("p", { class: "small mono muted" }, `${name}/YYYY/MM/DD/YYYYMMDD.{table}.parquet`), el("p", { class: "small mono muted" }, `${name}/YYYY/MM/YYYYMM.{table}.parquet (monthly / compacted)`), el("p", { class: "small muted" }, "Append ?format=csv to any Parquet URL. In Python: ", el("code", {}, `pd.read_parquet(url)`)))), el("div", { class: "filters" }, field("Table", tableSel), el("span", { class: "muted small", text: `${d.total} files` })), files), {
      sub: `${d.cadence} · ${d.first_date || "–"} to ${d.last_date || "–"}`,
    }),
  );
}
