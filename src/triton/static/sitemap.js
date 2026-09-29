// Maps of where projects are built: the projects map on the home page, the project's sites on the
// Project tab and each section's place on the Sections tab. Leaflet with OpenStreetMap tiles (free, no
// key), loaded from cdnjs the first time a map is shown; a satellite layer is one click away.

const LEAFLET = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4";
const esc = (s) =>
  String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

export const SITE_COLOR = "#00467a";
export const SECTION_COLOR = "#d9730d";
// Several sites of one project in different colours, the same on every map.
const SITE_COLORS = ["#00467a", "#2f7d4f", "#8a3ffc", "#b3261e", "#0f7c8c", "#9a6200"];
export const siteColor = (i) => SITE_COLORS[i % SITE_COLORS.length];

let loading = null;
export function loadLeaflet() {
  if (window.L) return Promise.resolve(window.L);
  loading ??= new Promise((ok, fail) => {
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = `${LEAFLET}/leaflet.min.css`;
    document.head.append(css);
    const js = document.createElement("script");
    js.src = `${LEAFLET}/leaflet.min.js`;
    js.onload = () => ok(window.L);
    js.onerror = () => {
      loading = null;
      fail(new Error("The map could not be loaded. It needs an internet connection."));
    };
    document.head.append(js);
  });
  return loading;
}

// A map in `el` with Map and Satellite layers and a scale bar. Resolves to the Leaflet map, or shows why not.
export async function makeMap(el, { center = [26.8, 30.8], zoom = 5 } = {}) {
  el.classList.add("site-map");
  let L;
  try {
    L = await loadLeaflet();
  } catch (e) {
    el.innerHTML = `<p class="map-off">${esc(e.message)}</p>`;
    return null;
  }
  const map = L.map(el, { center, zoom, scrollWheelZoom: false, worldCopyJump: true });
  const street = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  });
  const satellite = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { maxZoom: 19, attribution: "Imagery &copy; Esri" },
  );
  street.addTo(map);
  L.control.layers({ Map: street, Satellite: satellite }, null, { position: "topright" }).addTo(map);
  L.control.scale({ imperial: false }).addTo(map);
  // Scroll zooms once the map is clicked, so the page still scrolls past it.
  map.on("click focus", () => map.scrollWheelZoom.enable());
  map.on("mouseout", () => map.scrollWheelZoom.disable());
  // Drawn while hidden (a folded card, a page of a form): size it when it shows.
  new ResizeObserver(() => map.invalidateSize()).observe(el);
  return map;
}

// A drop pin with a label, in the site's colour; small round dots for sections with their own pin.
export function pin(L, { color = SITE_COLOR, label = "", small = false, faint = false } = {}) {
  const html = small
    ? `<span class="pin-dot" style="--pin:${color}"></span>`
    : `<svg width="26" height="36" viewBox="0 0 26 36" aria-hidden="true"><path d="M13 35C13 35 25 20.5 25 12.5A12 12 0 0 0 1 12.5C1 20.5 13 35 13 35Z" fill="${color}" stroke="#fff" stroke-width="2"/><circle cx="13" cy="12.5" r="4.5" fill="#fff"/></svg>`;
  return L.divIcon({
    className: `map-pin${faint ? " faint" : ""}${small ? " small" : ""}`,
    html: `${html}${label ? `<span class="pin-label">${esc(label)}</span>` : ""}`,
    iconSize: small ? [14, 14] : [26, 36],
    iconAnchor: small ? [7, 7] : [13, 35],
    popupAnchor: small ? [0, -8] : [0, -32],
  });
}

export const hasPoint = (o) => o && o.lat != null && o.lon != null && isFinite(o.lat) && isFinite(o.lon);

// Where a section is: its own pin, else its site's.
export function sectionPoint(section, sites) {
  const loc = section.location || section;
  if (hasPoint(loc)) return { lat: +loc.lat, lon: +loc.lon, own: true };
  const site = sites.find((s) => s.id === (loc.site ?? ""));
  return hasPoint(site) ? { lat: +site.lat, lon: +site.lon, own: false } : null;
}

// "30.0444, 31.2357", "30.0444 31.2357", or a Google Maps / OpenStreetMap link.
export function parseLatLon(text) {
  const t = String(text || "").trim();
  const pats = [/@(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)/, /[?&](?:q|query|ll|mlat)=(-?\d+(?:\.\d+)?)(?:,|%2C|&mlon=)(-?\d+(?:\.\d+)?)/i,
    /#map=\d+\/(-?\d+(?:\.\d+)?)\/(-?\d+(?:\.\d+)?)/, /^(-?\d+(?:\.\d+)?)\s*[,;\s]\s*(-?\d+(?:\.\d+)?)$/];
  for (const re of pats) {
    const m = t.match(re);
    if (m) {
      const lat = +m[1], lon = +m[2];
      if (Math.abs(lat) <= 90 && Math.abs(lon) <= 180) return { lat, lon };
    }
  }
  return null;
}

// Places by name (OpenStreetMap's Nominatim, a few results).
export async function findPlace(q) {
  const url = `https://nominatim.openstreetmap.org/search?format=json&limit=5&q=${encodeURIComponent(q)}`;
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error("The place search did not answer; try again or type the coordinates.");
  return (await res.json()).map((r) => ({ name: r.display_name, lat: +r.lat, lon: +r.lon }));
}

// A search box over a map: a place name, coordinates or a map link. `onPick({lat, lon, name})`.
export function searchBox(map, onPick, placeholder = "Find a place, or paste coordinates or a map link") {
  const box = document.createElement("form");
  box.className = "map-search";
  box.innerHTML = `<input type="search" placeholder="${esc(placeholder)}" aria-label="Find a place"><button class="quiet" type="submit">Find</button><div class="map-found" hidden></div>`;
  const input = box.querySelector("input");
  const found = box.querySelector(".map-found");
  const go = (p) => {
    found.hidden = true;
    map?.setView([p.lat, p.lon], Math.max(map.getZoom(), 14));
    onPick?.(p);
  };
  box.onsubmit = async (e) => {
    e.preventDefault();
    const q = input.value.trim();
    if (!q) return;
    const ll = parseLatLon(q);
    if (ll) return go(ll);
    found.hidden = false;
    found.textContent = "Searching…";
    try {
      const hits = await findPlace(q);
      if (!hits.length) {
        found.textContent = "Nothing found. Try a port or town name, or paste coordinates.";
        return;
      }
      found.innerHTML = hits.map((h, i) => `<button type="button" class="quiet" data-i="${i}">${esc(h.name)}</button>`).join("");
      found.querySelectorAll("button").forEach((b) => (b.onclick = () => go(hits[+b.dataset.i])));
    } catch (err) {
      found.textContent = err.message;
    }
  };
  return box;
}

const fmtLL = (v) => (v == null ? "" : (+v).toFixed(5));
const newId = () => Math.random().toString(16).slice(2, 10).padEnd(8, "0");

// The Project tab's Sites card: one or more named sites, each placed by clicking the map, searching
// for a place or typing coordinates. `changed()` saves the project.
export function sitesPanel(project, changed) {
  const info = project.info;
  info.sites ??= [];
  const panel = document.createElement("div");
  panel.className = "panel sites-panel";
  panel.dataset.free = ""; // where the project is: never a design input, open while locked
  let active = info.sites[0]?.id ?? null;
  let map = null, L = null, layer = null;

  panel.innerHTML = `<div class="pick-head"><h3>Sites on the map</h3>
      <span class="status">Where this project is built. A project in two places gets two sites; each section is then placed at one of them (Sections tab).</span></div>
    <div class="sites-grid"><div class="sites-list"></div><div><div class="map-tools"></div><div class="map-box"></div>
      <p class="hint map-hint"></p></div></div>`;
  const list = panel.querySelector(".sites-list");
  const hint = panel.querySelector(".map-hint");

  const sectionsAt = (id) => project.sections.filter((s) => (s.location?.site ?? "") === id);
  const place = (site, ll) => {
    site.lat = +ll.lat.toFixed(6);
    site.lon = +ll.lon.toFixed(6);
    changed();
    draw();
  };
  const draw = () => {
    list.innerHTML = "";
    info.sites.forEach((site, i) => {
      const row = document.createElement("div");
      row.className = `site-row${site.id === active ? " on" : ""}`;
      row.style.setProperty("--pin", siteColor(i));
      const secs = sectionsAt(site.id);
      row.innerHTML = `<div class="site-top"><span class="pin-dot"></span>
          <input class="site-name" value="${esc(site.name)}" aria-label="Site name">
          <button class="quiet" data-del title="Remove this site">✕</button></div>
        <div class="site-ll"><label>Lat <input data-ll="lat" inputmode="decimal" value="${fmtLL(site.lat)}"></label>
          <label>Lon <input data-ll="lon" inputmode="decimal" value="${fmtLL(site.lon)}"></label></div>
        <div class="hint">${hasPoint(site) ? "" : "<strong>Not placed yet.</strong> "}${secs.length ? `Sections here: ${secs.map((s) => esc(s.name)).join(", ")}` : "No sections placed here yet."}</div>`;
      row.onclick = (e) => {
        if (active === site.id || e.target.closest("[data-del]")) return;
        active = site.id;
        draw();
        if (hasPoint(site)) map?.panTo([site.lat, site.lon]);
      };
      const name = row.querySelector(".site-name");
      name.oninput = () => {
        site.name = name.value.trim() || `Site ${i + 1}`;
        changed();
        drawPins();
      };
      row.querySelectorAll("[data-ll]").forEach((inp) => (inp.onchange = () => {
        const v = inp.value.trim() === "" ? null : +inp.value;
        if (v != null && !isFinite(v)) return;
        site[inp.dataset.ll] = v;
        changed();
        draw();
        if (hasPoint(site)) map?.setView([site.lat, site.lon], Math.max(map.getZoom(), 12));
      }));
      row.querySelector("[data-del]").onclick = () => {
        const n = sectionsAt(site.id).length;
        if (!confirm(`Remove the site "${site.name}"?${n ? ` Its ${n} section${n === 1 ? "" : "s"} will have no site until you place ${n === 1 ? "it" : "them"} again.` : ""}`)) return;
        info.sites.splice(i, 1);
        for (const s of project.sections) if (s.location?.site === site.id) s.location.site = "";
        if (active === site.id) active = info.sites[0]?.id ?? null;
        changed();
        draw();
      };
      list.append(row);
    });
    const add = document.createElement("button");
    add.className = "quiet add-site";
    add.textContent = info.sites.length ? "+ Add another site" : "+ Add a site";
    add.onclick = () => {
      const site = { id: newId(), name: info.sites.length ? `Site ${info.sites.length + 1}` : info.location || "Site 1", lat: null, lon: null, note: "" };
      info.sites.push(site);
      // The first site: every section is there until told otherwise.
      if (info.sites.length === 1) for (const s of project.sections) {
        s.location ??= { site: "", lat: null, lon: null };
        if (!s.location.site) s.location.site = site.id;
      }
      active = site.id;
      changed();
      draw();
      list.querySelector(".site-row.on .site-name")?.select();
    };
    list.append(add);
    const cur = info.sites.find((s) => s.id === active);
    hint.textContent = cur ? `Click the map to place "${cur.name}".` : "Add a site, then click the map to place it.";
    drawPins();
  };
  const drawPins = () => {
    if (!map) return;
    layer.clearLayers();
    const pts = [];
    info.sites.forEach((site, i) => {
      if (!hasPoint(site)) return;
      pts.push([site.lat, site.lon]);
      const m = L.marker([site.lat, site.lon], { icon: pin(L, { color: siteColor(i), label: site.name, faint: info.sites.length > 1 && site.id !== active }), draggable: true });
      m.on("dragend", () => place(site, { lat: m.getLatLng().lat, lon: m.getLatLng().lng }));
      m.on("click", () => { active = site.id; draw(); });
      layer.addLayer(m);
    });
    for (const s of project.sections) {
      if (!hasPoint(s.location)) continue;
      layer.addLayer(L.marker([s.location.lat, s.location.lon], { icon: pin(L, { color: SECTION_COLOR, label: s.name, small: true }) }));
    }
    return pts;
  };
  draw();
  makeMap(panel.querySelector(".map-box")).then((m) => {
    if (!m) return;
    map = m;
    L = window.L;
    layer = L.layerGroup().addTo(map);
    const pts = drawPins();
    if (pts.length === 1) map.setView(pts[0], 13);
    else if (pts.length) map.fitBounds(pts, { padding: [40, 40], maxZoom: 14 });
    map.on("click", (e) => {
      let site = info.sites.find((s) => s.id === active);
      if (!site) {
        list.querySelector(".add-site").click();
        site = info.sites.find((s) => s.id === active);
      }
      place(site, { lat: e.latlng.lat, lon: e.latlng.lng });
    });
    panel.querySelector(".map-tools").append(searchBox(map, (p) => {
      const site = info.sites.find((s) => s.id === active);
      if (site && !hasPoint(site)) place(site, p);
    }));
  });
  return panel;
}

// A section's place on the Sections tab: which site it is at and, if wanted, its own pin (a berth a
// few hundred metres along from the site's pin). Other sections are drawn faint for reference.
export function sectionPlace(project, section, changed, openProjectTab) {
  const sites = project.info.sites ?? [];
  section.location ??= { site: "", lat: null, lon: null };
  const loc = section.location;
  const box = document.createElement("div");
  box.className = "field full section-place";
  box.dataset.free = ""; // never a design input: open while the section is locked
  if (!sites.length) {
    box.innerHTML = `<p class="hint">This project has no sites yet. Add them on the Project tab's map (one per place the
      project is built), then choose here which one this section is at.</p><button class="quiet" type="button">Add sites on the Project tab</button>`;
    box.querySelector("button").onclick = openProjectTab;
    return box;
  }
  let map = null, L = null, layer = null;
  box.innerHTML = `<div class="row"><label>Site <select class="sec-site">
        <option value="">Not placed</option>${sites.map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("")}</select></label>
      <label class="toggle-inline"><input type="checkbox" class="own-pin"> Its own pin on the map</label>
      <button class="quiet clear-pin" type="button">Back to the site's pin</button></div>
    <div class="map-box small"></div><p class="hint place-hint"></p>`;
  const select = box.querySelector(".sec-site");
  const own = box.querySelector(".own-pin");
  const clear = box.querySelector(".clear-pin");
  const hint = box.querySelector(".place-hint");
  select.value = sites.some((s) => s.id === loc.site) ? loc.site : "";
  const refresh = () => {
    own.checked = hasPoint(loc);
    clear.hidden = !own.checked;
    const site = sites.find((s) => s.id === loc.site);
    hint.textContent = !site
      ? "Choose the site this section is at."
      : own.checked ? "Click the map or drag the orange dot to move this section's pin."
      : hasPoint(site) ? `Shown at ${site.name}'s pin. Tick "Its own pin" to place it exactly.` : `${site.name} is not placed on the map yet (Project tab).`;
    drawPins();
  };
  const drawPins = () => {
    if (!map) return;
    layer.clearLayers();
    const pts = [];
    sites.forEach((s, i) => {
      if (!hasPoint(s)) return;
      layer.addLayer(L.marker([s.lat, s.lon], { icon: pin(L, { color: siteColor(i), label: s.name, faint: s.id !== loc.site }) }));
      if (s.id === loc.site) pts.push([s.lat, s.lon]);
    });
    for (const other of project.sections) {
      if (other === section || !hasPoint(other.location)) continue;
      layer.addLayer(L.marker([other.location.lat, other.location.lon], { icon: pin(L, { color: SECTION_COLOR, label: other.name, small: true, faint: true }) }));
    }
    const p = sectionPoint(section, sites);
    if (p?.own) {
      const m = L.marker([p.lat, p.lon], { icon: pin(L, { color: SECTION_COLOR, label: section.name, small: true }), draggable: true });
      m.on("dragend", () => setPin(m.getLatLng().lat, m.getLatLng().lng));
      layer.addLayer(m);
      pts.push([p.lat, p.lon]);
    }
    return pts;
  };
  const setPin = (lat, lon) => {
    loc.lat = +lat.toFixed(6);
    loc.lon = +lon.toFixed(6);
    changed();
    refresh();
  };
  select.onchange = () => {
    loc.site = select.value;
    changed();
    refresh();
    const s = sites.find((x) => x.id === loc.site);
    if (hasPoint(s) && !hasPoint(loc)) map?.setView([s.lat, s.lon], 14);
  };
  own.onchange = () => {
    if (own.checked) {
      const c = map?.getCenter() ?? sectionPoint(section, sites);
      if (!c) {
        own.checked = false;
        hint.textContent = "Place the site on the Project tab first, or click the map where this section is.";
        return;
      }
      setPin(c.lat, c.lng ?? c.lon);
    } else clear.onclick();
  };
  clear.onclick = () => {
    loc.lat = loc.lon = null;
    changed();
    refresh();
  };
  refresh();
  makeMap(box.querySelector(".map-box")).then((m) => {
    if (!m) return;
    map = m;
    L = window.L;
    layer = L.layerGroup().addTo(map);
    const pts = drawPins();
    const all = sites.filter(hasPoint).map((s) => [s.lat, s.lon]);
    if (pts.length) map.setView(pts.at(-1), 15);
    else if (all.length) map.fitBounds(all, { padding: [30, 30], maxZoom: 14 });
    map.on("click", (e) => {
      if (!loc.site) return;
      setPin(e.latlng.lat, e.latlng.lng);
    });
  });
  return box;
}
