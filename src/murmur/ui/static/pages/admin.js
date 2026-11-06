// Admin: sign in with the API token, then jobs, backfills, the scheduler, settings
// and logs. Every action is a call to a write endpoint; reads refresh on a timer.

import { api, qs, el, card, clear, table, fmt, state, setTitle, field, select, saveToken, badge, notice, navigate, today, daysAgo, ApiError } from "../app.js";

const TABS = [
  ["jobs", "Jobs"],
  ["backfill", "Backfill"],
  ["scheduler", "Scheduler"],
  ["settings", "Settings"],
  ["logs", "Logs"],
];
const STATUS_KIND = { done: "good", running: "accent", queued: "", cancelling: "warning", failed: "critical", cancelled: "", interrupted: "warning" };

export async function render(main, parts, query) {
  setTitle("Admin");
  if (!state.token) return renderLogin(main);
  // verify the token cheaply: a write endpoint that changes nothing is not available, so
  // probe a private-safe read with the token and let 401s on actions do the rest.
  const tab = parts[0] || "jobs";
  const tabs = el("div", { class: "tabs" }, TABS.map(([k, label]) => el("button", { type: "button", class: k === tab ? "on" : "", onclick: () => navigate(`/admin/${k}`) }, label)));
  const body = el("div");
  const signOut = el("button", { type: "button", class: "btn small", onclick: () => { saveToken(null); navigate("/admin"); } }, "Sign out");
  clear(main).append(el("div", { class: "page-head" }, el("h1", { text: "Admin" }), el("div", { style: "margin-left:auto" }, signOut)), tabs, body);
  const timers = [];
  const ctx = { body, timers, params: parts.slice(1), query };
  try {
    if (tab === "jobs") await renderJobs(ctx);
    else if (tab === "backfill") await renderBackfill(ctx);
    else if (tab === "scheduler") await renderScheduler(ctx);
    else if (tab === "settings") await renderSettings(ctx);
    else if (tab === "logs") await renderLogs(ctx);
    else clear(body).append(el("div", { class: "empty", text: "No such tab." }));
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) {
      saveToken(null);
      clear(body).append(notice("That token was refused. Sign in again.", "error"));
      body.append(loginForm());
    } else {
      clear(body).append(notice(e.message, "error"));
    }
  }
  return { destroy: () => timers.forEach(clearInterval) };
}

function renderLogin(main) {
  clear(main).append(el("div", { class: "page-head" }, el("h1", { text: "Admin" })), card("Sign in", el("div", {}, el("p", { class: "muted", text: "The admin area uses the API token (MURMUR_API_TOKEN on the server). It is kept in this tab's session storage only." }), loginForm())));
}

function loginForm() {
  const input = el("input", { type: "password", placeholder: "API token", autocomplete: "current-password", required: true, style: "min-width:260px" });
  const msg = el("div", { class: "help" });
  const form = el("form", { class: "actions", onsubmit: async (ev) => {
    ev.preventDefault();
    const token = input.value.trim();
    if (!token) return;
    saveToken(token);
    try {
      await api("jobs/types", { auth: true });
      // a read never checks the token; prove it with a harmless write: resume when not paused is a no-op
      const st = await api("status");
      const paused = st.config.schedule.paused;
      await api(paused ? "scheduler/pause" : "scheduler/resume", { method: "POST", auth: true });
      navigate("/admin/jobs");
    } catch (e) {
      saveToken(null);
      msg.textContent = e.status === 401 ? "Token refused." : e.status === 503 ? "The server has no API token configured; writes are disabled." : e.message;
    }
  } }, field("Token", input), el("button", { class: "btn primary", type: "submit" }, "Sign in"), msg);
  return form;
}

// ---- jobs ------------------------------------------------------------------------

async function renderJobs(ctx) {
  const { body, timers } = ctx;
  const jobId = ctx.params[0] ? Number(ctx.params[0]) : null;
  if (jobId) return renderJob(ctx, jobId);
  const list = el("div");
  const quick = el(
    "div",
    { class: "actions" },
    el("button", { class: "btn", type: "button", onclick: () => run({ type: "run", params: {} }, "Full run for today queued") }, "Run today now"),
    el("button", { class: "btn", type: "button", onclick: () => run({ type: "analyze", params: { all: true } }, "Re-analysis of every day queued") }, "Re-analyse all"),
    el("button", { class: "btn", type: "button", onclick: () => run({ type: "export", params: { all: true } }, "Full export queued") }, "Rebuild published files"),
    el("button", { class: "btn", type: "button", onclick: () => run({ type: "backup", params: { keep: 7 } }, "Backup queued") }, "Backup store"),
    el("button", { class: "btn", type: "button", onclick: () => run({ type: "universe", params: { rebuild: true } }, "Index rebuild queued") }, "Rebuild this month's index"),
  );
  const msg = el("div", { class: "help" });
  async function run(body, ok) {
    try {
      const r = await api("jobs", { method: "POST", body, auth: true });
      msg.textContent = `${ok} (job ${r.id}).`;
      await draw();
    } catch (e) {
      msg.textContent = e.message;
    }
  }
  const filterSel = select([["", "All statuses"], "queued", "running", "done", "failed", "cancelled", "interrupted"], "", () => draw());
  async function draw() {
    const data = await api(`jobs${qs({ status: filterSel.value, limit: 50 })}`);
    clear(list).append(
      table(
        [
          { label: "#", num: true, value: (j) => el("a", { href: `/admin/jobs/${j.id}`, "data-link": true }, String(j.id)) },
          { label: "Type", value: (j) => el("span", {}, el("span", { class: "mono" }, j.type), j.params && j.params.kind ? el("span", { class: "faint" }, ` ${j.params.kind}`) : "") },
          { label: "Params", value: (j) => el("span", { class: "small mono muted" }, summarizeParams(j.params)) },
          { label: "Status", value: (j) => statusCell(j) },
          { label: "Trigger", key: "trigger" },
          { label: "Created", value: (j) => fmt.ago(j.created_at) },
          { label: "Took", num: true, value: (j) => (j.started_at && j.finished_at ? fmt.seconds((new Date(j.finished_at) - new Date(j.started_at)) / 1000) : "") },
          { label: "", value: (j) => (["queued", "running"].includes(j.status) ? el("button", { class: "btn small danger", type: "button", onclick: async (ev) => { ev.stopPropagation(); await api(`jobs/${j.id}`, { method: "DELETE", auth: true }).catch((e) => (msg.textContent = e.message)); draw(); } }, "Cancel") : "") },
        ],
        data.items || [],
        { compact: true, onRow: (j) => navigate(`/admin/jobs/${j.id}`), empty: "No jobs yet." },
      ),
    );
  }
  clear(body).append(card("Queue a job", el("div", {}, quick, msg), { sub: "One worker runs jobs one at a time; the daily run always goes first." }), card("Jobs", el("div", {}, el("div", { class: "filters" }, field("Status", filterSel)), list)));
  await draw();
  timers.push(setInterval(() => draw().catch(() => {}), 5000));
}

function summarizeParams(p) {
  if (!p) return "";
  const parts = [];
  for (const [k, v] of Object.entries(p)) {
    if (k === "trigger" || k === "attempt") continue;
    parts.push(`${k}=${Array.isArray(v) ? v.join(",") : v}`);
  }
  return parts.join(" ");
}

function statusCell(j) {
  const wrap = el("div", {}, badge(j.status, STATUS_KIND[j.status] || ""));
  if (j.status === "running" && j.progress) {
    const pr = j.progress;
    const pct = pr.total ? Math.round((100 * (pr.done || 0)) / pr.total) : null;
    wrap.append(el("div", { class: "help", text: `${pr.step}${pr.message ? `: ${pr.message}` : ""}${pct !== null ? ` (${pct}%)` : ""}` }));
    if (pct !== null) wrap.append(el("div", { class: "progress", style: "width:140px" }, el("span", { style: `width:${pct}%` })));
  }
  if (j.status === "failed" && j.error) wrap.append(el("div", { class: "help", text: j.error.slice(0, 120) }));
  return wrap;
}

async function renderJob(ctx, id) {
  const { body, timers } = ctx;
  const log = el("pre", { class: "log" });
  const meta = el("dl", { class: "kv" });
  const head = el("div", { class: "card-head" });
  let after = 0;
  async function draw() {
    const j = await api(`jobs/${id}`);
    clear(head).append(el("h2", {}, `Job ${id} · `, el("span", { class: "mono" }, j.type)), statusCell(j));
    clear(meta).append(
      ...[
        ["Params", summarizeParams(j.params) || "–"],
        ["Trigger", j.trigger],
        ["Created", fmt.time(j.created_at)],
        ["Started", fmt.time(j.started_at)],
        ["Finished", fmt.time(j.finished_at)],
        ["Result", j.result ? JSON.stringify(j.result).slice(0, 400) : "–"],
        ["Error", j.error || "–"],
      ].flatMap(([k, v]) => [el("dt", { text: k }), el("dd", { text: v })]),
    );
    const chunk = await api(`jobs/${id}/log${qs({ after })}`);
    if (chunk.text) {
      log.append(document.createTextNode(chunk.text));
      after = chunk.next;
      log.scrollTop = log.scrollHeight;
    }
    return j.status;
  }
  const back = el("a", { href: "/admin/jobs", "data-link": true }, "← all jobs");
  const cancel = el("button", { class: "btn small danger", type: "button", onclick: async () => { await api(`jobs/${id}`, { method: "DELETE", auth: true }).catch(() => {}); draw(); } }, "Cancel");
  clear(body).append(el("div", { class: "card" }, head, meta, el("div", { class: "actions" }, back, cancel)), card("Log", log));
  const status = await draw();
  if (["queued", "running", "cancelling"].includes(status)) {
    const t = setInterval(async () => {
      const s = await draw().catch(() => "failed");
      if (!["queued", "running", "cancelling"].includes(s)) clearInterval(t);
    }, 2000);
    timers.push(t);
  }
}

// ---- backfill ----------------------------------------------------------------------

async function renderBackfill({ body }) {
  const types = await api("jobs/types");
  const stages = types.backfill_stages || ["news", "prices", "sentiment", "analyze", "export"];
  const from = el("input", { type: "date", value: daysAgo(7), required: true });
  const to = el("input", { type: "date", value: today(), required: true });
  const boxes = stages.map((s) => el("label", { class: "check" }, el("input", { type: "checkbox", value: s, checked: true }), s));
  const regen = el("input", { type: "checkbox" });
  const msg = el("div", { class: "help" });
  const submit = el("button", { class: "btn primary", type: "submit" }, "Queue backfill");
  const form = el(
    "form",
    { onsubmit: async (ev) => {
      ev.preventDefault();
      const chosen = boxes.filter((b) => b.firstChild.checked).map((b) => b.firstChild.value);
      const params = { from: from.value, to: to.value, stages: chosen };
      let confirm = false;
      if (regen.checked) {
        if (!window.confirm(`Regenerate deletes the analysis rows for ${from.value} to ${to.value} and rebuilds those months' files. Continue?`)) return;
        params.regenerate = true;
        confirm = true;
      }
      submit.disabled = true;
      try {
        const r = await api("jobs", { method: "POST", body: { type: "backfill", params, confirm }, auth: true });
        msg.textContent = `Queued as job ${r.id}.`;
        navigate(`/admin/jobs/${r.id}`);
      } catch (e) {
        msg.textContent = e.message;
      } finally {
        submit.disabled = false;
      }
    } },
    el("div", { class: "form-grid" }, field("From", from), field("To", to)),
    el("div", { style: "margin-top:10px" }, el("div", { class: "help", style: "margin-bottom:4px" }, "Stages, in pipeline order"), el("div", { class: "actions", style: "margin-top:0" }, boxes)),
    el("label", { class: "check", style: "margin-top:10px" }, regen, el("span", {}, "Regenerate: delete the range's analysis first and rebuild its months' files (a clean slate instead of a merge)")),
    el("div", { class: "actions" }, submit, msg),
  );
  clear(body).append(
    card("Backfill a date range", form, { sub: "news needs a source with date ranges (finnhub, gdelt); prices come from the configured source; analyze recomputes every session in the range and the rolling results as of each" }),
    el("div", { class: "notice", text: "Headlines are never deleted and a re-fetched one is deduplicated, so a backfill is safe to repeat. Yahoo cannot backfill news: its feed only has the latest ~20 headlines per company." }),
  );
}

// ---- scheduler ---------------------------------------------------------------------

async function renderScheduler({ body, timers }) {
  const wrap = el("div");
  async function draw() {
    const st = await api("status");
    const sched = st.config.schedule;
    const worker = st.worker;
    const msg = el("div", { class: "help" });
    const act = async (path, label) => {
      try {
        await api(path, { method: "POST", auth: true });
        msg.textContent = label;
        await draw();
      } catch (e) {
        msg.textContent = e.message;
      }
    };
    clear(wrap).append(
      el(
        "div",
        { class: "grid cols-2" },
        card("Schedule", el("dl", { class: "kv" }, ...[
          ["Slots", sched.slots.map(([t, k]) => `${t} ${k}`).join(" · ")],
          ["Timezone", sched.timezone],
          ["State", sched.paused ? "paused" : "active"],
          ["Worker", worker.alive ? `alive, last tick ${fmt.ago(worker.last_tick)}` : `dead${worker.error ? `: ${worker.error}` : ""}`],
          ["Running job", st.running_job ? `#${st.running_job.id} ${st.running_job.type}` : "none"],
          ["Next queued", st.next_queued_job ? `#${st.next_queued_job.id} ${st.next_queued_job.type}${st.next_queued_job.not_before ? ` after ${fmt.time(st.next_queued_job.not_before)}` : ""}` : "none"],
          ["Last run", st.last_run ? `${st.last_run.date} ${st.last_run.kind || "full"} · ${st.last_run.ok ? "ok" : "failed"} · ${fmt.ago(st.last_run.finished_at)}` : "none"],
        ].flatMap(([k, v]) => [el("dt", { text: k }), el("dd", { text: v })]))),
        card("Actions", el("div", {}, el("div", { class: "actions" }, sched.paused ? el("button", { class: "btn primary", type: "button", onclick: () => act("scheduler/resume", "Scheduler resumed.") }, "Resume scheduler") : el("button", { class: "btn", type: "button", onclick: () => act("scheduler/pause", "Scheduler paused. Manual jobs still run.") }, "Pause scheduler"), el("button", { class: "btn", type: "button", onclick: () => act("scheduler/run-now", "Today's run queued.") }, "Run today now")), msg, el("p", { class: "help", style: "margin-top:10px" }, "Change the slot times under Settings → schedule. The time applies on the next tick; a split schedule needs analysis_time."))),
      ),
      card("Recent runs", await runsTable()),
    );
  }
  clear(body).append(wrap);
  await draw();
  timers.push(setInterval(() => draw().catch(() => {}), 10000));
}

async function runsTable() {
  const runs = await api("runs?limit=15");
  return table(
    [
      { label: "#", num: true, key: "id" },
      { label: "Day", key: "date" },
      { label: "Kind", value: (r) => r.kind || "full" },
      { label: "Trigger", key: "trigger" },
      { label: "Attempt", num: true, key: "attempt" },
      { label: "Result", value: (r) => (r.ok === null ? badge("running", "accent") : r.ok ? badge("ok", "good") : badge(`failed: ${(r.stages || []).filter((s) => !s.ok).map((s) => s.name).join(", ")}`, "critical")) },
      { label: "Stages", value: (r) => el("span", { class: "small muted" }, (r.stages || []).map((s) => `${s.name} ${fmt.seconds(s.seconds)}`).join(" · ")) },
      { label: "Finished", value: (r) => fmt.time(r.finished_at) },
    ],
    runs.items || [],
    { compact: true, empty: "No runs yet." },
  );
}

// ---- settings ---------------------------------------------------------------------

async function renderSettings({ body }) {
  const schema = (await api("settings/schema")).keys || [];
  const groups = {};
  for (const k of schema) {
    const g = k.path.includes(".") ? k.path.split(".")[0] : "general";
    (groups[g] = groups[g] || []).push(k);
  }
  const msg = el("div", { class: "help" });
  const controls = {};
  const sections = Object.entries(groups).map(([g, keys]) =>
    card(
      g,
      el(
        "div",
        { class: "form-grid" },
        keys.map((k) => {
          const ctl = control(k);
          if (ctl) controls[k.path] = { ctl, k };
          return el(
            "div",
            { class: "field" },
            el("span", {}, k.path.split(".").slice(-1)[0], k.source !== "default" ? el("span", { class: "faint" }, ` · from ${k.source}`) : "", k.restart_required ? el("span", { class: "faint" }, " · restart") : ""),
            ctl || el("input", { type: "text", value: k.current === null || k.current === undefined ? "" : String(k.current), disabled: true }),
            el("span", { class: "help", text: k.help || "" }),
          );
        }),
      ),
    ),
  );
  const save = el("button", { class: "btn primary", type: "submit" }, "Save changes");
  const form = el("form", { onsubmit: async (ev) => {
    ev.preventDefault();
    const patch = {};
    let n = 0;
    for (const [path, { ctl, k }] of Object.entries(controls)) {
      const v = readControl(ctl, k);
      if (JSON.stringify(v) === JSON.stringify(k.current)) continue;
      const parts = path.split(".");
      let node = patch;
      for (const p of parts.slice(0, -1)) node = node[p] = node[p] || {};
      node[parts.slice(-1)[0]] = v;
      n++;
    }
    if (!n) {
      msg.textContent = "Nothing changed.";
      return;
    }
    save.disabled = true;
    try {
      await api("settings", { method: "PUT", body: patch, auth: true });
      msg.textContent = `Saved ${n} setting${n > 1 ? "s" : ""}.`;
      navigate("/admin/settings", { replace: true });
    } catch (e) {
      msg.textContent = e.message;
    } finally {
      save.disabled = false;
    }
  } }, el("div", { class: "stack" }, ...sections), el("div", { class: "actions" }, save, msg));
  clear(body).append(el("div", { class: "notice", text: "Changes are written to settings.toml in the data directory, above the ops file and below the environment. Secrets and the data directory can only be set in the environment." }), form);
}

function control(k) {
  if (k.editable === false) return null;
  const cur = k.current;
  if (cur !== null && typeof cur === "object" && !Array.isArray(cur)) {
    const ta = el("textarea", { rows: 3, disabled: true, style: "font-family:var(--mono);font-size:.8rem" });
    ta.value = `${Object.keys(cur).length} entries (edit in the ops file)\n${JSON.stringify(cur).slice(0, 400)}`;
    return ta;
  }
  if (k.type === "boolean") return el("input", { type: "checkbox", checked: Boolean(cur) });
  if (k.type === "choice") return select((k.choices || []).map((c) => [c, String(c)]), cur, () => {});
  if (k.type === "list") {
    const wrap = el("div", { class: "actions", style: "margin-top:0" });
    for (const c of k.choices || []) wrap.append(el("label", { class: "check" }, el("input", { type: "checkbox", value: String(c), checked: Array.isArray(cur) && cur.includes(c) }), String(c)));
    wrap.dataset.list = "1";
    return wrap;
  }
  if (k.type === "integer" || k.type === "number") return el("input", { type: "number", value: cur ?? "", min: k.min ?? undefined, step: k.type === "integer" ? 1 : "any" });
  return el("input", { type: "text", value: cur ?? "", pattern: k.pattern ? k.pattern.replace(/^\^|\$$/g, "") : undefined });
}

function readControl(ctl, k) {
  if (ctl.disabled) return k.current;
  if (k.type === "boolean") return ctl.checked;
  if (k.type === "list") return [...ctl.querySelectorAll("input:checked")].map((i) => i.value);
  if (k.type === "integer") return ctl.value === "" ? null : Number(ctl.value);
  if (k.type === "number") return ctl.value === "" ? null : Number(ctl.value);
  if (k.type === "choice") return k.choices && typeof k.choices[0] === "number" ? Number(ctl.value) : ctl.value;
  return ctl.value;
}

// ---- logs -------------------------------------------------------------------------

async function renderLogs({ body, timers }) {
  const pre = el("pre", { class: "log", style: "max-height:70vh" });
  const lines = el("input", { type: "number", value: 300, min: 20, max: 2000, style: "width:90px" });
  async function draw() {
    const data = await api(`logs${qs({ lines: lines.value })}`);
    pre.textContent = (data.lines || []).join("\n");
    pre.scrollTop = pre.scrollHeight;
  }
  lines.addEventListener("change", draw);
  clear(body).append(card("Process log", el("div", {}, el("div", { class: "filters" }, field("Lines", lines)), pre), { sub: "the in-memory ring buffer of the server process; job logs live on each job" }));
  await draw();
  timers.push(setInterval(() => draw().catch(() => {}), 5000));
}
