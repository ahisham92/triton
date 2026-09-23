// Triton front end: projects, schema-driven setup forms and the workbook check.
const $app = document.getElementById("app");
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
    const err = new Error(typeof data.detail === "string" ? data.detail : res.statusText);
    err.detail = data.detail;
    throw err;
  }
  return data;
}

let SCHEMA = null;
let MATERIALS = null;
async function reference() {
  if (!SCHEMA) [SCHEMA, MATERIALS] = await Promise.all([api("/api/schema/project"), api("/api/materials")]);
}
const resolve = (node) => (node && node.$ref ? SCHEMA.$defs[node.$ref.split("/").pop()] : node);

// ---------------------------------------------------------------- routing
window.addEventListener("hashchange", route);
route();

function route() {
  const hash = location.hash.slice(1) || "/";
  const m = hash.match(/^\/project\/([a-f0-9]+)(?:\/(\w+))?$/);
  if (m) return projectPage(m[1], m[2] || "info");
  if (hash === "/check") return checkPage();
  return projectsPage();
}

// ---------------------------------------------------------------- projects list
async function projectsPage() {
  $app.innerHTML = `<h1>Projects</h1><p class="sub">Each project holds the sections, materials and design
    settings used to design the elements in a Plaxis workbook.</p>
    <div class="row"><input id="new-name" placeholder="Project name" style="flex:1;max-width:320px;padding:7px 9px;border:1px solid var(--line);border-radius:7px;background:var(--input);color:var(--text);font:inherit">
    <button id="new">New project</button></div>
    <h2>Saved projects</h2><div class="panel scroll" id="list">Loading…</div>`;
  document.getElementById("new").onclick = async () => {
    const name = document.getElementById("new-name").value.trim() || "New project";
    const p = await api("/api/projects", { method: "POST", body: JSON.stringify({ info: { name } }) });
    location.hash = `#/project/${p.id}/info`;
  };
  const list = await api("/api/projects");
  const el = document.getElementById("list");
  if (!list.length) {
    el.innerHTML = `<p class="empty">No projects yet.</p>`;
    return;
  }
  el.innerHTML =
    `<table><tr><th>Name</th><th>Number</th><th>Section</th><th>Elements</th><th>Last saved</th></tr>` +
    list
      .map(
        (p) => `<tr class="link" data-id="${esc(p.id)}"><td>${esc(p.name)}</td><td>${esc(p.number)}</td>
        <td>${esc(p.section)}</td><td>${p.elements}</td><td>${esc(p.updated_at.replace("T", " ").slice(0, 16))}</td></tr>`
      )
      .join("") +
    `</table>`;
  el.querySelectorAll("tr.link").forEach((tr) => (tr.onclick = () => (location.hash = `#/project/${tr.dataset.id}/info`)));
}

// ---------------------------------------------------------------- project page
let state = null; // { project, dirty }

async function projectPage(id, tab) {
  await reference();
  if (!state || state.project.id !== id) {
    try {
      state = { project: await api(`/api/projects/${id}`), dirty: false, errors: [] };
    } catch (e) {
      $app.innerHTML = `<p>${esc(e.message)} <a href="#/">Back to projects</a></p>`;
      return;
    }
  }
  const p = state.project;
  const tabs = [
    ["info", "Project"],
    ["settings", "Design settings"],
    ["elements", `Elements (${Object.keys(p.elements).length})`],
    ["workbook", "Workbook"],
    ["design", "Design"],
  ];
  $app.innerHTML = `<h1>${esc(p.info.name)}</h1>
    <p class="sub">${esc([p.info.number, p.info.section].filter(Boolean).join(" · ") || "Project setup")}</p>
    <div class="tabs">${tabs.map(([k, t]) => `<button data-tab="${k}" class="${k === tab ? "on" : ""}">${t}</button>`).join("")}</div>
    <div id="tab"></div>
    <div class="savebar"><button id="save">Save</button><span class="status" id="save-status"></span>
      <span style="flex:1"></span><button class="danger" id="delete">Delete project</button></div>
    <ul class="errors" id="errors"></ul>`;
  $app.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => (location.hash = `#/project/${id}/${b.dataset.tab}`)));
  document.getElementById("save").onclick = save;
  document.getElementById("delete").onclick = async () => {
    if (!confirm(`Delete "${p.info.name}"? This cannot be undone.`)) return;
    await api(`/api/projects/${id}`, { method: "DELETE" });
    state = null;
    location.hash = "#/";
  };
  const host = document.getElementById("tab");
  if (tab === "info") host.append(renderObject(SCHEMA.properties.info, p.info, "info", "Project"));
  else if (tab === "settings") host.append(renderObject(SCHEMA.properties.design, p.design, "design", "Design settings"));
  else if (tab === "design") renderDesignTab(host);
  else if (tab === "elements") renderElements(host);
  else if (tab === "workbook") renderWorkbookTab(host);
  showSaveState();
  showErrors();
}

function markDirty() {
  state.dirty = true;
  showSaveState();
}
function showSaveState(text) {
  const s = document.getElementById("save-status");
  if (s) s.textContent = text || (state.dirty ? "Unsaved changes" : "All changes saved");
}

async function save() {
  showSaveState("Saving…");
  try {
    state.project = await api(`/api/projects/${state.project.id}`, { method: "PUT", body: JSON.stringify(state.project) });
    state.dirty = false;
    state.errors = [];
    showSaveState();
  } catch (e) {
    state.errors = Array.isArray(e.detail) ? e.detail : [{ loc: [], msg: e.message }];
    showSaveState("Not saved: fix the fields below");
  }
  showErrors();
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
  // ["elements", "Pile(1)", "pile", "casing", "thickness"] -> "elements.Pile(1).casing.thickness"
  const out = [];
  loc.forEach((x, i) => {
    if (i === 2 && loc[0] === "elements" && typeof x === "string" && /^[a-z_]+$/.test(x)) return;
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
  for (const [k, prop] of Object.entries(schema.properties || {})) {
    if (HIDDEN.has(k)) continue;
    const p = path ? `${path}.${k}` : k;
    const nullable = Boolean(prop.anyOf && prop.anyOf.some((a) => a.type === "null"));
    const inner = nullable ? resolve(prop.anyOf.find((a) => a.type !== "null")) : resolve(prop);
    grid.append(inner.properties ? renderNested(obj, k, prop, inner, nullable, p) : renderField(obj, k, prop, inner, nullable, p));
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
    control = `<select>${options
      .map((o) => `<option value="${esc(o)}" ${o === value ? "selected" : ""}>${esc(typeof o === "string" ? prettyOption(o) : o)}</option>`)
      .join("")}</select>`;
  } else if (inner.type === "number" || inner.type === "integer") {
    control = `<input type="number" step="any" value="${value ?? ""}" ${nullable ? 'placeholder="not set"' : ""}>`;
  } else {
    control = `<input type="text" value="${esc(value ?? "")}">`;
  }
  f.innerHTML = `<label>${esc(title)}</label><div class="inputwrap">${control}${unit ? `<span class="unit">${esc(unit)}</span>` : ""}</div>${hint}`;
  const input = f.querySelector("input, select");
  input.oninput = input.onchange = () => {
    let v = input.value;
    if (options) v = options.find((o) => String(o) === v);
    else if (inner.type === "number" || inner.type === "integer") v = v === "" ? (nullable ? null : v) : Number(v);
    obj[key] = v;
    markDirty();
  };
  return f;
}

function optionLabel(key, o) {
  if (key === "rows") return o === 1 ? "1 row" : `${o} rows`;
  return String(o);
}

function prettyOption(o) {
  const map = { crack_only: "Crack width only", structural: "Structural (shares load)", min_steel: "Least steel",
    lap: "Lapped", coupler: "Couplers", least_steel: "Least steel", standard_lengths: "Standard cut lengths",
    min_cost: "Lowest cost", uniform: "Uniform slab", column_and_field: "Column and field strips" };
  return map[o] || o;
}

// ---------------------------------------------------------------- elements tab
const KIND_LABEL = { pile: "Pile", combi_wall: "Combi wall", sheet_pile_wall: "Sheet pile wall", slab: "Slab",
  front_beam: "Front beam", rear_beam: "Rear beam" };

function renderElements(host) {
  const p = state.project;
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
  if (!names.length) {
    host.insertAdjacentHTML("beforeend", `<p class="empty">No elements yet.</p>`);
    return;
  }
  for (const name of names) {
    const el = p.elements[name];
    const schema = { $ref: SCHEMA.properties.elements.additionalProperties.discriminator.mapping[el.kind] };
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
    const fs = renderObject(schema, el, `elements.${name}`, "");
    fs.style.border = "0";
    fs.style.padding = "0";
    card.append(fs);
    host.append(card);
  }
}

async function addElements(names) {
  if (state.dirty) await save();
  if (state.errors?.length) return;
  const res = await api(`/api/projects/${state.project.id}/elements`, { method: "POST", body: JSON.stringify({ names }) });
  state.project = res.project;
  const s = document.getElementById("el-status");
  const skipped = names.filter((n) => !res.added.includes(n) && !(n in res.project.elements));
  const msg = [res.added.length ? `Added ${res.added.join(", ")}.` : "Nothing new to add.",
    skipped.length ? `Not recognised: ${skipped.join(", ")}.` : ""].join(" ");
  route();
  const s2 = document.getElementById("el-status") || s;
  if (s2) s2.textContent = msg;
}

// ---------------------------------------------------------------- workbook check
function checkerHtml() {
  return `<div class="panel row">
      <input type="file" id="file" accept=".xlsb,.xlsx,.xlsm">
      <button id="run" disabled>Check workbook</button>
      <span class="status" id="status"></span></div>
    <div id="report" hidden>
      <div class="counts">
        <div class="count error"><b id="n-error">0</b>errors</div>
        <div class="count warning"><b id="n-warning">0</b>warnings</div>
        <div class="count"><b id="n-info">0</b>automatic clean-ups</div>
      </div>
      <div id="add-found"></div>
      <h2>Sheets found</h2>
      <div class="panel scroll"><table id="coverage"></table></div>
      <h2>Problems to review</h2>
      <div class="panel scroll"><table id="problems"></table></div>
      <details class="panel" style="margin-top:16px"><summary>Automatic clean-ups</summary>
        <div class="scroll"><table id="cleanups"></table></div></details>
    </div>`;
}

function wireChecker(onReport, url = "/api/workbooks/check") {
  const file = document.getElementById("file");
  const run = document.getElementById("run");
  file.onchange = () => (run.disabled = !file.files.length);
  run.onclick = async () => {
    const f = file.files[0];
    if (!f) return;
    run.disabled = true;
    document.getElementById("status").textContent = `Reading ${f.name}…`;
    const body = new FormData();
    body.append("file", f);
    try {
      const data = await api(url, { method: "POST", body });
      renderReport(data);
      onReport?.(data);
      document.getElementById("status").textContent = `Checked ${data.file}`;
    } catch (e) {
      document.getElementById("status").textContent = `Failed: ${e.message}`;
    } finally {
      run.disabled = false;
    }
  };
}

function checkPage() {
  $app.innerHTML = `<h1>Check a workbook</h1>
    <p class="sub">Upload the Plaxis straining-actions workbook to check it before design.</p>` + checkerHtml();
  wireChecker();
}

async function renderWorkbookTab(host) {
  const id = state.project.id;
  host.innerHTML = `<p class="sub" id="wb-note">Upload the Plaxis workbook for this project. It is checked, then kept with
    the project so the elements can be designed without uploading it again.</p>` + checkerHtml();
  const onReport = (data) => {
    document.getElementById("wb-note").textContent =
      `Workbook in use: ${data.file}, uploaded ${String(data.uploaded_at || "").replace("T", " ").slice(0, 16)}. Upload again to replace it.`;
    const missing = data.elements.filter((e) => !(e in state.project.elements));
    const box = document.getElementById("add-found");
    if (!missing.length) {
      box.innerHTML = `<p class="status">Every element in the workbook is already in the project.</p>`;
      return;
    }
    box.innerHTML = `<div class="panel row" style="margin-top:16px"><span>${missing.length} element(s) in the workbook are not in the project yet: ${esc(missing.join(", "))}.</span>
      <button id="add-all">Add to project</button></div>`;
    document.getElementById("add-all").onclick = async () => {
      await addElements(missing);
      location.hash = `#/project/${id}/elements`;
    };
  };
  wireChecker(onReport, `/api/projects/${id}/workbook`);
  try {
    const stored = await api(`/api/projects/${id}/workbook`);
    renderReport(stored);
    onReport(stored);
  } catch {
    /* no workbook yet */
  }
}

const LABEL = { ok: "OK", warning: "Check", error: "Error", missing: "—" };
function renderReport(d) {
  for (const k of ["error", "warning", "info"]) document.getElementById("n-" + k).textContent = d.counts[k];
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
  const problems = d.issues.filter((i) => i.severity !== "info");
  document.getElementById("problems").innerHTML = problems.length ? head + problems.map(row).join("") : "<tr><td>No problems found.</td></tr>";
  document.getElementById("cleanups").innerHTML = head + d.issues.filter((i) => i.severity === "info").map(row).join("");
  document.getElementById("report").hidden = false;
}

// ---------------------------------------------------------------- design tab
async function renderDesignTab(host) {
  const id = state.project.id;
  const piles = Object.entries(state.project.elements).filter(([, e]) => e.kind === "pile");
  host.innerHTML = `<div class="panel row">
      <button id="run-piles" ${piles.length ? "" : "disabled"}>Design piles</button>
      <a class="quiet-link" id="cages" href="/api/projects/${id}/design/piles/cages.json" hidden>Download cages for Revit (JSON)</a>
      <span class="status" id="design-status">${piles.length ? `${piles.length} pile element(s): ${esc(piles.map(([n]) => n).join(", "))}` : "Add pile elements first."}</span>
    </div><div id="design-out"></div>`;
  document.getElementById("run-piles").onclick = async () => {
    if (state.dirty) await save();
    if (state.errors?.length) return;
    const status = document.getElementById("design-status");
    status.textContent = "Designing…";
    try {
      renderPileResults(await api(`/api/projects/${id}/design/piles`, { method: "POST" }));
      status.textContent = "Done.";
    } catch (e) {
      status.textContent = e.message;
    }
  };
  try {
    renderPileResults(await api(`/api/projects/${id}/design/piles`));
  } catch {
    /* not designed yet */
  }
}

const fmt = (v, d = 0) => (v == null || !isFinite(v) ? "–" : Number(v).toLocaleString("en-GB", { maximumFractionDigits: d, minimumFractionDigits: d }));

function renderPileResults(res) {
  const out = document.getElementById("design-out");
  if (!out) return;
  const link = document.getElementById("cages");
  if (link) link.hidden = !res.piles.length;
  const rows = res.piles
    .map((p) => {
      const a = p.arrangement;
      const g = p.governing;
      return `<tr><td>${esc(p.element)}</td><td>${a ? esc(a.label) : "–"}</td>
        <td class="cell ${p.passed ? "ok" : "error"}">${fmt(p.utilisation, 2)}</td>
        <td>${fmt(p.reinforcement_ratio_pct, 2)}%</td><td>${fmt(p.curtailment?.steel_ratio_kg_m3 ?? p.steel_ratio_kg_m3)}</td>
        <td>${g.combination ? `${esc(g.combination)}, z ${fmt(g.z, 2)} m` : "–"}</td></tr>`;
    })
    .join("");
  out.innerHTML = `<p class="status">Designed ${esc(res.run_at.replace("T", " ").slice(0, 16))}. Longitudinal steel only; links and crack width come next.</p>
    ${res.skipped.map((s) => `<p class="status">${esc(s)}</p>`).join("")}
    <div class="panel scroll"><table><tr><th>Element</th><th>Bars</th><th>Utilisation</th><th>ρ at head</th><th>kg/m³ over pile</th><th>Governing</th></tr>${rows}</table></div>
    <div id="pile-cards"></div>`;
  const cards = document.getElementById("pile-cards");
  for (const p of res.piles) cards.append(pileCard(p));
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
    <div class="counts" style="margin-top:0">
      <div class="count"><b>${fmt(p.utilisation, 2)}</b>max utilisation</div>
      <div class="count"><b>${fmt(p.curtailment?.steel_ratio_kg_m3 ?? p.steel_ratio_kg_m3)}</b>${p.curtailment?.runs?.length ? "kg/m³ over the pile, with laps" : "kg/m³ longitudinal"}</div>
      <div class="count"><b>${a ? fmt(a.clear_spacing_mm) : "–"} mm</b>clear spacing, outer row (allowed ${fmt(lim.min_clear_mm)} to ${fmt(lim.max_clear_mm)} mm)</div>
    </div>
    ${g.combination ? `<p>Governing: ${esc(g.combination)}, node ${g.node}, y ${fmt(g.y, 2)} m, z ${fmt(g.z, 2)} m.
      N<sub>Ed</sub> = ${fmt(g.N_kN)} kN (compression +), M<sub>Ed</sub> = ${fmt(g.M_kNm)} kNm, M<sub>Rd</sub> at this N = ${fmt(g.M_Rd_kNm)} kNm.</p>` : ""}
    ${p.notes.map((n) => `<p class="status">${esc(n)}</p>`).join("")}
    ${a?.rings ? `<div class="cage"><div class="chart" data-kind="section"></div><div class="scroll"><table>
      <tr><th>Row</th><th>Bars</th><th>Bar circle radius</th><th>Clear spacing</th></tr>
      ${a.rings.map((r, i) => `<tr><td>${i ? (r.count < a.rings[0].count ? `${i + 1} (half row)` : i + 1) : "1 (outer)"}</td><td>${r.count}Ø${r.diameter}</td><td>${fmt(r.radius)} mm</td><td>${fmt(r.clear_spacing_mm)} mm</td></tr>`).join("")}
      <tr><td>Total</td><td>${a.bar_count} bars</td><td colspan="2">${fmt(a.area_mm2)} mm², ${fmt(p.reinforcement_ratio_pct, 2)}%, ${fmt(a.weight_kg_per_m, 1)} kg/m</td></tr>
      </table>
      <p class="status">Clear gap between rows: ${lim.row_gap_mm == null ? "EN 1992-1-1 8.2 minimum" : `${fmt(lim.row_gap_mm)} mm`}.</p></div></div>` : ""}
    ${p.curtailment?.runs?.length ? curtailmentBlock(p.curtailment) : ""}
    <div class="charts"><div class="chart" data-kind="nm"></div><div class="chart" data-kind="profile"></div></div>
    <details style="margin-top:12px"><summary>Other cages that pass</summary><div class="scroll"><table>
      <tr><th>Bars</th><th>Rows</th><th>Area mm²</th><th>Utilisation</th><th>kg/m³</th><th>Clear spacing mm</th></tr>
      ${p.alternatives.map((x) => `<tr${x.chosen ? ' style="font-weight:600"' : ""}><td>${esc(x.label)}${x.chosen ? " (chosen)" : ""}</td><td>${x.rows}</td><td>${fmt(x.area_mm2)}</td><td>${fmt(x.utilisation, 3)}</td><td>${fmt(x.steel_ratio_kg_m3)}</td><td>${fmt(x.clear_spacing_mm)}</td></tr>`).join("")}
    </table></div></details>`;
  if (a?.rings) sectionDrawing(card.querySelector('[data-kind="section"]'), p);
  if (p.curtailment?.runs?.length) elevationDrawing(card.querySelector('[data-kind="elevation"]'), p.curtailment);
  if (p.curve.length) {
    nmChart(card.querySelector('[data-kind="nm"]'), p);
    profileChart(card.querySelector('[data-kind="profile"]'), p);
  }
  return card;
}

function curtailmentBlock(c) {
  const mode = c.mode === "standard_lengths" ? "standard cut lengths" : "least steel";
  const joint = { lap: "lap", coupler: "coupler", toe: "toe" };
  const saving = c.unified_weight_kg ? Math.round((1 - c.weight_kg / c.unified_weight_kg) * 100) : null;
  return `<h3 style="margin:20px 0 4px;font-size:15px">Reinforcement down the pile</h3>
    <p class="status" style="margin:0 0 8px">Zones chosen for ${mode}. ${fmt(c.weight_kg)} kg per pile, ${fmt(c.steel_ratio_kg_m3)} kg/m³${saving != null ? `, against ${fmt(c.unified_steel_ratio_kg_m3)} kg/m³ with the head cage all the way down (${saving}% less)` : ""}.${c.couplers ? ` ${c.couplers} couplers.` : ""}</p>
    <div class="cage"><div class="chart" data-kind="elevation"></div><div class="scroll"><table>
      <tr><th>From</th><th>To</th><th>Cage</th><th>Bar lengths</th><th>Below</th><th>Utilisation</th></tr>
      ${c.runs.map((r) => `<tr><td>${fmt(r.top, 2)}</td><td>${fmt(r.bottom, 2)}</td><td>${esc(r.cage.label)}</td>
        <td>${r.bar_lengths_m.map((x) => fmt(x, 2)).join(" / ")} m</td>
        <td>${r.joint === "toe" ? "toe" : r.joint === "coupler" ? "couplers" : `lap ${r.lap_below_m.map((x) => fmt(x, 2)).join(" / ")} m`}</td>
        <td>${fmt(r.utilisation, 2)}</td></tr>`).join("")}
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

function nmChart(el, p) {
  // x: M (kNm), y: N (kN, compression up). Capacity curve for persistent factors.
  const curve = p.curve;
  const pts = p.points;
  const maxM = Math.max(...curve.map((c) => c[1]), ...pts.map((q) => q[2])) * 1.05;
  const nVals = curve.map((c) => c[0]).concat(pts.map((q) => q[1]));
  const c = frame(el, {
    xDomain: [0, maxM], yDomain: [Math.min(...nVals) * 1.05, Math.max(...nVals) * 1.05],
    xLabel: "M (kNm)", yLabel: "N (kN, compression +)", title: "N–M interaction, all ULS results",
  });
  const path = curve.map((q, i) => `${i ? "L" : "M"}${c.x(q[1]).toFixed(1)},${c.y(q[0]).toFixed(1)}`).join("");
  const g = p.governing;
  c.g.innerHTML = `<line class="zero" x1="${c.x(0)}" x2="${c.x(maxM)}" y1="${c.y(0)}" y2="${c.y(0)}"/>
    ${pts.map((q) => `<circle class="pt" cx="${c.x(q[2]).toFixed(1)}" cy="${c.y(q[1]).toFixed(1)}" r="3"/>`).join("")}
    <path class="cap" d="${path}"/>
    ${g.combination ? `<circle class="gov" cx="${c.x(g.M_kNm)}" cy="${c.y(g.N_kN)}" r="5"/>
      <text class="label" x="${c.x(g.M_kNm) - 8}" y="${c.y(g.N_kN) - 8}" text-anchor="end">governing, ${fmt(p.utilisation, 2)}</text>` : ""}
    <text class="label" x="${c.x(curve[Math.floor(curve.length / 3)][1]) + 6}" y="${c.y(curve[Math.floor(curve.length / 3)][0])}">capacity</text>`;
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
    showTip(c, evt, `<b>${esc(q[0])}</b><br>N ${fmt(q[1])} kN<br>M ${fmt(q[2])} kNm<br>utilisation ${fmt(q[3], 3)}`);
  };
  c.svg.onmouseleave = () => (c.tip.hidden = true);
}

function profileChart(el, p) {
  // Max utilisation at each level (0.1 m bands): z up the page, utilisation across.
  const prof = p.profile;
  const zs = prof.map((q) => q.z);
  const maxU = Math.max(1.1, ...prof.map((q) => q.util)) * 1.05;
  const c = frame(el, {
    xDomain: [0, maxU], yDomain: [Math.min(...zs), Math.max(...zs)],
    xLabel: "Utilisation", yLabel: "Level z (m)", title: "Utilisation along the pile",
  });
  const path = prof.map((q, i) => `${i ? "L" : "M"}${c.x(q.util).toFixed(1)},${c.y(q.z).toFixed(1)}`).join("");
  c.g.innerHTML = `<line class="limit" x1="${c.x(1)}" x2="${c.x(1)}" y1="${c.m.t}" y2="${c.h - c.m.b}"/>
    <text class="label" x="${c.x(1) + 4}" y="${c.m.t + 12}">1.0</text>
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
