// Triton front end: projects, schema-driven setup forms and the workbook check.
import { View3D, directionArrows, heat, legendHtml } from "./view3d.js";

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
const SECTION_TABS = new Set(["elements", "workbook", "design", "view3d"]);
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
    ["view3d", "3D view"],
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
  else if (tab === "view3d") renderView3dTab(host);
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
          .map(([k]) => `<td><input type="number" step="any" data-i="${i}" data-k="${esc(k)}" value="${r[k] ?? ""}"></td>`)
          .join("")}<td><button type="button" class="quiet" data-del="${i}">Remove</button></td></tr>`)
        .join("")}</table></div><button type="button" class="quiet" data-add style="margin-top:6px">Add a row</button>`;
      f.querySelectorAll("input[data-i]").forEach((inp) => (inp.oninput = () => {
        const v = inp.value === "" ? null : Number(inp.value);
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
  const res = await api(`/api/durability-defaults?${q}`);
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
  const map = { crack_only: "Crack width only", structural: "Structural (shares load)", min_steel: "Least steel",
    lap: "Lapped", raw: "Raw values", average: "Average with neighbours", unified: "Unified", zoned: "Zoned", coupler: "Couplers", least_steel: "Least steel", standard_lengths: "Standard cut lengths",
    min_cost: "Lowest cost", en1992: "EN 1992-1-1 (Table 4.4N)", en1993_5: "EN 1993-5 (Table 4.2)", bs6349: "BS 6349-1-4 (maritime)", uniform: "Uniform slab", column_and_field: "Column and field strips" };
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
      <div id="axes-block" hidden><h2>Directions of the actions</h2>
        <p class="status">Worked out from the results (shears against the moments' change, forces against depth). Confirm them against the Plaxis model.</p>
        <div class="panel scroll"><table id="axes"></table></div></div>
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
  const axes = d.axes || [];
  document.getElementById("axes-block").hidden = !axes.length;
  document.getElementById("axes").innerHTML = "<tr><th>Element</th><th></th><th>Finding</th></tr>" + axes
    .map((a) => `<tr><th>${esc(a.element)}</th><td><span class="sev ${a.clear ? "ok" : "warning"}">${a.clear ? "clear" : "unclear"}</span></td><td>${esc(a.text)}</td></tr>`)
    .join("");
  document.getElementById("report").hidden = false;
}

// ---------------------------------------------------------------- design tab
const DESIGNED = new Set(["pile", "combi_wall", "front_beam", "rear_beam", "transverse_beam", "slab"]);

async function renderDesignTab(host) {
  const url = secUrl();
  const els = Object.entries(sec().elements).filter(([, e]) => DESIGNED.has(e.kind));
  host.innerHTML = `<div class="panel row">
      <button id="run-design" ${els.length ? "" : "disabled"}>Design the elements</button>
      <a class="quiet-link" id="cages" href="${url}/design/cages.json" hidden>Download cages for Revit (JSON)</a>
      <a class="quiet-link" id="sets" href="${url}/design/governing.xlsx" hidden>Download governing sets for AdSec (Excel)</a>
      <a class="quiet-link" id="ads" href="${url}/design/adsec.zip" hidden>Download AdSec files (.ads per pile part)</a>
      <span class="reports" id="reports" hidden>Report:
        <select id="report-detail"><option value="summary">Summary</option><option value="detailed">Detailed</option></select>
        <a class="quiet-link" data-fmt="docx" href="#">Word</a>
        <a class="quiet-link" data-fmt="pdf" href="#">PDF</a>
        <a class="quiet-link" data-fmt="xlsx" href="#">Excel</a>
      </span>
      ${Object.values(sec().elements).some((e) => e.kind === "sheet_pile_wall") ? `<a class="quiet-link" href="${url}/spw.xlsx">Download SPW straining actions (Excel)</a>` : ""}
      <span class="status" id="design-status">${els.length ? esc(els.map(([n]) => n).join(", ")) : "Add pile, combi wall, beam or slab elements first."}</span>
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

const fmt = (v, d = 0) => (v == null || !isFinite(v) ? "–" : (Math.abs(v) < 0.5 * 10 ** -d ? 0 : Number(v)).toLocaleString("en-GB", { maximumFractionDigits: d, minimumFractionDigits: d }));

function renderResults(res) {
  const out = document.getElementById("design-out");
  if (!out) return;
  const walls = res.combi_walls || [];
  const spws = res.sheet_pile_walls || [];
  const beams = res.beams || [];
  const slabs = res.slabs || [];
  const link = document.getElementById("cages");
  if (link) link.hidden = !res.piles.length && !walls.length;
  const ads = document.getElementById("ads");
  if (ads) ads.hidden = !res.piles.length && !walls.length;
  const reports = document.getElementById("reports");
  if (reports) {
    reports.hidden = false;
    const detail = document.getElementById("report-detail");
    const setLinks = () =>
      reports.querySelectorAll("a[data-fmt]").forEach((a) => {
        a.href = `${secUrl()}/design/report.${a.dataset.fmt}?detail=${detail.value}`;
      });
    detail.onchange = setLinks;
    setLinks();
  }
  const sets = document.getElementById("sets");
  if (sets) sets.hidden = !res.piles.length && !walls.length && !spws.length && !beams.length;
  const rows = res.piles
    .map((p) => {
      const a = p.arrangement;
      const sh = p.shear;
      const kg = p.steel?.kg_per_m3 ?? p.curtailment?.steel_ratio_kg_m3 ?? p.steel_ratio_kg_m3;
      return `<tr><td>${esc(p.element)}</td><td>${a ? esc(a.label) : "–"}</td>
        <td class="cell ${p.passed ? "ok" : "error"}">${fmt(p.utilisation, 2)}</td>
        <td>${sh ? esc(sh.zones[0].link) : "–"}</td>
        <td class="cell ${sh ? (sh.passed ? "ok" : "error") : ""}">${sh ? fmt(sh.utilisation, 2) : "–"}</td>
        <td class="cell ${p.cracks?.wk_mm == null ? "" : p.cracks.passed ? "ok" : "error"}">${p.cracks?.wk_mm == null ? "–" : `${fmt(p.cracks.wk_mm, 2)} / ${fmt(p.cracks.limit_mm, 2)}`}</td>
        <td>${fmt(p.reinforcement_ratio_pct, 2)}%</td><td>${fmt(kg)}</td></tr>`;
    })
    .join("");
  out.innerHTML = `<p class="status">Designed ${esc(res.run_at.replace("T", " ").slice(0, 16))}.</p>
    ${res.skipped.map((s) => `<p class="status">${esc(s)}</p>`).join("")}
    ${res.piles.length ? `<h2>Piles</h2><div class="panel scroll"><table><tr><th>Element</th><th>Bars at head</th><th>N–M</th><th>Links at head</th><th>Shear</th><th>Crack mm</th><th>ρ at head</th><th>kg/m³ incl. links</th></tr>${rows}</table></div>` : ""}
    <div id="pile-cards"></div><div id="combi-cards"></div>
    ${beams.length ? `<h2>Beams</h2><div class="panel scroll"><table><tr><th>Element</th><th>b × h</th><th>Longitudinal bars</th><th>Links</th><th>Transverse bars (top / bottom)</th><th>Max util.</th><th>kg/m³</th></tr>
      ${beams.map((b) => `<tr><td>${esc(b.element)}</td><td>${fmt(b.width_mm)} × ${fmt(b.depth_mm)}</td><td>${b.cage ? esc(b.cage.label) : "–"}</td>
        <td>${b.shear?.link ? esc(b.shear.link.label) : "–"}</td><td>${b.transverse ? `${esc(b.transverse.top.label)} / ${esc(b.transverse.bottom.label)}` : "–"}</td>
        <td class="cell ${b.passed ? "ok" : "error"}">${fmt(b.utilisation, 2)}</td><td>${fmt(b.steel?.kg_per_m3)}</td></tr>`).join("")}</table></div>` : ""}
    <div id="beam-cards"></div>
    ${slabs.length ? "<h2>Slab</h2>" : ""}<div id="slab-cards"></div>
    ${spws.length ? `<h2>Sheet pile wall</h2>${spws.map((w) => `<div class="panel"><h3>${esc(w.element)}</h3><p class="status">Designed in the sheet pile program; these are its straining actions.</p>${steelSetsBlock(w.governing_sets, "kN/m, kNm/m", true)}</div>`).join("")}` : ""}`;
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
    <div class="v3d-slot" data-element="${esc(name)}"></div>${legendHtml()}</details>`;
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

function resultBands(res) {
  const bands = {};
  for (const p of res?.piles || []) bands[p.element] = p.bands || [];
  for (const w of res?.combi_walls || []) bands[w.element] = w.bands || [];
  for (const b of res?.beams || []) bands[b.element] = b.bands || [];
  for (const d of res?.slabs || []) bands[d.element] = d.bands || [];
  return bands;
}

async function mountElementViews(res) {
  const slots = [...document.querySelectorAll(".v3d-slot")];
  if (!slots.length) return;
  const geo = await sectionGeometry();
  if (!geo) {
    slots.forEach((s) => (s.innerHTML = '<p class="status">Upload the workbook to see the 3D view.</p>'));
    return;
  }
  const bands = resultBands(res);
  for (const slot of slots) {
    const name = slot.dataset.element;
    const el = geo.elements.find((e) => e.element === name);
    const view = new View3D(slot, { height: 380, compact: true });
    view.setScene({ elements: geo.elements, bands, selected: name, focus: name,
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
  }
  for (const w of res.combi_walls || []) {
    const u = w.infill?.utilisation;
    if (u > 1) add("unsafe", w.element, `infill N–M utilisation ${fmt(u, 2)}${at(w.infill.governing)}`);
    else if (u >= 0.95) add("limit", w.element, `infill N–M utilisation ${fmt(u, 2)}${at(w.infill.governing)}: close to the limit`);
    const t = w.tube?.utilisation;
    if (t > 1) add("unsafe", w.element, `steel tube utilisation ${fmt(t, 2)} (${esc(w.tube.governing?.check || "")})`);
    else if (t >= 0.95) add("limit", w.element, `steel tube utilisation ${fmt(t, 2)}: close to the limit`);
  }
  for (const b of res.beams || []) {
    const at2 = (g) => (g?.combination ? ` (${g.combination}, at ${fmt(g.s, 1)} m)` : "");
    const checks = [
      ["bending", b.bending?.utilisation, b.bending?.governing],
      ["shear and torsion", b.shear?.utilisation, b.shear?.governing],
      ["transverse bending", b.transverse?.utilisation, b.transverse?.governing],
    ];
    for (const [f, c] of Object.entries(b.cracks || {})) checks.push([`${f} crack width ${fmt(c.wk, 2)} mm of ${fmt(c.limit, 2)}`, c.wk / c.limit, c]);
    for (const [f, c] of Object.entries(b.restraint?.faces || {})) {
      if (isFinite(c.wk)) checks.push([`${f} restraint crack ${fmt(c.wk, 2)} mm of ${fmt(c.limit, 2)}`, c.wk / c.limit, null]);
    }
    if (b.utilisation == null) add("unsafe", b.element, "no reinforcement passes");
    for (const [what, u, g] of checks) {
      if (u == null) continue;
      if (u > 1) add("unsafe", b.element, `${what}: ${fmt(u, 2)}${at2(g)}`);
      else if (u >= 0.95) add("limit", b.element, `${what}: ${fmt(u, 2)}${at2(g)}, close to the limit`);
    }
    if (b.utilisation != null && b.utilisation < 0.5) add("safe", b.element, `max utilisation ${fmt(b.utilisation, 2)}: very safe`);
  }
  for (const d of res.slabs || []) {
    for (const [k, l] of Object.entries(d.layers || {})) {
      if (l.utilisation > 1) add("unsafe", d.element, `${k.replace("_", " bars along ")}: ${fmt(l.utilisation, 2)} of the steel needed`);
    }
    for (const q of d.punching || []) {
      if (!q.passed) add("unsafe", d.element, `punching at ${q.pile} (X ${fmt(q.x, 1)}, Y ${fmt(q.y, 1)}): crushes at the pile face`);
      else if (q.needs_reinforcement) add("limit", d.element, `punching links needed at ${q.pile} (X ${fmt(q.x, 1)}, Y ${fmt(q.y, 1)}), ${q.perimeters} perimeters`);
    }
    if (d.shear && d.shear.passed === false) add("unsafe", d.element, `shear per metre ${fmt(d.shear.utilisation, 2)}`);
    for (const [k, r] of Object.entries(d.restraint?.layers || {})) {
      if (!r.passed) add("unsafe", d.element, `restraint crack ${k.replace("_", " ")} ${fmt(r.wk, 2)} mm of ${fmt(r.limit, 2)}`);
    }
  }
  const rank = { unsafe: 0, limit: 1, safe: 2 };
  return out.sort((a, b) => rank[a.level] - rank[b.level]);
}

async function renderView3dTab(host) {
  host.innerHTML = `<div class="v3d-layout"><div><div class="panel" id="v3d-main"></div>${legendHtml()}</div>
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
  const max = {};
  for (const p of res?.piles || []) max[p.element] = p.utilisation;
  for (const w of res?.combi_walls || []) max[w.element] = w.utilisation;
  for (const b of res?.beams || []) max[b.element] = b.utilisation;
  for (const d of res?.slabs || []) max[d.element] = d.utilisation;
  let selected = null;
  const show = () => {
    const el = geo.elements.find((e) => e.element === selected);
    view.setScene({ elements: geo.elements, bands, selected,
      arrows: selected ? directionArrows(el, geo.axes.find((a) => a.element === selected)) : [] });
    side.querySelectorAll("[data-pick]").forEach((b) => b.classList.toggle("on", b.dataset.pick === selected));
  };
  const side = document.getElementById("v3d-side");
  const list = alerts(res || {});
  const label = { unsafe: "Unsafe", limit: "Check", safe: "Very safe" };
  const sevClass = { unsafe: "error", limit: "warning", safe: "ok" };
  side.innerHTML = `<h3 style="margin-top:0">Alerts</h3>
    ${res ? "" : '<p class="status">Not designed yet: run the design on the Design tab to colour the elements.</p>'}
    ${list.length ? `<ul class="alerts">${list.map((a) => `<li><span class="sev ${sevClass[a.level]}">${label[a.level]}</span> <b>${esc(a.name)}</b>: ${esc(a.text)}</li>`).join("")}</ul>` : res ? '<p class="status">Nothing unsafe or close to the limit.</p>' : ""}
    <h3>Elements</h3><p class="status">Pick one to see it alone with the directions of its actions.</p>
    <div class="v3d-picks">${geo.elements.map((e) => `<button class="quiet" data-pick="${esc(e.element)}">
      <i style="background:${heat(max[e.element])}"></i>${esc(e.element)}<span>${max[e.element] == null ? "not designed" : fmt(max[e.element], 2)}</span></button>`).join("")}</div>`;
  side.querySelectorAll("[data-pick]").forEach((b) => (b.onclick = () => {
    selected = selected === b.dataset.pick ? null : b.dataset.pick;
    show();
  }));
  show();
}

// ---------------------------------------------------------------- Slabs
const LAYER_NAME = { bottom_x: "Bottom, bars along X", bottom_y: "Bottom, bars along Y", top_x: "Top, bars along X", top_y: "Top, bars along Y" };

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
  card.innerHTML = `<div class="element-head"><h3>${esc(d.element)}<span class="type">Slab, ${fmt(d.thickness_mm)} mm, ${esc(d.concrete || "")}, covers ${fmt(d.cover_top_mm)} top / ${fmt(d.cover_bottom_mm)} bottom, ${d.strips === "column_and_field" ? "column and field strips" : "uniform"}</span></h3>${ok(d.passed)}</div>
    <div class="counts" style="margin-top:0">
      <div class="count"><b>${fmt(d.utilisation, 2)}</b>max utilisation</div>
      <div class="count"><b>${fmt(st.kg_per_m3)}</b>kg/m³ (${fmt(st.kg_per_m2, 1)} kg/m², links not included)</div>
      <div class="count"><b>${fmt(st.total_t, 1)} t</b>bars over ${fmt(st.area_m2)} m²</div>
      <div class="count"><b>${needs.length} of ${punch.length}</b>piles need punching links</div>
      <div class="count"><b>${sh.cells_needing_links ?? 0}</b>${fmt(d.zone_size_m, 1)} m cells need shear links</div>
    </div>
    ${(d.notes || []).map((n) => `<p class="status">${esc(n)}</p>`).join("")}
    ${v3dSlot(d.element)}
    <h3 style="margin-top:18px">Bars per metre</h3>
    <div class="scroll"><table><tr><th>Layer</th><th>Mesh</th><th>Additional bars (between the mesh bars)</th><th>Utilisation</th><th>Set by cracking</th><th>d</th></tr>
      ${Object.entries(layers).map(([k, l]) => `<tr><td>${esc(LAYER_NAME[k] || k)}</td><td><b>${esc(l.basic.label)}</b> (${fmt(l.basic.as_mm2_per_m)} mm²/m)${l.basic.set_by === "user" ? "<br><span class=\"status\">your mesh</span>" : ""}</td>
        <td>${l.mode === "mesh_only" ? "mesh only (your choice)" : l.zones.length ? `${l.zones.length} zones: ${esc([...new Set(l.zones.map((z) => z.label))].join(", "))}` : "none needed"}</td>
        <td class="cell ${l.utilisation <= 1 ? "ok" : "error"}">${fmt(l.utilisation, 2)}</td><td>${fmt(l.cells_set_by_cracks)} cells</td><td>${fmt(l.d_mm)} mm</td></tr>`).join("")}
    </table></div>
    <div class="row" style="margin:10px 0 4px">${Object.keys(layers).map((k, i) => `<button class="quiet${i ? "" : " on"}" data-layer="${k}">${esc(LAYER_NAME[k] || k)}</button>`).join("")}</div>
    <div class="chart wide" data-kind="plan"></div>
    <h3 style="margin-top:18px">Punching at the piles</h3>
    ${punch.length ? `<p class="status">Click a pile to see its control perimeters. Change a pile's thickness for a slope, then save and design again.</p>
      <div class="scroll"><table class="punch"><tr><th>Pile</th><th>X, Y</th><th>Thickness</th><th>V<sub>Ed</sub></th><th>β</th><th>v<sub>Ed</sub> / v<sub>Rd,c</sub> (MPa)</th><th>At the face / v<sub>Rd,max</sub></th><th>Links</th><th></th></tr>
      ${punch.map((q, i) => `<tr class="link" data-punch="${i}"><td>${esc(q.pile)}</td><td>${fmt(q.x, 1)}, ${fmt(q.y, 1)}</td>
        <td><input type="number" step="any" data-depth="${i}" value="${q.thickness_mm}" style="width:80px" title="${esc(q.thickness_from)}"> mm</td><td>${fmt(q.V_kN)} kN, ${esc(q.direction)}<br><span class="status">${esc(q.combination)}</span></td><td>${fmt(q.beta, 2)}</td>
        <td>${fmt(q.vEd_MPa, 3)} / ${fmt(q.vRd_c_MPa, 3)}</td><td>${fmt(q.vEd_face_MPa, 2)} / ${fmt(q.vRd_max_MPa, 2)}</td>
        <td>${q.needs_reinforcement ? (q.perimeters ? `${q.perimeters} perimeters @ ${fmt(q.radial_spacing_mm)} mm, ${fmt(q.asw_mm2_per_perimeter)} mm² each, to ${fmt(q.reinforced_to_mm)} mm from the face` : "–") : "none"}</td><td>${ok(q.passed)}</td></tr>`).join("")}
    </table></div><div class="charts" data-kind="punch"></div>
    <p class="status">EN 1992-1-1 6.4: checked from the pile face (u0, v<sub>Rd,max</sub>) out to u1 at 2d, u1 = π(D + 4d); nothing inside the pile. β = 1 + 0.6π·e/(D + 4d) with the pile moment at the slab soffit, as in the pile design; ρl of the face in tension over the pile. One-way shear starts at 2d from the pile faces. Piles under a beam are left to the beam.</p>` : '<p class="status">No piles under the slab.</p>'}
    <h3 style="margin-top:18px">Shear per metre ${ok(sh.passed !== false)}</h3>
    <p>${sh.governing ? `Largest v − V<sub>Rd,c</sub>: ${esc(sh.governing.combination)} at X ${fmt(sh.governing.x, 1)}, Y ${fmt(sh.governing.y, 1)}: v = ${fmt(sh.governing.V_kN_per_m)} kN/m, V<sub>Rd,c</sub> = ${fmt(sh.governing.VRd_c_kN_per_m)} kN/m, V<sub>Rd,max</sub> = ${fmt(sh.governing.VRd_max_kN_per_m)} kN/m.` : ""}
      ${sh.heaviest ? ` Links in ${sh.cells_needing_links} cells, heaviest ${esc(sh.heaviest.label)} (${fmt(sh.heaviest.asw_mm2_per_m2)} mm²/m²).` : " No shear links needed."}</p>
    ${sh.method ? `<p class="status">${esc(sh.method)}.</p>` : ""}
    <h3 style="margin-top:18px">Temperature and shrinkage restraint</h3>
    <div class="scroll"><table><tr><th>Layer</th><th>w<sub>k</sub></th><th>Limit</th><th>Details</th><th></th></tr>
      ${Object.entries(d.restraint?.layers || {}).map(([k, r]) => `<tr><td>${esc(LAYER_NAME[k] || k)}</td><td>${fmt(r.wk, 3)} mm</td><td>${fmt(r.limit, 2)} mm</td><td>ε<sub>r</sub> ${fmt(r.eps_r)} µε, s<sub>r,max</sub> ${fmt(r.sr_max)} mm</td><td>${ok(r.passed)}</td></tr>`).join("")}
    </table></div>
    <p class="status">${fmt(d.restraint?.length_m)} m between joints, R = ${fmt(d.restraint?.R, 2)} (${esc(d.restraint?.R_from || "")}).</p>`;
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
    const list = (el.punching_depths || []).filter((p) => Math.hypot(p.x - q.x, p.y - q.y) > 0.5);
    if (inp.value !== "") list.push({ x: q.x, y: q.y, thickness: Number(inp.value) });
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
  // Plan of one layer: the basic mesh everywhere, zones of heavier bars, piles.
  const l = d.layers[key];
  const [x0, x1] = d.box.X, [y0, y1] = d.box.Y;
  const W = 820, pad = 30;
  const sc = (W - 2 * pad) / (x1 - x0);
  const H = (y1 - y0) * sc + 2 * pad;
  const X = (x) => pad + (x - x0) * sc, Y = (y) => H - pad - (y - y0) * sc;
  const labels = [...new Set(l.zones.map((z) => z.label))].sort((a, b) => l.zones.find((z) => z.label === a).as_mm2_per_m - l.zones.find((z) => z.label === b).as_mm2_per_m);
  const shade = (lab) => `rgba(214,48,39,${0.25 + 0.6 * (labels.indexOf(lab) + 1) / Math.max(labels.length, 1)})`;
  const zones = l.zones.map((z) => `<rect x="${X(z.x[0])}" y="${Y(z.y[1])}" width="${(z.x[1] - z.x[0]) * sc}" height="${(z.y[1] - z.y[0]) * sc}" fill="${shade(z.label)}"><title>Additional ${esc(z.label)} (${fmt(z.additional_mm2_per_m ?? z.as_mm2_per_m)} mm²/m, ${fmt(z.as_mm2_per_m)} mm²/m with the mesh), X ${fmt(z.x[0], 1)} to ${fmt(z.x[1], 1)}, Y ${fmt(z.y[0], 1)} to ${fmt(z.y[1], 1)}</title></rect>`).join("");
  const piles = (d.punching || []).map((q) => `<circle cx="${X(q.x)}" cy="${Y(q.y)}" r="${(q.r_u1_mm / 1000) * sc}" class="${q.needs_reinforcement ? "pp-out" : "pp-u1"}"><title>${esc(q.pile)}: u1 at 2d${q.needs_reinforcement ? ", needs punching links" : ""}</title></circle>
    <circle cx="${X(q.x)}" cy="${Y(q.y)}" r="${(q.D_mm / 2000) * sc}" fill="none" stroke="var(--text)" stroke-width="1.5"><title>${esc(q.pile)}</title></circle>`).join("");
  el.innerHTML = `<div class="chart-title">${esc(LAYER_NAME[key] || key)}: mesh ${esc(l.basic.label)} everywhere${labels.length ? `, plus additional ${esc(labels.join(", "))} in the shaded zones` : ""}</div>
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Slab plan of ${esc(key)}">
      <rect x="${X(x0)}" y="${Y(y1)}" width="${(x1 - x0) * sc}" height="${(y1 - y0) * sc}" fill="var(--miss-bg)" stroke="var(--muted)"/>
      ${zones}${piles}
      <text class="tick" x="${X(x0)}" y="${H - 8}">X ${fmt(x0, 1)}</text><text class="tick" x="${X(x1)}" y="${H - 8}" text-anchor="end">X ${fmt(x1, 1)}</text>
      <text class="tick" x="${pad - 4}" y="${Y(y1) + 4}" text-anchor="end">Y ${fmt(y1, 0)}</text><text class="tick" x="${pad - 4}" y="${Y(y0)}" text-anchor="end">${fmt(y0, 0)}</text>
    </svg>`;
}

// ---------------------------------------------------------------- Beams
const BEAM_KIND = { front_beam: "Front beam", rear_beam: "Rear beam", transverse_beam: "Transverse beam" };

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
  card.innerHTML = `<div class="element-head"><h3>${esc(b.element)}<span class="type">${esc(BEAM_KIND[b.kind] || "Beam")}, ${fmt(b.width_mm)} × ${fmt(b.depth_mm)} mm, ${esc(b.concrete || "")}, cover ${fmt(b.cover_mm)} mm</span></h3>
      ${ok(b.passed)}</div>
    <div class="counts" style="margin-top:0">
      <div class="count"><b>${fmt(b.utilisation, 2)}</b>max utilisation (all checks)</div>
      <div class="count"><b>${fmt(bend.utilisation, 2)}</b>N with biaxial bending</div>
      <div class="count"><b>${fmt(sh.utilisation, 2)}</b>shear and torsion</div>
      <div class="count"><b>${fmt(worstCrack, 2)} mm</b>largest QP crack width</div>
      <div class="count"><b>${fmt(st.kg_per_m3)}</b>kg/m³ (${fmt(st.kg_per_m)} kg/m)</div>
      ${st.element_total_t != null ? `<div class="count"><b>${fmt(st.element_total_t, 1)} t</b>steel over ${fmt(st.length_m, 1)} m</div>` : ""}
    </div>
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
    ${g.combination ? `<p>Governing bending: ${esc(g.combination)} at ${fmt(g.s, 2)} m along the beam. N = ${fmt(g.N_kN)} kN, M<sub>v</sub> = ${fmt(g.Mv_kNm)} kNm (M<sub>Rd</sub> ${fmt(g.MRd_v_kNm)}), M<sub>h</sub> = ${fmt(g.Mh_kNm)} kNm (M<sub>Rd</sub> ${fmt(g.MRd_h_kNm)}), exponent a = ${fmt(g.a, 2)}. ${esc(bend.method || "")}.</p>` : ""}
    <div class="charts"><div class="chart" data-kind="moments"></div><div class="chart" data-kind="profile"></div></div>` : ""}
    <h3 style="margin-top:18px">Crack widths</h3>
    <div class="scroll"><table><tr><th>Check</th><th>Face</th><th>w<sub>k</sub></th><th>Limit</th><th>Details</th><th></th></tr>
      ${Object.entries(b.cracks || {}).map(([f, x]) => `<tr><td>QP loads (7.3.4)</td><td>${f}</td><td>${fmt(x.wk, 3)} mm</td><td>${fmt(x.limit, 2)} mm</td>
        <td>${x.sigma_s > 0 ? `σ<sub>s</sub> ${fmt(x.sigma_s)} MPa, s<sub>r,max</sub> ${fmt(x.sr_max)} mm, ${esc(x.combination)} at ${fmt(x.s, 1)} m` : "no tension at this face under QP loads"}</td><td>${ok(x.passed)}</td></tr>`).join("")}
      ${Object.entries(r.faces || {}).map(([f, x]) => `<tr><td>Restraint</td><td>${f}</td><td>${isFinite(x.wk) ? `${fmt(x.wk, 3)} mm` : "–"}</td><td>${fmt(x.limit, 2)} mm</td>
        <td>${x.note ? esc(x.note) : `ε<sub>r</sub> ${fmt(x.eps_r)} µε, crack strain ${fmt(x.eps_cr)} µε, s<sub>r,max</sub> ${fmt(x.sr_max)} mm`}</td><td>${ok(x.passed)}</td></tr>`).join("")}
    </table></div>
    ${r.faces ? `<p class="status">Restraint: ${fmt(r.length_m)} m between joints, R = ${fmt(r.R, 2)} (${esc(r.R_from)}), K1 = ${fmt(r.K1, 2)}, T1 = ${fmt(r.T1)} °C, T2 = ${fmt(r.T2)} °C, plus autogenous shrinkage (EN 1992-3 Annex M, CIRIA C660).</p>` : ""}
    ${sh.link ? `<h3 style="margin-top:18px">Links ${ok(sh.passed)}</h3>
    <p><b>${esc(sh.link.label)}</b>, ${fmt(sh.link.kg_per_m, 1)} kg/m, one arrangement for the whole beam (largest spacing ${fmt(sh.max_spacing_mm)} mm).
      ${sh.governing ? `Governing: ${esc(sh.governing.combination)} at ${fmt(sh.governing.s, 2)} m, V = ${fmt(sh.governing.V_kN)} kN, T = ${fmt(sh.governing.T_kNm)} kNm, N = ${fmt(sh.governing.N_kN)} kN; V<sub>Rd,c</sub> ${fmt(sh.governing.VRd_c_kN)} kN${sh.governing.N_kN < 0 ? " (tension: no concrete contribution)" : ""}, V<sub>Rd,max</sub> ${fmt(sh.governing.VRd_max_kN)} kN, T<sub>Rd,max</sub> ${fmt(sh.governing.TRd_max_kNm)} kNm, cot θ ${fmt(sh.governing.cot_theta, 2)}.` : ""}</p>
    <p class="status">${esc(sh.method)}. Horizontal shear ${fmt(sh.horizontal?.V_kN)} kN. Torsion needs ${fmt(sh.torsion_long_steel_mm2)} mm² of longitudinal steel around the perimeter.${sh.transverse_shear_needs_links ? " The transverse shear per metre also needs links; they are included." : ""}</p>
    ${(sh.notes || []).map((n) => `<p class="status">${esc(n)}</p>`).join("")}` : ""}
    ${tr.top ? `<h3 style="margin-top:18px">Transverse bars (per metre, across the beam) ${ok(tr.passed)}</h3>
    <p>Top <b>${esc(tr.top.label)}</b> (${fmt(tr.top.as_mm2_per_m)} mm²/m), bottom <b>${esc(tr.bottom.label)}</b> (${fmt(tr.bottom.as_mm2_per_m)} mm²/m). Bending utilisation ${fmt(tr.utilisation, 2)}${tr.governing ? `, governed by ${esc(tr.governing.combination)} at ${fmt(tr.governing.s, 1)} m, M = ${fmt(tr.governing.M_kNm_per_m)} kNm/m with N = ${fmt(tr.governing.N_kN_per_m)} kN/m` : ""}.
      ${Object.entries(tr.cracks || {}).map(([f, x]) => `QP crack at the ${f}: ${fmt(x.wk, 3)} mm of ${fmt(x.limit, 2)}.`).join(" ")}</p>` : ""}
    ${setsBlock(b.governing_sets, "N in the concrete sign convention (compression +). M3 is the vertical bending of the beam section (sagging +), M2 the horizontal bending; z is the position along the beam.")}`;
  if (c?.bars) beamSection(card.querySelector('[data-kind="section"]'), b);
  if (b.profile?.length) {
    beamMoments(card.querySelector('[data-kind="moments"]'), b.profile);
    const prof = { profile: b.profile.map((q) => ({ z: q.s, util: q.u })) };
    alongChart(card.querySelector('[data-kind="profile"]'), prof.profile, "Utilisation along the beam", "Utilisation", (q) => q.util, 1);
  }
  return card;
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

function beamMoments(el, prof) {
  // Vertical bending envelope along the beam (ULS), sagging +.
  const xs = prof.map((q) => q.s);
  const lo = Math.min(0, ...prof.map((q) => q.Mv_min)), hi = Math.max(0, ...prof.map((q) => q.Mv_max));
  const padm = (hi - lo) * 0.05 || 1;
  const c = frame(el, { xDomain: [Math.min(...xs), Math.max(...xs)], yDomain: [lo - padm, hi + padm],
    xLabel: "Position along the beam (m)", yLabel: "M vertical (kNm)", title: "Vertical bending, ULS envelope (sagging +)" });
  // Break the line over the supports, where there are no results.
  const line = (k) => prof.map((q, i) => `${i && q.s - prof[i - 1].s < 0.6 ? "L" : "M"}${c.x(q.s).toFixed(1)},${c.y(q[k]).toFixed(1)}`).join("");
  c.g.innerHTML = `<line class="grid" x1="${c.m.l}" x2="${c.w - c.m.r}" y1="${c.y(0)}" y2="${c.y(0)}"/>
    <path class="series" d="${line("Mv_max")}"/><path class="series" d="${line("Mv_min")}" stroke-dasharray="5 3"/>`;
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

function alongChart(el, rows, title, yLabel, val, limit = null) {
  // A value along the beam (x = position), with an optional limit line.
  const xs = rows.map((q) => q.z);
  const hi = Math.max(limit ?? 0, ...rows.map(val)) * 1.08 || 1;
  const c = frame(el, { xDomain: [Math.min(...xs), Math.max(...xs)], yDomain: [0, hi], xLabel: "Position along the beam (m)", yLabel, title });
  const path = rows.map((q, i) => `${i && q.z - rows[i - 1].z < 0.6 ? "L" : "M"}${c.x(q.z).toFixed(1)},${c.y(val(q)).toFixed(1)}`).join("");
  c.g.innerHTML = (limit != null ? `<line class="limit" x1="${c.m.l}" x2="${c.w - c.m.r}" y1="${c.y(limit)}" y2="${c.y(limit)}"/>` : "") + `<path class="series" d="${path}"/>`;
  c.svg.onmousemove = (evt) => {
    const r = c.svg.getBoundingClientRect();
    const sx = ((evt.clientX - r.left) / r.width) * c.w;
    let best = 0;
    rows.forEach((q, i) => { if (Math.abs(c.x(q.z) - sx) < Math.abs(c.x(rows[best].z) - sx)) best = i; });
    showTip(c, evt, `${fmt(rows[best].z, 2)} m<br>${esc(yLabel)} ${fmt(val(rows[best]), 3)}`);
  };
  c.svg.onmouseleave = () => { c.tip.hidden = true; };
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
    ${g.combination ? `<p>Governing: ${esc(g.combination)}, z ${fmt(g.z, 2)} m (${esc(g.zone)}), N = ${fmt(g.N_kN)} kN (Plaxis sign), M = ${fmt(g.M_kNm)} kNm, V = ${fmt(g.V_kN)} kN. Check: ${esc(g.check)}.</p>` : ""}
    ${(t.notes || []).map((n) => `<p class="status">${esc(n)}</p>`).join("")}
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
    ${p.element.endsWith(" infill") ? "" : v3dSlot(p.element)}
    ${a?.rings ? `<div class="cage"><div class="chart" data-kind="section"></div><div class="scroll"><table>
      <tr><th>Row</th><th>Bars</th><th>Bar circle radius</th><th>Clear spacing</th></tr>
      ${a.rings.map((r, i) => `<tr><td>${i ? (r.count < a.rings[0].count ? `${i + 1} (half row)` : i + 1) : "1 (outer)"}</td><td>${r.count}Ø${r.diameter}</td><td>${fmt(r.radius)} mm</td><td>${fmt(r.clear_spacing_mm)} mm</td></tr>`).join("")}
      <tr><td>Total</td><td>${a.bar_count} bars</td><td colspan="2">${fmt(a.area_mm2)} mm², ${fmt(p.reinforcement_ratio_pct, 2)}%, ${fmt(a.weight_kg_per_m, 1)} kg/m</td></tr>
      </table>
      <p class="status">Clear gap between rows: ${lim.row_gap_mm == null ? "EN 1992-1-1 8.2 minimum" : `${fmt(lim.row_gap_mm)} mm`}.</p></div></div>` : ""}
    ${p.curtailment?.runs?.length ? curtailmentBlock(p.curtailment) : ""}
    ${p.shear ? shearBlock(p.shear, p.head_name || "the slab") : ""}
    ${connectionBlock(p.connection)}
    ${pileCrackBlock(p.cracks)}
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

function setsBlock(stations, note = null) {
  // The seven governing ULS and QP sets per station, as entered in AdSec.
  if (!stations?.length) return "";
  const rows = stations
    .map((st) =>
      [["QP", st.qp], ["ULS", st.uls]]
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
  return `<details style="margin-top:12px"><summary>Governing sets per station for AdSec (${stations.length} station${stations.length === 1 ? "" : "s"}, 7 QP + 7 ULS each)</summary>
    <p class="status">${esc(note || "N in the concrete sign convention (Plaxis N × −1, compression +). M2 and M3 as in Plaxis.")} For QP the 7th set is the largest resultant moment.</p>
    <div class="scroll"><table class="sets"><tr><th>Station (m)</th><th>Limit state</th><th>Case</th><th>Combination</th><th>Node</th><th>z (m)</th><th>N kN</th><th>M2 kNm</th><th>M3 kNm</th><th>N–M util.</th></tr>${rows}</table></div></details>`;
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
