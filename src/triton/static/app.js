// Triton front end: projects, schema-driven setup forms and the workbook check.
import { GAP_WHY, View3D, directionArrows, heat } from "./view3d.js";
import { crackPicturesHtml, mountCrackPictures } from "./cracks.js";
import { spwCard } from "./spw.js";
import { renderTrials } from "./trials.js";
import { renderValueEngineering } from "./ve.js";
import { renderClashes } from "./clashes.js";
import { APPROACH, approachCard, approachPanel } from "./approach.js";
import { renderFurniture } from "./furniture.js";
import { renderMovedPiles } from "./moved.js";

const $app = document.getElementById("app");
// Where Triton is served: "" at the site root, or e.g. "/triton" when mounted inside another site.
const ROOT = new URL(".", location.href.split("#")[0]).pathname.replace(/\/$/, "");
const esc = (s) =>
  String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const pretty = (s) => String(s).replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: opts.body && !(opts.body instanceof FormData) ? { "Content-Type": "application/json" } : {},
    ...opts,
  });
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const busy = GATEWAY.includes(res.status) && typeof data.detail !== "string";
    const err = new Error(
      busy
        ? `The server did not answer (${res.status} ${res.statusText}). It may have been restarting; try again.`
        : typeof data.detail === "string" ? data.detail : res.statusText,
    );
    err.detail = data.detail;
    err.status = res.status;
    throw err;
  }
  return data;
}

// A host answers these when a request was cut off or the site was restarting (a Reload).
const GATEWAY = [502, 503, 504];

// Asks again, a few times, when a request was cut off rather than refused.
async function again(fn, tries = 4) {
  for (let i = 1; ; i++) {
    try {
      return await fn();
    } catch (e) {
      const cutOff = GATEWAY.includes(e.status) || e instanceof TypeError; // TypeError: no answer at all
      if (!cutOff || i >= tries) throw e;
      await new Promise((r) => setTimeout(r, 2000 * i));
    }
  }
}

// Uploads and designs under way, whatever page is open (see "long work" below).
const JOBS = [];
// Sections whose workbook was opened on this visit: their Workbook tab opens it straight away.
const OPENED = new Set();
let state = null; // the open project: { project, sectionId, dirty, errors }

let SCHEMA = null;
let MATERIALS = null;
async function reference() {
  if (!SCHEMA) [SCHEMA, MATERIALS] = await Promise.all([api(ROOT + "/api/schema/project"), api(ROOT + "/api/materials")]);
}
const resolve = (node) => (node && node.$ref ? SCHEMA.$defs[node.$ref.split("/").pop()] : node);

// ---------------------------------------------------------------- routing
window.addEventListener("hashchange", route);
route();

function route() {
  if (state?.dirty) save(); // what was typed on the last tab
  const hash = location.hash.slice(1) || "/";
  const m = hash.match(/^\/project\/([a-f0-9]+)(?:\/(\w+))?(?:\/([a-f0-9]+))?$/);
  if (m) return projectPage(m[1], m[2] || "info", m[3]);
  if (hash === "/check") return checkPage();
  return projectsPage();
}

// ---------------------------------------------------------------- projects list
async function projectsPage() {
  $app.innerHTML = `<h1>Projects</h1><p class="sub">Each project holds the sections, materials and design
    settings used to design the elements in a Plaxis workbook.</p>
    <div class="row"><input id="new-name" placeholder="Project name" style="flex:1;max-width:320px;padding:7px 9px;border:1px solid var(--line);border-radius:7px;background:var(--input);color:var(--text);font:inherit">
    <button id="new">New project</button></div>
    <div class="row" style="margin-top:10px"><label for="trt">Open a project file</label>
      <input type="file" id="trt" accept=".trt"><button id="open-trt" disabled>Open</button>
      <span class="status" id="trt-status">A .trt downloaded from Triton (Project page › Download project).</span></div>
    <div class="row" id="trt-choice" hidden></div>
    <h2>Saved projects</h2><div class="panel scroll" id="list">Loading…</div>`;
  wireOpenProject();
  document.getElementById("new").onclick = async () => {
    const name = document.getElementById("new-name").value.trim() || "New project";
    const p = await api(ROOT + "/api/projects", { method: "POST", body: JSON.stringify({ info: { name } }) });
    location.hash = `#/project/${p.id}/info`;
  };
  const list = await api(ROOT + "/api/projects");
  const el = document.getElementById("list");
  if (!list.length) {
    el.innerHTML = `<p class="empty">No projects yet.</p>`;
    return;
  }
  el.innerHTML =
    `<table><tr><th>Name</th><th>Number</th><th>Sections</th><th>Elements</th><th>Last saved (Cairo)</th></tr>` +
    list
      .map(
        (p) => `<tr class="link" data-id="${esc(p.id)}"><td>${esc(p.name)}</td><td>${esc(p.number)}</td>
        <td>${p.sections}</td><td>${p.elements}</td><td>${esc(when(p.updated_at))}</td></tr>`
      )
      .join("") +
    `</table>`;
  el.querySelectorAll("tr.link").forEach((tr) => (tr.onclick = () => (location.hash = `#/project/${tr.dataset.id}/info`)));
}

// Open a .trt as a project on the server: it goes up in pieces like a workbook. When a project
// already has its name, the choice is to replace that project or keep both.
function wireOpenProject() {
  const file = document.getElementById("trt");
  const btn = document.getElementById("open-trt");
  const status = document.getElementById("trt-status");
  const choice = document.getElementById("trt-choice");
  file.onchange = () => (btn.disabled = !file.files.length);
  const open = async (id, body) => {
    status.textContent = "Opening the project and checking its workbooks…";
    const r = await api(`${ROOT}/api/projects/open/${id}`, { method: "POST", body: JSON.stringify(body) });
    if (r.exists) {
      const p = r.exists[0];
      status.textContent = "";
      choice.hidden = false;
      choice.innerHTML = `<span>A project named <strong>${esc(r.name)}</strong> is already here (last saved ${esc(when(p.updated_at))}).</span>
        <button id="trt-keep">Keep both</button>
        <button class="danger" id="trt-replace">Replace it</button>
        <button class="quiet" id="trt-cancel">Cancel</button>`;
      document.getElementById("trt-keep").onclick = () => finish(id, { if_exists: "keep" });
      document.getElementById("trt-replace").onclick = () => {
        if (!confirm(`Replace "${r.name}" with the project in the file? The one here is deleted, with its workbooks and results.`)) return;
        finish(id, { if_exists: "replace", replace_id: p.id });
      };
      document.getElementById("trt-cancel").onclick = async () => {
        choice.hidden = true;
        btn.disabled = false;
        status.textContent = "Not opened.";
        await api(`${ROOT}/api/uploads/${id}`, { method: "DELETE" }).catch(() => {});
      };
      return;
    }
    if (r.notes?.length) alert(r.notes.join("\n\n"));
    location.hash = `#/project/${r.id}/info`;
  };
  const finish = async (id, body) => {
    choice.hidden = true;
    try {
      await open(id, body);
    } catch (e) {
      status.textContent = `Not opened: ${e.message}`;
      btn.disabled = false;
    }
  };
  btn.onclick = async () => {
    const f = file.files[0];
    if (!f) return;
    btn.disabled = true;
    try {
      const mb = (n) => (n / 1048576).toFixed(0);
      const id = await sendInPieces(f, (at) => (status.textContent = `Uploading ${f.name}: ${mb(at)} of ${mb(f.size)} MB`));
      await open(id, { if_exists: "ask" });
    } catch (e) {
      status.textContent = `Not opened: ${e.message}`;
      btn.disabled = false;
    }
  };
}

// The checker's status per designed element: designed, returned with comments, checked, approved.
const CHECK_LABEL = { designed: "Designed", comments: "Comments", checked: "Checked", approved: "Approved" };
function checkingPanel(names, runAt) {
  let box = document.getElementById("checking");
  if (!box) {
    box = document.createElement("details");
    box.id = "checking";
    box.className = "panel";
    document.getElementById("export-pick")?.closest(".panel")?.after(box);
  }
  const s = sec();
  const checks = s.checks || {};
  const done = names.filter((n) => ["checked", "approved"].includes(checks[n]?.status) && checks[n]?.design_run_at === runAt).length;
  const open = box.open;
  box.innerHTML = `<summary><strong>Checking</strong> <span class="status">${done} of ${names.length} checked or approved on this design</span></summary>
    <table class="check-table"><tr><th>Element</th><th>Status</th><th>By</th><th>Comment</th><th></th></tr>
    ${names
      .map((n) => {
        const c = checks[n] || {};
        const earlier = c.status && c.status !== "designed" && c.design_run_at && c.design_run_at !== runAt;
        return `<tr data-el="${esc(n)}"><td>${esc(n)}</td>
          <td><select data-f="status">${Object.entries(CHECK_LABEL)
            .map(([k, t]) => `<option value="${k}" ${(c.status || "designed") === k ? "selected" : ""}>${t}</option>`)
            .join("")}</select>${earlier ? ' <span class="chip small-chip stale-chip" title="Given on an earlier design">earlier design</span>' : ""}</td>
          <td><input data-f="by" value="${esc(c.by || state.project.info.checker || "")}" style="width:9em"></td>
          <td><input data-f="comment" value="${esc(c.comment || "")}" style="width:100%;min-width:12em"></td>
          <td><button class="quiet small" data-save>Save</button> <span class="status">${c.at ? esc(when(c.at)) : ""}</span></td></tr>`;
      })
      .join("")}</table>`;
  box.open = open;
  box.querySelectorAll("[data-save]").forEach(
    (b) =>
      (b.onclick = async () => {
        const tr = b.closest("tr");
        const body = Object.fromEntries([...tr.querySelectorAll("[data-f]")].map((i) => [i.dataset.f, i.value]));
        b.disabled = true;
        try {
          const saved = await api(`${secUrl()}/checks/${encodeURIComponent(tr.dataset.el)}`, { method: "PUT", body: JSON.stringify(body) });
          const mine = saved.sections.find((x) => x.id === s.id);
          s.checks = mine.checks;
          checkingPanel(names, runAt);
          document.getElementById("checking").open = true;
        } catch (e) {
          b.disabled = false;
          b.nextElementSibling.textContent = e.message;
        }
      })
  );
}

// Displacements come by email from the geotechnical team: typed in per section with their limits.
function displacementsPanel(box) {
  const s = sec();
  s.displacements ??= [];
  const rows = s.displacements;
  const fails = rows.filter((d) => d.limit != null && Math.abs(d.value) > d.limit).length;
  const state_ = (d) =>
    d.limit == null ? `<span class="status">No limit</span>` : Math.abs(d.value) <= d.limit ? `<span class="chip ok-chip">OK</span>` : `<span class="chip fail-chip">Not OK</span>`;
  box.innerHTML = `<h3>Displacements <span class="status">${
    rows.length ? (fails ? `${fails} over the limit` : "all within their limits") : "as received from the geotechnical team"
  }</span></h3>
    ${
      rows.length
        ? `<table class="disp-table"><tr><th>What</th><th>Combination or phase</th><th>Displacement (mm)</th><th>Limit (mm)</th><th>Check</th><th>Source</th><th></th></tr>
      ${rows
        .map(
          (d, i) => `<tr data-i="${i}"><td><input data-f="what" value="${esc(d.what)}" placeholder="Front beam, horizontal"></td>
          <td><input data-f="combination" value="${esc(d.combination)}" placeholder="SLS"></td>
          <td><input data-f="value" type="number" step="any" value="${d.value ?? ""}" style="width:7em"></td>
          <td><input data-f="limit" type="number" step="any" min="0" value="${d.limit ?? ""}" style="width:7em"></td>
          <td>${state_(d)}</td>
          <td><input data-f="source" value="${esc(d.source)}" placeholder="Email of 24 Sep"></td>
          <td><button class="quiet small" data-del title="Remove this row">✕</button></td></tr>`
        )
        .join("")}</table>`
        : ""
    }
    <button class="quiet small" id="disp-add">Add a displacement</button>`;
  box.querySelector("#disp-add").onclick = () => {
    rows.push({ what: "", value: 0, limit: null, combination: "", source: "" });
    markDirty();
    displacementsPanel(box);
    box.querySelector('tr:last-child input[data-f="what"]')?.focus();
  };
  box.querySelectorAll("tr[data-i]").forEach((tr) => {
    const d = rows[+tr.dataset.i];
    tr.querySelectorAll("[data-f]").forEach(
      (inp) =>
        (inp.onchange = () => {
          const f = inp.dataset.f;
          if (f === "value") d.value = inp.value === "" ? 0 : +inp.value;
          else if (f === "limit") d.limit = inp.value === "" ? null : +inp.value;
          else d[f] = inp.value;
          markDirty();
          displacementsPanel(box);
        })
    );
    tr.querySelector("[data-del]").onclick = () => {
      rows.splice(+tr.dataset.i, 1);
      markDirty();
      displacementsPanel(box);
    };
  });
}

// Displacements estimated from the straining actions (no Plaxis displacement run): worked out by the
// server from the stored workbook with the section's settings, kept apart from the typed-in ones.
function deflectionsPanel(box) {
  const s = sec();
  s.deflection ??= { toe: "fixed", firm_soil_level: null, stiffness: "gross", long_term: false, combination: "" };
  const ds = s.deflection;
  box.innerHTML = `<h3>Estimated displacements <span class="chip small-chip stale-chip">Estimate</span>
    <span class="status">from the straining actions (M / EI integrated twice along each member), not a Plaxis displacement result</span></h3>
    <div class="row defl-settings">
      <label>Piles and walls at the toe <select data-d="toe">
        <option value="fixed">Fixed: no displacement, no rotation</option>
        <option value="firm_soil">Held at the toe and at the firm soil level</option></select></label>
      <label>Firm soil level (piles) <input data-d="firm_soil_level" type="number" step="any" style="width:6em" placeholder="none"> m</label>
      <label>Stiffness <select data-d="stiffness">
        <option value="gross">Gross (uncracked)</option>
        <option value="cracked">Cracked (EC2 7.4.3; combi infill 0.6 EcIc)</option></select></label>
      <label><input type="checkbox" data-d="long_term"> Long term (Ecm / (1 + φ))</label>
      <label>Combination <input data-d="combination" list="defl-combos" placeholder="QP, else governing" style="width:10em"></label>
      <datalist id="defl-combos">${(s.combinations || []).map((c) => `<option value="${esc(c)}">`).join("")}</datalist>
    </div>
    <div data-defl-out><p class="status">Working out the estimate…</p></div>`;
  box.querySelectorAll("[data-d]").forEach((inp) => {
    const f = inp.dataset.d;
    if (inp.type === "checkbox") inp.checked = !!ds[f];
    else inp.value = ds[f] ?? "";
    inp.onchange = async () => {
      if (f === "long_term") ds[f] = inp.checked;
      else if (f === "firm_soil_level") ds[f] = inp.value === "" ? null : +inp.value;
      else ds[f] = inp.value;
      markDirty();
      await save();
      drawDeflections(box);
    };
  });
  drawDeflections(box);
}

async function drawDeflections(box) {
  const out = box.querySelector("[data-defl-out]");
  if (!out) return;
  let est;
  try {
    est = await api(`${secUrl()}/deflections`);
  } catch (e) {
    out.innerHTML = `<p class="status">${esc(e.message)}</p>`;
    return;
  }
  if (!document.body.contains(out)) return;
  const els = est.elements || [];
  const mm = (v) => (v == null ? "–" : `${fmt(v, 1)}`);
  out.innerHTML = `${els.length
    ? `<div class="scroll"><table><tr><th>Element</th><th>Head / top (mm)</th><th>Largest (mm)</th><th>At</th><th>Direction of the largest</th><th>Combination</th><th>How</th></tr>
      ${els.map((e) => `<tr><td>${esc(e.element)}</td><td>${mm(e.head_mm)}</td><td><b>${mm(e.max_mm)}</b></td>
        <td>${e.axis === "level" ? `level ${fmt(e.max_at, 1)} m` : `${fmt(e.max_at, 1)} m along`}</td><td>${esc(e.max_direction)}</td>
        <td title="${esc(e.combination_note)}">${esc(e.combination)}</td>
        <td><details><summary class="status">Assumptions</summary><p class="status">${esc(e.at)}.<br>${esc(e.boundary)}<br>${esc(e.stiffness)}<br>Combination: ${esc(e.combination_note)}.${(e.notes || []).map((n) => `<br>${esc(n)}`).join("")}</p></details></td></tr>`).join("")}</table></div>
      <div class="charts">${els.map((_, i) => `<div class="chart" data-defl="${i}"></div>`).join("")}</div>`
    : `<p class="status">No element to estimate.</p>`}
    ${(est.skipped || []).length ? `<p class="status">${est.skipped.map(esc).join("<br>")}</p>` : ""}
    <p class="status">${esc(est.note || "")} Signs follow each member's local axes; the size and the shape are the estimate.
      It leaves out the soil springs, the toe moving in the ground and second-order effects. Compare it with the displacements received above; it does not replace them.</p>`;
  els.forEach((e, i) => deflectionChart(out.querySelector(`[data-defl="${i}"]`), e));
}

// One element's estimated deflected shape: displacement across and level up (piles and walls), or
// position across and vertical displacement up (slab and beam strips). One line per direction.
function deflectionChart(el, e) {
  const curves = (e.directions || []).filter((c) => c.stations?.length);
  if (!el || !curves.length) return;
  const level = e.axis === "level";
  const pos = curves.flatMap((c) => c.stations.map((q) => q[0]));
  const ws = curves.flatMap((c) => c.stations.map((q) => q[1])).concat([0]);
  const pad = Math.max((Math.max(...ws) - Math.min(...ws)) * 0.1, 0.5);
  const wDom = [Math.min(...ws) - pad, Math.max(...ws) + pad];
  const sDom = [Math.min(...pos), Math.max(...pos)];
  const title = `${e.element}: estimated deflected shape (${e.combination})`;
  const c = level
    ? frame(el, { xDomain: wDom, yDomain: sDom, xLabel: "Displacement (mm), estimate", yLabel: "Level z (m)", title })
    : frame(el, { xDomain: sDom, yDomain: wDom, xLabel: "Position (m)", yLabel: "Vertical (mm, up +), estimate", title });
  const at = (s, w) => (level ? [c.x(w), c.y(s)] : [c.x(s), c.y(w)]);
  const zero = level
    ? `<line class="grid" x1="${c.x(0)}" x2="${c.x(0)}" y1="${c.m.t}" y2="${c.h - c.m.b}" stroke-dasharray="4 3"/>`
    : `<line class="grid" x1="${c.m.l}" x2="${c.w - c.m.r}" y1="${c.y(0)}" y2="${c.y(0)}" stroke-dasharray="4 3"/>`;
  const paths = curves
    .map((cv, k) => `<path class="series${k ? " field" : ""}" d="${cv.stations.map((q, j) => `${j ? "L" : "M"}${at(q[0], q[1]).map((v) => v.toFixed(1)).join(",")}`).join("")}"/>`)
    .join("");
  const key = curves
    .map((cv, k) => `<text class="label" x="${c.m.l + 6}" y="${c.m.t + 14 + 14 * k}" style="fill:var(--series-${k + 1})">— ${esc(cv.label)}</text>`)
    .join("");
  c.g.innerHTML = zero + paths + key;
  c.svg.onmousemove = (evt) => {
    const r = c.svg.getBoundingClientRect();
    const sx = ((evt.clientX - r.left) / r.width) * c.w;
    const sy = ((evt.clientY - r.top) / r.height) * c.h;
    const q0 = curves[0].stations;
    let best = 0;
    q0.forEach((q, j) => {
      const d = level ? Math.abs(c.y(q[0]) - sy) : Math.abs(c.x(q[0]) - sx);
      const b = level ? Math.abs(c.y(q0[best][0]) - sy) : Math.abs(c.x(q0[best][0]) - sx);
      if (d < b) best = j;
    });
    const s = q0[best][0];
    const vals = curves.map((cv) => {
      const q = cv.stations.reduce((a, b) => (Math.abs(b[0] - s) < Math.abs(a[0] - s) ? b : a));
      return `${esc(cv.label)}: ${fmt(q[1], 1)} mm`;
    });
    c.g.querySelector(".hover")?.remove();
    const [hx, hy] = at(s, q0[best][1]);
    c.g.insertAdjacentHTML("beforeend", `<circle class="hover" cx="${hx}" cy="${hy}" r="4"/>`);
    showTip(c, evt, `${level ? "z" : "at"} ${fmt(s, 2)} m<br>${vals.join("<br>")}`);
  };
  c.svg.onmouseleave = () => {
    c.tip.hidden = true;
    c.g.querySelector(".hover")?.remove();
  };
}

// ---------------------------------------------------------------- project page

// Elements, workbook, load multipliers and design results belong to one section of the project.
const SECTION_TABS = new Set(["elements", "workbook", "design", "openings", "view3d", "clashes", "furniture", "moved", "compare", "ve"]);
const sec = () => state.project.sections.find((s) => s.id === state.sectionId) || state.project.sections[0];
const secIndex = () => state.project.sections.indexOf(sec());
const secUrl = () => `${ROOT}/api/projects/${state.project.id}/sections/${sec().id}`;
const tabHash = (tab) => `#/project/${state.project.id}/${tab}` + (SECTION_TABS.has(tab) ? `/${sec().id}` : "");

async function projectPage(id, tab, sectionId) {
  await reference();
  if (!state || state.project.id !== id) {
    try {
      state = { project: await api(`${ROOT}/api/projects/${id}`), dirty: false, errors: [] };
    } catch (e) {
      $app.innerHTML = `<p>${esc(e.message)} <a href="#/">Back to projects</a></p>`;
      return;
    }
  }
  const p = state.project;
  if (sectionId && p.sections.some((s) => s.id === sectionId)) state.sectionId = sectionId;
  if (!p.sections.some((s) => s.id === state.sectionId)) state.sectionId = p.sections[0].id;
  const tabs = [
    ["info", "Project"],
    ["settings", "Design settings"],
    ["sections", `Sections (${p.sections.length})`],
    ["elements", `Elements (${Object.keys(sec().elements).length})`],
    ["workbook", "Workbook"],
    ["design", "Design"],
    ["openings", "Openings"],
    ["view3d", "3D view"],
    ["clashes", "Clashes"],
    ["furniture", "Furniture"],
    ["moved", "Moved piles"],
    ["costing", "Costing"],
    ["compare", "Comparisons"],
    ["ve", "Value engineering"],
    ["method", "Method"],
  ];
  const picker = SECTION_TABS.has(tab)
    ? `<div class="row section-pick"><label for="section-pick">Section</label>
        <select id="section-pick">${p.sections
          .map((s) => `<option value="${esc(s.id)}" ${s.id === sec().id ? "selected" : ""}>${esc(s.name)}</option>`)
          .join("")}</select>
        <span class="status">Elements, workbook, load multipliers and results below are for this section.</span></div>`
    : "";
  $app.innerHTML = `<h1>${esc(p.info.name)}</h1>
    <p class="sub">${esc([p.info.number, p.sections.length > 1 ? `${p.sections.length} sections` : sec().name].filter(Boolean).join(" · "))}</p>
    <div class="tabs">${tabs.map(([k, t]) => `<button data-tab="${k}" class="${k === tab ? "on" : ""}">${t}</button>`).join("")}</div>
    <div id="lockbar"></div>
    ${picker}<div id="tab"></div>
    <div class="savebar"><span class="save-state" id="save-status"></span>
      <span style="flex:1"></span>
      <a class="quiet-link" id="download-project" href="${ROOT}/api/projects/${esc(p.id)}/project.trt"
        title="Settings, sections, workbooks, results and trials in one file, to send to someone or keep">Download project (.trt)</a>
      <button class="quiet" id="duplicate" title="A new project with all of this one's settings, sections, workbooks, results and trials">Duplicate project</button>
      <button class="danger" id="delete">Delete project</button></div>
    <ul class="errors" id="errors"></ul>`;
  $app.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => (location.hash = tabHash(b.dataset.tab))));
  const pick = document.getElementById("section-pick");
  if (pick)
    pick.onchange = () => {
      state.sectionId = pick.value;
      location.hash = tabHash(tab);
    };
  document.getElementById("delete").onclick = async () => {
    if (!confirm(`Delete "${p.info.name}"? This cannot be undone.`)) return;
    await api(`${ROOT}/api/projects/${id}`, { method: "DELETE" });
    state = null;
    location.hash = "#/";
  };
  document.getElementById("duplicate").onclick = async () => {
    const name = prompt("Name of the copy", `${p.info.name} copy`);
    if (name === null) return;
    if (state.dirty) await save(); // what was just typed goes into the copy too
    const copy = await api(`${ROOT}/api/projects/${id}/duplicate`, { method: "POST", body: JSON.stringify({ name }) });
    state = null;
    location.hash = `#/project/${copy.id}/info`;
  };
  document.getElementById("download-project").onclick = async (e) => {
    if (!state.dirty) return;
    e.preventDefault(); // what was just typed goes into the file too
    await save();
    location.href = e.target.href;
  };
  const host = document.getElementById("tab");
  if (tab === "info") {
    const info = renderObject(SCHEMA.properties.info, p.info, "info", "Project");
    info.dataset.free = ""; // names and numbers, not design inputs: open to edit at any time
    host.append(info);
    const prices = renderObject(SCHEMA.properties.prices, p.prices, "prices", "Prices (for the Costing tab)");
    prices.dataset.free = ""; // not a design input: open while the model is locked
    host.append(prices);
    const names = renderObject(SCHEMA.properties.drawings, p.drawings, "drawings", "Drawing names (AutoCAD layers, Revit line styles and family types)");
    names.dataset.free = ""; // not a design input either
    host.append(names);
    const used = document.createElement("div");
    used.className = "panel";
    used.dataset.free = "";
    used.innerHTML = '<h2>Storage</h2><p class="status">Working out…</p>';
    host.append(used);
    storagePanel(used, id);
  }
  else if (tab === "settings") host.append(renderObject(SCHEMA.properties.design, p.design, "design", "Design settings"));
  else if (tab === "design") renderDesignTab(host);
  else if (tab === "openings") renderOpeningsTab(host);
  else if (tab === "sections") renderSections(host);
  else if (tab === "elements") renderElements(host);
  else if (tab === "workbook") renderWorkbookTab(host);
  else if (tab === "view3d") renderView3dTab(host);
  else if (tab === "costing") renderCostingTab(host);
  else if (tab === "method") renderMethodTab(host);
  else if (tab === "ve")
    renderValueEngineering(host, {
      api, again, esc, fmt, secUrl, ROOT,
      project: () => state.project,
      sectionId: () => sec().id,
      costingHash: tabHash("costing"),
    });
  else if (tab === "clashes") renderClashes(host, { api, again, esc, fmt, secUrl });
  else if (tab === "moved")
    renderMovedPiles(host, {
      api, again, esc, fmt, secUrl, ROOT,
      project: () => state.project,
      sectionId: () => sec().id,
    });
  else if (tab === "furniture")
    renderFurniture(host, {
      api, again, esc, fmt, secUrl, save,
      forms: () => [
        renderObject(SCHEMA.properties.furniture, p.furniture, "furniture", ""),
        renderObject(SCHEMA.$defs.Section.properties.furniture, sec().furniture, `sections.${secIndex()}.furniture`, ""),
      ],
    });
  else if (tab === "compare")
    renderTrials(host, {
      api, again, esc, fmt, secUrl, ROOT,
      project: () => state.project,
      sectionId: () => sec().id,
      costingHash: tabHash("costing"),
      used: (res) => {
        mergeInto(state.project, res.project);
        state.dirty = false;
        applyLock();
      },
    });
  showSaveState();
  showErrors();
  applyLock();
  drawJobs();
}

// Space each section takes on the server, so it is clear where it goes.
async function storagePanel(box, id) {
  let d;
  try {
    d = await api(`${ROOT}/api/projects/${id}/storage`);
  } catch (e) {
    box.innerHTML = `<h2>Storage</h2><p class="status">${esc(e.message)}</p>`;
    return;
  }
  const mb = (b) => (b >= 1e6 ? `${fmt(b / 1e6, 1)} MB` : b ? `${fmt(b / 1e3, 0)} kB` : "—");
  box.innerHTML = `<h2>Storage</h2>
    <p class="status" style="margin-top:0">This project uses <strong>${mb(d.total)}</strong>. The workbook is kept compressed, with its rows as uploaded
      for editing; results are deleted when the inputs they came from change, and deleting a section or the project frees all of it.
      Upload leftovers and temporary files are cleared on their own.</p>
    <div class="scroll"><table class="factor-sheets"><thead><tr><th>Section</th><th class="num">Workbook</th><th class="num">Rows kept for editing</th>
      <th class="num">Design results</th><th class="num">Total</th></tr></thead><tbody>
      ${d.sections.map((x) => `<tr><td>${esc(x.name)}</td><td class="num">${mb(x.workbook)}</td><td class="num">${mb(x.rows)}</td>
        <td class="num">${mb(x.results)}</td><td class="num"><strong>${mb(x.total)}</strong></td></tr>`).join("")}
    </tbody></table></div>`;
}

// ---------------------------------------------------------------- lock
// Designing locks the model: its inputs are read only (prices and costing inputs stay open) until
// Unlock to edit. Changes after unlocking mark the results out of date.
const LOCKED_TABS = new Set(["info", "settings", "sections", "elements", "workbook"]);

function applyLock() {
  const bar = document.getElementById("lockbar");
  const host = document.getElementById("tab");
  if (!bar || !host || !state) return;
  const locked = state.project.locked;
  bar.innerHTML = locked
    ? `<div class="lockbar"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 7V5a4 4 0 0 1 8 0v2h.5A1.5 1.5 0 0 1 14 8.5v5a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 2 13.5v-5A1.5 1.5 0 0 1 3.5 7H4Zm2 0h4V5a2 2 0 0 0-4 0v2Z"/></svg>
        <span><strong>Locked since the design.</strong> The inputs are read only, so the results match them. Prices and costing inputs can still change.</span>
        <button id="unlock">Unlock to edit</button></div>`
    : "";
  const unlock = document.getElementById("unlock");
  if (unlock)
    unlock.onclick = async () => {
      const ok = confirm(
        "Unlock to edit?\n\nAs you change an input, the design results it affects are deleted (a shared input such as a " +
          "design setting or the workbook deletes the whole section's), so no out-of-date results are kept. Results of " +
          "elements you don't touch stay. Files you already downloaded are not affected.",
      );
      if (!ok) return;
      state.project.locked = false;
      state.dirty = true;
      await save();
      route();
    };
  host._lockWatch?.disconnect();
  const tab = (location.hash.match(/^#\/project\/[a-f0-9]+\/(\w+)/) || [])[1] || "info";
  if (!locked || !LOCKED_TABS.has(tab)) return;
  const lock = () =>
    host.querySelectorAll("input, select, textarea, button").forEach((el) => {
      if (!el.disabled && !el.closest("[data-free], [data-slot]")) el.disabled = true;
    });
  lock();
  // Parts of a tab arrive later (the workbook's report): lock them as they come.
  host._lockWatch = new MutationObserver(lock);
  host._lockWatch.observe(host, { childList: true, subtree: true });
}

// Every change saves itself a moment later; the bar at the bottom says whether it has.
let saveTimer = null;
let saving = null;
function markDirty() {
  state.dirty = true;
  state.edits = (state.edits || 0) + 1;
  showSaveState();
  clearTimeout(saveTimer);
  saveTimer = setTimeout(save, 700);
}
function showSaveState(text) {
  const s = document.getElementById("save-status");
  if (!s) return;
  const bad = state.errors?.length;
  s.className = `save-state ${text === "Saving…" || (!bad && state.dirty) ? "saving" : bad ? "unsaved" : "saved"}`;
  s.textContent = text || (bad ? "Not saved: fix the fields below" : state.dirty ? "Saving…" : "All changes saved");
}

// Copies the saved project into the one the forms are bound to, keeping its objects.
function mergeInto(target, source) {
  for (const k of Object.keys(target)) if (!(k in source)) delete target[k];
  for (const [k, v] of Object.entries(source)) {
    const t = target[k];
    if (Array.isArray(v) && Array.isArray(t) && v.length === t.length) {
      v.forEach((x, i) => {
        if (x && typeof x === "object" && t[i] && typeof t[i] === "object" && !Array.isArray(x)) mergeInto(t[i], x);
        else t[i] = x;
      });
    } else if (v && typeof v === "object" && !Array.isArray(v) && t && typeof t === "object" && !Array.isArray(t)) mergeInto(t, v);
    else target[k] = v;
  }
}

async function save() {
  clearTimeout(saveTimer);
  if (saving) {
    await saving; // one save at a time; the next picks up what changed meanwhile
    if (!state.dirty) return;
  }
  saving = (async () => {
    const edits = state.edits || 0;
    const project = state.project;
    showSaveState("Saving…");
    try {
      const saved = await api(`${ROOT}/api/projects/${project.id}`, { method: "PUT", body: JSON.stringify(project) });
      if (state?.project !== project) return;
      state.errors = [];
      if ((state.edits || 0) === edits) {
        mergeInto(project, saved);
        state.dirty = false;
      } else project.updated_at = saved.updated_at; // changed again meanwhile: saved next
    } catch (e) {
      if (state?.project !== project) return;
      state.errors = Array.isArray(e.detail) ? e.detail : [{ loc: [], msg: e.message }];
    }
    showSaveState();
    showErrors();
    if (state.dirty && !state.errors.length) saveTimer = setTimeout(save, 700);
  })();
  try {
    await saving;
  } finally {
    saving = null;
  }
}

function showErrors() {
  document.querySelectorAll(".field.bad").forEach((f) => {
    f.classList.remove("bad");
    f.querySelector(".msg")?.remove();
  });
  const ul = document.getElementById("errors");
  if (!ul) return;
  ul.innerHTML = "";
  for (const err of state.errors || []) {
    // Drop "body" and the discriminator tag pydantic adds inside element unions.
    const loc = (err.loc || []).filter((x, i) => !(i === 0 && x === "body"));
    const clean = cleanPath(loc);
    const field = document.querySelector(`[data-path="${CSS.escape(clean)}"]`);
    const msg = err.msg.replace(/^Value error, /, "");
    if (field) {
      field.classList.add("bad");
      field.insertAdjacentHTML("beforeend", `<div class="msg">${esc(msg)}</div>`);
    }
    ul.insertAdjacentHTML("beforeend", `<li>${esc(clean || "Project")}: ${esc(msg)}</li>`);
  }
}
function cleanPath(loc) {
  // ["sections", 0, "elements", "Pile(1)", "pile", "casing", "thickness"] -> "sections.0.elements.Pile(1).casing.thickness"
  const out = [];
  loc.forEach((x, i) => {
    if (i >= 2 && loc[i - 2] === "elements" && typeof x === "string" && /^[a-z_]+$/.test(x)) return;
    out.push(x);
  });
  return out.join(".");
}

// ---------------------------------------------------------------- schema-driven form
const HIDDEN = new Set(["kind", "id", "created_at", "updated_at"]);

function defaultsFor(schema) {
  schema = resolve(schema);
  const out = {};
  for (const [k, prop] of Object.entries(schema.properties || {})) {
    if ("default" in prop) out[k] = structuredClone(prop.default);
    else if (resolve(prop).properties) out[k] = defaultsFor(prop);
  }
  return out;
}

function renderObject(schema, obj, path, title) {
  schema = resolve(schema);
  const fs = document.createElement("fieldset");
  const legend = title ?? schema.title;
  if (legend) fs.innerHTML = `<legend>${esc(legend)}</legend>`;
  const grid = document.createElement("div");
  grid.className = "fields";
  fs.append(grid);
  const conditional = [];
  for (const [k, prop] of Object.entries(schema.properties || {})) {
    if (HIDDEN.has(k) || prop.hidden) continue;
    const p = path ? `${path}.${k}` : k;
    const nullable = Boolean(prop.anyOf && prop.anyOf.some((a) => a.type === "null"));
    const inner = nullable ? resolve(prop.anyOf.find((a) => a.type !== "null")) : resolve(prop);
    const el = inner.properties ? renderNested(obj, k, prop, inner, nullable, p) : renderField(obj, k, prop, inner, nullable, p);
    if (prop.show_when) conditional.push([el, prop.show_when]);
    grid.append(el);
  }
  // Fields that only apply for some values of another field (a structural casing's welded bars).
  if (conditional.length) {
    const apply = () => {
      for (const [el, when] of conditional)
        el.hidden = !Object.entries(when).every(([key, values]) => values.includes(obj[key]));
    };
    apply();
    grid.addEventListener("change", apply);
  }
  return fs;
}

function renderNested(obj, key, prop, inner, nullable, path) {
  const wrap = document.createElement("div");
  wrap.className = "full";
  const draw = () => {
    wrap.innerHTML = "";
    if (nullable) {
      const t = document.createElement("label");
      t.className = "toggle";
      t.innerHTML = `<input type="checkbox" ${obj[key] ? "checked" : ""}> ${esc(prop.title || pretty(key))}
        ${prop.description ? `<span class="hint status">${esc(prop.description)}</span>` : ""}`;
      t.querySelector("input").onchange = (e) => {
        obj[key] = e.target.checked ? defaultsFor(inner) : null;
        markDirty();
        draw();
      };
      wrap.append(t);
      if (!obj[key]) return;
    }
    wrap.append(renderObject(inner, obj[key], path, prop.title));
  };
  if (!nullable && !obj[key]) obj[key] = defaultsFor(inner);
  draw();
  return wrap;
}

function renderField(obj, key, prop, inner, nullable, path) {
  const f = document.createElement("div");
  f.className = "field";
  f.dataset.path = path;
  const unit = prop.unit || inner.unit;
  const title = prop.title || pretty(key);
  const hint = prop.description ? `<div class="hint">${esc(prop.description)}</div>` : "";
  const value = obj[key];

  if (key === "bar_diameters") {
    f.classList.add("full");
    f.innerHTML = `<label>${esc(title)}</label><div class="checks">${MATERIALS.bar_diameters
      .map((d) => `<label><input type="checkbox" value="${d}" ${value.includes(d) ? "checked" : ""}> Ø${d}</label>`)
      .join("")}</div>${hint}`;
    f.querySelectorAll("input").forEach(
      (c) =>
        (c.onchange = () => {
          obj[key] = [...f.querySelectorAll("input:checked")].map((x) => Number(x.value));
          markDirty();
        })
    );
    return f;
  }

  if (inner.type === "array" && resolve(inner.items)?.properties) {
    // A list of small records, e.g. crane areas: one table row each.
    f.classList.add("full");
    const item = resolve(inner.items);
    const cols = Object.entries(item.properties);
    const draw = () => {
      const rows = obj[key] || [];
      f.innerHTML = `<label>${esc(title)}</label>${hint}<div class="scroll"><table class="edit-list"><tr>${cols
        .map(([k, c]) => `<th>${esc(c.title || pretty(k))}${c.unit ? ` (${esc(c.unit)})` : ""}</th>`)
        .join("")}<th></th></tr>${rows
        .map((r, i) => `<tr>${cols
          .map(([k, c]) => {
            const t = resolve(c);
            if (t.enum)
              return `<td><select data-i="${i}" data-k="${esc(k)}">${t.enum.map((o) => `<option ${r[k] === o ? "selected" : ""}>${esc(o)}</option>`).join("")}</select></td>`;
            const text = t.type === "string";
            return `<td><input type="${text ? "text" : "number"}" ${text ? "" : 'step="any"'} data-i="${i}" data-k="${esc(k)}" data-text="${text ? 1 : ""}" value="${esc(r[k] ?? "")}"></td>`;
          })
          .join("")}<td><button type="button" class="quiet" data-del="${i}">Remove</button></td></tr>`)
        .join("")}</table></div><button type="button" class="quiet" data-add style="margin-top:6px">Add a row</button>`;
      f.querySelectorAll("[data-i]").forEach((inp) => (inp.oninput = inp.onchange = () => {
        const v = inp.tagName === "SELECT" || inp.dataset.text ? inp.value : inp.value === "" ? null : Number(inp.value);
        obj[key][Number(inp.dataset.i)][inp.dataset.k] = v;
        markDirty();
      }));
      f.querySelectorAll("[data-del]").forEach((btn) => (btn.onclick = () => {
        obj[key].splice(Number(btn.dataset.del), 1);
        markDirty();
        draw();
      }));
      f.querySelector("[data-add]").onclick = () => {
        obj[key] = [...(obj[key] || []), defaultsFor(item)];
        markDirty();
        draw();
      };
    };
    draw();
    return f;
  }

  if (inner.type === "array" && inner.items?.enum) {
    // e.g. the rows allowed in a pile cage
    f.classList.add("full");
    f.innerHTML = `<label>${esc(title)}</label><div class="checks">${inner.items.enum
      .map((o) => `<label><input type="checkbox" value="${o}" ${value.includes(o) ? "checked" : ""}> ${esc(optionLabel(key, o))}</label>`)
      .join("")}</div>${hint}`;
    f.querySelectorAll("input").forEach(
      (c) =>
        (c.onchange = () => {
          obj[key] = [...f.querySelectorAll("input:checked")].map((x) => Number(x.value));
          markDirty();
        })
    );
    return f;
  }

  if (inner.type === "array" && (inner.items?.type === "number" || inner.items?.type === "integer")) {
    // e.g. standard cut lengths: "6, 8, 9, 12"
    f.innerHTML = `<label>${esc(title)}</label><div class="inputwrap"><input type="text" value="${esc((value || []).join(", "))}">${unit ? `<span class="unit">${esc(unit)}</span>` : ""}</div>${hint}`;
    const input = f.querySelector("input");
    input.oninput = () => {
      obj[key] = input.value.split(/[,;\s]+/).filter(Boolean).map(Number);
      markDirty();
    };
    return f;
  }

  if (inner.type === "boolean") {
    f.innerHTML = `<label class="toggle" style="color:var(--text);font-size:14px"><input type="checkbox" ${value ? "checked" : ""}> ${esc(title)}</label>${hint}`;
    f.querySelector("input").onchange = (e) => {
      obj[key] = e.target.checked;
      markDirty();
    };
    return f;
  }

  const options = inner.enum || (inner.const !== undefined ? [inner.const] : null);
  let control;
  if (options) {
    const inherit = nullable ? `<option value="" ${value == null ? "selected" : ""}>${esc(inheritLabel(key, path))}</option>` : "";
    control = `<select>${inherit}${options
      .map((o) => `<option value="${esc(o)}" ${o === value ? "selected" : ""}>${esc(typeof o === "string" ? prettyOption(o) : o)}</option>`)
      .join("")}</select>`;
  } else if (inner.type === "number" || inner.type === "integer") {
    control = `<input type="number" step="any" value="${value ?? ""}" ${nullable ? `placeholder="${esc(inheritNumber(key, path))}"` : ""}>`;
  } else {
    control = `<input type="text" value="${esc(value ?? "")}">`;
  }
  f.innerHTML = `<label>${esc(title)}</label><div class="inputwrap">${control}${unit ? `<span class="unit">${esc(unit)}</span>` : ""}</div>${hint}`;
  const input = f.querySelector("input, select");
  input.oninput = input.onchange = () => {
    let v = input.value;
    if (options) v = v === "" && nullable ? null : options.find((o) => String(o) === v);
    else if (inner.type === "number" || inner.type === "integer") v = v === "" ? (nullable ? null : v) : Number(v);
    obj[key] = v;
    markDirty();
    if (DURABILITY_KEYS.has(path)) applyDurabilityDefaults();
  };
  return f;
}

// Changing the codes or the design life fills in the project covers and corrosion allowances.
const DURABILITY_KEYS = new Set(["design.design_life_years", "design.durability.cover_code", "design.durability.corrosion_code"]);
async function applyDurabilityDefaults() {
  const d = state.project.design;
  const life = Number(d.design_life_years);
  if (!(life >= 1)) return;
  const q = new URLSearchParams({ cover_code: d.durability.cover_code, corrosion_code: d.durability.corrosion_code, life });
  const res = await api(`${ROOT}/api/durability-defaults?${q}`);
  if (res.covers) d.durability.covers = res.covers;
  if (res.corrosion) d.durability.corrosion = res.corrosion;
  markDirty();
  await route();
  if (res.notes.length) showSaveState(`Unsaved changes. ${res.notes.join(" ")}`);
}

function inheritNumber(key, path) {
  // Unset element covers and corrosion allowances use the project values (Design settings).
  if (key === "width" && /beam/i.test(path)) return "From the model";
  if (key === "restraint_factor") return "From length / depth";
  const d = state?.project?.design?.durability;
  if (!d) return "not set";
  const cv = d.covers, co = d.corrosion;
  const combi = /Combi/.test(path), casing = /casing/.test(path);
  const v = { cover: combi ? cv.combi_infill : /Beam|beam/.test(path) ? cv.beams : cv.piles, cover_top: cv.slab_top,
    cover_bottom: cv.slab_bottom, corrosion_loss: casing ? co.casing : co.combi_tube,
    corrosion_loss_per_face: co.sheet_pile_per_face }[key];
  return v == null ? "not set" : `Project value (${v})`;
}

function inheritLabel(key, path) {
  // Unset element grades use the project grades (Design settings).
  const m = state?.project?.design?.materials || {};
  const combi = /Combi/.test(path);
  const grade = key === "concrete" ? (combi ? m.infill_concrete : m.concrete)
    : key === "steel" ? (/sheet_pile|SPW/.test(path) ? m.sheet_pile_steel : m.structural_steel) : null;
  return grade ? `Project grade (${grade})` : "Project grade";
}

function optionLabel(key, o) {
  if (key === "rows") return o === 1 ? "1 row" : `${o} rows`;
  return String(o);
}

function prettyOption(o) {
  const map = { crack_only: "No: it only removes the crack width check", structural: "Yes: designed with the concrete (E·I share)", min_steel: "Least steel",
    lap: "Lapped", raw: "Raw values", average: "Average with neighbours", unified: "Unified", zoned: "Zoned", coupler: "Couplers", least_steel: "Least steel", standard_lengths: "Standard cut lengths",
    min_cost: "Lowest cost", en1992: "EN 1992-1-1 (Table 4.4N)", en1993_5: "EN 1993-5 (Table 4.2)", bs6349: "BS 6349-1-4 (maritime)", uniform: "Uniform slab", column_and_field: "Column and field strips",
    office: "Office sheets", ec2: "EN 1992-1-1", ec3: "EN 1993 (plastic filled, shell buckling empty)", ei_split: "E·I split where filled", all: "All actions on the tube",
    feltham: "Feltham", two_legs: "Two legs per hoop", peak: "Peak (as they are)", face_mean: "Face mean (each face on its own)",
    ring_mean: "Ring mean (all round the pile)", envelope_face_mean: "Envelope, then face mean",
    midway: "Mid-way between pile rows", at_row: "At a pile row (doubled row)", anywhere: "Anywhere" };
  return map[o] || o;
}

const KIND_LABEL = { pile: "Pile", combi_wall: "Combi wall", sheet_pile_wall: "Sheet pile wall", slab: "Slab",
  front_beam: "Front beam", rear_beam: "Rear beam", transverse_beam: "Transverse beam" };

// ---------------------------------------------------------------- sections tab
function renderSections(host) {
  const p = state.project;
  const def = SCHEMA.$defs.Section;
  const keys = ["name", "x_min", "x_max", "y_min", "y_max", "peaks", "peak_ratio"];
  const schema = { properties: Object.fromEntries(keys.map((k) => [k, def.properties[k]])) };
  host.innerHTML = `<p class="sub">A project can have several sections, e.g. Section 01a and Section 02. Each section has its own
    Plaxis workbook, elements, load multipliers and results. Materials and design settings are shared. A new section
    starts with another section's settings (elements and sizes, levels, combinations, multipliers, costing), or blank.</p>
    <div class="panel row" style="margin-bottom:16px">
      <input id="sec-name" placeholder="Section name, e.g. Section 02" style="flex:1;max-width:280px;padding:7px 9px;border:1px solid var(--line);border-radius:7px;background:var(--input);color:var(--text);font:inherit">
      <label class="hint" for="sec-copy" style="margin:0">Settings</label>
      <select id="sec-copy" style="padding:7px 9px;border:1px solid var(--line);border-radius:7px;background:var(--input);color:var(--text);font:inherit" title="Elements and sizes, levels, load combinations, multipliers and costing inputs are copied. The workbook, mapping and results are not.">
        ${p.sections.map((s) => `<option value="${esc(s.id)}">Copy from ${esc(s.name)}</option>`).join("")}
        <option value="">Start blank</option>
      </select>
      <button id="sec-add" class="quiet">Add section</button><span class="status" id="sec-status"></span></div>`;
  const copy = document.getElementById("sec-copy");
  copy.value = p.sections.at(-1)?.id ?? "";
  const addSection = async (name, copyFrom, status) => {
    if (state.dirty) await save();
    if (state.errors?.length) return;
    try {
      const body = { name, copy_from: copyFrom || null };
      state.project = await api(`${ROOT}/api/projects/${p.id}/sections`, { method: "POST", body: JSON.stringify(body) });
      state.sectionId = state.project.sections.at(-1).id;
      route();
    } catch (e) {
      status.textContent = e.message;
    }
  };
  document.getElementById("sec-add").onclick = () => {
    const name = document.getElementById("sec-name").value.trim();
    if (name) addSection(name, copy.value, document.getElementById("sec-status"));
  };
  p.sections.forEach((s, i) => {
    const card = document.createElement("div");
    card.className = "panel";
    card.style.marginBottom = "16px";
    const n = Object.keys(s.elements).length;
    card.innerHTML = `<div class="element-head"><h3>${esc(s.name)}<span class="type">${n} element${n === 1 ? "" : "s"}</span></h3>
      <span><button class="quiet" data-open data-free>Open</button> <button class="quiet" data-copy title="A new section with this one's settings, without its workbook, mapping or results">Duplicate</button> <button class="danger" data-remove ${p.sections.length > 1 ? "" : "disabled"}>Remove</button></span></div>`;
    card.querySelector("[data-open]").onclick = () => {
      state.sectionId = s.id;
      location.hash = tabHash("elements");
    };
    card.querySelector("[data-copy]").onclick = () => {
      const taken = new Set(p.sections.map((x) => x.name.trim().toLowerCase()));
      let name = `${s.name} copy`;
      for (let k = 2; taken.has(name.toLowerCase()); k++) name = `${s.name} copy ${k}`;
      name = prompt("Name of the new section (it starts with this section's settings):", name)?.trim();
      if (name) addSection(name, s.id, document.getElementById("sec-status"));
    };
    card.querySelector("[data-remove]").onclick = () => {
      if (!confirm(`Remove "${s.name}" with its elements, workbook and results when you save?`)) return;
      p.sections.splice(i, 1);
      markDirty();
      route();
    };
    const fs = renderObject(schema, s, `sections.${i}`, "");
    fs.style.border = "0";
    fs.style.padding = "0";
    card.append(fs);
    const combos = document.createElement("div");
    combos.className = "field full";
    combos.innerHTML = `<label>Load combinations</label><div class="hint">${esc(def.properties.combinations.description)}</div>`;
    combos.append(comboListEditor(s));
    card.append(combos);
    card.append(alignmentEditor(p, s));
    card.append(jointsEditor(p, s));
    host.append(card);
  });
}

// A corner berth: the quay's line in plan, found from the front beam or given by hand
// (triton/alignment.py). Slabs and beams are designed part by part, each turned onto the quay's axis.
function alignmentEditor(p, s) {
  const def = SCHEMA.$defs.Alignment.properties;
  s.alignment ??= { mode: "auto", points: [], min_angle: 2, own_axes: [] };
  const a = s.alignment;
  const box = document.createElement("div");
  box.className = "field full";
  const pts = (list) => (list || []).map((q) => `${fmt(q[0], 2)}, ${fmt(q[1], 2)}`).join("\n");
  box.innerHTML = `<label>Berth alignment (corner berths)</label><div class="hint">${esc(def.mode.description)}
      An inclined part is turned (sin, cos) to lie straight along the quay and designed like the straight part: its own column and
      field strips, stations, zones, punching and cracking. The 3D view keeps it where it is in Plaxis.</div>
    <div class="row" style="gap:10px;align-items:flex-start;flex-wrap:wrap">
      <select data-al="mode"><option value="auto">Automatic, from the front beam</option><option value="straight">Straight berth</option><option value="manual">Corner points by hand</option></select>
      <label class="hint" style="margin:0">Least turn for a corner <input type="number" step="any" min="0.1" max="45" data-al="min_angle" style="width:70px"> °</label>
      <label class="hint" style="margin:0" title="${esc(def.own_axes.description)}">Parts with results in their own axes <input data-al="own_axes" placeholder="e.g. 2" style="width:70px"></label>
      <button class="quiet" data-al-find data-free>Find the parts</button>
    </div>
    <div data-al-points style="margin-top:8px"><div class="hint">${esc(def.points.description)} One point per line: X, Y.</div>
      <textarea data-al="points" rows="4" style="width:100%;max-width:360px;font:inherit"></textarea></div>
    <div class="status" data-al-found></div>`;
  const mode = box.querySelector('[data-al="mode"]');
  const minA = box.querySelector('[data-al="min_angle"]');
  const own = box.querySelector('[data-al="own_axes"]');
  const text = box.querySelector('[data-al="points"]');
  const found = box.querySelector("[data-al-found]");
  const show = () => (box.querySelector("[data-al-points]").style.display = a.mode === "manual" ? "" : "none");
  mode.value = a.mode;
  minA.value = a.min_angle ?? 2;
  own.value = (a.own_axes || []).join(", ");
  text.value = pts(a.points);
  show();
  mode.onchange = () => {
    a.mode = mode.value;
    show();
    markDirty();
  };
  minA.onchange = () => {
    a.min_angle = Number(minA.value) || 2;
    markDirty();
  };
  own.onchange = () => {
    a.own_axes = own.value.split(/[ ,;]+/).map(Number).filter((n) => Number.isInteger(n) && n > 0);
    markDirty();
  };
  text.onchange = () => {
    a.points = text.value.split("\n").map((l) => l.split(/[ ,;\t]+/).filter(Boolean).map(Number)).filter((q) => q.length === 2 && q.every(Number.isFinite));
    markDirty();
  };
  box.querySelector("[data-al-find]").onclick = async () => {
    found.textContent = "Reading the workbook…";
    try {
      if (state.dirty) await save();
      const g = await api(`${ROOT}/api/projects/${p.id}/sections/${s.id}/geometry`);
      const al = g.alignment || {};
      const parts = al.parts || [];
      found.innerHTML = `${esc(al.text || "")}${parts.length > 1 || parts.some((q) => q.rotation_deg) ? `<br>${parts.map((q) => `${esc(q.name)}: ${fmt(q.length_m, 1)} m, from X ${fmt(q.start[0], 2)}, Y ${fmt(q.start[1], 2)} to X ${fmt(q.end[0], 2)}, Y ${fmt(q.end[1], 2)}`).join("<br>")}
        <br><button class="quiet" data-al-use>Edit these points by hand</button>` : ""}`;
      const use = found.querySelector("[data-al-use]");
      if (use) use.onclick = () => {
        a.mode = "manual";
        a.points = (al.points || []).map((q) => q.map((v) => Math.round(v * 1000) / 1000));
        mode.value = "manual";
        text.value = pts(a.points);
        show();
        markDirty();
      };
    } catch (e) {
      found.textContent = e.message;
    }
  };
  return box;
}

// Expansion joints along the berth (triton/joints.py): placed by the rules in Design settings round
// any joints set by hand, and drawn on a plan of the berth laid out straight.
function jointsEditor(p, s) {
  const def = SCHEMA.$defs.SectionJoints.properties;
  s.joints ??= { runs: [], pile_spacing: null, first_row: null, furniture: [], fixed: [], mode: "auto" };
  const j = s.joints;
  const box = document.createElement("div");
  box.className = "field full";
  const nums = (v) => v.split(/[ ,;\t\n]+/).filter(Boolean).map(Number).filter(Number.isFinite);
  const furn = (list) => (list || []).map((f) => `${f.name}, ${fmt(f.chainage, 2)}`).join("\n");
  box.innerHTML = `<label>Expansion joints</label>
    <div class="hint">Triton places the joints by the rules in Design settings (longest and shortest segment, mid-way between pile rows,
      clear of fenders and bollards, a joint at each corner). With the setting on, each beam's and slab's restraint check takes the
      longest segment of its part of the berth as its length between movement joints.</div>
    <div class="row" style="gap:10px;align-items:flex-start;flex-wrap:wrap">
      <select data-j="mode"><option value="auto">Placed by the rules</option><option value="manual">Only the joints set by hand</option></select>
      <label class="hint" style="margin:0" title="${esc(def.runs.description)}">Straight runs, m <input data-j="runs" placeholder="berth length" style="width:150px"></label>
      <label class="hint" style="margin:0" title="${esc(def.pile_spacing.description)}">Pile row spacing <input type="number" step="any" min="0" data-j="pile_spacing" placeholder="from the model" style="width:110px"> m</label>
      <label class="hint" style="margin:0" title="${esc(def.first_row.description)}">First row from each run's start <input type="number" step="any" min="0" data-j="first_row" placeholder="half a bay" style="width:100px"> m</label>
      <label class="hint" style="margin:0" title="${esc(def.fixed.description)}">Joints set by hand at <input data-j="fixed" placeholder="e.g. 120, 250" style="width:130px"> m</label>
    </div>
    <div style="margin-top:8px"><div class="hint">${esc(def.furniture.description)} One item per line: name, chainage (m).</div>
      <textarea data-j="furniture" rows="3" style="width:100%;max-width:360px;font:inherit" placeholder="Bollard, 15"></textarea></div>
    <div class="row" style="margin-top:8px"><button class="quiet" data-j-place data-free>Place the joints</button><span class="status" data-j-status></span></div>
    <div data-j-plan></div>`;
  const q = (k) => box.querySelector(`[data-j="${k}"]`);
  q("mode").value = j.mode || "auto";
  q("runs").value = (j.runs || []).join(", ");
  q("pile_spacing").value = j.pile_spacing ?? "";
  q("first_row").value = j.first_row ?? "";
  q("fixed").value = (j.fixed || []).join(", ");
  q("furniture").value = furn(j.furniture);
  q("mode").onchange = () => { j.mode = q("mode").value; markDirty(); };
  q("runs").onchange = () => { j.runs = nums(q("runs").value).filter((v) => v > 0); markDirty(); };
  q("fixed").onchange = () => { j.fixed = nums(q("fixed").value).filter((v) => v > 0); markDirty(); };
  for (const k of ["pile_spacing", "first_row"])
    q(k).onchange = () => { j[k] = q(k).value === "" ? null : Number(q(k).value); markDirty(); };
  q("furniture").onchange = () => {
    j.furniture = q("furniture").value.split("\n").map((l) => {
      const m = l.split(/[,;\t]+/).map((t) => t.trim());
      const c = Number(m.at(-1));
      return m.length >= 2 && Number.isFinite(c) ? { name: m.slice(0, -1).join(", "), chainage: c } : null;
    }).filter(Boolean);
    markDirty();
  };
  const status = box.querySelector("[data-j-status]");
  const plan = box.querySelector("[data-j-plan]");
  box.querySelector("[data-j-place]").onclick = async () => {
    status.textContent = "Placing…";
    try {
      if (state.dirty) await save();
      const d = await api(`${ROOT}/api/projects/${p.id}/sections/${s.id}/joints`);
      status.textContent = "";
      plan.innerHTML = jointsPlan(d) + (d.segments?.length
        ? `<p><a href="${ROOT}/api/projects/${esc(p.id)}/sections/${esc(s.id)}/joints.dxf">Download the joint layout (DXF)</a></p>` : "");
    } catch (e) {
      status.textContent = e.message;
    }
  };
  return box;
}

// The berth laid out straight: runs (corners), pile rows, furniture, joints and segment lengths.
function jointsPlan(d) {
  if (!d.segments?.length) return `<p class="status">${esc(d.text || "")}</p>`;
  const L = d.berth_length_m, W = 1000, pad = 20, sx = (c) => pad + (c / L) * (W - 2 * pad);
  const deckY = 46, deckH = 26;
  const rows = L / (d.pile_spacing_m || L) > 250 ? [] : d.pile_rows || [];
  const svg = `<svg viewBox="0 0 ${W} 120" style="width:100%;height:auto;max-height:180px" role="img" aria-label="Expansion joints along the berth">
    <rect x="${sx(0)}" y="${deckY}" width="${sx(L) - sx(0)}" height="${deckH}" fill="var(--accent-bg)" stroke="var(--line)"/>
    ${rows.map((c) => `<line x1="${sx(c)}" x2="${sx(c)}" y1="${deckY + 4}" y2="${deckY + deckH - 4}" stroke="var(--muted)" stroke-width="0.6" opacity="0.6"/>`).join("")}
    ${(d.runs || []).slice(1).map((r) => `<line x1="${sx(r.start)}" x2="${sx(r.start)}" y1="${deckY - 12}" y2="${deckY + deckH + 12}" stroke="var(--text)" stroke-dasharray="3 3"/>`).join("")}
    ${(d.furniture || []).map((f) => `<path d="M${sx(f.chainage) - 3},${deckY + deckH + 10} l3,-6 l3,6 z" fill="var(--warn)"><title>${esc(f.name)} at ${fmt(f.chainage, 1)} m</title></path>`).join("")}
    ${d.joints.map((jt) => `<line x1="${sx(jt.chainage)}" x2="${sx(jt.chainage)}" y1="${deckY - 6}" y2="${deckY + deckH + 6}" stroke="var(--err)" stroke-width="2.5"><title>Joint at ${fmt(jt.chainage, 1)} m (${esc(jt.from)})</title></line>`).join("")}
    ${d.segments.map((sg) => `<text x="${(sx(sg.start) + sx(sg.end)) / 2}" y="${deckY - 10}" text-anchor="middle" font-size="${d.segments.length > 14 ? 9 : 12}" fill="var(--text)">${fmt(sg.length, 1)}</text>`).join("")}
    <text x="${sx(0)}" y="${deckY + deckH + 30}" font-size="11" fill="var(--muted)">0</text>
    <text x="${sx(L)}" y="${deckY + deckH + 30}" font-size="11" fill="var(--muted)" text-anchor="end">${fmt(L, 1)} m</text>
  </svg>`;
  const legend = `<div class="hint"><span style="color:var(--err)">▍</span> joint · <span style="color:var(--warn)">▲</span> furniture ·
    thin lines: pile rows${rows.length ? "" : " (too many to draw)"} · dashed: corner · figures: segment lengths, m</div>`;
  const facts = `<p class="status">${esc(d.text)} Pile rows every ${d.pile_spacing_m ? fmt(d.pile_spacing_m, 2) + " m (" + esc(d.pile_spacing_from) + ")" : "– (none found)"};
    furniture from ${esc(d.furniture_from || "none")}.</p>`;
  const warn = (d.warnings || []).length ? `<ul class="errors">${d.warnings.map((w) => `<li class="warning">${esc(w)}</li>`).join("")}</ul>` : "";
  const table = `<div class="scroll"><table><thead><tr><th>Segment</th><th class="num">From, m</th><th class="num">To, m</th><th class="num">Length, m</th><th>Ends at</th></tr></thead><tbody>
    ${d.segments.map((sg, i) => {
      const jt = d.joints[i];
      const end = jt ? `joint (${esc(jt.from)})${jt.nearest_furniture_m != null ? `, ${fmt(jt.nearest_furniture_m, 1)} m from the nearest furniture` : ""}` : "end of the berth";
      return `<tr><td>${esc(sg.name)}</td><td class="num">${fmt(sg.start, 1)}</td><td class="num">${fmt(sg.end, 1)}</td><td class="num"><strong>${fmt(sg.length, 1)}</strong></td><td>${end}</td></tr>`;
    }).join("")}</tbody></table></div>`;
  return svg + legend + facts + warn + table;
}


// ---------------------------------------------------------------- elements tab
function renderElements(host) {
  const p = sec();
  const names = Object.keys(p.elements);
  host.innerHTML = `<div class="panel row" style="margin-bottom:16px">
      <input id="el-name" placeholder="Element name, e.g. Pile(5)" style="flex:1;max-width:280px;padding:7px 9px;border:1px solid var(--line);border-radius:7px;background:var(--input);color:var(--text);font:inherit">
      <button id="el-add" class="quiet">Add element</button>
      <span class="status" id="el-status">Or add them all from the Workbook tab.</span></div>`;
  document.getElementById("el-add").onclick = async () => {
    const name = document.getElementById("el-name").value.trim();
    if (!name) return;
    await addElements([name]);
  };
  approachPanel(host, { project: state.project, schema: SCHEMA.properties.approach, renderObject, esc });
  if (!names.length) {
    host.insertAdjacentHTML("beforeend", `<p class="empty">No elements yet.</p>`);
    return;
  }
  for (const name of names) {
    const el = p.elements[name];
    const schema = { $ref: SCHEMA.$defs.Section.properties.elements.additionalProperties.discriminator.mapping[el.kind] };
    const card = document.createElement("div");
    card.className = "panel";
    card.style.marginBottom = "16px";
    card.innerHTML = `<div class="element-head"><h3>${esc(name)}<span class="type">${esc(KIND_LABEL[el.kind] || el.kind)}</span></h3>
      <button class="danger">Remove</button></div>`;
    card.querySelector("button").onclick = () => {
      const copy = { ...p.elements };
      delete copy[name];
      p.elements = copy;
      markDirty();
      route();
    };
    const fs = renderObject(schema, el, `sections.${secIndex()}.elements.${name}`, "");
    fs.style.border = "0";
    fs.style.padding = "0";
    card.append(fs);
    host.append(card);
  }
}

async function addElements(names) {
  if (state.dirty) await save();
  if (state.errors?.length) return;
  const res = await api(`${secUrl()}/elements`, { method: "POST", body: JSON.stringify({ names }) });
  state.project = res.project;
  const s = document.getElementById("el-status");
  const skipped = names.filter((n) => !res.added.includes(n) && !(n in sec().elements));
  const msg = [res.added.length ? `Added ${res.added.join(", ")}.` : "Nothing new to add.",
    skipped.length ? `Not recognised: ${skipped.join(", ")}.` : ""].join(" ");
  route();
  const s2 = document.getElementById("el-status") || s;
  if (s2) s2.textContent = msg;
}

// ---------------------------------------------------------------- workbook check
function checkerHtml(slot = "upload-check") {
  return `<div class="panel row">
      <input type="file" id="file" accept=".xlsb,.xlsx,.xlsm">
      <select id="upload-mode" hidden title="What to do with the workbook this section already has">
        <option value="replace">Replace the whole workbook</option>
        <option value="update">Replace matching tabs and add new ones</option>
        <option value="matching">Replace matching tabs only</option>
        <option value="add">Add new tabs only</option>
      </select>
      <button id="run" disabled>Check workbook</button>
      <button class="quiet" id="del-tabs" hidden>Delete tabs…</button>
      <span class="status" id="status"></span></div>
    <div id="del-panel" hidden></div>
    <div data-slot="${esc(slot)}"></div>
    <div id="report" hidden>
      <div class="counts">
        <div class="count error"><b id="n-error">0</b>errors</div>
        <div class="count warning"><b id="n-warning">0</b>warnings</div>
        <div class="count"><b id="n-info">0</b>automatic clean-ups</div>
      </div>
      <div id="add-found"></div>
      <div id="combos"></div>
      <div id="mapping"></div>
      <div id="factors"></div>
      <h2>Sheets found</h2>
      <div class="panel scroll"><table id="coverage"></table></div>
      <h2>Problems to review</h2>
      <div id="review-bar" hidden></div>
      <div class="panel scroll"><table id="problems"></table></div>
      <div id="axes-block" hidden><h2>Directions of the actions</h2>
        <p class="status">Worked out from the results (shears against the moments' change, forces against depth). Confirm them against the Plaxis model.</p>
        <div class="panel scroll"><table id="axes"></table></div></div>
      <details class="panel" style="margin-top:16px"><summary>Automatic clean-ups</summary>
        <div class="scroll"><table id="cleanups"></table></div></details>
    </div>`;
}

// ---------------------------------------------------------------- long work
// An upload or a design is a job that keeps running whatever tab, section or project is open. The
// tab it belongs to shows it in place (a slot, data-slot="…"); anywhere else the dock at the bottom
// right shows it, and a finished job says so there with a link back.
function newJob(job) {
  Object.assign(job, { id: Math.random().toString(36).slice(2), began: Date.now(), state: "running", stopped: false });
  for (const old of JOBS.filter((j) => j.slot === job.slot && j.state !== "running")) JOBS.splice(JOBS.indexOf(old), 1);
  JOBS.push(job);
  drawJobs();
  return job;
}

function jobDone(job, end, message) {
  job.state = end;
  job.message = message;
  for (const s of job.steps) if (s.state === "running") s.state = end === "done" ? "done" : "waiting";
  drawJobs();
  if (end === "done") setTimeout(() => dismissJob(job), 60000);
}

function dismissJob(job) {
  const i = JOBS.indexOf(job);
  if (i >= 0) JOBS.splice(i, 1);
  drawJobs();
}

const busyWith = (slot) => JOBS.some((j) => j.slot === slot && j.state === "running");

function jobFraction(job) {
  if (job.state === "done") return 1;
  const weight = (s) => s.weight ?? 1;
  const total = job.steps.reduce((a, s) => a + weight(s), 0) || 1;
  const got = job.steps.reduce((a, s) => a + weight(s) * (s.state === "done" ? 1 : s.fraction ?? 0), 0);
  return Math.min(got / total, 0.99);
}

function timeLeft(job, f) {
  const spent = (Date.now() - job.began) / 1000;
  if (job.state !== "running" || f < 0.04 || spent < 4) return "";
  const s = (spent * (1 - f)) / f;
  return s < 60 ? `about ${Math.max(5, Math.ceil(s / 5) * 5)} s left` : `about ${Math.ceil(s / 60)} min left`;
}

const JOB_END = { done: "Done", failed: "Failed", stopped: "Stopped" };

function stepNote(s) {
  if (s.note) return s.note;
  if (s.state === "done") return "Done";
  if (s.state === "running") return s.fraction == null ? "Working" : `${Math.round(s.fraction * 100)}%`;
  return "";
}

// The card: an overall bar with the percentage and time left, then one bar per step (or element).
function jobCardHtml(job, inDock) {
  return `<div class="job ${job.state}" data-job="${job.id}" data-sig="${job.state}:${job.steps.length}">
    <div class="job-head"><span class="job-title">${esc(job.title)}</span><span class="job-pct"></span>
      ${job.state === "running" ? `<button class="quiet small" data-job-stop>Stop</button>` : `<button class="x" data-job-close title="Close">×</button>`}</div>
    <div class="bar big"><i></i></div>
    <div class="job-sub"><span></span>${inDock && job.home ? ` <a href="${job.home}">Open</a>` : ""}</div>
    <ol class="job-steps${job.steps.length > 5 ? " many" : ""}">${job.steps
      .map((s) => `<li><span class="step-name">${esc(s.label)}</span><span class="bar"><i></i></span><span class="step-note"></span></li>`)
      .join("")}</ol></div>`;
}

// Updates a card in place, so the bars glide from one value to the next.
function fillJobCard(card, job) {
  const f = jobFraction(job);
  const running = job.steps.find((s) => s.state === "running");
  card.querySelector(".job-pct").textContent = JOB_END[job.state] && job.state !== "done" ? JOB_END[job.state] : `${Math.round(f * 100)}%`;
  card.querySelector(".bar.big i").style.width = `${Math.max(f * 100, 1)}%`;
  card.querySelector(".job-sub span").textContent =
    job.state === "running" ? [running?.detail || running?.label, timeLeft(job, f)].filter(Boolean).join(" · ") : job.message || "";
  card.querySelectorAll(".job-steps li").forEach((li, i) => {
    const s = job.steps[i];
    li.className = `${s.state}${s.bad ? " bad" : ""}${s.state === "running" && s.fraction == null ? " busy" : ""}`;
    li.querySelector("i").style.width = `${s.state === "done" ? 100 : s.state === "running" && s.fraction == null ? 100 : Math.round((s.fraction ?? 0) * 100)}%`;
    li.querySelector(".step-note").textContent = stepNote(s);
  });
  if (running && card.querySelector(".job-steps.many")) {
    const li = card.querySelectorAll(".job-steps li")[job.steps.indexOf(running)];
    const list = li?.parentElement;
    if (li && list && (li.offsetTop < list.scrollTop || li.offsetTop > list.scrollTop + list.clientHeight - 24))
      list.scrollTop = li.offsetTop - list.clientHeight / 2;
  }
}

function showJob(box, job, inDock) {
  let card = box.querySelector(`[data-job="${job.id}"]`);
  if (!card || card.dataset.sig !== `${job.state}:${job.steps.length}`) {
    const html = jobCardHtml(job, inDock);
    if (card) card.outerHTML = html;
    else box.insertAdjacentHTML("beforeend", html);
    card = box.querySelector(`[data-job="${job.id}"]`);
    card.querySelector("[data-job-stop]")?.addEventListener("click", (e) => {
      e.target.disabled = true;
      e.target.textContent = "Stopping…";
      job.stop?.();
    });
    card.querySelector("[data-job-close]")?.addEventListener("click", () => dismissJob(job));
  }
  fillJobCard(card, job);
}

function drawJobs() {
  let dock = document.getElementById("dock");
  if (!dock) {
    dock = document.createElement("div");
    dock.id = "dock";
    document.body.append(dock);
  }
  const shown = new Set();
  for (const job of JOBS) {
    const slot = document.querySelector(`[data-slot="${CSS.escape(job.slot)}"]`);
    const box = slot || dock;
    shown.add(job.id);
    if (slot) dock.querySelector(`[data-job="${job.id}"]`)?.remove();
    showJob(box, job, !slot);
  }
  document.querySelectorAll("[data-job]").forEach((c) => shown.has(c.dataset.job) || c.remove());
}
// Time left moves on even between answers.
setInterval(() => JOBS.some((j) => j.state === "running") && drawJobs(), 1000);
window.addEventListener("beforeunload", (e) => {
  if (JOBS.some((j) => j.state === "running") || state?.dirty) e.preventDefault();
});

// Workbooks go up in pieces: hosts cap one request (PythonAnywhere at about 100 MB), and a whole
// project's workbook can be bigger than that. A piece that fails is sent again.
const PIECE = 8 * 1024 * 1024;
async function sendInPieces(f, progress, run = {}) {
  const { id } = await api(ROOT + "/api/uploads", {
    method: "POST",
    body: JSON.stringify({ filename: f.name, size: f.size }),
  });
  run.upload = id;
  let at = 0;
  do {
    if (run.stopped) {
      await api(`${ROOT}/api/uploads/${id}`, { method: "DELETE" }).catch(() => {});
      throw new Error("Stopped.");
    }
    const piece = f.slice(at, at + PIECE);
    await again(() =>
      api(`${ROOT}/api/uploads/${id}?offset=${at}`, {
        method: "PUT",
        body: piece,
        headers: { "Content-Type": "application/octet-stream" },
      }),
    );
    at += piece.size;
    progress(at);
  } while (at < f.size);
  return id;
}

// Upload, read (a few sheets per request) and check a workbook as a job. ``url`` takes the upload:
// the workbook check, or a section's workbook (with ``mode`` for a second upload).
async function uploadJob(f, { url, mode, title, slot, home, onDone }) {
  const mb = (n) => (n / 1048576).toFixed(0);
  const job = newJob({
    kind: "upload",
    title,
    slot,
    home,
    steps: [
      { label: "Upload", weight: 3, state: "running", fraction: 0 },
      { label: "Read the sheets", weight: 6, state: "waiting" },
      { label: "Check and keep", weight: 1, state: "waiting" },
    ],
  });
  const [up, read, check] = job.steps;
  job.stop = async () => {
    job.stopped = true;
    if (job.key) await api(`${ROOT}/api/progress/${job.key}/stop`, { method: "POST" }).catch(() => {});
  };
  try {
    const id = await sendInPieces(
      f,
      (at) => {
        up.fraction = at / Math.max(f.size, 1);
        up.detail = `Uploading ${f.name}: ${mb(at)} of ${mb(f.size)} MB`;
        up.note = `${mb(at)} / ${mb(f.size)} MB`;
        drawJobs();
      },
      job,
    );
    up.state = "done";
    read.state = "running";
    read.fraction = 0;
    drawJobs();
    // A few sheets per request: a host cuts off a request that runs too long.
    for (;;) {
      if (job.stopped) {
        await api(`${ROOT}/api/uploads/${id}`, { method: "DELETE" }).catch(() => {});
        throw new Error("Stopped.");
      }
      const r = await again(() => api(`${ROOT}/api/uploads/${id}/read`, { method: "POST" }));
      read.fraction = r.fraction;
      read.note = `${r.sheets} of ${r.of} sheets`;
      read.detail = r.step;
      drawJobs();
      if (r.done) break;
    }
    read.state = "done";
    check.state = "running";
    job.key = id;
    drawJobs();
    const data = await again(() => api(`${url}/${id}${mode ? `?mode=${mode}` : ""}`, { method: "POST" }));
    const counts = data.counts ? `${data.counts.error} errors, ${data.counts.warning} warnings` : "";
    jobDone(job, "done", data.merged ? mergedText(data.merged) : `Checked ${data.file}${counts ? `: ${counts}` : ""}.`);
    onDone?.(data);
  } catch (e) {
    jobDone(job, job.stopped ? "stopped" : "failed", job.stopped ? "Stopped. Nothing was kept from this upload." : `Failed: ${e.message}`);
  }
}

function wireChecker(onReport, url = ROOT + "/api/workbooks/check", slot = "upload-check") {
  const file = document.getElementById("file");
  const run = document.getElementById("run");
  const ready = () => (run.disabled = !file.files.length || busyWith(slot));
  file.onchange = ready;
  ready();
  run.onclick = async () => {
    const f = file.files[0];
    if (!f || busyWith(slot)) return;
    const mode = document.getElementById("upload-mode");
    const home = location.hash;
    const sid = state && url !== ROOT + "/api/workbooks/check" ? sec()?.id : null;
    const job = uploadJob(f, {
      url,
      mode: mode && !mode.hidden ? mode.value : null,
      title: `${f.name}${state && url !== ROOT + "/api/workbooks/check" ? ` into ${sec().name}` : ""}`,
      slot,
      home,
      onDone: (data) => {
        if (location.hash !== home) return; // the tab shows it when opened again
        if (url === ROOT + "/api/workbooks/check") {
          renderReport(data);
          onReport?.(data);
        } else {
          OPENED.add(sid); // the upload has just checked it: the tab opens it at once
          route();
        }
      },
    });
    ready();
    await job;
    if (document.body.contains(run)) ready();
  };
}

// What a second upload into a section did to its workbook.
function mergedText(m) {
  const list = (xs) => (xs.length > 4 ? `${xs.slice(0, 4).join(", ")} and ${xs.length - 4} more` : xs.join(", "));
  const parts = [];
  if (m.replaced.length) parts.push(`replaced ${m.replaced.length} tab(s): ${list(m.replaced)}`);
  if (m.added.length) parts.push(`added ${m.added.length} tab(s): ${list(m.added)}`);
  if (m.skipped.length) parts.push(`left out ${m.skipped.length} tab(s) already in the section: ${list(m.skipped)}`);
  const text = parts.join("; ") || "nothing new in that file";
  return text[0].toUpperCase() + text.slice(1) + ". Map any new tabs below if they are not recognised.";
}

function checkPage() {
  $app.innerHTML = `<h1>Check a workbook</h1>
    <p class="sub">Upload the Plaxis straining-actions workbook to check it before design.</p>` + checkerHtml();
  wireChecker();
  drawJobs();
}

async function renderWorkbookTab(host) {
  const url = secUrl();
  const slot = `upload-${sec().id}`;
  host.innerHTML = `<p class="sub" id="wb-note">Upload the Plaxis workbook for ${esc(sec().name)}. It is checked, then kept with
    the section so its elements can be designed without uploading it again. You can go on working on other tabs while it uploads.</p>` + checkerHtml(slot);
  const onReport = (data) => {
    document.getElementById("wb-note").outerHTML = `<p class="sub" id="wb-note">${esc(
      `Workbook in use: ${data.file}, uploaded ${when(data.uploaded_at)} (Cairo time). Upload another file to replace it, replace some of its tabs or add tabs to it.`,
    )}</p>`;
    document.getElementById("upload-mode").hidden = false;
    wireDeleteTabs(data, url);
    renderFactors(data);
    const refresh = async (given) => {
      const fresh = given ?? (await api(`${url}/workbook`));
      renderReport(fresh);
      onReport(fresh);
    };
    renderMapping(data, refresh);
    renderCombos(data, refresh);
    renderReview(data, refresh, url);
    if (state.geometry?.uploaded !== data.uploaded_at) state.geometry = { uploaded: data.uploaded_at };
    const missing = data.elements.filter((e) => !(e in sec().elements));
    const box = document.getElementById("add-found");
    if (!missing.length) {
      box.innerHTML = `<p class="status">Every element in the workbook is already in this section.</p>`;
      return;
    }
    box.innerHTML = `<div class="panel row" style="margin-top:16px"><span>${missing.length} element(s) in the workbook are not in this section yet: ${esc(missing.join(", "))}.</span>
      <button id="add-all">Add to section</button></div>`;
    document.getElementById("add-all").onclick = async () => {
      await addElements(missing);
      location.hash = tabHash("elements");
    };
  };
  wireChecker(onReport, `${url}/workbook`, slot);
  drawJobs();
  // First only what the workbook is (a small file): reading and checking all of it takes a while,
  // and uploading, replacing or deleting tabs does not need it.
  const sid = sec().id;
  let brief;
  try {
    brief = await api(`${url}/workbook/brief`);
  } catch {
    return; /* no workbook yet */
  }
  if (!document.body.contains(host) || sec()?.id !== sid) return;
  const openSlot = `open-${sid}`;
  const note = document.getElementById("wb-note");
  const c = brief.counts || {};
  note.outerHTML = `<div class="panel wb-brief" id="wb-note">
      <div class="row"><strong>Workbook in use: ${esc(brief.file)}</strong><span class="status">uploaded ${when(brief.uploaded_at)} (Cairo time)</span></div>
      <p class="status">${brief.sheets.length} tab${brief.sheets.length === 1 ? "" : "s"}, ${brief.elements.length} element${brief.elements.length === 1 ? "" : "s"}
        · ${c.error ?? 0} errors, ${c.warning ?? 0} warnings, ${c.info ?? 0} automatic clean-ups${brief.checked ? "" : " (as found on upload)"}</p>
      <div class="row" id="wb-open-row"><button id="wb-open">Open the workbook</button>
        <span class="status">Shows its checks, sheet mapping and warnings. To replace it, replace or add tabs, or delete tabs,
        you don't need to open it: use the upload box below.</span></div>
      <div data-slot="${esc(openSlot)}"></div></div>`;
  document.getElementById("upload-mode").hidden = false;
  wireDeleteTabs(brief, url);
  const shown = (data) => {
    if (!document.body.contains(host) || sec()?.id !== sid) return false;
    document.getElementById("wb-open-row")?.remove();
    renderReport(data);
    onReport(data);
    return true;
  };
  const open = () => openWorkbook(url, brief.file, openSlot, shown);
  document.getElementById("wb-open").onclick = open;
  drawJobs();
  if (busyWith(openSlot)) document.getElementById("wb-open-row").hidden = true;
  else if (OPENED.has(sid)) open();
}

// Reads and checks a section's stored workbook as a job with a percentage: how far the server has
// got (it says so under a progress key), then the download of the answer. A check already worked
// out comes back at once; ``fresh`` checks it again anyway.
async function fetchWorkbook(url, { title, slot, fresh = false, doneText }) {
  const key = `wb-${Math.random().toString(36).slice(2)}`;
  const job = newJob({
    kind: "open",
    title,
    slot,
    home: location.hash,
    steps: [
      { label: "Read and check", weight: 4, state: "running", fraction: 0, detail: "Loading the workbook" },
      { label: "Download", weight: 1, state: "waiting" },
    ],
  });
  const [check, down] = job.steps;
  const ctrl = new AbortController();
  job.stop = () => {
    job.stopped = true;
    ctrl.abort();
  };
  const poll = setInterval(async () => {
    try {
      const p = await api(`${ROOT}/api/progress/${key}`);
      if (check.state !== "running") return;
      check.fraction = p.fraction;
      check.detail = p.step;
      drawJobs();
    } catch {
      /* not started yet, or finished */
    }
  }, 700);
  try {
    const res = await fetch(`${url}/workbook?progress=${key}${fresh ? "&fresh=true" : ""}`, { signal: ctrl.signal });
    clearInterval(poll);
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      throw new Error(typeof d.detail === "string" ? d.detail : GATEWAY.includes(res.status) ? `The server did not answer (${res.status}); try again.` : res.statusText);
    }
    Object.assign(check, { state: "done", fraction: 1 });
    Object.assign(down, { state: "running", fraction: 0, detail: "Downloading the checks" });
    drawJobs();
    const size = Number(res.headers.get("Content-Length")) || 0;
    const reader = res.body.getReader();
    const parts = [];
    let got = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      parts.push(value);
      got += value.length;
      if (size) {
        down.fraction = Math.min(got / size, 1);
        down.note = `${(got / 1048576).toFixed(1)} / ${(size / 1048576).toFixed(1)} MB`;
        drawJobs();
      }
    }
    const data = JSON.parse(await new Blob(parts).text());
    jobDone(job, "done", doneText);
    job.data = data;
    return job;
  } catch (e) {
    clearInterval(poll);
    jobDone(job, job.stopped ? "stopped" : "failed", job.stopped ? "Stopped." : `Failed: ${e.message}`);
    throw Object.assign(e, { job });
  }
}

async function openWorkbook(url, file, slot, shown) {
  if (busyWith(slot)) return;
  document.getElementById("wb-open-row")?.setAttribute("hidden", "");
  const home = location.hash;
  const sid = sec().id;
  try {
    const job = await fetchWorkbook(url, { title: `Opening ${file}`, slot, doneText: `Opened ${file}.` });
    OPENED.add(sid);
    if (location.hash === home && shown(job.data)) dismissJob(job);
  } catch {
    document.getElementById("wb-open-row")?.removeAttribute("hidden");
  }
}

// Delete some tabs of the section's workbook, or all of it (back to a blank section). Results
// designed from them stay, flagged as out of date.
function wireDeleteTabs(data, url) {
  const button = document.getElementById("del-tabs");
  const panel = document.getElementById("del-panel");
  button.hidden = false;
  button.onclick = () => {
    panel.hidden = !panel.hidden;
    if (panel.hidden) return;
    panel.innerHTML = `<div class="panel" style="margin-top:12px">
        <div class="row"><strong>Delete tabs</strong><span class="status">Pick the tabs to delete from ${esc(data.file)}.</span>
          <span style="flex:1"></span><label class="chip"><input type="checkbox" data-all-tabs> All tabs</label></div>
        <div class="checks tabs-list">${data.sheets
          .map((s) => `<label><input type="checkbox" value="${esc(s.name)}"> ${esc(s.name)}</label>`)
          .join("")}</div>
        <div class="row"><button class="danger" id="del-some" disabled>Delete selected tabs</button>
          <button class="danger quiet" id="del-all">Delete the whole workbook</button><span class="status" id="del-status"></span></div></div>`;
    const boxes = [...panel.querySelectorAll(".tabs-list input")];
    const some = panel.querySelector("#del-some");
    const picked = () => boxes.filter((b) => b.checked).map((b) => b.value);
    const count = () => {
      const n = picked().length;
      some.disabled = !n;
      some.textContent = n ? `Delete ${n} tab${n === 1 ? "" : "s"}` : "Delete selected tabs";
    };
    boxes.forEach((b) => (b.onchange = count));
    panel.querySelector("[data-all-tabs]").onchange = (e) => {
      boxes.forEach((b) => (b.checked = e.target.checked));
      count();
    };
    const status = panel.querySelector("#del-status");
    some.onclick = async () => {
      const names = picked();
      const list = names.length > 6 ? `${names.slice(0, 6).join(", ")} and ${names.length - 6} more` : names.join(", ");
      if (!confirm(`Delete ${names.length} tab(s) from this section's workbook?\n\n${list}\n\nResults designed from them will show as out of date.`)) return;
      status.textContent = "Deleting…";
      try {
        await api(`${url}/workbook/delete`, { method: "POST", body: JSON.stringify({ sheets: names }) });
        route();
      } catch (e) {
        status.textContent = e.message;
      }
    };
    panel.querySelector("#del-all").onclick = async () => {
      if (!confirm(`Delete the whole workbook of ${sec().name}? The section keeps its elements and settings, and its results show as out of date until a new workbook is uploaded and designed.`)) return;
      status.textContent = "Deleting…";
      try {
        await api(`${url}/workbook`, { method: "DELETE" });
        route();
      } catch (e) {
        status.textContent = e.message;
      }
    };
  };
}

// A section's load combinations as removable chips, with a box to add one.
function comboListEditor(section, changed) {
  const wrap = document.createElement("div");
  wrap.className = "combo-list";
  const draw = () => {
    wrap.innerHTML = `${section.combinations
      .map((c, i) => `<span class="chip">${esc(c)} <button type="button" class="x" data-del="${i}" title="Remove ${esc(c)}">×</button></span>`)
      .join("")}<input type="text" placeholder="Add a combination" data-add-combo><button type="button" class="quiet" data-add-btn>Add</button>`;
    wrap.querySelectorAll("[data-del]").forEach((b) => (b.onclick = () => {
      section.combinations.splice(Number(b.dataset.del), 1);
      markDirty();
      draw();
      changed?.();
    }));
    const box = wrap.querySelector("[data-add-combo]");
    const add = () => {
      const v = box.value.trim();
      if (!v || section.combinations.some((c) => c.toLowerCase() === v.toLowerCase())) return;
      section.combinations.push(v);
      markDirty();
      draw();
      changed?.();
    };
    wrap.querySelector("[data-add-btn]").onclick = add;
    box.onkeydown = (e) => e.key === "Enter" && add();
  };
  section.combinations ??= [];
  draw();
  return wrap;
}

// The first step after an upload: the workbook's combinations against the section's list. Mapping
// the sheets comes after every combination is either one of the list or left out.
function renderCombos(data, refresh) {
  const box = document.getElementById("combos");
  const map = document.getElementById("mapping");
  const check = data.combination_check;
  if (!box || !check) return;
  const p = sec();
  p.combination_map ??= {};
  const draw = () => {
    const rows = check.found
      .map((f) => {
        const chosen = f.name in p.combination_map ? p.combination_map[f.name] : f.target ?? null;
        const opts = p.combinations
          .map((c) => `<option value="${esc(c)}" ${chosen === c ? "selected" : ""}>${esc(c)}</option>`)
          .join("");
        const state = { defined: ["ok", "defined"], matched: ["info", "same, spelled differently"], read_as: ["info", "read as chosen"], left_out: ["info", "left out"], undefined: ["error", "not in the list"] }[f.status];
        return `<tr><td>${esc(f.name)}</td><td>${f.sheets}</td>
          <td><select data-combo="${esc(f.name)}">${chosen == null ? '<option value="?" selected>Pick one…</option>' : ""}${opts}
            <option value="+" >Add “${esc(f.name)}” to the list</option><option value="" ${chosen === "" ? "selected" : ""}>Leave these sheets out</option></select></td>
          <td><span class="sev ${state[0]}">${esc(state[1])}</span></td></tr>`;
      })
      .join("");
    const open = check.unresolved.length;
    box.innerHTML = `<h2>Load combinations</h2>
      <p class="status" style="margin-top:0">This section's combinations. Each combination in the workbook is read as one of them or left out; sheets are mapped only to them.</p>
      <div class="panel"><div id="combo-list"></div>
        <div class="scroll" style="margin-top:12px"><table><tr><th>In the workbook</th><th>Sheets</th><th>Read as</th><th></th></tr>${rows || '<tr><td colspan="4">No combinations read yet.</td></tr>'}</table></div>
        ${check.missing.length ? `<p class="status">In the list but not in the workbook: ${esc(check.missing.join(", "))}.</p>` : ""}
        <div class="row" style="margin-top:10px"><button id="combo-apply">Apply combinations</button><span class="status" id="combo-status">${open ? `${open} combination(s) to decide before the sheets are mapped.` : ""}</span></div></div>`;
    box.querySelector("#combo-list").append(comboListEditor(p, () => (box.querySelector("#combo-status").textContent = "Changed: press Apply combinations.")));
    box.querySelectorAll("[data-combo]").forEach((sel) => (sel.onchange = () => {
      const name = sel.dataset.combo;
      if (sel.value === "+") {
        if (!p.combinations.includes(name)) p.combinations.push(name);
        delete p.combination_map[name];
      } else if (sel.value !== "?") {
        const f = check.found.find((x) => x.name === name);
        if (sel.value === f.target && f.status !== "read_as" && f.status !== "left_out") delete p.combination_map[name];
        else p.combination_map[name] = sel.value;
      }
      markDirty();
      box.querySelector("#combo-status").textContent = "Changed: press Apply combinations.";
      if (sel.value === "+") draw();
    }));
    box.querySelector("#combo-apply").onclick = async () => {
      const st = box.querySelector("#combo-status");
      st.textContent = "Saving…";
      await save();
      if (state.errors?.length) {
        st.textContent = state.errors.map((e) => e.msg).join(" ");
        return;
      }
      st.textContent = "Checking the workbook again…";
      await refresh();
    };
    if (map) map.hidden = open > 0;
  };
  draw();
}

// Sheet mapping: sheets whose names do not follow '<Element>-<Combination>', assigned by hand.
function renderMapping(data, refresh) {
  const box = document.getElementById("mapping");
  if (!box) return;
  const p = sec();
  p.sheet_map ??= {};
  const hints = data.suggestions || {};
  const sheets = data.sheets.filter((x) => !x.empty);
  // The sheets that need a look: not recognised, spelled differently from the rest, or mapped by hand.
  const needs = (x) => x.name in p.sheet_map || !x.element || x.name in hints;
  let showAll = false;
  const picked = new Set();
  const known = [...new Set([...data.elements, ...Object.keys(p.elements), ...Object.values(hints).map((h) => h.element)])];
  const combos = p.combinations?.length ? p.combinations : [...new Set([...data.combinations.map((c) => c.name), ...Object.values(hints).map((h) => h.combination)])];
  const comboSelect = (attrs, value, first) =>
    `<select ${attrs}><option value="">${first}</option>${[...combos, ...(value && !combos.includes(value) ? [value] : [])]
      .map((c) => `<option ${c === value ? "selected" : ""}>${esc(c)}</option>`)
      .join("")}</select>`;
  const pending = () => Object.keys(hints).filter((n) => !(n in p.sheet_map));
  const accept = (n) => {
    const h = hints[n];
    p.sheet_map[n] = { element: h.element, combination: h.combination, ignore: false };
  };
  const draw = () => {
    const list = sheets.filter((x) => showAll || needs(x));
    const rows = list
      .map((x) => {
        const n = x.name;
        const mapped = p.sheet_map[n];
        const h = hints[n];
        const m = mapped || (h ? h : { element: x.element || "", combination: x.combination || "" });
        const tag = mapped
          ? '<span class="sev ok">mapped</span>'
          : h
            ? `<span class="sev ${h.sure ? "info" : "warning"}" title="${esc(h.why)}">suggested${h.sure ? "" : ", check"}</span>`
            : x.element
              ? ""
              : '<span class="sev warning">not recognised</span>';
        const off = mapped?.ignore;
        return `<tr><td><input type="checkbox" data-pick="${esc(n)}" ${picked.has(n) ? "checked" : ""}></td>
          <td>${esc(n)} ${tag}${h && !mapped ? `<div class="status">${esc(h.why)}</div>` : ""}</td>
          <td><input type="text" list="map-elements" data-map="${esc(n)}" data-key="element" value="${esc(m.element)}" placeholder="Pile(5)" ${off ? "disabled" : ""}></td>
          <td>${comboSelect(`data-map="${esc(n)}" data-key="combination" ${off ? "disabled" : ""}`, m.combination, "Pick one")}</td>
          <td><label><input type="checkbox" data-map="${esc(n)}" data-key="ignore" ${off ? "checked" : ""}> leave out</label></td>
          <td>${h && !mapped ? `<button class="quiet" data-accept="${esc(n)}">Accept</button>` : ""}${mapped ? `<button class="quiet" data-unmap="${esc(n)}">Undo</button>` : ""}</td></tr>`;
      })
      .join("");
    const waiting = pending().length;
    box.innerHTML = `<h2>Sheet mapping</h2>
      <p class="status" style="margin-top:0">Which element and combination each sheet holds. Sheets whose names are not written as
        &lt;Element&gt;-&lt;Combination&gt;, or whose combination is spelled differently from the other sheets, get a suggestion:
        accept it, correct it, or leave the sheet out. Combinations are the section's load combinations above. Apply saves the project and checks the workbook again.</p>
      ${waiting ? `<div class="panel row"><span>${waiting} sheet(s) have a suggested mapping.</span><button id="map-accept-all">Accept all suggestions</button></div>` : ""}
      <div class="panel scroll"><table><tr><th></th><th>Sheet</th><th>Element</th><th>Combination</th><th></th><th></th></tr>
        ${rows || '<tr><td colspan="6">Every sheet is named as expected.</td></tr>'}</table>
      <div class="row" style="margin-top:10px">
        <label><input type="checkbox" id="map-all" ${showAll ? "checked" : ""}> show all ${sheets.length} sheets</label>
        <span class="status">Selected: ${picked.size}</span>
        <input type="text" list="map-elements" id="bulk-element" placeholder="Element for selected" ${picked.size ? "" : "disabled"}>
        ${comboSelect(`id="bulk-combo" ${picked.size ? "" : "disabled"}`, "", "Combination for selected")}
        <button class="quiet" id="bulk-set" ${picked.size ? "" : "disabled"}>Set</button>
        <button class="quiet" id="bulk-out" ${picked.size ? "" : "disabled"}>Leave out</button>
      </div>
      <div class="row" style="margin-top:10px"><button id="map-apply">Apply mapping</button><span class="status" id="map-status"></span></div></div>
      <datalist id="map-elements">${known.map((e) => `<option value="${esc(e)}">`).join("")}</datalist>
      `;
    const entry = (n) => {
      if (!p.sheet_map[n]) {
        const x = sheets.find((q) => q.name === n);
        const h = hints[n];
        p.sheet_map[n] = { element: h?.element || x?.element || "", combination: h?.combination || x?.combination || "", ignore: false };
      }
      return p.sheet_map[n];
    };
    box.querySelectorAll("[data-map]").forEach((inp) => {
      const n = inp.dataset.map;
      if (inp.type === "checkbox") inp.onchange = () => { entry(n).ignore = inp.checked; markDirty(); draw(); };
      else inp.onchange = () => { entry(n)[inp.dataset.key] = inp.value.trim(); markDirty(); draw(); };
    });
    box.querySelectorAll("input[data-pick]").forEach((c) => (c.onchange = () => {
      c.checked ? picked.add(c.dataset.pick) : picked.delete(c.dataset.pick);
      draw();
    }));
    box.querySelectorAll("[data-accept]").forEach((b) => (b.onclick = () => { accept(b.dataset.accept); markDirty(); draw(); }));
    box.querySelectorAll("[data-unmap]").forEach((b) => (b.onclick = () => { delete p.sheet_map[b.dataset.unmap]; markDirty(); draw(); }));
    const all = box.querySelector("#map-accept-all");
    if (all) all.onclick = () => { pending().forEach(accept); markDirty(); draw(); };
    box.querySelector("#map-all").onchange = (e) => { showAll = e.target.checked; draw(); };
    box.querySelector("#bulk-set").onclick = () => {
      const el = box.querySelector("#bulk-element").value.trim();
      const co = box.querySelector("#bulk-combo").value.trim();
      picked.forEach((n) => {
        const m = entry(n);
        m.ignore = false;
        if (el) m.element = el;
        if (co) m.combination = co;
      });
      picked.clear();
      markDirty();
      draw();
    };
    box.querySelector("#bulk-out").onclick = () => {
      picked.forEach((n) => (entry(n).ignore = true));
      picked.clear();
      markDirty();
      draw();
    };
    box.querySelector("#map-apply").onclick = async () => {
      const st = box.querySelector("#map-status");
      st.textContent = "Saving…";
      await save();
      if (state.errors?.length) {
        st.textContent = state.errors.map((e) => e.msg).join(" ");
        return;
      }
      st.textContent = "Checking the workbook again…";
      await refresh();
    };
  };
  if (!sheets.some(needs) && !sheets.length) {
    box.innerHTML = "";
    return;
  }
  draw();
}

// Load multipliers: a factor on the straining actions of chosen sheets (X, Y, Z untouched).
function renderFactors(data) {
  const box = document.getElementById("factors");
  if (!box) return;
  const p = sec();
  p.load_factors ??= [];
  const sheets = data.sheets.filter((x) => x.combination && x.raw_rows);
  const combos = data.combinations.map((c) => c.name);
  const byCombo = (c) => sheets.filter((x) => x.combination === c).map((x) => x.name);
  const open = new Set();
  // Changes are made on a copy and used only when Apply is pressed.
  const copy = () => JSON.parse(JSON.stringify(p.load_factors));
  let draft = copy();
  let message = "";
  const pending = () => JSON.stringify(draft) !== JSON.stringify(p.load_factors);
  const showState = () => {
    const button = box.querySelector("#factor-apply");
    const st = box.querySelector("#factor-status");
    if (!button) return;
    button.disabled = !pending();
    box.querySelector("#factor-undo").hidden = !pending();
    st.className = `status${pending() ? " unsaved" : ""}`;
    st.textContent = pending() ? "Not applied yet: press Apply multiplier to use it (leaving this tab drops the change)." : message;
  };
  const touched = () => {
    message = "";
    showState();
  };
  const describe = (rules) => {
    const used = rules.filter((r) => r.sheets.length && Number(r.factor) !== 1);
    if (!used.length) return "Applied: no load multiplier; the straining actions are used as uploaded.";
    const comboOf = new Map(sheets.map((x) => [x.name, x.combination]));
    const parts = used.map((r) => {
      const cs = [...new Set(r.sheets.map((n) => comboOf.get(n)).filter(Boolean))];
      const which = cs.length && cs.length <= 3 ? ` ${cs.join(", ")}` : "";
      return `×${r.factor} on ${r.sheets.length}${which} sheet${r.sheets.length === 1 ? "" : "s"}${r.note ? ` (${r.note})` : ""}`;
    });
    return `Applied: ${parts.join("; ")}. Results designed with these sheets were deleted: design again.`;
  };
  const draw = () => {
    const taken = (i) => new Set(draft.flatMap((r, j) => (j === i ? [] : r.sheets)));
    box.innerHTML = `<h2>Load multipliers</h2>
      <p class="status" style="margin-top:0">Multiply the straining actions of chosen sheets, e.g. 1.35 on the Set B sheets. X, Y and Z are not changed.
        Press Apply multiplier to use your changes (Apply decisions is only for the warnings). It is used when you design and in the
        sheet pile wall export; the stored workbook and the sheet view keep the uploaded values. Changing it deletes the results designed with those sheets.</p>
      ${draft
        .map((r, i) => {
          const other = taken(i);
          const comboBoxes = combos
            .map((c) => {
              const names = byCombo(c).filter((n) => !other.has(n));
              const on = names.filter((n) => r.sheets.includes(n)).length;
              return `<label><input type="checkbox" data-rule="${i}" data-combo="${esc(c)}" ${names.length && on === names.length ? "checked" : ""} ${names.length ? "" : "disabled"}> ${esc(c)}${on && on < names.length ? ` (${on}/${names.length})` : ""}</label>`;
            })
            .join("");
          const sheetBoxes = sheets
            .map((x) => `<label><input type="checkbox" data-rule="${i}" data-sheet="${esc(x.name)}" ${r.sheets.includes(x.name) ? "checked" : ""} ${other.has(x.name) ? "disabled" : ""}> ${esc(x.name)}</label>`)
            .join("");
          return `<div class="panel" style="margin-bottom:12px">
            <div class="row"><div class="field" style="width:160px"><label>Multiplier</label><div class="inputwrap"><input type="number" step="any" data-rule="${i}" data-key="factor" value="${r.factor}"></div></div>
              <div class="field" style="flex:1;min-width:200px"><label>Note</label><div class="inputwrap"><input type="text" data-rule="${i}" data-key="note" value="${esc(r.note)}" placeholder="e.g. Set B to design values"></div></div>
              <button class="danger" data-remove="${i}">Remove</button></div>
            <div class="field full" style="margin-top:10px"><label>Combinations (all elements)</label><div class="checks">${comboBoxes}</div></div>
            <details style="margin-top:8px" data-rule="${i}" ${open.has(i) ? "open" : ""}><summary>${r.sheets.length} sheet(s) selected; choose sheets one by one</summary><div class="checks" style="margin-top:8px">${sheetBoxes}</div></details>
          </div>`;
        })
        .join("")}
      <div class="row"><button class="quiet" id="add-factor">Add multiplier</button>
        <button id="factor-apply">Apply multiplier</button><button class="quiet" id="factor-undo" hidden>Discard changes</button>
        <span class="status" id="factor-status"></span></div>
      ${factorSheetTable(data, sheets, p.load_factors)}`;
    box.querySelectorAll("details[data-rule]").forEach((d) => (d.ontoggle = () => {
      const i = Number(d.dataset.rule);
      if (d.open) open.add(i);
      else open.delete(i);
    }));
    box.querySelector("#add-factor").onclick = () => {
      draft.push({ factor: 1.35, sheets: [], note: "" });
      draw();
    };
    box.querySelectorAll("[data-remove]").forEach((b) => (b.onclick = () => {
      draft.splice(Number(b.dataset.remove), 1);
      draw();
    }));
    box.querySelectorAll("input[data-key]").forEach((inp) => (inp.oninput = () => {
      const r = draft[Number(inp.dataset.rule)];
      r[inp.dataset.key] = inp.dataset.key === "factor" ? (inp.value === "" ? inp.value : Number(inp.value)) : inp.value;
      touched();
    }));
    box.querySelectorAll("input[data-combo]").forEach((inp) => (inp.onchange = () => {
      const i = Number(inp.dataset.rule);
      const r = draft[i];
      const names = byCombo(inp.dataset.combo).filter((n) => !taken(i).has(n));
      r.sheets = inp.checked ? [...new Set([...r.sheets, ...names])] : r.sheets.filter((n) => !names.includes(n));
      draw();
    }));
    box.querySelectorAll("input[data-sheet]").forEach((inp) => (inp.onchange = () => {
      const r = draft[Number(inp.dataset.rule)];
      r.sheets = inp.checked ? [...r.sheets, inp.dataset.sheet] : r.sheets.filter((n) => n !== inp.dataset.sheet);
      draw();
    }));
    box.querySelector("#factor-undo").onclick = () => {
      draft = copy();
      draw();
    };
    box.querySelector("#factor-apply").onclick = async (e) => {
      const button = e.target;
      const st = box.querySelector("#factor-status");
      const bad = draft.find((r) => !(Number(r.factor) > 0));
      if (bad) return (st.textContent = "Each multiplier needs a number above 0.");
      button.disabled = true;
      button.textContent = "Applying…";
      st.className = "status";
      st.textContent = "Saving the multipliers…";
      const before = p.load_factors;
      p.load_factors = JSON.parse(JSON.stringify(draft)).map((r) => ({ ...r, factor: Number(r.factor) }));
      markDirty();
      await save();
      if (state.errors?.length) {
        p.load_factors = before;
        markDirty(); // back to what was applied before
        message = `Not applied: ${state.errors.map((x) => x.msg).join(" ")}`;
      } else {
        message = describe(p.load_factors);
        draft = copy();
      }
      draw();
    };
    showState();
  };
  draw();
}

// Sheet by sheet: which tabs the design multiplies (applied multipliers only), set against the values
// as uploaded, and tabs to check: uploaded after their multiplier was applied, or left out while the
// other tabs of their combination are multiplied.
function factorSheetTable(data, sheets, rules) {
  if (!sheets.length) return "";
  const ruleOf = new Map();
  for (const r of rules || []) for (const n of r.sheets) ruleOf.set(n, r);
  const multiplied = new Map(); // combination -> factors used on it
  for (const x of sheets) {
    const r = ruleOf.get(x.name);
    if (r && Number(r.factor) !== 1) multiplied.set(x.combination, [...(multiplied.get(x.combination) || []), r.factor]);
  }
  const ms = (t) => (t ? Date.parse(t) : NaN);
  const rows = sheets.map((x) => {
    const r = ruleOf.get(x.name);
    const f = r ? Number(r.factor) : 1;
    const src = data.sources?.[x.name] ?? (data.file ? { file: data.file, at: data.uploaded_at, guess: true } : null);
    const peak = x.peak_moment;
    const flags = [];
    if (r && f !== 1 && src && !src.guess && ms(src.at) > ms(r.applied_at))
      flags.push(`Uploaded after ×${f} was applied: check this tab is not already multiplied in the Excel.`);
    if (f === 1 && multiplied.has(x.combination))
      flags.push(`Not multiplied, while other ${x.combination} tabs are ×${multiplied.get(x.combination)[0]}.`);
    const used = peak ? `${fmt(peak.value * f, 0)}` : "—";
    return `<tr${flags.length ? ' class="flag"' : ""}><td>${esc(x.name)}</td><td>${esc(x.combination)}</td>
      <td>${src ? `${esc(src.file)}<br><span class="status">${esc(when(src.at))}</span>` : "—"}</td>
      <td>${f !== 1 ? `<strong>×${f}</strong>` : "as uploaded"}</td>
      <td class="num">${peak ? `${fmt(peak.value, 0)} <span class="status">${esc(peak.action.replace("_", ""))}</span>` : "—"}</td>
      <td class="num">${f !== 1 ? `<strong>${used}</strong>` : used}</td>
      <td>${flags.map(esc).join("<br>") || (f !== 1 ? "Multiplied once, at design" : "")}</td></tr>`;
  });
  const n = sheets.filter((x) => (ruleOf.get(x.name)?.factor ?? 1) !== 1).length;
  const flagged = rows.filter((r) => r.startsWith('<tr class="flag"')).length;
  return `<details class="factor-detail" ${flagged ? "open" : ""}><summary><strong>Sheet by sheet:</strong> ${n} of ${sheets.length} tabs multiplied at design${
    flagged ? `, <strong>${flagged} to check</strong>` : ""}</summary>
    <p class="status">${n} of ${sheets.length} tabs are multiplied at design, as applied above. The stored workbook keeps the Excel's own values,
      so the largest moment here is the one in your Excel, and "Used in design" is it times the multiplier. A tab replaced later keeps its multiplier and is never multiplied twice.
      ${flagged ? `<strong>${flagged} tab(s) to check</strong> are highlighted.` : ""}</p>
    <div class="scroll"><table class="factor-sheets"><thead><tr><th>Tab</th><th>Combination</th><th>Uploaded from</th><th>Multiplier</th>
      <th class="num">Largest |M| in the Excel</th><th class="num">Used in design</th><th>Check</th></tr></thead>
      <tbody>${rows.join("")}</tbody></table></div></details>`;
}

const LABEL = { ok: "OK", warning: "Check", error: "Error", missing: "—" };
// Readable names for the kinds of findings.
const ISSUE_TITLE = {
  duplicate_rows_removed: "Duplicate rows",
  blank_rows_removed: "Blank rows",
  repeated_header_removed: "Stacked tables",
  unnamed_columns: "Columns without a header",
  empty_sheet: "Empty sheets",
  non_numeric: "Text where a number should be",
  missing_values: "Empty cells",
  node_coordinates_differ: "Node at two places",
  node_values_differ: "Node with two sets of forces",
  content_above_header: "Rows above the header",
  outside_envelope: "Values outside their min and max",
  unexpected_units: "Unexpected units",
  identical_combinations: "Duplicate sheets (identical combinations)",
  rejected: "Removed from the design",
  node_set_differs: "Different nodes between combinations",
  point_count_differs: "Unusual number of points",
  missing_combination: "Missing combinations",
  missing_qp: "No QP combination",
};
const issueTitle = (code) => ISSUE_TITLE[code] || pretty(code);
// A tidy-up done without asking (blank rows, stacked tables); findings that wait for a decision are
// reviewed, not listed as done.
const isCleanup = (i) => i.severity === "info" && (!i.choices || i.choices.before === "auto");

function renderReport(d) {
  for (const k of ["error", "warning"]) document.getElementById("n-" + k).textContent = d.counts[k];
  document.getElementById("n-info").textContent = d.issues.filter(isCleanup).length;
  const combos = d.combinations.map((c) => c.name);
  let h = "<tr><th>Element</th>" + combos.map((c) => `<th>${esc(c)}</th>`).join("") + "</tr>";
  for (const e of d.elements) {
    h += `<tr><th>${esc(e)}</th>` + combos.map((c) => {
      const s = d.coverage[e][c];
      return `<td class="cell ${s}">${LABEL[s]}</td>`;
    }).join("") + "</tr>";
  }
  document.getElementById("coverage").innerHTML = h;
  const row = (i) => {
    const where = i.sheet || [i.element, i.combination].filter(Boolean).join(" ");
    const rows = i.rows.length ? `<div class="rows">Excel rows ${i.rows.join(", ")}${i.rows.length >= 20 ? "…" : ""}</div>` : "";
    return `<tr><td><span class="sev ${i.severity}">${i.severity}</span></td><td>${esc(where)}</td><td>${esc(i.message)}${rows}</td></tr>`;
  };
  const head = "<tr><th></th><th>Sheet</th><th>What was found</th></tr>";
  const problems = d.issues.filter((i) => !isCleanup(i));
  document.getElementById("problems").innerHTML = problems.length ? head + problems.map(row).join("") : "<tr><td>No problems found.</td></tr>";
  document.getElementById("cleanups").innerHTML = head + d.issues.filter(isCleanup).map(row).join("");
  const axes = d.axes || [];
  document.getElementById("axes-block").hidden = !axes.length;
  document.getElementById("axes").innerHTML = "<tr><th>Element</th><th></th><th>Finding</th></tr>" + axes
    .map((a) => `<tr><th>${esc(a.element)}</th><td><span class="sev ${a.clear ? "ok" : "warning"}">${a.clear ? "clear" : "unclear"}</span></td><td>${esc(a.text)}${a.sign_text ? `<br>${esc(a.sign_text)}` : ""}</td></tr>`)
    .join("");
  document.getElementById("report").hidden = false;
}

// ---------------------------------------------------------------- warnings review
// Every warning with its two choices, named for what they keep or remove (stored as "accept" and
// "reject"). Nothing that changes the numbers happens before it is chosen; removing a sheet (or
// element) leaves it out of the design.
const rowRanges = (rows, most = 12) => {
  const out = [];
  let a = null, b = null;
  for (const r of [...rows].sort((x, y) => x - y)) {
    if (a == null) a = b = r;
    else if (r === b + 1) b = r;
    else { out.push(a === b ? `${a}` : `${a}–${b}`); a = b = r; }
  }
  if (a != null) out.push(a === b ? `${a}` : `${a}–${b}`);
  return out.length > most ? `${out.slice(0, most).join(", ")} and ${out.length - most} more` : out.join(", ");
};

// What Apply did, said once in the review bar drawn after it.
let REVIEW_NOTE = null;

function appliedText(changes) {
  const groups = new Map();
  for (const i of changes) {
    const d = sec().review[i.id];
    const k = `${i.code}|${d || ""}`;
    if (!groups.has(k)) groups.set(k, { code: i.code, d, items: [] });
    groups.get(k).items.push(i);
  }
  const parts = [...groups.values()].map(({ code, d, items }) => {
    const what = d ? (d === "accept" ? items[0].choices.yes : items[0].choices.no) : "back to review";
    const rows = code === "duplicate_rows_removed" && d === "accept" ? `, ${items.reduce((a, i) => a + i.rows.length, 0)} rows` : "";
    return `${issueTitle(code)}: ${what} (${items.length}${rows})`;
  });
  return `Applied: ${parts.join("; ")}.`;
}

function renderReview(data, refresh, url) {
  const table = document.getElementById("problems");
  const bar = document.getElementById("review-bar");
  if (!table || !bar) return;
  const p = sec();
  const sid = p.id;
  p.review ??= {};
  const reviewable = (i) => i.choices && i.choices.before !== "auto";
  // A removed sheet shows as its warning under Decided, not as a new error.
  const list = data.issues.filter((i) => i.code !== "rejected" && (reviewable(i) || (i.severity !== "info" && !i.choices)));
  const kinds = [...new Set(list.map((i) => i.code))];
  const expanded = new Set(); // kinds shown in full; long ones show their first few
  const chosen = (i) => p.review[i.id] || null;
  // Decided and applied (the last check read the workbook with this decision): out of the list.
  const resolved = (i) => reviewable(i) && chosen(i) && chosen(i) === (i.decision || null);
  const changes = () => list.filter((i) => reviewable(i) && chosen(i) !== (i.decision || null));
  const choiceName = (i, d) => (d === "accept" ? i.choices.yes : i.choices.no);
  const note = REVIEW_NOTE?.sid === sid ? REVIEW_NOTE.text : "";
  REVIEW_NOTE = null;
  const draw = (message = note) => {
    const open = list.filter((i) => reviewable(i) && !resolved(i) && !chosen(i)).length;
    const done = list.filter(resolved);
    const n = changes().length;
    bar.hidden = false;
    bar.innerHTML = `<div class="panel">
      <div class="row">
        <span><b>${open}</b> to review · ${done.length} decided${n ? ` · <b>${n}</b> not applied yet` : ""}</span>
        <button id="review-apply" ${n ? "" : "disabled"}>${n ? `Apply ${n} decision${n === 1 ? "" : "s"}` : "Apply decisions"}</button>
        <button class="quiet" id="review-recheck">Check again</button>
        <span style="flex:1"></span>
        <a class="quiet-link" href="${url}/workbook/checker.xlsx">Download the Checker (Excel)</a></div>
      <p class="status" id="review-status">${esc(message || (n ? "Press Apply to use your choices; until then the section reads the workbook as before." : "Choose Keep or Remove on each warning, then press Apply."))}</p>
      <div data-slot="review-${esc(sid)}"></div></div>
      <p class="status">Click a sheet name to open it at the flagged rows: fix cells there, or in the Checker in Excel and upload it again with “Replace matching tabs”.</p>`;
    const row = (i) => {
      const d = chosen(i);
      const where = i.sheet
        ? `<a href="#" data-open="${esc(i.sheet)}">${esc(i.sheet)}</a>`
        : esc([i.element, i.combination].filter(Boolean).join(" "));
      const show3d = !i.sheet && i.element ? ` <a href="#" data-3d="${esc(i.element)}">3D view</a>` : "";
      const buttons = reviewable(i)
        ? `<button class="quiet ${d === "accept" ? "on" : ""}" data-id="${i.id}" data-d="accept" title="${esc(i.choices.accept)}">${esc(i.choices.yes)}</button>
          ${i.choices.reject ? `<button class="quiet ${d === "reject" ? "on" : ""}" data-id="${i.id}" data-d="reject" title="${esc(i.choices.reject)}">${esc(i.choices.no)}</button>` : ""}`
        : "";
      return `<tr class="${d ? `decided-${d}` : ""}"><td><span class="sev ${i.severity}">${i.severity}</span></td>
        <td>${where}${show3d}${i.rows.length ? `<div class="rows">rows ${esc(rowRanges(i.rows))}</div>` : ""}</td>
        <td>${esc(i.message)}</td><td class="nowrap">${buttons}</td></tr>`;
    };
    const rows = kinds
      .map((code) => {
        const items = list.filter((i) => i.code === code && !resolved(i));
        if (!items.length) return "";
        const c = items[0].choices;
        const head = `<tr class="kind"><th colspan="3">${esc(issueTitle(code))} (${items.length})
          ${c ? `<span class="status">${c.yes === c.accept ? "" : `${esc(c.yes)}: `}${esc(c.accept)}${c.reject ? ` · ${esc(c.no)}: ${esc(c.reject)}` : ""}</span>` : '<span class="status">Fix it in the workbook, the sheet mapping or the load combinations.</span>'}</th>
          <th class="nowrap">${c ? `<button class="quiet" data-all="${esc(code)}" data-d="accept">${esc(c.yes)} (all)</button>${c.reject ? ` <button class="quiet" data-all="${esc(code)}" data-d="reject">${esc(c.no)} (all)</button>` : ""}` : ""}</th></tr>`;
        const shown = items.length > 5 && !expanded.has(code) ? items.slice(0, 3) : items;
        const more = shown.length < items.length ? `<tr><td></td><td colspan="3"><a href="#" data-more="${esc(code)}">Show all ${items.length}</a></td></tr>` : "";
        return head + shown.map(row).join("") + more;
      })
      .join("");
    const decidedRows = done
      .map((i) => `<tr><td>${esc(issueTitle(i.code))}</td><td>${i.sheet ? `<a href="#" data-open="${esc(i.sheet)}">${esc(i.sheet)}</a>` : esc([i.element, i.combination].filter(Boolean).join(" "))}</td>
        <td>${esc(i.message)}</td><td class="nowrap"><b>${esc(choiceName(i, chosen(i)))}</b> <button class="quiet small" data-undo="${i.id}" title="Put it back to review">Undo</button></td></tr>`)
      .join("");
    table.innerHTML =
      (rows ? `<tr><th></th><th>Sheet</th><th>What was found</th><th></th></tr>${rows}` : `<tr><td>${list.length ? "Nothing left to review." : "No problems found."}</td></tr>`) +
      (done.length ? `<tr><td colspan="4"><details class="decided"><summary>Decided and applied (${done.length})</summary>
        <table>${decidedRows}</table></details></td></tr>` : "");
    const changed = () => {
      markDirty();
      draw("Changed: press Apply to use it.");
    };
    table.querySelectorAll("[data-id]").forEach((b) => (b.onclick = () => {
      const id = b.dataset.id;
      if (p.review[id] === b.dataset.d) delete p.review[id];
      else p.review[id] = b.dataset.d;
      changed();
    }));
    table.querySelectorAll("[data-all]").forEach((b) => (b.onclick = () => {
      for (const i of list) if (i.code === b.dataset.all && reviewable(i) && !resolved(i)) p.review[i.id] = b.dataset.d;
      changed();
    }));
    table.querySelectorAll("[data-undo]").forEach((b) => (b.onclick = () => {
      delete p.review[b.dataset.undo];
      changed();
    }));
    table.querySelectorAll("[data-more]").forEach((a) => (a.onclick = (e) => {
      e.preventDefault();
      expanded.add(a.dataset.more);
      draw();
    }));
    table.querySelectorAll("[data-open]").forEach((a) => (a.onclick = (e) => {
      e.preventDefault();
      openSheet(url, a.dataset.open, refresh);
    }));
    table.querySelectorAll("[data-3d]").forEach((a) => (a.onclick = (e) => {
      e.preventDefault();
      state.pick3d = a.dataset["3d"];
      location.hash = tabHash("view3d");
    }));
    const slot = `review-${sid}`;
    const run = async (button, fresh) => {
      const st = bar.querySelector("#review-status");
      const pending = changes();
      bar.querySelectorAll("#review-apply, #review-recheck").forEach((b) => (b.disabled = true));
      button.textContent = fresh ? "Checking…" : "Applying…";
      st.textContent = "Saving your choices…";
      await save();
      if (state.errors?.length) {
        st.textContent = `Could not save: ${state.errors.map((e) => e.msg).join(" ")}`;
        return draw(st.textContent);
      }
      st.textContent = fresh ? "Checking the workbook again…" : "Checking the workbook with your choices…";
      try {
        const job = await fetchWorkbook(url, {
          title: fresh ? "Checking the workbook again" : "Applying your decisions",
          slot,
          fresh,
          doneText: "Done.",
        });
        dismissJob(job);
        const d = job.data;
        const left = d.issues.filter((i) => reviewable(i) && i.code !== "rejected" && !sec().review[i.id]);
        REVIEW_NOTE = {
          sid,
          text: fresh
            ? `Checked again: ${left.length} warning(s) to review.`
            : pending.length ? appliedText(pending) : "Your choices are applied.",
        };
        if (sec()?.id === sid && document.body.contains(bar)) await refresh(d);
      } catch (e) {
        draw(`Could not ${fresh ? "check" : "apply"}: ${e.message}`);
      }
    };
    bar.querySelector("#review-apply").onclick = (e) => run(e.target, false);
    bar.querySelector("#review-recheck").onclick = (e) => run(e.target, true);
    drawJobs();
  };
  draw();
}

// A sheet as read, in a grid: flagged rows highlighted with their reasons, cells editable. Saving
// sends the changed cells; the sheet is read again and the workbook checked again.
// The view can show only the flagged rows (or one kind of warning) and sort by a column; that only
// changes what is shown, rows keep their Excel numbers, and the sheet opens in Excel order each time.
async function openSheet(url, name, refresh, start = null) {
  document.getElementById("sheet-view")?.remove();
  const box = document.createElement("div");
  box.id = "sheet-view";
  box.className = "sheet-view";
  document.body.append(box);
  const edits = new Map();
  const view = { show: "all", code: null, sort: null, desc: false };
  const colName = (i) => { let s = ""; for (i += 1; i > 0; i = Math.floor((i - 1) / 26)) s = String.fromCharCode(65 + ((i - 1) % 26)) + s; return s; };
  const load = async (from) => {
    const q = new URLSearchParams({ name });
    if (from != null) q.set("start", from);
    if (view.show !== "all") q.set("show", view.show);
    if (view.code) q.set("code", view.code);
    if (view.sort != null) {
      q.set("sort", view.sort);
      if (view.desc) q.set("desc", "true");
    }
    if (!box.firstChild) box.innerHTML = `<div class="sheet-card"><p class="status">Opening ${esc(name)}…</p></div>`;
    const d = await api(`${url}/workbook/sheet?${q}`);
    const locked = state?.project.locked && d.editable;
    if (locked) d.editable = false; // read only while the model is locked
    const flagged = d.flagged_rows;
    const body = d.rows
      .map((r, k) => {
        const n = d.numbers[k];
        const f = d.flags[n];
        const sev = f ? (f.some((x) => x.severity === "error") ? "error" : f.some((x) => x.severity === "warning") ? "warning" : "info") : "";
        const cells = Array.from({ length: d.width }, (_, c) => `<td ${d.editable ? 'contenteditable="true"' : ""} data-r="${n}" data-c="${c}">${esc(r[c] ?? "")}</td>`).join("");
        return `<tr class="${sev ? `flag ${sev}` : ""}" ${f ? `title="${esc(f.map((x) => x.message).join("\n"))}"` : ""}><th>${n}</th>${cells}</tr>`;
      })
      .join("");
    const last = d.numbers.at(-1) ?? 0;
    const next = d.view ? null : flagged.find((r) => r > last);
    const prev = d.view ? null : [...flagged].reverse().find((r) => r <= d.start);
    const kinds = [...new Map(d.issues.filter((i) => i.rows.length).map((i) => [i.code, i])).values()];
    const arrow = (c) => (view.sort === c ? (view.desc ? " ▼" : " ▲") : "");
    const shown = d.rows.length ? `${d.view ? "" : "rows "}${d.start + 1}–${d.start + d.rows.length} of ${d.total}${d.view ? " shown" : ""}` : "no rows";
    box.innerHTML = `<div class="sheet-card">
      <div class="row"><h3 style="margin:0">${esc(name)}</h3><span class="status">${shown}</span>
        <span style="flex:1"></span>
        ${prev ? `<button class="quiet" data-go="${Math.max(0, prev - 6)}">Previous flagged</button>` : ""}
        ${next ? `<button class="quiet" data-go="${Math.max(0, next - 6)}">Next flagged</button>` : ""}
        ${d.start > 0 ? `<button class="quiet" data-go="${Math.max(0, d.start - 200)}">Up</button>` : ""}
        ${d.start + d.rows.length < d.total ? `<button class="quiet" data-go="${d.start + 200}">Down</button>` : ""}
        ${d.editable ? `<button id="sheet-save" ${edits.size ? "" : "disabled"}>Save edits</button>` : ""}
        <button class="quiet" id="sheet-close">Close</button></div>
      <div class="row sheet-tools"><label for="sheet-show">Show</label>
        <select id="sheet-show">
          <option value="all">All rows</option>
          <option value="flagged">Only rows with warnings (${flagged.length})</option>
          ${kinds.map((i) => `<option value="code:${esc(i.code)}">Only: ${esc(issueTitle(i.code))} (${i.rows.length})</option>`).join("")}
        </select>
        <span class="status">Click a column letter to sort. This only changes the view: rows keep their Excel numbers, and the sheet opens in Excel order next time.</span>
        ${view.sort != null || view.show !== "all" ? '<button class="quiet small" id="sheet-reset">Excel order</button>' : ""}</div>
      ${d.issues.length ? `<ul class="sheet-issues">${d.issues.map((i) => `<li><span class="sev ${i.severity}">${i.severity}</span> ${esc(i.message)}${i.rows.length ? ` <span class="rows">rows ${esc(rowRanges(i.rows))}</span>` : ""}</li>`).join("")}</ul>` : ""}
      ${d.editable ? "" : locked ? '<p class="status">Read only while the model is locked. Press Unlock to edit to change cells.</p>' : '<p class="status">This workbook was uploaded before its rows were kept, so this shows the rows as cleaned and cannot be edited. Upload it again to edit here.</p>'}
      <p class="status" id="sheet-status">${edits.size ? `${edits.size} cell(s) changed: Save edits to read the sheet again.` : d.editable ? "Click a cell to change it. Highlighted rows are the flagged ones; hover for the reason." : ""}</p>
      <div class="sheet-grid"><table><tr><th></th>${Array.from({ length: d.width }, (_, c) => `<th class="sortable" data-sort="${c}" title="Sort by this column">${colName(c)}${arrow(c)}${d.header?.[c] != null && d.header[c] !== "" ? `<div class="head-text">${esc(d.header[c])}</div>` : ""}</th>`).join("")}</tr>${body || `<tr><td colspan="${d.width + 1}" class="status">No rows to show.</td></tr>`}</table></div></div>`;
    const pick = box.querySelector("#sheet-show");
    pick.value = view.code ? `code:${view.code}` : view.show;
    pick.onchange = () => {
      const v = pick.value;
      view.show = v === "all" ? "all" : "flagged";
      view.code = v.startsWith("code:") ? v.slice(5) : null;
      load(null);
    };
    box.querySelector("#sheet-reset")?.addEventListener("click", () => {
      Object.assign(view, { show: "all", code: null, sort: null, desc: false });
      load(null);
    });
    box.querySelectorAll("th[data-sort]").forEach((th) => (th.onclick = () => {
      const c = Number(th.dataset.sort);
      // Each click: ascending, descending, then back to Excel order.
      if (view.sort !== c) Object.assign(view, { sort: c, desc: false });
      else if (!view.desc) view.desc = true;
      else Object.assign(view, { sort: null, desc: false });
      load(null);
    }));
    box.querySelector("#sheet-close").onclick = () => {
      if (edits.size && !confirm("Close without saving your cell edits?")) return;
      box.remove();
    };
    box.querySelectorAll("[data-go]").forEach((b) => (b.onclick = () => load(Number(b.dataset.go))));
    const saveBtn = box.querySelector("#sheet-save");
    box.querySelectorAll("td[contenteditable]").forEach((td) => {
      const kept = edits.get(`${td.dataset.r}|${td.dataset.c}`); // an edit made before the view changed
      if (kept) {
        td.textContent = kept.value;
        td.classList.add("edited");
      }
      td.oninput = () => {
        edits.set(`${td.dataset.r}|${td.dataset.c}`, { row: Number(td.dataset.r), col: Number(td.dataset.c), value: td.textContent });
        td.classList.add("edited");
        saveBtn.disabled = false;
        box.querySelector("#sheet-status").textContent = `${edits.size} cell(s) changed: Save edits to read the sheet again.`;
      };
    });
    if (saveBtn)
      saveBtn.onclick = async () => {
        saveBtn.disabled = true;
        box.querySelector("#sheet-status").textContent = "Saving and checking the workbook again…";
        try {
          await api(`${url}/workbook/sheet?${new URLSearchParams({ name })}`, { method: "PUT", body: JSON.stringify({ edits: [...edits.values()] }) });
          edits.clear();
          await refresh();
          await load(d.start);
          box.querySelector("#sheet-status").textContent = "Saved. The sheet was read again; the results are out of date until you redesign.";
        } catch (e) {
          box.querySelector("#sheet-status").textContent = e.message;
          saveBtn.disabled = false;
        }
      };
    if (!d.view) box.querySelector("tr.flag")?.scrollIntoView({ block: "center" });
  };
  await load(start);
}

// ---------------------------------------------------------------- design tab
// The main bars' volume over the element's concrete in %, not the heaviest section's ratio.
function overallRatio(st) {
  if (!st) return null;
  if (st.ratio_pct != null) return st.ratio_pct;
  if (st.longitudinal_kg && st.concrete_m3) return (100 * st.longitudinal_kg) / 7850 / st.concrete_m3;
  if (st.kg_per_m3 != null && st.links_kg_per_m == null && st.longitudinal_kg_per_m == null) return (100 * st.kg_per_m3) / 7850;
  return null;
}

// Results designed before their inputs changed say so, naming what changed.
function staleHtml(res) {
  if (!res?.changed?.length) return "";
  return `<div class="stale"><strong>Inputs updated since this design:</strong> ${esc(res.changed.join(", "))}.
    These results are out of date: design the elements marked out of date again.</div>`;
}

const DESIGNED = new Set(["pile", "combi_wall", "front_beam", "rear_beam", "transverse_beam", "slab"]);

// What the Design tab can design: every pile, combi wall, beam and slab, and the sheet pile wall's
// governing sets.
const DESIGN_ORDER = { pile: 0, combi_wall: 0, sheet_pile_wall: 1, front_beam: 2, rear_beam: 2, transverse_beam: 2, slab: 3 };
const designUnits = (section) =>
  Object.entries(section.elements)
    .filter(([, e]) => e.kind in DESIGN_ORDER)
    .sort(([, a], [, b]) => DESIGN_ORDER[a.kind] - DESIGN_ORDER[b.kind]) // the order the server designs them in
    .map(([n]) => n)
    .concat(state?.project?.approach ? [APPROACH] : []); // the project's approach slab, last

async function renderDesignTab(host) {
  const url = secUrl();
  const section = sec();
  const units = designUnits(section);
  state.designPick ??= {};
  host.innerHTML = `<div class="panel">
      <div class="row pick-row" id="design-pick"></div>
      <div class="row">
      <button id="run-design" ${units.length ? "" : "disabled"}>Design</button>
      <span class="status" id="design-status">${units.length ? "" : "Add pile, combi wall, beam or slab elements first."}</span>
      </div>
      <div class="row pick-row" id="export-pick" hidden></div>
      <div class="row export-links">
      <a class="quiet-link" id="cages" href="${url}/design/cages.json" hidden>Download bars for Revit (JSON: pile and infill cages, beams, slab)</a>
      <a class="quiet-link" id="sets" href="${url}/design/governing.xlsx" hidden>Download governing sets for AdSec (Excel)</a>
      <a class="quiet-link" id="ads" href="${url}/design/adsec.zip" hidden>Download AdSec 8.3 files (.ads: pile parts, combi infill, beams, slab strips)</a>
      <span class="reports" id="drawings" hidden>Drawings:
        <a class="quiet-link" data-draw="dxf" href="#">AutoCAD (DXF)</a>
        <a class="quiet-link" data-draw="crm" href="#">Revit (.crm drawings file)</a>
        <a class="quiet-link" href="#" id="drawing-help">How to open in Revit</a>
      </span>
      <span class="reports" id="reports" hidden>Report:
        <select id="report-detail"><option value="summary">Summary</option><option value="detailed">Detailed</option></select>
        <a class="quiet-link" data-fmt="docx" href="#">Word</a>
        <a class="quiet-link" data-fmt="pdf" href="#">PDF</a>
        <a class="quiet-link" data-fmt="xlsx" href="#">Excel</a>
      </span>
      ${Object.values(section.elements).some((e) => e.kind === "sheet_pile_wall") ? `<a class="quiet-link" href="${url}/spw.xlsx">Download SPW straining actions (Excel)</a>` : ""}
      </div>
      <div data-slot="design-${esc(section.id)}"></div>
    </div><div class="panel" id="displacements" data-free></div><div class="panel" id="deflections" data-free></div><div id="design-out"></div>`;
  displacementsPanel(document.getElementById("displacements"));
  deflectionsPanel(document.getElementById("deflections"));
  let stale = [];
  const run = document.getElementById("run-design");
  const drawPick = () => {
    const picked = state.designPick[section.id]?.filter((n) => units.includes(n)) ?? null;
    const box = document.getElementById("design-pick");
    if (!box) return;
    box.innerHTML = `<span>Design</span>
      <label class="chip"><input type="checkbox" data-pick="*" ${picked ? "" : "checked"}> All</label>
      ${units
        .map((n) => `<label class="chip${stale.includes(n) ? " stale-chip" : ""}" title="${stale.includes(n) ? "Out of date" : ""}">
          <input type="checkbox" data-pick="${esc(n)}" ${picked?.includes(n) ? "checked" : ""}> ${esc(n)}</label>`)
        .join("")}
      ${stale.length ? `<button class="quiet small" id="pick-stale">Only the ${stale.length} out of date</button>` : ""}`;
    box.querySelectorAll("[data-pick]").forEach(
      (c) =>
        (c.onchange = () => {
          const n = c.dataset.pick;
          const now = new Set(picked || []);
          if (n === "*") now.clear();
          else if (c.checked) now.add(n);
          else now.delete(n);
          state.designPick[section.id] = now.size && now.size < units.length ? [...now] : null;
          drawPick();
        })
    );
    const only = box.querySelector("#pick-stale");
    if (only)
      only.onclick = () => {
        state.designPick[section.id] = stale.filter((n) => units.includes(n));
        drawPick();
      };
    const n = picked?.length;
    run.textContent = busyWith(`design-${section.id}`) ? "Designing…" : n ? `Design ${n} element${n === 1 ? "" : "s"}` : "Design all elements";
    run.disabled = !units.length || busyWith(`design-${section.id}`);
  };
  drawPick();
  // A run shows only its own new results, element by element as they come; the earlier ones are
  // hidden meanwhile.
  const out = document.getElementById("design-out");
  const start = async (picked) => {
    if (state.dirty) await save();
    if (state.errors?.length) return;
    if (busyWith(`design-${section.id}`)) return;
    out.innerHTML = `<p class="status">Designing. Each element's new results appear here as it is done; earlier results are hidden until then.</p>`;
    const job = designJob(section, picked, (res, done) => {
      if (!document.body.contains(out)) return;
      const view = { ...res };
      for (const k of ["piles", "combi_walls", "beams", "slabs", "sheet_pile_walls", "approach_slabs"]) view[k] = (res[k] || []).filter((e) => done.has(e.element));
      view.stale = [];
      view.changed = [];
      drawResults(view);
      out.insertAdjacentHTML("afterbegin", `<p class="status">New results so far: ${done.size} element${done.size === 1 ? "" : "s"}. Still designing…</p>`);
    });
    drawPick();
    await job;
    drawPick();
  };
  state.runDesign = (names) => start(names);
  run.onclick = () => start(state.designPick[section.id]?.filter((n) => units.includes(n)) ?? null);
  drawJobs();
  if (busyWith(`design-${section.id}`)) {
    out.innerHTML = `<p class="status">Designing. The new results appear here when it is done.</p>`;
    return;
  }
  if (!state.project.locked) {
    // Unlocked to edit: the inputs may no longer match the last results, so they are not shown.
    try {
      await api(`${url}/design`);
      out.innerHTML = `<div class="panel"><p style="margin:0"><strong>Results are hidden while the model is unlocked.</strong>
        Design again to see them; the model locks again when you do.</p></div>`;
    } catch {
      /* not designed yet */
    }
    return;
  }
  try {
    const res = await api(`${url}/design`);
    stale = res.stale || [];
    drawPick();
    renderResults(res);
  } catch {
    /* not designed yet */
  }
}

// Design a section's elements (all, or the ones picked) as a job: a few elements per request, each
// element with its own bar; the others keep their results.
async function designJob(section, chosen, onResults) {
  const pid = state.project.id;
  const url = `${ROOT}/api/projects/${pid}/sections/${section.id}`;
  const key = `design-${pid}-${section.id}`;
  const names = chosen || designUnits(section);
  const job = newJob({
    kind: "design",
    title: `Designing ${section.name}${chosen ? ` (${names.length} of ${designUnits(section).length})` : ""}`,
    slot: `design-${section.id}`,
    home: `#/project/${pid}/design/${section.id}`,
    steps: names.map((n) => ({ label: n, name: n, state: "waiting" })),
  });
  const step = (n) => {
    let s = job.steps.find((x) => x.name === n);
    if (!s) job.steps.push((s = { label: n, name: n, state: "waiting" }));
    return s;
  };
  job.stop = async () => {
    job.stopped = true;
    await api(`${ROOT}/api/progress/${key}/stop`, { method: "POST" }).catch(() => {});
  };
  // Which element the server is on, between its answers.
  const poll = setInterval(async () => {
    try {
      const p = await api(`${ROOT}/api/progress/${key}`);
      const m = /^Designing (.+)$/.exec(p.step || "");
      const s = m && job.steps.find((x) => x.name === m[1]);
      if (s && s.state === "waiting") {
        for (const x of job.steps) if (x.state === "running") Object.assign(x, { state: "done", fraction: 1 });
        s.state = "running";
        drawJobs();
      }
    } catch {
      /* between requests */
    }
  }, 1200);
  let ask = chosen;
  let res = null;
  const done = new Set();
  try {
    job.steps[0] && (job.steps[0].state = "running");
    drawJobs();
    for (;;) {
      if (job.stopped) throw new Error("Stopped.");
      res = await again(() => api(`${url}/design`, { method: "POST", body: JSON.stringify({ elements: ask, budget_s: 3 }) }));
      const all = ["piles", "combi_walls", "beams", "slabs", "sheet_pile_walls", "approach_slabs"].flatMap((k) => res[k] || []);
      for (const n of res.designed) {
        const s = step(n);
        const r = all.find((x) => x.element === n);
        s.state = "done";
        s.fraction = 1;
        if (!r) s.note = (res.skipped || []).some((m) => m.startsWith(`${n}:`)) ? "No results in the workbook" : "Nothing to design";
        else if (r.passed === false) {
          s.note = "Unsafe";
          s.bad = true;
        } else s.note = "OK";
      }
      res.designed.forEach((n) => done.add(n));
      onResults?.(res, done);
      res.left.forEach(step);
      const next = res.left.find((n) => step(n).state !== "done");
      if (next) step(next).state = "running";
      drawJobs();
      if (!res.left.length) break;
      ask = res.left;
    }
    for (const s of job.steps) if (s.state !== "done") Object.assign(s, { state: "done", note: "Nothing to design" });
    const bad = job.steps.filter((s) => s.bad).length;
    jobDone(job, "done", `Designed ${job.steps.length} element${job.steps.length === 1 ? "" : "s"}${bad ? `, ${bad} unsafe` : ", all safe"}.`);
    if (state?.project.id === pid) {
      state.project.locked = true;
      if (location.hash.startsWith(`#/project/${pid}/`)) route();
    }
  } catch (e) {
    const on = job.steps.find((s) => s.state === "running");
    const cut = GATEWAY.includes(e.status) || e instanceof TypeError;
    const why = cut && on ? `The server stopped answering while designing ${on.label}; it may take longer than the host allows. Try designing it on its own.` : e.message;
    jobDone(job, job.stopped ? "stopped" : "failed", job.stopped ? "Stopped. Elements already designed keep their new results." : `Failed: ${why}`);
  } finally {
    clearInterval(poll);
  }
}

// Every time is shown in Cairo, whatever the browser's own zone; stamps carry their offset.
const CAIRO = new Intl.DateTimeFormat("en-CA", { timeZone: "Africa/Cairo", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const when = (iso) => {
  if (!iso) return "";
  const text = String(iso);
  const d = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(text) ? text : text + "Z");
  if (isNaN(d)) return text.replace("T", " ").slice(0, 16);
  const p = Object.fromEntries(CAIRO.formatToParts(d).map((x) => [x.type, x.value]));
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}`;
};
const fmt = (v, d = 0) => (v == null || !isFinite(v) ? "–" : (Math.abs(v) < 0.5 * 10 ** -d ? 0 : Number(v)).toLocaleString("en-GB", { maximumFractionDigits: d, minimumFractionDigits: d }));

// The results of the elements picked in the "Show" row (all by default); the exports always cover
// every element.
function renderResults(full) {
  const out = document.getElementById("design-out");
  if (!out) return;
  const kinds = ["piles", "combi_walls", "beams", "slabs", "sheet_pile_walls", "approach_slabs"];
  const names = [...new Set(kinds.flatMap((k) => (full[k] || []).map((e) => e.element)))]; // a corner berth's parts: once
  state.designShow ??= {};
  let shown = (state.designShow[sec().id] || []).filter((n) => names.includes(n));
  if (!shown.length) shown = null;
  const view = { ...full };
  for (const k of kinds) view[k] = (full[k] || []).filter((e) => !shown || shown.includes(e.element));
  drawResults(view, full);
  if (names.length < 2) return;
  const bar = document.createElement("div");
  bar.className = "panel row show-bar";
  bar.innerHTML = `<span>Show</span>
    <label class="chip"><input type="checkbox" data-show="*" ${shown ? "" : "checked"}> All</label>
    ${names.map((n) => `<label class="chip"><input type="checkbox" data-show="${esc(n)}" ${shown?.includes(n) ? "checked" : ""}> ${esc(n)}</label>`).join("")}`;
  out.prepend(bar);
  bar.querySelectorAll("input").forEach(
    (c) =>
      (c.onchange = () => {
        const n = c.dataset.show;
        const now = new Set(shown || []);
        if (n === "*") now.clear();
        else if (c.checked) now.add(n);
        else now.delete(n);
        state.designShow[sec().id] = [...now];
        renderResults(full);
      })
  );
}

// How the drawings open in AutoCAD and Revit, with the one-time Revit script downloads.
function drawingHelp() {
  let box = document.getElementById("drawing-help-box");
  if (box) {
    box.remove();
    return;
  }
  box = document.createElement("div");
  box.id = "drawing-help-box";
  box.className = "panel";
  box.innerHTML = `<h2>Reinforcement drawings in AutoCAD and Revit</h2>
    <p>Every drawing is plain 2D detail lines: pile cage sections and elevations, beam sections, slab plans of each face and
      direction, 1 m slab cuts and shear link zones. Each bar size is on its own layer (AutoCAD) or line style (Revit); the names
      are on the Project tab under <em>Drawing names</em>. Until your office names are set there, they are placeholders (REBAR-16 …).</p>
    <h3>AutoCAD</h3>
    <p>Download <em>AutoCAD (DXF)</em> and open it (File › Open, file type DXF). The views sit side by side in model space, 1 unit = 1 mm.</p>
    <h3>Revit</h3>
    <ol>
      <li>Download <em>Revit (.crm drawings file)</em> for the elements ticked.</li>
      <li>Paste <a class="quiet-link" href="${ROOT}/api/revit/triton-draw-bars.txt">the Triton DevKit code</a> into your DevKit code
        runner in Revit and run it (Revit 2021 and later). It asks for the .crm file, lists its views to tick, and asks where: a
        drafting view for each, or the view that is open, at a point you click.</li>
      <li>Drafting views are named "Triton - section - view". Drawing a new file redraws the same views, so views already on
        sheets stay there.</li>
    </ol>
    <p class="status">The same code as a <a class="quiet-link" href="${ROOT}/api/revit/triton-addin.zip">Revit add-in</a> (build once in
      Visual Studio) gives a Triton button on the Add-Ins tab instead.</p>
    <p class="status">Line styles your project lacks are made by the add-in, and it lists them when it finishes. Where a Revit family type is set
      for a bar size, cut bars are placed as that detail component and bars along the view as the line-based one.</p>`;
  document.getElementById("drawings").closest(".panel").after(box);
}

// One pick of elements for every download in the export row: All, or only the ticked ones
// (?elements=A,B on each link), so one element can be exported without the whole section.
function wireExportPick(names, steelOnly, anyCages) {
  const box = document.getElementById("export-pick");
  if (!box) return;
  const id = sec().id;
  const url = secUrl();
  state.exportPick ??= {};
  const detail = document.getElementById("report-detail");
  const draw = () => {
    const picked = state.exportPick[id]?.filter((n) => names.includes(n)) ?? null;
    box.hidden = names.length < 2;
    box.innerHTML = `<span>Export</span>
      <label class="chip"><input type="checkbox" data-pick="*" ${picked ? "" : "checked"}> All</label>
      ${names
        .map((n) => `<label class="chip"><input type="checkbox" data-pick="${esc(n)}" ${picked?.includes(n) ? "checked" : ""}> ${esc(n)}</label>`)
        .join("")}
      <span class="status">${
        picked ? `The downloads below hold ${picked.length === 1 ? esc(picked[0]) : `these ${picked.length} elements`} only.` : "The downloads below hold every designed element."
      }</span>`;
    box.querySelectorAll("[data-pick]").forEach(
      (c) =>
        (c.onchange = () => {
          const n = c.dataset.pick;
          const now = new Set(picked || []);
          if (n === "*") now.clear();
          else if (c.checked) now.add(n);
          else now.delete(n);
          state.exportPick[id] = now.size && now.size < names.length ? names.filter((x) => now.has(x)) : null;
          draw();
        })
    );
    const q = picked ? `elements=${encodeURIComponent(picked.join(","))}` : "";
    const link = (path, more = "") => {
      const query = [more, q].filter(Boolean).join("&");
      return `${url}/design/${path}${query ? `?${query}` : ""}`;
    };
    // The sheet pile wall has only governing sets and reports: no bars, AdSec files or drawings.
    const bars = anyCages && !(picked && picked.every((n) => steelOnly.has(n)));
    const setHref = (elId, path) => {
      const a = document.getElementById(elId);
      if (!a) return;
      a.href = link(path);
      a.hidden = !bars;
    };
    setHref("cages", "cages.json");
    setHref("ads", "adsec.zip");
    const sets = document.getElementById("sets");
    if (sets) sets.href = link("governing.xlsx");
    document.querySelectorAll("#reports a[data-fmt]").forEach((a) => (a.href = link(`report.${a.dataset.fmt}`, `detail=${detail.value}`)));
    const dr = document.getElementById("drawings");
    if (dr) {
      dr.hidden = !bars;
      dr.querySelectorAll("a[data-draw]").forEach((a) => (a.href = link(`drawings.${a.dataset.draw}`)));
    }
  };
  if (detail) detail.onchange = draw;
  draw();
}

function drawResults(res, full = res) {
  const out = document.getElementById("design-out");
  const walls = res.combi_walls || [];
  const spws = res.sheet_pile_walls || [];
  const beams = res.beams || [];
  const slabs = res.slabs || [];
  const anyCages = full.piles.length || (full.combi_walls || []).length || (full.beams || []).length || (full.slabs || []).length;
  const link = document.getElementById("cages");
  if (link) link.hidden = !anyCages;
  const ads = document.getElementById("ads");
  if (ads) ads.hidden = !anyCages;
  const reports = document.getElementById("reports");
  if (reports) reports.hidden = false;
  const sets = document.getElementById("sets");
  if (sets) sets.hidden = !anyCages && !(full.sheet_pile_walls || []).length;
  const draw = document.getElementById("drawings");
  if (draw) draw.hidden = !anyCages;
  // In the order of the Design row's tick boxes.
  const order = designUnits(sec());
  const exported = ["piles", "combi_walls", "sheet_pile_walls", "beams", "slabs", "approach_slabs"]
    .flatMap((k) => (full[k] || []).map((x) => x.element))
    .sort((a, b) => (order.indexOf(a) + 1 || 1e9) - (order.indexOf(b) + 1 || 1e9));
  wireExportPick(exported, new Set((full.sheet_pile_walls || []).map((x) => x.element)), anyCages);
  checkingPanel(exported, full.run_at);
  if (draw) {
    document.getElementById("drawing-help").onclick = (e) => {
      e.preventDefault();
      drawingHelp();
    };
  }
  const rows = res.piles
    .map((p) => {
      const a = p.arrangement;
      const sh = p.shear;
      const kg = p.steel?.kg_per_m3 ?? p.curtailment?.steel_ratio_kg_m3 ?? p.steel_ratio_kg_m3;
      return `<tr><td>${esc(p.element)}</td><td>${a ? esc(a.label) : "–"}${p.user_set ? ' <span class="chip small-chip">set by you</span>' : ""}</td>
        <td class="cell ${p.utilisation <= 1 ? "ok" : "error"}">${fmt(p.utilisation, 2)}</td>
        <td>${sh ? esc(sh.zones[0].link) : "–"}</td>
        <td class="cell ${sh ? (sh.passed ? "ok" : "error") : ""}">${sh ? fmt(sh.utilisation, 2) : "–"}</td>
        <td class="cell ${p.cracks?.wk_mm == null ? "" : p.cracks.passed ? "ok" : "error"}">${p.cracks?.wk_mm == null ? "–" : `${fmt(p.cracks.wk_mm, 2)} / ${fmt(p.cracks.limit_mm, 2)} = ${fmt(p.cracks.wk_mm / p.cracks.limit_mm, 2)}`}</td>
        <td>${fmt(overallRatio(p.steel), 2)}%</td><td class="${p.reinforcement_ratio_pct > (p.spacing_limits?.max_ratio_pct ?? 4) + 1e-9 ? "bad" : "muted"}">${fmt(p.reinforcement_ratio_pct, 2)}%</td><td>${fmt(kg)}</td></tr>`;
    })
    .join("");
  out.innerHTML = `${staleHtml(full)}<p class="status">Designed ${esc(when(res.run_at))} (Cairo time).</p>
    ${res.skipped.map((s) => `<p class="status">${esc(s)}</p>`).join("")}
    ${(() => {
      const list = alerts(res).filter((a) => a.level !== "safe");
      return list.length ? `<div class="panel"><h3 style="margin-top:0">To look at</h3>${alertsHtml(list)}</div>` : "";
    })()}
    ${res.piles.length ? `<h2>Piles</h2><div class="panel scroll"><table><tr><th>Element</th><th>Bars at head</th><th>ULS N–M</th><th>Links at head</th><th>Shear</th><th title="Crack width over its limit">SLS (QP) crack mm</th><th title="Main bars over the whole pile, laps included">ρ overall</th><th>ρ at head</th><th>kg/m³ incl. links</th></tr>${rows}</table></div>` : ""}
    <div id="pile-cards"></div><div id="combi-cards"></div>
    ${beams.length ? `<h2>Beams</h2><div class="panel scroll"><table><tr><th>Element</th><th>b × h</th><th>Longitudinal bars</th><th>Links</th><th>Transverse bars (top / bottom)</th><th>Max util.</th><th>ρ overall</th><th>kg/m³</th></tr>
      ${beams.map((b) => `<tr><td>${esc(b.element)}</td><td>${fmt(b.width_mm)} × ${fmt(b.depth_mm)}</td><td>${b.cage ? esc(b.cage.label) : "–"}</td>
        <td>${b.shear?.link ? esc(b.shear.link.label) : "–"}</td><td>${b.transverse ? `${esc(b.transverse.top.label)} / ${esc(b.transverse.bottom.label)}` : "–"}</td>
        <td class="cell ${b.passed ? "ok" : "error"}">${fmt(b.utilisation, 2)}</td><td>${fmt(overallRatio(b.steel), 2)}%</td><td>${fmt(b.steel?.kg_per_m3)}</td></tr>`).join("")}</table></div>` : ""}
    <div id="beam-cards"></div>
    ${slabs.length ? "<h2>Slab</h2>" : ""}<div id="slab-cards"></div>
    ${spws.length ? `<h2>Sheet pile wall</h2><div id="spw-cards"></div>` : ""}
    ${(res.approach_slabs || []).length ? `<h2>Approach slab and ledge</h2><div id="approach-cards"></div>` : ""}`;
  for (const a of res.approach_slabs || []) document.getElementById("approach-cards").append(approachCard(a, { esc, fmt }));
  for (const w of spws) document.getElementById("spw-cards").append(spwWithSets(w));
  const cards = document.getElementById("pile-cards");
  for (const p of res.piles) cards.append(pileCard(p));
  const combi = document.getElementById("combi-cards");
  if (walls.length) combi.insertAdjacentHTML("beforeend", "<h2>Combi wall</h2>");
  for (const w of walls) combi.append(combiCard(w));
  const bc = document.getElementById("beam-cards");
  for (const b of beams) bc.append(beamCard(b));
  const sc = document.getElementById("slab-cards");
  for (const d of slabs) sc.append(slabCard(d));
  mountElementViews(res);
}

// ---------------------------------------------------------------- 3D
function v3dSlot(name) {
  return `<details class="v3d-details" open><summary>In 3D, with the directions of the actions</summary>
    <div class="v3d-slot" data-element="${esc(name)}"></div></details>`;
}

async function sectionGeometry() {
  // Cached per section; a new workbook upload clears it.
  if (state.geometry?.section !== sec().id) {
    let data = null;
    try {
      data = await api(`${secUrl()}/geometry`);
    } catch {
      /* no workbook yet */
    }
    state.geometry = { section: sec().id, data };
  }
  return state.geometry.data;
}

function resultTension(res) {
  const out = {};
  for (const k of ["piles", "combi_walls", "beams", "slabs"]) {
    for (const d of res?.[k] || []) if (d.tension?.points?.length) out[d.key || d.element] = d.tension;
  }
  return out;
}

// wk / limit bands for the 3D view's "Crack width" mode.
function resultCracks(res) {
  const out = {};
  for (const p of res?.piles || []) if (p.cracks?.bands?.length) out[p.element] = p.cracks.bands;
  for (const w of res?.combi_walls || []) if (w.infill?.cracks?.bands?.length) out[w.element] = w.infill.cracks.bands;
  for (const k of ["beams", "slabs"]) for (const d of res?.[k] || []) if (d.crack_bands?.length) out[d.key || d.element] = d.crack_bands;
  return out;
}

function resultBands(res) {
  const bands = {};
  for (const p of res?.piles || []) bands[p.element] = p.bands || [];
  for (const w of res?.combi_walls || []) bands[w.element] = w.bands || [];
  for (const b of res?.beams || []) bands[b.key || b.element] = b.bands || [];
  for (const d of res?.slabs || []) bands[d.key || d.element] = d.bands || [];
  for (const w of res?.sheet_pile_walls || []) {
    // The wall's largest Uf per level, in 0.5 m bands (the same all along the wall).
    const by = new Map();
    for (const [z, u] of (w.design?.designed || w.design?.as_plaxis)?.profile || []) {
      const k = Math.round(z * 2) / 2;
      by.set(k, Math.max(by.get(k) ?? 0, u));
    }
    bands[w.element] = [...by].map(([z, u]) => [0, 0, z, u]);
  }
  return bands;
}

async function mountElementViews(res) {
  const slots = [...document.querySelectorAll(".v3d-slot")];
  if (!slots.length) return;
  const geo = await sectionGeometry();
  if (!geo) {
    slots.forEach((s) => document.body.contains(s) && (s.innerHTML = '<p class="status">Upload the workbook to see the 3D view.</p>'));
    return;
  }
  const bands = resultBands(res);
  const tension = resultTension(res);
  const crack = resultCracks(res);
  for (const slot of slots) {
    if (!document.body.contains(slot)) continue; // redrawn meanwhile (a design run's new results)
    const name = slot.dataset.element;
    const el = geo.elements.find((e) => e.element === name);
    const view = new View3D(slot, { height: 380, compact: true });
    view.setScene({ elements: geo.elements, bands, tension, crack, selected: name, focus: name,
      arrows: directionArrows(el, geo.axes.find((a) => a.element === name)) });
  }
}

function alerts(res) {
  // Unsafe first, then close to the limit, then very safe.
  const out = [];
  const add = (level, name, text) => out.push({ level, name, text });
  const at = (g) => (g?.combination ? ` (${g.combination}, z ${fmt(g.z, 2)} m)` : "");
  for (const p of res.piles || []) {
    const u = p.utilisation;
    if (u == null) add("unsafe", p.element, "no cage carries the loads");
    else if (u > 1) add("unsafe", p.element, `N–M utilisation ${fmt(u, 2)}${at(p.governing)}: needs a stronger cage`);
    else if (u >= 0.95) add("limit", p.element, `N–M utilisation ${fmt(u, 2)}${at(p.governing)}: close to the limit`);
    else if (u < 0.5) add("safe", p.element, `N–M utilisation ${fmt(u, 2)}: very safe, could be lighter`);
    if (p.shear && !p.shear.passed) add("unsafe", p.element, `shear utilisation ${fmt(p.shear.utilisation, 2)}`);
    if (p.connection?.passed === false) add("unsafe", p.element, `casing connection utilisation ${fmt(p.connection.utilisation, 2)}`);
    if (p.casing?.tube?.passed === false) add("unsafe", p.element, `steel casing utilisation ${fmt(p.casing.tube.utilisation, 2)}`);
  }
  const noTop = (res.piles || []).filter((p) => p.section?.head_level_set === false).map((p) => p.element);
  if (noTop.length) add("limit", noTop.join(", "), "no top level set, so results inside the slab are included: set it on the Elements tab");
  for (const a of res.approach_slabs || []) {
    if (!a.passed) add("unsafe", a.element, `utilisation ${fmt(a.utilisation, 2)} (slab ${fmt(a.slab_utilisation, 2)}, ledge ${fmt(a.ledge?.utilisation, 2)}): see its card`);
    else if (a.shear?.links_mm2_per_m2) add("limit", a.element, `needs shear links near the ledge (${fmt(a.shear.links_mm2_per_m2)} mm²/m²), or a thicker slab`);
  }
  for (const w of res.combi_walls || []) {
    const u = w.infill?.utilisation;
    if (u > 1) add("unsafe", w.element, `infill N–M utilisation ${fmt(u, 2)}${at(w.infill.governing)}`);
    else if (u >= 0.95) add("limit", w.element, `infill N–M utilisation ${fmt(u, 2)}${at(w.infill.governing)}: close to the limit`);
    if (w.top_level_set === false) add("limit", w.element, "no top level set, so results inside the front beam are included: set it on the Elements tab");
    const t = w.tube?.utilisation;
    if (t > 1) add("unsafe", w.element, `steel tube utilisation ${fmt(t, 2)} (${esc(w.tube.governing?.check || "")})`);
    else if (t >= 0.95) add("limit", w.element, `steel tube utilisation ${fmt(t, 2)}: close to the limit`);
  }
  for (const w of res.sheet_pile_walls || []) {
    const d = w.design;
    if (!d) continue;
    if (d.error) { add("unsafe", w.element, d.error); continue; }
    const g = d.designed.governing;
    const why = `${d.check_titles[g.governs].toLowerCase()} Uf ${fmt(d.uf, 2)}${at(g)}`;
    if (d.uf > 1) add("unsafe", w.element, `${d.section}: ${why}`);
    else if (d.uf >= 0.95) add("limit", w.element, `${d.section}: ${why}, close to the limit`);
    else if (d.uf < 0.5) add("safe", w.element, `${d.section}: Uf ${fmt(d.uf, 2)}, very safe, a lighter section may do`);
    if (d.adjusted && !d.as_plaxis.ok) add("limit", w.element, `safe only with N or Q left out: with every Plaxis action Uf is ${fmt(d.as_plaxis.uf, 2)}`);
  }
  for (const b of res.beams || []) {
    const at2 = (g) => (g?.combination ? ` (${g.combination}, at ${fmt(g.s, 1)} m)` : "");
    const checks = [
      ["bending", b.bending?.utilisation, b.bending?.governing],
      ["shear and torsion", b.shear?.utilisation, b.shear?.governing],
      ["transverse bending", b.transverse?.utilisation, b.transverse?.governing],
      ["bollard ties", b.bollard?.utilisation, null],
      ["truss ties between king piles", b.truss?.utilisation, null],
    ];
    for (const [f, c] of Object.entries(b.cracks || {})) checks.push([`${f} crack width ${fmt(c.wk, 2)} mm of ${fmt(c.limit, 2)}`, c.wk / c.limit, c]);
    for (const [f, c] of Object.entries(b.restraint?.faces || {})) {
      if (isFinite(c.wk)) checks.push([`${f} restraint crack ${fmt(c.wk, 2)} mm of ${fmt(c.limit, 2)}`, c.wk / c.limit, null]);
    }
    if (b.utilisation == null) add("unsafe", (b.key || b.element), "no reinforcement passes");
    for (const [what, u, g] of checks) {
      if (u == null) continue;
      if (u > 1) add("unsafe", (b.key || b.element), `${what}: ${fmt(u, 2)}${at2(g)}`);
      else if (u >= 0.95) add("limit", (b.key || b.element), `${what}: ${fmt(u, 2)}${at2(g)}, close to the limit`);
    }
    if (b.utilisation != null && b.utilisation < 0.5) add("safe", (b.key || b.element), `max utilisation ${fmt(b.utilisation, 2)}: very safe`);
  }
  for (const d of res.slabs || []) {
    for (const [k, l] of Object.entries(d.layers || {})) {
      if (l.utilisation > 1) add("unsafe", (d.key || d.element), `${k.replace("_", " bars along ")}: ${fmt(l.utilisation, 2)} of the steel needed`);
    }
    const where = (q) => `${q.pile} (X ${fmt(q.plan_x ?? q.x, 1)}, Y ${fmt(q.plan_y ?? q.y, 1)})`;
    for (const q of d.punching || []) {
      if (!q.passed) add("unsafe", (d.key || d.element), `punching at ${where(q)}: ${q.vEd_face_MPa > q.vRd_max_MPa ? "crushes at the pile face" : `needs more than links can give (${fmt(q.kmax_ratio, 2)} × 1.5·vRd,c)`}${punchFix(q) ? `. Fix: ${punchFix(q)}` : ""}`);
    }
    const links = (d.punching || []).filter((q) => q.passed && q.needs_reinforcement);
    if (links.length === 1) add("limit", (d.key || d.element), `punching links needed at ${where(links[0])}, ${links[0].perimeters} perimeters`);
    else if (links.length) {
      const by = {};
      for (const q of links) by[q.pile] = (by[q.pile] || 0) + 1;
      const most = Math.max(...links.map((q) => q.perimeters));
      add("limit", (d.key || d.element), `punching links needed at ${links.length} piles (${Object.entries(by).map(([k, n]) => `${n} × ${k}`).join(", ")}), up to ${most} perimeters: see the punching table`);
    }
    if (d.shear && d.shear.passed === false) add("unsafe", (d.key || d.element), `shear per metre ${fmt(d.shear.utilisation, 2)}`);
    for (const [k, r] of Object.entries(d.restraint?.check && d.restraint.check !== "design" ? {} : d.restraint?.layers || {})) {
      if (!r.passed) add("unsafe", (d.key || d.element), `restraint crack ${k.replace("_", " ")} ${fmt(r.wk, 2)} mm of ${fmt(r.limit, 2)}`);
    }
  }
  // From the run itself, so the alert matches the results shown (older runs: the section as saved).
  const s = state?.project ? sec() : null;
  const zone = res.working_zone ?? (s && [s.x_min, s.x_max, s.y_min, s.y_max].some((v) => v != null));
  if (s && res.run_at && !zone)
    add("limit", s.name, "no working zone set, so results up to the model's boundaries are included: set it on the Sections tab");
  const rank = { unsafe: 0, limit: 1, safe: 2 };
  return out.sort((a, b) => rank[a.level] - rank[b.level]);
}

function alertsHtml(list) {
  const label = { unsafe: "Unsafe", limit: "Check", safe: "Very safe" };
  const sevClass = { unsafe: "error", limit: "warning", safe: "ok" };
  return `<ul class="alerts">${list.map((a) => `<li><span class="sev ${sevClass[a.level]}">${label[a.level]}</span> <b>${esc(a.name)}</b>: ${esc(a.text)}</li>`).join("")}</ul>`;
}

// ---------------------------------------------------------------- costing tab
// Quantities and cost of each designed section along its berth, and the sections side by side.
// The inputs (berth length, spacing or number of each element, lengths) are saved with each section;
// the unit prices are on the Project tab.
// ---------------------------------------------------------------- method tab
const optName = (o) => {
  const p = prettyOption(o);
  return p !== o ? p : String(o).replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
};

// A design module's description as HTML: paragraphs, "* " bullet lists and indented formulas.
function docHtml(text) {
  const inline = (t) => esc(t).replace(/``([^`]+)``/g, "<code>$1</code>");
  return text
    .split(/\n\s*\n/)
    .map((block) => {
      const lines = block.split("\n");
      if (lines.every((l) => /^ {4}/.test(l))) return `<pre class="formula">${esc(lines.map((l) => l.slice(4)).join("\n"))}</pre>`;
      if (/^\s*[*-] /.test(lines[0])) {
        const items = [];
        for (const l of lines) {
          if (/^\s*[*-] /.test(l)) items.push(l.replace(/^\s*[*-] /, ""));
          else if (items.length) items[items.length - 1] += " " + l.trim();
        }
        return `<ul>${items.map((i) => `<li>${inline(i)}</li>`).join("")}</ul>`;
      }
      const lead = lines.findIndex((l) => /^\s*[*-] /.test(l));
      if (lead > 0) return docHtml(lines.slice(0, lead).join("\n")) + docHtml(lines.slice(lead).join("\n"));
      if (/^\d+\. /.test(lines[0])) return `<p>${inline(lines.join(" "))}</p>`;
      return `<p>${inline(lines.map((l) => l.trim()).join(" "))}</p>`;
    })
    .join("");
}

function optionRows(options, withElement) {
  return options
    .map(
      (o) => `<tr>${withElement ? `<td>${esc(o.element)}${o.section && state.project.sections.length > 1 ? `<br><span class="status">${esc(o.section)}</span>` : ""}</td>` : ""}
        <td>${esc(o.title)}${o.description ? `<br><span class="status">${esc(o.description)}</span>` : ""}</td>
        <td><strong>${o.chosen === null ? "–" : esc(optName(o.chosen))}</strong></td>
        <td class="status">${o.choices.filter((c) => c !== o.chosen).map((c) => esc(optName(c))).join("<br>")}</td></tr>`,
    )
    .join("");
}

async function renderMethodTab(host) {
  host.innerHTML = '<p class="status">Loading…</p>';
  let m;
  try {
    m = await api(`${ROOT}/api/projects/${state.project.id}/method`);
  } catch (e) {
    host.innerHTML = `<p class="status">${esc(e.message)}</p>`;
    return;
  }
  const head = (el) => `<tr>${el ? "<th>Element</th>" : ""}<th>Setting</th><th>In use</th><th>Other choices</th></tr>`;
  const faces = m.kinds.find((k) => k.pile_faces)?.pile_faces;
  const facesBlock = faces
    ? `<h2>Slab moments at the pile faces</h2>
      <p class="sub">Plate results peak at the pile heads. Nodes inside a pile are always left out; the moments just outside it are then treated by one of these methods (slab setting "Moments at the pile faces"). After that, every method averages across the strip's width at each cut, for each combination, and designs the worst combination.</p>
      <div class="panel scroll"><table class="method-table"><tr><th>Method</th><th>What it does</th><th>Used by</th></tr>
      ${faces.methods
        .map((f) => {
          const users = faces.chosen.filter((c) => c.value === f.value).map((c) => c.element + (state.project.sections.length > 1 ? ` (${c.section})` : ""));
          return `<tr class="${users.length ? "method-on" : ""}"><td><strong>${esc(f.label)}</strong></td><td>${esc(f.text)}</td><td>${users.length ? esc(users.join(", ")) : '<span class="status">–</span>'}</td></tr>`;
        })
        .join("")}</table></div>`
    : "";
  host.innerHTML = `<p class="sub">How Triton designs each kind of element in this project, from the design code's own descriptions, and every choice of method with the one in use. Change a choice on Design settings (whole project) or on the element (Elements tab), then design again.</p>
    ${facesBlock}
    <h2>Whole project (Design settings)</h2>
    <div class="panel scroll"><table class="method-table">${head(false)}${optionRows(m.project_options, false)}</table></div>
    ${m.kinds
      .map(
        (k) => `<h2>${esc(k.kind)}</h2>
        <p class="status">${esc(k.elements.map((e) => e.element).join(", "))}</p>
        ${k.options.length ? `<div class="panel scroll"><table class="method-table">${head(true)}${optionRows(k.options, true)}</table></div>` : ""}
        ${k.topics.map((t) => `<details class="panel method-doc"><summary>${esc(t.title)}</summary>${docHtml(t.text)}</details>`).join("")}`,
      )
      .join("")}
    <h2>Across elements</h2>
    ${m.general.map((t) => `<details class="panel method-doc"><summary>${esc(t.title)}</summary>${docHtml(t.text)}</details>`).join("")}
    ${m.empty ? '<p class="status">No elements yet: add them on the Elements tab.</p>' : ""}`;
}

async function renderCostingTab(host) {
  let p = state.project;
  const cur = p.prices.currency || "";
  const money = (v) => (v == null ? "–" : `${fmt(v)}`);
  host.innerHTML = `<p class="sub">Quantities and cost along the berth, from each section's latest design. Unit prices are on the
      <a href="${tabHash("info")}">Project tab</a>; the numbers below change with them and with the inputs here.</p>
    <div class="panel row"><button id="cost-run">Work out the costs</button><span class="status" id="cost-status"></span></div>
    <div id="cost-out"></div>`;
  const out = document.getElementById("cost-out");
  const status = document.getElementById("cost-status");
  const steelNames = p.prices.steel_elements.map((x) => x.name).filter(Boolean);

  const input = (obj, key, placeholder, attrs = "") =>
    `<input type="number" step="any" min="0" ${attrs} data-obj="${esc(obj)}" data-key="${esc(key)}" placeholder="${esc(placeholder ?? "")}">`;
  const pick = (obj, key, value, none) =>
    `<select data-obj="${esc(obj)}" data-key="${esc(key)}"><option value="">${esc(none)}</option>${steelNames
      .map((n) => `<option ${n === value ? "selected" : ""}>${esc(n)}</option>`)
      .join("")}</select>`;

  // Fenders, bollards, crane rails and anything else the berth needs, each priced per item, per metre or
  // as a lump sum; the total above includes them.
  const itemsTable = (c, cs) => {
    const got = Object.fromEntries(c.rows.filter((r) => r.kind === "item").map((r) => [r.item, r]));
    const box = (i, key, placeholder = "", attrs = "") =>
      `<input type="number" step="any" min="0" ${attrs} data-item="${esc(c.section_id)}|${i}" data-key="${key}" placeholder="${esc(placeholder)}">`;
    const rows = (cs.items || [])
      .map((it, i) => {
        const r = got[i] || {};
        const each = it.unit === "each";
        return `<tr><td><input type="text" data-item="${esc(c.section_id)}|${i}" data-key="name" style="width:10em"></td>
          <td><select data-item="${esc(c.section_id)}|${i}" data-key="unit">${[["each", "each"], ["m", "per m"], ["lump", "lump sum"]]
            .map(([v, t]) => `<option value="${v}" ${it.unit === v ? "selected" : ""}>${t}</option>`)
            .join("")}</select></td>
          <td>${box(i, "price")}</td>
          <td>${each ? box(i, "spacing") : "–"}</td>
          <td>${each ? box(i, "count", r.count_auto ?? "", 'step="1"') + (it.count != null ? `<div class="hint">Given by you${r.count_auto != null ? `; automatic ${fmt(r.count_auto)}` : ""}</div><button class="small" data-item-auto="${esc(c.section_id)}|${i}">Use automatic</button>` : "") : "–"}</td>
          <td>${it.unit === "m" ? `${box(i, "runs", "1")}<div class="hint">lines</div>${box(i, "length", fmt(c.berth_length_m, 1))}<div class="hint">m each</div>` : "–"}</td>
          <td class="basis">${esc(r.basis || "")}${it.price == null ? '<div class="flag-bad">No price yet</div>' : ""}</td>
          <td>${money(r.cost)}</td><td>${money(r.cost_per_m)}</td>
          <td><button class="small quiet" data-item-drop="${esc(c.section_id)}|${i}" title="Take this item off">×</button></td></tr>`;
      })
      .join("");
    return `<h3 style="margin-top:14px">Other items</h3>
      <div class="scroll"><table class="cost"><tr><th>Item</th><th>Priced</th><th>Unit price (${esc(cur)})</th><th>Spacing (m)</th><th>Number</th><th>Length</th><th>Basis</th><th>Cost (${esc(cur)})</th><th>Per m</th><th></th></tr>
        ${rows}</table></div>
      <button class="small" data-item-add="${esc(c.section_id)}">Add an item</button>
      <p class="status">Spacings for fenders (20 m) and bollards (30 m) are common values, not from your drawings: change them. Items with no price are left out of the total.</p>`;
  };

  const draw = (data) => {
    const sections = data.sections;
    const costed = sections.filter((x) => x.totals);
    const cards = sections
      .map((c) => {
        const section = p.sections.find((x) => x.id === c.section_id);
        const cs = section.costing;
        const head = `<h2>${esc(c.section)}</h2>${staleHtml(c)}`;
        if (!c.rows.length && !c.totals)
          return `${head}<div class="panel"><p class="status">${esc(c.notes.join(" "))}</p>
            ${c.notes[0] === "Not designed yet." ? "" : `<div class="row"><label>Berth length (m) ${input(`${c.section_id}`, "berth_length", "")}</label></div>`}</div>`;
        const rows = c.rows
          .filter((r) => r.kind !== "item")
          .map((r) => {
            const key = `${c.section_id}|${r.element}`;
            const spaced = ["pile", "combi_wall"].includes(r.kind) || (r.kind === "beam" && r.count != null);
            const steel = r.kind === "combi_wall" || r.kind === "sheet_pile_wall";
            const e = cs.elements[r.element] || {};
            return `<tr><td>${esc(r.element)}</td>
              <td>${spaced ? input(key, "spacing", r.spacing_m != null ? fmt(r.spacing_m, 2) : "") : "–"}</td>
              <td>${spaced ? input(key, "count", r.count_auto ?? r.count ?? "", 'step="1"') + (e.count != null ? `<div class="hint">Given by you${r.count_auto != null ? `; automatic ${fmt(r.count_auto)}` : ""}</div><button class="small" data-auto="${esc(key)}">Use automatic</button>` : "") : "–"}</td>
              <td>${["approach_slab", "ledge"].includes(r.kind) ? (r.length_m != null ? fmt(r.length_m, 1) : "–") : input(key, "length", r.length_m != null ? fmt(r.length_m, 1) : "")}</td>
              <td>${steel ? pick(key, "steel_element", e.steel_element, r.kind === "sheet_pile_wall" ? "Its section, else the first AZ" : "Structural steel price") : "–"}${r.kind === "combi_wall" ? `<div class="hint">Intermediate sheets</div>${pick(key, "intermediate_element", e.intermediate_element, "None")}` : ""}</td>
              <td class="basis">${esc(r.basis)}${r.flags.map((f) => `<div class="${/above/.test(f) ? "flag-bad" : "flag-ok"}">${esc(f)}</div>`).join("")}${r.missing.length ? `<div class="flag-bad">Missing: ${esc(r.missing.join(", "))}</div>` : ""}</td>
              <td>${fmt(r.concrete_m3, 1)}</td><td>${fmt(r.rebar_t, 1)}</td><td>${fmt(r.steel_t, 1)}</td>
              <td>${money(r.cost)}</td><td>${money(r.cost_per_m)}</td></tr>`;
          })
          .join("");
        const t = c.totals;
        return `${head}<div class="panel">
          <div class="row">
            <label>Berth length (m) ${input(c.section_id, "berth_length", fmt(c.berth_length_m, 1))}</label>
            <label>Length the model covers (m) ${input(c.section_id, "model_length", c.model_length_m != null ? fmt(c.model_length_m, 1) : "")}</label>
          </div>
          <p class="status">Empty boxes use the value shown in grey, from the design. The number follows the berth length and spacing
            as you type them; a number you give yourself stays until you press Use automatic.</p>
          <div class="scroll"><table class="cost"><tr><th>Element</th><th>Spacing (m)</th><th>Number</th><th>Length (m)</th><th>Steel price</th><th>Basis</th>
            <th>Concrete m³</th><th>Rebar t</th><th>Steel t</th><th>Cost (${esc(cur)})</th><th>Per m</th></tr>${rows}
            <tr class="total"><td>Total</td><td colspan="5">${fmt(c.berth_length_m, 1)} m of berth${t.complete ? "" : " (incomplete: prices missing)"}</td>
              <td>${fmt(t.concrete_m3, 1)}</td><td>${fmt(t.rebar_t, 1)}</td><td>${fmt(t.steel_t, 1)}</td><td>${money(t.cost)}</td><td>${money(c.per_m.cost)}</td></tr></table></div>
          ${itemsTable(c, cs)}
          ${c.notes.map((n) => `<p class="status">${esc(n)}</p>`).join("")}</div>`;
      })
      .join("");
    const best = costed.filter((c) => c.totals.complete).sort((a, b) => a.per_m.cost - b.per_m.cost)[0];
    const line = (label, f) => `<tr><th>${label}</th>${costed.map((c) => `<td class="${c === best && label.startsWith("Cost per m") ? "cell ok" : ""}">${f(c)}</td>`).join("")}${costed.length > 1 ? `<td>${f(null)}</td>` : ""}</tr>`;
    const T = data.total;
    const compare = costed.length
      ? `<h2>Sections side by side</h2><div class="panel scroll"><table class="compare"><tr><th></th>${costed.map((c) => `<th>${esc(c.section)}</th>`).join("")}${costed.length > 1 ? "<th>All sections</th>" : ""}</tr>
        ${line("Berth length (m)", (c) => fmt(c ? c.berth_length_m : T.berth_length_m, 1))}
        ${line(`Cost (${esc(cur)})`, (c) => money(c ? c.totals.cost : T.cost))}
        ${line(`Cost per m (${esc(cur)}/m)`, (c) => money(c ? c.per_m.cost : T.berth_length_m ? T.cost / T.berth_length_m : null))}
        ${line("Concrete (m³/m)", (c) => fmt(c ? c.per_m.concrete_m3 : T.berth_length_m ? T.concrete_m3 / T.berth_length_m : null, 2))}
        ${line("Reinforcement (t/m)", (c) => fmt(c ? c.per_m.rebar_t : T.berth_length_m ? T.rebar_t / T.berth_length_m : null, 3))}
        ${line("Structural steel (t/m)", (c) => fmt(c ? c.per_m.steel_t : T.berth_length_m ? T.steel_t / T.berth_length_m : null, 3))}
        ${line("Prices complete", (c) => ((c ? c.totals.complete : T.complete) ? "Yes" : "No"))}
        </table></div>${best && costed.length > 1 ? `<p class="status">Lowest cost per metre: ${esc(best.section)}.</p>` : ""}`
      : "";
    out.innerHTML = compare + cards;
    // Fill the inputs from the saved costing and write edits back to it.
    out.querySelectorAll("[data-obj]").forEach((el) => {
      const [sid, name] = el.dataset.obj.split("|");
      const section = p.sections.find((x) => x.id === sid);
      const target = () => (name ? (section.costing.elements[name] ??= {}) : section.costing);
      const now = name ? section.costing.elements[name]?.[el.dataset.key] : section.costing[el.dataset.key];
      if (el.tagName === "INPUT") el.value = now ?? "";
      el.onchange = () => {
        const v = el.tagName === "SELECT" ? el.value : el.value === "" ? null : Number(el.value);
        target()[el.dataset.key] = el.dataset.key === "count" && v != null ? Math.round(v) : v;
        markDirty();
        rerun(); // the numbers follow straight away
      };
    });
    const itemOf = (ref) => {
      const [sid, i] = ref.split("|");
      const section = p.sections.find((x) => x.id === sid);
      section.costing.items ??= [];
      return [section.costing.items, +i];
    };
    out.querySelectorAll("[data-item][data-key]").forEach((el) => {
      const [list, i] = itemOf(el.dataset.item);
      const k = el.dataset.key;
      if (el.tagName === "INPUT") el.value = list[i]?.[k] ?? "";
      el.onchange = () => {
        const v = el.tagName === "SELECT" || el.type === "text" ? el.value : el.value === "" ? null : Number(el.value);
        list[i][k] = k === "count" && v != null ? Math.round(v) : k === "runs" && v == null ? 1 : v;
        markDirty();
        rerun();
      };
    });
    out.querySelectorAll("[data-item-auto]").forEach((b) => {
      b.onclick = () => {
        const [list, i] = itemOf(b.dataset.itemAuto);
        list[i].count = null;
        markDirty();
        rerun();
      };
    });
    out.querySelectorAll("[data-item-drop]").forEach((b) => {
      b.onclick = () => {
        const [list, i] = itemOf(b.dataset.itemDrop);
        list.splice(i, 1);
        markDirty();
        rerun();
      };
    });
    out.querySelectorAll("[data-item-add]").forEach((b) => {
      b.onclick = () => {
        const section = p.sections.find((x) => x.id === b.dataset.itemAdd);
        (section.costing.items ??= []).push({ name: "", unit: "each", price: null, spacing: null, count: null, runs: 1, length: null });
        markDirty();
        rerun();
      };
    });
    out.querySelectorAll("[data-auto]").forEach((b) => {
      b.onclick = () => {
        const [sid, name] = b.dataset.auto.split("|");
        const section = p.sections.find((x) => x.id === sid);
        if (section.costing.elements[name]) section.costing.elements[name].count = null;
        markDirty();
        rerun();
      };
    });
  };
  // Worked out again a moment after each change, keeping the cursor in the box it went to.
  let timer = null;
  const rerun = () => {
    clearTimeout(timer);
    status.textContent = "Working out…";
    timer = setTimeout(run, 250);
  };

  const run = async () => {
    if (state.dirty) await save();
    if (state.errors?.length) return;
    p = state.project; // saving replaces it
    status.textContent = "Working out…";
    try {
      const data = await api(`${ROOT}/api/projects/${p.id}/costing`);
      const a = document.activeElement;
      const ref = a?.dataset?.obj ? ["obj", a.dataset.obj] : a?.dataset?.item ? ["item", a.dataset.item] : null;
      const focus = ref ? `[data-${ref[0]}="${CSS.escape(ref[1])}"][data-key="${CSS.escape(a.dataset.key)}"]` : null;
      draw(data);
      if (focus) out.querySelector(focus)?.focus();
      status.textContent = "";
    } catch (e) {
      status.textContent = e.message;
    }
  };
  document.getElementById("cost-run").onclick = run;
  run();
}

async function renderView3dTab(host) {
  host.innerHTML = `<div class="v3d-layout"><div><div class="panel" id="v3d-main"></div></div>
    <div class="panel v3d-side" id="v3d-side"><p class="status">Loading…</p></div></div>`;
  const geo = await sectionGeometry();
  if (!geo) {
    document.getElementById("v3d-main").innerHTML = '<p class="status">Upload this section\'s workbook on the Workbook tab first.</p>';
    document.getElementById("v3d-side").innerHTML = "";
    return;
  }
  let res = null;
  try {
    res = await api(`${secUrl()}/design`);
  } catch {
    /* not designed yet */
  }
  const view = new View3D(document.getElementById("v3d-main"), { height: 560 });
  const bands = resultBands(res);
  const tension = resultTension(res);
  const crack = resultCracks(res);
  const max = {};
  for (const p of res?.piles || []) max[p.element] = p.utilisation;
  for (const w of res?.combi_walls || []) max[w.element] = w.utilisation;
  for (const b of res?.beams || []) max[b.key || b.element] = b.utilisation;
  for (const d of res?.slabs || []) max[d.key || d.element] = d.utilisation;
  for (const w of res?.sheet_pile_walls || []) if (w.design?.uf != null) max[w.element] = w.design.uf;
  let selected = state.pick3d && geo.elements.some((e) => e.element === state.pick3d) ? state.pick3d : null;
  state.pick3d = null;
  const show = () => {
    const el = geo.elements.find((e) => e.element === selected);
    view.setScene({ elements: geo.elements, bands, tension, crack, selected,
      arrows: selected ? directionArrows(el, geo.axes.find((a) => a.element === selected)) : [] });
    side.querySelectorAll("[data-pick]").forEach((b) => b.classList.toggle("on", b.dataset.pick === selected));
  };
  const side = document.getElementById("v3d-side");
  const list = alerts(res || {});
  side.innerHTML = `${staleHtml(res)}<h3 style="margin-top:0">Alerts</h3>
    ${res ? "" : '<p class="status">Not designed yet: run the design on the Design tab to colour the elements.</p>'}
    ${list.length ? alertsHtml(list) : res ? '<p class="status">Nothing unsafe or close to the limit.</p>' : ""}
    <h3>Elements</h3><p class="status">Pick one to see it alone with the directions of its actions.</p>
    <div class="v3d-picks">${geo.elements.map((e) => { const k = e.key || e.element; return `<button class="quiet" data-pick="${esc(e.element)}">
      <i style="background:${heat(max[k])}"></i>${esc(k)}<span>${max[k] == null ? "not designed" : fmt(max[k], 2)}</span></button>`; }).join("")}</div>`;
  side.querySelectorAll("[data-pick]").forEach((b) => (b.onclick = () => {
    selected = selected === b.dataset.pick ? null : b.dataset.pick;
    show();
  }));
  show();
}

// ---------------------------------------------------------------- Slabs
const LAYER_NAME = { bottom_x: "Bottom, bars along X", bottom_y: "Bottom, bars along Y", top_x: "Top, bars along X", top_y: "Top, bars along Y" };

function stripRowName(r) {
  return r.strip === "all" ? `${r.moment} – ${r.label}` : `${r.moment} – ${r.label} – ${r.strip === "column" ? "Column Strip" : "Field Strip"}`;
}

function stripTable(d) {
  // The calc report's slab table (Table 5-4): bars along the strips per station and strip; bars across
  // them (M22) as one mesh over the whole deck and zones where it needs more. Each row says what sets
  // its bars and can take the user's layers of bars.
  const sd = d.strip_design;
  const rows = sd.table || [];
  const faceName = (f) => (f === "bottom" ? "Bottom" : "Top");
  const setBy = (r) => (r.edit || Object.keys(r.set_by || {})).map((f) => `${faceName(f)}: ${esc(r.set_by?.[f] || "–")}`).join("<br>");
  const barsCell = (r, f) => {
    if (!r.bars[f]) return "–";
    const lines = layerLines(r.bar_layers?.[f]);
    const du = r.ductility?.[f] || {};
    const warn = du.warnings?.length ? ` <span class="over-mark" title="${esc(du.warnings.join("; "))}">⚠ x/d ${fmt(du.x_d, 2)}</span>` : "";
    // More than one layer: each layer on its own line instead of the one-line label.
    if (lines.length > 1) return `<span class="layer-lines" title="${esc(r.bars[f])}">${lines.map(esc).join("<br>")}</span>${warn}`;
    return `${esc(r.bars[f])}${warn}`;
  };
  const line = (r, i) => `<tr data-row="${i}"><td>${esc(stripRowName(r))}${r.user_set ? '<span class="user-chip">your bars</span>' : ""}</td>
    <td class="cell ${r.wk_mm == null || r.wk_mm <= r.wk_limit_mm ? "ok" : "error"}" title="${esc(r.qp?.face || "")} face">${r.wk_mm == null ? "–" : fmt(r.wk_mm, 3)}</td>
    <td>${r.qp?.M_kNm_per_m == null ? "–" : `${fmt(r.qp.M_kNm_per_m)} / ${fmt(r.qp.N_kN_per_m)}`}</td><td>${esc(r.qp?.combination || "–")}</td>
    <td class="cell ${r.ratio != null && r.ratio <= 1 ? "ok" : "error"}" title="${esc(r.face)} face">${fmt(r.ratio, 2)}</td><td>${fmt(r.M_kNm_per_m)}${r.N_kN_per_m == null ? "" : ` / ${fmt(r.N_kN_per_m)}`}</td><td>${fmt(r.MRd_kNm_per_m)}</td>
    <td>${esc(r.combination)}</td><td>${barsCell(r, "bottom")}</td><td>${barsCell(r, "top")}</td><td class="set-by">${setBy(r)}</td>
    <td><button class="quiet" data-bars="${i}">Change bars</button></td></tr>`;
  return `<h3 style="margin-top:18px">Slab design results</h3>
    <p class="status">As the calc report's slab table. Stations are metres from the ${esc(sd.from)}, on the sea side, increasing towards the rear. Bars along the strips (${esc(sd.along)}) are designed per station in column strips ${fmt(sd.column_width_m, 1)} m wide on the pile lines and field strips ${fmt(sd.field_width_m, 1)} m between them, all column strips together and all field strips together. Bars along the quay are one basic mesh over the whole deck, with zones of additional bars only where the deck needs more. "Set by" says which check chose each face's bars. Acting M is the size of the moment on the face that governs; the QP M and every N carry their sign (M sagging +, N compression +).</p>
    <p class="status">AdSec files and the Slabs sheet of the force-set Excel: per strip and direction, max N, min N, max M and min M over every combination for QP and for ULS, plus the set that governs each face's bars when it is not one of them. Each strip is a whole number of the tension face's mesh bars about 1 m wide (1050 mm for a 150 mm mesh, 1000 mm for 200 mm), with the forces per metre multiplied by width / 1000.</p>
    <div class="scroll"><table class="strip-table"><tr><th>Slab</th><th>Crack width (mm)</th><th>QP M / N for the crack width (kNm/m, kN/m)</th><th>QP combination</th><th>Ultimate M / M<sub>Rd</sub></th><th>Acting M / N (kNm/m, kN/m)</th><th>M<sub>Rd</sub> (kNm/m)</th><th>Governing combination</th><th>Bottom bars</th><th>Top bars</th><th>Set by</th><th></th></tr>
      ${rows.map(line).join("")}</table></div>`;
}

async function saveSlabStrips(name, change, status) {
  // Stations and bars set on the Design tab live on the section, so they can change while it is locked.
  const s = sec();
  s.slab_strips ??= {};
  const cur = { stations: null, bars: {}, ...(s.slab_strips[name] || {}) };
  const next = change(cur);
  if (next.stations == null && !Object.keys(next.bars || {}).length && next.spacing == null) delete s.slab_strips[name];
  else s.slab_strips[name] = next;
  status.textContent = "Saving…";
  markDirty();
  await save();
  if (state.errors?.length) return (status.textContent = state.errors.map((e) => e.msg).join(" "));
  status.textContent = `Checking ${name}…`;
  state.runDesign?.([name.split(" · ")[0]]); // a corner berth's part: its slab is designed again
}

function layerBuilder(d, r, f) {
  // One face of a row: layer 1 is the mesh at the cover (with bars between its bars), then layers 2, 3…
  // inside it (above the bottom mesh, below the top mesh), each with its own bar and spacing. Returns the editor's element and a reader of its state.
  const layer = r.layers[f];
  const lay = d.layers[layer] || {};
  const whole = (r.keys[f] || []).every((k) => k.endsWith("|mesh"));
  const bars = (state.project.design?.reinforcement?.bar_diameters || [10, 12, 16, 20, 25, 32]).filter((x) => x >= 10);
  const meshes = (lay.mesh_labels || []).filter((t) => !t.includes(" in "));
  const mesh0 = r.mesh?.[f] || { phi: lay.basic?.phi, spacing_mm: lay.basic?.spacing_mm };
  let mesh = { phi: mesh0.phi, s: mesh0.spacing_mm };
  let spec = (r.spec?.[f] || []).map((p) => (p ? [Number(p[0]), Number(p[1])] : null));
  if (!spec.length) spec = [null];
  const along = layer.endsWith("_y");
  const cover = lay.cover_mm ?? (f === "top" ? d.cover_top_mm : d.cover_bottom_mm);
  const box = document.createElement("div");
  box.className = "layer-builder";
  const opt = (v, cur, text = v) => `<option value="${v}" ${String(v) === String(cur) ? "selected" : ""}>${esc(text)}</option>`;
  const depths = () => {
    // As Triton places them: layer 1 at the cover, each next layer below with a clear gap of max(25, Ø).
    const rows = [];
    const n = Math.max(1, spec.length);
    for (let k = 0; k < n; k++) {
      const items = [];
      if (k === 0) items.push(mesh.phi);
      if (spec[k]) items.push(spec[k][0]);
      if (items.length) rows.push({ k, big: Math.max(...items) });
    }
    const shift = along ? Math.max(...rows.map((x) => x.big)) : 0;
    let at = cover + shift, prev = null;
    return rows.map((x) => {
      at += prev == null ? x.big / 2 : prev / 2 + Math.max(25, prev, x.big) + x.big / 2;
      prev = x.big;
      return { k: x.k, at };
    });
  };
  const draw = () => {
    const dep = Object.fromEntries(depths().map((x) => [x.k, x.at]));
    const s = mesh.s;
    const between = spec[0];
    const inward = f === "bottom" ? "above" : "below";
    const first = `<div class="lb-row"><span class="lb-name">Layer 1</span>
        <label>Mesh <select data-mesh>${meshes.map((t) => opt(t, `Ø${mesh.phi} @ ${fmt(mesh.s)}`)).join("")}</select></label>
        ${whole ? "" : `<label>between its bars <select data-between><option value="">none</option>${bars.flatMap((b) => [opt(`${b}@${s}`, between ? `${between[0]}@${between[1]}` : "", `Ø${b} @ ${fmt(s)} (every gap)`), opt(`${b}@${2 * s}`, between ? `${between[0]}@${between[1]}` : "", `Ø${b} @ ${fmt(2 * s)} (every second gap)`)]).join("")}</select></label>`}
        <span class="status">centre ${fmt(dep[0])} mm from the face (the mesh, nearest the ${f} face)</span></div>`;
    const inner = whole ? [] : spec.slice(1).map((p, i) => `<div class="lb-row"><span class="lb-name">Layer ${i + 2}</span>
        <label>Ø <select data-lphi="${i + 1}">${bars.map((b) => opt(b, p ? p[0] : 25)).join("")}</select></label>
        <label>@ <select data-ls="${i + 1}">${[s / 2, s, 2 * s].map((v) => opt(v, p ? p[1] : s, `${fmt(v)} mm${v < s ? ` (${inward} every bar and gap)` : v > s ? " (every second gap)" : ` (${inward} every gap)`}`)).join("")}</select></label>
        <span class="status">centre ${fmt(dep[i + 1])} mm from the face, ${inward} layer ${i + 1}</span> <button class="quiet" data-ldel="${i + 1}">Remove</button></div>`);
    // Drawn as they sit in the slab: the bottom mesh at the bottom with its layers above it, the top
    // mesh at the top with its layers below it.
    const stack = f === "bottom" ? [...inner.reverse(), first] : [first, ...inner];
    box.innerHTML = `<div class="lb-head"><b>${f === "bottom" ? "Bottom" : "Top"} face</b>, bars along ${along ? "Y" : "X"}, cover ${fmt(cover)} mm. Layers 2, 3… go ${inward} the mesh, inside the slab.</div>
      ${stack.join("")}
      ${whole ? '<div class="status">The mesh runs over the whole deck: changing it here changes it everywhere, and the zones are worked out again on it.</div>' : `<button class="quiet" data-ladd>Add layer ${spec.length + 1}</button> <span class="status">A new mesh here changes it over the whole deck.</span>`}`;
    box.querySelector("[data-mesh]").onchange = (e) => {
      const m = /Ø(\d+) @ ([\d.]+)/.exec(e.target.value);
      if (m) mesh = { phi: Number(m[1]), s: Number(m[2]) };
      spec = spec.map((p) => p && [p[0], Math.max(mesh.s / 2, Math.min(2 * mesh.s, p[1]))]);
      draw();
    };
    const bt = box.querySelector("[data-between]");
    if (bt) bt.onchange = () => { spec[0] = bt.value ? bt.value.split("@").map(Number) : null; draw(); };
    box.querySelectorAll("[data-lphi]").forEach((sel) => (sel.onchange = () => { const i = Number(sel.dataset.lphi); spec[i] = [Number(sel.value), spec[i]?.[1] ?? mesh.s]; draw(); }));
    box.querySelectorAll("[data-ls]").forEach((sel) => (sel.onchange = () => { const i = Number(sel.dataset.ls); spec[i] = [spec[i]?.[0] ?? 25, Number(sel.value)]; draw(); }));
    box.querySelectorAll("[data-ldel]").forEach((b) => (b.onclick = () => { spec.splice(Number(b.dataset.ldel), 1); draw(); }));
    const add = box.querySelector("[data-ladd]");
    if (add) add.onclick = () => { spec.push([25, mesh.s]); draw(); };
  };
  draw();
  const read = () => {
    const meshLabel = `Ø${mesh.phi} @ ${fmt(mesh.s)}`.replace(/,/g, "");
    const changedMesh = mesh.phi !== mesh0.phi || mesh.s !== mesh0.spacing_mm;
    let bars_ = null;
    if (!whole) {
      const used = spec.map((p) => (p ? `Ø${p[0]}@${p[1]}` : "–"));
      while (used.length && used[used.length - 1] === "–") used.pop();
      bars_ = used.length ? `layers: ${used.join(" | ")}` : "mesh only";
    }
    return { layer, meshLabel, changedMesh, bars: bars_ };
  };
  return { el: box, read };
}

function wireStripTable(card, d) {
  const sd = d.strip_design;
  card.querySelectorAll("[data-bars]").forEach((btn) => (btn.onclick = () => {
    const i = Number(btn.dataset.bars);
    const r = sd.table[i];
    const tr = btn.closest("tr");
    const open = tr.nextElementSibling?.classList.contains("bars-edit");
    card.querySelectorAll("tr.bars-edit").forEach((x) => x.remove());
    if (open) return;
    const faces = r.edit || Object.keys(r.layers);
    const mine = sec().slab_strips?.[d.key || d.element]?.bars || {};
    const hasOwn = faces.some((f) => (r.keys[f] || []).some((k) => k in mine) || `${r.layers[f]}|mesh` in mine);
    const edit = document.createElement("tr");
    edit.className = "bars-edit";
    edit.innerHTML = `<td colspan="10"><b>${esc(stripRowName(r))}.</b> Layer 1 is the mesh at the cover; each added layer goes inside it (above the bottom mesh, below the top mesh), with its own bar and spacing.
      <div class="layer-builders"></div>
      <button data-recheck>Re-check</button>${hasOwn ? ' <button class="quiet" data-auto>Use Triton\'s bars</button>' : ""} <span class="status" data-bars-status></span>
      <div class="status">Re-check keeps these bars for this row and checks bending and crack widths with them.</div></td>`;
    tr.after(edit);
    const builders = faces.map((f) => layerBuilder(d, r, f));
    const host = edit.querySelector(".layer-builders");
    builders.forEach((b) => host.appendChild(b.el));
    const status = edit.querySelector("[data-bars-status]");
    edit.querySelector("[data-recheck]").onclick = () =>
      saveSlabStrips(d.key || d.element, (cur) => {
        const bars = { ...(cur.bars || {}) };
        builders.forEach((b, j) => {
          const st = b.read();
          if (st.changedMesh) bars[`${st.layer}|mesh`] = st.meshLabel;
          if (st.bars != null) (r.keys[faces[j]] || []).forEach((k) => (bars[k] = st.bars));
        });
        // Bars are set on the mesh shown: keep that mesh spacing with them.
        return { ...cur, bars, spacing: cur.spacing ?? d.mesh_choice?.chosen_mm ?? null };
      }, status);
    const auto = edit.querySelector("[data-auto]");
    if (auto) auto.onclick = () =>
      saveSlabStrips(d.key || d.element, (cur) => {
        const bars = { ...(cur.bars || {}) };
        faces.forEach((f) => {
          (r.keys[f] || []).forEach((k) => delete bars[k]);
          if (r.strip === "all" && r.label.startsWith("Whole deck")) delete bars[`${r.layers[f]}|mesh`];
        });
        return { ...cur, bars };
      }, status);
  }));
}

// A ULS / SLS (QP) switch for a diagram: the same axes and signs, the other results.
const LIMIT_NAME = { uls: "ULS", qp: "SLS (QP)" };
function limitSwitch(hasQp, label = "") {
  if (!hasQp) return "";
  return `${label ? `<span class="status">${esc(label)}</span>` : ""}<span class="limit-switch" role="group" aria-label="Results shown">${["uls", "qp"].map((m) => `<button class="quiet${m === "uls" ? " on" : ""}" data-limit="${m}">${LIMIT_NAME[m]}</button>`).join("")}</span>`;
}
function wireLimitSwitch(host, onPick) {
  host.querySelectorAll("[data-limit]").forEach((b) => (b.onclick = () => {
    host.querySelectorAll("[data-limit]").forEach((x) => x.classList.toggle("on", x === b));
    onPick(b.dataset.limit);
  }));
}

function stationEditor(card, d) {
  // Column and field strip moments across the deck, sea side on the left, with the stations on them:
  // drag a station to move it, add or delete stations, then design the slab with them.
  const sd = d.strip_design;
  const el = card.querySelector('[data-kind="stations"]');
  const ctl = card.querySelector('[data-kind="station-ctl"]');
  if (!el || !ctl || !sd.profile) return;
  const names = Object.keys(sd.profile);
  let which = (sd.table || []).find((r) => r.along_strips)?.moment || names[0];
  const start = sd.start ?? sd.stations[0], end = sd.end ?? sd.stations[sd.stations.length - 1];
  let st = sd.stations.slice();
  const own = sec().slab_strips?.[d.key || d.element]?.stations;
  ctl.innerHTML = `<div class="legend"><span><i></i>Column strip, largest</span><span><i class="low"></i>Column strip, smallest</span><span><i class="field"></i>Field strip, largest</span><span><i class="field low"></i>Field strip, smallest</span><span>▲ row of piles</span></div>
    <div class="row">${names.map((n) => `<button class="quiet${n === which ? " on" : ""}" data-m="${esc(n)}">${esc(n)}</button>`).join("")}${limitSwitch(sd.profile_qp && Object.keys(sd.profile_qp).length, "Results:")}</div>
    <div data-st-tools><p class="status">Drag a station's circle to move it, press × to delete it, or Add station. You can also type the stations. Then Design with these stations. ${own ? "These are your stations." : "These are Triton's stations: 2 m each side of every row of piles."}</p>
    <div class="row"><input data-st-text style="flex:1;min-width:200px" aria-label="Stations (m from the sea side)"><button class="quiet" data-st-add>Add station</button><button data-st-design>Design with these stations</button>${own ? '<button class="quiet" data-st-auto>Triton\'s stations</button>' : ""}<span class="status" data-st-status></span></div></div>
    <p class="status" data-st-across hidden></p>`;
  const text = ctl.querySelector("[data-st-text]");
  const status = ctl.querySelector("[data-st-status]");
  const round = (v) => Math.round(v * 20) / 20;
  let c = null;
  let mode = "uls";
  let ap = sd.across_profile;
  const tools = ctl.querySelector("[data-st-tools]"), acrossNote = ctl.querySelector("[data-st-across]");
  const legend = ctl.querySelector(".legend");
  const drawAcross = () => {
    // M22 along the quay: one design over the whole deck, so the largest and smallest over all of it at
    // every cut along the quay, with the lines of piles.
    const pts = ap.points;
    const vals = pts.flatMap((q) => [q.max, q.min]);
    const lo = Math.min(0, ...vals), hi = Math.max(0, ...vals), pad = (hi - lo) * 0.1 || 1;
    c = frame(el, { w: 820, h: 360, xDomain: ap.range, yDomain: [lo - pad, hi + pad],
      xLabel: `${ap.axis} along the quay (m)`, yLabel: `${ap.moment} kNm/m (− hogging up, + sagging down)`, yReverse: true,
      title: `${ap.moment} along the quay, ${LIMIT_NAME[mode]} envelope over the whole deck. Hogging (top steel) up, sagging (bottom steel) down` });
    c.svg.classList.remove("editing");
    const path = (k) => pts.map((q, i) => `${i ? "L" : "M"}${c.x(q.s).toFixed(1)},${c.y(q[k]).toFixed(1)}`).join("");
    const bot = c.h - c.m.b;
    const rows = ap.lines.map((L) => `<path class="pile-row" d="M${c.x(L).toFixed(1)},${bot - 10} l-6,10 h12 z"><title>Line of piles at ${ap.axis} ${fmt(L, 2)} m</title></path><line class="station" x1="${c.x(L).toFixed(1)}" x2="${c.x(L).toFixed(1)}" y1="${c.m.t}" y2="${bot}" stroke-dasharray="3 4"/>`).join("");
    c.g.innerHTML = `<line class="zero" x1="${c.m.l}" x2="${c.w - c.m.r}" y1="${c.y(0)}" y2="${c.y(0)}"/>
      <path class="series" d="${path("max")}"/><path class="series low" d="${path("min")}"/>${rows}`;
    const gaps = ap.lines.slice(1).map((L, i) => L - ap.lines[i]);
    const gap = gaps.length ? gaps.reduce((a, b) => a + b, 0) / gaps.length : null;
    acrossNote.textContent = `${ap.moment} is designed over the whole deck, not in strips: one basic mesh, and zones of additional bars only where it needs more (see the table). ▲ lines of piles${gap ? `, ${fmt(gap, 1)} m apart` : ""}.`;
    c.svg.onmousemove = (evt) => {
      const r = c.svg.getBoundingClientRect();
      const x = ((evt.clientX - r.left) / r.width) * c.w;
      const q = pts.reduce((b, p) => (Math.abs(c.x(p.s) - x) < Math.abs(c.x(b.s) - x) ? p : b), pts[0]);
      if (q) showTip(c, evt, `<b>${ap.axis} ${fmt(q.s, 1)} m</b><br>Largest ${fmt(q.max)} kNm/m<br>Smallest ${fmt(q.min)} kNm/m`);
    };
    c.svg.onmouseleave = () => (c.tip.hidden = true);
  };
  const draw = () => {
    ap = mode === "qp" ? sd.across_profile_qp || sd.across_profile : sd.across_profile;
    const across = ap && which === ap.moment;
    tools.hidden = across;
    acrossNote.hidden = !across;
    if (legend) legend.innerHTML = across ? '<span><i></i>Largest over the deck</span><span><i class="low"></i>Smallest over the deck</span><span>▲ line of piles</span>' : '<span><i></i>Column strip, largest</span><span><i class="low"></i>Column strip, smallest</span><span><i class="field"></i>Field strip, largest</span><span><i class="field low"></i>Field strip, smallest</span><span>▲ row of piles</span>';
    if (across) return drawAcross();
    const prof = (mode === "qp" ? sd.profile_qp : sd.profile)[which] || [];
    const vals = prof.flatMap((q) => [q.column_max, q.column_min, q.field_max, q.field_min]).filter((v) => v != null);
    const lo = Math.min(0, ...vals), hi = Math.max(0, ...vals), pad = (hi - lo) * 0.1 || 1;
    c = frame(el, { w: 820, h: 360, xDomain: [start, end], yDomain: [lo - pad, hi + pad],
      xLabel: `Station from the ${sd.from}, sea side (m)`, yLabel: `${which} kNm/m (− hogging up, + sagging down)`, yReverse: true,
      title: `${which} across the deck, ${LIMIT_NAME[mode]} envelope of each strip's average. Hogging (top steel) up, sagging (bottom steel) down` });
    c.svg.classList.add("editing");
    const path = (k) => prof.filter((q) => q[k] != null).map((q, i) => `${i ? "L" : "M"}${c.x(q.s).toFixed(1)},${c.y(q[k]).toFixed(1)}`).join("");
    const top = c.m.t, bot = c.h - c.m.b;
    const rows = (sd.pile_rows_m || []).map((r) => `<path class="pile-row" d="M${c.x(r).toFixed(1)},${bot - 10} l-6,10 h12 z"><title>Row of piles at ${fmt(r, 2)} m</title></path>`).join("");
    const lines = st.map((s, i) => {
      const fixed = i === 0 || i === st.length - 1, x = c.x(s).toFixed(1);
      return `<line class="station" x1="${x}" x2="${x}" y1="${top}" y2="${bot}"/>
        <circle class="station-grip${fixed ? " fixed" : ""}" cx="${x}" cy="${top + 9}" r="8" data-grip="${i}"><title>Station ${fmt(s, 2)} m${fixed ? " (slab edge)" : ": drag to move"}</title></circle>
        ${fixed ? "" : `<text class="station-del" x="${Number(x) + 10}" y="${top + 5}" data-del="${i}">×<title>Delete station ${fmt(s, 2)}</title></text>`}
        <text class="tick" x="${x}" y="${top + 30}" text-anchor="middle">${fmt(s, 2)}</text>`;
    }).join("");
    c.g.innerHTML = `<line class="zero" x1="${c.m.l}" x2="${c.w - c.m.r}" y1="${c.y(0)}" y2="${c.y(0)}"/>
      <path class="series" d="${path("column_max")}"/><path class="series low" d="${path("column_min")}"/>
      <path class="series field" d="${path("field_max")}"/><path class="series field low" d="${path("field_min")}"/>${rows}${lines}`;
    text.value = st.slice(1, -1).map((v) => fmt(v, 2)).join(", ");
    c.g.querySelectorAll("[data-del]").forEach((t) => (t.onclick = () => { st.splice(Number(t.dataset.del), 1); draw(); }));
    c.g.querySelectorAll("[data-grip]").forEach((g) => {
      const i = Number(g.dataset.grip);
      if (i === 0 || i === st.length - 1) return;
      g.onpointerdown = (e) => {
        e.preventDefault();
        g.setPointerCapture(e.pointerId);
        g.onpointermove = (ev) => {
          const r = c.svg.getBoundingClientRect();
          const x = ((ev.clientX - r.left) / r.width) * c.w;
          const s = start + ((x - c.m.l) / (c.w - c.m.l - c.m.r)) * (end - start);
          st[i] = Math.min(st[i + 1] - 0.25, Math.max(st[i - 1] + 0.25, round(s)));
          const X = c.x(st[i]).toFixed(1);
          const ln = g.previousElementSibling;
          ln.setAttribute("x1", X); ln.setAttribute("x2", X);
          g.setAttribute("cx", X);
          text.value = st.slice(1, -1).map((v) => fmt(v, 2)).join(", ");
        };
        g.onpointerup = () => { g.onpointermove = null; draw(); };
      };
    });
    c.svg.onmousemove = (evt) => {
      const r = c.svg.getBoundingClientRect();
      const x = ((evt.clientX - r.left) / r.width) * c.w;
      const q = prof.reduce((b, p) => (Math.abs(c.x(p.s) - x) < Math.abs(c.x(b.s) - x) ? p : b), prof[0]);
      if (!q) return;
      showTip(c, evt, `<b>${fmt(q.s, 1)} m</b><br>Column strip ${fmt(q.column_max)} / ${fmt(q.column_min)} kNm/m<br>Field strip ${fmt(q.field_max)} / ${fmt(q.field_min)} kNm/m`);
    };
    c.svg.onmouseleave = () => (c.tip.hidden = true);
  };
  text.onchange = () => {
    const vals = text.value.split(/[,;\s]+/).map(Number).filter((v) => Number.isFinite(v) && v > start + 0.1 && v < end - 0.1);
    st = [start, ...[...new Set(vals.map(round))].sort((a, b) => a - b), end];
    draw();
  };
  wireLimitSwitch(ctl, (m) => { mode = m; draw(); });
  ctl.querySelectorAll("[data-m]").forEach((b) => (b.onclick = () => {
    which = b.dataset.m;
    ctl.querySelectorAll("[data-m]").forEach((x) => x.classList.toggle("on", x === b));
    draw();
  }));
  ctl.querySelector("[data-st-add]").onclick = () => {
    let k = 0;
    st.forEach((s, i) => { if (i && s - st[i - 1] > st[k + 1] - st[k]) k = i - 1; });
    st.splice(k + 1, 0, round((st[k] + st[k + 1]) / 2));
    draw();
  };
  ctl.querySelector("[data-st-design]").onclick = () => saveSlabStrips(d.key || d.element, (cur) => ({ ...cur, stations: st.slice(1, -1) }), status);
  const auto = ctl.querySelector("[data-st-auto]");
  if (auto) auto.onclick = () => saveSlabStrips(d.key || d.element, (cur) => ({ ...cur, stations: null }), status);
  draw();
}

function shortBars(t) {
  return String(t || "").replace(" in 2 layers", " ×2 layers").replace(/ \+ Ø\d+ (under the mesh|behind the mesh bars)/, " + behind").replace(/ between the mesh bars/g, " between").replace(/ layer (\d)/g, " (L$1)");
}

// A face's bars layer by layer, outermost first: "L1 Ø16 @ 150 + Ø32 @ 150 (between) · L2 Ø32 @ 75".
function layerLines(bl, withMesh = true) {
  return (bl || [])
    .map((q) => {
      const bars = (q.bars || []).filter((b) => withMesh || b.kind !== "mesh");
      if (!bars.length) return null;
      return `L${q.layer} ` + bars.map((b) => `Ø${fmt(b.diameter_mm)} @ ${fmt(b.spacing_mm)}${withMesh && b.kind === "between the mesh bars" ? " (between)" : ""}`).join(" + ");
    })
    .filter(Boolean);
}

// Warnings for sections short of ductility or over-reinforced (x/d, bars not yielding, over 4%).
function overWarn(items) {
  if (!items?.length) return "";
  return `<div class="over-warn"><b>Over-reinforced sections:</b><ul>${items.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>
    <p class="status">Checked at the moment capacity with εcu2 = 0.0035: x/d above 0.45 is beyond the ductility limit of EN 1992-1-1 5.5(4); where the tension bars would not reach fyd/Es the section is over-reinforced; 9.2.1.1(3) caps the steel at 4% of the concrete. A deeper section or compression bars bring x/d down.</p></div>`;
}

// Section through one face's bars, 1 m wide: every layer drawn at its own depth with its own bars.
function barSection(bl, face, h, title) {
  if (!bl?.length) return "";
  const W = 1000, deepest = Math.max(...bl.map((q) => q.from_face_mm)) + 70;
  const sc = 0.72, pad = 12, left = 24, lab = 330;
  const Wpx = W * sc + left + lab, Hpx = deepest * sc + 2 * pad + 16;
  const Y = (mm) => (face === "top" ? pad + mm * sc : pad + (deepest - mm) * sc);
  const sMesh = bl[0].bars.find((b) => b.kind === "mesh")?.spacing_mm || 150;
  const circles = [], labels = [];
  bl.forEach((q) => {
    q.bars.forEach((b) => {
      const off = b.kind === "mesh" ? sMesh / 4 : b.kind === "between the mesh bars" ? sMesh / 4 + sMesh / 2 : b.spacing_mm >= sMesh - 1e-6 ? sMesh / 4 + sMesh / 2 : sMesh / 4;
      for (let x = off; x < W; x += b.spacing_mm)
        circles.push(`<circle class="${b.kind === "mesh" ? "sec-mesh" : "sec-add"}" cx="${(left + x * sc).toFixed(1)}" cy="${Y(q.from_face_mm).toFixed(1)}" r="${Math.max(2, (b.diameter_mm / 2) * sc).toFixed(1)}"><title>Layer ${q.layer}: Ø${fmt(b.diameter_mm)} @ ${fmt(b.spacing_mm)} (${esc(b.kind)}), centre ${fmt(q.from_face_mm)} mm from the ${face} face</title></circle>`);
    });
    labels.push(`<text class="tick" x="${left + W * sc + 10}" y="${(Y(q.from_face_mm) + 4).toFixed(1)}">${esc(layerLines([q])[0])} · ${fmt(q.from_face_mm)} mm</text>`);
  });
  const faceY = face === "top" ? pad : pad + deepest * sc;
  const body = face === "top" ? `<rect class="bd-slab" x="${left}" y="${pad}" width="${W * sc}" height="${deepest * sc}"/>` : `<rect class="bd-slab" x="${left}" y="${pad}" width="${W * sc}" height="${deepest * sc}"/>`;
  return `<div class="chart-title">${esc(title)}: ${bl.length} layer${bl.length > 1 ? "s" : ""} of bars, a 1 m wide cut through the ${face} face (slab ${fmt(h)} mm, the part near this face drawn).</div>
    <svg viewBox="0 0 ${Wpx.toFixed(0)} ${Hpx.toFixed(0)}" role="img" aria-label="${esc(title)}">${body}
      <line class="axis" x1="${left}" x2="${left + W * sc}" y1="${faceY}" y2="${faceY}"/>
      <text class="tick" x="${left}" y="${face === "top" ? Hpx - 4 : 10}">${face === "top" ? "↑ top face" : "↓ bottom face"}; 1000 mm wide</text>
      ${circles.join("")}${labels.join("")}</svg>`;
}

function barDiagrams(card, d) {
  // Elevations of the bars along X and along Y: the basic mesh of each face over the whole length and
  // the additional bars where they are added (per station and strip along the strips, per zone across).
  const sd = d.strip_design;
  const el = card.querySelector('[data-kind="bardiag"]'), pick = card.querySelector('[data-kind="bardiag-pick"]');
  if (!sd || !el || !pick) return;
  const al = sd.along.toLowerCase(), cl = al === "x" ? "y" : "x";
  const acrossAxis = sd.along === "X" ? "Y" : "X";
  const across = sd.across_profile;
  const mAlong = (sd.table || []).find((r) => r.along_strips)?.moment || (al === "x" ? "M11" : "M22");
  const views = [
    { key: "along", dir: al, title: `Bars along ${sd.along} (${mAlong})` },
    { key: "across", dir: cl, title: `Bars along ${acrossAxis} (${across?.moment || ""})` },
  ];
  let view = 0;
  pick.innerHTML = views.map((v, i) => `<button class="quiet${i ? "" : " on"}" data-bd="${i}">${esc(v.title)}</button>`).join("");
  const draw = () => {
    const v = views[view];
    const isAlong = v.key === "along";
    const lo = isAlong ? sd.start : across?.range?.[0] ?? d.box[acrossAxis][0];
    const hi = isAlong ? sd.end : across?.range?.[1] ?? d.box[acrossAxis][1];
    const segs = { top: [], bottom: [] };
    for (const f of ["top", "bottom"]) {
      const rows = (sd.rows || []).filter((r) => r.layer === `${f}_${v.dir}` && r.additional_bars);
      if (isAlong) {
        for (const strip of ["column", "field"]) {
          const mine = rows.filter((r) => r.strip === strip).sort((a, b) => a.station[0] - b.station[0]);
          const merged = [];
          for (const r of mine) {
            const last = merged[merged.length - 1];
            if (last && last.text === r.additional_bars && Math.abs(last.b - r.station[0]) < 1e-6) last.b = r.station[1];
            else merged.push({ a: r.station[0], b: r.station[1], text: r.additional_bars, lane: strip === "column" ? 0 : 1, what: `${strip} strip`, layers: r.bar_layers });
          }
          segs[f].push(...merged);
        }
      } else {
        const ax = acrossAxis === "Y" ? 1 : 0, sx = 1 - ax;
        rows.filter((r) => r.zone).forEach((r) => {
          const st = r.zone[sx].map((x) => (x - sd.origin) * sd.sign).sort((a, b) => a - b);
          segs[f].push({ a: r.zone[ax][0], b: r.zone[ax][1], text: r.additional_bars, what: `zone at station ${fmt(st[0], 1)} to ${fmt(st[1], 1)}`, layers: r.bar_layers });
        });
        // Stack zones that overlap along this axis.
        const lanes = [];
        segs[f].sort((p, q) => p.a - q.a).forEach((s) => {
          let k = lanes.findIndex((end) => end <= s.a + 1e-6);
          if (k < 0) { k = lanes.length; lanes.push(0); }
          lanes[k] = s.b;
          s.lane = k;
        });
      }
    }
    // Every layer of additional bars is its own line, layer 1 (between the mesh bars) nearest the mesh.
    for (const f of ["top", "bottom"]) segs[f].forEach((s) => (s.lines = layerLines(s.layers, false).length ? layerLines(s.layers, false) : [shortBars(s.text)]));
    const perLane = Math.max(1, ...segs.top.concat(segs.bottom).map((s) => s.lines.length));
    const nT = Math.max(1, ...segs.top.map((s) => s.lane + 1)), nB = Math.max(1, ...segs.bottom.map((s) => s.lane + 1));
    const W = 860, L = 136, R = 16, lane = 6 + 15 * perLane;
    const X = (s) => L + ((s - lo) / (hi - lo || 1)) * (W - L - R);
    const yMeshT = 16 + nT * lane + 8, slabTop = yMeshT - 7, slabBot = slabTop + 58, yMeshB = slabBot - 7;
    const laneY = (f, k) => (f === "top" ? yMeshT - 8 - (k + 0.5) * lane : yMeshB + 8 + (k + 0.5) * lane);
    const H = yMeshB + 8 + nB * lane + 44;
    const mesh = (f) => d.layers[`${f}_${v.dir}`]?.basic?.label || "–";
    const depth = (f) => (d.layers[`${f}_${v.dir}`]?.mesh_bar_layers || [])[0]?.from_face_mm;
    const segSvg = (f) => segs[f].map((s, si) => {
      const x0 = X(s.a), x1 = X(s.b), w = Math.max(2, x1 - x0), room = Math.floor((w - 6) / 5.6);
      const lay = (s.layers || []).map((q) => `Layer ${q.layer}: ${q.text}, centre ${fmt(q.from_face_mm)} mm from the face`).join("\n");
      const top = laneY(f, s.lane) - lane / 2;
      // Layer 1 nearest the mesh: downwards from the top mesh, upwards from the bottom mesh.
      const lines = s.lines.map((t, j) => {
        const y = f === "top" ? top + lane - 6 - j * 15 : top + 10 + j * 15;
        const label = t.length <= room ? t : room > 4 ? `${t.slice(0, room - 1)}…` : "";
        return `<line x1="${x0.toFixed(1)}" x2="${x1.toFixed(1)}" y1="${y}" y2="${y}"/><line x1="${x0.toFixed(1)}" x2="${x0.toFixed(1)}" y1="${y - 3}" y2="${y + 3}"/><line x1="${x1.toFixed(1)}" x2="${x1.toFixed(1)}" y1="${y - 3}" y2="${y + 3}"/>
          ${label ? `<text class="tick" x="${((x0 + x1) / 2).toFixed(1)}" y="${y - 3}" text-anchor="middle">${esc(label)}</text>` : ""}`;
      }).join("");
      return `<g class="bd-seg" data-seg="${f}:${si}" style="cursor:pointer">${lines}
        <rect x="${x0.toFixed(1)}" y="${top}" width="${w.toFixed(1)}" height="${lane}" fill="transparent"><title>${esc(`${f === "top" ? "Top" : "Bottom"}, ${s.what}, ${fmt(s.a, 2)} to ${fmt(s.b, 2)} m: ${s.text}\n${lay}\nClick for the section through its layers.`)}</title></rect></g>`;
    }).join("");
    const marks = isAlong ? (sd.pile_rows_m || []) : (sd.lines || []);
    const ticks = isAlong ? sd.stations : marks;
    const axisY = H - 18;
    const piles = marks.map((m) => `<path class="pile-row" d="M${X(m).toFixed(1)},${axisY - 11} l-6,10 h12 z"><title>${isAlong ? "Row" : "Line"} of piles at ${fmt(m, 2)} m</title></path>`).join("");
    el.innerHTML = `<div class="chart-title">${esc(v.title)}: basic mesh of each face over the whole ${isAlong ? "deck" : "length"}, additional bars where they are added. ${isAlong ? "Sea side on the left; stations in m from the " + esc(sd.from) + "." : `${acrossAxis} in m along the quay.`}</div>
      <div class="legend"><span><i class="bd-mesh"></i>basic mesh (layer 1, at the cover)</span><span><i class="bd-add"></i>additional bars, one line per layer (L1 between the mesh bars, L2, L3… inside it)${isAlong ? ": column strip next to the mesh, field strip beyond" : ", one group per zone"}</span><span>▲ piles</span><span>click a group for its section</span></div>
      <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(v.title)}">
        <rect x="${X(lo)}" y="${slabTop}" width="${X(hi) - X(lo)}" height="${slabBot - slabTop}" class="bd-slab"/>
        <line class="bd-mesh" x1="${X(lo)}" x2="${X(hi)}" y1="${yMeshT}" y2="${yMeshT}"><title>Top mesh ${esc(mesh("top"))}, centre ${fmt(depth("top"))} mm from the top</title></line>
        <line class="bd-mesh" x1="${X(lo)}" x2="${X(hi)}" y1="${yMeshB}" y2="${yMeshB}"><title>Bottom mesh ${esc(mesh("bottom"))}, centre ${fmt(depth("bottom"))} mm from the bottom</title></line>
        <text class="tick" x="${L - 8}" y="${yMeshT + 4}" text-anchor="end">Top ${esc(mesh("top"))}</text>
        <text class="tick" x="${L - 8}" y="${yMeshB + 4}" text-anchor="end">Bottom ${esc(mesh("bottom"))}</text>
        ${segSvg("top")}${segSvg("bottom")}${piles}
        <line class="axis" x1="${X(lo)}" x2="${X(hi)}" y1="${axisY}" y2="${axisY}"/>
        ${[lo, ...ticks, hi].map((t) => `<line class="axis" x1="${X(t)}" x2="${X(t)}" y1="${axisY}" y2="${axisY + 4}"/><text class="tick" x="${X(t)}" y="${axisY + 14}" text-anchor="middle">${fmt(t, 1)}</text>`).join("")}
      </svg><div data-kind="bar-section"></div>`;
    // A cut through every group of additional bars, top face first, each in turn along the axis; the
    // heaviest (most layers) starts open, and clicking a group on the elevation opens its cut.
    const secEl = el.querySelector('[data-kind="bar-section"]');
    const all = ["top", "bottom"].flatMap((f) => segs[f].map((s, i) => ({ f, s, i }))).filter((g) => g.s.layers?.length);
    all.sort((p, q) => (p.f === q.f ? p.s.a - q.s.a || p.s.lane - q.s.lane : p.f === "top" ? -1 : 1));
    const heaviest = all.reduce((b, g) => (!b || g.s.layers.length > b.s.layers.length ? g : b), null);
    const cutTitle = (g) => `${g.f === "top" ? "Top" : "Bottom"} bars along ${v.dir.toUpperCase()}, ${g.s.what}, ${fmt(g.s.a, 2)} to ${fmt(g.s.b, 2)} m`;
    secEl.innerHTML = all.length
      ? `<div class="chart-title">Cuts through each group of additional bars (${all.length}), 1 m wide:</div>` +
        all.map((g) => `<details class="bar-cut" data-cut="${g.f}:${g.i}"${g === heaviest ? " open" : ""}><summary>${esc(cutTitle(g))}: ${esc(layerLines(g.s.layers, false).join(" · ") || shortBars(g.s.text))}</summary>${barSection(g.s.layers, g.f, d.thickness_mm, cutTitle(g))}</details>`).join("")
      : "";
    el.querySelectorAll("[data-seg]").forEach((g) => (g.onclick = () => {
      const cut = secEl.querySelector(`[data-cut="${g.dataset.seg}"]`);
      if (!cut) return;
      cut.open = true;
      cut.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }));
  };
  pick.querySelectorAll("[data-bd]").forEach((b) => (b.onclick = () => {
    view = Number(b.dataset.bd);
    pick.querySelectorAll("[data-bd]").forEach((x) => x.classList.toggle("on", x === b));
    draw();
  }));
  draw();
}

function momentPlan(card, d) {
  // Plan of the deck, sea side on the left: the ULS envelope of M11 or M22 per cell, sagging (bottom
  // face in tension) or hogging (top face in tension), with the strips and stations over it.
  const mc = d.moment_cells, sd = d.strip_design;
  const el = card.querySelector('[data-kind="mplan"]'), pick = card.querySelector('[data-kind="mplan-pick"]');
  if (!mc || !sd || !el || !pick) return;
  const modes = [["x", 0], ["x", 1], ["y", 0], ["y", 1]];
  let mode = 0;
  pick.innerHTML = modes.map(([k, h], i) => `<button class="quiet${i ? "" : " on"}" data-mp="${i}">${esc(mc.names[k])} ${h ? "hogging: top in tension" : "sagging: bottom in tension"}</button>`).join("");
  const alongX = sd.along === "X";
  const draw = () => {
    const [k, hog] = modes[mode];
    const col = (k === "x" ? 2 : 4) + hog;
    const cells = mc.cells.map((v) => {
      const cx = mc.x0 + (v[0] + 0.5) * mc.size, cy = mc.y0 + (v[1] + 0.5) * mc.size;
      const raw = v[col];
      return { s: ((alongX ? cx : cy) - sd.origin) * sd.sign, t: alongX ? cy : cx, raw, v: hog ? Math.max(-raw, 0) : Math.max(raw, 0), why: v[6] };
    });
    const vmax = Math.max(1, ...cells.map((q) => q.v));
    const t0 = Math.min(...cells.map((q) => q.t)) - mc.size / 2, t1 = Math.max(...cells.map((q) => q.t)) + mc.size / 2;
    const s0 = Math.min(sd.start, ...cells.map((q) => q.s - mc.size / 2)), s1 = Math.max(sd.end, ...cells.map((q) => q.s + mc.size / 2));
    const pad = 44, sc = Math.min((820 - 2 * pad) / (s1 - s0), (560 - 2 * pad) / (t1 - t0));
    const W = (s1 - s0) * sc + 2 * pad, H = (t1 - t0) * sc + 2 * pad;
    const X = (s) => pad + (s - s0) * sc, Y = (t) => H - pad - (t - t0) * sc;
    const hue = hog ? "31,95,160" : "200,52,40";
    const rects = cells.map((q) => `<rect x="${X(q.s - mc.size / 2).toFixed(1)}" y="${Y(q.t + mc.size / 2).toFixed(1)}" width="${(mc.size * sc).toFixed(1)}" height="${(mc.size * sc).toFixed(1)}" fill="rgba(${hue},${(0.06 + 0.88 * q.v / vmax).toFixed(2)})"><title>${esc(mc.names[k])} ${hog ? "smallest" : "largest"} ${fmt(q.raw)} kNm/m at station ${fmt(q.s, 1)} m, ${alongX ? "Y" : "X"} ${fmt(q.t, 1)}${q.why ? `. ${GAP_WHY[q.why] || ""}` : ""}</title></rect>`).join("");
    // Pile heads under the deck (those not under a beam), so a square over a pile reads as one.
    const heads = (d.punching || []).map((p) => {
      const ps = ((alongX ? p.x : p.y) - sd.origin) * sd.sign, pt = alongX ? p.y : p.x;
      return `<circle cx="${X(ps).toFixed(1)}" cy="${Y(pt).toFixed(1)}" r="${((p.D_mm / 2000) * sc).toFixed(1)}" fill="none" stroke="var(--text)" stroke-width="1.2"><title>${esc(p.pile)}: ${GAP_WHY.pile}</title></circle>`;
    }).join("");
    const strips = sd.lines.map((L) => `<rect x="${X(s0)}" y="${Y(L + sd.column_width_m / 2)}" width="${(s1 - s0) * sc}" height="${sd.column_width_m * sc}" fill="none" stroke="var(--text)" stroke-dasharray="6 4" stroke-width="1"><title>Column strip on the pile line at ${fmt(L, 1)}</title></rect>`).join("");
    const stations = sd.stations.map((s) => `<line x1="${X(s)}" x2="${X(s)}" y1="${pad - 6}" y2="${H - pad}" stroke="var(--text)" stroke-width="1.2"/><text class="tick" x="${X(s)}" y="${pad - 10}" text-anchor="middle">${fmt(s, 2)}</text>`).join("");
    el.innerHTML = `<div class="chart-title">${esc(mc.names[k])}, ULS ${hog ? "hogging (top face in tension)" : "sagging (bottom face in tension)"}: largest ${fmt(vmax)} kNm/m. Sea side on the left.</div>
      <div class="legend"><span>0</span><i class="ramp" style="background:linear-gradient(90deg,rgba(${hue},.06),rgba(${hue},.94))"></i><span>${fmt(vmax)} kNm/m</span><span>dashed: column strips</span><span>lines: stations (m from the sea side)</span>${(d.punching || []).length ? "<span>circles: pile heads (the squares over them show the pile faces)</span>" : ""}</div>
      <svg viewBox="0 0 ${W} ${H}" style="max-width:${Math.round(W)}px" role="img" aria-label="Moment plan"><rect x="${X(s0)}" y="${Y(t1)}" width="${(s1 - s0) * sc}" height="${(t1 - t0) * sc}" fill="var(--miss-bg)"/>${rects}${heads}${strips}${stations}
      <text class="tick" x="${X(s0)}" y="${H - 14}">Sea side</text><text class="tick" x="${X(s1)}" y="${H - 14}" text-anchor="end">Rear</text></svg>`;
  };
  pick.querySelectorAll("[data-mp]").forEach((b) => (b.onclick = () => {
    mode = Number(b.dataset.mp);
    pick.querySelectorAll("[data-mp]").forEach((x) => x.classList.toggle("on", x === b));
    draw();
  }));
  draw();
}

// The slab is designed with a 150 and a 200 mm mesh; the one picked drives everything below it.
function meshChooser(d) {
  const mc = d.mesh_choice;
  if (!mc) return "";
  const btn = (o) => {
    const on = o.spacing_mm === mc.chosen_mm;
    const short = (o.utilisation ?? 0) > 1 + 1e-6;
    return `<button class="mesh-pick${on ? " on" : ""}" data-mesh-pick="${o.spacing_mm}" ${on ? 'aria-pressed="true"' : ""}>
      <b>${fmt(o.spacing_mm)} mm mesh</b><span>${fmt(o.kg_per_m3)} kg/m³ · ρ ${fmt(o.ratio_pct, 2)}%</span>
      <span class="${short ? "bad" : ""}">bars ${short ? `not enough (${fmt(o.utilisation, o.utilisation < 1.01 ? 3 : 2)})` : `utilisation ${fmt(o.utilisation, o.utilisation > 0.99 ? 3 : 2)}`}</span>
      ${on ? `<em>${mc.from_bars ? "the spacing of your bars" : mc.picked ? "your pick" : "shown"}</em>` : ""}</button>`;
  };
  return `<div class="mesh-choice"><span class="status">Show the design with:</span>${mc.options.map(btn).join("")}
    <span class="status" data-mesh-status>${mc.picked ? "" : "Triton shows the lighter mesh whose bars are enough until you pick."} Results, drawings, AdSec files, the force-set Excel and the report follow the mesh shown.</span></div>`;
}

function wireMeshChooser(card, d) {
  const status = card.querySelector("[data-mesh-status]");
  card.querySelectorAll("[data-mesh-pick]").forEach((b) => (b.onclick = () => {
    const sp = Number(b.dataset.meshPick);
    if (sp === d.mesh_choice.chosen_mm && d.mesh_choice.picked) return;
    card.querySelectorAll("[data-mesh-pick]").forEach((x) => (x.disabled = true));
    saveSlabStrips(d.key || d.element, (cur) => ({ ...cur, spacing: sp }), status);
  }));
}

// "Part 2, turned +25°: " for a corner berth's part (triton/alignment.py), else nothing.
function partText(d) {
  const p = d.part;
  if (!p || (d.parts || 1) < 2 && !p.rotation_deg) return "";
  return `${esc(p.name)}${p.rotation_deg ? `, turned ${p.rotation_deg > 0 ? "+" : ""}${fmt(p.rotation_deg, 1)}°` : ", straight"}, ${fmt(p.length_m, 1)} m · `;
}

function slabCard(d) {
  const card = document.createElement("div");
  card.className = "panel";
  card.style.marginTop = "16px";
  const ok = (x) => `<span class="sev ${x ? "ok" : "error"}">${x ? "passes" : "fails"}</span>`;
  const st = d.steel || {};
  const punch = d.punching || [];
  const needs = punch.filter((q) => q.needs_reinforcement);
  const sh = d.shear || {};
  const layers = d.layers || {};
  card.innerHTML = `<div class="element-head"><h3>${esc(d.key || d.element)}<span class="type">${partText(d)}Slab, ${fmt(d.thickness_mm)} mm, ${esc(d.concrete || "")}, covers ${fmt(d.cover_top_mm)} top / ${fmt(d.cover_bottom_mm)} bottom, ${d.strips === "column_and_field" ? "column and field strips" : "uniform"}</span></h3>${ok(d.passed)}</div>
    ${meshChooser(d)}
    ${overWarn((d.ductility || []).map((q) => `${q.where}: ${q.bars}. ${q.warnings.join("; ")}.`))}
    <div class="counts" style="margin-top:0">
      <div class="count"><b>${fmt(d.utilisation, 2)}</b>max utilisation</div>
      <div class="count"><b>${fmt(st.kg_per_m3)}</b>kg/m³ (${fmt(st.kg_per_m2, 1)} kg/m², links not included)</div>
      <div class="count"><b>${fmt(overallRatio(st), 2)}%</b>overall ρ</div>
      <div class="count"><b>${fmt(st.total_t, 1)} t</b>bars over ${fmt(st.area_m2)} m²</div>
      <div class="count"><b>${needs.length} of ${punch.length}</b>piles need punching links</div>
      <div class="count"><b>${sh.cells_needing_links ?? 0}</b>${fmt(d.zone_size_m, 1)} m cells need shear links</div>
    </div>
    ${d.frame_note ? `<p class="status">${esc(d.frame_note)}</p>` : ""}
    ${(d.notes || []).map((n) => `<p class="status">${esc(n)}</p>`).join("")}
    ${voidsBlock(d)}
    ${jointsBlock(d.construction_joints)}
    ${v3dSlot(d.element)}
    ${d.strip_design ? `<h3 style="margin-top:18px">Moments across the deck and the stations</h3>
      <div class="chart wide" data-kind="stations"></div><div data-kind="station-ctl"></div>
      ${stripTable(d)}
      <h3 style="margin-top:18px">Reinforcement along X and Y</h3>
      <div class="row" data-kind="bardiag-pick"></div><div class="chart wide" data-kind="bardiag"></div>
      <h3 style="margin-top:18px">Moments in plan: where the deck is in tension</h3>
      <div class="row" data-kind="mplan-pick"></div><div class="chart wide" data-kind="mplan"></div>
      ${crackPicturesHtml(slabCrackItems(d), "Crack pictures (QP, per strip and station)")}` : ""}
    <h3 style="margin-top:18px">Bars per metre</h3>
    <div class="scroll"><table><tr><th>Layer</th><th>Mesh</th><th>Additional bars (between the mesh bars)</th><th>Utilisation</th><th>Set by cracking</th><th>d</th></tr>
      ${Object.entries(layers).map(([k, l]) => `<tr><td>${esc(LAYER_NAME[k] || k)}</td><td><b>${esc(l.basic.label)}</b> (${fmt(l.basic.as_mm2_per_m)} mm²/m)${l.basic.set_by === "user" ? "<br><span class=\"status\">your mesh</span>" : ""}</td>
        <td>${l.mode === "mesh_only" ? "mesh only (your choice)" : l.zones.length ? `${l.zones.length} zones: ${esc([...new Set(l.zones.map((z) => z.label))].join(", "))}` : "none needed"}</td>
        <td class="cell ${l.utilisation <= 1 ? "ok" : "error"}">${fmt(l.utilisation, 2)}</td><td>${fmt(l.cells_set_by_cracks)} cells</td><td>${fmt(l.d_mm)} mm</td></tr>`).join("")}
    </table></div>
    <div class="row" style="margin:10px 0 4px">${Object.keys(layers).map((k, i) => `<button class="quiet${i ? "" : " on"}" data-layer="${k}">${esc(LAYER_NAME[k] || k)}</button>`).join("")}${sh.links?.length ? `<button class="quiet" data-layer="shear">Shear links</button>` : ""}</div>
    <div class="chart wide" data-kind="plan"></div>
    <h3 style="margin-top:18px">Punching at the piles</h3>
    ${punch.length ? `<p class="status">Click a pile to see its control perimeters. Change a pile's thickness for a slope, then save and design again.</p>
      <div class="scroll"><table class="punch"><tr><th>Pile</th><th>X, Y</th><th>Thickness</th><th>V<sub>Ed</sub></th><th>β</th><th>v<sub>Ed</sub> / v<sub>Rd,c</sub> (MPa)</th><th>At the face / v<sub>Rd,max</sub></th><th>Links</th><th></th></tr>
      ${punch.map((q, i) => `<tr class="link" data-punch="${i}"><td>${esc(q.pile)}</td><td>${fmt(q.plan_x ?? q.x, 1)}, ${fmt(q.plan_y ?? q.y, 1)}</td>
        <td><input type="number" step="any" data-depth="${i}" value="${q.thickness_mm}" style="width:80px" title="${esc(q.thickness_from)}"> mm</td><td>${fmt(q.V_kN)} kN, ${esc(q.direction)}<br><span class="status">${esc(q.combination)}</span></td><td>${fmt(q.beta, 2)}</td>
        <td>${fmt(q.vEd_MPa, 3)} / ${fmt(q.vRd_c_MPa, 3)}</td><td>${fmt(q.vEd_face_MPa, 2)} / ${fmt(q.vRd_max_MPa, 2)}</td>
        <td>${q.needs_reinforcement ? (q.perimeters ? `${q.perimeters} perimeters @ ${fmt(q.radial_spacing_mm)} mm, ${fmt(q.asw_mm2_per_perimeter)} mm² each, to ${fmt(q.reinforced_to_mm)} mm from the face` : q.fix ? `Links alone cannot: ${esc(punchFix(q))}` : "–") : "none"}</td><td>${ok(q.passed)}</td></tr>`).join("")}
    </table></div><div class="charts" data-kind="punch"></div>
    <p class="status">EN 1992-1-1 6.4: checked from the pile face (u0, v<sub>Rd,max</sub>) out to u1 at 2d, u1 = π(D + 4d); nothing inside the pile. β = 1 + 0.6π·e/(D + 4d) with the pile moment at the slab soffit, as in the pile design; ρl of the face in tension over the pile. One-way shear starts at 2d from the pile faces. Piles under a beam are left to the beam.</p>` : '<p class="status">No piles under the slab.</p>'}
    <h3 style="margin-top:18px">Shear per metre ${ok(sh.passed !== false)}</h3>
    <p>${sh.governing ? `Largest v − V<sub>Rd,c</sub>: ${esc(sh.governing.combination)} at X ${fmt(sh.governing.x, 1)}, Y ${fmt(sh.governing.y, 1)}: v = ${fmt(sh.governing.V_kN_per_m)} kN/m, V<sub>Rd,c</sub> = ${fmt(sh.governing.VRd_c_kN_per_m)} kN/m, V<sub>Rd,max</sub> = ${fmt(sh.governing.VRd_max_kN_per_m)} kN/m.` : ""}
      ${sh.heaviest ? ` Links in ${sh.cells_needing_links} cells, in ${sh.links.length} bands across the deck at the mesh spacing (they hook round the bottom mesh: ${fmt(sh.link_spacing_mm?.x)} mm across X, ${fmt(sh.link_spacing_mm?.y)} mm across Y, or every second bar).` : " No shear links needed."}</p>
    ${sh.links?.length ? `<div class="scroll"><table><tr><th>Band</th>${sh.links[0].stations ? "<th>Stations (m)</th>" : ""}<th>X (m)</th><th>Y (m)</th><th>Links</th><th>A<sub>sw</sub> given / needed (mm²/m²)</th><th>Cells needing links</th></tr>
      ${[...sh.links].sort((a, b) => (a.stations?.[0] ?? a.x[0]) - (b.stations?.[0] ?? b.x[0])).map((z, i) => `<tr><td>S${i + 1}</td>${z.stations ? `<td>${fmt(z.stations[0], 1)} to ${fmt(z.stations[1], 1)}</td>` : ""}<td>${fmt(z.x[0], 1)} to ${fmt(z.x[1], 1)}</td><td>${fmt(z.y[0], 1)} to ${fmt(z.y[1], 1)}</td><td><b>${esc(z.label)}</b></td><td>${fmt(z.asw_mm2_per_m2)} / ${fmt(z.needs_mm2_per_m2)}</td><td>${fmt(z.cells)}</td></tr>`).join("")}</table></div>
      ${sh.links.some((z) => z.in_webs) ? `<p class="status">Bands marked "per web" are in the voided slab: the links stand in the webs between the voids (${fmt(d.voids?.web_mm)} mm), at the void spacing across them; the other bands are in the solid slab.</p>` : ""}
      <p class="status">Each band takes the links its worst cell needs, over the full width of the deck, as the office's slab sheets. The bands are drawn on the plan above (Shear links); they follow the shear, not the bending zones.</p>` : ""}
    ${sh.method ? `<p class="status">${esc(sh.method)}.</p>` : ""}
    <h3 style="margin-top:18px">Temperature and shrinkage restraint</h3>
    ${d.restraint?.check === "off" ? '<p class="status">Not checked (slab setting "Restraint cracking"): temperature and shrinkage come in as axial tension in the combinations, as the office\'s slab design.</p>' : `${d.restraint?.check === "report" ? '<p class="status">Reported only: it does not choose the bars.</p>' : ""}<div class="scroll"><table><tr><th>Layer</th><th>w<sub>k</sub></th><th>Limit</th><th>Details</th><th></th></tr>
      ${Object.entries(d.restraint?.layers || {}).map(([k, r]) => `<tr><td>${esc(LAYER_NAME[k] || k)}</td><td>${fmt(r.wk, 3)} mm</td><td>${fmt(r.limit, 2)} mm</td><td>ε<sub>r</sub> ${fmt(r.eps_r)} µε, s<sub>r,max</sub> ${fmt(r.sr_max)} mm</td><td>${ok(r.passed)}</td></tr>`).join("")}
    </table></div>
    <p class="status">${fmt(d.restraint?.length_m)} m between joints, R = ${fmt(d.restraint?.R, 2)} (${esc(d.restraint?.R_from || "")}), bars along the quay.</p>`}`;
  wireMeshChooser(card, d);
  if (d.strip_design) {
    mountCrackPictures(card, slabCrackItems(d));
    stationEditor(card, d);
    wireStripTable(card, d);
    barDiagrams(card, d);
    momentPlan(card, d);
  }
  const plan = card.querySelector('[data-kind="plan"]');
  const draw = (k) => slabPlan(plan, d, k);
  card.querySelectorAll("[data-layer]").forEach((b) => (b.onclick = () => {
    card.querySelectorAll("[data-layer]").forEach((x) => x.classList.toggle("on", x === b));
    draw(b.dataset.layer);
  }));
  draw(Object.keys(layers)[0]);
  const punchEl = card.querySelector('[data-kind="punch"]');
  const showPunch = (i) => {
    card.querySelectorAll("[data-punch]").forEach((r) => r.classList.toggle("on", Number(r.dataset.punch) === i));
    punchDiagram(punchEl, punch[i]);
  };
  card.querySelectorAll("[data-punch]").forEach((r) => (r.onclick = (e) => {
    if (e.target.tagName !== "INPUT") showPunch(Number(r.dataset.punch));
  }));
  card.querySelectorAll("[data-depth]").forEach((inp) => (inp.onchange = () => {
    const q = punch[Number(inp.dataset.depth)];
    const el = sec().elements[d.element];
    if (!el) return;
    // In plan: a corner berth's turned part shows its piles at their plan X, Y too.
    const [qx, qy] = [q.plan_x ?? q.x, q.plan_y ?? q.y];
    const list = (el.punching_depths || []).filter((p) => Math.hypot(p.x - qx, p.y - qy) > 0.5);
    if (inp.value !== "") list.push({ x: qx, y: qy, thickness: Number(inp.value) });
    el.punching_depths = list;
    markDirty();
  }));
  if (punch.length) {
    const worst = punch.reduce((b, q, i) => ((q.utilisation ?? 0) > (punch[b].utilisation ?? 0) ? i : b), 0);
    showPunch(worst);
  }
  return card;
}

function punchDiagram(el, q) {
  // One pile under the slab: plan with the face (u0), u1 at 2d, the link perimeters and u_out;
  // and a section through the slab with the 2d spread.
  if (!el || !q) return;
  const R = q.D_mm / 2, d = q.d_mm, h = q.thickness_mm;
  const rOut = q.r_out_mm || 0;
  const rMax = Math.max(q.r_u1_mm, rOut, R + 2 * d) * 1.12;
  const S = 300, c = S / 2, k = (S / 2 - 12) / rMax;
  const circle = (r, cls, title) => `<circle cx="${c}" cy="${c}" r="${(r * k).toFixed(1)}" class="${cls}"><title>${esc(title)}</title></circle>`;
  const links = (q.link_radii_mm || []).map((r, i) => {
    const n = Math.max(8, Math.round((2 * Math.PI * r) / Math.max(q.radial_spacing_mm, 1) / 1.5));
    return Array.from({ length: n }, (_, j) => {
      const a = (2 * Math.PI * j) / n;
      return `<circle cx="${(c + r * k * Math.cos(a)).toFixed(1)}" cy="${(c + r * k * Math.sin(a)).toFixed(1)}" r="2.2" class="pp-link"><title>Link perimeter ${i + 1}, ${fmt(r - R)} mm from the face</title></circle>`;
    }).join("");
  }).join("");
  const plan = `<div><div class="chart-title">${esc(q.pile)} at X ${fmt(q.x, 1)}, Y ${fmt(q.y, 1)}: plan</div>
    <svg viewBox="0 0 ${S} ${S}" role="img" aria-label="Punching perimeters in plan">
      ${rOut ? circle(rOut, "pp-out", `u_out = ${fmt(q.u_out_mm)} mm: no links needed beyond`) : ""}
      ${circle(q.r_u1_mm, "pp-u1", `u1 at 2d = ${fmt(2 * d)} mm from the face, ${fmt(q.u1_mm)} mm long`)}
      ${links}
      ${circle(R, "pp-pile", `Pile face u0, D = ${fmt(q.D_mm)} mm`)}
      <text class="tick" x="${c}" y="${c + R * k + 14}" text-anchor="middle">D ${fmt(q.D_mm)}</text>
      <text class="tick" x="${c + q.r_u1_mm * k * 0.72}" y="${c - q.r_u1_mm * k * 0.72}">u1</text>
      ${rOut ? `<text class="tick" x="${c + rOut * k * 0.72}" y="${c + rOut * k * 0.72 + 12}">u_out</text>` : ""}
    </svg></div>`;
  // Section: slab of thickness h over the pile, 2d lines from the face, links as vertical ticks.
  const W = 420, H = 200, m = 20;
  const span = rMax;
  const sx = (W - 2 * m) / (2 * span), sy = Math.min((H - 70) / h, sx * 1.5);
  const X = (u) => W / 2 + u * sx, top = 30, Y = (v) => top + v * sy;
  const tickLinks = (q.link_radii_mm || []).flatMap((r) => [r, -r]).map((r) => `<line x1="${X(r)}" x2="${X(r)}" y1="${Y(40)}" y2="${Y(h - 40)}" class="pp-linkline"/>`).join("");
  const section = `<div><div class="chart-title">Section through the pile: ${fmt(h)} mm (${esc(q.thickness_from)}), d = ${fmt(d)} mm</div>
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Punching section">
      <rect x="${X(-span)}" y="${Y(0)}" width="${2 * span * sx}" height="${h * sy}" class="pp-slab"/>
      <rect x="${X(-R)}" y="${Y(h)}" width="${2 * R * sx}" height="${H - 26 - Y(h)}" class="pp-pilesec"/>
      <line x1="${X(R)}" y1="${Y(h)}" x2="${X(R + 2 * d)}" y2="${Y(h - d)}" class="pp-cone"/><line x1="${X(-R)}" y1="${Y(h)}" x2="${X(-R - 2 * d)}" y2="${Y(h - d)}" class="pp-cone"/>
      <line x1="${X(R + 2 * d)}" x2="${X(R + 2 * d)}" y1="${Y(0) - 6}" y2="${Y(h) + 6}" class="pp-u1line"/><line x1="${X(-R - 2 * d)}" x2="${X(-R - 2 * d)}" y1="${Y(0) - 6}" y2="${Y(h) + 6}" class="pp-u1line"/>
      ${tickLinks}
      <text class="tick" x="${X(R + 2 * d)}" y="${Y(0) - 10}" text-anchor="middle">u1 (2d)</text>
      <text class="tick" x="${W / 2}" y="${H - 6}" text-anchor="middle">${esc(q.direction)}, V ${fmt(q.V_kN)} kN, β ${fmt(q.beta, 2)}</text>
    </svg></div>`;
  el.innerHTML = plan + section + `<p class="status" style="grid-column:1/-1">${q.needs_reinforcement
    ? `Links: ${q.perimeters} perimeters at ${fmt(q.radial_spacing_mm)} mm, the first at 0.5d from the face, ${fmt(q.asw_mm2_per_perimeter)} mm² each, out to ${fmt(q.reinforced_to_mm)} mm from the face; u_out ${fmt(q.u_out_mm)} mm (red dashed circle), beyond which the concrete alone is enough.`
    : `No links: v<sub>Ed</sub> ${fmt(q.vEd_MPa, 3)} MPa at u1 is within v<sub>Rd,c</sub> ${fmt(q.vRd_c_MPa, 3)} MPa.`}</p>`;
}

function slabPlan(el, d, key) {
  // Plan of one layer: the basic mesh everywhere, zones of heavier bars, piles. "shear": the link zones.
  const l = key === "shear"
    ? { basic: { label: "no links" }, zones: (d.shear?.links || []).map((z) => ({ ...z, as_mm2_per_m: z.asw_mm2_per_m2, additional_mm2_per_m: z.asw_mm2_per_m2 })) }
    : d.layers[key];
  const [x0, x1] = d.box.X, [y0, y1] = d.box.Y;
  const W = 820, pad = 30;
  const sc = (W - 2 * pad) / (x1 - x0);
  const H = (y1 - y0) * sc + 2 * pad;
  const flip = d.strip_design?.along === "X" && d.strip_design.sign < 0; // sea side on the left
  const X = (x) => pad + (flip ? x1 - x : x - x0) * sc, Y = (y) => H - pad - (y - y0) * sc;
  const L = (a, b) => Math.min(X(a), X(b)); // left edge of a span in plan
  const labels = [...new Set(l.zones.map((z) => z.label))].sort((a, b) => l.zones.find((z) => z.label === a).as_mm2_per_m - l.zones.find((z) => z.label === b).as_mm2_per_m);
  const shade = (lab) => `rgba(214,48,39,${0.25 + 0.6 * (labels.indexOf(lab) + 1) / Math.max(labels.length, 1)})`;
  const zones = l.zones.map((z) => `<rect x="${L(z.x[0], z.x[1])}" y="${Y(z.y[1])}" width="${(z.x[1] - z.x[0]) * sc}" height="${(z.y[1] - z.y[0]) * sc}" fill="${shade(z.label)}"><title>Additional ${esc(z.label)} (${fmt(z.additional_mm2_per_m ?? z.as_mm2_per_m)} mm²/m, ${fmt(z.as_mm2_per_m)} mm²/m with the mesh), X ${fmt(z.x[0], 1)} to ${fmt(z.x[1], 1)}, Y ${fmt(z.y[0], 1)} to ${fmt(z.y[1], 1)}</title></rect>`).join("");
  const piles = (d.punching || []).map((q) => `<circle cx="${X(q.x)}" cy="${Y(q.y)}" r="${(q.r_u1_mm / 1000) * sc}" class="${q.needs_reinforcement ? "pp-out" : "pp-u1"}"><title>${esc(q.pile)}: u1 at 2d${q.needs_reinforcement ? ", needs punching links" : ""}</title></circle>
    <circle cx="${X(q.x)}" cy="${Y(q.y)}" r="${(q.D_mm / 2000) * sc}" fill="none" stroke="var(--text)" stroke-width="1.5"><title>${esc(q.pile)}</title></circle>`).join("");
  el.innerHTML = `<div class="chart-title">${key === "shear" ? `Shear links: ${esc(labels.join(", "))} in the shaded zones, none elsewhere` : `${esc(LAYER_NAME[key] || key)}: mesh ${esc(l.basic.label)} everywhere${labels.length ? `, plus additional ${esc(labels.join(", "))} in the shaded zones` : ""}`}</div>
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Slab plan of ${esc(key)}">
      <rect x="${L(x0, x1)}" y="${Y(y1)}" width="${(x1 - x0) * sc}" height="${(y1 - y0) * sc}" fill="var(--miss-bg)" stroke="var(--muted)"/>
      ${voidLines(d, X, Y)}${zones}${piles}
      <text class="tick" x="${pad}" y="${H - 8}">X ${fmt(flip ? x1 : x0, 1)}${flip ? " (sea side)" : ""}</text><text class="tick" x="${W - pad}" y="${H - 8}" text-anchor="end">X ${fmt(flip ? x0 : x1, 1)}</text>
      <text class="tick" x="${pad - 4}" y="${Y(y1) + 4}" text-anchor="end">Y ${fmt(y1, 0)}</text><text class="tick" x="${pad - 4}" y="${Y(y0)}" text-anchor="end">${fmt(y0, 0)}</text>
    </svg>
    ${(d.punching || []).length ? `<p class="status plan-key"><svg width="16" height="16" aria-hidden="true"><circle cx="8" cy="8" r="4" fill="none" stroke="var(--text)" stroke-width="1.5"/></svg> pile head
      <svg width="16" height="16" aria-hidden="true"><circle cx="8" cy="8" r="7" class="pp-u1"/></svg> punching control perimeter u1, 2d from the pile face: no links needed
      <svg width="16" height="16" aria-hidden="true"><circle cx="8" cy="8" r="7" class="pp-out"/></svg> the same, where the pile needs punching links (see Punching at the piles)</p>` : ""}`;
}

// The slab's voids in plan: a dashed band for each void over its run.
function voidLines(d, X, Y) {
  const v = d.voids;
  if (!v?.positions?.length) return "";
  const r = v.diameter_mm / 2000;
  return v.positions.map((p) => {
    const [xa, xb, ya, yb] = v.along === "X" ? [v.run[0], v.run[1], p - r, p + r] : [p - r, p + r, v.run[0], v.run[1]];
    return `<rect x="${Math.min(X(xa), X(xb))}" y="${Y(yb)}" width="${Math.abs(X(xb) - X(xa))}" height="${Math.abs(Y(ya) - Y(yb))}" class="void-band"><title>Void Ø${fmt(v.diameter_mm)} at ${v.across} ${fmt(p, 2)}</title></rect>`;
  }).join("") + (v.piles || []).map(([px, py, pr]) => `<circle cx="${X(px)}" cy="${Y(py)}" r="${Math.abs(X(px + pr + v.solid_round_piles_m) - X(px))}" class="void-solid"><title>Solid over the pile: the voids stop ${fmt(v.solid_round_piles_m * 1000)} mm from its face</title></circle>`).join("");
}

// The voids: where they are and the voided sections the design uses.
function voidsBlock(d) {
  const v = d.voids;
  if (!v) return "";
  if (!v.positions?.length) return `<h3 style="margin-top:18px">Voids</h3><p class="status">No void fits between the piles with the clear distance set: the slab is designed solid.</p>`;
  const h = d.thickness_mm, D = v.diameter_mm, s = v.spacing_mm, tc = v.centre_depth_mm;
  const n = 3, W = 440, pad = 34, padR = 64, k = (W - pad - padR) / (n * s), Hs = h * k + 46;
  const circles = Array.from({ length: n }, (_, i) => `<circle cx="${pad + (i + 0.5) * s * k}" cy="${14 + tc * k}" r="${(D / 2) * k}" class="void-hole"/>`).join("");
  const dim = (x, y1, y2, t) => `<line x1="${x}" x2="${x}" y1="${y1}" y2="${y2}" class="void-dim"/><text class="tick" x="${x + 4}" y="${(y1 + y2) / 2 + 4}">${t}</text>`;
  const across = `<svg viewBox="0 0 ${W} ${Hs}" role="img" aria-label="Section across the voids">
      <rect x="${pad}" y="14" width="${n * s * k}" height="${h * k}" class="pp-slab"/>${circles}
      ${dim(pad + n * s * k + 6, 14, 14 + v.flange_top_mm * k, fmt(v.flange_top_mm))}
      ${dim(pad + n * s * k + 6, 14 + (h - v.flange_bottom_mm) * k, 14 + h * k, fmt(v.flange_bottom_mm))}
      <text class="tick" x="${pad + s * k}" y="${14 + tc * k + 4}" text-anchor="middle">${fmt(v.web_mm)}</text>
      <text class="tick" x="${pad + 0.5 * s * k}" y="${Hs - 8}" text-anchor="middle">Ø${fmt(D)} @ ${fmt(s)}</text>
    </svg>`;
  const along = `<svg viewBox="0 0 ${W} ${Hs}" role="img" aria-label="Section along a void">
      <rect x="${pad}" y="14" width="${W - 2 * pad}" height="${h * k}" class="pp-slab"/>
      <rect x="${pad + 30}" y="${14 + (tc - D / 2) * k}" width="${W - 2 * pad - 60}" height="${D * k}" class="void-hole"/>
      <text class="tick" x="${W / 2}" y="${Hs - 8}" text-anchor="middle">along the void: ${fmt(v.flange_top_mm)} mm above, ${fmt(v.flange_bottom_mm)} mm below</text>
    </svg>`;
  return `<h3 style="margin-top:18px">Voids</h3>
    <p>${v.positions.length} voids Ø${fmt(D)} at ${fmt(s)} mm along ${esc(v.along)}, from ${fmt(v.run[0], 2)} to ${fmt(v.run[1], 2)} m (${esc(v.run_from)}), centre ${fmt(tc)} mm below the top; ${fmt(v.void_share_pct, 1)}% of the slab's concrete (${fmt(v.void_m3, 1)} m³)${v.solid_round_piles_m != null ? `; they stop ${fmt(v.solid_round_piles_m * 1000)} mm short of every pile's face` : ""}${v.left_out?.length ? `; ${v.left_out.length} left out on the lines of piles (${esc(v.across)} ${v.left_out.map((p) => fmt(p, 2)).join(", ")})` : ""}.</p>
    <div class="charts"><div><div class="chart-title">Across the voids (bars along ${esc(v.along)})</div>${across}</div><div><div class="chart-title">Along a void (bars across the voids)</div>${along}</div></div>
    <p class="status">Bending: the compression block on the concrete left at each depth (the circles for the bars along the voids, only the solid top and bottom over a void for the bars across them); the solid slab's result wherever the block stays in the solid part. Crack widths with the voided compression zone. Shear: the webs between the voids (b<sub>w</sub> ${fmt(100 * (1 - D / s))}% of the width) with links in the webs only. Punching: on the solid slab, as the voids stop short of the piles. Outside the voided area (the ends, and ${fmt(s / 2)} mm beyond the outer voids) the slab is solid.</p>`;
}

// ---------------------------------------------------------------- Beams
const BEAM_KIND = { front_beam: "Front beam", rear_beam: "Rear beam", transverse_beam: "Transverse beam" };

// The sheet pile wall card (spw.js), with its straining actions for Durability underneath.
function spwWithSets(w) {
  const card = spwCard(w, { fmt, esc, frame, showTip, v3dSlot, onIgnore: saveSpwIgnore });
  card.insertAdjacentHTML("beforeend", `<details style="margin-top:12px"><summary>Governing straining actions (Plaxis sign, for Durability)</summary>${steelSetsBlock(w.governing_sets, "kN/m, kNm/m", true)}</details>`);
  return card;
}

async function saveSpwIgnore(name, rules) {
  const el = sec().elements[name];
  if (!el) return "This wall is not on the Elements tab.";
  el.ignore = rules;
  markDirty();
  await save();
  if (state.errors?.length) return state.errors.map((e) => e.msg).join(" ");
  state.runDesign?.([name]);
  return null;
}

function beamCard(b) {
  const card = document.createElement("div");
  card.className = "panel";
  card.style.marginTop = "16px";
  const c = b.cage;
  const bend = b.bending || {};
  const g = bend.governing || {};
  const sh = b.shear || {};
  const tr = b.transverse || {};
  const st = b.steel || {};
  const r = b.restraint || {};
  const ok = (x) => `<span class="sev ${x ? "ok" : "error"}">${x ? "passes" : "fails"}</span>`;
  const ex = bend.extremes || {};
  const exRow = (k, label, unit) => ex[k] ? `<tr><td>${label}</td><td>${fmt(ex[k].max)} / ${fmt(ex[k].min)} ${unit}</td></tr>` : "";
  const worstCrack = Object.values(b.cracks || {}).reduce((m, x) => Math.max(m, x.wk), 0);
  card.innerHTML = `<div class="element-head"><h3>${esc(b.key || b.element)}<span class="type">${partText(b)}${esc(BEAM_KIND[b.kind] || "Beam")}, ${fmt(b.width_mm)} × ${fmt(b.depth_mm)} mm, ${esc(b.concrete || "")}, cover ${fmt(b.cover_mm)} mm</span></h3>
      ${ok(b.passed)}</div>
    ${overWarn((b.ductility?.warnings || []).map((w) => `${w}.`))}
    <div class="counts" style="margin-top:0">
      <div class="count"><b>${fmt(b.utilisation, 2)}</b>max utilisation (all checks)</div>
      <div class="count"><b>${fmt(bend.utilisation, 2)}</b>N with biaxial bending</div>
      <div class="count"><b>${fmt(sh.utilisation, 2)}</b>shear and torsion</div>
      <div class="count"><b>${fmt(worstCrack, 2)} mm</b>largest QP crack width</div>
      <div class="count"><b>${fmt(st.kg_per_m3)}</b>kg/m³ (${fmt(st.kg_per_m)} kg/m)</div>
      ${st.element_total_t != null ? `<div class="count"><b>${fmt(st.element_total_t, 1)} t</b>steel over ${fmt(st.length_m, 1)} m</div>` : ""}
    </div>
    ${b.frame_note ? `<p class="status">${esc(b.frame_note)}</p>` : ""}
    ${b.notes.map((n) => `<p class="status">${esc(n)}</p>`).join("")}
    ${v3dSlot(b.element)}
    ${c ? `<h3 style="margin-top:18px">Longitudinal cage, one for the whole beam</h3>
    <div class="cage"><div class="chart" data-kind="section"></div><div class="scroll"><table>
      <tr><th>Face</th><th>Bars</th></tr>
      <tr><td>Top</td><td>${c.top.count}Ø${c.top.phi}${c.top.layers > 1 ? ` in ${c.top.layers} layers` : ""}</td></tr>
      <tr><td>Bottom</td><td>${c.bottom.count}Ø${c.bottom.phi}${c.bottom.layers > 1 ? ` in ${c.bottom.layers} layers` : ""}</td></tr>
      <tr><td>Each side</td><td>${c.side.count ? `${c.side.count}Ø${c.side.phi}` : "none"}</td></tr>
      <tr><td>Total</td><td>${fmt(c.area_mm2)} mm², ${fmt(c.ratio_pct, 2)}%, ${fmt(c.kg_per_m, 1)} kg/m</td></tr>
      <tr><th colspan="2">Actions, max / min (ULS, at support faces and between)</th></tr>
      ${exRow("N", "N (compression +)", "kN")}${exRow("Mv", "M vertical (sagging +)", "kNm")}${exRow("Mh", "M horizontal", "kNm")}
      ${exRow("V", "V vertical", "kN")}${exRow("Vh", "V horizontal", "kN")}${exRow("T", "Torsion", "kNm")}
    </table></div></div>
    ${beamCageHtml(b)}
    ${faceNeeds(b)}
    ${jointsBlock(b.construction_joints)}
    ${g.combination ? `<p>Governing bending: ${esc(g.combination)} at ${fmt(g.s, 2)} m along the beam. N = ${fmt(g.N_kN)} kN, M<sub>v</sub> = ${fmt(g.Mv_kNm)} kNm (M<sub>Rd</sub> ${fmt(g.MRd_v_kNm)}), M<sub>h</sub> = ${fmt(g.Mh_kNm)} kNm (M<sub>Rd</sub> ${fmt(g.MRd_h_kNm)}), exponent a = ${fmt(g.a, 2)}. ${esc(bend.method || "")}.</p>` : ""}
    <div class="row" data-kind="limit-switch">${limitSwitch(b.profile_qp?.length, "Moment and utilisation diagrams:")}</div>
    <div class="charts"><div class="chart" data-kind="moments"></div><div class="chart" data-kind="profile"></div></div>` : ""}
    <h3 style="margin-top:18px">Crack widths</h3>
    <div class="scroll"><table><tr><th>Check</th><th>Face</th><th>w<sub>k</sub></th><th>Limit</th><th>Details</th><th></th></tr>
      ${Object.entries(b.cracks || {}).map(([f, x]) => `<tr><td>QP loads (7.3.4)</td><td>${f}</td><td>${fmt(x.wk, 3)} mm</td><td>${fmt(x.limit, 2)} mm</td>
        <td>${x.sigma_s > 0 ? `σ<sub>s</sub> ${fmt(x.sigma_s)} MPa, s<sub>r,max</sub> ${fmt(x.sr_max)} mm, ${esc(x.combination)} at ${fmt(x.s, 1)} m` : "no tension at this face under QP loads"}</td><td>${ok(x.passed)}</td></tr>`).join("")}
      ${Object.entries(r.faces || {}).map(([f, x]) => `<tr><td>Restraint</td><td>${f}</td><td>${isFinite(x.wk) ? `${fmt(x.wk, 3)} mm` : "–"}</td><td>${fmt(x.limit, 2)} mm</td>
        <td>${x.note ? esc(x.note) : `ε<sub>r</sub> ${fmt(x.eps_r)} µε, crack strain ${fmt(x.eps_cr)} µε, s<sub>r,max</sub> ${fmt(x.sr_max)} mm`}</td><td>${ok(x.passed)}</td></tr>`).join("")}
    </table></div>
    ${crackPicturesHtml(beamCrackItems(b))}
    ${r.faces ? `<p class="status">Restraint: ${fmt(r.length_m)} m between joints, R = ${fmt(r.R, 2)} (${esc(r.R_from)}), K1 = ${fmt(r.K1, 2)}, T1 = ${fmt(r.T1)} °C, T2 = ${fmt(r.T2)} °C, plus autogenous shrinkage (EN 1992-3 Annex M, CIRIA C660).</p>` : ""}
    ${sh.link ? `<h3 style="margin-top:18px">Links ${ok(sh.passed)}</h3>
    <p><b>${esc(sh.link.label)}</b>, ${fmt(sh.link.kg_per_m, 1)} kg/m, one arrangement for the whole beam (largest spacing ${fmt(sh.max_spacing_mm)} mm).
      ${sh.governing ? `Governing: ${esc(sh.governing.combination)} at ${fmt(sh.governing.s, 2)} m, V = ${fmt(sh.governing.V_kN)} kN, T = ${fmt(sh.governing.T_kNm)} kNm, N = ${fmt(sh.governing.N_kN)} kN; V<sub>Rd,c</sub> ${fmt(sh.governing.VRd_c_kN)} kN${sh.governing.N_kN < 0 ? " (tension: no concrete contribution)" : ""}, V<sub>Rd,max</sub> ${fmt(sh.governing.VRd_max_kN)} kN, T<sub>Rd,max</sub> ${fmt(sh.governing.TRd_max_kNm)} kNm, cot θ ${fmt(sh.governing.cot_theta, 2)}.` : ""}</p>
    <p class="status">${esc(sh.method)}. Horizontal shear ${fmt(sh.horizontal?.V_kN)} kN. Torsion needs ${fmt(sh.torsion_long_steel_mm2)} mm² of longitudinal steel around the perimeter.${sh.transverse_shear_needs_links ? " The transverse shear per metre also needs links; they are included." : ""}</p>
    ${(sh.notes || []).map((n) => `<p class="status">${esc(n)}</p>`).join("")}` : ""}
    ${tr.top ? `<h3 style="margin-top:18px">Transverse bars (per metre, across the beam) ${ok(tr.passed)}</h3>
    <p>Top <b>${esc(tr.top.label)}</b> (${fmt(tr.top.as_mm2_per_m)} mm²/m), bottom <b>${esc(tr.bottom.label)}</b> (${fmt(tr.bottom.as_mm2_per_m)} mm²/m). Bending utilisation ${fmt(tr.utilisation, 2)}${tr.governing ? `, governed by ${esc(tr.governing.combination)} at ${fmt(tr.governing.s, 1)} m, M = ${fmt(tr.governing.M_kNm_per_m)} kNm/m with N = ${fmt(tr.governing.N_kN_per_m)} kN/m` : ""}.
      ${Object.entries(tr.cracks || {}).map(([f, x]) => `QP crack at the ${f}: ${fmt(x.wk, 3)} mm of ${fmt(x.limit, 2)}.`).join(" ")}</p>` : ""}
    ${b.bollard ? `<h3 style="margin-top:18px">Bollard tie bars ${ok(b.bollard.passed)}</h3>
    <p>${fmt(b.bollard.capacity_t)} t bollard: F<sub>Ed</sub> = ${fmt(b.bollard.F_Ed_kN)} kN against ${fmt(b.bollard.R_kN)} kN from ${esc(b.bollard.ties.map((t) => `${t.bars} at ${fmt(t.angle_deg)}°`).join(", "))} (utilisation ${fmt(b.bollard.tie_utilisation, 2)}). Laps with the slab bottom bars: ${fmt(Math.max(...b.bollard.laps.map((x) => x.l0_mm)))} mm needed, ${fmt(b.bollard.lap_length_mm)} mm given.</p>
    <p class="status">${esc(b.bollard.method)}</p>` : ""}
    ${b.truss?.cases ? `<h3 style="margin-top:18px">Truss between king piles ${ok(b.truss.passed)}</h3>
    <p>King piles ${fmt(b.truss.spacing_m, 2)} m apart${b.truss.spacing_from === "input" ? "" : " (from the workbook)"}, lever arm ${fmt(b.truss.lever_arm_mm)} mm, θ = ${fmt(b.truss.theta_deg, 1)}°: T = ${fmt(b.truss.tie_factor, 3)} P on ${fmt(b.truss.As_provided_mm2)} mm² of bottom bars at ${fmt(b.truss.working_stress_MPa)} MPa.</p>
    <div class="scroll"><table><tr><th>Case</th><th>Slab mm</th><th>P kN</th><th>T kN</th><th>A<sub>s,req</sub> mm²</th><th>Ratio</th></tr>
      ${b.truss.cases.map((c) => `<tr><td>${esc(c.case)}</td><td>${fmt(c.slab_thickness_mm)}</td><td>${fmt(c.P_kN)}</td><td>${fmt(c.T_kN)}</td><td>${fmt(c.As_req_mm2)}</td>
        <td class="cell ${c.utilisation <= 1 ? "ok" : "error"}">${fmt(c.utilisation, 2)}</td></tr>`).join("")}</table></div>
    <p class="status">${esc(b.truss.method)}</p>` : b.truss?.note ? `<p class="status">${esc(b.truss.note)}</p>` : ""}
    ${b.rooms?.length ? `<p class="status">${b.rooms.length === 1 ? "A room is" : `${b.rooms.length} rooms are`} cut into this beam: see the Openings tab (${b.rooms.every((r) => r.passed) ? "all pass" : "some fail"}).</p>` : ""}
    ${setsBlock(b.governing_sets, "N in the concrete sign convention (compression +). M3 is the vertical bending of the beam section (sagging +), M2 the horizontal bending; z is the position along the beam.")}`;
  mountCrackPictures(card, beamCrackItems(b));
  wireBeamCage(card, b);
  if (c?.bars) beamSection(card.querySelector('[data-kind="section"]'), b);
  if (b.profile?.length) {
    const drawBeam = (mode) => {
      const qp = mode === "qp";
      const rows = qp ? b.profile_qp : b.profile;
      beamMoments(card.querySelector('[data-kind="moments"]'), rows, b.supports, b.support_results === "faces", mode);
      alongChart(card.querySelector('[data-kind="profile"]'), rows.map((q) => ({ z: q.s, util: q.u })),
        qp ? "Crack width / limit along the beam, SLS (QP)" : "Utilisation along the beam, ULS", qp ? "wk / limit" : "Utilisation",
        (q) => q.util, 1, b.supports, b.support_results === "faces");
    };
    drawBeam("uls");
    wireLimitSwitch(card.querySelector('[data-kind="limit-switch"]'), drawBeam);
  }
  return card;
}

// A room cut into a beam or a channel in the deck: the section left and its extra bars.
function troughHtml(rm, key, kind) {
  const ok = (x) => `<span class="sev ${x ? "ok" : "error"}">${x ? "passes" : "fails"}</span>`;
  const u = (x) => `<td class="cell ${x != null && x <= 1 ? "ok" : "error"}">${fmt(x, 2)}</td>`;
  const room = kind === "room";
  const where = room
    ? `${fmt(rm.start_m, 2)} to ${fmt(rm.end_m, 2)} m along the beam`
    : `along ${esc(rm.direction || "")} from ${fmt(rm.start_m, 2)} to ${fmt(rm.end_m, 2)} m, centre line at ${fmt(rm.at_m, 2)} m`;
  const head = `<h3 style="margin-top:18px">${esc(rm.name)}, ${where} ${ok(rm.passed)}</h3>`;
  if (!rm.section) return `${head}${(rm.notes || []).map((n) => `<p class="status">${esc(n)}</p>`).join("")}`;
  const x = rm.section, bars = rm.bars || {}, fr = rm.frame || {}, g = rm.bending?.governing || {};
  const walls = Object.entries(rm.shear || {}).filter(([k]) => k === "sea" || k === "land");
  const partName = room ? { sea: "Sea-side wall", land: "Land-side wall", floor: "Floor", roof: "Roof" } : { sea: "Wall", land: "Other wall", floor: "Base", roof: "Roof" };
  const floor = room ? "floor" : "base";
  const shown = room ? walls : walls.slice(0, 1);
  const crack = Object.entries(rm.cracks || {}).map(([f, c]) => `QP crack at the ${f === "top" ? "top of the walls" : "bottom"}: ${fmt(c.wk, 3)} mm of ${fmt(c.limit, 2)}`).join("; ");
  const perM = (p) => p ? `top ${esc(p.top.label)}, bottom ${esc(p.bottom.label)} (utilisation ${fmt(p.utilisation, 2)}${Object.entries(p.cracks || {}).map(([f, c]) => `, crack ${f} ${fmt(c.wk, 3)} of ${fmt(c.limit, 2)} mm`).join("")})` : "";
  return `${head}
    ${(rm.warnings || []).map((w) => `<p class="status sev error" style="display:block">${esc(w)}</p>`).join("")}
    ${rm.suggestion ? `<p><b>${esc(rm.suggestion.text)}</b></p>` : ""}
    <div class="counts" style="margin-top:0">
      <div class="count"><b>${fmt(rm.utilisation, 2)}</b>utilisation (all checks)</div>
      <div class="count"><b>${fmt(x.bottom_mm)} mm</b>concrete below, ${kind} ${fmt(x.height_mm)} deep</div>
      <div class="count"><b>${room ? `${fmt(x.wall_sea_mm)} / ${fmt(x.wall_land_mm)} mm` : `${fmt(x.wall_sea_mm)} mm`}</b>${room ? "walls, sea / land side" : "walls"}</div>
      ${rm.downstand_mm ? `<div class="count"><b>${fmt(rm.downstand_mm)} mm</b>below the slab soffit</div>` : ""}
      ${rm.extra_steel_kg != null ? `<div class="count"><b>${fmt(rm.extra_steel_kg)} kg</b>extra bars and links over ${fmt(rm.length_m, 2)} m</div>` : ""}
    </div>
    <div class="cage"><div class="chart" data-open-section="${esc(key)}"></div><div class="scroll"><table>
      <tr><th>Check along the ${kind}</th><th>Utilisation</th></tr>
      <tr><td>N with biaxial bending on the section left${room ? "" : ` (a strip ${fmt(rm.strip_mm)} mm wide)`}</td>${u(rm.bending?.utilisation)}</tr>
      ${shown.map(([k, w]) => `<tr><td>${partName[k]}: shear and torsion</td>${u(w.utilisation)}</tr>`).join("")}
      ${["floor", "roof"].filter((k) => rm.shear?.[k]).map((k) => `<tr><td>${partName[k]}: torsion</td>${u(rm.shear[k].utilisation)}</tr>`).join("")}
      ${Object.entries(rm.cracks || {}).map(([f, c]) => `<tr><td>QP crack, ${f === "top" ? "top of the walls" : "bottom"}</td>${u(c.wk / c.limit)}</tr>`).join("")}
      <tr><td>${partName.floor} across, per metre</td>${u(fr.floor?.utilisation)}</tr>
      <tr><td>${partName.floor} shear per metre</td>${u(fr.floor_shear?.utilisation)}</tr>
      <tr><td>Walls at the corners, per metre</td>${u(fr.walls?.utilisation)}</tr>
    </table></div></div>
    <div class="scroll"><table><tr><th>Bars over its length</th><th></th></tr>
      <tr><td>${room ? "Beam" : "Slab"} top bars cut</td><td>${bars.cut_top?.count ? `${bars.cut_top.count}Ø${bars.cut_top.phi} (${fmt(bars.cut_top.area_mm2)} mm²)` : "none"}</td></tr>
      <tr><td>Top of each wall (replaces them)</td><td><b>${esc(bars.wall_top?.label || "none")}</b>, ${fmt(rm.corners?.wall_top_bars_past_ends_mm)} mm past each end</td></tr>
      <tr><td>Extra bottom bars (a layer above)</td><td>${esc(bars.bottom_extra?.label || "none")}</td></tr>
      <tr><td>Inside faces of the walls</td><td>${esc(bars.inner_sides?.label || "none")}</td></tr>
      ${shown.map(([k, w]) => `<tr><td>${room ? partName[k] : "Each wall"}: links</td><td>${w.link ? esc(w.link.label) : "none fit"}</td></tr>`).join("")}
      ${["floor", "roof"].filter((k) => rm.shear?.[k]?.link).map((k) => `<tr><td>${partName[k]} links (torsion)</td><td>${esc(rm.shear[k].link.label)}</td></tr>`).join("")}
      <tr><td>${partName.floor}, across, per metre</td><td>${perM(fr.floor)}</td></tr>
      ${fr.floor_shear?.links ? `<tr><td>${partName.floor} shear links</td><td>${esc(fr.floor_shear.links)}</td></tr>` : ""}
      <tr><td>Walls, vertical bars each face, per metre</td><td>${esc(fr.walls?.top?.label || "")}</td></tr>
      <tr><td>Inside corners</td><td>${esc(fr.corner_bars?.label || "")}</td></tr>
      <tr><td>Corners of the opening</td><td>${esc(rm.corners?.diagonals || "")}</td></tr>
    </table></div>
    ${g.combination ? `<p>Governing bending: ${esc(g.combination)} at ${fmt(g.s, 2)} m, N = ${fmt(g.N_kN)} kN, M<sub>v</sub> = ${fmt(g.Mv_kNm)} kNm (M<sub>Rd</sub> ${fmt(g.MRd_v_kNm)}), M<sub>h</sub> = ${fmt(g.Mh_kNm)} kNm (M<sub>Rd</sub> ${fmt(g.MRd_h_kNm)}).${crack ? ` ${crack}.` : ""}</p>` : ""}
    ${shown.map(([k, w]) => w.governing ? `<p class="status">${room ? partName[k] : "Each wall"} (${fmt(w.b_mm)} mm, ${fmt(100 * w.V_share)}% of V, ${fmt(100 * w.T_share)}% of T): V = ${fmt(w.governing.V_kN)} kN, T = ${fmt(w.governing.T_kNm)} kNm, V<sub>Rd,c</sub> ${fmt(w.governing.VRd_c_kN)} kN, V<sub>Rd,max</sub> ${fmt(w.governing.VRd_max_kN)} kN, T<sub>Rd,max</sub> ${fmt(w.governing.TRd_max_kNm)} kNm, cot θ ${fmt(w.governing.cot_theta, 2)}${w.torsion_long_steel_mm2 ? `; torsion takes ${fmt(w.torsion_long_steel_mm2)} mm² of its longitudinal bars` : ""}.</p>` : "").join("")}
    <p class="status">Across: M = ${fmt(fr.M_kNm_per_m?.max)} / ${fmt(fr.M_kNm_per_m?.min)} kNm/m from the plates, plus the ${partName.floor.toLowerCase()}'s own span of ${fmt(fr.floor_load?.span_m, 2)} m under ${fmt(fr.floor_load?.g_kPa, 1)} kPa self weight and ${fmt(fr.floor_load?.q_kPa, 1)} kPa load.</p>
    ${(rm.notes || []).map((n) => `<p class="status">${esc(n)}</p>`).join("")}`;
}

function troughSection(el, w, h, rm) {
  // The section left, to scale: outline, the room or channel, every bar.
  const [u0, u1, v0, v1] = rm.section.void_mm;
  const pad = Math.max(w, h) * 0.06;
  const bars = (rm.bars?.all || []).map(([u, v, phi]) => `<circle class="bar" cx="${u}" cy="${-v}" r="${phi / 2}"><title>Ø${phi}</title></circle>`).join("");
  el.innerHTML = `<div class="chart-title">Section through ${esc(rm.name)}: ${fmt(w)} × ${fmt(h)} mm, inside ${fmt(rm.section.width_mm)} × ${fmt(rm.section.height_mm)} mm</div>
    <svg viewBox="${-w / 2 - pad} ${-h / 2 - pad} ${w + 2 * pad} ${h + 2 * pad}" role="img" aria-label="Section through the opening">
      <rect class="outline" x="${-w / 2}" y="${-h / 2}" width="${w}" height="${h}"/>
      ${rm.section.top_mm
        ? `<rect x="${u0}" y="${-v1}" width="${u1 - u0}" height="${v1 - v0}" style="fill:var(--panel);stroke:var(--text);stroke-width:6"/>`
        : `<rect x="${u0}" y="${-v1 - 8}" width="${u1 - u0}" height="${v1 - v0 + 8}" style="fill:var(--panel)"/>
           <polyline points="${u0},${-v1} ${u0},${-v0} ${u1},${-v0} ${u1},${-v1}" style="fill:none;stroke:var(--text);stroke-width:6"/>`}
      ${bars}</svg>`;
}

function manholeHtml(m) {
  const ok = (x) => `<span class="sev ${x ? "ok" : "error"}">${x ? "passes" : "fails"}</span>`;
  const u = (x) => `<td class="cell ${x != null && x <= 1 ? "ok" : "error"}">${fmt(x, 2)}</td>`;
  const head = `<h3 style="margin-top:18px">${esc(m.name)}${m.x != null ? `, ${fmt(m.size_x_mm)} × ${fmt(m.size_y_mm)} mm at X ${fmt(m.x, 2)}, Y ${fmt(m.y, 2)}${m.through ? "" : `, a pit ${fmt(m.depth_mm)} mm deep`}` : ""} ${ok(m.passed)}</h3>`;
  if (!m.directions) return `${head}${(m.notes || []).map((n) => `<p class="status">${esc(n)}</p>`).join("")}`;
  const dirs = Object.entries(m.directions);
  return `${head}
    ${m.suggestion ? `<p><b>${esc(m.suggestion.text)}</b></p>` : ""}
    <div class="scroll"><table><tr><th>Bars along</th><th>Cut over</th><th>Strips each side</th><th>M round it (kNm/m)</th><th>Slab has (top / bottom)</th><th>Strips need (top / bottom)</th><th>Trimmer bars, top</th><th>Trimmer bars, bottom</th><th>Bending and cracks</th><th>Shear</th></tr>
      ${dirs.map(([k, d]) => `<tr><td>${k}</td><td>${fmt(d.cut_width_mm)} mm</td><td>${fmt(d.strip_mm)} mm (× ${fmt(d.factor, 2)})</td>
        <td>${fmt(d.M_kNm_per_m.max)} / ${fmt(d.M_kNm_per_m.min)}</td><td>${fmt(d.existing.top.as_mm2_per_m)} / ${fmt(d.existing.bottom.as_mm2_per_m)} mm²/m</td>
        <td>${esc(d.strip.top.label)} / ${esc(d.strip.bottom.label)}</td>
        <td><b>${esc(d.trimmers.top.label)}</b>, ${fmt(d.trimmers.top.length_mm)} long</td><td><b>${esc(d.trimmers.bottom.label)}</b>, ${fmt(d.trimmers.bottom.length_mm)} long</td>
        ${u(Math.max(d.strip.utilisation ?? 99, ...Object.values(d.strip.cracks || {}).map((c) => c.wk / c.limit)))}${u(d.shear.utilisation)}</tr>`).join("")}
    </table></div>
    <p class="status">${esc(m.corners.diagonals)}.${dirs.map(([k, d]) => d.shear.links ? ` Bars along ${k}: ${esc(d.shear.links)}.` : "").join("")}</p>
    ${m.piles?.length ? `<p class="status">Piles near it (EC2 6.4.2(3)): ${m.piles.map((p) => `${esc(p.pile)} at X ${fmt(p.x, 2)}, Y ${fmt(p.y, 2)} loses ${fmt(100 * p.share)}% of its punching perimeter, utilisation ${fmt(p.utilisation_before, 2)} → ${fmt(p.utilisation, 2)}`).join("; ")}.</p>` : ""}
    ${m.pit ? `<p class="status">Pit floor ${fmt(m.pit.thickness_mm)} mm over ${fmt(m.pit.span_m, 2)} m: top ${esc(m.pit.top.label)}, bottom ${esc(m.pit.bottom.label)}, utilisation ${fmt(m.pit.utilisation, 2)}.</p>` : ""}
    ${(m.notes || []).map((n) => `<p class="status">${esc(n)}</p>`).join("")}`;
}

// Openings: rooms in the beams, manholes and channels in the deck, entered and checked per section.
async function renderOpeningsTab(host) {
  const section = sec();
  const idx = secIndex();
  const defs = SCHEMA.$defs;
  const beams = Object.entries(section.elements).filter(([, e]) => ["front_beam", "rear_beam", "transverse_beam"].includes(e.kind));
  const slabs = Object.entries(section.elements).filter(([, e]) => e.kind === "slab");
  host.innerHTML = `<p class="sub">Cuts the Plaxis model does not have: rooms in a beam (e.g. for electrical work), manholes and pits in the deck,
    and service channels. Each is checked on the concrete that is left, with the Plaxis actions where it is, and gets its own extra bars.
    Enter them here, then check them (their elements are designed again).</p>
    <div class="panel"><div class="row" style="gap:10px;flex-wrap:wrap"><button id="op-run">Check openings</button><span class="status" id="op-status"></span></div></div>
    <div id="op-inputs"></div><div id="op-out"></div>`;
  const inputs = host.querySelector("#op-inputs");
  if (!beams.length && !slabs.length) {
    inputs.innerHTML = `<p class="empty">Add a beam or slab element first (Elements tab).</p>`;
    host.querySelector("#op-run").disabled = true;
    return;
  }
  const editor = (name, el, keys, def) => {
    const schema = { properties: Object.fromEntries(keys.map((k) => [k, def.properties[k]])) };
    const card = document.createElement("div");
    card.className = "panel";
    card.style.marginBottom = "16px";
    card.innerHTML = `<div class="element-head"><h3>${esc(name)}<span class="type">${esc(KIND_LABEL[el.kind] || el.kind)}</span></h3></div>`;
    const fs = renderObject(schema, el, `sections.${idx}.elements.${name}`, "");
    fs.style.border = "0";
    fs.style.padding = "0";
    card.append(fs);
    if (state.project.locked) card.querySelectorAll("input, select, textarea, button").forEach((x) => (x.disabled = true));
    inputs.append(card);
  };
  for (const [n, e] of beams) editor(n, e, ["rooms"], defs.BeamInput);
  for (const [n, e] of slabs) editor(n, e, ["manholes", "channels"], defs.SlabInput);
  if (state.project.locked)
    inputs.insertAdjacentHTML("afterbegin", `<p class="status">Locked since the design: unlock to change the openings.</p>`);
  const withOpenings = () => [...beams.filter(([, e]) => e.rooms?.length), ...slabs.filter(([, e]) => e.manholes?.length || e.channels?.length)].map(([n]) => n);
  const status = host.querySelector("#op-status");
  host.querySelector("#op-run").onclick = async () => {
    const names = withOpenings();
    if (!names.length) return (status.textContent = "No openings entered yet.");
    if (state.dirty) await save();
    if (state.errors?.length) return (status.textContent = state.errors.map((e) => e.msg).join(" "));
    status.textContent = `Designing ${names.join(", ")} with their openings…`;
    await designJob(section, names);
  };
  const out = host.querySelector("#op-out");
  let res;
  try {
    res = await api(`${secUrl()}/design`);
  } catch {
    return;
  }
  if (!state.project.locked) {
    out.innerHTML = `<div class="panel"><p style="margin:0">Results are hidden while the model is unlocked. Check the openings to see them.</p></div>`;
    return;
  }
  const draws = [];
  let html = "";
  for (const b of res.beams || []) {
    if (!b.rooms?.length) continue;
    html += `<h2>Rooms in ${esc(b.key || b.element)}</h2><div class="panel">`;
    b.rooms.forEach((rm, i) => {
      const key = `r-${b.key || b.element}-${i}`;
      html += troughHtml(rm, key, "room");
      if (rm.section) draws.push([key, b.width_mm, b.depth_mm, rm]);
    });
    html += "</div>";
  }
  for (const d of res.slabs || []) {
    const op = d.openings;
    if (!op || !(op.manholes?.length || op.channels?.length)) continue;
    const label = esc(d.key || d.element);
    if (op.manholes?.length) html += `<h2>Manholes and pits in ${label}</h2><div class="panel">${op.manholes.map(manholeHtml).join("")}</div>`;
    if (op.channels?.length) {
      html += `<h2>Channels in ${label}</h2><div class="panel">`;
      op.channels.forEach((c, i) => {
        const key = `c-${d.key || d.element}-${i}`;
        html += troughHtml(c, key, "channel");
        if (c.section) draws.push([key, c.strip_mm, c.depth_total_mm, c]);
      });
      html += "</div>";
    }
  }
  out.innerHTML = html || `<p class="status">No openings in the last design. Enter them above and check them.</p>`;
  for (const [key, w, h, rm] of draws) {
    const el = [...out.querySelectorAll("[data-open-section]")].find((x) => x.dataset.openSection === key);
    if (el) troughSection(el, w, h, rm);
  }
}

// A beam's longitudinal bars set by hand, face by face; Check designs just that beam with them.
function beamCageHtml(b) {
  if (!b.cage) return "";
  const own = sec().beam_cages?.[b.key || b.element];
  const c = b.cage;
  const v = own || {
    top: { count: c.top.count, diameter: c.top.phi, layers: c.top.layers },
    bottom: { count: c.bottom.count, diameter: c.bottom.phi, layers: c.bottom.layers },
    side: { count: c.side.count, diameter: c.side.phi || 20, layers: 1 },
  };
  const bars = (state.project.design?.reinforcement?.bar_diameters || [16, 20, 25, 32]).filter((d) => d >= 12);
  const opt = (cur) => bars.map((d) => `<option value="${d}" ${Number(cur) === d ? "selected" : ""}>Ø${d}</option>`).join("");
  const row = (f, name, layers) => `<div class="row" style="gap:10px;align-items:end;margin-top:6px" data-beam-face="${f}">
      <span class="status" style="min-width:80px">${name}</span>
      <label>Bars${layers ? " per layer" : ""} <input type="number" min="0" step="1" data-k="count" value="${v[f].count}" style="width:70px"></label>
      <label>Bar <select data-k="diameter">${opt(v[f].diameter)}</select></label>
      ${layers ? `<label>Layers <input type="number" min="1" max="4" step="1" data-k="layers" value="${v[f].layers}" style="width:60px"></label>` : ""}</div>`;
  return `<details class="panel cage-set" data-beam-cage ${own ? "open" : ""}><summary>${own ? "Bars set by you (checked, not chosen by Triton)" : "Change bars and re-check"}</summary>
    ${row("top", "Top", true)}${row("bottom", "Bottom", true)}${row("side", "Each side", false)}
    <div class="row" style="margin-top:8px;gap:10px;flex-wrap:wrap;align-items:center"><span data-beam-sum></span></div>
    <div class="row" style="margin-top:10px;flex-wrap:wrap;gap:10px">
      <button data-beam-check>Re-check</button>
      ${own ? `<button class="quiet" data-beam-auto>Let Triton choose again</button>` : ""}
      <span class="status" data-beam-status>${own ? "" : "Re-check designs only this beam with your bars: bending, cracks, restraint, links and the truss are worked out again."}</span>
    </div></details>`;
}

function wireBeamCage(card, b) {
  const box = card.querySelector("[data-beam-cage]");
  if (!box) return;
  const status = box.querySelector("[data-beam-status]");
  const read = () => Object.fromEntries(["top", "bottom", "side"].map((f) => {
    const r = box.querySelector(`[data-beam-face="${f}"]`);
    const get = (k) => r.querySelector(`[data-k="${k}"]`)?.value;
    return [f, { count: Math.round(Number(get("count"))), diameter: Number(get("diameter")), layers: Math.round(Number(get("layers") ?? 1)) }];
  }));
  const sum = () => {
    const v = read();
    const a = (x, n = 1) => (x.count * x.layers * Math.PI * x.diameter ** 2) / 4 * n;
    const tot = a(v.top) + a(v.bottom) + a(v.side, 2);
    box.querySelector("[data-beam-sum]").innerHTML = `Top ${fmt(a(v.top))} mm², bottom ${fmt(a(v.bottom))} mm², sides 2 × ${fmt(a(v.side))} mm²: <b>${fmt(tot)} mm², ${fmt((100 * tot) / (b.width_mm * b.depth_mm), 2)}%</b>`;
  };
  box.querySelectorAll("input,select").forEach((el) => (el.oninput = sum));
  sum();
  const run = async (cage) => {
    const s = sec();
    s.beam_cages ??= {};
    if (cage) s.beam_cages[b.key || b.element] = cage;
    else delete s.beam_cages[b.key || b.element];
    status.textContent = "Saving…";
    markDirty();
    await save();
    if (state.errors?.length) return (status.textContent = state.errors.map((e) => e.msg).join(" "));
    status.textContent = `Checking ${b.element}…`;
    state.runDesign?.([b.element]);
  };
  box.querySelector("[data-beam-check]").onclick = () => {
    const v = read();
    if (!(v.top.count >= 2 && v.bottom.count >= 2)) return (status.textContent = "At least 2 bars on the top and on the bottom.");
    run(v);
  };
  const auto = box.querySelector("[data-beam-auto]");
  if (auto) auto.onclick = () => run(null);
}

function faceNeeds(b) {
  // Steel each check needs per face (mm²), the Plaxis-only design beside the final one.
  if (!b.faces?.length) return "";
  const cols = [["minimum", "Minimum"], ["bending", "Bending (Plaxis)"], ["crack", "QP crack (Plaxis)"], ["restraint", "Restraint cracking"], ["truss", "Truss tie"]];
  const hasTruss = b.faces.some((f) => f.needs_mm2 && "truss" in f.needs_mm2);
  const shown = cols.filter(([k]) => k !== "truss" || hasTruss);
  const cell = (f, k) => {
    const v = f.needs_mm2?.[k];
    if (!(k in (f.needs_mm2 || {}))) return "–";
    if (v === 0) return "below minimum";
    if (v == null) return "more than any bars";
    return fmt(v);
  };
  const name = { top: "Top", bottom: "Bottom", side: "Each side" };
  return `<h3 style="margin-top:18px">What sets each face</h3>
    <div class="scroll"><table><tr><th>Face</th>${shown.map(([, l]) => `<th>${l} mm²</th>`).join("")}${hasTruss ? "<th>From Plaxis alone</th>" : ""}<th>Final</th><th>Governed by</th></tr>
      ${b.faces.map((f) => `<tr><td>${name[f.face]}</td>${shown.map(([k]) => `<td>${cell(f, k)}</td>`).join("")}${hasTruss ? `<td>${esc(f.plaxis)} (${fmt(f.plaxis_mm2)})</td>` : ""}<td><b>${esc(f.final)}</b> (${fmt(f.final_mm2)})</td><td>${esc(f.governed_by)}</td></tr>`).join("")}
    </table></div>
    <p class="status">Each column is the steel that check alone needs on that face, the other faces as designed. The final bars are the largest of them.${hasTruss ? " From Plaxis alone is the cage without the truss check." : ""}</p>`;
}

function beamSection(el, b) {
  // Cross-section to scale: outline, link, bars.
  const w = b.width_mm, h = b.depth_mm, c = b.cover_mm, lk = b.cage.link_diameter_mm;
  const pad = Math.max(w, h) * 0.06;
  const bars = b.cage.bars.map(([u, v, phi]) => `<circle class="bar" cx="${u}" cy="${-v}" r="${phi / 2}"><title>Ø${phi}</title></circle>`).join("");
  el.innerHTML = `<div class="chart-title">Section ${fmt(w)} × ${fmt(h)} mm</div>
    <svg viewBox="${-w / 2 - pad} ${-h / 2 - pad} ${w + 2 * pad} ${h + 2 * pad}" role="img" aria-label="Beam cross-section">
      <rect class="outline" x="${-w / 2}" y="${-h / 2}" width="${w}" height="${h}"/>
      <rect class="link" x="${-w / 2 + c + lk / 2}" y="${-h / 2 + c + lk / 2}" width="${w - 2 * c - lk}" height="${h - 2 * c - lk}" stroke-width="${lk}" fill="none"/>
      ${bars}</svg>`;
}

// The supports inside a beam (king piles, piles) as shaded bands: the results inside them are FE
// peaks in the connection and are not designed; bending is taken at their faces. A line is broken
// only where a support lies between two results.
function supportBands(c, supports, lo, hi, cut = true) {
  return (supports || []).filter((q) => q.s + q.r > lo && q.s - q.r < hi).map((q) => {
    const a = c.x(Math.max(lo, q.s - q.r)), b = c.x(Math.min(hi, q.s + q.r));
    return `<rect class="support-band" x="${a}" y="${c.m.t}" width="${b - a}" height="${c.h - c.m.t - c.m.b}"><title>${esc(q.element)} at ${fmt(q.s, 2)} m, Ø${fmt(2000 * q.r)} mm${cut ? ": no design inside it, bending at its faces" : ""}</title></rect>`;
  }).join("");
}
const acrossSupport = (supports, a, b) => (supports || []).some((q) => q.s > Math.min(a, b) && q.s < Math.max(a, b));
const supportLegend = (supports, cut = true) => (supports?.length ? `<p class="status" style="margin:4px 0 0"><span class="support-key"></span> ${esc(supports[0].element)}${new Set(supports.map((q) => q.element)).size > 1 ? " and other supports" : ""}${cut ? ": nothing is designed inside them (FE peaks in the connection); bending is taken at their faces (Design settings)." : ": their results are designed like the rest of the beam (Design settings can leave them out)."}</p>` : "");

function beamMoments(el, prof, supports = [], cut = true, mode = "uls") {
  // Vertical bending envelope along the beam (ULS or QP), sagging +.
  const xs = prof.map((q) => q.s);
  const lo = Math.min(0, ...prof.map((q) => q.Mv_min)), hi = Math.max(0, ...prof.map((q) => q.Mv_max));
  const padm = (hi - lo) * 0.05 || 1;
  const c = frame(el, { xDomain: [Math.min(...xs), Math.max(...xs)], yDomain: [lo - padm, hi + padm],
    xLabel: "Position along the beam (m)", yLabel: "M vertical (kNm)", title: `Vertical bending, ${LIMIT_NAME[mode]} envelope (sagging +)` });
  // Break the line over the supports, where there are no results.
  const line = (k) => prof.map((q, i) => `${i && !(cut && acrossSupport(supports, q.s, prof[i - 1].s)) ? "L" : "M"}${c.x(q.s).toFixed(1)},${c.y(q[k]).toFixed(1)}`).join("");
  c.g.innerHTML = supportBands(c, supports, Math.min(...xs), Math.max(...xs), cut) + `<line class="grid" x1="${c.m.l}" x2="${c.w - c.m.r}" y1="${c.y(0)}" y2="${c.y(0)}"/>
    <path class="series" d="${line("Mv_max")}"/><path class="series" d="${line("Mv_min")}" stroke-dasharray="5 3"/>`;
  el.insertAdjacentHTML("beforeend", supportLegend(supports, cut));
  c.svg.onmousemove = (evt) => {
    const r = c.svg.getBoundingClientRect();
    const sx = ((evt.clientX - r.left) / r.width) * c.w;
    let best = 0;
    prof.forEach((q, i) => { if (Math.abs(c.x(q.s) - sx) < Math.abs(c.x(prof[best].s) - sx)) best = i; });
    const q = prof[best];
    showTip(c, evt, `${fmt(q.s, 2)} m<br>M ${fmt(q.Mv_min)} to ${fmt(q.Mv_max)} kNm`);
  };
  c.svg.onmouseleave = () => { c.tip.hidden = true; };
}

function alongChart(el, rows, title, yLabel, val, limit = null, supports = null, cut = true) {
  // A value along the beam (x = position), with an optional limit line.
  const xs = rows.map((q) => q.z);
  const hi = Math.max(limit ?? 0, ...rows.map(val)) * 1.08 || 1;
  const c = frame(el, { xDomain: [Math.min(...xs), Math.max(...xs)], yDomain: [0, hi], xLabel: "Position along the beam (m)", yLabel, title });
  const gap = (a, b) => (supports ? cut && acrossSupport(supports, a, b) : Math.abs(a - b) >= 0.6);
  const path = rows.map((q, i) => `${i && !gap(q.z, rows[i - 1].z) ? "L" : "M"}${c.x(q.z).toFixed(1)},${c.y(val(q)).toFixed(1)}`).join("");
  c.g.innerHTML = (supports ? supportBands(c, supports, Math.min(...xs), Math.max(...xs), cut) : "") + (limit != null ? `<line class="limit" x1="${c.m.l}" x2="${c.w - c.m.r}" y1="${c.y(limit)}" y2="${c.y(limit)}"/>` : "") + `<path class="series" d="${path}"/>`;
  if (supports) el.insertAdjacentHTML("beforeend", supportLegend(supports, cut));
  c.svg.onmousemove = (evt) => {
    const r = c.svg.getBoundingClientRect();
    const sx = ((evt.clientX - r.left) / r.width) * c.w;
    let best = 0;
    rows.forEach((q, i) => { if (Math.abs(c.x(q.z) - sx) < Math.abs(c.x(rows[best].z) - sx)) best = i; });
    showTip(c, evt, `${fmt(rows[best].z, 2)} m<br>${esc(yLabel)} ${fmt(val(rows[best]), 3)}`);
  };
  c.svg.onmouseleave = () => { c.tip.hidden = true; };
}

// The office king pile sheet: one column per corrosion zone, rows grouped as the sheet, checks green
// when they pass and red when they fail.
// What makes a failing pile head pass, in words (from the design's punching fix).
function punchFix(q) {
  const f = q.fix;
  if (!f) return "";
  const ways = [];
  if (f.rho_l_with_links != null) ways.push(`ρl of the ${q.direction === "pile pulls down" ? "bottom" : "top"} bars over the pile ≥ ${fmt(f.rho_l_with_links * 100, 2)}% (now ${fmt(q.rho_l * 100, 2)}%; about ${fmt(f.as_mm2_per_m_with_links)} mm²/m each way), with links`);
  if (f.thickness_mm_with_links) ways.push(`${fmt(f.thickness_mm_with_links)} mm thick at the pile with links`);
  if (f.thickness_mm_without_links) ways.push(`${fmt(f.thickness_mm_without_links)} mm without links`);
  return ways.join("; or ");
}

function officeSheet(sh) {
  if (!sh?.columns?.length) return "";
  const cell = (v, r) => {
    if (v == null) return "<td></td>";
    if (typeof v === "string") return `<td class="${r.check ? "sheet-note" : ""}">${esc(v)}</td>`;
    if (r.format === "pct") return `<td class="${r.check ? (v <= 100 ? "sheet-ok" : "sheet-bad") : ""}">${v.toFixed(2)}%</td>`;
    if (r.format === "sci") return `<td>${v.toExponential(2).toUpperCase()}</td>`;
    return `<td>${fmt(v, Math.abs(v) >= 1000 ? 0 : 2)}</td>`;
  };
  const head = `<tr><th>Item</th><th>Unit</th>${sh.columns.map((c) => `<th>${esc(c)}</th>`).join("")}</tr>`;
  const body = sh.groups.map((g) => `<tr class="sheet-group"><th colspan="${sh.columns.length + 2}">${esc(g.title)}</th></tr>`
    + g.rows.map((r) => `<tr><td>${esc(r.item)}</td><td>${esc(r.unit)}</td>${r.values.map((v) => cell(v, r)).join("")}</tr>`).join("")).join("");
  return `<h3 style="margin-top:18px">Tube check by zone (office sheet)</h3>
    <p class="status">Each zone takes its largest N, V and M together, as the office sheet does. Green passes, red fails.</p>
    <div class="scroll"><table class="office-sheet">${head}${body}</table></div>`;
}

function combiCard(w) {
  const card = document.createElement("div");
  card.className = "panel";
  card.style.marginTop = "16px";
  const t = w.tube;
  const s = t.section || {};
  const r = t.resistances || {};
  const b = t.buckling;
  const g = t.governing || {};
  card.innerHTML = `<div class="element-head"><h3>${esc(w.element)}<span class="type">${w.count} king pile${w.count === 1 ? "" : "s"}, Ø${fmt(s.diameter_mm)} × ${fmt(s.thickness_mm)} mm ${esc(s.grade || "")}</span></h3>
      <span class="sev ${w.passed ? "ok" : "error"}">${w.passed ? "passes" : "fails"}</span></div>
    <div class="counts" style="margin-top:0">
      <div class="count"><b>${fmt(w.utilisation, 2)}</b>max utilisation</div>
      <div class="count"><b>${fmt((1 - w.steel_share) * 100)}% / ${fmt(w.steel_share * 100)}%</b>infill / tube share where filled (E·I)</div>
      <div class="count"><b>${fmt(w.infill.utilisation, 2)}</b>infill N–M</div>
      <div class="count"><b>${fmt(t.utilisation, 2)}</b>steel tube</div>
    </div>
    ${w.notes.map((n) => `<p class="status">${esc(n)}</p>`).join("")}
    ${v3dSlot(w.element)}
    <h3 style="margin-top:18px">Steel tube</h3>
    <div class="cage"><div class="chart" data-kind="tube"></div><div class="scroll"><table>
      <tr><th colspan="2">Corroded section (${fmt(s.corrosion_mm, 1)} mm lost outside)</th></tr>
      <tr><td>Diameter × wall</td><td>${fmt(s.corroded_diameter_mm)} × ${fmt(s.corroded_thickness_mm, 1)} mm</td></tr>
      <tr><td>f<sub>y</sub></td><td>${fmt(s.fy_MPa)} MPa</td></tr>
      <tr><td>d/t, class below the infill</td><td>${fmt(s.d_over_t)}, class ${s.class_unfilled}</td></tr>
      <tr><td>N<sub>pl,Rd</sub> / M<sub>pl,Rd</sub></td><td>${fmt(r.N_pl_kN)} kN / ${fmt(r.M_pl_kNm)} kNm</td></tr>
      <tr><td>M<sub>el,Rd</sub> / V<sub>pl,Rd</sub></td><td>${fmt(r.M_el_kNm)} kNm / ${fmt(r.V_pl_kN)} kN</td></tr>
      ${b ? `<tr><td>Shell buckling σ<sub>x,Rd</sub></td><td>${fmt(b.sigma_Rd_MPa)} MPa (χ ${fmt(b.chi, 3)}, λ̄ ${fmt(b.slenderness, 3)})</td></tr>` : ""}
    </table></div></div>
    ${g.envelope ? `<p>Governing: ${esc(g.zone)} zone, with the zone's largest N, V and M taken together as the office sheet does: N = ${fmt(g.N_kN)} kN (Plaxis sign), M = ${fmt(g.M_kNm)} kNm, V = ${fmt(g.V_kN)} kN. Check: ${esc(g.check)}.</p>`
      : g.combination ? `<p>Governing: ${esc(g.combination)}, z ${fmt(g.z, 2)} m (${esc(g.zone)}), N = ${fmt(g.N_kN)} kN (Plaxis sign), M = ${fmt(g.M_kNm)} kNm, V = ${fmt(g.V_kN)} kN. Check: ${esc(g.check)}.</p>` : ""}
    ${(t.notes || []).map((n) => `<p class="status">${esc(n)}</p>`).join("")}
    ${officeSheet(t.sheet)}
    ${steelSetsBlock(t.governing_sets, "kN, kNm")}
    <h3 style="margin-top:18px">Concrete infill</h3>`;
  if (t.profile?.length) profileChart(card.querySelector('[data-kind="tube"]'), t, "Tube utilisation along the wall", w.infill_bottom_level);
  card.querySelector('[data-kind="tube"]').parentElement.classList.add("wide");
  const infill = pileCard({ ...w.infill, element: `${w.element} infill` });
  infill.style.marginTop = "0";
  infill.style.border = "0";
  infill.style.padding = "0";
  card.append(infill);
  return card;
}

// A pile (or combi wall infill) cage set by hand, row by row from the outside in, each row with its own
// bar count and size. Check designs just that element with it; "Let Triton choose" goes back to the
// automatic cage.
const cageKey = (p) => p.element.replace(/ infill$/, "");
function cageRows(own, a) {
  if (own?.row_bars?.length) return own.row_bars.map((r) => ({ count: r.count, diameter: r.diameter }));
  if (own) {
    const inner = own.inner_diameter || own.diameter;
    const full = Math.floor(own.rows), half = own.rows % 1 > 0;
    return [{ count: own.count, diameter: own.diameter }, ...Array.from({ length: full - 1 }, () => ({ count: own.count, diameter: inner })), ...(half ? [{ count: own.count / 2, diameter: inner }] : [])];
  }
  if (a?.rings?.length) return a.rings.map((r) => ({ count: r.count, diameter: r.diameter }));
  return [{ count: 26, diameter: 32 }];
}

function cageSetHtml(p) {
  const key = cageKey(p);
  const own = sec().user_cages?.[key];
  const bars = (state.project.design?.reinforcement?.bar_diameters || [16, 20, 25, 32]).filter((d) => d >= 16);
  const opt = (cur) => bars.map((d) => `<option value="${d}" ${Number(cur) === d ? "selected" : ""}>Ø${d}</option>`).join("");
  const rowHtml = (r, i) => `<div class="row cage-row" data-cage-row style="gap:10px;align-items:end;margin-top:6px">
      <span class="status" style="min-width:110px">${i ? `Row ${i + 1}` : "Row 1 (outer)"}</span>
      <label>Bars <input type="number" min="1" step="1" data-cage-count value="${r.count}" style="width:80px"></label>
      <label>Bar <select data-cage-dia>${opt(r.diameter)}</select></label>
      ${i ? `<button class="quiet" data-cage-remove title="Remove this row">Remove</button>` : ""}</div>`;
  const rows = cageRows(own, p.arrangement);
  return `<details class="panel cage-set" data-free ${own ? "open" : ""}><summary>${own ? "Cage set by you (checked, not chosen by Triton)" : "Set the cage yourself and check it"}</summary>
    <p class="status" style="margin:8px 0 0">Rows from the outside in. A row with half the outer row's bars sits behind every second bar (a half row).</p>
    <div data-cage-rows data-bars="${bars.join(",")}">${rows.map(rowHtml).join("")}</div>
    <div class="row" style="margin-top:8px;flex-wrap:wrap;gap:10px;align-items:center">
      <button class="quiet" data-cage-add>Add a row</button>
      <span data-cage-sum></span>
    </div>
    <div data-cage-over class="cage-over" hidden>
      <p><b>Over the steel limit.</b> <span data-cage-over-text></span> EN 1992-1-1 9.5.2(3) allows more than 4% only with couplers (no laps), and at most 8%.</p>
      <label class="check"><input type="checkbox" data-cage-proceed ${own?.over_limit_with_couplers ? "checked" : ""}> Proceed: splice this cage with couplers at its joint. The zones below are designed as usual.</label>
    </div>
    <div class="row" style="margin-top:10px;flex-wrap:wrap;gap:10px">
      <button data-cage-check>Check</button>
      ${own ? `<button class="quiet" data-cage-auto>Let Triton choose again</button>` : ""}
      <span class="status" data-cage-status>${own ? "" : "Check designs only this element with your cage: utilisation, cracks, links and steel are worked out again, and the zones below it are curtailed."}</span>
    </div></details>`;
}

function wireCageSet(card, p) {
  const box = card.querySelector(".cage-set");
  if (!box) return;
  const key = cageKey(p);
  const status = box.querySelector("[data-cage-status]");
  const list = box.querySelector("[data-cage-rows]");
  const D = p.section?.diameter_mm;
  const limit = p.spacing_limits?.max_ratio_pct ?? 4;
  const read = () => [...list.querySelectorAll("[data-cage-row]")].map((row) => ({
    count: Math.round(Number(row.querySelector("[data-cage-count]").value)),
    diameter: Number(row.querySelector("[data-cage-dia]").value),
  }));
  // Total bars, area and steel ratio as you type, and the coupler choice when it is over the limit.
  const sum = () => {
    const rows = read();
    const area = rows.reduce((t, r) => t + (r.count * Math.PI * r.diameter ** 2) / 4, 0);
    const pct = D ? (100 * area) / ((Math.PI * D ** 2) / 4) : null;
    const over = pct != null && pct > limit + 1e-9;
    box.querySelector("[data-cage-sum]").innerHTML = `<b>${rows.map((r) => `${r.count}Ø${r.diameter}`).join(" + ")}</b>: ${rows.reduce((t, r) => t + r.count, 0)} bars, ${fmt(area)} mm², <b class="${over ? "bad" : ""}">${pct == null ? "–" : fmt(pct, 2)}% steel</b> (limit ${fmt(limit)}%)`;
    const warn = box.querySelector("[data-cage-over]");
    warn.hidden = !over;
    if (over) box.querySelector("[data-cage-over-text]").textContent = pct > 8 ? `${fmt(pct, 2)}% is over 8%, the limit even with couplers: use fewer or smaller bars.` : `${fmt(pct, 2)}% steel is over the ${fmt(limit)}% limit.`;
    return { rows, pct, over };
  };
  const wireRow = (row) => {
    row.querySelectorAll("input,select").forEach((el) => (el.oninput = sum));
    const rm = row.querySelector("[data-cage-remove]");
    if (rm) rm.onclick = () => { row.remove(); relabel(); sum(); };
  };
  const relabel = () => list.querySelectorAll("[data-cage-row]").forEach((row, i) => (row.querySelector(".status").textContent = i ? `Row ${i + 1}` : "Row 1 (outer)"));
  list.querySelectorAll("[data-cage-row]").forEach(wireRow);
  box.querySelector("[data-cage-add]").onclick = () => {
    const rows = read();
    if (rows.length >= 4) return (status.textContent = "At most 4 rows.");
    const last = rows[rows.length - 1];
    const row = list.querySelector("[data-cage-row]:last-child").cloneNode(true);
    row.querySelector("[data-cage-count]").value = last.count;
    row.querySelector("[data-cage-dia]").value = last.diameter;
    if (!row.querySelector("[data-cage-remove]")) row.insertAdjacentHTML("beforeend", `<button class="quiet" data-cage-remove title="Remove this row">Remove</button>`);
    list.append(row);
    wireRow(row);
    relabel();
    sum();
  };
  sum();
  const run = async (cage) => {
    const s = sec();
    if (cage) {
      const bad = cage.row_bars.find((r) => !(r.count >= 1));
      if (bad) return (status.textContent = "Each row needs at least one bar.");
      if (!(cage.row_bars[0].count >= 6)) return (status.textContent = "At least 6 bars in the outer row (EN 1992-1-1 9.8.5).");
    }
    s.user_cages ??= {};
    if (cage) s.user_cages[key] = cage;
    else delete s.user_cages[key];
    status.textContent = "Saving…";
    markDirty();
    await save();
    if (state.errors?.length) return (status.textContent = state.errors.map((e) => e.msg).join(" "));
    status.textContent = `Checking ${key}…`;
    state.runDesign?.([key]);
  };
  box.querySelector("[data-cage-check]").onclick = () => {
    const { rows, over } = sum();
    run({ row_bars: rows, over_limit_with_couplers: over && box.querySelector("[data-cage-proceed]").checked });
  };
  const auto = box.querySelector("[data-cage-auto]");
  if (auto) auto.onclick = () => run(null);
}

// Why a pile fails, plainly, first on its card; with couplers when the steel limit is what stops it.
function failureHtml(p) {
  if (p.passed || !p.failure?.length) return "";
  const c = p.with_couplers;
  const own = p.user_set;
  const offer = c?.passes && !own && c.allow_pct > (p.spacing_limits?.max_ratio_pct ?? 4);
  return `<div class="fail-why"><b>Fails because:</b><ul>${p.failure.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>
    ${c ? `<p>${esc(c.note)}</p>` : ""}
    ${c?.passes && own ? `<p>Tick <i>Proceed</i> under the cage and press Check to accept it with couplers.</p>` : ""}
    ${offer ? `<p><button data-use-couplers="${c.allow_pct}" data-couplers="${c.couplers ? 1 : ""}">${c.couplers ? "Use couplers and allow" : "Allow"} ${fmt(c.allow_pct, 1)}% steel</button> <span class="status">Changes Design settings for every pile in the project, then designs again.</span></p>` : ""}</div>`;
}

function wireFailure(card) {
  const b = card.querySelector("[data-use-couplers]");
  if (!b) return;
  b.onclick = async () => {
    const piles = (state.project.design.piles ??= {});
    if (b.dataset.couplers) piles.splice = "coupler";
    piles.max_steel_ratio = Number(b.dataset.useCouplers);
    b.disabled = true;
    b.textContent = "Saving…";
    markDirty();
    await save();
    if (state.errors?.length) return (b.textContent = state.errors.map((e) => e.msg).join(" "));
    state.runDesign?.(null);
  };
}

function pileCard(p) {
  const card = document.createElement("div");
  card.className = "panel";
  card.style.marginTop = "16px";
  const a = p.arrangement;
  const g = p.governing;
  const lim = p.spacing_limits || {};
  const rowsText = (r) => (r === 1 ? "1 row" : `${r} rows`);
  card.innerHTML = `<div class="element-head"><h3>${esc(p.element)}<span class="type">${a ? `${esc(a.label)}, ${rowsText(a.rows)}, ${fmt(a.area_mm2)} mm²` : "no arrangement"}</span></h3>
      <span class="sev ${p.passed ? "ok" : "error"}">${p.passed ? "passes" : "fails"}</span></div>
    ${failureHtml(p)}
    <div class="counts" style="margin-top:0">
      ${utilCounts(p)}
      <div class="count"><b class="${p.reinforcement_ratio_pct > (lim.max_ratio_pct ?? 4) + 1e-9 ? "bad" : ""}">${fmt(p.reinforcement_ratio_pct, 2)}%</b>steel at the head (limit ${fmt(lim.max_ratio_pct ?? 4)}%${p.reinforcement_ratio_pct > 4 + 1e-9 ? ", over 4% only with couplers" : ""})</div>
      <div class="count"><b>${fmt(p.steel?.kg_per_m3 ?? p.curtailment?.steel_ratio_kg_m3 ?? p.steel_ratio_kg_m3)}</b>${p.steel ? `kg/m³ over the pile (${fmt(p.steel.longitudinal_kg)} kg bars with laps + ${fmt(p.steel.links_kg)} kg links)` : "kg/m³ longitudinal"}</div>
      <div class="count"><b>${a ? fmt(a.clear_spacing_mm) : "–"} mm</b>clear spacing, outer row (allowed ${fmt(lim.min_clear_mm)} to ${fmt(lim.max_clear_mm)} mm)</div>
      ${p.steel?.element_total_t != null ? `<div class="count"><b>${fmt(p.steel.element_total_t, 1)} t</b>steel for ${p.count} pile${p.count === 1 ? "" : "s"} of this type</div>` : ""}
    </div>
    ${g.combination ? `<p>Governing: ${esc(g.combination)}, node ${g.node}, y ${fmt(g.y, 2)} m, z ${fmt(g.z, 2)} m.
      N<sub>Ed</sub> = ${fmt(g.N_kN)} kN (compression +), M<sub>Ed</sub> = ${fmt(g.M_kNm)} kNm, M<sub>Rd</sub> at this N = ${fmt(g.M_Rd_kNm)} kNm.</p>` : ""}
    ${p.notes.map((n) => `<p class="status">${esc(n)}</p>`).join("")}
    ${cageSetHtml(p)}
    ${p.element.endsWith(" infill") ? "" : v3dSlot(p.element)}
    ${a?.rings ? `<div class="cage"><div class="chart" data-kind="section"></div><div class="scroll"><table>
      <tr><th>Row</th><th>Bars</th><th>Bar circle radius</th><th>Clear spacing</th></tr>
      ${a.rings.map((r, i) => `<tr><td>${i ? (r.count < a.rings[0].count ? `${i + 1} (half row)` : i + 1) : "1 (outer)"}</td><td>${r.count}Ø${r.diameter}</td><td>${fmt(r.radius)} mm</td><td>${fmt(r.clear_spacing_mm)} mm</td></tr>`).join("")}
      <tr><td>Total</td><td>${a.bar_count} bars</td><td colspan="2">${fmt(a.area_mm2)} mm², ${fmt(p.reinforcement_ratio_pct, 2)}%, ${fmt(a.weight_kg_per_m, 1)} kg/m</td></tr>
      </table>
      <p class="status">Clear gap between rows: ${lim.row_gap_mm == null ? "EN 1992-1-1 8.2 minimum" : `${fmt(lim.row_gap_mm)} mm`}.</p></div></div>` : ""}
    ${p.curtailment?.runs?.length ? curtailmentBlock(p.curtailment) : ""}
    ${p.shear ? shearBlock(p.shear, p.head_name || "the slab") : ""}
    ${jointsBlock(p.construction_joints)}
    ${casingBlock(p.casing)}
    ${connectionBlock(p.connection)}
    ${levelSketch(p)}
    ${pileCrackBlock(p.cracks)}
    ${crackPicturesHtml(pileCrackItems(p))}
    <div class="row" data-kind="limit-switch">${limitSwitch(p.moments_qp?.length || p.cracks?.profile?.length, "Utilisation and moment diagrams:")}</div>
    <div class="charts"><div class="chart" data-kind="nm"></div><div class="chart" data-kind="profile"></div></div>
    ${p.moments?.length ? `<div class="charts"><div class="chart" data-kind="moments"></div><div data-kind="peaks"></div></div>` : ""}
    ${setsBlock(p.governing_sets)}
    <details style="margin-top:12px"><summary>Other cages that pass</summary><div class="scroll"><table>
      <tr><th>Bars</th><th>Rows</th><th>Area mm²</th><th>Utilisation</th><th>kg/m³</th><th>Clear spacing mm</th></tr>
      ${p.alternatives.map((x) => `<tr${x.chosen ? ' style="font-weight:600"' : ""}><td>${esc(x.label)}${x.chosen ? " (chosen)" : ""}</td><td>${x.rows}</td><td>${fmt(x.area_mm2)}</td><td>${fmt(x.utilisation, 3)}</td><td>${fmt(x.steel_ratio_kg_m3)}</td><td>${fmt(x.clear_spacing_mm)}</td></tr>`).join("")}
    </table></div></details>`;
  wireCageSet(card, p);
  wireFailure(card);
  mountCrackPictures(card, pileCrackItems(p));
  if (a?.rings) sectionDrawing(card.querySelector('[data-kind="section"]'), p);
  if (p.curtailment?.runs?.length) elevationDrawing(card.querySelector('[data-kind="elevation"]'), p.curtailment);
  const drawPile = (mode) => {
    const pel = card.querySelector('[data-kind="profile"]');
    if (p.curve.length && pel) {
      if (mode === "qp") {
        const lim = p.cracks?.limit_mm;
        const rows = (p.cracks?.profile || []).map((q) => ({ z: q.z, util: q.wk / lim }));
        if (rows.length) profileChart(pel, { profile: rows }, "Crack width / limit along the pile, SLS (QP)");
        else pel.innerHTML = `<p class="status">${esc(p.cracks?.casing || p.cracks?.note || "No QP crack check.")}</p>`;
      } else profileChart(pel, p, "Utilisation along the pile, ULS");
    }
    if (p.moments?.length) momentChart(card.querySelector('[data-kind="moments"]'), p, mode);
  };
  if (p.curve.length) nmChart(card.querySelector('[data-kind="nm"]'), p);
  drawPile("uls");
  if (p.moments?.length) peaksBlock(card.querySelector('[data-kind="peaks"]'), p.peaks || []);
  const sw = card.querySelector('[data-kind="limit-switch"]');
  if (sw) wireLimitSwitch(sw, drawPile);
  return card;
}

// ULS (N–M) and SLS (QP crack width over its limit) side by side, the governing one marked.
function utilCounts(p) {
  const uls = p.utilisation;
  const sls = p.cracks?.wk_mm != null && p.cracks?.limit_mm ? p.cracks.wk_mm / p.cracks.limit_mm : null;
  const gov = sls != null && sls > (uls ?? 0) ? "sls" : "uls";
  const tag = (k) => (gov === k && sls != null ? ' <span class="chip small-chip">governs</span>' : "");
  return `<div class="count"><b class="${uls > 1 ? "bad" : ""}">${fmt(uls, 2)}</b>ULS: N–M utilisation${p.curtailment?.runs?.length > 1 ? ", each zone with its own cage" : ""}${tag("uls")}</div>
    ${sls != null ? `<div class="count"><b class="${sls > 1 ? "bad" : ""}">${fmt(sls, 2)}</b>SLS (QP): crack width ${fmt(p.cracks.wk_mm, 3)} / ${fmt(p.cracks.limit_mm, 2)} mm${tag("sls")}</div>` : ""}`;
}

// The QP sets of each station as crack pictures (see cracks.js).
function pileCrackItems(p) {
  const D = p.section?.diameter_mm;
  const many = (p.governing_sets || []).length > 1;
  return (p.governing_sets || []).flatMap((st) =>
    (st.qp || []).filter((r) => r.crack).map((r) => ({
      label: `${many ? `${fmt(st.top, 2)} to ${fmt(st.bottom, 2)} m, ` : ""}${r.case} (${r.combination}, z ${fmt(r.z, 2)} m)`,
      set: r,
      geom: { shape: "circle", D, rings: st.rings },
      forces: `${r.combination} at z ${fmt(r.z, 2)} m: N = ${fmt(r.N_kN)} kN (compression +), M2 = ${fmt(r.M2_kNm)} kNm, M3 = ${fmt(r.M3_kNm)} kNm, resultant ${fmt(Math.hypot(r.M2_kNm, r.M3_kNm))} kNm, with ${st.cage}`,
    }))
  );
}

function beamCrackItems(b) {
  return (b.governing_sets || []).flatMap((st) =>
    (st.qp || []).filter((r) => r.crack).map((r) => ({
      label: `${r.case} (${r.combination}, ${fmt(r.z, 1)} m along)`,
      set: r,
      geom: { shape: "rect", b: b.width_mm, h: b.depth_mm, bars: b.cage?.bars || [] },
      forces: `${r.combination} at ${fmt(r.z, 2)} m along the beam: N = ${fmt(r.N_kN)} kN (compression +), M vertical = ${fmt(r.M3_kNm)} kNm (sagging +), M horizontal = ${fmt(r.M2_kNm)} kNm (not in the crack check)`,
    }))
  );
}

function slabCrackItems(d) {
  const rows = d.strip_design?.rows || [];
  return rows.flatMap((r) =>
    (r.sets?.qp || []).filter((q) => q.crack).map((q) => ({
      label: r.strip === "all" ? `${r.moment} ${r.face} bars, ${r.label}, ${q.case ? `${q.case}: ` : ""}${q.combination}` : `${r.moment} ${r.face} bars, ${r.strip} strip, ${fmt(r.station[0], 2)} to ${fmt(r.station[1], 2)} m, ${q.case ? `${q.case}: ` : ""}${q.combination}`,
      set: q,
      geom: { shape: "strip" },
      forces: `${q.combination}: M = ${fmt(q.M_kNm_per_m)} kNm/m, N = ${fmt(q.N_kN_per_m)} kN/m (compression +), strip averaged, with ${r.bars}`,
    }))
  );
}

// The levels at the pile head, not to scale: slab soffit (pile top), design top, casing, worst crack.
function levelSketch(p) {
  const s = p.section || {};
  if (s.soffit_m == null && !s.casing_m) return "";
  const z = (v) => Math.round(v * 100) / 100;
  const marks = [];
  const add = (v, text) => v != null && marks.push({ v: z(v), text });
  if (s.results_to_m != null && s.results_to_m > s.soffit_m + 1e-6)
    add(s.results_to_m, `Results used up to (soffit + ${fmt((s.results_to_m - s.soffit_m) * 100)} cm)`);
  if (s.head_level_m != null && s.soffit_m != null && s.head_level_m > s.soffit_m + 1e-6)
    add(s.head_level_m, "Design top (highest result used)");
  add(s.soffit_m, "Slab soffit = pile top");
  if (s.casing_m) {
    add(s.casing_m[1], "Casing top");
    add(s.casing_m[0], "Casing bottom");
    if (s.no_crack_m && s.no_crack_m[1] > s.casing_m[1] + 1e-6) add(s.no_crack_m[1], "no crack check to here");
  }
  const g = p.cracks?.governing;
  if (g) add(g.z, `Worst QP crack (${fmt(p.cracks.wk_mm, 2)} mm)`);
  const levels = [...new Set(marks.map((m) => m.v))].sort((a, b) => b - a);
  const rows = levels.map((v) => ({ v, text: marks.filter((m) => m.v === v).map((m) => m.text).join(", ") }));
  const top = 30, gap = 38, h = top + gap * (rows.length - 1) + 40;
  const y = (v) => top + gap * levels.indexOf(z(v));
  const soffitY = s.soffit_m != null ? y(s.soffit_m) : top;
  const pileTop = [s.results_to_m, s.head_level_m].find((v) => v != null && levels.includes(z(v)));
  const pileY = pileTop != null ? y(pileTop) : soffitY;
  const band = s.no_crack_m || s.casing_m;
  const cas = s.casing_m ? `<rect x="58" y="${y(s.casing_m[1])}" width="4" height="${y(s.casing_m[0]) - y(s.casing_m[1])}" fill="var(--accent)"/><rect x="118" y="${y(s.casing_m[1])}" width="4" height="${y(s.casing_m[0]) - y(s.casing_m[1])}" fill="var(--accent)"/>` : "";
  const nc = band ? `<rect x="62" y="${y(band[1])}" width="56" height="${y(band[0]) - y(band[1])}" fill="var(--accent-bg)"/>` : "";
  const lines = rows.map((r) => `<line x1="40" x2="150" y1="${y(r.v)}" y2="${y(r.v)}" stroke="var(--muted)" stroke-dasharray="3 3"/>
    <text x="158" y="${y(r.v) + 4}" font-size="12" fill="var(--text)">${fmt(r.v, 2)} m  ${esc(r.text)}</text>`).join("");
  return `<h3>Levels at the pile head</h3>
    <svg class="level-sketch" viewBox="0 0 680 ${h}" width="100%" style="max-width:680px" role="img" aria-label="Pile head levels">
      <rect x="20" y="4" width="140" height="${soffitY - 4}" fill="var(--miss-bg)" stroke="var(--line)"/>
      <text x="26" y="18" font-size="11" fill="var(--muted)">slab</text>
      <rect x="62" y="${pileY}" width="56" height="${h - pileY}" fill="var(--panel)" stroke="var(--muted)"/>
      ${nc}${cas}${lines}
    </svg>
    <p class="status">Not to scale. Shaded: no crack width check${s.casing_m ? " (inside the casing)" : ""}. The pile is designed up to the design top; results above it are inside the slab and ignored.</p>`;
}

function pileCrackBlock(c) {
  // QP crack width at the extreme bar, EN 1992-1-1 7.3.4, with the cage at each level.
  if (!c) return "";
  const g = c.governing;
  const head = `<h3>Crack width (QP) <span class="sev ${c.passed ? "ok" : "error"}">${c.wk_mm == null ? "not checked" : c.passed ? "passes" : "fails"}</span></h3>`;
  const casing = c.casing ? `<p class="status">${esc(c.casing)}</p>` : "";
  if (c.wk_mm == null) return head + casing + `<p class="status">${esc(c.note || "")}</p>`;
  return `${head}
    <p>w<sub>k</sub> = ${fmt(c.wk_mm, 3)} mm against ${fmt(c.limit_mm, 2)} mm, at z ${fmt(g.z, 2)} m (${esc(g.combination)}),
      N = ${fmt(g.N_kN)} kN, M = ${fmt(g.M_kNm)} kNm with ${esc(g.cage)}:
      σ<sub>s</sub> = ${fmt(g.sigma_s_MPa)} MPa, x = ${fmt(g.x_mm)} mm, s<sub>r,max</sub> = ${fmt(g.sr_max_mm)} mm, ρ<sub>p,eff</sub> = ${fmt(g.rho_eff, 4)}.</p>
    ${casing}
    <p class="status">EN 1992-1-1 7.3.4 at the extreme bar of the cracked section, E<sub>c,eff</sub> = E<sub>cm</sub>/(1 + φ). A<sub>c,eff</sub> is the circular segment of depth h<sub>c,ef</sub> at the tension face (a ring round the pile when it is all in tension). The cage choice and curtailment keep w<sub>k</sub> within the limit.</p>`;
}

function momentChart(el, p, mode = "uls") {
  // Largest resultant moment at each level (ULS or QP), with isolated peaks marked.
  const qp = mode === "qp";
  const prof = qp ? p.moments_qp || [] : p.moments;
  const peaks = (p.peaks || []).filter((q) => q.combination && /QP/.test(q.combination) === qp);
  if (!prof.length) { el.innerHTML = `<p class="status">${qp && !p.moments_qp ? "Design this pile again to see its SLS (QP) moments." : `No ${LIMIT_NAME[mode]} results.`}</p>`; return; }
  const zs = prof.map((q) => q.z).concat(peaks.map((q) => q.z));
  const maxM = Math.max(1, ...prof.map((q) => q.M_kNm), ...peaks.map((q) => q.M_kNm)) * 1.05;
  const c = frame(el, {
    xDomain: [0, maxM], yDomain: [Math.min(...zs), Math.max(...zs)],
    xLabel: "M (kNm)", yLabel: "Level z (m)", title: `Moment along the pile (${LIMIT_NAME[mode]} envelope)`,
  });
  const path = prof.map((q, i) => `${i ? "L" : "M"}${c.x(q.M_kNm).toFixed(1)},${c.y(q.z).toFixed(1)}`).join("");
  c.g.innerHTML = `<path class="series" d="${path}"/>` + peaks
    .map((q, i) => `<circle class="peak ${q.treatment === "left out" ? "out" : ""}" data-i="${i}" cx="${c.x(q.M_kNm)}" cy="${c.y(q.z)}" r="5"/>`)
    .join("");
  c.svg.onmousemove = (evt) => {
    const r = c.svg.getBoundingClientRect();
    const sx = ((evt.clientX - r.left) / r.width) * c.w, sy = ((evt.clientY - r.top) / r.height) * c.h;
    const hit = peaks.findIndex((q) => Math.hypot(c.x(q.M_kNm) - sx, c.y(q.z) - sy) < 10);
    if (hit >= 0) {
      const q = peaks[hit];
      showTip(c, evt, `Isolated peak, ${esc(q.combination)}, node ${q.node}<br>z ${fmt(q.z, 2)} m, M ${fmt(q.M_kNm)} kNm<br>neighbours up to ${fmt(q.neighbours_M_kNm)} kNm, ${esc(q.treatment)}`);
      return;
    }
    let best = 0;
    prof.forEach((q, i) => { if (Math.abs(c.y(q.z) - sy) < Math.abs(c.y(prof[best].z) - sy)) best = i; });
    showTip(c, evt, `z ${fmt(prof[best].z, 2)} m<br>M ${fmt(prof[best].M_kNm)} kNm`);
  };
  c.svg.onmouseleave = () => { c.tip.hidden = true; };
}

function peaksBlock(el, peaks) {
  // Isolated peaks found in this element, each can be left out of the design.
  if (!peaks.length) {
    el.innerHTML = `<p class="status" style="margin-top:28px">No isolated peaks above ${fmt(sec().peak_ratio, 1)}× their neighbours.</p>`;
    return;
  }
  const out = new Set(sec().excluded_peaks || []);
  el.innerHTML = `<h3 style="margin-top:0">Isolated peaks</h3>
    <p class="status">Tick a peak to leave it out, then save and design again. Section setting: ${sec().peaks === "average" ? "peaks averaged" : "raw values"}.</p>
    <div class="scroll"><table><tr><th>Leave out</th><th>Combination</th><th>Node</th><th>z (m)</th><th>M kNm</th><th>Neighbours</th></tr>
    ${peaks.map((q) => `<tr><td><input type="checkbox" data-key="${esc(q.key)}" ${out.has(q.key) ? "checked" : ""}></td><td>${esc(q.combination)}</td><td>${q.node}</td><td>${fmt(q.z, 2)}</td><td>${fmt(q.M_kNm)}</td><td>${fmt(q.neighbours_M_kNm)}</td></tr>`).join("")}
    </table></div>`;
  el.querySelectorAll("input[data-key]").forEach((box) => (box.onchange = () => {
    const s = sec();
    const keys = new Set(s.excluded_peaks || []);
    if (box.checked) keys.add(box.dataset.key); else keys.delete(box.dataset.key);
    s.excluded_peaks = [...keys];
    markDirty();
  }));
}

function steelSetsBlock(sets, unit, open = false) {
  // The ten ULS sets of a steel element: max and min of N, M2, M3, Q1 and Q2 over all combinations.
  if (!sets?.rows?.length) return "";
  const keys = ["N", "M2", "M3", "Q1", "Q2"];
  const head = keys.map((k) => (sets.columns[k].replace("_", "") === k ? k : `${k} (${esc(sets.columns[k])})`));
  const rows = sets.rows
    .map((r) => `<tr><td>${esc(r.case)}</td><td>${esc(r.combination)}</td><td>${r.node ?? "–"}</td><td>${fmt(r.z, 2)}</td>${keys.map((k) => `<td>${fmt(r[k])}</td>`).join("")}</tr>`)
    .join("");
  return `<details style="margin-top:12px"${open ? " open" : ""}><summary>Governing sets (10 ULS rows, ${unit}, Plaxis sign)</summary>
    <p class="status">Maximum and minimum of each action over all ULS combinations, with the other actions at the same point.</p>
    <div class="scroll"><table class="sets"><tr><th>Case</th><th>Combination</th><th>Node</th><th>z (m)</th>${head.map((h) => `<th>${h}</th>`).join("")}</tr>${rows}</table></div></details>`;
}

// The station's governing set: the highest utilisation over ULS (N–M) and SLS (crack width / limit).
const govRow = (st) => [...(st.qp || []), ...(st.uls || [])].reduce((best, r) => (r.utilisation != null && (best == null || r.utilisation > best.utilisation) ? r : best), null);

function setsBlock(stations, note = null) {
  // The seven governing ULS and QP sets per station, as entered in AdSec.
  if (!stations?.length) return "";
  const rows = stations
    .map((st) =>
      [["SLS (QP)", st.qp], ["ULS", st.uls]]
        .map(([ls, list], k) =>
          list
            .map((r, i) => `<tr class="${i === 0 && k === 0 ? "group" : ""} ${r === govRow(st) ? "gov-row" : ""}">
              <td>${i === 0 && k === 0 ? `${fmt(st.top, 2)} to ${fmt(st.bottom, 2)}<br><span class="status">${esc(st.cage)}</span>` : ""}</td>
              <td>${i === 0 ? ls : ""}</td><td>${esc(r.case)}</td><td>${esc(r.combination)}</td><td>${r.node ?? "–"}</td>
              <td>${fmt(r.z, 2)}</td><td>${fmt(r.N_kN)}</td><td>${fmt(r.M2_kNm)}</td><td>${fmt(r.M3_kNm)}</td><td>${r.utilisation == null ? "–" : fmt(r.utilisation, 3)}${r === govRow(st) ? ' <span class="chip small-chip">governs</span>' : ""}</td></tr>`)
            .join("")
        )
        .join("")
    )
    .join("");
  return `<details style="margin-top:12px"><summary>Governing sets per station for AdSec (${stations.length} station${stations.length === 1 ? "" : "s"}, 7 QP + 7 ULS each)</summary>
    <p class="status">${esc(note || "N in the concrete sign convention (Plaxis N × −1, compression +). M2 and M3 as in Plaxis.")} For QP the 7th set is the largest resultant moment. Utilisation: ULS is N–M, SLS (QP) is the crack width over its limit; the highest of each station governs.</p>
    <div class="scroll"><table class="sets"><tr><th>Station (m)</th><th>Limit state</th><th>Case</th><th>Combination</th><th>Node</th><th>z (m)</th><th>N kN</th><th>M2 kNm</th><th>M3 kNm</th><th>Utilisation</th></tr>${rows}</table></div></details>`;
}

// A structural casing: its E·I share of the actions between its levels, checked as a filled tube.
function casingBlock(c) {
  if (!c?.tube) return "";
  const t = c.tube;
  const s = t.section || {};
  const r = t.resistances || {};
  const g = t.governing || {};
  return `<h3 style="margin-top:18px">Steel casing, ${fmt(c.top, 2)} to ${fmt(c.bottom, 2)} m
      <span class="sev ${t.passed ? "ok" : "error"}">${fmt(t.utilisation, 2)}</span></h3>
    <p class="status">${esc(c.note)}</p>
    <div class="scroll"><table>
      <tr><td>Casing (corroded)</td><td>Ø${fmt(s.diameter_mm)} × ${fmt(s.thickness_mm)} mm ${esc(s.grade || "")} (Ø${fmt(s.corroded_diameter_mm)} × ${fmt(s.corroded_thickness_mm, 1)} mm)</td></tr>
      <tr><td>Share of the actions</td><td>${fmt(c.steel_share * 100)}% casing, ${fmt((1 - c.steel_share) * 100)}% concrete</td></tr>
      <tr><td>N<sub>pl,Rd</sub> / M<sub>pl,Rd</sub> / V<sub>pl,Rd</sub></td><td>${fmt(r.N_pl_kN)} kN / ${fmt(r.M_pl_kNm)} kNm / ${fmt(r.V_pl_kN)} kN</td></tr>
      ${g.combination ? `<tr><td>Governing</td><td>${esc(g.combination)}, z ${fmt(g.z, 2)} m: N = ${fmt(g.N_kN)} kN, M = ${fmt(g.M_kNm)} kNm, V = ${fmt(g.V_kN)} kN (${esc(g.check)})</td></tr>` : ""}
    </table></div>
    ${(t.notes || []).map((n) => `<p class="status">${esc(n)}</p>`).join("")}`;
}

function connectionBlock(c) {
  // Where a structural casing stops: welded bars (cover 0) and the cage, no casing.
  if (!c) return "";
  const g = c.governing;
  return `<h3 style="margin-top:18px">Casing connection, ${fmt(c.top, 2)} to ${fmt(c.bottom, 2)} m
      ${c.passed == null ? "" : `<span class="sev ${c.passed ? "ok" : "error"}">${c.passed ? "passes" : "fails"}</span>`}</h3>
    <p>Checked without the casing: ${c.welded ? `${esc(c.welded)} welded to the casing (cover 0) plus ` : ""}the head cage ${esc(c.cage)}.
      ${c.utilisation == null ? "" : `Utilisation ${fmt(c.utilisation, 2)}${g ? `, governed by ${esc(g.combination)} at z ${fmt(g.z, 2)} m (N = ${fmt(g.N_kN)} kN, M = ${fmt(g.M_kNm)} kNm)` : ""}.`}</p>
    ${c.notes.map((n) => `<p class="status">${esc(n)}</p>`).join("")}`;
}

function shearBlock(sh, above = "the slab") {
  const g = sh.governing;
  const why = { shear: "shear", minimum: "9.5.3 maximum spacing", "near slab": `0.6 × spacing below ${above}`, "at lap": "0.6 × spacing at laps" };
  return `<h3 style="margin:20px 0 4px;font-size:15px">Shear and links <span class="sev ${sh.passed ? "ok" : "error"}">${sh.passed ? "passes" : "fails"}</span></h3>
    <p style="margin:4px 0 8px">Governing: ${esc(g.combination)}, z ${fmt(g.z, 2)} m. V<sub>Ed</sub> = ${fmt(g.V_kN)} kN with N<sub>Ed</sub> = ${fmt(g.N_kN)} kN;
      V<sub>Rd,c</sub> = ${fmt(g.VRd_c_kN)} kN${g.N_kN < 0 ? " (pile in tension: no concrete contribution)" : ""}, V<sub>Rd,max</sub> = ${fmt(g.VRd_max_kN)} kN at cot θ = ${fmt(g.cot_theta, 2)}. Utilisation ${fmt(sh.utilisation, 2)}.</p>
    <div class="scroll"><table><tr><th>From</th><th>To</th><th>Links</th><th>Set by</th></tr>
      ${sh.zones.map((z) => `<tr><td>${fmt(z.top, 2)}</td><td>${fmt(z.bottom, 2)}</td><td>${esc(z.link)}</td><td>${esc(why[z.reason] || z.reason)}</td></tr>`).join("")}
    </table></div>
    <p class="status">${esc(sh.method)}. Largest spacing ${fmt(sh.max_spacing_mm)} mm, smallest link Ø${fmt(sh.min_link_diameter_mm)} (9.5.3). ${fmt(sh.links_kg)} kg of links per pile${sh.inner_rings ? `, of which ${fmt(sh.inner_links_kg)} kg in ${sh.inner_rings} inner ring${sh.inner_rings > 1 ? "s" : ""} around the inner row${sh.inner_rings > 1 ? "s" : ""}` : ""}.</p>
    ${sh.notes.map((n) => `<p class="status">${esc(n)}</p>`).join("")}`;
}

// Construction joints set on the element: the check at each and the bars it needs there.
function jointsBlock(list) {
  if (!list?.length) return "";
  const pm = (j) => (j.provided_mm2_per_m != null ? "mm²/m" : "mm²");
  const val = (j, k) => j[`${k}_mm2_per_m`] ?? j[`${k}_mm2`];
  const extra = (j) => {
    const parts = (j.stretches?.length ? j.stretches : j.additional ? [{ bars: j.additional }] : [])
      .map((s) => (s.bars ? esc(s.bars.label) : `${fmt(s.additional_mm2_per_m)} mm²/m more from ${fmt(s.from_m, 2)} to ${fmt(s.to_m, 2)} m: no allowed bar fits`));
    return parts.length ? parts.map((t) => `<b>${t}</b>`).join("<br>") : "";
  };
  return `<h3 style="margin-top:18px">Construction joints</h3>
    <div class="scroll"><table><tr><th>Joint</th><th>Surface</th><th>Bars crossing</th><th>v<sub>Edi</sub> / v<sub>Rdi</sub></th><th>Needed (tension + shear)</th><th>Utilisation</th><th>Additional bars at this joint</th><th></th></tr>
    ${list.map((j) => `<tr><td>${esc(j.where)}${j.note ? `<br><span class="status">${esc(j.note)}</span>` : ""}</td>
      <td>${esc(j.surface)} (c ${fmt(j.c, 3)}, μ ${fmt(j.mu, 2)})</td>
      <td>${j.crossing ? `${esc(j.crossing.label)}<br>${fmt(val(j, "provided"))} ${pm(j)}` : "–"}</td>
      <td>${j.v_Edi_MPa != null ? `${fmt(j.v_Edi_MPa, 2)} / ${fmt(j.v_Rdi_MPa, 2)} MPa (max ${fmt(j.v_Rdi_max_MPa, 2)})` : "–"}</td>
      <td>${val(j, "needed") != null ? `${fmt(val(j, "tension"))} + ${fmt(val(j, "shear"))} = ${fmt(val(j, "needed"))} ${pm(j)}` : "–"}</td>
      <td>${j.utilisation != null ? `<b class="${j.utilisation > 1 ? "bad" : ""}">${fmt(j.utilisation, 2)}</b>` : "–"}</td>
      <td>${extra(j) || esc(j.status || "")}${(j.laps || []).map((w) => `<br><span class="status">${esc(w)}</span>`).join("")}</td>
      <td>${j.passed == null ? "" : j.passed ? '<span class="sev ok">OK</span>' : '<span class="sev error">more bars</span>'}</td></tr>`).join("")}
    </table></div>
    <p class="status">EN 1992-1-1 6.2.5 at each joint with the Plaxis actions there (ULS): the bars crossing it carry the tension of N with M, and the shear friction steel on top of it. Details of each check in the report.</p>`;
}

function curtailmentBlock(c) {
  const mode = c.mode === "standard_lengths" ? "standard cut lengths" : "least steel";
  const joint = { lap: "lap", coupler: "coupler", toe: "toe" };
  const saving = c.unified_weight_kg ? Math.round((1 - c.weight_kg / c.unified_weight_kg) * 100) : null;
  return `<h3 style="margin:20px 0 4px;font-size:15px">Reinforcement down the pile</h3>
    <p class="status" style="margin:0 0 8px">Zones chosen for ${mode}. Main bars: ${fmt(c.weight_kg)} kg per pile, ${fmt(c.steel_ratio_kg_m3)} kg/m³${saving != null ? `, against ${fmt(c.unified_steel_ratio_kg_m3)} kg/m³ with the head cage all the way down (${saving}% less)` : ""}.${c.couplers ? ` ${c.couplers} couplers.` : ""}</p>
    <div class="cage"><div class="chart" data-kind="elevation"></div><div class="scroll"><table>
      <tr><th>From</th><th>To</th><th>Cage</th><th>Bar lengths</th><th>Above head</th><th>Below</th><th>ULS N–M</th><th>SLS crack</th></tr>
      ${c.runs.map((r) => `<tr><td>${fmt(r.top, 2)}</td><td>${fmt(r.bottom, 2)}</td><td>${esc(r.cage.label)}</td>
        <td>${r.bar_lengths_m.map((x) => fmt(x, 2)).join(" / ")} m</td>
        <td>${r.above_head_m?.length ? `${r.above_head_m.map((x) => fmt(x, 2)).join(" / ")} m` : "–"}</td>
        <td>${r.joint === "toe" ? "toe" : r.joint === "coupler" ? "couplers" : `lap ${r.lap_below_m.map((x) => fmt(x, 2)).join(" / ")} m`}</td>
        <td class="${r.utilisation > 1 ? "bad" : ""}">${fmt(r.utilisation, 2)}</td><td class="${r.crack_utilisation > 1 ? "bad" : ""}">${r.crack_utilisation == null ? "–" : fmt(r.crack_utilisation, 2)}</td></tr>`).join("")}
    </table>
    ${c.notes.map((n) => `<p class="status">${esc(n)}</p>`).join("")}</div></div>`;
}

function elevationDrawing(el, c) {
  // Pile elevation: each run's bars drawn from its top to the end of its lap, alternating sides.
  const w = 300, h = 460, m = { t: 16, b: 16, l: 52 };
  const top = c.head_level, toe = c.toe_level;
  const y = (z) => m.t + ((top - z) / (top - toe)) * (h - m.t - m.b);
  const x0 = m.l + 20, pw = 70;
  const levels = ticks(toe, top, 6);
  const runs = c.runs
    .map((r, i) => {
      const x = x0 + (i % 2 ? pw * 0.62 : pw * 0.38);
      const end = r.bottom - Math.max(...r.lap_below_m);
      const sw = Math.max(1.5, r.cage.rings[0].diameter / 8);
      return `<line class="rebar" x1="${x}" x2="${x}" y1="${y(r.top)}" y2="${y(end)}" stroke-width="${sw}"><title>${esc(r.cage.label)}: ${fmt(r.top, 2)} to ${fmt(end, 2)} m</title></line>
        ${i ? `<line class="grid" x1="${x0 - 10}" x2="${w - 8}" y1="${y(r.top)}" y2="${y(r.top)}" stroke-dasharray="4 4"/>` : ""}
        <text class="label" x="${x0 + pw + 12}" y="${(y(r.top) + y(r.bottom)) / 2 + 4}">${esc(r.cage.label)}</text>`;
    })
    .join("");
  el.innerHTML = `<div class="chart-title">Elevation, ${fmt(top, 2)} to ${fmt(toe, 2)} m</div>
    <svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Pile elevation with reinforcement zones">
      ${levels.map((v) => `<text class="tick" x="${m.l - 6}" y="${y(v) + 4}" text-anchor="end">${fmt(v)}</text>`).join("")}
      <rect class="outline" x="${x0}" y="${y(top)}" width="${pw}" height="${y(toe) - y(top)}" style="stroke-width:1.5"/>
      ${runs}
    </svg>`;
}

function sectionDrawing(el, p) {
  // Cross-section to scale: pile outline, link, bars row by row (outer row first).
  const s = p.section;
  const a = p.arrangement;
  const R = s.diameter_mm / 2;
  const pad = R * 0.08;
  const box = 2 * (R + pad);
  const link = R - s.cover_mm - s.link_diameter_mm / 2;
  const bars = a.rings
    .map((r, i) =>
      Array.from({ length: r.count }, (_, k) => {
        const t = -Math.PI / 2 + (2 * Math.PI * k) / r.count;
        return `<circle class="${i ? "bar inner" : "bar"}" cx="${(r.radius * Math.cos(t)).toFixed(1)}" cy="${(r.radius * Math.sin(t)).toFixed(1)}" r="${r.diameter / 2}"><title>Row ${i + 1}: Ø${r.diameter}</title></circle>`;
      }).join("")
    )
    .join("");
  el.innerHTML = `<div class="chart-title">Section, Ø${fmt(s.diameter_mm)} mm</div>
    <svg viewBox="${-R - pad} ${-R - pad} ${box} ${box}" role="img" aria-label="Pile cross-section with ${esc(a.label)}">
      <circle class="outline" r="${R}"/>
      <circle class="link" r="${link}" stroke-width="${s.link_diameter_mm}"/>
      ${bars}
    </svg>`;
}

// Small SVG chart helpers ------------------------------------------------------
function ticks(lo, hi, n = 5) {
  const span = hi - lo || 1;
  const step0 = span / n;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0);
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
  return out;
}

// Enough decimals to tell neighbouring ticks apart (0.25 steps need two).
const decimals = (t) => (t.length > 1 ? (String(+(t[1] - t[0]).toFixed(10)).split(".")[1] || "").length : 0);

function frame(el, { w = 520, h = 340, xDomain, yDomain, xLabel, yLabel, title, yReverse = false }) {
  const m = { l: 64, r: 16, t: 28, b: 44 };
  const x = (v) => m.l + ((v - xDomain[0]) / (xDomain[1] - xDomain[0])) * (w - m.l - m.r);
  const y = (v) => {
    const f = (v - yDomain[0]) / (yDomain[1] - yDomain[0]);
    return yReverse ? m.t + f * (h - m.t - m.b) : h - m.b - f * (h - m.t - m.b);
  };
  const xt = ticks(...xDomain);
  const yt = ticks(...yDomain);
  const grid =
    xt.map((v) => `<line class="grid" x1="${x(v)}" x2="${x(v)}" y1="${m.t}" y2="${h - m.b}"/><text class="tick" x="${x(v)}" y="${h - m.b + 16}" text-anchor="middle">${fmt(v, decimals(xt))}</text>`).join("") +
    yt.map((v) => `<line class="grid" x1="${m.l}" x2="${w - m.r}" y1="${y(v)}" y2="${y(v)}"/><text class="tick" x="${m.l - 8}" y="${y(v) + 4}" text-anchor="end">${fmt(v, decimals(yt))}</text>`).join("");
  el.innerHTML = `<div class="chart-title">${esc(title)}</div>
    <svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(title)}">${grid}
    <text class="axis" x="${(m.l + w - m.r) / 2}" y="${h - 8}" text-anchor="middle">${esc(xLabel)}</text>
    <text class="axis" transform="translate(14 ${(m.t + h - m.b) / 2}) rotate(-90)" text-anchor="middle">${esc(yLabel)}</text>
    <g class="marks"></g></svg><div class="tip" hidden></div>`;
  return { svg: el.querySelector("svg"), g: el.querySelector("g.marks"), tip: el.querySelector(".tip"), x, y, w, h, m };
}

function showTip(c, evt, html) {
  const r = c.svg.getBoundingClientRect();
  c.tip.hidden = false;
  c.tip.innerHTML = html;
  c.tip.style.left = `${Math.min(evt.clientX - r.left + 12, r.width - 180)}px`;
  c.tip.style.top = `${evt.clientY - r.top + 12}px`;
}

function nmChart(el, p, whole = false) {
  // The closed N–M interaction diagram: N up (compression +), M across, the capacity curve drawn for
  // both signs of M. Each result sits at its resultant √(M2² + M3²), on the side of the sign of its
  // larger component. The curve runs from the full tension to the squash load, tens of MN, while
  // pile loads are a few MN, so the chart opens zoomed round the loads; the other view shows it all.
  const half = p.curve;
  const curve = half.concat(half.slice().reverse().map(([n, m]) => [n, -m]));
  const pts = p.points.map((q) => [q[0], q[1], q[4] ?? q[2], q[3]]);
  const curveN = half.map((q) => q[0]);
  const full = [Math.min(...curveN), Math.max(...curveN)];
  const fullM = Math.max(...half.map((q) => q[1]), ...pts.map((q) => Math.abs(q[2])), 1);
  // The window round the loads.
  const loadN = pts.map((q) => q[1]);
  const lo = Math.min(...loadN), hi = Math.max(...loadN);
  const pad = Math.max(0.3 * (hi - lo), 0.04 * (full[1] - full[0]), 200);
  const win = loadN.length ? [Math.max(lo - pad, full[0]), Math.min(hi + pad, full[1])] : full;
  const edgeM = (n) => {
    let best = 0;
    for (let i = 1; i < half.length; i++) {
      const [a, b] = [half[i - 1], half[i]];
      if ((a[0] - n) * (b[0] - n) <= 0 && a[0] !== b[0]) best = Math.max(best, a[1] + ((n - a[0]) / (b[0] - a[0])) * (b[1] - a[1]));
    }
    return best;
  };
  const winM = Math.max(...half.filter((q) => q[0] >= win[0] && q[0] <= win[1]).map((q) => q[1]), edgeM(win[0]), edgeM(win[1]),
    ...pts.map((q) => Math.abs(q[2])), 1) * 1.08;
  const yDomain = whole ? [full[0] - 0.05 * (full[1] - full[0]), full[1] + 0.05 * (full[1] - full[0])] : win;
  const X = whole ? fullM * 1.08 : winM;
  const c = frame(el, {
    xDomain: [-X, X], yDomain,
    xLabel: "M (kNm), side set by the sign of the larger of M2 and M3", yLabel: "N (kN, compression +)",
    title: `N–M interaction${p.curtailment?.runs?.length > 1 ? " of the head cage" : ""}, all ULS results (${whole ? "whole diagram" : "zoomed to the loads"})`,
  });
  const clip = `nmclip${Math.random().toString(36).slice(2, 8)}`;
  const path = curve.map((q, i) => `${i ? "L" : "M"}${c.x(q[1]).toFixed(1)},${c.y(q[0]).toFixed(1)}`).join("") + "Z";
  // The governing point: the most utilised result.
  let gi = -1;
  pts.forEach((q, i) => { if (gi < 0 || q[3] > pts[gi][3]) gi = i; });
  const gp = gi >= 0 ? pts[gi] : null;
  const zoomBox = whole && loadN.length
    ? `<rect class="zoom-box" x="${c.x(-winM)}" y="${c.y(win[1])}" width="${c.x(winM) - c.x(-winM)}" height="${c.y(win[0]) - c.y(win[1])}"/>
       <text class="label" x="${c.x(winM) + 4}" y="${c.y(win[1]) + 12}">loads</text>`
    : "";
  c.g.innerHTML = `<defs><clipPath id="${clip}"><rect x="${c.m.l}" y="${c.m.t}" width="${c.w - c.m.l - c.m.r}" height="${c.h - c.m.t - c.m.b}"/></clipPath></defs>
    <g clip-path="url(#${clip})">
    <path class="cap-area" d="${path}"/>
    ${yDomain[0] < 0 && yDomain[1] > 0 ? `<line class="zero" x1="${c.x(-X)}" x2="${c.x(X)}" y1="${c.y(0)}" y2="${c.y(0)}"/>` : ""}
    <line class="zero" x1="${c.x(0)}" x2="${c.x(0)}" y1="${c.m.t}" y2="${c.h - c.m.b}"/>
    ${pts.map((q) => `<circle class="pt" cx="${c.x(q[2]).toFixed(1)}" cy="${c.y(q[1]).toFixed(1)}" r="3"/>`).join("")}
    <path class="cap" d="${path}"/>
    ${zoomBox}
    ${gp ? `<circle class="gov" cx="${c.x(gp[2])}" cy="${c.y(gp[1])}" r="5"/>
      <text class="label" x="${c.x(gp[2]) + (gp[2] >= 0 ? -8 : 8)}" y="${c.y(gp[1]) - 8}" text-anchor="${gp[2] >= 0 ? "end" : "start"}">governing, ${fmt(gp[3], 2)}</text>` : ""}
    </g>`;
  const pick = document.createElement("div");
  pick.className = "row nm-view";
  pick.innerHTML = `<button class="quiet${whole ? "" : " on"}" data-nm="zoom">Around the loads</button><button class="quiet${whole ? " on" : ""}" data-nm="whole">Whole diagram</button>`;
  el.append(pick);
  pick.querySelectorAll("[data-nm]").forEach((b) => (b.onclick = () => nmChart(el, p, b.dataset.nm === "whole")));
  const xs = pts.map((q) => c.x(q[2]));
  const ys = pts.map((q) => c.y(q[1]));
  c.svg.onmousemove = (evt) => {
    const r = c.svg.getBoundingClientRect();
    const sx = ((evt.clientX - r.left) / r.width) * c.w;
    const sy = ((evt.clientY - r.top) / r.height) * c.h;
    let best = -1, bd = 144; // within 12 px
    for (let i = 0; i < xs.length; i++) {
      const d = (xs[i] - sx) ** 2 + (ys[i] - sy) ** 2;
      if (d < bd) { bd = d; best = i; }
    }
    if (best < 0) { c.tip.hidden = true; return; }
    const q = pts[best];
    showTip(c, evt, `<b>${esc(q[0])}</b><br>N ${fmt(q[1])} kN${q[1] < 0 ? " (tension)" : ""}<br>M ${fmt(Math.abs(q[2]))} kNm (resultant)<br>utilisation ${fmt(q[3], 3)}`);
  };
  c.svg.onmouseleave = () => (c.tip.hidden = true);
}

function profileChart(el, p, title = "Utilisation along the pile", markLevel = null) {
  // Max utilisation at each level (0.1 m bands): z up the page, utilisation across.
  const prof = p.profile.filter((q) => q.util != null);
  const zs = prof.map((q) => q.z);
  const maxU = Math.max(1.1, ...prof.map((q) => q.util)) * 1.05;
  const c = frame(el, {
    xDomain: [0, maxU], yDomain: [Math.min(...zs), Math.max(...zs)],
    xLabel: "Utilisation", yLabel: "Level z (m)", title,
  });
  const path = prof.map((q, i) => `${i ? "L" : "M"}${c.x(q.util).toFixed(1)},${c.y(q.z).toFixed(1)}`).join("");
  const mark = markLevel != null && markLevel > Math.min(...zs) && markLevel < Math.max(...zs)
    ? `<line class="grid" x1="${c.m.l}" x2="${c.w - c.m.r}" y1="${c.y(markLevel)}" y2="${c.y(markLevel)}" stroke-dasharray="4 3"/>
       <text class="label" x="${c.w - c.m.r - 4}" y="${c.y(markLevel) - 4}" text-anchor="end">infill bottom ${fmt(markLevel, 1)} m</text>`
    : "";
  c.g.innerHTML = `<line class="limit" x1="${c.x(1)}" x2="${c.x(1)}" y1="${c.m.t}" y2="${c.h - c.m.b}"/>
    <text class="label" x="${c.x(1) + 4}" y="${c.m.t + 12}">1.0</text>${mark}
    <path class="series" d="${path}"/>`;
  c.svg.onmousemove = (evt) => {
    const r = c.svg.getBoundingClientRect();
    const sy = ((evt.clientY - r.top) / r.height) * c.h;
    let best = 0;
    prof.forEach((q, i) => { if (Math.abs(c.y(q.z) - sy) < Math.abs(c.y(prof[best].z) - sy)) best = i; });
    const q = prof[best];
    c.g.querySelector(".hover")?.remove();
    c.g.insertAdjacentHTML("beforeend", `<circle class="hover" cx="${c.x(q.util)}" cy="${c.y(q.z)}" r="4"/>`);
    showTip(c, evt, `z ${fmt(q.z, 2)} m<br>utilisation ${fmt(q.util, 3)}`);
  };
  c.svg.onmouseleave = () => { c.tip.hidden = true; c.g.querySelector(".hover")?.remove(); };
}
