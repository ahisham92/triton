// Crack pictures: for one QP set, the cross-section with its bars, neutral axis and tension zone, and
// an elevation with the cracks at s_r,max reaching in to the neutral axis, drawn wider as w_k grows.

const esc = (s) =>
  String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const num = (v, d = 0) =>
  v == null || !isFinite(v) ? "–" : Number(v).toLocaleString("en-GB", { maximumFractionDigits: d, minimumFractionDigits: d });

// items: [{label, set: {combination, N…, M…, crack: {wk_mm, limit_mm, util, sigma_s_MPa, sr_max_mm, x_mm,
//          h_mm, face?, angle_deg?, phi_mm?, spacing_mm?, d_mm?}}, geom, forces}]
// geom: {shape: "circle", D, rings} | {shape: "rect", b, h, bars: [[u, v, Ø]]} (u across, v up, mm from
// the centre) | {shape: "strip", h, …} (a 1 m slab strip drawn from the set's own bars).
export function crackPicturesHtml(items, title = "Crack pictures (QP sets)") {
  const usable = items.filter((it) => it.set?.crack);
  if (!usable.length) return "";
  let best = 0;
  usable.forEach((it, i) => { if ((it.set.crack.wk_mm || 0) > (usable[best].set.crack.wk_mm || 0)) best = i; });
  return `<div class="crack-pics" data-crack-pics>
    <h3 style="margin-top:18px">${esc(title)}</h3>
    <label class="crack-pick">Set <select data-crack-set>${usable
      .map((it, i) => `<option value="${i}" ${i === best ? "selected" : ""}>${esc(it.label)} · wk ${num(it.set.crack.wk_mm, 3)} mm</option>`)
      .join("")}</select></label>
    <div data-crack-view></div></div>`;
}

export function mountCrackPictures(root, items) {
  const box = root.querySelector("[data-crack-pics]");
  if (!box) return;
  const usable = items.filter((it) => it.set?.crack);
  const pick = box.querySelector("[data-crack-set]");
  const view = box.querySelector("[data-crack-view]");
  const show = () => (view.innerHTML = picture(usable[Number(pick.value)]));
  pick.onchange = show;
  show();
}

function picture(it) {
  const c = it.set.crack;
  const h = c.h_mm;
  const x = Math.max(0, Math.min(c.x_mm ?? h, h));
  const cracked = (c.wk_mm || 0) > 0 && x < h;
  const face = it.geom.shape === "circle" ? "bottom" : c.face || "bottom";
  const pct = c.util == null ? "" : ` (${Math.round(c.util * 100)}% of the ${num(c.limit_mm, 2)} mm limit)`;
  const words = !cracked
    ? `<p><b>No cracks:</b> the whole section is in compression under this set (x = h).</p>`
    : `<p><b>w<sub>k</sub> = ${num(c.wk_mm, 3)} mm</b>${pct}. Cracks every s<sub>r,max</sub> = ${num(c.sr_max_mm)} mm,
        reaching ${x > 0 ? `in to the neutral axis, ${num(h - x)} mm from the ${face === "top" ? "top" : "tension"} face (x = ${num(x)} mm in compression)` : "right through: the whole section is in tension"};
        σ<sub>s</sub> = ${num(c.sigma_s_MPa)} MPa in the extreme bar.</p>`;
  return `<div class="crack-figs">${section(it, x, face, cracked)}${elevation(it, x, face, cracked)}</div>
    ${words}
    <p class="status">${esc(it.forces)}. Crack widths are drawn wider in proportion to w<sub>k</sub> / limit so sets can be compared; they are not to scale with the section.</p>`;
}

const W = 300; // each figure's box (px)
const PAD = 26;

function crackPx(c) {
  // Drawn width at the face: 1.5 px at nothing up to 9 px at the limit and beyond.
  return Math.max(1.5, Math.min(9, 1.5 + 7.5 * (c.util || 0)));
}

function section(it, x, face, cracked) {
  const g = it.geom;
  const c = it.set.crack;
  const h = c.h_mm;
  const b = g.shape === "circle" ? g.D : g.shape === "strip" ? 1000 : g.b;
  const k = Math.min((W - 2 * PAD) / b, (W - 2 * PAD) / h);
  const cx = W / 2;
  const cy = PAD + (h * k) / 2 + 6;
  const H = h * k + 2 * PAD + 20;
  const X = (u) => cx + u * k;
  const Y = (v) => cy - v * k;
  // v of the neutral axis: x down from the compressed face.
  const vNa = face === "top" ? -h / 2 + x : h / 2 - x;
  const tensionUp = face === "top";
  let outline, clip;
  if (g.shape === "circle") {
    outline = `<circle cx="${cx}" cy="${cy}" r="${(g.D / 2) * k}"/>`;
    clip = outline;
  } else {
    outline = `<rect x="${X(-b / 2)}" y="${Y(h / 2)}" width="${b * k}" height="${h * k}"/>`;
    clip = outline;
  }
  const id = `cp${Math.random().toString(36).slice(2, 8)}`;
  const compTop = tensionUp ? Y(vNa) : Y(h / 2);
  const compBot = tensionUp ? Y(-h / 2) : Y(vNa);
  const zones = `<g clip-path="url(#${id})">
      <rect class="cz" x="${X(-b / 2) - 2}" y="${compTop}" width="${b * k + 4}" height="${Math.max(0, compBot - compTop)}"/>
      <rect class="tz" x="${X(-b / 2) - 2}" y="${tensionUp ? Y(h / 2) : Y(vNa)}" width="${b * k + 4}" height="${Math.max(0, (h - x) * k)}"/></g>`;
  const bars = barsOf(it, face);
  const inTension = (v) => (tensionUp ? v > vNa : v < vNa);
  const barSvg = bars
    .map(([u, v, phi]) => `<circle class="${cracked && inTension(v) ? "bar t" : "bar"}" cx="${X(u).toFixed(1)}" cy="${Y(v).toFixed(1)}" r="${Math.max(1.6, (phi / 2) * k).toFixed(1)}"/>`)
    .join("");
  // Cracks seen on the section: lines from the tension face in to the neutral axis, between the bars.
  let cracks = "";
  if (cracked) {
    const n = 5;
    const w = crackPx(c);
    for (let i = 0; i < n; i++) {
      const u = -b / 2 + (b * (i + 0.5)) / n;
      let vFace = tensionUp ? h / 2 : -h / 2;
      if (g.shape === "circle") {
        const half = Math.sqrt(Math.max(0, (g.D / 2) ** 2 - u * u));
        vFace = tensionUp ? half : -half;
        if ((tensionUp && vFace < vNa) || (!tensionUp && vFace > vNa)) continue;
      }
      const vEnd = x > 0 ? vNa : -vFace;
      cracks += `<path class="crack" d="${zigzag(X(u), Y(vFace), X(u), Y(vEnd), w)}"/>`;
    }
  }
  const naY = Y(vNa);
  const na = x > 0 && x < h
    ? `<line class="na" x1="${X(-b / 2) - 12}" y1="${naY}" x2="${X(b / 2) + 12}" y2="${naY}"/>
       <text class="lbl halo" x="${X(-b / 2) - 10}" y="${naY - 4}">neutral axis</text>`
    : "";
  const head =
    g.shape === "circle"
      ? `Section, bending turned so its tension side is at the bottom${c.angle_deg == null ? "" : ` (moment vector ${num(c.angle_deg)}° from M3 towards M2)`}`
      : g.shape === "strip"
        ? `1 m of strip, ${face} bars Ø${num(c.phi_mm)} @ ${num(c.spacing_mm)} as the crack check takes them`
        : `Section, ${face} face in tension (vertical bending)`;
  return `<figure><figcaption>${esc(head)}</figcaption>
    <svg class="crack-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(head)}">
      <defs><clipPath id="${id}">${clip}</clipPath></defs>
      ${zones}<g class="outline">${outline}</g>${cracks}${barSvg}${na}
      <text class="lbl" x="${W / 2}" y="${H - 6}" text-anchor="middle">${esc(g.shape === "circle" ? `Ø${num(g.D)}` : `${num(b)} × ${num(h)} mm`)}</text>
    </svg></figure>`;
}

// Bars as [u, v, Ø] (mm from the centre, v up); a pile's with one bar at the bottom, as the crack check.
function barsOf(it, face) {
  const g = it.geom;
  const c = it.set.crack;
  const h = c.h_mm;
  const bars = [];
  if (g.shape === "circle") {
    for (const r of g.rings || []) {
      for (let i = 0; i < r.count; i++) {
        const a = -Math.PI / 2 + (2 * Math.PI * i) / r.count;
        bars.push([r.radius * Math.cos(a), r.radius * Math.sin(a), r.diameter]);
      }
    }
  } else if (g.shape === "strip") {
    const s = c.spacing_mm || 200;
    const v = face === "top" ? c.d_mm - h / 2 : h / 2 - c.d_mm;
    for (let u = -500 + s / 2; u < 500; u += s) bars.push([u, v, c.phi_mm || 16]);
  } else {
    bars.push(...(g.bars || []));
  }
  return bars;
}

function elevation(it, x, face, cracked) {
  // A length of the element, tension face at the bottom (top when the top face is in tension), with a
  // crack every s_r,max and the bars in the tension zone.
  const c = it.set.crack;
  const h = c.h_mm;
  const sr = c.sr_max_mm || h;
  const L = Math.max(4 * sr, 2 * h);
  const k = Math.min((W - 2 * PAD) / L, 140 / h);
  const top = PAD;
  const H = h * k + 2 * PAD + 34;
  const x0 = PAD;
  const tensionUp = face === "top";
  const Y = (t) => (tensionUp ? top + t * k : top + (h - t) * k); // t: depth from the tension face
  const na = h - x;
  const zones = `<rect class="cz" x="${x0}" y="${Math.min(Y(h), Y(na))}" width="${L * k}" height="${x * k}"/>
    <rect class="tz" x="${x0}" y="${Math.min(Y(na), Y(0))}" width="${L * k}" height="${na * k}"/>`;
  // Depth of the bars nearest the tension face.
  const depths = barsOf(it, face).map(([, v]) => (tensionUp ? h / 2 - v : v + h / 2));
  const cover = depths.length ? Math.min(...depths) : Math.min(0.08 * h, 90);
  const bar = cracked ? `<line class="barline" x1="${x0}" x2="${x0 + L * k}" y1="${Y(cover)}" y2="${Y(cover)}"/>` : "";
  let cracks = "";
  if (cracked) {
    const w = crackPx(c);
    for (let s = sr / 2; s < L; s += sr) {
      const end = x > 0 ? na : h;
      cracks += `<path class="crack" d="${zigzag(x0 + s * k, Y(0), x0 + s * k, Y(end), w)}"/>`;
    }
  }
  const dim = cracked
    ? `<line class="dim" x1="${x0 + (sr / 2) * k}" x2="${x0 + (1.5 * sr) * k}" y1="${H - 8}" y2="${H - 8}"/>
       <line class="dim" x1="${x0 + (sr / 2) * k}" x2="${x0 + (sr / 2) * k}" y1="${H - 11}" y2="${H - 5}"/>
       <line class="dim" x1="${x0 + (1.5 * sr) * k}" x2="${x0 + (1.5 * sr) * k}" y1="${H - 11}" y2="${H - 5}"/>
       <text class="lbl" x="${x0 + sr * k}" y="${H - 14}" text-anchor="middle">crack spacing s<tspan font-size="8" dy="2">r,max</tspan><tspan dy="-2"> ${num(sr)} mm</tspan></text>`
    : "";
  const naLine = x > 0 && x < h
    ? `<line class="na" x1="${x0 - 8}" x2="${x0 + L * k + 8}" y1="${Y(na)}" y2="${Y(na)}"/>`
    : "";
  const head = `Elevation over ${num(L / 1000, 1)} m, ${tensionUp ? "top" : "tension"} face ${tensionUp ? "up" : "down"}`;
  return `<figure><figcaption>${esc(head)}</figcaption>
    <svg class="crack-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(head)}">
      ${zones}<rect class="outline-r" x="${x0}" y="${top}" width="${L * k}" height="${h * k}"/>${bar}${cracks}${naLine}${dim}
      <text class="lbl" x="${x0}" y="${tensionUp ? top - 6 : top + h * k + 12}">${cracked ? `w<tspan baseline-shift="sub" font-size="8">k</tspan> ${num(c.wk_mm, 3)} mm at this face` : "no tension"}</text>
    </svg></figure>`;
}

// A crack from (x1, y1) at the face to (x2, y2) at its tip: a jagged wedge, w px wide at the face.
function zigzag(x1, y1, x2, y2, w) {
  const n = 6;
  const dx = x2 - x1;
  const dy = y2 - y1;
  const len = Math.hypot(dx, dy) || 1;
  const nx = -dy / len;
  const ny = dx / len;
  const left = [];
  const right = [];
  for (let i = 0; i <= n; i++) {
    const t = i / n;
    const jig = i === 0 || i === n ? 0 : (i % 2 ? 1 : -1) * Math.min(3, len / 12);
    const half = (w / 2) * (1 - t);
    const px = x1 + dx * t + nx * jig;
    const py = y1 + dy * t + ny * jig;
    left.push([px + nx * half, py + ny * half]);
    right.push([px - nx * half, py - ny * half]);
  }
  const pts = left.concat(right.reverse());
  return pts.map(([a, b], i) => `${i ? "L" : "M"}${a.toFixed(1)},${b.toFixed(1)}`).join("") + "Z";
}
