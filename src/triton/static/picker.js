// One-at-a-time pickers for elements and sections: a row of coloured tiles to choose from, then only
// the chosen one shown, with Previous / Next and a list to jump straight to one (e.g. Pile(4)). Also
// the colour and small drawing of each kind of element, and a cross-section of the quay showing where
// each element sits.

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// One colour per kind of element, used on the tiles, the cross-section and the result cards.
export const KINDS = {
  pile: { label: "Pile", color: "#2a78d6" },
  combi_wall: { label: "Combi wall", color: "#7b4bc4" },
  sheet_pile_wall: { label: "Sheet pile wall", color: "#8c6d1f" },
  slab: { label: "Slab", color: "#1f9e89" },
  front_beam: { label: "Front beam", color: "#d9730d" },
  rear_beam: { label: "Rear beam", color: "#c2477d" },
  transverse_beam: { label: "Transverse beam", color: "#b35a1f" },
  approach: { label: "Approach slab", color: "#5a8f29" },
  section: { label: "Section", color: "#00467a" },
};
export const kindColor = (kind) => (KINDS[kind] || KINDS.section).color;

// Guess the kind from a name (result cards and workbook elements carry only their name).
export function guessKind(name) {
  const n = String(name).toLowerCase();
  if (/combi/.test(n)) return "combi_wall";
  if (/spw|sheet/.test(n)) return "sheet_pile_wall";
  if (/approach|ledge/.test(n)) return "approach";
  if (/front/.test(n)) return "front_beam";
  if (/rear/.test(n)) return "rear_beam";
  if (/transverse|cross/.test(n)) return "transverse_beam";
  if (/deck|slab/.test(n)) return "slab";
  if (/pile/.test(n)) return "pile";
  return "section";
}

// A small drawing of each kind, in its colour.
export function kindIcon(kind, size = 28) {
  const c = kindColor(kind);
  const soft = `${c}33`;
  const body = {
    pile: `<rect x="11" y="4" width="10" height="24" rx="2" fill="${soft}" stroke="${c}" stroke-width="2"/>
      <path d="M11 10h10M11 16h10M11 22h10" stroke="${c}" stroke-width="1.2"/>`,
    combi_wall: `<circle cx="9" cy="16" r="5" fill="${soft}" stroke="${c}" stroke-width="2"/>
      <circle cx="23" cy="16" r="5" fill="${soft}" stroke="${c}" stroke-width="2"/>
      <path d="M14 16h4" stroke="${c}" stroke-width="2.5"/>`,
    sheet_pile_wall: `<path d="M3 20 l4 -8 h6 l4 8 h6 l4 -8 h2" fill="none" stroke="${c}" stroke-width="2.4" stroke-linejoin="round"/>`,
    slab: `<rect x="3" y="9" width="26" height="7" rx="1.5" fill="${soft}" stroke="${c}" stroke-width="2"/>
      <path d="M8 16v10M16 16v10M24 16v10" stroke="${c}" stroke-width="2"/>`,
    front_beam: `<rect x="5" y="7" width="22" height="16" rx="2" fill="${soft}" stroke="${c}" stroke-width="2"/>
      <circle cx="10" cy="18" r="1.6" fill="${c}"/><circle cx="16" cy="18" r="1.6" fill="${c}"/><circle cx="22" cy="18" r="1.6" fill="${c}"/>
      <circle cx="10" cy="12" r="1.6" fill="${c}"/><circle cx="22" cy="12" r="1.6" fill="${c}"/>`,
    rear_beam: `<rect x="7" y="5" width="18" height="20" rx="2" fill="${soft}" stroke="${c}" stroke-width="2"/>
      <circle cx="12" cy="20" r="1.6" fill="${c}"/><circle cx="20" cy="20" r="1.6" fill="${c}"/><circle cx="12" cy="10" r="1.6" fill="${c}"/><circle cx="20" cy="10" r="1.6" fill="${c}"/>`,
    transverse_beam: `<rect x="3" y="11" width="26" height="10" rx="2" fill="${soft}" stroke="${c}" stroke-width="2"/>`,
    approach: `<path d="M3 22 L29 12 L29 17 L3 27 Z" fill="${soft}" stroke="${c}" stroke-width="2" stroke-linejoin="round"/>`,
    section: `<path d="M4 8h18v6H4z" fill="${soft}" stroke="${c}" stroke-width="2"/><path d="M8 14v12M16 14v12M22 8v18" stroke="${c}" stroke-width="2"/>
      <path d="M2 22q3 -2 6 0t6 0t6 0t6 0t6 0" fill="none" stroke="#1e8fc0" stroke-width="1.6"/>`,
  }[kind] || "";
  return `<svg class="kind-icon" width="${size}" height="${size}" viewBox="0 0 32 32" aria-hidden="true">${body}</svg>`;
}

// safe / near the limit / unsafe from a utilisation (the same bands as the "To look at" list).
export function statusOf(u) {
  if (u == null || !isFinite(u)) return null;
  return u > 1 ? "unsafe" : u >= 0.95 ? "limit" : "safe";
}
const STATUS_TEXT = { unsafe: "Unsafe", limit: "Near the limit", safe: "Safe", none: "Not designed" };

// What was picked, per list (elements of a section, results, sections), kept for the visit and, where
// the browser allows it, the next one.
const PICKS = new Map();
const STORE = "triton-picks";
try {
  for (const [k, v] of Object.entries(JSON.parse(localStorage.getItem(STORE) || "{}"))) PICKS.set(k, v);
} catch {
  /* private window or storage blocked: kept for this visit only */
}
export function picked(key) {
  return PICKS.get(key);
}
export function keepPick(key, value) {
  PICKS.set(key, value);
  try {
    localStorage.setItem(STORE, JSON.stringify(Object.fromEntries([...PICKS].slice(-200))));
  } catch {
    /* ignore */
  }
}

export const ALL = "*";

/**
 * The tiles and the Previous / list / Next bar.
 * items: [{ key, label, kind, sub, status, utilisation }]
 * Returns the element; `onPick(key)` is called on every change (ALL for "show all").
 */
export function stepper({ items, current, noun = "element", nouns = `${noun}s`, onPick, allowAll = true, tiles = true, onFoldAll = null }) {
  const box = document.createElement("div");
  box.className = "stepper";
  const keys = items.map((i) => i.key);
  const draw = (cur) => {
    const at = keys.indexOf(cur);
    const tileHtml = tiles
      ? `<div class="pick-tiles">${items
          .map((i) => {
            const st = i.status || (i.utilisation !== undefined ? statusOf(i.utilisation) || "none" : "");
            const meter = i.utilisation != null && isFinite(i.utilisation)
              ? `<span class="meter ${st}"><i style="width:${Math.min(100, Math.max(3, i.utilisation * 100)).toFixed(0)}%"></i></span>`
              : "";
            return `<button type="button" class="pick-tile ${i.key === cur ? "on" : ""} ${st ? `st-${st}` : ""}" data-key="${esc(i.key)}"
                style="--kind:${kindColor(i.kind)}" title="${esc(i.label)}${st ? ` · ${STATUS_TEXT[st]}` : ""}">
              ${kindIcon(i.kind)}<span class="pick-text"><b>${esc(i.label)}</b><small>${esc(i.sub ?? KINDS[i.kind]?.label ?? "")}</small>
              ${meter}${i.dots ? `<span class="kdots">${i.dots.map((k) => `<i style="background:${kindColor(k)}" title="${esc(KINDS[k]?.label || k)}"></i>`).join("")}</span>` : ""}</span>${st && st !== "none" ? `<span class="dot ${st}" aria-label="${STATUS_TEXT[st]}"></span>` : ""}</button>`;
          })
          .join("")}${allowAll ? `<button type="button" class="pick-tile all ${cur === ALL ? "on" : ""}" data-key="${ALL}">
            <span class="pick-text"><b>Show all</b><small>every ${esc(noun)} on one page</small></span></button>` : ""}</div>`
      : "";
    box.innerHTML = `${tileHtml}<div class="pick-nav">
        <button type="button" class="quiet" data-step="-1" ${at <= 0 ? "disabled" : ""} data-free>&#9664; Previous</button>
        <select data-free aria-label="Choose the ${esc(noun)}">${items
          .map((i) => `<option value="${esc(i.key)}" ${i.key === cur ? "selected" : ""}>${esc(i.label)}</option>`)
          .join("")}${allowAll ? `<option value="${ALL}" ${cur === ALL ? "selected" : ""}>All ${esc(nouns)}</option>` : ""}</select>
        <button type="button" class="quiet" data-step="1" ${at < 0 || at >= keys.length - 1 ? "disabled" : ""} data-free>Next &#9654;</button>
        <span class="status">${at >= 0 ? `${at + 1} of ${keys.length}` : `all ${keys.length}`}</span>
        ${cur === ALL && onFoldAll ? `<span class="fold-all"><button type="button" class="quiet" data-fold="0" data-free>&#9662; Open all</button>
          <button type="button" class="quiet" data-fold="1" data-free>&#9656; Fold all</button></span>` : ""}
        ${cur === ALL && allowAll && keys.length ? `<button type="button" class="quiet" data-one data-free title="Back to one ${esc(noun)} at a time">Show one at a time</button>` : ""}</div>`;
    box.querySelectorAll(".pick-tile, .pick-nav button, .pick-nav select").forEach((b) => (b.dataset.free = ""));
    box.querySelectorAll(".pick-tile").forEach((b) => (b.onclick = () => go(b.dataset.key)));
    box.querySelectorAll("[data-step]").forEach((b) => (b.onclick = () => go(keys[Math.max(0, Math.min(keys.length - 1, keys.indexOf(cur) + +b.dataset.step))])));
    box.querySelector("select").onchange = (e) => go(e.target.value);
    box.querySelectorAll("[data-fold]").forEach((b) => (b.onclick = () => onFoldAll(b.dataset.fold === "1")));
    const one = box.querySelector("[data-one]");
    if (one) one.onclick = () => go(keys[0]);
  };
  const go = (key) => {
    if (key == null) return;
    draw(key);
    onPick(key);
  };
  draw(current);
  box.pick = go;
  return box;
}

/**
 * A cross-section of the quay (across the berth, levels up) with every element in its colour, drawn
 * from the workbook's geometry when it is there, or as a typical quay otherwise. Clicking an element
 * picks it.
 */
export function quaySketch({ elements, geometry, selected, site, onPick }) {
  const W = 760, H = 250, pad = 24;
  const wrap = document.createElement("div");
  wrap.className = "quay-sketch";
  const items = placeElements(elements, geometry);
  if (!items.length) return wrap;
  const xs = items.flatMap((i) => [i.x0, i.x1]);
  const zs = items.flatMap((i) => [i.z0, i.z1]);
  const seabed = site?.seabed_level ?? null;
  const water = (site?.water_levels || []).filter((w) => !(site?.water_hidden || []).includes(w.name));
  if (seabed != null) zs.push(seabed);
  let [xl, xh, zl, zh] = [Math.min(...xs), Math.max(...xs), Math.min(...zs), Math.max(...zs)];
  xl -= (xh - xl) * 0.12 + 1;
  xh += (xh - xl) * 0.25 + 1;
  zh += (zh - zl) * 0.08 + 0.5;
  zl -= (zh - zl) * 0.04;
  // Across the quay is drawn true to scale in plan and stretched in depth so short decks still read.
  const sx = (x) => pad + ((x - xl) / (xh - xl)) * (W - 2 * pad);
  const sz = (z) => pad / 2 + ((zh - z) / (zh - zl)) * (H - pad - 16);
  const sea = xh; // the sea is on the high X side (the front wall's side) in Triton's sections
  const front = Math.max(...items.filter((i) => ["combi_wall", "sheet_pile_wall", "front_beam"].includes(i.kind)).map((i) => i.x1), -Infinity);
  const seaFrom = isFinite(front) ? front : xh - (xh - xl) * 0.2;
  const levels = water.length ? water : [{ name: "Water", level: 0 }];
  const top = Math.max(...levels.map((w) => w.level));
  const bed = seabed ?? zl + (zh - zl) * 0.35;
  const shapes = items
    .map((i) => {
      const c = kindColor(i.kind);
      const on = i.name === selected;
      const x = sx(i.x0), w = Math.max(3, sx(i.x1) - sx(i.x0)), y = sz(i.z1), h = Math.max(3, sz(i.z0) - sz(i.z1));
      return `<g class="qs-el ${on ? "on" : ""}" data-name="${esc(i.name)}" tabindex="0" role="button" aria-label="${esc(i.name)}">
        <title>${esc(i.name)} · ${esc(KINDS[i.kind]?.label || "")}</title>
        <rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${w.toFixed(1)}" height="${h.toFixed(1)}" rx="${i.round ? Math.min(w, 6) / 2 : 1.5}"
          fill="${c}" fill-opacity="${on ? 0.9 : 0.35}" stroke="${c}" stroke-width="${on ? 2.5 : 1.2}"/>
        ${i.labelled ? `<text x="${(x + w / 2).toFixed(1)}" y="${(i.labelAbove ? y - 5 : y + h + 12).toFixed(1)}" text-anchor="middle" class="qs-label" fill="${c}">${esc(i.short)}</text>` : ""}</g>`;
    })
    .join("");
  wrap.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Cross-section of the quay with its elements">
      <defs><linearGradient id="qs-sea" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#6fb7df" stop-opacity=".45"/><stop offset="1" stop-color="#1e8fc0" stop-opacity=".25"/></linearGradient>
      <pattern id="qs-soil" width="8" height="8" patternUnits="userSpaceOnUse"><rect width="8" height="8" fill="#c9a86b" fill-opacity=".22"/><circle cx="2" cy="2" r=".9" fill="#a0824a" fill-opacity=".5"/></pattern></defs>
      <rect x="${sx(seaFrom).toFixed(1)}" y="${sz(top).toFixed(1)}" width="${(W - pad - sx(seaFrom)).toFixed(1)}" height="${(sz(bed) - sz(top)).toFixed(1)}" fill="url(#qs-sea)"/>
      ${levels.map((w) => `<line x1="${sx(seaFrom).toFixed(1)}" x2="${W - pad}" y1="${sz(w.level).toFixed(1)}" y2="${sz(w.level).toFixed(1)}" stroke="#1e8fc0" stroke-dasharray="5 3" stroke-width="1"/>`).join("")}
      <text x="${W - pad - 4}" y="${(sz(top) - 4).toFixed(1)}" text-anchor="end" class="qs-note" fill="#1e6f99">sea ▽ ${esc(levels.length > 1 ? "tide levels" : levels[0].name)}</text>
      <rect x="${sx(seaFrom).toFixed(1)}" y="${sz(bed).toFixed(1)}" width="${(W - pad - sx(seaFrom)).toFixed(1)}" height="${(H - sz(bed)).toFixed(1)}" fill="url(#qs-soil)"/>
      <rect x="${pad}" y="${sz(Math.max(...items.filter((i) => i.kind === "slab").map((i) => i.z0), bed + (zh - bed) * 0.5)).toFixed(1)}" width="${(sx(seaFrom) - pad).toFixed(1)}" height="${H}" fill="url(#qs-soil)"/>
      <text x="${pad + 4}" y="${H - 6}" class="qs-note" fill="#8a6d3b">soil</text>
      ${seabed != null ? `<text x="${W - pad - 4}" y="${(sz(bed) + 13).toFixed(1)}" text-anchor="end" class="qs-note" fill="#8a6d3b">seabed ${seabed}</text>` : ""}
      <text x="${pad}" y="14" class="qs-note">land side</text><text x="${W - pad}" y="14" text-anchor="end" class="qs-note">sea side</text>
      ${shapes}</svg>
    <p class="status qs-foot">${geometry ? "Across the quay, from the workbook's node levels and positions (depth stretched to fit)." : "A typical quay: upload the workbook to see this section's real levels and positions."} Click an element to open it.</p>`;
  wrap.querySelectorAll(".qs-el").forEach((g) => {
    g.onclick = () => onPick?.(g.dataset.name);
    g.onkeydown = (e) => (e.key === "Enter" || e.key === " ") && onPick?.(g.dataset.name);
  });
  return wrap;
}

// Where each element sits across the quay: [x0, x1] and levels [z0, z1], in metres.
function placeElements(elements, geometry) {
  const byName = new Map((geometry?.elements || []).map((g) => [g.element, g]));
  const out = [];
  const piles = elements.filter((e) => e.kind === "pile");
  let fallbackPile = 0;
  for (const e of elements) {
    const g = byName.get(e.name);
    const short = e.name.replace(/^Pile\((\d+)\)$/, "P$1").replace(/ Beam$/i, " beam");
    let item = null;
    if (g?.lines?.length) {
      const x = g.lines.reduce((s, l) => s + l[0], 0) / g.lines.length;
      const top = Math.max(...g.lines.map((l) => l[2]));
      const bot = Math.min(...g.lines.map((l) => l[3]));
      const d = e.kind === "combi_wall" ? 1.6 : 1.2;
      item = { x0: x - d / 2, x1: x + d / 2, z0: bot, z1: top, round: true };
    } else if (g?.box) {
      const [x0, x1] = g.box.X, [z0, z1] = g.box.Z;
      const thick = e.kind === "slab" ? 0.8 : ["front_beam", "rear_beam", "transverse_beam"].includes(e.kind) ? 2 : 0;
      item = thick && z1 - z0 < 0.5 ? { x0, x1, z0: z1 - thick / 2, z1: z1 + thick / 2 } : { x0: x0 - (x1 - x0 < 0.2 ? 0.3 : 0), x1: x1 + (x1 - x0 < 0.2 ? 0.3 : 0), z0, z1 };
    } else {
      // A typical quay: wall at the front (x 0), deck back to -24 m, piles in rows under it.
      const typical = {
        combi_wall: { x0: -0.8, x1: 0.8, z0: -39, z1: 2.7, round: true },
        sheet_pile_wall: { x0: -0.3, x1: 0.3, z0: -19.5, z1: 2.7 },
        front_beam: { x0: -1, x1: 1, z0: 1.7, z1: 3.7 },
        rear_beam: { x0: -24.8, x1: -23.2, z0: 1.7, z1: 3.7 },
        transverse_beam: { x0: -23, x1: -1, z0: 2.1, z1: 3.3 },
        slab: { x0: -23.2, x1: -1, z0: 2.3, z1: 3.1 },
        approach: { x0: -32, x1: -24.8, z0: 2.6, z1: 3.3 },
      }[e.kind];
      if (typical) item = { ...typical };
      else if (e.kind === "pile") {
        const k = fallbackPile++;
        const x = -4 - (k * 20) / Math.max(1, piles.length - 1);
        item = { x0: x - 0.6, x1: x + 0.6, z0: -34, z1: 2.3, round: true };
      }
    }
    if (item) out.push({ ...item, name: e.name, kind: e.kind, short, labelled: true, labelAbove: !["pile", "combi_wall", "sheet_pile_wall"].includes(e.kind) });
  }
  // Walls and piles are labelled below their toe; plates above. Beams drawn over the slab.
  const order = { slab: 0, approach: 0, transverse_beam: 1 };
  return out.sort((a, b) => (order[a.kind] ?? 2) - (order[b.kind] ?? 2));
}

// How Triton works, as a row of steps from the project to the exports, each coloured by where it
// stands (done, the next thing to do, still to come) and opening its tab when clicked.
export function flowDiagram(steps, onGo) {
  const box = document.createElement("div");
  box.className = "flow";
  box.innerHTML = steps
    .map((s, i) => `<button type="button" class="flow-step ${s.state}" data-tab="${esc(s.tab)}" data-free style="--step:${s.color}">
        <span class="flow-n">${s.state === "done" ? "&#10003;" : i + 1}</span>
        <span class="flow-t"><b>${esc(s.title)}</b><small>${esc(s.note)}</small></span></button>${i < steps.length - 1 ? '<span class="flow-arrow" aria-hidden="true">&#10140;</span>' : ""}`)
    .join("");
  box.querySelectorAll(".flow-step").forEach((b) => (b.onclick = () => onGo(b.dataset.tab)));
  return box;
}

// A panel that folds to its title line: click the title (or its arrow) to open or hide the rest.
// What was open is kept per `key`; `open` is the first-time state.
export function foldable(box, key, open = false) {
  box.classList.add("foldable");
  const kept = key ? picked(`fold:${key}`) : undefined;
  box.classList.toggle("folded", !(kept ?? open));
  box.dataset.foldKey = key || "";
  if (box._folding) return box;
  box._folding = true;
  box.addEventListener("click", (e) => {
    const head = [...box.children].find((ch) => ch.matches(".element-head, .pick-head, h3:first-child") && ch.contains(e.target));
    if (!head) return;
    if (e.target.closest("button, a, input, select, label, textarea") && !e.target.closest(".fold-arrow")) return;
    setFolded(box, !box.classList.contains("folded"));
  });
  return box;
}
export function setFolded(box, folded) {
  box.classList.toggle("folded", folded);
  if (box.dataset.foldKey) keepPick(`fold:${box.dataset.foldKey}`, !folded);
}

// A long form in pages: each group of fields (a nested fieldset) is a page of its own and the loose
// fields before it are "General"; numbered steps on top, Previous / Next below, and "All on one page"
// to see everything at once. Pages with a field in error get a red mark. Only forms with 3+ pages.
export function pageForm(fs, key, { minPages = 3, plan = null, obj = null, ctx = {} } = {}) {
  const grid = fs.querySelector(":scope > .fields");
  if (!grid) return fs;
  const keyOf = (el) => el.dataset.key || (el.dataset.path || "").split(".").pop();
  const titleOf = (el) => el.classList.contains("full") ? el.querySelector(":scope > fieldset > legend, :scope > .toggle")?.textContent.trim().split("\n")[0].trim() : null;
  const planned = (plan || []).map((p) => ({ ...p, els: [] }));
  const own = [];
  const rest = { title: plan ? "Other settings" : "General", els: [] };
  for (const el of [...grid.children]) {
    const k = keyOf(el);
    const home = planned.find((p) => p.keys.includes(k));
    const title = titleOf(el);
    if (home) home.els.push(el);
    else if (title) own.push({ title, els: [el] });
    else rest.els.push(el);
  }
  const pages = plan ? [...planned.filter((p) => p.els.length), ...own, ...(rest.els.length ? [rest] : [])]
    : [...(rest.els.length ? [rest] : []), ...own];
  if (pages.length < (plan ? 2 : minPages)) return fs;
  // Each planned page opens with a line on what it is for and, where one helps, a drawing that
  // follows the values as they are typed.
  const arts = [];
  for (const p of pages) {
    if (!p.intro && !p.art) continue;
    const box = document.createElement("div");
    box.className = "page-art full";
    grid.insertBefore(box, p.els[0]);
    p.els.unshift(box);
    const draw = () => {
      let pic = "";
      try {
        pic = p.art && obj ? p.art(obj, ctx) : "";
      } catch {
        pic = ""; // a drawing never stops the form
      }
      box.innerHTML = `${p.intro ? `<p class="page-intro">${esc(p.intro)}</p>` : ""}${pic ? `<div class="page-pic">${pic}</div>` : ""}`;
    };
    draw();
    if (p.art) arts.push(draw);
  }
  if (arts.length) {
    let queued = false;
    const redraw = () => {
      if (queued) return;
      queued = true;
      requestAnimationFrame(() => {
        queued = false;
        arts.forEach((d) => d());
      });
    };
    fs.addEventListener("input", redraw);
    fs.addEventListener("change", redraw);
  }
  fs.classList.add("paged");
  let cur = Math.min(picked(`page:${key}`) ?? 0, pages.length - 1);
  if (cur !== ALL && (cur < 0 || typeof cur !== "number")) cur = 0;
  const top = document.createElement("div");
  top.className = "page-steps";
  top.dataset.free = "";
  const bottom = document.createElement("div");
  bottom.className = "pick-nav page-nav";
  bottom.dataset.free = "";
  grid.before(top);
  grid.after(bottom);
  const draw = () => {
    pages.forEach((p, i) => p.els.forEach((el) => el.classList.toggle("page-off", cur !== ALL && i !== cur)));
    top.innerHTML = pages
      .map((p, i) => `<button type="button" class="page-step ${i === cur ? "on" : ""} ${p.els.some((e) => e.querySelector(".field.bad") || e.matches(".field.bad")) ? "bad" : ""}" data-i="${i}" data-free>
        <span class="n">${i + 1}</span>${esc(p.title)}</button>`)
      .join("") + `<button type="button" class="page-step all ${cur === ALL ? "on" : ""}" data-i="${ALL}" data-free>All on one page</button>`;
    bottom.innerHTML = cur === ALL
      ? `<button type="button" class="quiet" data-go="0" data-free>Back to one page at a time</button>`
      : `<button type="button" class="quiet" data-go="${cur - 1}" ${cur === 0 ? "disabled" : ""} data-free>&#9664; Previous</button>
         <span class="status">Page ${cur + 1} of ${pages.length}: ${esc(pages[cur].title)}</span>
         <button type="button" class="quiet" data-go="${cur + 1}" ${cur === pages.length - 1 ? "disabled" : ""} data-free>${cur < pages.length - 1 ? `Next: ${esc(pages[cur + 1].title)} &#9654;` : "Next &#9654;"}</button>`;
    top.querySelectorAll("[data-i]").forEach((b) => (b.onclick = () => go(b.dataset.i === ALL ? ALL : +b.dataset.i)));
    bottom.querySelectorAll("[data-go]").forEach((b) => (b.onclick = () => {
      go(+b.dataset.go);
      top.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }));
  };
  const go = (i) => {
    cur = i;
    keepPick(`page:${key}`, i);
    draw();
  };
  fs._marks = draw; // showErrors calls it, so a page with a bad field gets its red mark
  draw();
  return fs;
}
