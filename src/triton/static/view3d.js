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

const PRESETS = {
  "3D": { yaw: 0.7, pitch: 0.42 },
  Plan: { yaw: 0, pitch: Math.PI / 2 - 1e-4 },
  "From the sea": { yaw: 0, pitch: 0.12 },
  "Along the quay": { yaw: Math.PI / 2, pitch: 0.12 },
};

export class View3D {
  constructor(host, { height = 460, compact = false, legend = true } = {}) {
    this.host = host;
    this.legend = legend;
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
      <canvas style="height:${height}px"></canvas><div class="v3d-tip" hidden></div><div class="v3d-legend"></div>`;
    this.canvas = host.querySelector("canvas");
    this.tip = host.querySelector(".v3d-tip");
    this.cam = { ...PRESETS["3D"], scale: 1, panX: 0, panY: 0 };
    this.items = [];
    host.querySelectorAll("[data-preset]").forEach((b) => (b.onclick = () => {
      Object.assign(this.cam, PRESETS[b.dataset.preset]);
      this.fit();
    }));
    host.querySelector("[data-fit]").onclick = () => this.fit();
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
    this._wire();
    new ResizeObserver(() => this.draw()).observe(this.canvas);
  }

  // scene: { elements: [{element, type, kind, lines?, box?}], bands: {element: [[x,y,z,u]]},
  //          selected: name|null, arrows: [{from:[x,y,z], dir:[x,y,z], label}], focus: name|null }
  //          tension: {element: {kind, points, combinations, codes}} for the "Tension zones" mode
  //          crack: {element: [[x,y,z,wk/limit,size?]]} for the "Crack width" mode
  setScene(scene) {
    this.scene = scene;
    this._controls();
    this._build();
    this.fit();
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
    this.host.querySelector(".v3d-legend").innerHTML = on
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
      return { bands: this.scene.crack?.[e.element] || [], color: heat, words: pct };
    }
    if (this.view !== "tension") {
      return { bands: this.scene.bands?.[e.element] || [], color: heat, words: null };
    }
    const t = this.scene.tension?.[e.element];
    if (!t) return { bands: [], color: heat, words: null };
    const { combo, dir } = View3D.prefs;
    const codes = t.codes?.[combo] ?? null;
    const bands = t.points.map((p, i) => {
      const ch = codes ? codes[i] : " ";
      return [p[0], p[1], p[2], ch && ch !== " " ? ch.charCodeAt(0) - 48 : null, p[3]];
    });
    const state = (v) => tensionState(t.kind, v, dir);
    return {
      bands,
      color: (v) => (v == null ? `rgb(${GREY})` : TENSION[state(v)[0]]),
      words: (v) => (v == null ? "no result" : state(v)[1]) + ` (${combo || "envelope"})`,
    };
  }

  _build() {
    const { elements, selected } = this.scene;
    const items = [];
    for (const e of elements) {
      const faded = selected && e.element !== selected;
      const src = this._source(e);
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
            items.push({ kind: "line", a: [x, y, top], b: [x, y, bottom], color: `rgb(${GREY})`, width, faded, element: e.element });
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
          if (i === 0) items.push({ kind: "label", at: [x, y, top], text: e.element, faded, element: e.element });
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
        items.push({ kind: "quad", pts: [at(0, 0), at(1, 0), at(1, 1), at(0, 1)], faded, element: e.element, under: true,
          fill: e.type === "sheet_pile_wall" ? "rgba(120,130,145,0.30)" : "rgba(150,158,168,0.22)" });
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
              // Slabs: square cells of the zone grid.
              const h = size / 2 + 0.01;
              items.push({ kind: "quad", pts: [[x - h, y - h, z], [x + h, y - h, z], [x + h, y + h, z], [x - h, y + h, z]],
                faded, element: e.element, fill: src.color(u), stroke: false,
                tip: `${e.element} at X ${x}, Y ${y}: ${src.words ? src.words(u) : `bending needs ${Math.round(u * 100)}% of the bars`}` });
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
        items.push({ kind: "label", at: mid, text: e.element, faded, element: e.element });
      }
    }
    if (this.view === "crack") {
      for (const e of elements) {
        const faded = selected && e.element !== selected;
        // Slab cells are small and many: one mark per 3 m block, at its widest crack.
        const blocks = new Map();
        for (const [x, y, z, u, size] of this.scene.crack?.[e.element] || []) {
          if (!(u >= 0.5)) continue;
          const key = size ? `${Math.floor(x / 3)},${Math.floor(y / 3)}` : `${x},${y},${z}`;
          const was = blocks.get(key);
          if (!was || u > was.u) blocks.set(key, { u, at: [x, y, z] });
        }
        for (const { at, u } of blocks.values()) items.push({ kind: "mark", at, u, faded, element: e.element });
      }
    }
    for (const a of this.scene.arrows || []) items.push({ kind: "arrow", ...a });
    this.items = items;
    const pts = [];
    const focus = this.scene.focus;
    for (const it of items) {
      if (focus && it.element && it.element !== focus) continue;
      if (it.kind === "line") pts.push(it.a, it.b);
      if (it.kind === "quad") pts.push(...it.pts);
    }
    if (!pts.length) for (const it of items) if (it.kind === "line") pts.push(it.a, it.b);
    const lo = [0, 1, 2].map((k) => Math.min(...pts.map((p) => p[k])));
    const hi = [0, 1, 2].map((k) => Math.max(...pts.map((p) => p[k])));
    this.center = lo.map((l, k) => (l + hi[k]) / 2);
    this.bounds = { lo, hi };
    this.size = Math.max(...hi.map((h, k) => h - lo[k]), 1);
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

  fit() {
    this._size();
    const { lo, hi } = this.bounds;
    const corners = [];
    for (const x of [lo[0], hi[0]]) for (const y of [lo[1], hi[1]]) for (const z of [lo[2], hi[2]]) corners.push([x, y, z]);
    this.cam.scale = 1;
    this.cam.panX = this.cam.panY = 0;
    const sp = corners.map((c) => this._project(c));
    const wx = Math.max(...sp.map((p) => p[0])) - Math.min(...sp.map((p) => p[0]));
    const wy = Math.max(...sp.map((p) => p[1])) - Math.min(...sp.map((p) => p[1]));
    this.cam.scale = 0.82 * Math.min(this.w / Math.max(wx, 1e-6), this.h / Math.max(wy, 1e-6));
    this.draw();
  }

  _size() {
    const r = this.canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
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
    const ink = css.getPropertyValue("--text").trim() || "#1d1d1b";
    const muted = css.getPropertyValue("--muted").trim() || "#6b6b66";
    const drawn = [];
    for (const it of this.items) {
      if (it.kind === "line") {
        const a = this._project(it.a);
        const b = this._project(it.b);
        drawn.push({ it, depth: (a[2] + b[2]) / 2, a, b });
      } else if (it.kind === "quad") {
        const ps = it.pts.map((p) => this._project(p));
        drawn.push({ it, depth: ps.reduce((s, p) => s + p[2], 0) / 4 - (it.under ? 2e6 : 1e6), ps }); // panels behind lines
      }
    }
    drawn.sort((p, q) => p.depth - q.depth);
    this.hits = [];
    for (const d of drawn) {
      ctx.globalAlpha = d.it.faded ? 0.18 : 1;
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
        if (d.it.tip && !d.it.faded) this.hits.push(d);
      } else {
        ctx.beginPath();
        ctx.moveTo(d.a[0], d.a[1]);
        ctx.lineTo(d.b[0], d.b[1]);
        ctx.strokeStyle = d.it.color;
        ctx.lineWidth = d.it.width;
        ctx.lineCap = d.it.cap || "round";
        ctx.stroke();
        if (d.it.tip && !d.it.faded) this.hits.push(d);
      }
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
View3D.prefs = { mode: "util", combo: "", dir: "x" };
