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
    ["design", "Design settings"],
    ["elements", `Elements (${Object.keys(p.elements).length})`],
    ["workbook", "Workbook"],
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
  else if (tab === "design") host.append(renderObject(SCHEMA.properties.design, p.design, "design", "Design settings"));
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

function prettyOption(o) {
  const map = { crack_only: "Crack width only", structural: "Structural (shares load)", min_steel: "Least steel",
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

function wireChecker(onReport) {
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
      const data = await api("/api/workbooks/check", { method: "POST", body });
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

function renderWorkbookTab(host) {
  host.innerHTML = `<p class="sub">Check the workbook for this project. Elements found in it can be added to the project.</p>` + checkerHtml();
  wireChecker((data) => {
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
      location.hash = `#/project/${state.project.id}/elements`;
    };
  });
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
