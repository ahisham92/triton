// Diaphragm wall results: a reinforced concrete front wall designed per metre run from the Plaxis plate
// (EN 1992-1-1). app.js passes its helpers in (fmt, esc, frame, showTip, v3dSlot).

import { checkBar, zoneElevation } from "./tubeview.js";

const CHECKS = ["bending", "shear", "crack_front", "crack_back"];
const TITLES = { bending: "Bending with N (N–M)", shear: "Shear", crack_front: "Crack width, front face", crack_back: "Crack width, back face" };

// Utilisations past 10 mean nothing more than "unsafe by far".
const uf = (u, fmt, digits = 2) => (u > 10 ? "&gt; 10, unsafe by far" : fmt(u, digits));

export function dwallCard(w, h) {
  const { fmt, esc } = h;
  const fu = (u, digits = 2) => uf(u, fmt, digits);
  const card = document.createElement("div");
  card.className = "panel";
  card.style.marginTop = "16px";
  const d = w.design;
  if (!d) {
    card.innerHTML = `<div class="element-head"><h3>${esc(w.element)}<span class="type">diaphragm wall</span></h3></div>
      <p class="status">No results to design with.</p>`;
    return card;
  }
  const g = d.governing;
  const st = d.steel;
  const ok = (x) => `<span class="sev ${x ? "ok" : "error"}">${x ? "passes" : "fails"}</span>`;
  const ufCell = (u) => (u == null ? `<td class="muted">–</td>` : `<td class="cell ${u <= 1 ? "ok" : "error"}">${fu(u)}</td>`);
  const links = (z) => (z.shear.links ? (z.shear.links.label ? esc(z.shear.links.label) : z.shear.links.crushes ? "struts crush" : "none fit") : "none needed");
  const zoneName = (z) => `${fmt(z.top, 2)} to ${fmt(z.bottom, 2)} m`;
  card.innerHTML = `<div class="element-head"><h3>${esc(w.element)}<span class="type">diaphragm wall ${fmt(d.thickness)} mm, ${esc(d.concrete.grade)}, EN 1992-1-1 per metre run</span></h3>
      ${ok(d.ok)}</div>
    <div class="counts" style="margin-top:0">
      <div class="count"><b class="${d.uf > 1 ? "bad" : ""}">${fu(d.uf)}</b>Uf: ${esc(TITLES[g.governs])}, ${fmt(g.zone[0], 2)} to ${fmt(g.zone[1], 2)} m</div>
      <div class="count"><b>${fmt(st.kg_per_m)}</b>kg of bars per metre of wall (${fmt(st.kg_per_m3)} kg/m³)</div>
      <div class="count"><b>${fmt(st.total_kg / 1000, 1)} t</b>one ${fmt(d.panel_width)} mm cage</div>
      <div class="count"><b>${fmt(d.top, 2)} to ${fmt(d.toe, 2)}</b>m, ${d.count} panels</div>
    </div>
    ${!d.ok ? `<div class="fail-why"><b>Fails because:</b> ${esc(TITLES[g.governs])} Uf ${fu(g.uf)} between ${fmt(g.zone[0], 2)} and ${fmt(g.zone[1], 2)} m. ${hint(g.governs)}</div>` : ""}
    ${[...(w.notes || []), ...d.notes].map((n) => `<p class="status">${esc(n)}</p>`).join("")}
    ${h.v3dSlot ? h.v3dSlot(w.element) : ""}
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px;align-items:start"><div class="chart" data-dw="profile"></div>
      <div class="scroll"><table>
        <tr><th colspan="2">Wall (per metre run)</th></tr>
        <tr><td>Thickness, panel</td><td>${fmt(d.thickness)} mm, ${fmt(d.panel_width)} mm long</td></tr>
        <tr><td>Concrete, bars</td><td>${esc(d.concrete.grade)} (fck ${fmt(d.concrete.fck)} MPa), fyk ${fmt(d.fyk)} MPa</td></tr>
        <tr><td>Cover</td><td>${fmt(d.cover)} mm to the horizontal bars (outer layer)</td></tr>
        <tr><td>Front (sea) face</td><td>in tension under ${esc(d.front_sign)} M_11</td></tr>
        <tr><td>Bars by weight</td><td>vertical ${fmt(st.vertical_kg_per_m)}, horizontal ${fmt(st.horizontal_kg_per_m)}, links ${fmt(st.links_kg_per_m)} kg/m</td></tr>
        <tr><td>Wall, element</td><td>${fmt(d.length_m, 1)} m long in the workbook, ${fmt(st.element_total_t, 1)} t of bars</td></tr>
        <tr><td>Points checked</td><td>${fmt(d.points)} ULS, ${fmt(d.qp_points)} QP</td></tr>
      </table></div></div>
    <h3 style="margin-top:18px">Bars by zone</h3>
    <p class="status">Green is safe, amber near the limit (0.95 to 1.0), red unsafe. Each zone carries its worst point.</p>
    <div class="tz-wrap" data-dw="zone-view"></div>
    <details class="office-details"><summary>Zones as a table</summary><div class="scroll" data-dw="zones"></div></details>
    <h3 style="margin-top:18px">Each check at its worst</h3>
    <ul class="tz-checks" data-dw="check-view"></ul>
    <details style="margin-top:12px"><summary>By combination</summary><div class="scroll" data-dw="combos"></div></details>`;

  const zs = d.zones;
  card.querySelector('[data-dw="zone-view"]').innerHTML = `<div class="tz-elev">${zoneElevation(
    zs.map((z, i) => ({ name: `Zone ${i + 1}`, top: z.top, bottom: z.bottom, u: z.uf })),
    { esc, fmt, note: "Level (m), from the top of the wall to its toe" })}</div>
    <div class="tz-cards">${zs.map((z, i) => `<div class="tz-card" style="--st:${z.uf > 1 ? "var(--err)" : z.uf >= 0.95 ? "#e0a100" : "var(--ok)"}">
      <div class="tz-head"><b>Zone ${i + 1}</b><span class="status">${zoneName(z)}</span><span class="tz-badge">${fu(z.uf)}</span></div>
      <div class="tz-facts"><span>front vertical <b>${esc(z.front.bars)}</b> <small>(${fmt(z.front.per_cage)} per cage face)</small></span>
        <span>back vertical <b>${esc(z.back.bars)}</b> <small>(${fmt(z.back.per_cage)} per cage face)</small></span>
        <span>horizontal <b>${esc(z.horizontal.front.bars)}</b> front, <b>${esc(z.horizontal.back.bars)}</b> back</span>
        <span>links <b>${links(z)}</b></span></div>
      <div class="tz-forces"><span><small>M</small><b>${fmt(z.M_max, 0)} / ${fmt(z.M_min, 0)}</b> kNm/m</span><span><small>V</small><b>${fmt(z.shear.V_kN_per_m, 0)}</b> kN/m</span>
        <span><small>wk</small><b>${fmt(z.front.wk_mm, 2)} / ${fmt(z.back.wk_mm, 2)}</b> mm</span></div>
      <ul class="tz-checks">${CHECKS.map((c) => checkBar(TITLES[c], z.util[c], { esc, fmt: fu, governs: z.governs === c })).join("")}</ul></div>`).join("")}</div>`;
  card.querySelector('[data-dw="zones"]').innerHTML = `<table><tr><th>From m</th><th>To m</th><th>Front vertical</th><th>Back vertical</th><th>As front req / prov</th><th>As back req / prov</th>
      <th>Horizontal front / back</th><th>Links</th><th>V / VRd,c kN/m</th><th>wk front / back mm</th>${CHECKS.map((c) => `<th>${esc(TITLES[c])}</th>`).join("")}<th>Uf</th></tr>
    ${zs.map((z) => `<tr><td>${fmt(z.top, 2)}</td><td>${fmt(z.bottom, 2)}</td><td>${esc(z.front.bars)}</td><td>${esc(z.back.bars)}</td>
      <td>${fmt(z.front.least_mm2_per_m)} / ${fmt(z.front.area_mm2_per_m)}</td><td>${fmt(z.back.least_mm2_per_m)} / ${fmt(z.back.area_mm2_per_m)}</td>
      <td>${esc(z.horizontal.front.bars)} / ${esc(z.horizontal.back.bars)}</td><td>${links(z)}</td>
      <td>${fmt(z.shear.V_kN_per_m, 0)} / ${fmt(z.shear.VRd_c_kN_per_m, 0)}</td><td>${fmt(z.front.wk_mm, 2)} / ${fmt(z.back.wk_mm, 2)}</td>
      ${CHECKS.map((c) => ufCell(z.util[c])).join("")}${ufCell(z.uf)}</tr>`).join("")}</table>`;
  card.querySelector('[data-dw="check-view"]').innerHTML = CHECKS.map((c) => {
    const z = zs.reduce((a, b) => (b.util[c] > a.util[c] ? b : a), zs[0]);
    return checkBar(TITLES[c], z.util[c], { esc, fmt: fu, governs: c === g.governs, note: zoneName(z) });
  }).join("");
  card.querySelector('[data-dw="combos"]').innerHTML = `<table><tr><th>Combination</th><th>max M kNm/m</th><th>min M kNm/m</th><th>max N kN/m</th><th>max |V| kN/m</th><th>max |M_22| kNm/m</th></tr>
    ${d.by_combination.map((c) => `<tr><td>${esc(c.combination)}</td><td>${fmt(c.max_M, 1)}</td><td>${fmt(c.min_M, 1)}</td><td>${fmt(c.max_N, 1)}</td><td>${fmt(c.max_V, 1)}</td><td>${fmt(c.max_Mh, 1)}</td></tr>`).join("")}</table>
    <p class="status">M positive puts the front (sea) face in tension; N compression positive.</p>`;
  profile(card.querySelector('[data-dw="profile"]'), d, h);
  return card;
}

function hint(governs) {
  if (governs === "shear") return "A thicker wall, or check the top level: the peak at the capping beam may be inside it.";
  if (governs === "bending") return "A thicker wall, bigger bars or a second layer on the face in tension.";
  return "Closer or smaller bars on that face, or a second layer.";
}

function profile(el, d, h) {
  const { fmt, frame, showTip } = h;
  const a = d.profile;
  if (!el || !a.length) return;
  const zs = a.map((q) => q[0]);
  const maxU = Math.max(1.1, ...a.map((q) => q[1])) * 1.05;
  const c = frame(el, { xDomain: [0, maxU], yDomain: [Math.min(...zs), Math.max(...zs)], xLabel: "M/MRd", yLabel: "Level z (m)", title: "Bending utilisation down the wall" });
  const path = a.map((q, i) => `${i ? "L" : "M"}${c.x(q[1]).toFixed(1)},${c.y(q[0]).toFixed(1)}`).join("");
  const cuts = d.zones.slice(0, -1).map((z) => `<line class="grid" x1="${c.m.l}" x2="${c.w - c.m.r}" y1="${c.y(z.bottom)}" y2="${c.y(z.bottom)}" stroke-dasharray="4 3"/>`).join("");
  c.g.innerHTML = `${cuts}<line class="limit" x1="${c.x(1)}" x2="${c.x(1)}" y1="${c.m.t}" y2="${c.h - c.m.b}"/>
    <text class="label" x="${c.x(1) + 4}" y="${c.m.t + 12}">1.0</text><path class="series" d="${path}"/>`;
  c.svg.onmousemove = (evt) => {
    const r = c.svg.getBoundingClientRect();
    const sy = ((evt.clientY - r.top) / r.height) * c.h;
    let best = 0;
    a.forEach((q, i) => { if (Math.abs(c.y(q[0]) - sy) < Math.abs(c.y(a[best][0]) - sy)) best = i; });
    const q = a[best];
    c.g.querySelector(".hover")?.remove();
    c.g.insertAdjacentHTML("beforeend", `<circle class="hover" cx="${c.x(q[1])}" cy="${c.y(q[0])}" r="4"/>`);
    showTip(c, evt, `z ${fmt(q[0], 1)} m<br>M/MRd ${fmt(q[1], 3)}`);
  };
  c.svg.onmouseleave = () => { c.tip.hidden = true; c.g.querySelector(".hover")?.remove(); };
}
