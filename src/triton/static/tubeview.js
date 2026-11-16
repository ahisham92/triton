// The combi wall's steel tube check by zone, drawn for reading rather than as the office sheet: an
// elevation of the tube with each zone coloured by its utilisation, and one card per zone with the
// section after corrosion, the forces and a bar per check. The office sheet layout stays one click away.
// Presentation only: every number comes from the design's own sheet (tube.sheet).

import { statusOf } from "./picker.js";

// Short names for the sheet's check rows (the first row with each name is used).
const CHECKS = [
  [/^NEd \/ NRd/, "Axial N / NRd"],
  [/^MEd \/ MRd/, "Bending M / MRd"],
  [/^τEd \/ \(fy/, "Shear τ / τRd"],
  [/^τEd \/ τRd/, "Shell buckling in shear"],
  [/^MEd \/ MV,Rd/, "Bending with shear"],
  [/^MEd \/ MN,Rd/, "Bending with axial (class 1, 2)"],
  [/^σ \/ fy/, "Stress σ / fy (as the sheet)"],
  [/^N\/Aeff \+ M\/Weff/, "N + M on the effective section"],
  [/^Nc \/ Nb,Rd/, "Column buckling N / Nb,Rd"],
  [/^Interaction/, "Buckling N + M interaction"],
];
const COLOURS = { safe: "var(--ok)", limit: "#e0a100", unsafe: "var(--err)" };

export function tubeZonesHtml(sh, { esc, fmt }) {
  if (!sh?.columns?.length) return "";
  const rows = sh.groups.flatMap((g) => g.rows.map((r) => ({ ...r, group: g.title })));
  const row = (re) => rows.find((r) => re.test(r.item));
  const val = (re, i) => row(re)?.values[i];
  const util = row(/^Utilisation$/);
  const gov = row(/^Governing check/);
  const zones = sh.columns.map((name, i) => {
    const lv = String(val(/^Levels/, i) ?? "").match(/(-?[+\d.]+)\s*to\s*(-?[+\d.]+)/);
    return {
      name, i,
      top: lv ? parseFloat(lv[1]) : null,
      bottom: lv ? parseFloat(lv[2]) : null,
      u: typeof util?.values[i] === "number" ? util.values[i] / 100 : null,
      governs: gov?.values[i],
    };
  });
  const worst = zones.reduce((a, z) => (z.u != null && (a == null || z.u > a.u) ? z : a), null);

  const svg = zoneElevation(
    zones.filter((z) => z.top != null).map((z) => ({ name: z.name, top: z.top, bottom: z.bottom, u: z.u,
      hatch: String(val(/^Filled with concrete/, z.i)).toLowerCase() === "yes" })),
    { esc, fmt, note: "Level (m) · dotted: filled with concrete" });

  const bar = (label, v, governs) => {
    if (typeof v !== "number") return `<li class="tz-na"><span>${esc(label)}</span><span class="tz-note">${esc(v ?? "–")}</span></li>`;
    const st = statusOf(v / 100) || "safe";
    return `<li class="${governs ? "gov" : ""}"><span>${esc(label)}${governs ? ' <b class="tz-gov">governs</b>' : ""}</span>
      <span class="meter ${st}"><i style="width:${Math.min(100, Math.max(1, v)).toFixed(1)}%"></i></span><span class="tz-pct" style="color:${COLOURS[st]}">${v.toFixed(1)}%</span></li>`;
  };
  const cards = zones.map((z) => {
    const st = statusOf(z.u) || "safe";
    const govText = String(z.governs || "");
    const seen = new Set();
    let marked = false; // the check equal to the zone's utilisation governs (the last one, as the sheet lists them)
    const list = CHECKS.map(([re, label]) => {
      const r = rows.find((x) => re.test(x.item) && x.check);
      if (!r || seen.has(r.item)) return null;
      seen.add(r.item);
      return { label, v: r.values[z.i] };
    }).filter(Boolean);
    for (const c of [...list].reverse()) {
      if (!marked && typeof c.v === "number" && z.u != null && Math.abs(c.v / 100 - z.u) < 5e-4) c.governs = marked = true;
    }
    const checks = list.map((c) => bar(c.label, c.v, c.governs)).join("");
    const t = val(/^Thickness$/, z.i), te = val(/^Effective thickness/, z.i);
    return `<div class="tz-card" style="--st:${COLOURS[st]}">
      <div class="tz-head"><b>${esc(z.name)}</b><span class="status">${esc(val(/^Levels/, z.i) ?? "")} m</span>
        <span class="tz-badge">${z.u == null ? "–" : fmt(z.u, 2)}</span></div>
      <div class="tz-facts">
        <span title="Wall thickness before and after corrosion (outside / inside loss)">wall ${fmt(t, 1)} → <b>${fmt(te, 2)}</b> mm <small>(−${fmt(val(/^Corrosion, outside/, z.i), 2)} out, −${fmt(val(/^Corrosion, inside/, z.i), 2)} in)</small></span>
        <span>${esc(val(/^Pile class/, z.i) ?? "")} · d/t ${fmt(val(/^d\/t/, z.i), 1)}</span>
        <span>${String(val(/^Filled with concrete/, z.i)).toLowerCase() === "yes" ? "filled with concrete" : "steel only"}</span>
      </div>
      <div class="tz-forces"><span><small>N<sub>Ed</sub></small><b>${fmt(val(/^Compression force/, z.i))}</b> kN</span>
        <span><small>V<sub>Ed</sub></small><b>${fmt(val(/^Shear force VEd/, z.i))}</b> kN</span>
        <span><small>M<sub>Ed</sub></small><b>${fmt(val(/^Moment MEd/, z.i))}</b> kNm</span></div>
      <ul class="tz-checks">${checks}</ul>
      ${govText ? `<p class="status tz-foot">Governs: ${esc(govText)}</p>` : ""}</div>`;
  }).join("");

  return `<h3 style="margin-top:18px">Tube check by zone</h3>
    <p class="status">Each zone takes its largest N, V and M together, as the office sheet does. ${worst ? `The worst is the <b>${esc(worst.name)}</b> zone at ${fmt(worst.u, 2)}.` : ""}
      Green is safe, amber near the limit (0.95 to 1.0), red unsafe.</p>
    <div class="tz-wrap">${svg ? `<div class="tz-elev">${svg}</div>` : ""}<div class="tz-cards">${cards}</div></div>`;
}

// An elevation of a wall or tube: zones to scale from top to toe, each in its status colour and shade
// by utilisation, with its name and utilisation beside it. zones: [{ name, top, bottom, u, hatch }].
export function zoneElevation(zones, { esc, fmt, note = "Level (m)" }) {
  if (!zones.length) return "";
  const top = Math.max(...zones.map((z) => z.top)), bot = Math.min(...zones.map((z) => z.bottom));
  const H = 360, W = 250, y = (z) => 14 + ((top - z) / (top - bot || 1)) * (H - 28);
  return `<svg class="tube-elev" viewBox="0 0 ${W} ${H}" role="img" aria-label="Zones by level">
    ${zones.map((z) => {
      const st = statusOf(z.u) || "safe";
      const h = Math.max(2, y(z.bottom) - y(z.top));
      const mid = (y(z.top) + y(z.bottom)) / 2;
      return `<g><rect x="58" y="${y(z.top).toFixed(1)}" width="44" height="${h.toFixed(1)}" fill="${COLOURS[st]}" fill-opacity="${0.25 + 0.6 * Math.min(1, z.u ?? 0)}" stroke="${COLOURS[st]}"/>
        ${z.hatch ? `<rect x="64" y="${y(z.top).toFixed(1)}" width="32" height="${h.toFixed(1)}" fill="url(#zone-hatch)"/>` : ""}
        <line x1="52" x2="108" y1="${y(z.bottom).toFixed(1)}" y2="${y(z.bottom).toFixed(1)}" stroke="var(--muted)" stroke-width=".8"/>
        <text x="50" y="${(y(z.bottom) + 3).toFixed(1)}" text-anchor="end" class="tick">${fmt(z.bottom, 2)}</text>
        <text x="112" y="${(mid - 2).toFixed(1)}" class="tz-name">${esc(z.name)}</text>
        <text x="112" y="${(mid + 11).toFixed(1)}" class="tz-u" fill="${COLOURS[st]}">${z.u == null ? "" : fmt(z.u, 2)}</text></g>`;
    }).join("")}
    <text x="50" y="${(y(top) + 3).toFixed(1)}" text-anchor="end" class="tick">${fmt(top, 2)}</text>
    <defs><pattern id="zone-hatch" width="6" height="6" patternUnits="userSpaceOnUse"><circle cx="2" cy="2" r=".9" fill="#7a7a74" fill-opacity=".55"/><circle cx="5" cy="4.5" r=".6" fill="#7a7a74" fill-opacity=".45"/></pattern></defs>
  </svg><p class="status" style="margin:2px 0 0;font-size:12px">${esc(note)}</p>`;
}

// One check as a labelled bar: utilisation as a fraction (1 = at the limit), coloured by status.
export function checkBar(label, u, { esc, fmt, governs = false, note = "" }) {
  if (u == null || !isFinite(u)) return `<li class="tz-na"><span>${esc(label)}</span><span class="tz-note">${esc(note || "not needed")}</span></li>`;
  const st = statusOf(u) || "safe";
  return `<li class="${governs ? "gov" : ""}"><span>${esc(label)}${governs ? ' <b class="tz-gov">governs</b>' : ""}${note ? `<small class="tz-sub">${note}</small>` : ""}</span>
    <span class="meter ${st}"><i style="width:${Math.min(100, Math.max(1, u * 100)).toFixed(1)}%"></i></span><span class="tz-pct" style="color:${COLOURS[st]}">${fmt(u, 2)}</span></li>`;
}
