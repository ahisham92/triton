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
    `<table><tr><th>Name</th><th>Number</th><th>Sections</th><th>Elements</th><th>Last saved</th></tr>` +
    list
      .map(
        (p) => `<tr class="link" data-id="${esc(p.id)}"><td>${esc(p.name)}</td><td>${esc(p.number)}</td>
        <td>${p.sections}</td><td>${p.elements}</td><td>${esc(p.updated_at.replace("T", " ").slice(0, 16))}</td></tr>`
      )
      .join("") +
    `</table>`;
  el.querySelectorAll("tr.link").forEach((tr) => (tr.onclick = () => (location.hash = `#/project/${tr.dataset.id}/info`)));
}

// ---------------------------------------------------------------- project page
let state = null; // { project, sectionId, dirty, errors }

// Elements, workbook, load multipliers and design results belong to one section of the project.
const SECTION_TABS = new Set(["elements", "workbook", "design"]);
const sec = () => state.project.sections.find((s) => s.id === state.sectionId) || state.project.sections[0];
const secIndex = () => state.project.sections.indexOf(sec());
const secUrl = () => `/api/projects/${state.project.id}/sections/${sec().id}`;
const tabHash = (tab) => `#/project/${state.project.id}/${tab}` + (SECTION_TABS.has(tab) ? `/${sec().id}` : "");

async function projectPage(id, tab, sectionId) {
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
  if (sectionId && p.sections.some((s) => s.id === sectionId)) state.sectionId = sectionId;
  if (!p.sections.some((s) => s.id === state.sectionId)) state.sectionId = p.sections[0].id;
  const tabs = [
    ["info", "Project"],
    ["settings", "Design settings"],
    ["sections", `Sections (${p.sections.length})`],
    ["elements", `Elements (${Object.keys(sec().elements).length})`],
    ["workbook", "Workbook"],
    ["design", "Design"],
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
    ${picker}<div id="tab"></div>
    <div class="savebar"><button id="save">Save</button><span class="status" id="save-status"></span>
      <span style="flex:1"></span><button class="danger" id="delete">Delete project</button></div>
    <ul class="errors" id="errors"></ul>`;
  $app.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => (location.hash = tabHash(b.dataset.tab))));
  const pick = document.getElementById("section-pick");
  if (pick)
    pick.onchange = () => {
      state.sectionId = pick.value;
      location.hash = tabHash(tab);
    };
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
  else if (tab === "sections") renderSections(host);
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
    const inherit = nullable ? `<option value="" ${value == null ? "selected" : ""}>${esc(inheritLabel(key, path))}</option>` : "";
    control = `<select>${inherit}${options
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
    if (options) v = v === "" && nullable ? null : options.find((o) => String(o) === v);
    else if (inner.type === "number" || inner.type === "integer") v = v === "" ? (nullable ? null : v) : Number(v);
    obj[key] = v;
    markDirty();
  };
  return f;
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
  const map = { crack_only: "Crack width only", structural: "Structural (shares load)", min_steel: "Least steel",
    lap: "Lapped", raw: "Raw values", average: "Average with neighbours", unified: "Unified", zoned: "Zoned", coupler: "Couplers", least_steel: "Least steel", standard_lengths: "Standard cut lengths",
    min_cost: "Lowest cost", uniform: "Uniform slab", column_and_field: "Column and field strips" };
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
    Plaxis workbook, elements, load multipliers and results. Materials and design settings are shared.</p>
    <div class="panel row" style="margin-bottom:16px">
      <input id="sec-name" placeholder="Section name, e.g. Section 02" style="flex:1;max-width:280px;padding:7px 9px;border:1px solid var(--line);border-radius:7px;background:var(--input);color:var(--text);font:inherit">
      <button id="sec-add" class="quiet">Add section</button><span class="status" id="sec-status"></span></div>`;
  document.getElementById("sec-add").onclick = async () => {
    const name = document.getElementById("sec-name").value.trim();
    if (!name) return;
    if (state.dirty) await save();
    if (state.errors?.length) return;
    try {
      state.project = await api(`/api/projects/${p.id}/sections`, { method: "POST", body: JSON.stringify({ name }) });
      state.sectionId = state.project.sections.at(-1).id;
      route();
    } catch (e) {
      document.getElementById("sec-status").textContent = e.message;
    }
  };
  p.sections.forEach((s, i) => {
    const card = document.createElement("div");
    card.className = "panel";
    card.style.marginBottom = "16px";
    const n = Object.keys(s.elements).length;
    card.innerHTML = `<div class="element-head"><h3>${esc(s.name)}<span class="type">${n} element${n === 1 ? "" : "s"}</span></h3>
      <span><button class="quiet" data-open>Open</button> <button class="danger" data-remove ${p.sections.length > 1 ? "" : "disabled"}>Remove</button></span></div>`;
    card.querySelector("[data-open]").onclick = () => {
      state.sectionId = s.id;
      location.hash = tabHash("elements");
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
    host.append(card);
  });
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
      <div id="factors"></div>
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
  const url = secUrl();
  host.innerHTML = `<p class="sub" id="wb-note">Upload the Plaxis workbook for ${esc(sec().name)}. It is checked, then kept with
    the section so its elements can be designed without uploading it again.</p>` + checkerHtml();
  const onReport = (data) => {
    document.getElementById("wb-note").textContent =
      `Workbook in use: ${data.file}, uploaded ${String(data.uploaded_at || "").replace("T", " ").slice(0, 16)}. Upload again to replace it.`;
    renderFactors(data);
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
  wireChecker(onReport, `${url}/workbook`);
  try {
    const stored = await api(`${url}/workbook`);
    renderReport(stored);
    onReport(stored);
  } catch {
    /* no workbook yet */
  }
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
  const draw = () => {
    const taken = (i) => new Set(p.load_factors.flatMap((r, j) => (j === i ? [] : r.sheets)));
    box.innerHTML = `<h2>Load multipliers</h2>
      <p class="status" style="margin-top:0">Multiply the straining actions of chosen sheets, e.g. 1.35 on the Set B sheets. X, Y and Z are not changed. Save to keep.</p>
      ${p.load_factors
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
      <button class="quiet" id="add-factor">Add multiplier</button>`;
    box.querySelectorAll("details[data-rule]").forEach((d) => (d.ontoggle = () => {
      const i = Number(d.dataset.rule);
      if (d.open) open.add(i);
      else open.delete(i);
    }));
    box.querySelector("#add-factor").onclick = () => {
      p.load_factors.push({ factor: 1.35, sheets: [], note: "" });
      markDirty();
      draw();
    };
    box.querySelectorAll("[data-remove]").forEach((b) => (b.onclick = () => {
      p.load_factors.splice(Number(b.dataset.remove), 1);
      markDirty();
      draw();
    }));
    box.querySelectorAll("input[data-key]").forEach((inp) => (inp.oninput = () => {
      const r = p.load_factors[Number(inp.dataset.rule)];
      r[inp.dataset.key] = inp.dataset.key === "factor" ? (inp.value === "" ? inp.value : Number(inp.value)) : inp.value;
      markDirty();
    }));
    box.querySelectorAll("input[data-combo]").forEach((inp) => (inp.onchange = () => {
      const i = Number(inp.dataset.rule);
      const r = p.load_factors[i];
      const names = byCombo(inp.dataset.combo).filter((n) => !taken(i).has(n));
      r.sheets = inp.checked ? [...new Set([...r.sheets, ...names])] : r.sheets.filter((n) => !names.includes(n));
      markDirty();
      draw();
    }));
    box.querySelectorAll("input[data-sheet]").forEach((inp) => (inp.onchange = () => {
      const r = p.load_factors[Number(inp.dataset.rule)];
      r.sheets = inp.checked ? [...r.sheets, inp.dataset.sheet] : r.sheets.filter((n) => n !== inp.dataset.sheet);
      markDirty();
      draw();
    }));
  };
  draw();
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
  const url = secUrl();
  const els = Object.entries(sec().elements).filter(([, e]) => e.kind === "pile" || e.kind === "combi_wall");
  host.innerHTML = `<div class="panel row">
      <button id="run-design" ${els.length ? "" : "disabled"}>Design piles and combi wall</button>
      <a class="quiet-link" id="cages" href="${url}/design/cages.json" hidden>Download cages for Revit (JSON)</a>
      <a class="quiet-link" id="sets" href="${url}/design/governing.xlsx" hidden>Download governing sets (Excel)</a>
      ${Object.values(sec().elements).some((e) => e.kind === "sheet_pile_wall") ? `<a class="quiet-link" href="${url}/spw.xlsx">Download SPW straining actions (Excel)</a>` : ""}
      <span class="status" id="design-status">${els.length ? esc(els.map(([n]) => n).join(", ")) : "Add pile or combi wall elements first."}</span>
    </div><div id="design-out"></div>`;
  document.getElementById("run-design").onclick = async () => {
    if (state.dirty) await save();
    if (state.errors?.length) return;
    const status = document.getElementById("design-status");
    status.textContent = "Designing…";
    try {
      renderResults(await api(`${url}/design`, { method: "POST" }));
      status.textContent = "Done.";
    } catch (e) {
      status.textContent = e.message;
    }
  };
  try {
    renderResults(await api(`${url}/design`));
  } catch {
    /* not designed yet */
  }
}

const fmt = (v, d = 0) => (v == null || !isFinite(v) ? "–" : Number(v).toLocaleString("en-GB", { maximumFractionDigits: d, minimumFractionDigits: d }));

function renderResults(res) {
  const out = document.getElementById("design-out");
  if (!out) return;
  const walls = res.combi_walls || [];
  const link = document.getElementById("cages");
  if (link) link.hidden = !res.piles.length && !walls.length;
  const sets = document.getElementById("sets");
  if (sets) sets.hidden = !res.piles.length && !walls.length;
  const rows = res.piles
    .map((p) => {
      const a = p.arrangement;
      const sh = p.shear;
      const kg = p.steel?.kg_per_m3 ?? p.curtailment?.steel_ratio_kg_m3 ?? p.steel_ratio_kg_m3;
      return `<tr><td>${esc(p.element)}</td><td>${a ? esc(a.label) : "–"}</td>
        <td class="cell ${p.passed ? "ok" : "error"}">${fmt(p.utilisation, 2)}</td>
        <td>${sh ? esc(sh.zones[0].link) : "–"}</td>
        <td class="cell ${sh ? (sh.passed ? "ok" : "error") : ""}">${sh ? fmt(sh.utilisation, 2) : "–"}</td>
        <td>${fmt(p.reinforcement_ratio_pct, 2)}%</td><td>${fmt(kg)}</td></tr>`;
    })
    .join("");
  out.innerHTML = `<p class="status">Designed ${esc(res.run_at.replace("T", " ").slice(0, 16))}. Crack width comes next.</p>
    ${res.skipped.map((s) => `<p class="status">${esc(s)}</p>`).join("")}
    ${res.piles.length ? `<h2>Piles</h2><div class="panel scroll"><table><tr><th>Element</th><th>Bars at head</th><th>N–M</th><th>Links at head</th><th>Shear</th><th>ρ at head</th><th>kg/m³ incl. links</th></tr>${rows}</table></div>` : ""}
    <div id="pile-cards"></div><div id="combi-cards"></div>`;
  const cards = document.getElementById("pile-cards");
  for (const p of res.piles) cards.append(pileCard(p));
  const combi = document.getElementById("combi-cards");
  if (walls.length) combi.insertAdjacentHTML("beforeend", "<h2>Combi wall</h2>");
  for (const w of walls) combi.append(combiCard(w));
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
    ${g.combination ? `<p>Governing: ${esc(g.combination)}, z ${fmt(g.z, 2)} m (${esc(g.zone)}), N = ${fmt(g.N_kN)} kN (Plaxis sign), M = ${fmt(g.M_kNm)} kNm, V = ${fmt(g.V_kN)} kN. Check: ${esc(g.check)}.</p>` : ""}
    ${(t.notes || []).map((n) => `<p class="status">${esc(n)}</p>`).join("")}
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
      <div class="count"><b>${fmt(p.steel?.kg_per_m3 ?? p.curtailment?.steel_ratio_kg_m3 ?? p.steel_ratio_kg_m3)}</b>${p.steel ? `kg/m³ over the pile (${fmt(p.steel.longitudinal_kg)} kg bars with laps + ${fmt(p.steel.links_kg)} kg links)` : "kg/m³ longitudinal"}</div>
      <div class="count"><b>${a ? fmt(a.clear_spacing_mm) : "–"} mm</b>clear spacing, outer row (allowed ${fmt(lim.min_clear_mm)} to ${fmt(lim.max_clear_mm)} mm)</div>
      ${p.steel?.element_total_t != null ? `<div class="count"><b>${fmt(p.steel.element_total_t, 1)} t</b>steel for ${p.count} pile${p.count === 1 ? "" : "s"} of this type</div>` : ""}
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
    ${p.shear ? shearBlock(p.shear, p.head_name || "the slab") : ""}
    <div class="charts"><div class="chart" data-kind="nm"></div><div class="chart" data-kind="profile"></div></div>
    ${p.moments?.length ? `<div class="charts"><div class="chart" data-kind="moments"></div><div data-kind="peaks"></div></div>` : ""}
    ${setsBlock(p.governing_sets)}
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
  if (p.moments?.length) {
    momentChart(card.querySelector('[data-kind="moments"]'), p);
    peaksBlock(card.querySelector('[data-kind="peaks"]'), p.peaks || []);
  }
  return card;
}

function momentChart(el, p) {
  // Largest resultant moment at each level (ULS), with isolated peaks marked.
  const prof = p.moments;
  const peaks = (p.peaks || []).filter((q) => q.combination && !/QP/.test(q.combination));
  const zs = prof.map((q) => q.z).concat(peaks.map((q) => q.z));
  const maxM = Math.max(1, ...prof.map((q) => q.M_kNm), ...peaks.map((q) => q.M_kNm)) * 1.05;
  const c = frame(el, {
    xDomain: [0, maxM], yDomain: [Math.min(...zs), Math.max(...zs)],
    xLabel: "M (kNm)", yLabel: "Level z (m)", title: "Moment along the pile (ULS envelope)",
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

function setsBlock(stations) {
  // The seven governing ULS and QP sets per station, as entered in AdSec.
  if (!stations?.length) return "";
  const rows = stations
    .map((st) =>
      [["ULS", st.uls], ["QP", st.qp]]
        .map(([ls, list], k) =>
          list
            .map((r, i) => `<tr${i === 0 && k === 0 ? ' class="group"' : ""}>
              <td>${i === 0 && k === 0 ? `${fmt(st.top, 2)} to ${fmt(st.bottom, 2)}<br><span class="status">${esc(st.cage)}</span>` : ""}</td>
              <td>${i === 0 ? ls : ""}</td><td>${esc(r.case)}</td><td>${esc(r.combination)}</td><td>${r.node ?? "–"}</td>
              <td>${fmt(r.z, 2)}</td><td>${fmt(r.N_kN)}</td><td>${fmt(r.M2_kNm)}</td><td>${fmt(r.M3_kNm)}</td><td>${r.utilisation == null ? "–" : fmt(r.utilisation, 3)}</td></tr>`)
            .join("")
        )
        .join("")
    )
    .join("");
  return `<details style="margin-top:12px"><summary>Governing sets per station for AdSec (${stations.length} station${stations.length === 1 ? "" : "s"}, 7 ULS + 7 QP each)</summary>
    <p class="status">N in the concrete sign convention (Plaxis N × −1, compression +). M2 and M3 as in Plaxis. For QP the 7th set is the largest resultant moment until crack width is checked.</p>
    <div class="scroll"><table class="sets"><tr><th>Station (m)</th><th>Limit state</th><th>Case</th><th>Combination</th><th>Node</th><th>z (m)</th><th>N kN</th><th>M2 kNm</th><th>M3 kNm</th><th>N–M util.</th></tr>${rows}</table></div></details>`;
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
    <p class="status">${esc(sh.method)}. Largest spacing ${fmt(sh.max_spacing_mm)} mm, smallest link Ø${fmt(sh.min_link_diameter_mm)} (9.5.3). ${fmt(sh.links_kg)} kg of links per pile.</p>
    ${sh.notes.map((n) => `<p class="status">${esc(n)}</p>`).join("")}`;
}

function curtailmentBlock(c) {
  const mode = c.mode === "standard_lengths" ? "standard cut lengths" : "least steel";
  const joint = { lap: "lap", coupler: "coupler", toe: "toe" };
  const saving = c.unified_weight_kg ? Math.round((1 - c.weight_kg / c.unified_weight_kg) * 100) : null;
  return `<h3 style="margin:20px 0 4px;font-size:15px">Reinforcement down the pile</h3>
    <p class="status" style="margin:0 0 8px">Zones chosen for ${mode}. Main bars: ${fmt(c.weight_kg)} kg per pile, ${fmt(c.steel_ratio_kg_m3)} kg/m³${saving != null ? `, against ${fmt(c.unified_steel_ratio_kg_m3)} kg/m³ with the head cage all the way down (${saving}% less)` : ""}.${c.couplers ? ` ${c.couplers} couplers.` : ""}</p>
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
