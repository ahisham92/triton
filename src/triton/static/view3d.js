// A light 3D view of a section: piles and king piles as lines coloured by utilisation, plates as
// panels, orbit / pan / zoom on a 2D canvas (orthographic, no library).

const HEAT = [
  [0, [46, 157, 87]], // very safe: green
  [0.5, [214, 190, 44]], // yellow
  [0.75, [238, 138, 42]], // orange
  [1.0, [214, 48, 39]], // at the limit: red
];
const UNSAFE = [127, 0, 0];
const GREY = [170, 176, 184];

export function heat(u) {
  if (u == null || !isFinite(u)) return `rgb(${GREY})`;
  if (u > 1.0) return `rgb(${UNSAFE})`;
  for (let i = 1; i < HEAT.length; i++) {
    const [u1, c1] = HEAT[i];
    const [u0, c0] = HEAT[i - 1];
    if (u <= u1) {
      const t = (Math.max(u, 0) - u0) / (u1 - u0);
      return `rgb(${c0.map((c, k) => Math.round(c + t * (c1[k] - c))).join(",")})`;
    }
  }
  return `rgb(${HEAT[HEAT.length - 1][1]})`;
}

export function legendHtml(title = "Utilisation", extra = "") {
  const stops = HEAT.map(([u, c]) => `rgb(${c}) ${u * 90}%`).join(", ");
  return `<div class="heat-legend"><span>${title}</span>
    <div class="bar" style="background:linear-gradient(90deg, ${stops}, rgb(${UNSAFE}) 91%, rgb(${UNSAFE}))"></div>
    <div class="ticks"><span>0</span><span style="left:45%">0.5</span><span style="left:67.5%">0.75</span><span style="left:90%">1.0</span></div>
    <div class="grey"><i style="background:rgb(${UNSAFE})"></i> above 1.0: unsafe</div>
    <div class="grey"><i style="background:rgb(${GREY})"></i> ${extra ? "no crack check here (inside a steel casing) or not designed yet" : "not designed yet"}</div>
    <div class="grey"><i style="background:rgba(150,158,168,0.22);border:1px solid rgba(150,158,168,0.6)"></i> no colour on a slab or beam: over a pile head or king pile, where the results are FE peaks in the connection and are left out (bending is taken at its face)</div>${extra}</div>`;
}

export function crackLegendHtml() {
  return legendHtml(
    "Crack width w<sub>k</sub> / limit (QP)",
    `<div class="grey"><svg class="crack-mark" width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M5 1 L9 5 L5 9 L8 13"/></svg> w<sub>k</sub> over half the limit</div>
    <p>Worst of the QP combinations at each point, with the bars designed there (EN 1992-1-1 7.3.4). Beams: the tension face in vertical bending; slabs: the worst of the four bar layers. Open an element to see its crack pictures.</p>`
  );
}

// Tension zones: one colour per state (validated for colour-blind separation; tooltips name the state).
const TENSION = { none: "#c9e3cf", bottom: "#2563eb", top: "#d9730d", both: "#a3389f", whole: "#9f1d1d" };

// A corner berth's part is designed turned onto the quay's axis (triton/alignment.py): its box and
// bands come in that turned frame, and are turned back to plan here.
function turnBack(items, turn) {
  const a = (-turn.deg * Math.PI) / 180;
  const c = Math.cos(a), s = Math.sin(a);
  const [px, py] = turn.pivot;
  const back = (p) => {
    const dx = p[0] - px, dy = p[1] - py;
    return [px + c * dx - s * dy, py + s * dx + c * dy, p[2]];
  };
  for (const it of items) {
    if (it.pts) it.pts = it.pts.map(back);
    if (it.a) it.a = back(it.a);
    if (it.b) it.b = back(it.b);
    if (it.at) it.at = back(it.at);
  }
}

// State of a tension code (see triton/design/tension.py): [colour key, words].
export function tensionState(kind, code, dir = "x") {
  if (code == null) return null;
  if (kind === "pile") {
    if (code & 4) return ["whole", code & 1 ? "whole section in tension in some combinations, one side in others" : "whole section in tension"];
    if (code & 2) return ["both", "one side in tension; the side changes between combinations"];
    if (code & 1) return ["bottom", "one side in tension from bending"];
    return ["none", "all in compression"];
  }
  const b = kind === "slab" && dir === "y" ? (code >> 3) & 7 : code & 7;
  if (b & 4) return ["whole", "whole section in tension (net axial tension)"];
  if ((b & 3) === 3) return ["both", "bottom in tension in some combinations, top in others"];
  if (b & 1) return ["bottom", "bottom face in tension"];
  if (b & 2) return ["top", "top face in tension"];
  return ["none", "no tension"];
}

export function tensionLegendHtml() {
  const row = (k, text) => `<div class="grey"><i style="background:${TENSION[k]}"></i> ${text}</div>`;
  return `<div class="heat-legend tension-legend"><span>Tension zones</span>
    ${row("bottom", "Bottom face in tension · piles: one side in tension")}
    ${row("top", "Top face in tension")}
    ${row("both", "Bottom in some combinations, top in others · piles: the tension side changes")}
    ${row("whole", "Whole section in tension (net axial tension)")}
    ${row("none", "No tension: the whole section is in compression")}
    <div class="grey"><i style="background:rgb(${GREY})"></i> no result for this combination</div>
    <p>Stresses of the uncracked concrete, N/A ± M/W, from the ULS and QP results; tension below 0.1 MPa is ignored.
    Beams show vertical bending; slabs one bar direction at a time.</p></div>`;
}

// Displacement: pale to dark blue, apart from the utilisation colours.
const DEFORM = [
  [0, [158, 202, 225]],
  [0.5, [49, 130, 189]],
  [1, [8, 48, 107]],
];

function ramp(stops, u) {
  const t = Math.max(0, Math.min(1, u || 0));
  for (let i = 1; i < stops.length; i++) {
    const [u1, c1] = stops[i];
    const [u0, c0] = stops[i - 1];
    if (t <= u1) {
      const f = (t - u0) / (u1 - u0);
      return `rgb(${c0.map((c, k) => Math.round(c + f * (c1[k] - c))).join(",")})`;
    }
  }
  return `rgb(${stops[stops.length - 1][1]})`;
}

const fmt1 = (v) => (Math.abs(v) >= 10 ? v.toFixed(0) : v.toFixed(1));

// A box as six shaded faces, drawn among the lines (fenders, bollards, blocks).
function solid(items, box, color, tip) {
  const [x0, x1] = box.X;
  const [y0, y1] = box.Y;
  const [z0, z1] = box.Z;
  const shade = (f) => {
    const c = color.match(/\w\w/g).map((h) => Math.round(parseInt(h, 16) * f));
    return `rgb(${c.join(",")})`;
  };
  const faces = [
    [[x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1], 1.0],
    [[x0, y0, z0], [x1, y0, z0], [x1, y0, z1], [x0, y0, z1], 0.8],
    [[x0, y1, z0], [x1, y1, z0], [x1, y1, z1], [x0, y1, z1], 0.8],
    [[x0, y0, z0], [x0, y1, z0], [x0, y1, z1], [x0, y0, z1], 0.68],
    [[x1, y0, z0], [x1, y1, z0], [x1, y1, z1], [x1, y0, z1], 0.68],
    [[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0], 0.55],
  ];
  for (const [a, b, c, d, f] of faces) items.push({ kind: "quad", pts: [a, b, c, d], fill: shade(f), base: 0, stroke: false, site: true, tip });
}

// An element drawn extruded: its box's faces cut into tiles (so they sort among the piles), each face
// wound anticlockwise seen from outside so the faces turned away are left out when drawn.
const TILE = 2.5;
const AXES = { X: ["Y", "Z"], Y: ["Z", "X"], Z: ["X", "Y"] };
function extruded(box, color, common) {
  const tiles = [];
  const faces = {};
  const shade = { Z1: 1.0, Z0: 0.55, X0: 0.8, X1: 0.8, Y0: 0.68, Y1: 0.68 };
  for (const a of ["X", "Y", "Z"]) {
    const [u, v] = AXES[a];
    const cut = (k) => {
      const [lo, hi] = box[k];
      const n = Math.max(1, Math.ceil((hi - lo) / TILE - 1e-9));
      return Array.from({ length: n + 1 }, (_, i) => lo + ((hi - lo) * i) / n);
    };
    const us = cut(u);
    const vs = cut(v);
    for (const side of [0, 1]) {
      const c = color.match(/\w\w/g).map((h) => Math.round(parseInt(h, 16) * shade[a + side]));
      const fill = `rgb(${c.join(",")})`;
      const grid = [];
      for (let i = 1; i < us.length; i++) {
        const row = [];
        for (let j = 1; j < vs.length; j++) {
          const p = (uu, vv) => {
            const q = { [a]: box[a][side], [u]: uu, [v]: vv };
            return [q.X, q.Y, q.Z];
          };
          const pts = [p(us[i - 1], vs[j - 1]), p(us[i], vs[j - 1]), p(us[i], vs[j]), p(us[i - 1], vs[j])];
          const it = { kind: "quad", pts: side ? pts : pts.reverse(), fill, base: 0, stroke: false, cull: true, ...common };
          tiles.push(it);
          row.push(it);
        }
        grid.push(row);
      }
      faces[a + side] = { u, v, us, vs, grid };
    }
  }
  return { tiles, faces };
}

export function deformedLegendHtml(d) {
  const stops = DEFORM.map(([u, c]) => `rgb(${c}) ${u * 100}%`).join(", ");
  const max = fmt1(d.max_mm);
  const extra = [...(d.notes || []), ...(d.missing?.length ? [`No results for ${d.combination}: ${d.missing.join(", ")}.`] : [])];
  return `<div class="heat-legend"><span>Displacement, ${d.combination.replace(/</g, "&lt;")} (estimate)</span>
    <div class="bar" style="background:linear-gradient(90deg, ${stops})"></div>
    <div class="ticks"><span>0</span><span style="left:45%">${fmt1(d.max_mm / 2)}</span><span style="left:90%">${max} mm</span></div>
    <p>The whole structure moved by the Design tab's estimate (curvature M / EI integrated twice down each pile and wall,
    held as set there: ${d.settings?.toe === "tied" ? "tied at the deck" : d.settings?.toe === "firm_soil" ? "held at the toe and the firm soil level" : "fixed at the toe"}),
    ${d.settings?.baseline ? `the movement after ${d.settings.baseline.replace(/</g, "&lt;")},` : "the total movement,"} enlarged by the scale shown.
    The deck moves sideways with the pile heads (${fmt1(d.deck_shift_mm[0])} mm in X, ${fmt1(d.deck_shift_mm[1])} mm in Y) and bends
    between the supports of each 1 m strip. The soil's face against the wall follows the wall; the ground's own movement is not
    in the estimate. Not a Plaxis displacement result. Faint: the structure as built.</p>${extra.map((t) => `<p class="grey">${t.replace(/</g, "&lt;")}</p>`).join("")}</div>`;
}

const PRESETS = {
  "3D": { yaw: 0.7, pitch: 0.42 },
  Plan: { yaw: 0, pitch: Math.PI / 2 - 1e-4 },
  "From the sea": { yaw: 0, pitch: 0.12 },
  "Along the quay": { yaw: Math.PI / 2, pitch: 0.12 },
};

// Why a slab square shows the squares round it (triton/design/slabs.py GAP_WHY).
export const GAP_WHY = {
  pile: "Over a pile head: the results inside the pile are FE peaks in the connection and are left out, so this square shows the worst of the squares round it (the pile faces, where the bars are designed)",
  "no node": "No Plaxis node falls in this square (the Plaxis mesh is coarser than the 1 m grid here), so it shows the worst of the squares round it",
};

export class View3D {
  constructor(host, { height = 460, compact = false, legend = true, onSite = null, deformed = null } = {}) {
    this.host = host;
    this.legend = legend;
    this.onSite = onSite; // (changes) => saved to the section's site settings
    this.loadDeformed = deformed; // async (combination) => the section's deformed shape
    this.def = null;
    this.phase = 0;
    this.labels = !compact;
    host.classList.add("view3d");
    host.innerHTML = `<div class="v3d-bar">${Object.keys(PRESETS)
      .map((k) => `<button class="quiet" data-preset="${k}">${k}</button>`)
      .join("")}<button class="quiet" data-fit>Fit view</button>
      <label class="toggle"><input type="checkbox" data-labels ${this.labels ? "checked" : ""}> Labels</label>
      <span class="v3d-mode"><select data-mode aria-label="Colour by"><option value="util">Utilisation</option>
      <option value="tension">Tension zones</option><option value="crack">Crack width (QP)</option></select>
      <select data-combo aria-label="Combination" hidden></select>
      <select data-dir aria-label="Slab bar direction" hidden><option value="x">Slabs: bars along X (M11)</option>
      <option value="y">Slabs: bars along Y (M22)</option></select></span></div>
      <div class="v3d-bar v3d-site" hidden><label class="toggle"><input type="checkbox" data-show="extrude"> Extrude elements</label>
      <label class="toggle">Soil <select data-soil aria-label="Soil">
      <option value="hidden">Hidden</option><option value="half">50%</option><option value="full">Full</option></select></label>
      <label class="toggle"><input type="checkbox" data-show="water"> Water</label><span class="v3d-waters" data-waters></span>
      <label class="toggle"><input type="checkbox" data-show="furniture"> Fenders and bollards</label>
      <label class="toggle"><input type="checkbox" data-show="crane"> STS crane</label>
      <label class="toggle" data-exrow>Existing <select data-existing aria-label="Existing structure">
      <option value="show">Show</option><option value="see_through">See-through</option><option value="hidden">Hide</option></select></label>
      <span class="v3d-def" ${deformed ? "" : "hidden"}><select data-def aria-label="Deformed shape"><option value="">Deformed shape: off</option></select>
      <select data-scale aria-label="Displacement scale" hidden><option value="auto">Scale: auto</option>
      ${[1, 10, 50, 100, 200, 500, 1000, 2000].map((k) => `<option value="${k}">× ${k}</option>`).join("")}</select>
      <button class="quiet" data-anim hidden>Animate</button><span class="status" data-defstat></span></span></div>
      <canvas style="height:${height}px"></canvas><div class="v3d-tip" hidden></div><div class="v3d-legend"></div>`;
    this.canvas = host.querySelector("canvas");
    this.tip = host.querySelector(".v3d-tip");
    this.cam = { ...PRESETS["3D"], scale: 1, panX: 0, panY: 0 };
    this.items = [];
    host.querySelectorAll("[data-preset]").forEach((b) => (b.onclick = () => {
      Object.assign(this.cam, PRESETS[b.dataset.preset]);
      this.fit(this.fitExtra || []);
    }));
    host.querySelector("[data-fit]").onclick = () => this.fit(this.fitExtra || []);
    host.querySelector("[data-labels]").onchange = (e) => {
      this.labels = e.target.checked;
      this.draw();
    };
    const prefs = View3D.prefs;
    for (const key of ["mode", "combo", "dir"]) {
      host.querySelector(`[data-${key}]`).onchange = (e) => {
        prefs[key] = e.target.value;
        this._controls();
        this._build();
        this.draw();
      };
    }
    host.querySelector("[data-soil]").onchange = (e) => this._site({ soil: e.target.value });
    host.querySelector("[data-existing]").onchange = (e) => this._site({ existing: e.target.value });
    host.querySelectorAll("[data-show]").forEach((b) => (b.onchange = () => this._site({ [b.dataset.show]: b.checked })));
    host.querySelector("[data-def]").onchange = (e) => this._deform(e.target.value);
    host.querySelector("[data-scale]").onchange = (e) => {
      View3D.prefs.defScale = e.target.value;
      this.draw();
    };
    host.querySelector("[data-anim]").onclick = () => this._animate(!this.anim);
    this._wire();
    new ResizeObserver(() => this.draw()).observe(this.canvas);
  }

  // Site switches: drawn at once, and kept on the section (never a design input).
  _site(change) {
    Object.assign(this.siteView, change);
    if ("water" in change) this.host.querySelector("[data-waters]").hidden = !change.water;
    this.onSite?.(change);
    this._build();
    this.draw();
  }

  _siteControls() {
    const site = this.scene?.site;
    const bar = this.host.querySelector(".v3d-site");
    bar.hidden = !site?.soil && !this.loadDeformed;
    if (!site?.soil) {
      bar.querySelectorAll("label").forEach((l) => (l.hidden = true));
      return;
    }
    this.host.querySelector("[data-soil]").value = this.siteView.soil;
    this.host.querySelector("[data-existing]").value = this.siteView.existing || "see_through";
    this.host.querySelector("[data-exrow]").hidden = !site.existing;
    // Several named water levels: a switch for each.
    const waters = this.host.querySelector("[data-waters]");
    const hidden = new Set(this.siteView.water_hidden || []);
    const list = site.water || [];
    waters.innerHTML = list.length > 1
      ? list.map((w, i) => `<label class="toggle"><input type="checkbox" data-water="${i}" ${hidden.has(w.name) ? "" : "checked"}>
        ${w.name.replace(/</g, "&lt;")} (${w.level} m)</label>`).join("")
      : "";
    waters.hidden = !this.siteView.water;
    waters.querySelectorAll("[data-water]").forEach((b) => (b.onchange = () => {
      const off = list.filter((w, i) => !waters.querySelector(`[data-water="${i}"]`).checked).map((w) => w.name);
      this._site({ water_hidden: off });
    }));
    this.host.querySelectorAll("[data-show]").forEach((b) => {
      b.checked = !!this.siteView[b.dataset.show];
      b.closest("label").hidden = (b.dataset.show === "crane" && !site.crane) || (b.dataset.show === "furniture" && !site.furniture?.length);
    });
  }

  // The deformed shape for a combination ("" for none), loaded once per combination.
  async _deform(combo) {
    View3D.prefs.defCombo = combo;
    const stat = this.host.querySelector("[data-defstat]");
    this.def = null;
    if (combo && this.loadDeformed) {
      const began = Date.now();
      const tick = () => (stat.textContent = `Working out the deformed shape… ${Math.round((Date.now() - began) / 1000)} s`);
      tick();
      const timer = setInterval(tick, 1000);
      try {
        this.def = await this.loadDeformed(combo);
        stat.textContent = "";
      } catch (e) {
        stat.textContent = e.message;
      } finally {
        clearInterval(timer);
      }
    } else stat.textContent = "";
    this._defControls();
    this._controls();
    this._build();
    this.draw();
  }

  _defControls() {
    const sel = this.host.querySelector("[data-def]");
    const combos = this.def?.combinations || this.combos || [];
    if (combos.length) this.combos = combos;
    sel.innerHTML = `<option value="">Deformed shape: off</option>${(this.combos || [View3D.prefs.defCombo].filter(Boolean))
      .map((c) => `<option value="${c.replace(/"/g, "&quot;")}">Deformed: ${c.replace(/</g, "&lt;")}</option>`).join("")}`;
    sel.value = this.def ? this.def.combination : "";
    const on = !!this.def;
    this.host.querySelector("[data-scale]").hidden = !on;
    this.host.querySelector("[data-scale]").value = View3D.prefs.defScale;
    this.host.querySelector("[data-anim]").hidden = !on;
    if (!on) this._animate(false);
  }

  _animate(on) {
    this.anim = on;
    const b = this.host.querySelector("[data-anim]");
    b.textContent = on ? "Stop" : "Animate";
    if (!on) {
      this.phase = 0;
      this.draw();
      return;
    }
    let last = performance.now();
    const step = (t) => {
      if (!this.anim || !document.body.contains(this.canvas)) return (this.anim = false);
      this.phase += ((t - last) / 2000) * 2 * Math.PI; // one cycle in 2 s
      last = t;
      this.draw();
      requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  // scene: { elements: [{element, type, kind, lines?, box?}], bands: {element: [[x,y,z,u]]},
  //          selected: name|null, arrows: [{from:[x,y,z], dir:[x,y,z], label}], focus: name|null }
  //          tension: {element: {kind, points, combinations, codes}} for the "Tension zones" mode
  //          crack: {element: [[x,y,z,wk/limit,size?]]} for the "Crack width" mode
  setScene(scene) {
    this.scene = scene;
    this.siteView = { ...(scene.site?.site || {}) };
    this._siteControls();
    if (this.loadDeformed && !this.combos) {
      // The combinations to pick from, and the shape picked before on this page.
      this.combos = [];
      if (View3D.prefs.defCombo) this._deform(View3D.prefs.defCombo);
      else
        this.loadDeformed("")
          .then((d) => {
            this.combos = d?.combinations || [];
            this._defControls();
          })
          .catch(() => {});
    }
    this._controls();
    this._build();
    this.fit();
  }

  // A new scene drawn from the same camera and centre (an animation's next frame).
  update(scene) {
    const keep = this.center && { center: this.center, bounds: this.bounds, size: this.size };
    this.scene = scene;
    this._build();
    if (keep) Object.assign(this, keep);
    this.draw();
  }

  _controls() {
    const prefs = View3D.prefs;
    const tz = Object.values(this.scene?.tension || {});
    const combos = [...new Set(tz.flatMap((t) => t.combinations || []))];
    if (prefs.combo && !combos.includes(prefs.combo)) prefs.combo = "";
    const ck = Object.values(this.scene?.crack || {}).filter((b) => b?.length);
    const has = { util: true, tension: tz.length > 0, crack: ck.length > 0 };
    const mode = this.host.querySelector("[data-mode]");
    for (const o of mode.options) o.disabled = !has[o.value];
    mode.disabled = !has.tension && !has.crack;
    mode.title = mode.disabled ? "Run the design to see the tension zones and crack widths" : "";
    this.view = has[prefs.mode] ? prefs.mode : "util";
    mode.value = this.view;
    const on = this.view === "tension";
    const combo = this.host.querySelector("[data-combo]");
    combo.innerHTML = `<option value="">Envelope of all combinations</option>${combos
      .map((c) => `<option value="${c.replace(/"/g, "&quot;")}">${c.replace(/</g, "&lt;")}</option>`)
      .join("")}`;
    combo.value = prefs.combo;
    combo.hidden = !on;
    const dir = this.host.querySelector("[data-dir]");
    dir.value = prefs.dir;
    dir.hidden = !on || !tz.some((t) => t.kind === "slab");
    this.host.querySelector(".v3d-legend").innerHTML = this.def
      ? deformedLegendHtml(this.def)
      : on
      ? tensionLegendHtml()
      : this.view === "crack"
        ? crackLegendHtml()
        : this.legend
          ? legendHtml()
          : "";
  }

  // Bands of an element as [x, y, z, value, size?] and how to paint a value.
  _source(e) {
    if (this.view === "crack") {
      const pct = (u) => `crack width ${Math.round(u * 100)}% of the limit (worst QP combination)`;
      return { bands: this.scene.crack?.[e.key || e.element] || [], color: heat, words: pct };
    }
    if (this.view !== "tension") {
      return { bands: this.scene.bands?.[e.key || e.element] || [], color: heat, words: null };
    }
    const t = this.scene.tension?.[e.key || e.element];
    if (!t) return { bands: [], color: heat, words: null };
    const { combo, dir } = View3D.prefs;
    const codes = t.codes?.[combo] ?? null;
    const bands = t.points.map((p, i) => {
      const ch = codes ? codes[i] : " ";
      return [p[0], p[1], p[2], ch && ch !== " " ? ch.charCodeAt(0) - 48 : null, p[3], p[4]];
    });
    const state = (v) => tensionState(t.kind, v, dir);
    return {
      bands,
      color: (v) => (v == null ? `rgb(${GREY})` : TENSION[state(v)[0]]),
      words: (v) => (v == null ? "no result" : state(v)[1]) + ` (${combo || "envelope"})`,
    };
  }

  _build() {
    const { selected } = this.scene;
    const stage = this.scene.stage;
    // A construction stage: only what is built so far.
    const elements = stage ? this.scene.elements.filter((e) => stage.elements[e.element]) : this.scene.elements;
    const sizes = this.siteView?.extrude !== false ? this.scene.site?.sizes || null : null;
    this.tops = {};
    let items = [];
    for (const e of elements) {
      const faded = selected && e.element !== selected;
      const src = this._source(e);
      const first = items.length;
      if (e.lines) {
        const b = src.bands;
        const byPos = new Map();
        for (const [x, y, z, u] of b) {
          if (this.view !== "util" && u == null) continue;
          const k = `${x.toFixed(2)},${y.toFixed(2)}`;
          if (!byPos.has(k)) byPos.set(k, []);
          byPos.get(k).push([z, u]);
        }
        const width = e.type === "combi_wall" ? 6 : 4;
        e.lines.forEach(([x, y, top, bottom], i) => {
          const segs = (byPos.get(`${x.toFixed(2)},${y.toFixed(2)}`) || []).sort((a, c) => c[0] - a[0]);
          if (!segs.length) {
            items.push({ kind: "line", a: [x, y, top], b: [x, y, bottom], color: e.tint || `rgb(${GREY})`, width: e.thin ? 1.5 : width, faded, element: e.element, tip: e.tip, thin: e.thin });
          } else {
            // Undesigned ends (above the top level, below the last result) stay grey.
            const hi = Math.min(top, segs[0][0] + 0.25);
            if (top > hi + 0.01) items.push({ kind: "line", a: [x, y, top], b: [x, y, hi], color: `rgb(${GREY})`, width, faded, element: e.element });
            // Each band reaches halfway to its neighbours, so the pile reads as one continuous line.
            segs.forEach(([z, u], k) => {
              // A small overlap hides the seams between bands.
              const up = k ? (z + segs[k - 1][0]) / 2 + 0.04 : Math.min(z + 0.25, top);
              const down = k < segs.length - 1 ? (z + segs[k + 1][0]) / 2 - 0.04 : Math.max(z - 0.25, bottom);
              const what = src.words ? src.words(u) : `utilisation ${u.toFixed(2)}`;
              items.push({ kind: "line", a: [x, y, up], b: [x, y, down], color: src.color(u), width, faded, cap: "butt",
                element: e.element, tip: `${e.element} at X ${x}, Y ${y}, z ${z.toFixed(1)} m: ${what}` });
            });
            const last = segs[segs.length - 1][0] - 0.25;
            if (last > bottom + 0.01) items.push({ kind: "line", a: [x, y, last], b: [x, y, bottom], color: `rgb(${GREY})`, width, faded, element: e.element });
          }
          if (i === 0 && !e.nolabel) items.push({ kind: "label", at: [x, y, top], text: e.element, faded, element: e.element });
        });
      } else if (e.box) {
        const { X, Y, Z } = e.box;
        const flat = ["X", "Y", "Z"].find((a) => e.box[a][1] - e.box[a][0] < 0.05) || "Z";
        const c = { X, Y, Z };
        const at = (a, b) => {
          const p = {};
          const [u, v] = ["X", "Y", "Z"].filter((k) => k !== flat);
          p[flat] = c[flat][0];
          p[u] = c[u][a];
          p[v] = c[v][b];
          return [p.X, p.Y, p.Z];
        };
        items.push({ kind: "quad", pts: [at(0, 0), at(1, 0), at(1, 1), at(0, 1)], faded, element: e.element, under: true, tip: e.tip,
          fill: e.tint ? `${e.tint}55` : e.type === "sheet_pile_wall" ? "rgba(120,130,145,0.30)" : "rgba(150,158,168,0.22)" });
        const b = src.bands;
        if (b.length && flat === "Z") {
          // Beams: 0.5 m bands along the beam, across its full width.
          const along = Y[1] - Y[0] >= X[1] - X[0] ? "Y" : "X";
          const across = along === "Y" ? "X" : "Y";
          for (const [x, y, z, u, size, why] of b) {
            if (size && u == null) {
              // Slabs: a cell with no result, drawn outlined so it reads as left out, not as a gap.
              if (this.view !== "util") continue;
              const h = size / 2 - 0.04;
              items.push({ kind: "quad", pts: [[x - h, y - h, z], [x + h, y - h, z], [x + h, y + h, z], [x - h, y + h, z]],
                faded, element: e.element, fill: "rgba(150,158,168,0.35)", stroke: "rgba(110,118,128,0.8)",
                tip: `${e.element} at X ${x}, Y ${y}: ${why === "pile" ? "over a pile head: the results inside the pile are FE peaks in the connection and are left out; bending is taken at the pile face" : "no Plaxis node in this cell: nothing to design here; the basic mesh runs through it"}` });
              continue;
            }
            if (size) {
              // Slabs: square cells of the zone grid. A square with no result of its own (over a pile head,
              // or no Plaxis node in it) shows the worst of the squares round it; those over a pile are outlined.
              const h = size / 2 + 0.01;
              const what = src.words ? src.words(u) : `bending needs ${Math.round(u * 100)}% of the bars`;
              items.push({ kind: "quad", pts: [[x - h, y - h, z], [x + h, y - h, z], [x + h, y + h, z], [x - h, y + h, z]],
                faded, element: e.element, fill: src.color(u), stroke: why === "pile",
                tip: `${e.element} at X ${x}, Y ${y}: ${what}${why ? `. ${GAP_WHY[why] || ""}` : ""}` });
              continue;
            }
            const s0 = (along === "Y" ? y : x) - 0.27;
            const s1 = s0 + 0.54;
            const pt = (sv, tv) => (along === "Y" ? [tv, sv, z] : [sv, tv, z]);
            const [t0, t1] = c[across];
            items.push({ kind: "quad", pts: [pt(s0, t0), pt(s1, t0), pt(s1, t1), pt(s0, t1)], faded, element: e.element,
              fill: src.color(u), stroke: false,
              tip: `${e.element} at ${along} ${(along === "Y" ? y : x).toFixed(1)} m: ${src.words ? src.words(u) : `utilisation ${u.toFixed(2)}`}` });
          }
        } else if (b.length) {
          // Walls: 0.5 m bands down the wall, along its full length.
          const along = ["X", "Y"].find((a) => a !== flat);
          const [a0, a1] = c[along];
          const w = c[flat][0];
          const pt = (sv, zv) => (along === "Y" ? [w, sv, zv] : [sv, w, zv]);
          // Each band reaches halfway to its neighbours, so the wall reads as one surface.
          const lv = b.filter((q) => q[3] != null).sort((p, q) => q[2] - p[2]);
          lv.forEach(([, , z, u], k) => {
            const up = k ? (z + lv[k - 1][2]) / 2 + 0.02 : Math.min(z + 0.25, c.Z[1]);
            const down = k < lv.length - 1 ? (z + lv[k + 1][2]) / 2 - 0.02 : Math.max(z - 0.25, c.Z[0]);
            items.push({ kind: "quad", pts: [pt(a0, down), pt(a1, down), pt(a1, up), pt(a0, up)], faded,
              element: e.element, fill: src.color(u), stroke: false,
              tip: `${e.element} at z ${z.toFixed(1)} m: ${src.words ? src.words(u) : `utilisation ${u.toFixed(2)}`}` });
          });
        }
        const mid = ["X", "Y", "Z"].map((a) => (c[a][0] + c[a][1]) / 2);
        items.push({ kind: "label", at: mid, text: e.key || e.element, faded, element: e.element });
        if (sizes?.[e.element]?.t) this._extrude(items, first, e, sizes[e.element], faded);
      }
      if (e.lines && sizes?.[e.element]?.round && !e.thin)
        for (const it of items.slice(first)) if (it.kind === "line") Object.assign(it, { size: sizes[e.element].round, cap: "butt" });
      if (e.turn) turnBack(items.slice(first), e.turn);
    }
    if (this.view === "crack") {
      for (const e of elements) {
        const faded = selected && e.element !== selected;
        // Slab cells are small and many: one mark per 3 m block, at its widest crack.
        const blocks = new Map();
        const top = this.tops[e.key || e.element];
        for (const [x, y, z, u, size] of this.scene.crack?.[e.key || e.element] || []) {
          if (!(u >= 0.5)) continue;
          const key = size ? `${Math.floor(x / 3)},${Math.floor(y / 3)}` : `${x},${y},${z}`;
          const was = blocks.get(key);
          if (!was || u > was.u) blocks.set(key, { u, at: [x, y, top ?? z] });
        }
        const first = items.length;
        for (const { at, u } of blocks.values()) items.push({ kind: "mark", at, u, faded, element: e.element });
        if (e.turn) turnBack(items.slice(first), e.turn);
      }
    }
    for (const a of this.scene.arrows || []) items.push({ kind: "arrow", ...a });
    // Extra lines a tab adds (a pile cast above its cut-off, its head broken down) and pins (clashes).
    for (const x of this.scene.extras || []) items.push({ kind: "line", width: 4, cap: "butt", ...x });
    for (const p of this.scene.pins || []) items.push({ kind: "pin", ...p });
    // Solids a tab adds (the construction sequence's equipment and workers).
    for (const b of this.scene.solids || []) solid(items, b.box, b.color, b.tip);
    if (this.def) {
      // The structure as it stands stays faint behind its deformed shape.
      for (const it of items) if (it.element && it.kind !== "label") it.ghost = true;
      this._deformedItems(items);
    }
    if (this.scene.site?.soil) {
      this._siteItems(items);
      if (this.siteView.soil === "full") items = this._bury(items);
    }
    this.items = items;
    const pts = [];
    const focus = this.scene.focus;
    for (const it of items) {
      if (it.site || (focus && it.element && it.element !== focus)) continue;
      if (it.kind === "line") pts.push(it.a, it.b);
      if (it.kind === "quad") pts.push(...it.pts);
    }
    if (!pts.length) for (const it of items) if (it.kind === "line" && !it.site) pts.push(it.a, it.b);
    const lo = [0, 1, 2].map((k) => Math.min(...pts.map((p) => p[k])));
    const hi = [0, 1, 2].map((k) => Math.max(...pts.map((p) => p[k])));
    this.center = lo.map((l, k) => (l + hi[k]) / 2);
    this.bounds = { lo, hi };
    this.size = Math.max(...hi.map((h, k) => h - lo[k]), 1);
  }

  // A plate element drawn with its real thickness (site3d.sizes): its colour bands move onto the
  // top face (walls: both faces), each drawn with the tile it lies on.
  _extrude(items, first, e, sz, faded) {
    const own = items.splice(first);
    const c = { X: [...e.box.X], Y: [...e.box.Y], Z: [...e.box.Z] };
    const flat = ["X", "Y", "Z"].find((a) => c[a][1] - c[a][0] < 0.05) || "Z";
    const mid = (c[flat][0] + c[flat][1]) / 2;
    let t = sz.t;
    if (flat === "Z") {
      const hi = sz.top ?? (sz.at === "top" ? c.Z[1] : mid + t / 2);
      c.Z = [hi - t, hi];
      if (sz.w) {
        const across = c.X[1] - c.X[0] <= c.Y[1] - c.Y[0] ? "X" : "Y";
        const m = (c[across][0] + c[across][1]) / 2;
        c[across] = [m - sz.w / 2, m + sz.w / 2];
      }
      this.tops[e.key || e.element] = hi;
    } else {
      if (sz.w) t = sz.w; // a beam drawn as an upright plate: its width across
      c[flat] = [mid - t / 2, mid + t / 2];
    }
    const steel = e.type === "sheet_pile_wall";
    const { tiles, faces } = extruded(c, e.tint || (steel ? "#8a939e" : "#c6cacf"), { faded, element: e.element, tip: e.tip });
    items.push(...tiles);
    const lift = 0.004;
    for (const it of own) {
      if (it.kind === "label") {
        if (flat === "Z") it.at = [it.at[0], it.at[1], c.Z[1]];
        items.push(it);
        continue;
      }
      if (it.kind !== "quad" || it.under) continue;
      const sides = flat === "Z" ? [1] : [0, 1];
      for (const side of sides) {
        const k = flat === "X" ? 0 : flat === "Y" ? 1 : 2;
        const at = c[flat][side] + (side ? lift : -lift);
        // Cut at the tiles' edges: a band as long as the wall is drawn piece by piece, each piece with
        // the tile it lies on, so no nearer tile covers it.
        const f = faces[flat + side];
        const ax = { X: 0, Y: 1, Z: 2 };
        const span = (key) => [Math.min(...it.pts.map((p) => p[ax[key]])), Math.max(...it.pts.map((p) => p[ax[key]]))];
        const [u0, u1] = span(f.u);
        const [v0, v1] = span(f.v);
        for (let i = 1; i < f.us.length; i++) {
          const a0 = Math.max(u0, f.us[i - 1]), a1 = Math.min(u1, f.us[i]);
          if (a1 - a0 <= 1e-6) continue;
          for (let j = 1; j < f.vs.length; j++) {
            const b0 = Math.max(v0, f.vs[j - 1]), b1 = Math.min(v1, f.vs[j]);
            if (b1 - b0 <= 1e-6) continue;
            const pt = (uu, vv) => {
              const q = [0, 0, 0];
              q[k] = at;
              q[ax[f.u]] = uu;
              q[ax[f.v]] = vv;
              return q;
            };
            items.push({ ...it, pts: [pt(a0, b0), pt(a1, b0), pt(a1, b1), pt(a0, b1)], on: f.grid[i - 1][j - 1] });
          }
        }
      }
    }
  }

  // Where furniture sits: the drawn top of the slab or beam under a plan point (the nearest one for
  // what hangs off the face), so it rests on the concrete whether the elements are extruded or not.
  _seat() {
    const plates = [];
    for (const e of this.scene.elements) {
      if (!e.box || e.turn || e.box.Z[1] - e.box.Z[0] >= 0.05) continue;
      plates.push({ X: e.box.X, Y: e.box.Y, top: this.tops[e.key || e.element] ?? e.box.Z[1] });
    }
    if (!plates.length) return () => 0;
    const cope = this.scene.site.levels.cope;
    const gap = (p, x, y) => Math.hypot(Math.max(p.X[0] - x, 0, x - p.X[1]), Math.max(p.Y[0] - y, 0, y - p.Y[1]));
    return (x, y) => {
      const on = plates.filter((p) => gap(p, x, y) < 0.05);
      const top = on.length ? Math.max(...on.map((p) => p.top)) : plates.reduce((b, p) => (gap(p, x, y) < gap(b, x, y) ? p : b)).top;
      return top - cope;
    };
  }

  // The deformed shape (triton/deformed.py): each pile and king pile, the sheet pile wall's strips and
  // the deck's strips, drawn at their displaced positions (m; scaled when drawn).
  _deformedItems(items) {
    const d = this.def;
    const max = Math.max(d.max_mm, 1e-6);
    const col = (mm) => ramp(DEFORM, Math.abs(mm) / max);
    const m = (v) => v / 1000;
    const f1 = (v) => fmt1(v);
    for (const mb of d.members || []) {
      const pts = mb.points;
      const top = pts[pts.length - 1];
      const head = `head ${f1(top[1])} mm in X, ${f1(top[2])} mm in Y`;
      for (let i = 1; i < pts.length; i++) {
        const [z0, x0, y0] = pts[i - 1];
        const [z1, x1, y1] = pts[i];
        items.push({ kind: "line", a: [mb.x, mb.y, z0], b: [mb.x, mb.y, z1], da: [m(x0), m(y0), 0], db: [m(x1), m(y1), 0],
          color: col(Math.hypot(x1, y1)), width: mb.kind === "combi_wall" ? 5 : mb.kind === "sheet_pile_wall" ? 1.5 : 3.5, deformed: true,
          tip: `${mb.element} at X ${mb.x}, Y ${mb.y}, z ${z1.toFixed(1)} m: ${f1(x1)} mm in X, ${f1(y1)} mm in Y (${head}; ${d.combination}, estimate)` });
      }
    }
    for (const w of d.walls || []) {
      for (const st of w.strips) {
        const at = (z) => (w.across === "X" ? [w.position, st.at, z] : [st.at, w.position, z]);
        const du = (u) => (w.across === "X" ? [m(u), 0, 0] : [0, m(u), 0]);
        for (let i = 1; i < st.points.length; i++) {
          const [z0, u0] = st.points[i - 1];
          const [z1, u1] = st.points[i];
          items.push({ kind: "line", a: at(z0), b: at(z1), da: du(u0), db: du(u1), color: col(u1), width: 1.5, deformed: true,
            tip: `${w.element}, 1 m strip at ${w.along} ${st.at} m, z ${z1.toFixed(1)} m: ${f1(u1)} mm across the wall (${d.combination}, estimate)` });
        }
      }
    }
    const [sx, sy] = (d.deck_shift_mm || [0, 0]).map(m);
    for (const p of d.plates || []) {
      for (const st of p.strips) {
        const at = (s) => (p.along === "X" ? [s, st.at, p.z] : [st.at, s, p.z]);
        for (let i = 1; i < st.points.length; i++) {
          const [s0, w0] = st.points[i - 1];
          const [s1, w1] = st.points[i];
          const size = Math.hypot(d.deck_shift_mm[0], d.deck_shift_mm[1], w1);
          items.push({ kind: "line", a: at(s0), b: at(s1), da: [sx, sy, m(w0)], db: [sx, sy, m(w1)], color: col(size), width: 1.5,
            deformed: true, tip: `${p.element} at ${p.along} ${s1.toFixed(1)} m, ${p.across} ${st.at} m: ${f1(w1)} mm up, moved ${f1(d.deck_shift_mm[0])} mm in X and ${f1(d.deck_shift_mm[1])} mm in Y with the pile heads (${d.combination}, estimate)` });
        }
      }
    }
  }

  // How far the front wall moves at a level (m in X and Y), from the king piles, else the sheet pile
  // wall: the soil against the wall follows it.
  _wallMove() {
    const d = this.def;
    if (!d) return null;
    const bins = new Map();
    const add = (z, ux, uy) => {
      const k = Math.round(z * 2) / 2;
      const b = bins.get(k) || [0, 0, 0];
      bins.set(k, [b[0] + ux, b[1] + uy, b[2] + 1]);
    };
    for (const mb of d.members || []) if (mb.kind === "combi_wall") for (const [z, ux, uy] of mb.points) add(z, ux, uy);
    if (!bins.size)
      for (const mb of d.members || []) if (mb.kind === "sheet_pile_wall") for (const [z, ux, uy] of mb.points) add(z, ux, uy);
    if (!bins.size)
      for (const w of d.walls || [])
        for (const st of w.strips) for (const [z, u] of st.points) add(z, w.across === "X" ? u : 0, w.across === "Y" ? u : 0);
    if (!bins.size) return null;
    const lv = [...bins.entries()].sort((a, b) => a[0] - b[0]).map(([z, [x, y, n]]) => [z, x / n / 1000, y / n / 1000]);
    return (z) => {
      if (z <= lv[0][0] || z >= lv[lv.length - 1][0]) {
        const e = z <= lv[0][0] ? lv[0] : lv[lv.length - 1];
        return [e[1], e[2], 0];
      }
      const i = lv.findIndex((p) => p[0] >= z);
      const [za, xa, ya] = lv[i - 1];
      const [zb, xb, yb] = lv[i];
      const t = (z - za) / (zb - za);
      return [xa + t * (xb - xa), ya + t * (yb - ya), 0];
    };
  }

  // Seabed, soil, water, furniture and the STS crane (triton/site3d.py), as the section's switches say.
  _siteItems(items) {
    const S = this.scene.site;
    const v = this.siteView;
    const stage = this.scene.stage;
    // A construction stage: the seabed before or after the dredging.
    const L = stage?.seabed != null ? { ...S.levels, seabed: Math.min(stage.seabed, S.levels.ground) } : S.levels;
    const move = this._wallMove();
    const still = [0, 0, 0];
    const alpha = v.soil === "full" ? 0.92 : v.soil === "half" ? 0.22 : 0;
    const quad = (pts, fill, base, dpts, tip) =>
      items.push({ kind: "quad", pts, fill, base, dpts, stroke: false, site: true, tip });
    const up = (c, z) => [c[0], c[1], z];
    if (alpha) {
      const top = `rgba(181,150,105,${alpha})`;
      const side = `rgba(146,116,78,${alpha})`;
      for (let b of S.soil) {
        const [c0, c1, c2, c3] = b.corners; // c0, c1 on the sea side; c2, c3 inland
        const wall = b.part === "sea" ? [c2, c3] : [c0, c1];
        const dz = (c, z) => (move && wall.includes(c) ? move(z) : still);
        const face = (p, q, z0, z1) =>
          quad([up(p, z0), up(q, z0), up(q, z1), up(p, z1)], side, -4e6, [dz(p, z0), dz(q, z0), dz(q, z1), dz(p, z1)]);
        if (b.part === "sea" && stage) b = { ...b, top: L.seabed };
        quad([c0, c1, c2, c3].map((c) => up(c, b.top)), top, -4e6, [c0, c1, c2, c3].map((c) => dz(c, b.top)),
          b.part === "sea" ? `Seabed at ${L.seabed} m` : `Soil behind the wall at ${L.ground} m (${L.ground_from})`);
        face(c3, c0, b.bottom, b.top);
        face(c1, c2, b.bottom, b.top);
        if (b.part === "sea") face(c0, c1, b.bottom, b.top);
        else {
          face(c2, c3, b.bottom, b.top);
          // The soil's face against the wall, in bands so it can follow the wall's deformed shape.
          const n = Math.max(1, Math.ceil((b.top - L.seabed) / 1.0));
          for (let i = 0; i < n; i++) {
            const z0 = L.seabed + ((b.top - L.seabed) * i) / n;
            face(c0, c1, z0, L.seabed + ((b.top - L.seabed) * (i + 1)) / n);
          }
        }
      }
    }
    const shown = v.water ? (S.water || []).filter((w) => !(v.water_hidden || []).includes(w.name)) : [];
    shown.forEach((wl, i) => {
      // Highest first: each level a plane, the sea's sides up to the highest one shown.
      const [c0, c1, c2, c3] = wl.corners;
      const w = wl.level;
      const dz = (c) => (move && (c === c2 || c === c3) ? move(w) : still);
      const a = Math.max(0.16, 0.3 - 0.05 * i);
      quad([c0, c1, c2, c3].map((c) => up(c, w)), `rgba(56,132,200,${a})`, -3e6 + i, [c0, c1, c2, c3].map(dz), `${wl.name}: ${w} m`);
      items.push({ kind: "line", a: up(c0, w), b: up(c1, w), color: "rgba(30,90,160,0.8)", width: 1.5, site: true, tip: `${wl.name}: ${w} m` });
      // Tidal levels lie close together: their labels spread along the sea edge so they do not overlap.
      const f = shown.length > 1 ? i / shown.length : 0;
      const spot = [c0[0] + f * (c1[0] - c0[0]), c0[1] + f * (c1[1] - c0[1])];
      if (shown.length > 1) items.push({ kind: "label", at: up(spot, w), text: `${wl.name} ${w} m`, site: true });
      if (i) return;
      const bed = L.seabed;
      for (const [p, q] of [[c0, c1], [c3, c0], [c1, c2]])
        quad([up(p, bed), up(q, bed), up(q, w), up(p, w)], "rgba(56,132,200,0.12)", -3e6);
    });
    if (S.existing && v.existing !== "hidden") this._existingItems(items, S.existing, v.existing === "show" ? 1 : 0.3, stage);
    const seat = this._seat();
    const lower = (p, dz) => [p[0], p[1], p[2] + dz];
    // A construction stage: only the furniture installed so far (stage.furniture: the kinds; true on an
    // older stage), the ones of the work under way only up to where it has got along the berth, and the
    // STS crane shifted out to sea while it is being pushed off its ship.
    const fr = S.frame;
    const sOf = (x, y) => (fr ? ({ X: x, Y: y })[fr.along] - fr.start : 0);
    const has = (k) => !stage || stage.furniture === true || (Array.isArray(stage.furniture) && stage.furniture.includes(k));
    const upTo = (k, x, y) => !stage?.furniture_upto || !stage.furniture_upto.kinds.includes(k) || sOf(x, y) <= stage.furniture_upto.s;
    if (v.furniture) {
      for (const f of S.furniture || []) {
        if (!has(f.kind)) continue;
        if (f.line) {
          if (!upTo(f.kind, f.line[0][0], f.line[0][1])) continue;
          const dz = seat(f.line[0][0], f.line[0][1]);
          items.push({ kind: "line", a: lower(f.line[0], dz), b: lower(f.line[1], dz), color: "#b45309", width: 2, site: true, tip: f.label });
          continue;
        }
        if (!upTo(f.kind, (f.box.X[0] + f.box.X[1]) / 2, (f.box.Y[0] + f.box.Y[1]) / 2)) continue;
        const dz = seat((f.box.X[0] + f.box.X[1]) / 2, (f.box.Y[0] + f.box.Y[1]) / 2);
        const color = f.kind === "fenders" ? (/panel/.test(f.label) ? "#d4a017" : "#2f3337")
          : f.kind === "fender_blocks" ? "#aab0b8" : f.kind === "bollards" ? "#4b5563"
          : f.kind === "crane_stoppers" ? "#b91c1c" : f.kind === "tie_downs" ? "#0f766e" : f.kind === "storm_pins" ? "#7c3aed" : "#6b7280";
        solid(items, { ...f.box, Z: [f.box.Z[0] + dz, f.box.Z[1] + dz] }, color, f.label);
      }
      if (has("crane_rails"))
        for (const r of S.rails || []) {
          const dz = seat((r.line[0][0] + r.line[1][0]) / 2, (r.line[0][1] + r.line[1][1]) / 2);
          let b = r.line[1];
          const lim = stage?.furniture_upto?.kinds.includes("crane_rails") ? stage.furniture_upto.s : null;
          if (lim != null && fr) {
            const s0 = sOf(r.line[0][0], r.line[0][1]);
            const s1 = sOf(b[0], b[1]);
            if (lim <= s0) continue;
            const k = Math.min(1, (lim - s0) / (s1 - s0 || 1));
            b = r.line[0].map((c, i) => c + (r.line[1][i] - c) * k);
          }
          items.push({ kind: "line", a: lower(r.line[0], dz), b: lower(b, dz), color: "#374151", width: 2.5, site: true, tip: r.label });
        }
    }
    if (v.crane && S.crane && has("sts_crane")) {
      const [x, y] = S.crane.lines[0][0]; // the foot of a sea-side leg
      const dz = seat(x, y);
      // Out to sea by crane_shift m (on its ship), and up by crane_lift m (the ship's deck above the rail).
      const sea = { X: 0, Y: 0 };
      if (fr && stage?.crane_shift) sea[fr.across] = -fr.inland * stage.crane_shift;
      const up = stage?.crane_lift || 0;
      const move = (p) => [p[0] + sea.X, p[1] + sea.Y, p[2] + dz + up];
      for (const [a, b] of S.crane.lines)
        items.push({ kind: "line", a: move(a), b: move(b), color: "#1d4ed8", width: 2.5, site: true, tip: S.crane.label });
    }
  }

  // The existing structure (triton/existing.py): solid or see-through; what the demolition takes away
  // is gone from that stage of the construction sequence on.
  _existingItems(items, ex, alpha, stage) {
    const COLOR = { capping_beam: "#a8a29e", slab: "#b8b2aa", anchor: "#a8a29e", combi_wall: "#6b7280", piles: "#8d99a6", tie_rods: "#c2410c",
      blocks: "#9ca3af", quarry_run: "#a3824f", warehouse: "#94a3b8" };
    for (let o of ex.objects || []) {
      if (stage?.demolished && o.removed_by === "demolition") continue;
      if (stage?.demolish_t != null && o.removed_by === "demolition" && o.shape === "box") {
        // Being demolished: what is left, from the breaker along the berth.
        const a = this.scene.site.frame.along;
        const [lo, hi] = o.box[a];
        const cut = lo + stage.demolish_t * (hi - lo);
        if (cut >= hi - 1e-6) continue;
        o = { ...o, box: { ...o.box, [a]: [cut, hi] } };
      }
      const color = COLOR[o.part] || "#9ca3af";
      const tip = `${o.label} (existing)`;
      const first = items.length;
      if (o.shape === "line") {
        items.push({ kind: "line", a: o.a, b: o.b, color, width: o.part === "tie_rods" ? 1.5 : 3, size: o.part === "tie_rods" ? null : o.size,
          cap: "butt", site: true, tip });
      } else if (o.shape === "box") {
        solid(items, o.box, color, tip);
      } else if (o.shape === "faces") {
        o.faces.forEach((pts, i) => {
          const f = [1.0, 0.8, 0.68, 0.55][i % 4];
          const c = color.match(/\w\w/g).map((h) => Math.round(parseInt(h, 16) * f));
          items.push({ kind: "quad", pts, fill: `rgb(${c.join(",")})`, base: 0, stroke: false, site: true, tip });
        });
      }
      for (const it of items.slice(first)) Object.assign(it, { alpha, existing: true });
    }
  }

  // Full soil: the parts of the piles and walls in the ground are drawn faint.
  _bury(items) {
    const S = this.scene.site;
    const fr = S.frame;
    const L = S.levels;
    const ground = (p) => {
      const d = ((fr.across === "X" ? p[0] : p[1]) - fr.face) * fr.inland;
      return d < S.wall.d + 0.5 ? L.seabed : L.ground;
    };
    const out = [];
    const focus = this.scene.focus; // an element's own view: the element itself stays clear
    for (const it of items) {
      if (it.site || (it.kind !== "line" && it.kind !== "quad") || (focus && it.element === focus)) {
        out.push(it);
        continue;
      }
      if (it.kind === "quad") {
        const g = ground(it.pts[0]);
        // The same object: colour bands find the extruded tile they lie on by it.
        if (it.pts.every((p) => p[2] <= g + 1e-6)) it.buried = true;
        out.push(it);
        continue;
      }
      const g = ground(it.a);
      const [za, zb] = [it.a[2], it.b[2]];
      if (Math.min(za, zb) >= g - 1e-6) out.push(it);
      else if (Math.max(za, zb) <= g + 1e-6) out.push({ ...it, buried: true });
      else {
        const t = (g - za) / (zb - za);
        const at = (p, q) => p.map((x, k) => x + t * (q[k] - x));
        const mid = at(it.a, it.b);
        const dmid = it.da ? at(it.da, it.db) : undefined;
        out.push({ ...it, b: mid, db: dmid, buried: za < g }, { ...it, a: mid, da: dmid, buried: zb < g });
      }
    }
    return out;
  }

  // How much the displacements are enlarged: auto draws the largest as 6% of the model's size.
  _defScale() {
    if (!this.def) return 0;
    const pick = View3D.prefs.defScale;
    if (pick !== "auto") return Number(pick) || 1;
    const big = Math.max(this.def.max_mm / 1000, 1e-9);
    const raw = (0.06 * this.size) / big;
    const p = 10 ** Math.floor(Math.log10(raw));
    return [1, 2, 5, 10].map((k) => k * p).filter((k) => k <= raw).pop() || p;
  }

  _basis() {
    const { yaw, pitch } = this.cam;
    const right = [-Math.sin(yaw), Math.cos(yaw), 0];
    const up = [-Math.sin(pitch) * Math.cos(yaw), -Math.sin(pitch) * Math.sin(yaw), Math.cos(pitch)];
    const toward = [Math.cos(pitch) * Math.cos(yaw), Math.cos(pitch) * Math.sin(yaw), Math.sin(pitch)];
    return { right, up, toward };
  }

  _project(p) {
    const d = [p[0] - this.center[0], p[1] - this.center[1], p[2] - this.center[2]];
    const { right, up, toward } = this._basis();
    const dot = (a) => a[0] * d[0] + a[1] * d[1] + a[2] * d[2];
    const s = this.cam.scale;
    return [this.w / 2 + this.cam.panX + dot(right) * s, this.h / 2 + this.cam.panY - dot(up) * s, dot(toward)];
  }

  // extra: more points to keep in view (the construction sequence's ship and crane).
  fit(extra = this.fitExtra || []) {
    this._size();
    const { lo, hi } = this.bounds;
    const corners = [...extra];
    for (const x of [lo[0], hi[0]]) for (const y of [lo[1], hi[1]]) for (const z of [lo[2], hi[2]]) corners.push([x, y, z]);
    this.cam.scale = 1;
    this.cam.panX = this.cam.panY = 0;
    const sp = corners.map((c) => this._project(c));
    const wx = Math.max(...sp.map((p) => p[0])) - Math.min(...sp.map((p) => p[0]));
    const wy = Math.max(...sp.map((p) => p[1])) - Math.min(...sp.map((p) => p[1]));
    this.cam.scale = 0.82 * Math.min(this.w / Math.max(wx, 1e-6), this.h / Math.max(wy, 1e-6));
    if (extra.length) {
      // Centred on everything to keep in view, not on the structure.
      const mx = (Math.max(...sp.map((p) => p[0])) + Math.min(...sp.map((p) => p[0]))) / 2 - this.w / 2;
      const my = (Math.max(...sp.map((p) => p[1])) + Math.min(...sp.map((p) => p[1]))) / 2 - this.h / 2;
      this.cam.panX = -mx * this.cam.scale;
      this.cam.panY = -my * this.cam.scale;
    }
    this.draw();
  }

  _size() {
    const r = this.canvas.getBoundingClientRect();
    const dpr = this.pixelRatio || window.devicePixelRatio || 1; // pixelRatio: sharper for a recording
    this.w = r.width || 600;
    this.h = r.height || 400;
    if (this.canvas.width !== Math.round(this.w * dpr)) {
      this.canvas.width = Math.round(this.w * dpr);
      this.canvas.height = Math.round(this.h * dpr);
    }
    this.dpr = dpr;
  }

  draw() {
    if (!this.scene) return;
    this._size();
    const ctx = this.canvas.getContext("2d");
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, this.w, this.h);
    const css = getComputedStyle(this.host);
    // A solid background, so a recorded video is not black behind the model.
    const bg = getComputedStyle(this.canvas).backgroundColor;
    ctx.fillStyle = bg && bg !== "rgba(0, 0, 0, 0)" && bg !== "transparent" ? bg : css.getPropertyValue("--panel").trim() || "#fff";
    ctx.fillRect(0, 0, this.w, this.h);
    const ink = css.getPropertyValue("--text").trim() || "#1d1d1b";
    const muted = css.getPropertyValue("--muted").trim() || "#6b6b66";
    const drawn = [];
    const k = this._defScale() * (this.anim ? Math.sin(this.phase) : 1);
    const P = (p, d) => this._project(d && k ? [p[0] + d[0] * k, p[1] + d[1] * k, p[2] + d[2] * k] : p);
    for (const it of this.items) {
      if (it.kind === "line") {
        const a = P(it.a, it.da);
        const b = P(it.b, it.db);
        drawn.push({ it, depth: (a[2] + b[2]) / 2, a, b });
      } else if (it.kind === "quad") {
        const ps = it.pts.map((p, i) => P(p, it.dpts?.[i]));
        if (it.cull) {
          // An extruded face turned away from the eye (screen y runs down): not drawn.
          let area = 0;
          ps.forEach((p, i) => {
            const q = ps[(i + 1) % ps.length];
            area += p[0] * q[1] - q[0] * p[1];
          });
          it.hidden = area > -1e-9;
          if (it.hidden) continue;
        }
        if (it.on?.hidden) continue;
        // Panels behind lines; the soil and the water behind every panel; solids among the lines.
        const base = it.base ?? (it.under ? -2e6 : -1e6);
        const depth = ps.reduce((s, p) => s + p[2], 0) / ps.length + base;
        it.depth = depth;
        drawn.push({ it, depth, ps });
      }
    }
    // Colour bands on an extruded face: drawn just after the tile they lie on.
    for (const d of drawn) if (d.it.on?.depth != null) d.depth = d.it.on.depth + 1e-3;
    if (this.def) {
      const stat = this.host.querySelector("[data-defstat]");
      const text = `× ${Math.round(this._defScale()).toLocaleString()} · largest ${fmt1(this.def.max_mm)} mm`;
      if (stat.textContent !== text && !stat.textContent.startsWith("Working")) stat.textContent = text;
    }
    drawn.sort((p, q) => p.depth - q.depth);
    this.hits = [];
    for (const d of drawn) {
      ctx.globalAlpha = d.it.faded ? 0.18 : d.it.buried ? 0.12 : d.it.ghost ? 0.22 : d.it.alpha ?? 1;
      if (d.it.kind === "quad") {
        ctx.beginPath();
        d.ps.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])));
        ctx.closePath();
        ctx.fillStyle = d.it.fill;
        ctx.fill();
        if (d.it.stroke !== false) {
          ctx.strokeStyle = muted;
          ctx.lineWidth = 1;
          ctx.stroke();
        }
        if (d.it.tip && !d.it.faded && !d.it.ghost) this.hits.push(d);
      } else {
        ctx.beginPath();
        ctx.moveTo(d.a[0], d.a[1]);
        ctx.lineTo(d.b[0], d.b[1]);
        ctx.strokeStyle = d.it.color;
        // Extruded piles and king piles: as wide as their diameter.
        ctx.lineWidth = d.it.size ? Math.max(d.it.width, d.it.size * this.cam.scale) : d.it.width;
        // Faint lines in pieces would show their round ends overlapping as dots.
        ctx.lineCap = d.it.buried || d.it.ghost ? "butt" : d.it.cap || "round";
        ctx.stroke();
        if (d.it.tip && !d.it.faded && !d.it.ghost) this.hits.push(d);
      }
    }
    // Pins on top: a clash or a warning at a point.
    for (const it of this.items) {
      if (it.kind !== "pin") continue;
      const [px, py] = this._project(it.at);
      ctx.globalAlpha = 1;
      ctx.beginPath();
      ctx.arc(px, py, 6, 0, 2 * Math.PI);
      ctx.fillStyle = it.level === "clash" ? "#dc2626" : "#d97706";
      ctx.fill();
      ctx.strokeStyle = "#fff";
      ctx.lineWidth = 2;
      ctx.stroke();
      if (it.tip) this.hits.push({ it, a: [px, py], b: [px, py] });
    }
    // Crack marks on top: a small zigzag where wk is over half the limit.
    ctx.lineCap = ctx.lineJoin = "round";
    const marked = [];
    const marks = this.items.filter((it) => it.kind === "mark").sort((p, q) => (p.faded - q.faded) || q.u - p.u);
    for (const it of marks) {
      const [px, py] = this._project(it.at);
      // Marks closer than 18 px on screen would pile up: the first one stands for them.
      if (marked.some(([mx, my]) => Math.abs(mx - px) < 18 && Math.abs(my - py) < 18)) continue;
      marked.push([px, py]);
      ctx.globalAlpha = it.faded ? 0.18 : 1;
      const zig = () => {
        ctx.beginPath();
        ctx.moveTo(px - 2, py - 6);
        ctx.lineTo(px + 2, py - 2);
        ctx.lineTo(px - 2, py + 2);
        ctx.lineTo(px + 1, py + 6);
      };
      zig();
      ctx.strokeStyle = "rgba(255,255,255,0.9)";
      ctx.lineWidth = 3.5;
      ctx.stroke();
      zig();
      ctx.strokeStyle = ink;
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
    ctx.font = "12px system-ui, sans-serif";
    if (this.labels) {
      for (const it of this.items) {
        if (it.kind !== "label" || it.faded) continue;
        const p = this._project(it.at);
        ctx.fillStyle = ink;
        ctx.fillText(it.text, p[0] + 6, p[1] - 6);
      }
    }
    for (const it of this.items) if (it.kind === "arrow") this._arrow(ctx, it, ink);
    // A caption on the picture itself (a recorded video carries it).
    const cap = this.scene.caption;
    if (cap?.length) {
      ctx.font = "600 15px system-ui, sans-serif";
      const w = Math.max(...cap.map((c, i) => (ctx.font = i ? "12px system-ui, sans-serif" : "600 15px system-ui, sans-serif", ctx.measureText(c).width)));
      ctx.globalAlpha = 0.88;
      ctx.fillStyle = getComputedStyle(this.host).getPropertyValue("--panel").trim() || "#fff";
      ctx.fillRect(10, 10, w + 20, 18 + cap.length * 18);
      ctx.globalAlpha = 1;
      ctx.fillStyle = ink;
      cap.forEach((c, i) => {
        ctx.font = i ? "12px system-ui, sans-serif" : "600 15px system-ui, sans-serif";
        ctx.fillText(c, 20, 32 + i * 18);
      });
    }
  }

  _arrow(ctx, a, ink) {
    const len = this.size * 0.22;
    const from = this._project(a.from);
    const to = this._project(a.from.map((v, k) => v + a.dir[k] * len));
    const dx = to[0] - from[0];
    const dy = to[1] - from[1];
    const n = Math.hypot(dx, dy);
    ctx.strokeStyle = ctx.fillStyle = ink;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(from[0], from[1]);
    ctx.lineTo(to[0], to[1]);
    ctx.stroke();
    if (n > 4) {
      const ux = dx / n, uy = dy / n;
      ctx.beginPath();
      ctx.moveTo(to[0], to[1]);
      ctx.lineTo(to[0] - 10 * ux - 5 * uy, to[1] - 10 * uy + 5 * ux);
      ctx.lineTo(to[0] - 10 * ux + 5 * uy, to[1] - 10 * uy - 5 * ux);
      ctx.closePath();
      ctx.fill();
    } else {
      // Pointing straight at the viewer: a dot.
      ctx.beginPath();
      ctx.arc(to[0], to[1], 4, 0, 2 * Math.PI);
      ctx.fill();
    }
    ctx.font = "600 12px system-ui, sans-serif";
    const w = ctx.measureText(a.label).width;
    const lx = Math.max(4, Math.min(this.w - w - 4, to[0] + (n > 4 ? (dx / n) * 8 : 8) - (dx < -4 ? w : 0)));
    const ly = Math.max(14, Math.min(this.h - 4, to[1] + (n > 4 ? (dy / n) * 12 : -8) + 4));
    ctx.fillStyle = getComputedStyle(this.host).getPropertyValue("--panel").trim() || "#fff";
    ctx.globalAlpha = 0.85;
    ctx.fillRect(lx - 3, ly - 12, w + 6, 16);
    ctx.globalAlpha = 1;
    ctx.fillStyle = ink;
    ctx.fillText(a.label, lx, ly);
  }

  _wire() {
    let drag = null;
    const c = this.canvas;
    c.addEventListener("pointerdown", (e) => {
      drag = { x: e.clientX, y: e.clientY, pan: e.shiftKey || e.button === 2, cam: { ...this.cam } };
      c.setPointerCapture(e.pointerId);
    });
    c.addEventListener("pointermove", (e) => {
      if (drag) {
        const dx = e.clientX - drag.x;
        const dy = e.clientY - drag.y;
        if (drag.pan) {
          this.cam.panX = drag.cam.panX + dx;
          this.cam.panY = drag.cam.panY + dy;
        } else {
          this.cam.yaw = drag.cam.yaw - dx * 0.008;
          this.cam.pitch = Math.max(-0.2, Math.min(Math.PI / 2 - 1e-4, drag.cam.pitch + dy * 0.008));
        }
        this.draw();
        return;
      }
      this._hover(e);
    });
    c.addEventListener("pointerup", () => (drag = null));
    c.addEventListener("pointerleave", () => (this.tip.hidden = true));
    c.addEventListener("contextmenu", (e) => e.preventDefault());
    c.addEventListener("wheel", (e) => {
      e.preventDefault();
      const r = c.getBoundingClientRect();
      const mx = e.clientX - r.left - this.w / 2 - this.cam.panX;
      const my = e.clientY - r.top - this.h / 2 - this.cam.panY;
      const f = Math.exp(-e.deltaY * 0.0015);
      this.cam.scale *= f;
      this.cam.panX -= mx * (f - 1);
      this.cam.panY -= my * (f - 1);
      this.draw();
    }, { passive: false });
  }

  _hover(e) {
    const r = this.canvas.getBoundingClientRect();
    const x = e.clientX - r.left;
    const y = e.clientY - r.top;
    let best = null;
    let band = null; // lines drawn over a beam win over its bands
    let bd = 8;
    for (const d of this.hits || []) {
      if (d.ps) {
        // A band of a beam: inside its outline.
        let inside = false;
        for (let i = 0, j = d.ps.length - 1; i < d.ps.length; j = i++) {
          const [xi, yi] = d.ps[i];
          const [xj, yj] = d.ps[j];
          if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
        }
        if (inside) band = d;
        continue;
      }
      const [ax, ay] = d.a;
      const [bx, by] = d.b;
      const l2 = (bx - ax) ** 2 + (by - ay) ** 2 || 1;
      const t = Math.max(0, Math.min(1, ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / l2));
      const dist = Math.hypot(x - (ax + t * (bx - ax)), y - (ay + t * (by - ay)));
      if (dist < bd) {
        bd = dist;
        best = d;
      }
    }
    best = best || band;
    if (!best) {
      this.tip.hidden = true;
      return;
    }
    this.tip.hidden = false;
    this.tip.textContent = best.it.tip;
    this.tip.style.left = `${Math.min(x + 12, this.w - 260)}px`;
    this.tip.style.top = `${y + 36}px`;
  }
}

// Arrows showing which way each action of an element acts, from the workbook's direction check.
export function directionArrows(el, finding) {
  // A corner berth's turned part: the arrows stand on its box turned back to plan; Plaxis's local
  // axes stay global, so their directions do not turn.
  const list = _directionArrows(el, finding);
  if (el?.turn) {
    const items = list.map((a) => ({ at: a.from }));
    turnBack(items, el.turn);
    list.forEach((a, k) => (a.from = items[k].at));
  }
  return list;
}

function _directionArrows(el, finding) {
  if (!el || !finding) return [];
  const unit = { X: [1, 0, 0], Y: [0, 1, 0], Z: [0, 0, 1] };
  if (el.lines && finding.kind === "beam") {
    const [x, y, top] = el.lines[0];
    const from = [x, y, top];
    const loc = finding.local; // {"2": "X", "3": "Y"} when found
    const a2 = loc?.["2"] || "X";
    const a3 = loc?.["3"] || "Y";
    return [
      { from, dir: [0, 0, -1], label: "1 (along the pile): N" },
      { from, dir: unit[a2], label: `2 → ${a2}: Q12; M3 bends in ${a2}–Z` },
      { from, dir: unit[a3], label: `3 → ${a3}: Q13; M2 bends in ${a3}–Z` },
    ];
  }
  if (el.box && finding.kind === "plate") {
    const b = el.box;
    const mid = ["X", "Y", "Z"].map((a) => (b[a][0] + b[a][1]) / 2);
    const one = finding.local["1"];
    const two = finding.local["2"];
    const normal = ["X", "Y", "Z"].find((a) => a !== one && a !== two);
    return [
      { from: mid, dir: unit[one], label: `1 → ${one}: N1, M11 (bars along ${one}), Q13` },
      { from: mid, dir: unit[two], label: `2 → ${two}: N2, M22 (bars along ${two}), Q23` },
      { from: mid, dir: unit[normal], label: `3 → ${normal}: out of plane` },
    ];
  }
  return [];
}

// Shared by every view on the page, so a choice made in one view carries to the next.
View3D.prefs = { mode: "util", combo: "", dir: "x", defCombo: "", defScale: "auto" };
