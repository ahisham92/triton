// The Projects home page: start or open a project, the saved projects as cards (or a table), and a map
// below of where every project is built, its sites and its sections.
import { KINDS, kindColor, kindIcon } from "./picker.js";
import { SECTION_COLOR, hasPoint, makeMap, pin, sectionPoint, siteColor } from "./sitemap.js";

const esc = (s) =>
  String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const MAIN_KINDS = ["pile", "combi_wall", "sheet_pile_wall", "diaphragm_wall", "slab", "front_beam", "rear_beam"];
const PREFS = "triton-projects-view";

function prefs() {
  try {
    return { sort: "recent", view: "cards", ...JSON.parse(localStorage.getItem(PREFS) || "{}") };
  } catch {
    return { sort: "recent", view: "cards" };
  }
}
function keepPrefs(p) {
  try {
    localStorage.setItem(PREFS, JSON.stringify(p));
  } catch {
    /* private window: the choice just isn't kept */
  }
}

// "5 minutes ago", "yesterday", "3 Sep".
function ago(iso) {
  const t = Date.parse(iso);
  if (!isFinite(t)) return "";
  const s = (Date.now() - t) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} minutes ago`;
  if (s < 86400) return `${Math.round(s / 3600)} hour${Math.round(s / 3600) === 1 ? "" : "s"} ago`;
  if (s < 172800) return "yesterday";
  if (s < 7 * 86400) return `${Math.floor(s / 86400)} days ago`;
  return new Date(t).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

const projectUrl = (id, tab = "info", sectionId = "") => `#/project/${id}/${tab}${sectionId ? `/${sectionId}` : ""}`;

// What a project card shows about where the project is.
function placeLine(p) {
  const placed = p.sites.filter(hasPoint);
  if (p.sites.length > 1) return `${p.sites.length} sites: ${p.sites.map((s) => esc(s.name)).join(", ")}`;
  if (p.sites.length === 1) return `${esc(p.sites[0].name)}${placed.length ? "" : " (not on the map yet)"}`;
  return p.location ? `${esc(p.location)} (not on the map yet)` : "No site yet";
}

export async function projectsPage($app, { ROOT, api, when, wireOpenProject }) {
  const pref = prefs();
  $app.innerHTML = `<div class="hero"><img src="${ROOT}/static/logo.svg" alt="" width="72" height="72">
      <div><h1>Projects</h1><p class="sub">Each project holds the sections, materials and design
    settings used to design the elements in a Plaxis workbook.</p>
      <div class="hero-kinds">${MAIN_KINDS.map((k) => `<span style="--kind:${kindColor(k)}">${kindIcon(k, 20)}${esc(KINDS[k].label)}</span>`).join("")}</div></div></div>
    <div class="start-cards">
      <div class="panel start-card"><h3>Start a new project</h3>
        <div class="row"><input id="new-name" placeholder="Project name, e.g. Container terminal quay"><button id="new">Create</button></div>
        <p class="hint">You set its sections, sites and settings next.</p></div>
      <div class="panel start-card"><h3>Open a project file</h3>
        <div class="row"><input type="file" id="trt" accept=".trt"><button id="open-trt" disabled>Open</button></div>
        <p class="hint" id="trt-status">A .trt downloaded from Triton (Project tab › Download project).</p>
        <div class="row" id="trt-choice" hidden></div></div>
    </div>
    <div class="saved-head"><h2>Saved projects <span class="count" id="count"></span></h2>
      <div class="saved-tools">
        <input type="search" id="find" placeholder="Search by name, number, client or site" aria-label="Search projects">
        <select id="sort" aria-label="Sort by"><option value="recent">Last saved first</option><option value="name">Name A to Z</option><option value="number">Project number</option></select>
        <span class="seg" role="group" aria-label="Show as"><button class="quiet" data-view="cards">Cards</button><button class="quiet" data-view="table">Table</button></span>
      </div></div>
    <div id="list"><div class="panel">Loading…</div></div>
    <div class="saved-head"><h2>Projects on the map</h2><span class="status" id="map-note"></span></div>
    <div class="panel map-panel"><div id="projects-map"></div><div class="map-legend" id="map-legend"></div></div>`;
  wireOpenProject();
  const create = async () => {
    const name = document.getElementById("new-name").value.trim() || "New project";
    const p = await api(ROOT + "/api/projects", { method: "POST", body: JSON.stringify({ info: { name } }) });
    location.hash = projectUrl(p.id);
  };
  document.getElementById("new").onclick = create;
  document.getElementById("new-name").onkeydown = (e) => e.key === "Enter" && create();

  const list = await api(ROOT + "/api/projects");
  const host = document.getElementById("list");
  const find = document.getElementById("find");
  const sort = document.getElementById("sort");
  sort.value = pref.sort;
  document.getElementById("count").textContent = list.length ? String(list.length) : "";
  if (!list.length) {
    host.innerHTML = `<div class="panel empty-state">${kindIcon("section", 40)}<p><strong>No projects yet.</strong> Start one above, or open a .trt file someone sent you.</p></div>`;
    document.querySelector(".saved-tools").hidden = true;
  }
  // Each project's colour on the map and its card, the same whatever the order shown.
  list.forEach((p) => (p.color = siteColor([...p.id].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7))));

  let mapApi = null; // set once the map is up: highlight(id), show(id)
  const shown = () => {
    const q = find.value.trim().toLowerCase();
    const hit = (p) => !q || [p.name, p.number, p.client, p.location, ...p.sites.map((s) => s.name), ...p.section_pins.map((s) => s.name)]
      .some((t) => String(t || "").toLowerCase().includes(q));
    const by = {
      recent: (a, b) => String(b.updated_at).localeCompare(String(a.updated_at)),
      name: (a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }),
      number: (a, b) => String(a.number || "~").localeCompare(String(b.number || "~"), undefined, { numeric: true }),
    }[sort.value];
    return list.filter(hit).sort(by);
  };
  const draw = () => {
    if (!list.length) return;
    const rows = shown();
    document.querySelectorAll("[data-view]").forEach((b) => b.classList.toggle("on", b.dataset.view === pref.view));
    if (!rows.length) {
      host.innerHTML = `<div class="panel empty-state"><p>No project matches "${esc(find.value)}".</p></div>`;
      return;
    }
    if (pref.view === "table") {
      host.innerHTML = `<div class="panel scroll"><table><tr><th>Name</th><th>Number</th><th>Client</th><th>Where</th><th>Sections</th><th>Elements</th><th>Last saved (Cairo)</th></tr>${rows
        .map((p) => `<tr class="link" data-id="${esc(p.id)}"><td><span class="pin-dot" style="--pin:${p.color}"></span> ${esc(p.name)}</td><td>${esc(p.number)}</td><td>${esc(p.client)}</td>
          <td>${placeLine(p)}</td><td>${p.sections}</td><td>${p.elements}</td><td>${esc(when(p.updated_at))}</td></tr>`).join("")}</table></div>`;
    } else {
      host.innerHTML = `<div class="project-cards">${rows.map((p) => {
        const kinds = [...new Set(p.section_pins.flatMap((s) => s.kinds))];
        const designed = p.section_pins.filter((s) => s.locked).length;
        return `<article class="project-card" data-id="${esc(p.id)}" style="--pin:${p.color}" tabindex="0">
          <div class="pc-top"><h3>${esc(p.name)}</h3>${p.number ? `<span class="pc-num">${esc(p.number)}</span>` : ""}</div>
          ${p.client ? `<div class="pc-client">${esc(p.client)}</div>` : ""}
          <div class="pc-where"><svg width="12" height="16" viewBox="0 0 26 36" aria-hidden="true"><path d="M13 35C13 35 25 20.5 25 12.5A12 12 0 0 0 1 12.5C1 20.5 13 35 13 35Z" fill="currentColor"/></svg> ${placeLine(p)}</div>
          <div class="pc-stats"><span><strong>${p.sections}</strong> section${p.sections === 1 ? "" : "s"}</span><span><strong>${p.elements}</strong> element${p.elements === 1 ? "" : "s"}</span>${designed ? `<span class="pc-done"><strong>${designed}</strong> designed</span>` : ""}</div>
          ${kinds.length ? `<div class="pc-kinds">${kinds.map((k) => `<span title="${esc(KINDS[k]?.label ?? k)}" style="--kind:${kindColor(k)}">${kindIcon(k, 18)}</span>`).join("")}</div>` : ""}
          <div class="pc-foot"><span class="status" title="${esc(when(p.updated_at))} (Cairo)">Saved ${esc(ago(p.updated_at))}</span>
            <span class="pc-links">${p.sites.some(hasPoint) || p.section_pins.some(hasPoint) ? `<button class="quiet" data-map>Map</button>` : `<a href="${projectUrl(p.id)}" data-set>Add a site</a>`}<a class="pc-open" href="${projectUrl(p.id)}">Open</a></span></div>
        </article>`;
      }).join("")}</div>`;
    }
    host.querySelectorAll("[data-id]").forEach((el) => {
      const id = el.dataset.id;
      el.onclick = (e) => {
        if (e.target.closest("[data-map]")) {
          mapApi?.show(id);
          document.getElementById("projects-map").scrollIntoView({ behavior: "smooth", block: "center" });
          return;
        }
        if (!e.target.closest("a")) location.hash = projectUrl(id);
      };
      el.onkeydown = (e) => e.key === "Enter" && e.target === el && (location.hash = projectUrl(id));
      el.onmouseenter = () => mapApi?.highlight(id);
      el.onmouseleave = () => mapApi?.highlight(null);
    });
  };
  find.oninput = draw;
  sort.onchange = () => {
    pref.sort = sort.value;
    keepPrefs(pref);
    draw();
  };
  document.querySelectorAll("[data-view]").forEach((b) => (b.onclick = () => {
    pref.view = b.dataset.view;
    keepPrefs(pref);
    draw();
  }));
  draw();
  mapApi = await projectsMap(list);
}

// Every project's sites as pins in the project's colour, and each section with its own pin as a small
// dot joined to its site. A pin's card lists the sections there, each a link to that section.
async function projectsMap(list) {
  const note = document.getElementById("map-note");
  const legend = document.getElementById("map-legend");
  const unplaced = list.filter((p) => !p.sites.some(hasPoint) && !p.section_pins.some(hasPoint));
  note.innerHTML = !list.length ? "" : unplaced.length === list.length
    ? "No project is on the map yet. Open a project and add its site on the Project tab."
    : unplaced.length ? `Not on the map yet: ${unplaced.map((p) => `<a href="${projectUrl(p.id)}">${esc(p.name)}</a>`).join(", ")} (add a site on the Project tab).` : "";
  const map = await makeMap(document.getElementById("projects-map"));
  if (!map) return null;
  const L = window.L;
  const groups = new Map(); // project id -> its layers
  const all = [];
  for (const p of list) {
    const g = L.featureGroup().addTo(map);
    groups.set(p.id, g);
    const multi = p.sites.length > 1;
    p.sites.forEach((site) => {
      if (!hasPoint(site)) return;
      const here = p.section_pins.filter((s) => s.site === site.id);
      const label = multi ? `${p.name} · ${site.name}` : p.name;
      const m = L.marker([site.lat, site.lon], { icon: pin(L, { color: p.color, label }), title: label });
      m.bindPopup(`<div class="map-pop"><strong>${esc(p.name)}</strong>${p.number ? ` <span class="status">${esc(p.number)}</span>` : ""}
        <div>${multi ? `Site: ${esc(site.name)} (one of ${p.sites.length})` : esc(site.name)}</div>
        ${here.length ? `<ul>${here.map((s) => `<li><a href="${projectUrl(p.id, "sections", s.id)}">${esc(s.name)}</a>${s.locked ? " · designed" : ""}</li>`).join("")}</ul>` : `<div class="status">No sections placed here yet.</div>`}
        <a class="pop-open" href="${projectUrl(p.id)}">Open the project</a></div>`);
      g.addLayer(m);
      all.push([site.lat, site.lon]);
    });
    for (const s of p.section_pins) {
      const at = sectionPoint(s, p.sites);
      if (!at?.own) continue;
      const site = p.sites.find((x) => x.id === s.site);
      if (hasPoint(site)) g.addLayer(L.polyline([[site.lat, site.lon], [at.lat, at.lon]], { color: p.color, weight: 1.5, dashArray: "4 4", opacity: 0.7 }));
      const m = L.marker([at.lat, at.lon], { icon: pin(L, { color: SECTION_COLOR, label: s.name, small: true }), title: `${p.name} · ${s.name}` });
      m.bindPopup(`<div class="map-pop"><strong>${esc(s.name)}</strong><div>${esc(p.name)}${site ? ` · ${esc(site.name)}` : ""}</div>
        <a class="pop-open" href="${projectUrl(p.id, "sections", s.id)}">Open this section</a></div>`);
      g.addLayer(m);
      all.push([at.lat, at.lon]);
    }
  }
  if (all.length === 1) map.setView(all[0], 12);
  else if (all.length) map.fitBounds(all, { padding: [50, 50], maxZoom: 13 });
  const placed = list.filter((p) => groups.get(p.id).getLayers().length);
  legend.innerHTML = placed.length
    ? `${placed.map((p) => `<button class="quiet legend-item" data-id="${esc(p.id)}"><span class="pin-dot" style="--pin:${p.color}"></span>${esc(p.name)}${p.sites.length > 1 ? ` <span class="status">(${p.sites.length} sites)</span>` : ""}</button>`).join("")}
       <span class="legend-item status"><span class="pin-dot small" style="--pin:${SECTION_COLOR}"></span>A section with its own pin</span>
       <button class="quiet legend-item" data-all>Show all</button>`
    : "";
  const highlight = (id) => {
    for (const [pid, g] of groups)
      g.eachLayer((l) => l.getElement?.()?.classList.toggle("dim", !!id && pid !== id));
  };
  const show = (id) => {
    const g = groups.get(id);
    if (!g?.getLayers().length) return;
    const b = g.getBounds();
    if (b.getNorthEast().equals(b.getSouthWest())) map.setView(b.getCenter(), 14);
    else map.fitBounds(b, { padding: [50, 50], maxZoom: 15 });
    const first = g.getLayers().find((l) => l.openPopup && l.getPopup());
    first?.openPopup();
  };
  legend.querySelectorAll("[data-id]").forEach((b) => {
    b.onclick = () => show(b.dataset.id);
    b.onmouseenter = () => highlight(b.dataset.id);
    b.onmouseleave = () => highlight(null);
  });
  const allBtn = legend.querySelector("[data-all]");
  if (allBtn) allBtn.onclick = () => (all.length > 1 ? map.fitBounds(all, { padding: [50, 50], maxZoom: 13 }) : all[0] && map.setView(all[0], 12));
  return { highlight, show };
}
