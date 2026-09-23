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

export function legendHtml() {
  const stops = HEAT.map(([u, c]) => `rgb(${c}) ${u * 90}%`).join(", ");
  return `<div class="heat-legend"><span>Utilisation</span>
    <div class="bar" style="background:linear-gradient(90deg, ${stops}, rgb(${UNSAFE}) 91%, rgb(${UNSAFE}))"></div>
    <div class="ticks"><span>0</span><span style="left:45%">0.5</span><span style="left:67.5%">0.75</span><span style="left:90%">1.0</span></div>
    <div class="grey"><i style="background:rgb(${UNSAFE})"></i> above 1.0: unsafe</div>
    <div class="grey"><i style="background:rgb(${GREY})"></i> not designed yet</div></div>`;
}

const PRESETS = {
  "3D": { yaw: 0.7, pitch: 0.42 },
  Plan: { yaw: 0, pitch: Math.PI / 2 - 1e-4 },
  "From the sea": { yaw: 0, pitch: 0.12 },
  "Along the quay": { yaw: Math.PI / 2, pitch: 0.12 },
};

export class View3D {
  constructor(host, { height = 460, compact = false } = {}) {
    this.host = host;
    this.labels = !compact;
    host.classList.add("view3d");
    host.innerHTML = `<div class="v3d-bar">${Object.keys(PRESETS)
      .map((k) => `<button class="quiet" data-preset="${k}">${k}</button>`)
      .join("")}<button class="quiet" data-fit>Fit view</button>
      <label class="toggle"><input type="checkbox" data-labels ${this.labels ? "checked" : ""}> Labels</label></div>
      <canvas style="height:${height}px"></canvas><div class="v3d-tip" hidden></div>`;
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
    this._wire();
    new ResizeObserver(() => this.draw()).observe(this.canvas);
  }

  // scene: { elements: [{element, type, kind, lines?, box?}], bands: {element: [[x,y,z,u]]},
  //          selected: name|null, arrows: [{from:[x,y,z], dir:[x,y,z], label}], focus: name|null }
  setScene(scene) {
    this.scene = scene;
    this._build();
    this.fit();
  }

  _build() {
    const { elements, bands = {}, selected } = this.scene;
    const items = [];
    for (const e of elements) {
      const faded = selected && e.element !== selected;
      if (e.lines) {
        const b = bands[e.element] || [];
        const byPos = new Map();
        for (const [x, y, z, u] of b) {
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
              items.push({ kind: "line", a: [x, y, up], b: [x, y, down], color: heat(u), width, faded, cap: "butt",
                element: e.element, tip: `${e.element} at X ${x}, Y ${y}, z ${z.toFixed(1)} m: utilisation ${u.toFixed(2)}` });
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
        const b = bands[e.element] || [];
        if (b.length && flat === "Z") {
          // Beams: 0.5 m bands along the beam, across its full width.
          const along = Y[1] - Y[0] >= X[1] - X[0] ? "Y" : "X";
          const across = along === "Y" ? "X" : "Y";
          for (const [x, y, z, u] of b) {
            const s0 = (along === "Y" ? y : x) - 0.27;
            const s1 = s0 + 0.54;
            const pt = (sv, tv) => (along === "Y" ? [tv, sv, z] : [sv, tv, z]);
            const [t0, t1] = c[across];
            items.push({ kind: "quad", pts: [pt(s0, t0), pt(s1, t0), pt(s1, t1), pt(s0, t1)], faded, element: e.element,
              fill: heat(u), stroke: false,
              tip: `${e.element} at ${along} ${(along === "Y" ? y : x).toFixed(1)} m: utilisation ${u.toFixed(2)}` });
          }
        }
        const mid = ["X", "Y", "Z"].map((a) => (c[a][0] + c[a][1]) / 2);
        items.push({ kind: "label", at: mid, text: e.element, faded, element: e.element });
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
