// The approach slab behind the quay and the ledge on the rear beam: its inputs (Elements tab, one
// for the whole project) and its results card (Design tab).

export const APPROACH = "Approach Slab";

// The project's approach slab inputs, shown on every section's Elements tab.
export function approachPanel(host, { project, schema, renderObject, esc }) {
  const card = document.createElement("div");
  card.className = "panel";
  card.style.marginBottom = "16px";
  card.innerHTML = `<div class="element-head"><h3>${esc(APPROACH)}<span class="type">Approach slab and rear beam ledge, whole project</span></h3></div>`;
  const fs = renderObject({ properties: { approach: schema } }, project, "", "");
  fs.style.border = "0";
  fs.style.padding = "0";
  card.append(fs);
  host.append(card);
}

// A small diagram of M (ULS envelope, sagging +) and V along the slab, from the ledge (left).
function diagramSvg(d, fmt) {
  const g = d.diagram || {};
  const x = g.x || [];
  if (x.length < 2) return "";
  const W = 560, H = 170, pad = 34;
  const L = x[x.length - 1] || 1;
  const mMax = Math.max(1, ...g.M_max.map(Math.abs), ...g.M_min.map(Math.abs));
  const vMax = Math.max(1, ...g.V);
  const sx = (v) => pad + (v / L) * (W - 2 * pad);
  const mid = H / 2;
  // Sagging is drawn below the line, as a bending moment diagram on the tension side.
  const sy = (m) => mid + (m / mMax) * (H / 2 - 18);
  const line = (vals, f) => vals.map((v, i) => `${i ? "L" : "M"}${sx(x[i]).toFixed(1)},${f(v).toFixed(1)}`).join("");
  const vy = (v) => mid - (v / vMax) * (H / 2 - 18);
  const iMax = g.M_max.indexOf(Math.max(...g.M_max));
  const iMin = g.M_min.indexOf(Math.min(...g.M_min));
  return `<svg viewBox="0 0 ${W} ${H}" class="approach-diagram" role="img" aria-label="Bending moment and shear along the approach slab">
    <line x1="${pad}" y1="${mid}" x2="${W - pad}" y2="${mid}" stroke="currentColor" stroke-opacity=".4"/>
    <path d="${line(g.M_max, sy)}" fill="none" stroke="var(--accent, #2f6fdf)" stroke-width="2"/>
    <path d="${line(g.M_min, sy)}" fill="none" stroke="var(--accent, #2f6fdf)" stroke-width="2" stroke-dasharray="4 3"/>
    <path d="${line(g.V, vy)}" fill="none" stroke="#b7791f" stroke-width="1.5" stroke-opacity=".8"/>
    <polygon points="${pad - 7},${mid + 12} ${pad + 7},${mid + 12} ${pad},${mid}" fill="currentColor" fill-opacity=".5"/>
    ${d.far_support ? `<polygon points="${W - pad - 7},${mid + 12} ${W - pad + 7},${mid + 12} ${W - pad},${mid}" fill="currentColor" fill-opacity=".5"/>` : ""}
    <text x="${pad}" y="14" font-size="11" fill="currentColor">Ledge</text>
    <text x="${W - pad}" y="14" font-size="11" text-anchor="end" fill="currentColor">Slab on grade</text>
    <text x="${sx(x[iMax])}" y="${Math.min(sy(g.M_max[iMax]) + 14, H - 2)}" font-size="11" text-anchor="middle" fill="currentColor">${fmt(g.M_max[iMax])} kNm/m</text>
    ${g.M_min[iMin] < -1 ? `<text x="${sx(x[iMin])}" y="${Math.max(sy(g.M_min[iMin]) - 6, 26)}" font-size="11" text-anchor="middle" fill="currentColor">${fmt(g.M_min[iMin])} kNm/m</text>` : ""}
    <text x="${W - pad}" y="${H - 4}" font-size="10" text-anchor="end" fill="#b7791f">V envelope (max ${fmt(vMax)} kN/m)</text>
  </svg>`;
}

// The ledge in section: the rear beam's side, the ledge, the bearing strip and the slab end.
function ledgeSvg(d, fmt) {
  const l = d.ledge || {};
  if (!l.projection_mm) return "";
  const s = 0.22; // px per mm
  const beamW = 260, top = 20;
  const drop = l.top_below_beam_top_mm || 0;
  const H = Math.max(drop + l.depth_mm + 80, 700) * s + top;
  const W = beamW + (l.projection_mm + 900) * s;
  const face = beamW;
  const ly = top + drop * s;
  const lx2 = face + l.projection_mm * s;
  const slabT = d.thickness_mm * s;
  const joint = (d.joint_mm || 25) * s;
  const bw = (l.bearing_width_mm || 200) * s;
  const bx = lx2 - ((l.edge_distance_mm || 0) * s) - bw;
  return `<svg viewBox="0 0 ${W.toFixed(0)} ${H.toFixed(0)}" class="approach-ledge" style="width:100%;min-width:260px;max-width:460px" role="img" aria-label="Ledge on the rear beam">
    <rect x="0" y="${top}" width="${face}" height="${H - top - 4}" fill="currentColor" fill-opacity=".08" stroke="currentColor"/>
    <text x="8" y="${top + 16}" font-size="11" fill="currentColor">Rear beam</text>
    <rect x="${face}" y="${ly}" width="${l.projection_mm * s}" height="${l.depth_mm * s}" fill="currentColor" fill-opacity=".14" stroke="currentColor"/>
    <rect x="${bx}" y="${ly - (l.bearing_thickness_mm ?? 20) * s}" width="${bw}" height="${Math.max((l.bearing_thickness_mm ?? 20) * s, 3)}" fill="#b7791f"/>
    <rect x="${face + joint}" y="${ly - slabT - 4}" width="${W - face - joint}" height="${slabT}" fill="currentColor" fill-opacity=".06" stroke="currentColor"/>
    <text x="${W - 8}" y="${ly - slabT + 12}" font-size="11" text-anchor="end" fill="currentColor">Approach slab ${fmt(d.thickness_mm)} mm</text>
    <path d="M${face + 6},${ly + (l.depth_mm - l.d_mm) * s} H${lx2 - 6} V${ly + (l.depth_mm - l.d_mm) * s + 10}" fill="none" stroke="var(--accent, #2f6fdf)" stroke-width="2"/>
    <text x="${lx2 + 6}" y="${ly + l.depth_mm * s / 2}" font-size="11" fill="currentColor">${fmt(l.projection_mm)} × ${fmt(l.depth_mm)}</text>
    <text x="${lx2 + 6}" y="${ly + l.depth_mm * s / 2 + 14}" font-size="11" fill="var(--accent, #2f6fdf)">tie ${l.tie?.bars || ""}</text>
  </svg>`;
}

export function approachCard(d, { esc, fmt }) {
  const card = document.createElement("div");
  card.className = "panel";
  card.style.marginTop = "16px";
  const ok = (x) => `<span class="sev ${x ? "ok" : "error"}">${x ? "passes" : "fails"}</span>`;
  const b = d.bending || {};
  const l = d.ledge || {};
  const sh = d.shear || {};
  const face = (f, label) =>
    f
      ? `<tr><td>${label}</td><td>${esc(f.bars)}</td><td class="num">${fmt(f.as_mm2_per_m)} / ${fmt(f.as_req_mm2_per_m)}</td>
      <td class="num">${fmt(f.M_Ed_kNm)} / ${fmt(f.M_Rd_kNm)}</td><td class="num cell ${f.wk_mm <= f.wk_limit_mm ? "ok" : "error"}">${fmt(f.wk_mm, 2)} / ${fmt(f.wk_limit_mm, 2)}</td>
      <td class="num cell ${f.passed ? "ok" : "error"}">${fmt(f.utilisation, 2)}</td></tr>`
      : "";
  const u = l.utilisations || {};
  const rb = l.rear_beam || {};
  card.innerHTML = `<div class="element-head"><h3>${esc(d.element)}<span class="type">Approach slab, ${fmt(d.length_m, 1)} m × ${fmt(d.thickness_mm)} mm, ${esc(d.concrete)}, covers ${fmt(d.cover_top_mm)} / ${fmt(d.cover_bottom_mm)} mm</span></h3>
      ${ok(d.passed)}</div>
    <div class="counts" style="margin-top:0">
      <div class="count"><b>${fmt(d.utilisation, 2)}</b>max utilisation (slab and ledge)</div>
      <div class="count"><b>${fmt(d.slab_utilisation, 2)}</b>slab</div>
      <div class="count"><b>${fmt(l.utilisation, 2)}</b>ledge</div>
      <div class="count"><b>${fmt(d.reaction?.uls_kN_per_m)} kN/m</b>on the ledge (ULS)</div>
      <div class="count"><b>${fmt(d.steel?.kg_per_m3)}</b>kg/m³ (${fmt(d.steel?.kg_per_m2)} kg/m²)</div>
    </div>
    ${(d.notes || []).map((n) => `<p class="status">${esc(n)}</p>`).join("")}
    <h3 style="margin-top:18px">Slab, per metre width</h3>
    ${diagramSvg(d, fmt)}
    <div class="scroll"><table>
      <tr><th>Face</th><th>Main bars</th><th class="num">As / needed mm²/m</th><th class="num">MEd / MRd kNm/m</th><th class="num">wk / limit mm (QP)</th><th class="num">Util.</th></tr>
      ${face(b.bottom, "Bottom (sagging)")}${face(b.top, "Top (hogging)")}
      <tr><td>Distribution bars</td><td colspan="5">bottom ${esc(d.distribution?.bottom?.bars || "–")}, top ${esc(d.distribution?.top?.bars || "–")} (≥ 20% of the main bars and the minimum)</td></tr>
      <tr><td>Shear at d</td><td colspan="4">VEd ${fmt(sh.V_Ed_kN)} kN/m, VRd,c ${fmt(sh.V_Rd_c_kN)} kN/m${sh.links_mm2_per_m2 ? `: links ${fmt(sh.links_mm2_per_m2)} mm²/m² near the ledge (VRd,max ${fmt(sh.V_Rd_max_kN)})` : ", no links"}</td>
        <td class="num cell ${sh.utilisation <= 1 ? "ok" : "error"}" title="${sh.links_mm2_per_m2 ? "VEd / VRd,max with the links" : "VEd / VRd,c"}">${fmt(sh.utilisation, 2)}</td></tr>
    </table></div>
    <h3 style="margin-top:18px">Ledge on the rear beam, per metre (strut and tie, EN 1992-1-1 J.3)</h3>
    <div class="cage"><div>${ledgeSvg(d, fmt)}</div><div class="scroll"><table>
      <tr><td>Load F (ULS, with its own weight)</td><td class="num">${fmt(l.F_Ed_kN_per_m)} kN/m at a<sub>c</sub> ${fmt(l.a_F_mm)} mm; H ${fmt(l.H_Ed_kN_per_m)} kN/m</td></tr>
      <tr><td>Strut and tie</td><td class="num">d ${fmt(l.d_mm)}, z ${fmt(l.z_mm)} mm, tan θ ${fmt(l.tan_theta, 2)}, F<sub>t</sub> ${fmt(l.F_t_kN_per_m)} kN/m</td></tr>
      <tr><td>Tie bars (top)</td><td class="num">${esc(l.tie?.bars || "–")}: ${fmt(l.tie?.as_mm2_per_m)} / ${fmt(l.tie?.as_req_mm2_per_m)} mm²/m (${fmt(u.tie, 2)})</td></tr>
      <tr><td>Links</td><td class="num">${esc(l.links?.bars || "none")} (${esc(l.links?.kind || "")})</td></tr>
      <tr><td>Bearing node</td><td class="num">${fmt(l.bearing?.sigma_MPa, 2)} / ${fmt(l.bearing?.sigma_Rd_MPa, 2)} MPa (${fmt(u.bearing, 2)})</td></tr>
      <tr><td>Shear at the face</td><td class="num">β ${fmt(l.shear?.beta, 2)} × ${fmt(l.shear?.V_Ed_kN)} / VRd,c ${fmt(l.shear?.V_Rd_c_kN)} kN (${fmt(u.shear, 2)})</td></tr>
      <tr><td>Crack width (QP)</td><td class="num">${fmt(l.crack?.wk_mm, 2)} / ${fmt(l.crack?.limit_mm, 2)} mm</td></tr>
      <tr><td>Hanger bars in the rear beam</td><td class="num">${esc(l.hanger?.bars || "–")} (${fmt(l.hanger?.as_req_mm2_per_m)} mm²/m, on top of its links)</td></tr>
      <tr><td>On the rear beam</td><td class="num">${fmt(rb.line_load_uls_kN_per_m)} kN/m + wheel ${fmt(rb.wheel_uls_kN)} kN${rb.eccentricity_mm != null ? ` at ${fmt(rb.eccentricity_mm)} mm off its centre line` : ""}</td></tr>
      <tr><td>Utilisation</td><td class="num cell ${l.passed ? "ok" : "error"}">${fmt(l.utilisation, 2)}</td></tr>
    </table></div></div>
    ${(l.notes || []).map((n) => `<p class="status">${esc(n)}</p>`).join("")}`;
  return card;
}
