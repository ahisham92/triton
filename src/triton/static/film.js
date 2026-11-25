// Results film: a short animation of a SAP2000 analysis for the client, in the way TritoMoor films a
// mooring: the world map in to the site, the structure in 3D in its sea with the breakwater, then
// what it carries, what it rests on, how it moves and how hard each member works, coloured green to
// red, and the results on one card. The length follows what there is to show. Played here, or saved
// as an MP4 drawn frame by frame (so a slow computer drops nothing).

import { Film3D, Mesh, hex, surroundings } from "./film3d.js";
import { WorldMap } from "./worldmap.js";
import { fromB64, isQuake, mergeModel, readSap } from "./sapread.js";
import { heat } from "./view3d.js";
import { loadLeaflet, makeMap, searchBox } from "./sitemap.js";
import { confirmBox } from "./cdt/sdk.js";

const ease = (u) => (u < 0 ? 0 : u > 1 ? 1 : u * u * (3 - 2 * u));
const lerp = (a, b, u) => a + (b - a) * u;
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const FPS = 30;
const rgb = (s) => s.match(/\d+/g).slice(0, 3).map((v) => +v / 255);
// relative values (a load, a reaction, a movement against the largest): green to red
const rel = (v, max) => rgb(heat(max > 0 ? clamp(Math.abs(v) / max, 0, 1) * 0.999 : 0));
const CONC = hex("#B5B0A6"), CONC_TOP = hex("#C3BEB4"), STEEL = hex("#565C63"), PLATE = hex("#BDB8AE"), GHOST = hex("#9AA3AD"), BED = hex("#BDB091");
const SHOTS = { loads: "What it carries", reactions: "Reactions", piles: "Pile capacities", movements: "Movements", quake: "Vibration, earthquake and waves", forces: "Member forces", design: "Utilisation" };
// shots that show the setting: the sea less clear there, and the revetment drawn
const SCENIC = new Set(["map", "intro", "site", "end"]);
const RESULTS = new Set(["reactions", "piles", "move", "mode", "modal", "quake", "act", "forces", "plates", "design", "stress", "summary"]);

// --- the place: map points to the model's axes ------------------------------------------------
export function toModel(s) {
  if (s.lat == null || s.lon == null) return null;
  const b = (s.bearing || 0) * Math.PI / 180, kx = 111320 * Math.cos(s.lat * Math.PI / 180), ky = 110540;
  return ([lat, lon]) => {
    const E = (lon - s.lon) * kx, N = (lat - s.lat) * ky;
    return [E * Math.sin(b) + N * Math.cos(b), -E * Math.cos(b) + N * Math.sin(b)];
  };
}
export function toMap(s) {
  if (s.lat == null || s.lon == null) return null;
  const b = (s.bearing || 0) * Math.PI / 180, kx = 111320 * Math.cos(s.lat * Math.PI / 180), ky = 110540;
  return ([x, y]) => [s.lat + (x * Math.cos(b) + y * Math.sin(b)) / ky, s.lon + (x * Math.sin(b) - y * Math.cos(b)) / kx];
}

// --- the model, ready to draw -----------------------------------------------------------------
function prepare(S, set) {
  const J = S.joints, lo = S.bbox[0], hi = S.bbox[1];
  const L = Math.max(10, Math.hypot(hi[0] - lo[0], hi[1] - lo[1]));
  const centre = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2];
  const seabed = set.seabed != null ? set.seabed : hi[2] - (hi[2] - lo[2]) * 0.55;
  const wl = set.water_level ?? 0;
  const place = toModel(set);
  const pts = (o) => (o.pts?.length && place ? o.pts.map(place) : o.xy || []);
  const top = hi[2];
  const site = {
    seabed,
    breakwaters: (set.breakwaters || []).map((r) => ({ ...r, pts: pts(r) })).filter((r) => r.pts.length >= 2),
    land: (set.land || []).map((l) => ({ pts: pts(l), level: l.level ?? top, open: l.open })).filter((l) => l.pts.length >= 3),
  };
  // which way the sea is: away from the land, toward the breakwater, else +Y
  const mid = (list) => { let x = 0, y = 0, n = 0; list.forEach((p) => { x += p[0]; y += p[1]; n++; }); return n ? [x / n - centre[0], y / n - centre[1]] : null; };
  let sea = null;
  const rock = mid(site.breakwaters.filter((r) => r.kind !== "revetment").flatMap((r) => r.pts)), land = mid(site.land.flatMap((l) => l.pts));
  if (rock && Math.hypot(...rock) > 1) sea = rock;
  else if (land && Math.hypot(...land) > 1) sea = [-land[0], -land[1]];
  const seaYaw = sea ? Math.atan2(sea[1], sea[0]) * 180 / Math.PI : 90;
  // a revetment slopes down toward the sea
  site.breakwaters.forEach((r) => {
    if (r.kind !== "revetment") return;
    const a = r.pts[0], b = r.pts[r.pts.length - 1], sy = seaYaw * Math.PI / 180;
    r.seaSide = -(b[1] - a[1]) * Math.cos(sy) + (b[0] - a[0]) * Math.sin(sy) >= 0 ? 1 : -1;
  });
  const sect = S.sections.map((s) => {
    const steel = /pipe|tube|box|^i$|channel|angle|steel/i.test(s.shape || "") || /^S\d|steel|A992|A572/i.test(s.material || "");
    const minW = L / 260;
    if (s.shape === "Shell") return { t: Math.max(s.t || 0.3, minW), steel: false };
    const d = s.d > 0 ? s.d : L / 90, b = s.b > 0 ? s.b : d;
    const round = /pipe|circle|tube/i.test(s.shape || "");
    return { w: Math.max(round ? d : b, minW), h: Math.max(d, minW), round, steel };
  });
  // the reach of everything drawn, for the sea and the camera
  let ext = L;
  [...site.breakwaters.flatMap((r) => r.pts), ...site.land.flatMap((l) => l.pts)].forEach((p) => { ext = Math.max(ext, 2 * Math.hypot(p[0] - centre[0], p[1] - centre[1])); });
  ext = Math.min(ext, 12 * L);
  // the sea side, surest from the loads: a ship pushes in from the sea, a mooring line pulls out to it
  let seaYaw2 = seaYaw;
  const loadSea = seaFromLoads(S);
  if (loadSea) seaYaw2 = Math.atan2(loadSea[1], loadSea[0]) * 180 / Math.PI;
  // the top of what is drawn at deck level (beams drawn on their centre line stand above the slab)
  let deckTop = top;
  S.frames.ends.forEach(([a, b], n) => { const p = J[a], q = J[b]; if (!p || !q || Math.abs(p[2] - q[2]) > 0.3 || p[2] < top - 1.5) return; deckTop = Math.max(deckTop, p[2] + (sect[S.frames.section[n]]?.h || 0) / 2); });
  S.areas.joints.forEach((js, n) => { const z = Math.max(...js.map((j) => J[j]?.[2] ?? -Infinity)); if (z > top - 1.5) deckTop = Math.max(deckTop, z + (sect[S.areas.section[n]]?.t || 0) / 2); });
  const M = { S, J, L, centre, seabed, wl, site, sect, seaYaw: seaYaw2, seaDir: [Math.cos(seaYaw2 * Math.PI / 180), Math.sin(seaYaw2 * Math.PI / 180)], ext, top, deckTop, bottom: lo[2] };
  findPiles(M, set);
  return M;
}

function seaFromLoads(S) {
  const pick = (re, sign) => {
    for (const p of Object.keys(S.loads || {})) {
      if (!re.test(p) || /FRICT/i.test(p)) continue;
      const o = loadItems(S, p).find((q) => Math.abs(q.d[2]) < 0.3 && q.kind === "point");
      if (o) { const h = Math.hypot(o.d[0], o.d[1]); return [sign * o.d[0] / h, sign * o.d[1] / h]; }
    }
    return null;
  };
  return pick(/BERTH|FEND/i, -1) || pick(/BOLL.*PERP|MOOR.*PERP/i, 1);
}
// the piles: from each support up the near-vertical members to the deck; then their groups
// (existing, new...) by frame or section names, with the capacities given for each
const glob = (pat) => new RegExp(`^${pat.trim().replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*").replace(/\?/g, ".")}$`, "i");
function findPiles(M, set) {
  const S = M.S, J = M.J, at = new Map();
  S.frames.ends.forEach(([a, b], n) => { [a, b].forEach((j) => { if (!at.has(j)) at.set(j, []); at.get(j).push(n); }); });
  const used = new Set(), piles = [], pileOf = new Map();
  const steep = (n, from) => { const [a, b] = S.frames.ends[n], o = a === from ? b : a, p = J[from], q = J[o]; if (!p || !q) return null; const dz = q[2] - p[2], l = Math.hypot(q[0] - p[0], q[1] - p[1], dz); return dz > 0 && l > 0 && dz / l > 0.5 ? { o, v: dz / l } : null; };
  S.supports.forEach((sj) => {
    if (!J[sj] || J[sj][2] > M.top - 1) return;
    const frames = [];
    let cur = sj;
    for (let guard = 0; guard < 400; guard++) {
      const next = (at.get(cur) || []).filter((n) => !used.has(n)).map((n) => ({ n, ...(steep(n, cur) || {}) })).filter((x) => x.o != null).sort((a, b) => b.v - a.v)[0];
      if (!next) break;
      used.add(next.n); frames.push(next.n); cur = next.o;
      if (J[cur][2] >= M.top - 0.3) break;
    }
    if (!frames.length) return;
    const k = piles.length;
    frames.forEach((n) => pileOf.set(n, k));
    const sec = S.sections[S.frames.section[frames[0]]] || {};
    piles.push({ support: sj, frames, top: J[cur], section: sec.name || "", d: sec.d || null });
  });
  // rows of piles: tops lined up across one plan direction, lettered A, B, C... from the low side
  const rowsOn = (d) => { const v = [...new Set(piles.map((p) => p.top[d]))].sort((a, b) => a - b), out = []; v.forEach((x) => { if (!out.length || x - out[out.length - 1].at(-1) > 0.75) out.push([x]); else out[out.length - 1].push(x); }); return out.map((c) => c.reduce((a, b) => a + b, 0) / c.length); };
  if (piles.length > 3) {
    const rx = rowsOn(0), ry = rowsOn(1), d = ry.length <= rx.length ? 1 : 0, rows = d ? ry : rx;
    if (rows.length >= 2 && rows.length <= 26 && rows.length < piles.length) {
      const given = (set.pile_rows || "").split(",").map((x) => x.trim()).filter(Boolean);
      const name = (i) => given[i] || String.fromCharCode(65 + i);
      piles.forEach((p) => { const r = rows.reduce((b, x, i) => (Math.abs(x - p.top[d]) < Math.abs(rows[b] - p.top[d]) ? i : b), 0); p.row = name(r); });
      M.rows = rows.map((x, i) => ({ row: name(i), at: x, axis: d ? "Y" : "X", piles: piles.filter((p) => p.row === name(i)) }));
    }
  }
  // groups
  const unit = set.capacity_unit === "kN" ? 1 : 9.80665;
  let groups = (set.piles || []).map((g) => ({ ...g }));
  if (!groups.length) {
    const old = /EXIST|EXST|OLD|EXIS/i;
    groups = piles.some((p) => old.test(p.section)) ? [{ name: "Existing piles", match: S.sections.filter((x) => old.test(x.name || "")).map((x) => x.name).join(","), colour: "#8E8577" }, { name: "New piles", match: "" }] : [{ name: "Piles", match: "" }];
  }
  const PAL = ["#2F6FD0", "#8E8577", "#E07B39", "#7A4BC4", "#2E9E8F"];
  groups.forEach((g, i) => {
    g.colour = g.colour || PAL[i % PAL.length];
    g.res = (g.match || "").split(",").map((x) => x.trim()).filter(Boolean).map(glob);
    g.comp = g.compression != null ? g.compression * unit : null;
    g.tens = g.tension != null ? g.tension * unit : g.comp != null ? (g.tension_factor ?? 0.7) * g.comp : null;
    g.lat = g.lateral != null ? g.lateral * unit : null;
    g.caseRes = (g.cases || "").split(",").map((x) => x.trim()).filter(Boolean).map(glob);
    g.latRes = g.lateral_cases ? g.lateral_cases.split(",").map((x) => x.trim()).filter(Boolean).map(glob) : g.caseRes;
    g.count = 0;
  });
  const fallback = groups.findIndex((g) => !g.res.length);
  piles.forEach((p) => {
    const names = [p.section, ...p.frames.map((n) => S.frames.names?.[n] ?? ""), ...(p.row ? [`Row ${p.row}`, p.row] : [])];
    let gi = groups.findIndex((g) => g.res.length && g.res.some((re) => names.some((nm) => re.test(nm))));
    if (gi < 0) gi = fallback >= 0 ? fallback : 0;
    p.group = gi; groups[gi].count++;
  });
  M.piles = piles; M.pileOf = pileOf; M.groups = groups.filter((g) => g.count); M.allGroups = groups;
}
const tN = (v, set) => (set.capacity_unit === "kN" ? `${n0(v)} kN` : `${n0(v)} kN (${n0(v / 9.80665)} t)`);

function structure(M, colour, { shift = null, k = 0, pick = null, clipZ = null } = {}) {
  const { S, J, sect } = M, m = new Mesh();
  const P = shift ? (j) => [J[j][0] + shift[j][0] * k, J[j][1] + shift[j][1] * k, J[j][2] + shift[j][2] * k] : (j) => J[j];
  const many = S.frames.ids.length > 6000;
  S.frames.ends.forEach(([a0, b0], n) => {
    if (pick && !pick.frame(n)) return;
    let a = a0, b = b0;
    const s = sect[S.frames.section[n]] || { w: M.L / 150, h: M.L / 150 };
    if (clipZ != null) {
      let p = P(a), q = P(b);
      if (p[2] > q[2]) [p, q] = [q, p];
      if (p[2] > clipZ) return;
      if (q[2] > clipZ) { const f = (clipZ - p[2]) / (q[2] - p[2] || 1); q = p.map((v, i) => v + (q[i] - v) * f); }
      const c = colour.frame(n) || (s.steel ? STEEL : CONC);
      m.member(p, q, s.w, s.h, c, s.round ? (many ? 4 : 8) : 0);
      return;
    }
    const c = colour.frame(n) || (s.steel ? STEEL : CONC);
    m.member(P(a), P(b), s.w, s.h, c, s.round ? (many ? 4 : 8) : 0);
  });
  S.areas.joints.forEach((js, n) => {
    if (pick && !pick.area(n)) return;
    const s = sect[S.areas.section[n]] || { t: 0.3 };
    const c = colour.area(n) || PLATE;
    const pts = js.map(P);
    const t = (s.t || 0.4) / 2;
    m.panel(pts.map((p) => [p[0], p[1], p[2] + t]), c === PLATE ? CONC_TOP : c);
    m.panel(pts.map((p) => [p[0], p[1], p[2] - t]), c);
  });
  return m;
}

// --- the shots --------------------------------------------------------------------------------
function cams(M) {
  const c = M.centre, L = M.L, y = M.seaYaw;
  const at = (yaw, pitch, dist, dz = 0, dx = 0, dy = 0) => ({ yaw, pitch, dist, tx: c[0] + dx, ty: c[1] + dy, tz: c[2] * 0.3 + dz });
  return {
    aerial: () => at(y + 60, 58, Math.max(2.6 * L, 0.9 * M.ext)),
    high: () => at(y - 30, 36, 1.7 * L),
    quarter: () => at(y - 38, 22, 1.25 * L),
    quarter2: () => at(y + 40, 20, 1.2 * L),
    low: () => at(y - 15, 4, 1.05 * L, M.wl - c[2] * 0.3),
    under: () => at(y - 25, -6, 1.1 * L, (M.seabed + M.wl) / 2 - c[2] * 0.3),
    side: () => at(y + 90, 8, 1.3 * L),
    top: () => at(y, 80, 1.6 * L),
    wide: () => at(y - 50, 30, Math.max(1.6 * L, 0.7 * M.ext)),
    // down the full height, from the deck to the pile toes, where the reactions are
    tall: () => ({ yaw: y - 35, pitch: 24, dist: Math.max(1.4 * L, 2.4 * (M.top - M.bottom)), tx: c[0], ty: c[1], tz: 0.6 * M.top + 0.4 * M.bottom }),
    // the structure and its breakwater together
    setting: () => {
      const r = M.site.breakwaters.flatMap((b) => b.pts);
      if (!r.length) return at(y - 50, 18, Math.max(1.6 * L, 0.7 * M.ext));
      const rc = [r.reduce((a, p) => a + p[0], 0) / r.length, r.reduce((a, p) => a + p[1], 0) / r.length];
      const far = Math.max(...r.map((p) => Math.hypot(p[0] - c[0], p[1] - c[1])));
      const yaw = Math.atan2(rc[1] - c[1], rc[0] - c[0]) * 180 / Math.PI - 150;
      return { yaw, pitch: 24, dist: Math.max(1.6 * L, 1.5 * far), tx: (c[0] + rc[0]) / 2, ty: (c[1] + rc[1]) / 2, tz: M.wl };
    },
  };
}

const n0 = (v, d = 0) => (v == null || !isFinite(v) ? "–" : Number(v).toLocaleString("en-GB", { maximumFractionDigits: d, minimumFractionDigits: d }));
const kN = (v) => `${n0(v)} kN`;
const mm = (v) => `${n0(v * 1000, Math.abs(v) < 0.01 ? 1 : 0)} mm`;

export function direct(S, set, M) {
  const C = cams(M), shots = [], brief = set.mode === "brief", hide = new Set(set.hide || []);
  const add = (s) => shots.push({ dur: 5, cut: false, ...s });
  const fr = (n) => S.frames.names?.[n] || `Frame ${S.frames.ids[n]}`;
  const title = set.title || set.name || "Structural analysis";
  if (set.lat != null && set.lon != null) add({ kind: "map", dur: 7, cam0: C.aerial(), cam1: C.aerial(), geo: { lat: set.lat, lon: set.lon } });
  add({ kind: "intro", dur: 7, cam0: { ...C.aerial(), dist: C.aerial().dist * 1.5, pitch: 62 }, cam1: C.high(), title: true });
  const nf = S.frames.ids.length, na = S.areas.ids.length;
  const used = new Map();
  S.frames.section.forEach((k) => used.set(k, (used.get(k) || 0) + 1));
  S.areas.section.forEach((k) => used.set(k, (used.get(k) || 0) + 1));
  const sects = [...used.entries()].sort((a, b) => b[1] - a[1]).map(([k, n]) => `${S.sections[k]?.name || "–"} (${n})`);
  if (M.piles.length) {
    const pileSecs = new Map(), beamSecs = new Map(), deckSecs = new Map();
    M.piles.forEach((p) => { const k2 = `${p.section}|${M.groups.length > 1 && p.section ? M.allGroups[p.group].name : ""}`; pileSecs.set(k2, (pileSecs.get(k2) || 0) + 1); });
    S.frames.section.forEach((q, n) => { if (!M.pileOf.has(n)) { const nm = S.sections[q]?.name || ""; beamSecs.set(nm, (beamSecs.get(nm) || 0) + 1); } });
    S.areas.section.forEach((q) => { const sc = S.sections[q] || {}; const nm = sc.name ? `${sc.name}${sc.t ? ` (${n0(sc.t * 1000)} mm)` : ""}` : ""; deckSecs.set(nm, (deckSecs.get(nm) || 0) + 1); });
    const secLine = (map, what) => { const list = [...map].sort((a, b) => b[1] - a[1]); const named = list.filter(([n]) => n.split("|")[0]); return named.length ? `${named.slice(0, 4).map(([n, c]) => `${n.split("|")[0]}${n.split("|")[1] ? ` ${n.split("|")[1].toLowerCase()}` : ""} (${c})`).join(", ")}${named.length > 4 ? ` and ${named.length - 4} more` : ""}` : `${n0(list.reduce((a, [, c]) => a + c, 0))} ${what}; their sections were not in the file`; };
    const beamCount = S.frames.ids.length - M.pileOf.size;
    add({ kind: "build", stage: "piles", dur: 6, cam0: C.high(), cam1: C.quarter(), head: `${n0(M.piles.length)} piles`, legend: M.groups.length > 1 ? "groups" : "sections",
      lines: [secLine(pileSecs, "piles"), M.groups.length > 1 ? M.groups.map((g) => `${g.name}: ${g.count}`).join(", ") : `From ${n0(Math.min(...M.piles.map((p) => M.J[p.support][2])), 1)} m up to the deck`,
        M.rows ? `${M.rows.length} rows, ${M.rows.map((r) => r.row).join(", ")}, along ${M.rows[0].axis === "Y" ? "X" : "Y"}` : ""].filter(Boolean),
      tags: (M.rows || []).map((r) => { const p = r.piles.reduce((b, q) => (q.top[r.axis === "Y" ? 0 : 1] < b.top[r.axis === "Y" ? 0 : 1] ? q : b), r.piles[0]); return { at: p.top, text: `Row ${r.row}` }; }) });
    if (beamCount > 0) add({ kind: "build", stage: "beams", dur: 5, cam0: C.quarter(), cam1: C.quarter2(), head: `${n0(beamCount)} beams`, legend: M.groups.length > 1 ? "groups" : "sections", lines: [secLine(beamSecs, "beams")] });
    if (na) add({ kind: "build", stage: "deck", dur: 5, cam0: C.quarter2(), cam1: C.high(), head: `The deck: ${n0(na)} panels`, legend: M.groups.length > 1 ? "groups" : "sections", lines: [secLine(deckSecs, "panels")] });
  } else
    add({ kind: "model", dur: 6, cam0: C.high(), cam1: C.quarter(), head: "The structure",
      lines: [`${n0(S.joints.filter(Boolean).length)} joints, ${n0(nf)} members${na ? ` and ${n0(na)} plates` : ""}, ${n0(S.supports.length)} supports`,
        `Sections: ${sects.slice(0, 4).join(", ")}${sects.length > 4 ? ` and ${sects.length - 4} more` : ""}`],
      legend: "sections" });
  const rocks = M.site.breakwaters;
  add({ kind: "site", dur: 7, cam0: C.low(), cam1: C.setting(), head: "In its setting",
    lines: [`Water at ${n0(M.wl, 2)} m, seabed at ${n0(M.seabed, 2)} m (${n0(M.wl - M.seabed, 1)} m of water)`,
      rocks.length ? `${rocks.map((r) => r.name || "Breakwater").join(", ")}: crest at +${n0(rocks[0].crest, 1)} m` : "No breakwater drawn: add one on the map beside the film"],
    seabedAlpha: 1 });

  // the loads put on it, the largest patterns first
  if (!hide.has("loads")) {
    const pats = Object.entries(S.loads).map(([p, L]) => {
      const it = L.items;
      let total = 0;
      (it.joint || []).forEach(([, a, b, c]) => (total += Math.hypot(a, b, c)));
      (it.point || []).forEach(([, , f]) => (total += Math.abs(f)));
      (it.frame || []).forEach(([i, , v]) => (total += Math.abs(v) * frameLen(S, i)));
      (it.area || []).forEach(([i, , v]) => (total += Math.abs(v) * areaOf(S, i)));
      const count = Object.values(L.counts || {}).reduce((a, b) => a + b, 0);
      return { p, total, count, type: (S.patterns.find((q) => q.name === p) || {}).type || "" };
    }).filter((x) => x.total > 0).sort((a, b) => b.total - a.total);
    loadFamilyShots(S, M, C, add, brief, pats);
    // no load tables in the file: each family of load cases (dead, live, berthing, mooring, wind,
    // waves...) as the total it puts on the structure, worked out from the base reactions
    if (!pats.length && Object.keys(S.base || {}).length) {
      const fams = families(S).filter((f) => f.key !== "quake");
      fams.slice(0, brief ? 3 : 8).forEach((f, i) => add({ kind: "family", fam: f, dur: brief ? 4.5 : 5.5, cut: i === 0,
        cam0: i % 2 ? C.quarter2() : C.quarter(), cam1: famCam(M, f, C), head: `${f.label}: ${f.cases.length} load case${f.cases.length === 1 ? "" : "s"}`,
        lines: f.mag < 1 ? ["These cases put no net load on the supports (they balance themselves, such as temperature)", f.cases.slice(0, 4).map((c) => c.name).join(", ")]
          : [...f.cases.filter((c) => c.mag >= 1).slice(0, brief ? 2 : 3).map((c) => `${c.name}: ${[c.h >= 1 ? `${kN(c.h)} sideways` : "", Math.abs(c.v) >= 1 ? `${kN(Math.abs(c.v))} ${c.v < 0 ? "down" : "up"}` : ""].filter(Boolean).join(", ")}`),
            f.cases.length > 3 ? `and ${f.cases.length - 3} more of the same kind` : ""].filter(Boolean),
        tags: f.mag >= 1 ? [{ at: famTip(M, f), text: kN(f.mag) }] : [] }));
      if (!fams.length) {
        const combo = new Set(S.cases.filter((c) => c.kind === "combo").map((c) => c.name));
        const rows = Object.entries(S.base).filter(([n]) => !combo.has(n) && !/mode/i.test(n)).map(([n, v]) => ({ n, fz: v[2] || 0, h: Math.hypot(v[0] || 0, v[1] || 0) }))
          .filter((r) => Math.abs(r.fz) + r.h > 1).sort((a, b) => Math.abs(b.fz) + b.h - (Math.abs(a.fz) + a.h));
        if (rows.length) add({ kind: "cases", dur: Math.min(9, 5 + rows.length * 0.05), cut: true, rows, cam0: C.quarter(), cam1: C.high(), head: "What it carries" });
      }
    }
    const nc = S.combos.length || S.cases.filter((c) => c.kind === "combo").length;
    if (nc) add({ kind: "combos", dur: Math.min(8, 4 + Math.min(nc, 12) * 0.3), cam0: C.high(), cam1: C.side(), head: `${nc} load combination${nc === 1 ? "" : "s"}` });
  }
  // what it rests on
  const R = S.reactions;
  if (!hide.has("reactions") && R.cases?.length && R.max) {
    const gov = R.cases.find((c) => c.case === R.max.down?.case) || R.cases[0];
    const lines = [];
    if (R.max.down) lines.push(`Largest compression ${kN(R.max.down.value)}, ${R.max.down.case}`);
    if (R.max.up && R.max.up.value < 0) lines.push(`Largest uplift ${kN(-R.max.up.value)}, ${R.max.up.case}`);
    else if (R.max.up) lines.push("No support lifts off: every reaction stays in compression");
    if (R.max.h) lines.push(`Largest horizontal reaction ${kN(R.max.h.value)}, ${R.max.h.case}`);
    const pos = supportPlaces(S, R);
    if (pos.length > 1) lines.push(`Highest by position: ${pos.map((p) => `${p.place} ${kN(p.value)}`).join(", ")}`);
    add({ kind: "reactions", dur: brief ? 6 : 8, cut: true, gov, cam0: C.under(), cam1: C.tall(), head: "Reactions at the supports", lines, legend: "relative", seabedAlpha: 0.35,
      tags: [R.max.down && { joint: R.max.down.joint, text: kN(R.max.down.value) }, R.max.up && R.max.up.value < 0 && { joint: R.max.up.joint, text: `uplift ${kN(-R.max.up.value)}` }].filter(Boolean) });
    if (pos.length > 1 && !brief) add({ kind: "reactions", dur: 6, gov, cam0: C.tall(), cam1: { ...C.top(), pitch: 62 }, head: "Reactions by position", legend: "relative", seabedAlpha: 0.35,
      lines: pos.map((p) => `${p.place[0].toUpperCase() + p.place.slice(1)} pile: ${kN(p.value)} (${p.case})`), tags: pos.map((p) => ({ joint: p.joint, text: `${p.place} ${kN(p.value)}` })) });
  }
  // pile capacities: compression, tension and lateral against what each group of piles can carry
  if (!hide.has("piles") && M.piles.length && R.cases?.length && M.groups.some((g) => g.comp != null || g.lat != null)) {
    const use = pileDemand(S, M);
    [["comp", "compression", "Vertical capacity (compression)"], ["tens", "tension", "Tension capacity"], ["lat", "lateral", "Lateral capacity"]].forEach(([k2, word, head]) => {
      if (!M.groups.some((g) => g[k2] != null)) return;
      const rows = use.map((d, i) => ({ i, d: d[k2], cap: M.allGroups[M.piles[i].group][k2], case: d[`${k2}Case`] })).filter((r) => r.cap != null && r.cap > 0);
      if (!rows.length || !rows.some((r) => r.d > 0)) return;
      rows.forEach((r) => (r.ratio = r.d / r.cap));
      rows.sort((a, b) => b.ratio - a.ratio);
      const over = rows.filter((r) => r.ratio > 1), worst = rows[0];
      const ratio = new Map(rows.map((r) => [r.i, r.ratio]));
      const load = new Map(rows.map((r) => [r.i, r.d]));
      add({ kind: "piles", what: k2, ratio, load, dur: brief ? 6 : 8, cut: k2 === "comp", cam0: C.tall(), cam1: { ...C.top(), pitch: 64, dist: 1.35 * M.L }, head: `${head}${use[0]?.head ? ", at the pile heads" : ""}`, legend: "util", seabedAlpha: 0.35,
        lines: [over.length ? `${over.length} pile${over.length === 1 ? "" : "s"} above the ${word} capacity, the worst ${n0(worst.ratio, 2)} (${tN(worst.d, set)} against ${tN(worst.cap, set)}, ${worst.case})` : `Every pile is within its ${word} capacity: the highest ${n0(worst.ratio, 2)} (${tN(worst.d, set)} against ${tN(worst.cap, set)})`,
          M.groups.filter((g) => g[k2] != null).map((g) => `${g.name}: ${tN(g[k2], set)}${(k2 === "lat" ? g.lateral_cases || g.cases : g.cases) ? ` under ${k2 === "lat" ? g.lateral_cases || g.cases : g.cases}` : ""}`).join(", ")],
        tags: rows.slice(0, 4).map((r) => ({ at: [M.piles[r.i].top[0], M.piles[r.i].top[1], M.deckTop + 1.2], text: `${tN(r.d, set)}: ${n0(r.ratio, 2)}` })) });
    });
  }
  // how it moves
  const D = S.displacements;
  if (!hide.has("movements") && D.cases?.length) {
    const calm = D.cases.filter((c) => !S.cases.some((q) => q.name === c.case && q.kind !== "combo" && isQuake(q.name, q.type)));
    const list = brief ? calm.slice(0, 1) : calm.slice(0, 3);
    const big = Math.max(...D.cases.map((c) => Math.max(c.h.value || 0, c.v.value || 0)), 1e-6);
    const k = set.exaggerate || Math.max(1, Math.round((0.06 * M.L) / big));
    list.forEach((c, i) => {
      const h = c.h.value || 0, v = c.v.value || 0;
      const dm = [0, 1, 2].map((d) => c.u.reduce((b, p) => Math.max(b, Math.abs(p?.[d] || 0)), 0));
      const lines = [`Largest movement ${mm(dm[0])} along X, ${mm(dm[1])} along Y and ${mm(dm[2])} up or down`, `Drawn ${n0(k)} times larger, swinging between rest and the loaded shape`];
      const tg = [{ joint: c.h.joint, text: `${mm(h)} on plan`, moved: true }];
      if (c.v.joint !== c.h.joint && v > 0.25 * h) tg.push({ joint: c.v.joint, text: `${mm(v)} vertical`, moved: true });
      add({ kind: "move", dur: brief ? 6 : 7.5, cut: i === 0, disp: c, k, cam0: i % 2 ? C.quarter2() : C.quarter(), cam1: i % 2 ? C.side() : C.high(), head: `Movement: ${c.case}`, lines, legend: "relative", tags: tg });
    });
  }
  // how it sways: the first modes of vibration
  if (!hide.has("quake") && S.modal?.length) add({ kind: "modal", dur: brief ? 6 : 8, cut: true, cam0: C.quarter2(), cam1: C.side(), head: "How it vibrates" });
  if (!hide.has("quake") && S.modes?.length) {
    const dirWords = (m) => { const parts = [["along X", m.ux], ["along Y", m.uy], ["in twist", m.rz]].filter(([, v]) => v > 0.05).sort((a, b) => b[1] - a[1]); return parts.length ? parts.map(([w, v]) => `${n0(v * 100)}% of the mass ${w}`).join(", ") : "a local mode"; };
    S.modes.slice(0, brief ? 1 : 3).forEach((m, i) => {
      const j = m.u.reduce((b, p, n) => (len(p) > len(m.u[b]) ? n : b), 0);
      add({ kind: "mode", dur: 6.5, cut: i === 0, mode: m, cam0: i % 2 ? C.side() : C.quarter2(), cam1: i % 2 ? C.quarter() : C.side(),
        head: `Mode ${m.mode}: period ${n0(m.period, 2)} s`, lines: [`${dirWords(m)}`, i === 0 ? "How the structure sways on its own, drawn much larger" : ""].filter(Boolean), legend: "relative",
        tags: [{ joint: j, text: `T = ${n0(m.period, 2)} s`, mode: true }] });
    });
  }
  // the earthquake: base shear of each seismic case against the weight, the structure shaking
  const quakes = S.cases.filter((c) => c.kind !== "combo" && isQuake(c.name, c.type));
  if (!hide.has("quake") && quakes.length) {
    const fams = families(S), dead = fams.find((f) => f.key === "dead");
    const W = dead ? dead.cases.reduce((a, c) => a + Math.max(0, -c.v), 0) : 0;
    const rows = quakes.map((c) => { const b = S.base?.[c.name] || [0, 0, 0]; return { name: c.name, vx: Math.abs(b[0] || 0), vy: Math.abs(b[1] || 0) }; });
    const d = S.displacements.cases?.find((c) => quakes.some((q) => q.name === c.case));
    const big = d ? Math.max(d.h.value || 0, d.v.value || 0, 1e-6) : 0;
    const sum = S.modal?.length ? S.modal[S.modal.length - 1] : null;
    quakes.slice(0, 2).forEach((q, qi) => {
      const r = rows[qi], U = caseU(S, q.name) || S.displacements.cases?.find((c) => c.case === q.name)?.u || null;
      const Mc = caseM(S, q.name), dir = r.vx >= r.vy ? "X" : "Y";
      const big = U ? Math.max(...U.map((p) => Math.hypot(p[0], p[1])), 1e-6) : 0;
      const jm = U ? U.reduce((b, p, n) => (Math.hypot(p[0], p[1]) > Math.hypot(U[b][0], U[b][1]) ? n : b), 0) : -1;
      const fm = Mc ? Mc.reduce((b, v, n) => (v > (Mc[b] || 0) ? n : b), 0) : -1;
      add({ kind: "act", style: "quake", case: q.name, U, Mc, mode: U ? null : S.modes?.find((m) => (dir === "X" ? m.ux : m.uy) > 0.3) || S.modes?.[0] || null,
        k: U ? set.exaggerate || Math.max(1, Math.round((0.05 * M.L) / big)) : 0, dur: brief ? 7 : 9, cut: qi === 0,
        cam0: qi ? C.quarter2() : C.quarter(), cam1: qi ? C.side() : C.high(), head: `Earthquake ${q.name}`, legend: "relative",
        lines: [`Base shear ${kN(Math.max(r.vx, r.vy))} along ${dir}${W > 0 ? `, ${n0((100 * Math.max(r.vx, r.vy)) / W, 1)}% of the dead weight` : ""}`,
          U ? `Moves up to ${mm(big)} on plan, drawn ${n0(set.exaggerate || Math.max(1, Math.round((0.05 * M.L) / big)))} times larger` : "Shaking in the shape of its main mode along this direction",
          Mc ? `Members coloured by their bending under ${q.name}: largest ${n0(Mc[fm])} kN m in ${fr(fm)}` : "",
          qi === 0 && sum && sum.sumux != null ? `${S.modal.length} modes carry ${n0(100 * sum.sumux)}% of the mass along X and ${n0(100 * sum.sumuy)}% along Y` : ""].filter(Boolean),
        tags: [U && jm >= 0 ? { joint: jm, text: mm(big), moved: true } : null, Mc && fm >= 0 ? { frame: fm, text: `${n0(Mc[fm])} kN m` } : null].filter(Boolean) });
    });
  }
  // waves and current on the structure, swinging slowly
  if (!hide.has("quake")) {
    const fam = families(S).find((f) => f.key === "wave");
    const pick = fam?.cases.filter((c) => caseU(S, c.name) || caseM(S, c.name)).sort((a, b) => b.h - a.h)[0];
    if (pick) {
      const U = caseU(S, pick.name), Mc = caseM(S, pick.name);
      const big = U ? Math.max(...U.map((p) => Math.hypot(p[0], p[1], p[2])), 1e-6) : 0;
      const fm = Mc ? Mc.reduce((b, v, n) => (v > (Mc[b] || 0) ? n : b), 0) : -1;
      add({ kind: "act", style: "wave", case: pick.name, U, Mc, k: U ? set.exaggerate || Math.max(1, Math.round((0.05 * M.L) / big)) : 0, dur: brief ? 7 : 9, cut: true,
        cam0: C.low(), cam1: { ...C.quarter(), pitch: 12 }, head: `Waves: ${pick.name}`, legend: "relative",
        lines: [`${kN(pick.h)} sideways${Math.abs(pick.v) >= 1 ? ` and ${kN(Math.abs(pick.v))} ${pick.v > 0 ? "up" : "down"}` : ""} on the structure`,
          U ? `Moves up to ${mm(big)}, drawn ${n0(set.exaggerate || Math.max(1, Math.round((0.05 * M.L) / big)))} times larger` : "",
          Mc ? `Largest bending ${n0(Mc[fm])} kN m in ${fr(fm)}` : ""].filter(Boolean),
        tags: Mc && fm >= 0 ? [{ frame: fm, text: `${n0(Mc[fm])} kN m` }] : [] });
    }
  }
  // how hard the members work
  const F = S.forces;
  if (!hide.has("forces") && F.M?.length) {
    const top = (arr) => arr.reduce((b, v, i) => (v != null && v > (arr[b] ?? -1) ? i : b), 0);
    const idx = S.frames.ids;
    const iM = top(idx.map((i) => F.M[i])), iN = top(idx.map((i) => F.compression?.[i] ?? F.N[i])), iV = top(idx.map((i) => F.V[i]));
    add({ kind: "forces", dur: brief ? 6 : 7, cut: true, what: "M", cam0: C.high(), cam1: C.quarter2(), head: "Bending in the members", legend: "relative",
      lines: [`Largest bending moment ${n0(F.M[idx[iM]])} kN m in ${fr(iM)} (${F.case.M[idx[iM]] || ""})`, `Largest compression ${kN(F.compression?.[idx[iN]] ?? F.N[idx[iN]])} in ${fr(iN)}, largest shear ${kN(F.V[idx[iV]])} in ${fr(iV)}`],
      tags: [{ frame: iM, text: `${n0(F.M[idx[iM]])} kN m` }] });
    if (!brief) {
      const iT = F.tension ? top(idx.map((i) => F.tension[i])) : -1;
      const tg = [{ frame: iN, text: `${kN(F.compression?.[idx[iN]] ?? F.N[idx[iN]])} compression` }];
      if (iT >= 0 && F.tension[idx[iT]] > 0) tg.push({ frame: iT, text: `${kN(F.tension[idx[iT]])} tension` });
      add({ kind: "forces", dur: 6, what: "N", cam0: C.quarter2(), cam1: C.low(), head: "Axial force in the members", legend: "axial",
        lines: ["Red members are pulled (tension), green ones are pushed (compression)",
          `Largest compression ${kN(F.compression?.[idx[iN]] ?? F.N[idx[iN]])} in ${fr(iN)}${iT >= 0 && F.tension[idx[iT]] > 0 ? `, largest tension ${kN(F.tension[idx[iT]])} in ${fr(iT)}` : ""}`], tags: tg });
    }
  }
  if (!hide.has("forces") && S.shells?.M11?.length && na) {
    const v = S.areas.ids.map((i) => Math.max(S.shells.M11[i] || 0, S.shells.M22[i] || 0));
    const k = v.reduce((b, x, i) => (x > v[b] ? i : b), 0);
    add({ kind: "plates", dur: 5, cam0: C.top(), cam1: C.high(), head: "Bending in the plates", legend: "relative", lines: [`Largest plate moment ${n0(v[k])} kN m/m`] });
  }
  // utilisation from SAP2000's design
  const design = Object.entries(S.design || {}).map(([i, d]) => ({ i: +i, ...d })).filter((d) => d.ratio != null);
  if (!hide.has("design") && design.length) {
    design.sort((a, b) => b.ratio - a.ratio);
    const worst = design[0], pos = S.frames.ids.indexOf(worst.i);
    const over = design.filter((d) => d.ratio > 1).length;
    const lines = [`${n0(design.length)} members designed in SAP2000. Highest ratio ${n0(worst.ratio, 2)}: ${S.frames.names?.[pos] ? "Frame " + S.frames.names[pos] : ""} (${worst.what}${worst.combo ? ", " + worst.combo : ""})`,
      over ? `${over} member${over === 1 ? "" : "s"} above 1.0` : "Every member is within its capacity"];
    add({ kind: "design", dur: brief ? 7 : 9, cut: true, cam0: { ...C.quarter(), pitch: 10 }, cam1: focusCam(M, pos), head: "Utilisation ratios", lines, legend: "util", top: design.slice(0, 8), tags: [{ frame: pos, text: n0(worst.ratio, 2) }] });
  }
  // without design tables: the von Mises stress in each member against the steel's yield strength
  const fy = (set.steel_fy || 355) * 1000;
  if (!hide.has("design") && !design.length && S.stress?.count && M.sect.some((q) => q.steel)) {
    const ids = S.frames.ids, ratio = ids.map((i) => (S.stress.svm[i] != null ? S.stress.svm[i] / fy : null));
    let worst = -1; ratio.forEach((v, n) => { if (v != null && (worst < 0 || v > ratio[worst])) worst = n; });
    const top = ratio.map((v, n) => ({ i: ids[n], ratio: v })).filter((d) => d.ratio != null).sort((a, b) => b.ratio - a.ratio).slice(0, 8);
    const over = ratio.filter((v) => v != null && v > 1).length;
    add({ kind: "stress", dur: brief ? 7 : 9, cut: true, ratio, cam0: C.quarter(), cam1: focusCam(M, worst), head: "Stress ratios", legend: "util", top,
      lines: [`von Mises stress against fy = ${n0(fy / 1000)} MPa, the worst output case at each member: highest ${n0(ratio[worst], 2)} in Frame ${S.frames.names?.[worst] ?? ""} (${S.stress.case[ids[worst]] || ""})`,
        S.stress.count < ids.length ? `${n0(ids.length - S.stress.count)} members have no stresses in the file and are grey` : over ? `${over} member${over === 1 ? "" : "s"} above 1.0` : "Every member is below yield"],
      tags: worst >= 0 ? [{ frame: worst, text: n0(ratio[worst], 2) }] : [] });
  }
  add({ kind: "summary", dur: brief ? 7 : 9, cut: true, cam0: C.high(), cam1: C.quarter2(), legend: design.length ? "util" : null });
  add({ kind: "end", dur: 5, cam0: C.quarter2(), cam1: { ...C.aerial(), dist: C.aerial().dist * 1.2 }, title: true });
  let t = 0;
  shots.forEach((s) => { s.t0 = t; t += s.dur; });
  return { shots, total: t, title };
}

function frameLen(S, i) { const k = S.frames.ids.indexOf(i); if (k < 0) return 0; const [a, b] = S.frames.ends[k]; const p = S.joints[a], q = S.joints[b]; return Math.hypot(p[0] - q[0], p[1] - q[1], p[2] - q[2]); }
function areaOf(S, i) {
  const k = S.areas.ids.indexOf(i); if (k < 0) return 0;
  const P = S.areas.joints[k].map((j) => S.joints[j]);
  let x = 0, y = 0, z = 0;
  for (let n = 1; n + 1 < P.length; n++) {
    const a = P[0], b = P[n], c = P[n + 1], u = [b[0] - a[0], b[1] - a[1], b[2] - a[2]], v = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
    x += u[1] * v[2] - u[2] * v[1]; y += u[2] * v[0] - u[0] * v[2]; z += u[0] * v[1] - u[1] * v[0];
  }
  return Math.hypot(x, y, z) / 2;
}
// each load of a pattern: where it acts, its direction, its size and kind (area kN/m², line kN/m, point kN)
function loadItems(S, pat) {
  const it = S.loads[pat]?.items || {}, out = [];
  const fIx = new Map(S.frames.ids.map((i, k) => [i, k])), aIx = new Map(S.areas.ids.map((i, k) => [i, k]));
  (it.joint || []).forEach(([j, a, b, c]) => { const v = Math.hypot(a, b, c); if (S.joints[j] && v > 0) out.push({ kind: "point", p: S.joints[j], d: [a / v, b / v, c / v], v }); });
  (it.point || []).forEach(([i, d, f, r0]) => { const k = fIx.get(i); if (k == null || !f) return; const [a, b] = S.frames.ends[k], P = S.joints[a], Q = S.joints[b]; const e = dirOf(d).map((x) => x * Math.sign(f)); out.push({ kind: "point", p: P.map((v, n) => v + (Q[n] - v) * r0), d: e, v: Math.abs(f) }); });
  (it.frame || []).forEach(([i, d, w]) => { const k = fIx.get(i); if (k == null || !w) return; const [a, b] = S.frames.ends[k]; const e = dirOf(d).map((x) => x * Math.sign(w)); out.push({ kind: "line", a: S.joints[a], b: S.joints[b], p: S.joints[a].map((v, n) => (v + S.joints[b][n]) / 2), d: e, v: Math.abs(w), frame: k }); });
  (it.area || []).forEach(([i, d, w]) => { const k = aIx.get(i); if (k == null || !w) return; const pts = S.areas.joints[k].map((j) => S.joints[j]); const c = [0, 1, 2].map((n) => pts.reduce((s2, q) => s2 + q[n], 0) / pts.length); out.push({ kind: "area", pts, p: c, d: dirOf(d).map((x) => x * Math.sign(w)), v: Math.abs(w), area: k }); });
  out.forEach((o) => (o.tag = o.p));
  return out;
}
function loadKind(pat, items) {
  const side = items.length && items.filter((o) => Math.abs(o.d[2]) < 0.5).length >= items.length / 2;
  if (!side) return "gravity";
  if (/MOOR|BOLL|ROPE|LINE/i.test(pat)) return "mooring";
  if (/BERTH|FEND|IMPACT/i.test(pat)) return "berthing";
  return "horizontal";
}
// families of load cases, from their names, each with what it puts on the structure (the base reactions turned round)
const FAMILIES = [
  ["dead", "Own weight and finishes", /DEAD|SDL|SIDL|SELF|SUPER|FINISH|SOIL|BUOY|FILL/],
  ["live", "Live loads and vehicles", /LIVE|TRUCK|CRANE|HOPPER|SURCH|UDL|VEHIC|TRAFF|CONSTR|STS|RTG|LL/],
  ["berthing", "Ships berthing", /BERTH|FENDER|IMPACT/],
  ["mooring", "Mooring line pulls", /BOLL|MOOR/],
  ["wind", "Wind", /WIND/],
  ["wave", "Waves and current", /WAVE|CURR|TIDE|HYDRO/],
  ["temp", "Temperature and shrinkage", /TEMP|SHRINK|CREEP/],
];
function families(S) {
  const out = new Map();
  S.cases.forEach((c) => {
    if (c.kind === "combo" || /mode/i.test(c.name) || /^ENV/i.test(c.name)) return;
    const b = S.base?.[c.name];
    if (!b) return;
    let f = isQuake(c.name, c.type) ? ["quake", "Earthquake"] : FAMILIES.find(([, , re]) => re.test(c.name.toUpperCase()));
    if (!f) return;
    const [key, label] = f, L = [-(b[0] || 0), -(b[1] || 0), -(b[2] || 0)];
    if (!out.has(key)) out.set(key, { key, label, cases: [] });
    out.get(key).cases.push({ name: c.name, L, h: Math.hypot(L[0], L[1]), v: L[2], mag: Math.hypot(...L) });
  });
  const order = [...FAMILIES.map(([k]) => k), "quake"];
  return [...out.values()].map((f) => { f.cases.sort((a, b) => b.mag - a.mag); f.rep = f.cases[0]; f.mag = f.rep.mag; return f; }).sort((a, b) => order.indexOf(a.key) - order.indexOf(b.key));
}
// where a family's total load meets the structure: on top of the deck for weight, at the deck's edge for sideways loads
function famTip(M, f) {
  const c = M.centre, L = f.rep.L, h = Math.hypot(L[0], L[1]);
  if (h < Math.abs(L[2])) return [c[0], c[1], M.top];
  const e = [L[0] / h, L[1] / h], S = M.S;
  let reach = 0;
  S.joints.forEach((p) => { if (p && p[2] > M.top - 3) reach = Math.max(reach, -((p[0] - c[0]) * e[0] + (p[1] - c[1]) * e[1])); });
  return [c[0] - e[0] * reach, c[1] - e[1] * reach, M.top];
}
// one case's joint movements and member bending from the full results kept with the summary
function caseU(S, name) {
  const b = S.full?.disp?.[name];
  if (!b) return null;
  const S2 = S._u || (S._u = new Map());
  if (!S2.has(name)) { const a = fromB64(b), out = []; for (let i = 0; i < a.length / 3; i++) out.push([0, 1, 2].map((k) => (isFinite(a[i * 3 + k]) ? a[i * 3 + k] : 0))); S2.set(name, out); }
  return S2.get(name);
}
function caseM(S, name) {
  const b = S.full?.frames?.[name];
  if (!b) return null;
  const S2 = S._m || (S._m = new Map());
  if (!S2.has(name)) { const a = fromB64(b), out = []; for (let i = 0; i < a.length / 6; i++) out.push(Math.hypot(isFinite(a[i * 6 + 4]) ? a[i * 6 + 4] : 0, isFinite(a[i * 6 + 5]) ? a[i * 6 + 5] : 0)); S2.set(name, out); }
  return S2.get(name);
}
// supports by where they stand on plan: corner, edge (the long sides), side (the short ends) or internal;
// the highest compression of each
function supportPlaces(S, R) {
  const sup = R.supports, P = sup.map((j) => S.joints[j]).filter(Boolean);
  if (P.length < 6) return [];
  const cx = P.reduce((a, p) => a + p[0], 0) / P.length, cy = P.reduce((a, p) => a + p[1], 0) / P.length;
  let sxx = 0, syy = 0, sxy = 0;
  P.forEach((p) => { sxx += (p[0] - cx) ** 2; syy += (p[1] - cy) ** 2; sxy += (p[0] - cx) * (p[1] - cy); });
  const th = 0.5 * Math.atan2(2 * sxy, sxx - syy), ex = [Math.cos(th), Math.sin(th)];
  const uv = sup.map((j) => { const p = S.joints[j]; return p ? [(p[0] - cx) * ex[0] + (p[1] - cy) * ex[1], -(p[0] - cx) * ex[1] + (p[1] - cy) * ex[0]] : null; });
  const us = uv.filter(Boolean).map((q) => q[0]), vs = uv.filter(Boolean).map((q) => q[1]);
  const [u0, u1, v0, v1] = [Math.min(...us), Math.max(...us), Math.min(...vs), Math.max(...vs)];
  const tu = Math.max(0.8, 0.06 * (u1 - u0)), tv = Math.max(0.8, 0.06 * (v1 - v0));
  const best = {};
  const combo = new Set(S.cases.filter((c) => c.kind === "combo").map((c) => c.name));
  const cases = R.cases.filter((c) => !combo.size || combo.has(c.case));
  uv.forEach((q, k) => {
    if (!q) return;
    const end = q[0] < u0 + tu || q[0] > u1 - tu, edge = q[1] < v0 + tv || q[1] > v1 - tv;
    const place = end && edge ? "corner" : edge ? "edge" : end ? "side" : "internal";
    cases.forEach((c) => { const v = c.fz[k]; if (v != null && (!best[place] || v > best[place].value)) best[place] = { place, value: v, joint: sup[k], case: c.case }; });
  });
  return ["corner", "edge", "side", "internal"].map((k) => best[k]).filter(Boolean);
}
// each pile's largest compression, tension and lateral force over the cases (combinations where there are some)
// at the pile head (the force in its top member) where the file has member forces for every case,
// else at its support
function pileDemand(S, M) {
  const R = S.reactions, ix = new Map(R.supports.map((j, k) => [j, k]));
  const combo = new Set(S.cases.filter((c) => c.kind === "combo").map((c) => c.name));
  const all = R.cases.filter((c) => !combo.size || combo.has(c.case));
  const byGroup = new Map(), latGroup = new Map();
  const full = S.full?.frames || {};
  if (Object.keys(full).length) {
    const names = (S.full.cases || Object.keys(full)).filter((n) => full[n]);
    const allN = names.filter((n) => !combo.size || combo.has(n)), dec = new Map();
    const get = (n) => { if (!dec.has(n)) dec.set(n, fromB64(full[n])); return dec.get(n); };
    return M.piles.map((p) => {
      const o = { comp: 0, tens: 0, lat: 0, head: true }, top = p.frames[p.frames.length - 1], g = M.allGroups[p.group];
      const pick = (res) => (res?.length ? names.filter((n) => res.some((re) => re.test(n))) : allN);
      pick(g?.caseRes).forEach((n) => { const a = get(n), P = a[top * 6]; if (!isFinite(P)) return; if (-P > o.comp) { o.comp = -P; o.compCase = n; } if (P > o.tens) { o.tens = P; o.tensCase = n; } });
      pick(g?.latRes).forEach((n) => { const a = get(n), v = Math.hypot(a[top * 6 + 1] || 0, a[top * 6 + 2] || 0); if (isFinite(v) && v > o.lat) { o.lat = v; o.latCase = n; } });
      return o;
    });
  }
  return M.piles.map((p) => {
    const k = ix.get(p.support), o = { comp: 0, tens: 0, lat: 0 };
    if (k == null) return o;
    const g = M.allGroups[p.group];
    if (!byGroup.has(p.group)) byGroup.set(p.group, g?.caseRes?.length ? R.cases.filter((c) => g.caseRes.some((re) => re.test(c.case))) : all);
    if (!latGroup.has(p.group)) latGroup.set(p.group, g?.latRes?.length ? R.cases.filter((c) => g.latRes.some((re) => re.test(c.case))) : all);
    latGroup.get(p.group).forEach((c) => { const h = c.h[k]; if (h != null && h > o.lat) { o.lat = h; o.latCase = c.case; } });
    byGroup.get(p.group).forEach((c) => {
      const up = c.fz[k], dn = c.fz_min[k], h = c.h[k];
      if (up != null && up > o.comp) { o.comp = up; o.compCase = c.case; }
      if (dn != null && -dn > o.tens) { o.tens = -dn; o.tensCase = c.case; }
    });
    return o;
  });
}
// a family's camera: from the side its sideways load comes from, so the arrow is seen whole
function famCam(M, f, C) {
  const L = f.rep.L, h = Math.hypot(L[0], L[1]);
  if (f.mag < 1 || h < Math.abs(L[2])) return C.quarter2();
  const from = Math.atan2(-L[1], -L[0]) * 180 / Math.PI;
  return { ...C.quarter(), yaw: from - 35, pitch: 24, dist: 1.45 * M.L };
}
// a value that most points stay under (so one odd point does not make the rest look still)
function p95(U) {
  const v = U.map(len).filter((x) => x > 0).sort((a, b) => a - b);
  return v.length ? v[Math.floor(v.length * 0.95)] || v[v.length - 1] : 1;
}
// how far along its swing a moving shot is: 0 at rest, 1 at the full loaded shape (or -1 for a mode)
function swing(s, u) {
  const t = u * s.dur, inn = ease(u / 0.12), out = ease((1 - u) / 0.1);
  if (s.kind === "move") return (0.5 - 0.5 * Math.cos((2 * Math.PI * t) / 2.6)) * out;
  if (s.kind === "mode") return Math.sin((2 * Math.PI * t) / 1.6) * inn * out;
  if (s.kind === "quake" || (s.kind === "act" && s.style === "quake")) {
    // an earthquake record: building up, strongest in the middle, dying away, several frequencies
    const env = Math.sin(Math.PI * clamp(u, 0, 1)) ** 1.5;
    return (0.62 * Math.sin(2 * Math.PI * 1.15 * t) + 0.28 * Math.sin(2 * Math.PI * 2.1 * t + 1.3) + 0.18 * Math.sin(2 * Math.PI * 0.55 * t + 0.4)) * env * 1.15;
  }
  if (s.kind === "act") return Math.sin((2 * Math.PI * t) / 4.2) * ease(u / 0.15) * out;
  return 0;
}
function frameMid(S, n) { const [a, b] = S.frames.ends[n]; const p = S.joints[a], q = S.joints[b]; return [(p[0] + q[0]) / 2, (p[1] + q[1]) / 2, (p[2] + q[2]) / 2]; }
function focusCam(M, pos) {
  if (pos < 0) return cams(M).quarter();
  const p = frameMid(M.S, pos);
  // under the deck, a low look from the sea side, so the deck does not hide the member
  const low = p[2] < M.top - 1.5;
  return { yaw: M.seaYaw - 30, pitch: low ? 6 : 18, dist: Math.max(0.45 * M.L, 12), tx: p[0], ty: p[1], tz: low ? Math.max(p[2], M.wl - 2) : p[2] };
}

// --- what each shot draws -----------------------------------------------------------------------
function layers(F, s, u) {
  const { M } = F, S = M.S, out = [];
  const keyed = (key, make) => { if (!F.cache.has(key)) F.cache.set(key, make()); return F.cache.get(key); };
  const plain = { frame: () => null, area: () => null };
  const bed = keyed("bed", () => { const m = new Mesh(), R = M.ext * 3, c = M.centre; m.quad([c[0] - R, c[1] - R, M.seabed], [c[0] + R, c[1] - R, M.seabed], [c[0] + R, c[1] + R, M.seabed], [c[0] - R, c[1] + R, M.seabed], BED); return m; });
  out.push({ mesh: bed, opacity: s.seabedAlpha ?? 0.92 });
  // where the seabed is see-through (to show the pile toes), a dark floor below the toes so the sea does not show the sky
  if ((s.seabedAlpha ?? 0.92) < 0.9) out.push({ mesh: keyed("deep", () => { const m = new Mesh(), R = M.ext * 3, c = M.centre, z = Math.min(M.bottom, M.seabed) - 4; m.quad([c[0] - R, c[1] - R, z], [c[0] + R, c[1] - R, z], [c[0] + R, c[1] + R, z], [c[0] - R, c[1] + R, z], hex("#33424A")); return m; }), opacity: 1 });
  const grey = () => keyed("ghost", () => structure(M, { frame: () => GHOST, area: () => GHOST }));
  const grouped = { frame: (n) => (M.groups.length > 1 && M.pileOf.has(n) ? hex(M.allGroups[M.piles[M.pileOf.get(n)].group].colour) : null), area: () => null };
  if (s.kind === "build") {
    const isPile = (n) => M.pileOf.has(n), zLow = Math.min(...M.piles.map((p) => M.J[p.support][2]));
    if (s.stage === "piles") {
      const z = lerp(zLow, M.top + 0.5, ease(u / 0.7));
      out.push({ mesh: structure(M, grouped, { pick: { frame: isPile, area: () => false }, clipZ: z }), opacity: 1 });
    } else {
      out.push({ mesh: keyed("build-piles", () => structure(M, grouped, { pick: { frame: isPile, area: () => false } })), opacity: 1 });
      const lo = M.S.bbox[0][0], hi2 = M.S.bbox[1][0], sweep = lerp(lo - 1, hi2 + 1, ease(u / 0.65));
      const midX = (js) => js.reduce((a, j) => a + M.J[j][0], 0) / js.length;
      if (s.stage === "beams") out.push({ mesh: structure(M, grouped, { pick: { frame: (n) => !isPile(n) && midX(S.frames.ends[n]) <= sweep, area: () => false } }), opacity: 1 });
      else {
        out.push({ mesh: keyed("build-beams", () => structure(M, grouped, { pick: { frame: (n) => !isPile(n), area: () => false } })), opacity: 1 });
        out.push({ mesh: structure(M, plain, { pick: { frame: () => false, area: (n) => midX(S.areas.joints[n]) <= sweep } }), opacity: 1 });
      }
    }
  } else if (s.kind === "piles") {
    const pulse = 0.5 + 0.5 * Math.sin(u * s.dur * 5);
    out.push({ mesh: keyed("pile-ghost", () => structure(M, { frame: (n) => (M.pileOf.has(n) ? null : GHOST), area: () => GHOST }, { pick: { frame: (n) => !M.pileOf.has(n), area: () => true } })), opacity: 0.35 });
    out.push({ mesh: keyed(`piles-${s.what}`, () => structure(M, { frame: (n) => { const r = s.ratio.get(M.pileOf.get(n)); return r != null ? rgb(heat(Math.min(r, 1.2))) : GHOST; }, area: () => null }, { pick: { frame: (n) => M.pileOf.has(n) && (s.ratio.get(M.pileOf.get(n)) ?? 0) <= 1, area: () => false } })), opacity: 1 });
    out.push({ mesh: keyed(`piles-over-${s.what}`, () => structure(M, { frame: () => rgb(heat(1.1)), area: () => null }, { pick: { frame: (n) => M.pileOf.has(n) && (s.ratio.get(M.pileOf.get(n)) ?? 0) > 1, area: () => false } })), opacity: 1, glow: 0.15 + 0.45 * pulse });
    // a bubble over each pile head: its size the load, its colour the ratio, so the most loaded stand out
    if (s.load) {
      const big = Math.max(...s.load.values(), 1e-9), g = ease((u - 0.15) / 0.3), b = new Mesh();
      M.piles.forEach((p, i) => {
        const v = s.load.get(i); if (!(v > 0) || g <= 0) return;
        const r = Math.max(0.4, (0.045 * M.L) * Math.sqrt(v / big)) * g, z = M.deckTop + 1.2;
        b.frustum([p.top[0], p.top[1], z - r * 0.35], [p.top[0], p.top[1], z], r * 0.8, r, rgb(heat(Math.min(s.ratio.get(i) ?? 0, 1.2))), 18);
        b.frustum([p.top[0], p.top[1], z], [p.top[0], p.top[1], z + r * 0.45], r, r * 0.55, rgb(heat(Math.min(s.ratio.get(i) ?? 0, 1.2))), 18);
        b.member([p.top[0], p.top[1], p.top[2]], [p.top[0], p.top[1], z - r * 0.35], 0.08, 0.08, hex("#DCE7EC"));
      });
      out.push({ mesh: b, opacity: 1, glow: 0.12 });
    }
  } else if (s.kind === "act") {
    const g = swing(s, u), U = s.U || s.mode?.u, k = s.U ? s.k : 0.05 * M.L;
    const Mc = s.Mc, mmax = Mc ? Math.max(...Mc, 1e-9) : 0, big = U ? (s.big ?? (s.big = p95(U))) : 1;
    out.push({ mesh: grey(), opacity: 0.18 });
    const col = Mc ? { frame: (n) => rel(Mc[n] || 0, mmax), area: () => null } : U ? {
      frame: (n) => { const [a, b] = S.frames.ends[n]; return rel(Math.max(len(U[a]), len(U[b])), big); },
      area: (n) => rel(Math.max(...S.areas.joints[n].map((j) => len(U[j]))), big),
    } : plain;
    out.push({ mesh: U ? structure(M, col, { shift: U, k: k * g }) : keyed(`act-${s.case}`, () => structure(M, col)), opacity: 1 });
  } else if (s.kind === "loads") {
    out.push({ mesh: keyed("plain", () => structure(M, plain)), opacity: 1 });
    (s.real ? realScene(M, s, u, u * s.dur) : loadScene(M, s, u)).forEach((l) => out.push(l));
  } else if (s.kind === "family") {
    out.push({ mesh: keyed("plain", () => structure(M, plain)), opacity: 1 });
    out.push({ mesh: familyArrows(M, s.fam, u), opacity: 1, glow: 0.3 });
  } else if (s.kind === "reactions") {
    out.push({ mesh: keyed("plain", () => structure(M, plain)), opacity: 1 });
    out.push({ mesh: reactionArrows(M, s.gov, ease((u - 0.1) / 0.45)), opacity: 1, glow: 0.25 });
  } else if (s.kind === "move") {
    const big = Math.max(s.disp.h.value || 0, s.disp.v.value || 0, 1e-9);
    const g = swing(s, u);
    const U = s.disp.u;
    out.push({ mesh: grey(), opacity: 0.22 });
    out.push({ mesh: structure(M, {
      frame: (n) => { const [a, b] = S.frames.ends[n]; return rel(Math.max(len(U[a]), len(U[b])), big); },
      area: (n) => rel(Math.max(...S.areas.joints[n].map((j) => len(U[j]))), big),
    }, { shift: U, k: s.k * g }), opacity: 1 });
  } else if (s.kind === "mode") {
    const U = s.mode.u, g = swing(s, u), amp = 0.08 * M.L, big = s.big ?? (s.big = p95(U));
    out.push({ mesh: grey(), opacity: 0.22 });
    out.push({ mesh: structure(M, {
      frame: (n) => { const [a, b] = S.frames.ends[n]; return rel(Math.max(len(U[a]), len(U[b])), big); },
      area: (n) => rel(Math.max(...S.areas.joints[n].map((j) => len(U[j]))), big),
    }, { shift: U, k: amp * g }), opacity: 1 });
  } else if (s.kind === "quake" && (s.disp || s.mode)) {
    const U = s.disp ? s.disp.u : s.mode.u, k = s.disp ? s.k : 0.05 * M.L, g = swing(s, u);
    const big = s.big ?? (s.big = p95(U));
    out.push({ mesh: grey(), opacity: 0.22 });
    out.push({ mesh: structure(M, {
      frame: (n) => { const [a, b] = S.frames.ends[n]; return rel(Math.max(len(U[a]), len(U[b])), big); },
      area: (n) => rel(Math.max(...S.areas.joints[n].map((j) => len(U[j]))), big),
    }, { shift: U, k: k * g }), opacity: 1 });
  } else if (s.kind === "forces" && s.what === "N" && S.forces.tension) {
    const ids = S.frames.ids, T = S.forces.tension, Cm = S.forces.compression || [];
    const max = Math.max(...ids.map((i) => Math.max(T[i] || 0, Cm[i] || 0)), 1e-9);
    out.push({ mesh: keyed("axial", () => structure(M, { frame: (n) => { const t = T[ids[n]] || 0, c = Cm[ids[n]] || 0; return t >= c ? mix(NEUTRAL, TENSION, Math.sqrt(t / max)) : mix(NEUTRAL, COMPRESSION, Math.sqrt(c / max)); }, area: () => null })), opacity: 1 });
  } else if (s.kind === "stress") {
    out.push({ mesh: keyed("stress", () => structure(M, { frame: (n) => (s.ratio[n] != null ? rgb(heat(s.ratio[n])) : GHOST), area: () => null })), opacity: 1 });
  } else if (s.kind === "forces") {
    const F2 = S.forces[s.what] || [], ids = S.frames.ids, max = Math.max(...ids.map((i) => F2[i] || 0), 1e-9);
    out.push({ mesh: keyed(`forces${s.what}`, () => structure(M, { frame: (n) => rel(F2[ids[n]] || 0, max), area: () => null })), opacity: 1 });
  } else if (s.kind === "plates") {
    const v = S.areas.ids.map((i) => Math.max(S.shells.M11[i] || 0, S.shells.M22[i] || 0)), max = Math.max(...v, 1e-9);
    out.push({ mesh: keyed("plates", () => structure(M, { frame: () => GHOST, area: (n) => rel(v[n], max) })), opacity: 1 });
  } else if (s.kind === "design" || s.kind === "summary" || s.kind === "end") {
    const has = Object.keys(S.design || {}).length, st = F.shots.find((q) => q.kind === "stress");
    out.push({ mesh: has ? keyed("util", () => structure(M, { frame: (n) => { const d = S.design[S.frames.ids[n]]; return d ? rgb(heat(d.ratio)) : null; }, area: () => null }))
      : st ? keyed("stress", () => structure(M, { frame: (n) => (st.ratio[n] != null ? rgb(heat(st.ratio[n])) : GHOST), area: () => null })) : keyed("plain", () => structure(M, plain)), opacity: 1 });
  } else {
    out.push({ mesh: keyed("plain", () => structure(M, plain)), opacity: 1 });
  }
  return out;
}
const len = (p) => (p ? Math.hypot(p[0], p[1], p[2]) : 0);
const NEUTRAL = hex("#D5D9DC"), TENSION = hex("#D7301F"), COMPRESSION = hex("#1A9850");
const mix = (a, b, f) => a.map((v, k) => v + (b[k] - v) * clamp(f, 0, 1));

// a pattern's loads drawn as what they are: a block of load standing on the deck for loads spread
// over it (its height the intensity, arrows raining onto it), a fence along a member for line loads,
// heavy arrows dropping onto the points for point loads, and for sideways loads a mooring line
// pulling from the bollard or a ship pressing on the fender
function loadScene(M, s, u) {
  const S = M.S, items = s.items, g = ease(u / 0.35), t = u * s.dur, L = M.L, out = [];
  const vmax = (kind) => Math.max(...items.filter((o) => o.kind === kind).map((o) => o.v), 1e-9);
  const mx = { area: vmax("area"), line: vmax("line"), point: vmax("point") };
  const block = new Mesh(), rain = new Mesh(), heavy = new Mesh();
  const H0 = 0.05 * L, rr = L / 520;
  const step = Math.max(1, Math.ceil(items.length / 900));
  items.forEach((o, n) => {
    if (n % step) return;
    const f = o.v / mx[o.kind], col = rel(f, 1);
    const side = Math.abs(o.d[2]) < 0.5;
    if (!side && o.kind === "area") {
      const k = S.areas.section[o.area], th = (M.sect[k]?.t || 0.4) / 2;
      const z0 = Math.max(...o.pts.map((q) => q[2])) + th + 0.02, h = H0 * (0.25 + 0.75 * f) * g;
      const bot = o.pts.map((q) => [q[0], q[1], z0]), top = o.pts.map((q) => [q[0], q[1], z0 + h]);
      block.panel(top, col);
      for (let i = 0; i < bot.length; i++) { const j = (i + 1) % bot.length; block.quad(bot[i], bot[j], top[j], top[i], col); }
      o.tag = [o.p[0], o.p[1], z0 + h];
      if (g > 0.6 && n % (step * Math.max(1, Math.round(items.length / 120))) === 0) {
        const ph = (t * 0.8 + ((n * 0.6180339) % 1)) % 1, a = 0.06 * L, tip = z0 + h + (1 - ph) * 0.16 * L;
        rain.arrow([o.p[0], o.p[1], tip + a], [o.p[0], o.p[1], tip], rr * 1.3, col);
      }
    } else if (!side && o.kind === "line") {
      const h = H0 * (0.3 + 0.7 * f) * g, up = 0.15;
      const a0 = [o.a[0], o.a[1], o.a[2] + up], b0 = [o.b[0], o.b[1], o.b[2] + up];
      const a1 = [a0[0], a0[1], a0[2] + h], b1 = [b0[0], b0[1], b0[2] + h];
      block.quad(a0, b0, b1, a1, col); block.quad(a0, a1, b1, b0, col);
      o.tag = [(a1[0] + b1[0]) / 2, (a1[1] + b1[1]) / 2, a1[2]];
      if (g > 0.6) [0.25, 0.5, 0.75].forEach((q, m2) => {
        const ph = (t * 0.8 + q + m2 * 0.37) % 1, p = a0.map((v, k) => v + (b0[k] - v) * q), tip = p[2] + h + (1 - ph) * 0.12 * L;
        rain.arrow([p[0], p[1], tip + 0.05 * L], [p[0], p[1], tip], rr * 1.2, col);
      });
    } else if (!side) {
      const Lr = 0.1 * L * (0.4 + 0.6 * f), drop = (1 - g) * 0.25 * L, e = o.d;
      const tip = [o.p[0] - e[0] * 0.05 - e[0] * drop, o.p[1] - e[1] * 0.05 - e[1] * drop, o.p[2] - e[2] * 0.05 - e[2] * drop];
      heavy.arrow([tip[0] - e[0] * Lr, tip[1] - e[1] * Lr, tip[2] - e[2] * Lr], tip, L / 200 * (0.6 + 0.6 * f), col);
      o.tag = [tip[0] - e[0] * Lr, tip[1] - e[1] * Lr, tip[2] - e[2] * Lr];
    } else {
      // sideways: outward from the structure means a pull (mooring line), inward a push (ship on a fender)
      const c = M.centre, e = o.d, len0 = 0.16 * L * (0.45 + 0.55 * f) * (1 + 0.05 * Math.sin(t * 3.5)) * g;
      const outward = (o.p[0] - c[0]) * e[0] + (o.p[1] - c[1]) * e[1] >= 0;
      const r = L / 170 * (0.7 + 0.5 * f);
      if (outward || s.lk === "mooring") {
        const tip = o.p.map((v, k) => v + e[k] * len0);
        heavy.arrow(o.p, tip, r, col);
        if (s.lk === "mooring") {
          const ship = o.p.map((v, k) => v + e[k] * 0.42 * L * g + (k === 2 ? 4 * g : 0));
          heavy.member(o.p, ship, L / 900, L / 900, hex("#2B2B2B"));
          heavy.box([ship[0], ship[1], 0], [1, 0, 0], [0, 1, 0], L / 120, L / 120, ship[2] - 0.6, ship[2] + 0.6, hex("#E3B23C"));
        }
        o.tag = tip;
      } else {
        const tail = o.p.map((v, k) => v - e[k] * len0);
        heavy.arrow(tail, o.p, r, col);
        if (s.lk === "berthing") {
          const hull = o.p.map((v, k) => v - e[k] * (len0 + 0.03 * L));
          const ex = [e[0], e[1], 0], el = Math.hypot(ex[0], ex[1]) || 1, ey = [-ex[1] / el, ex[0] / el, 0];
          heavy.box([hull[0], hull[1], 0], [ex[0] / el, ex[1] / el, 0], ey, 0.012 * L, 0.35 * L, M.wl - 4, Math.max(M.top + 3, M.wl + 6), hex("#3C4A5C"), hex("#8C3A2E"));
        }
        o.tag = tail;
      }
    }
  });
  out.push({ mesh: block, opacity: 0.72 });
  out.push({ mesh: rain, opacity: 1, glow: 0.2 });
  out.push({ mesh: heavy, opacity: 1, glow: 0.25 });
  return out;
}
// --- the load cases as what they are -----------------------------------------------------------------
// the load patterns of the file in families; each family is one shot that steps through its cases,
// each case drawn as the thing that makes it (paving, cargo, trucks, a hopper, a crane, a ship on the
// fender, a mooring line, wind, waves) with its values written on the picture
const LOOKS = [
  ["self", "Own weight", /^(DEAD|SELF|SW|OWN)/],
  ["seg", "The next segment resting on it", /SEG\d|ADJAC|NEIGHB/],
  ["truck", "Trucks", /TRUCK|LORRY|VEHIC|AXLE/],
  ["hopper", "The hopper", /HOPPER/],
  ["crane", "Construction loads", /CONSTR|CRANE/],
  ["finish", "Finishes and services", /SDL|SIDL|FINISH|SUPER|SERVIC/],
  ["live", "Live load: cargo on the deck", /LIVE|UDL|SURCH|STORE|CARGO/],
  ["berthing", "Ships berthing", /BERTH|FEND|IMPACT/],
  ["mooring", "Mooring line pulls", /BOLL|MOOR/],
  ["wind", "Wind", /WIND/],
  ["wave", "Waves", /WAVE|CURR/],
  ["buoy", "Buoyancy", /BUOY/],
  ["soil", "Soil and fill", /SOIL|FILL|EARTH/],
  ["temp", "Temperature", /TEMP|SHRINK/],
];
const STEP = 3.6;
export function loadFamilies(S) {
  const names = Object.keys(S.loads || {}).filter((p) => loadItems(S, p).length);
  const self = (S.patterns || []).filter((p) => p.self > 0 && !names.includes(p.name) && S.base?.[p.name]).map((p) => p.name);
  const fams = new Map();
  [...self, ...names].forEach((p) => {
    const f = LOOKS.find(([, , re]) => re.test(p.toUpperCase())) || ["other", "Other loads"];
    if (!fams.has(f[0])) fams.set(f[0], { key: f[0], label: f[1], names: [] });
    fams.get(f[0]).names.push(p);
  });
  const order = [...LOOKS.map(([k]) => k), "other"];
  return [...fams.values()].sort((a, b) => order.indexOf(a.key) - order.indexOf(b.key)).map((f) => {
    const list = f.names, has = new Set(list), steps = [], at = new Map();
    // friction joins the berthing case it goes with; an operating case joins its extreme twin
    const main = (n) => { const b = n.replace(/-FRICT?(ION)?(-[A-Z0-9]+)?$/i, ""); return b !== n && has.has(b) ? b : null; };
    const twin = (n) => ["-EXT-", "-STORM-", "-EXTREME-"].map((t) => n.replace(/-(OP|OPER|OPERATING)-/i, t)).find((x) => x !== n && has.has(x)) || null;
    list.forEach((n) => { if (!main(n) && !twin(n)) { const st = { name: n, parts: [n], also: [] }; at.set(n, st); steps.push(st); } });
    list.forEach((n) => { const m = main(n); if (m) at.get(m)?.parts.push(n); });
    list.forEach((n) => { const t = twin(n); if (t && !main(n)) at.get(t)?.also.push(n); });
    steps.forEach((st) => (st.items = st.parts.flatMap((p) => loadItems(S, p).map((o) => ({ ...o, pat: p })))));
    return { ...f, steps };
  });
}
const sum3 = (items, S) => {
  const F = [0, 0, 0];
  items.forEach((o) => { const k = o.kind === "area" ? areaOf(S, S.areas.ids[o.area]) : o.kind === "line" ? Math.hypot(...o.a.map((v, n) => v - o.b[n])) : 1; for (let n = 0; n < 3; n++) F[n] += o.d[n] * o.v * k; });
  return F;
};
const tonnes = (v) => `${n0(v / 9.81)} t`;
const val = (v) => n0(v, v < 10 ? 1 : 0);
// what one case puts on the structure, in a line or two
function describe(S, M, look, st) {
  const it = st.items, F = sum3(it, S), down = -F[2], side = Math.hypot(F[0], F[1]);
  const by = (kind) => it.filter((o) => o.kind === kind);
  const area = by("area"), line = by("line"), pt = by("point");
  const peak = (list) => list.reduce((m, o) => Math.max(m, o.v), 0);
  const m2 = area.reduce((a, o) => a + areaOf(S, S.areas.ids[o.area]), 0);
  const out = [];
  const total = [down > 1 ? `${kN(down)} down` : down < -1 ? `${kN(-down)} up` : "", side > 1 ? `${kN(side)} sideways` : ""].filter(Boolean).join(", ");
  if (look === "self") { const b = S.base?.[st.name] || []; const w = Math.abs(b[2] || 0), a2 = S.areas.ids.reduce((x, id) => x + areaOf(S, id), 0); out.push(`The concrete's own weight, spread over the whole structure: ${kN(w)} in all (${tonnes(w)})${a2 ? `, about ${n0(w / a2, 1)} kN/m² of deck (slab, beams and piles)` : ""}`); out.push("Worked out by SAP2000 from the sections and the concrete's density"); }
  else if (look === "finish") {
    if (area.length) out.push(`Surfacing and services: ${val(peak(area))} kN/m² over ${n0(m2)} m² of deck (about ${n0(Math.round(peak(area) / 24 * 100) * 10)} mm of paving)`);
    if (line.length) out.push(`Kerbs and services: ${val(peak(line))} kN/m along ${n0(line.length)} beams`);
    if (pt.length) out.push(`Fittings: ${pt.length} × ${val(peak(pt))} kN (bollards, ladders)`);
  } else if (look === "live" || (look === "crane" && area.length)) {
    const p = peak(area.length ? area : line);
    out.push(`${val(p)} kN/m² = ${n0(p / 9.81, 1)} t/m² ${look === "crane" ? "of materials and plant while it is built" : "of cargo"}${area.length ? ` over ${n0(m2)} m² of deck` : ""}${line.length ? `, ${val(peak(line))} kN/m along ${line.length} beams` : ""}`);
  } else if (look === "truck") out.push(`${pt.length} wheels of ${val(peak(pt))} kN: a truck of about ${tonnes(down)} on the deck`);
  else if (look === "hopper") {
    const dn = pt.filter((o) => o.d[2] < 0), up = pt.filter((o) => o.d[2] > 0);
    out.push(up.length ? `Off-centre load: ${dn.length} legs pressed by ${val(peak(dn))} kN, ${up.length} lifted by ${val(peak(up))} kN` : `${pt.length} legs × ${val(peak(pt))} kN = ${kN(down)} (${tonnes(down)})${line.length ? `, conveyor ${val(peak(line))} kN/m` : ""}`);
  } else if (look === "crane") out.push(`Mobile crane: ${kN(peak(pt))} (${tonnes(peak(pt))}) on its outrigger`);
  else if (look === "seg") out.push(`${pt.length} reactions where segment 1 rests on this one, up to ${kN(peak(pt))}`);
  else if (look === "berthing") {
    const h = pt.filter((o) => Math.abs(o.d[2]) < 0.5 && !/FRICT/i.test(o.pat)), fr = pt.filter((o) => /FRICT/i.test(o.pat));
    out.push(`The ship presses ${kN(peak(h.length ? h : pt))} on the fender${fr.length ? `; friction ${fr.map((o) => `${kN(o.v)} ${Math.abs(o.d[2]) > 0.5 ? "up" : "along"}`).join(" and ")}` : ""}`);
  } else if (look === "mooring") {
    const sea = M.seaDir;
    out.push(pt.map((o) => { const hz = Math.hypot(o.d[0], o.d[1]), c = hz > 1e-6 ? Math.abs(o.d[0] * sea[0] + o.d[1] * sea[1]) / hz : 0;
      const how = o.d[2] > 0.3 ? "rising at 45°" : c > 0.9 ? "straight out to sea" : c > 0.4 ? "at 45° to the quay" : "along the quay";
      return `${kN(o.v)} ${how}`; }).slice(0, 3).join("; ") + ` (${pt.length} bollard${pt.length === 1 ? "" : "s"})`);
  } else if (look === "wind") out.push(`Wind on the deck and fittings: ${kN(side)} in all${line.length ? `, ${val(peak(line))} kN/m on the front beam` : ""}`);
  else if (look === "wave") {
    const up = area.filter((o) => o.d[2] > 0.5), dn = area.filter((o) => o.d[2] < -0.5), hl = line.filter((o) => Math.abs(o.d[2]) < 0.5), pp = pt;
    if (up.length) out.push(`Wave slamming up under the deck: ${val(peak(up))} kN/m²${line.length ? `, ${val(peak(line))} kN/m under the beams` : ""}`);
    if (dn.length) out.push(`Suction as the wave drops away: ${val(peak(dn))} kN/m² pulling the deck down`);
    if (hl.length && !area.length) out.push(`Wave pushing on the beams: ${val(peak(hl))} kN/m${hl.length > 1 ? ` over ${hl.length} members` : ""}`);
    if (pp.length) out.push(`Wave drag on the piles: up to ${val(peak(pp))} kN on each piece of pile`);
  } else if (look === "buoy") out.push(`Buoyancy: ${val(peak(line))} kN/m lifting ${line.length} piles where they are under water`);
  if (!out.length) out.push(`${n0(it.length)} loads, up to ${val(peak(it))} ${it[0]?.kind === "area" ? "kN/m²" : it[0]?.kind === "line" ? "kN/m" : "kN"}`);
  if (total && look !== "self") out.push(`In all: ${total}`);
  if (st.also.length) {
    const a = st.also.map((n) => { const L2 = loadItems(S, n), p = L2.reduce((m, o) => Math.max(m, o.v), 0); return `${n} ${val(p)} ${L2[0]?.kind === "area" ? "kN/m²" : L2[0]?.kind === "line" ? "kN/m" : "kN"}`; });
    out.push(`Shown at its extreme; in normal operation: ${a.join(", ")}`);
  }
  return out;
}
// the tags of a case: its largest value of each kind (where the thing is)
function stepTags(look, st) {
  const unit = { area: "kN/m²", line: "kN/m", point: "kN" }, tags = [];
  ["area", "line", "point"].forEach((kind) => {
    const list = st.items.filter((o) => o.kind === kind);
    if (!list.length) return;
    const big = list.reduce((b, o) => (o.v > b.v ? o : b));
    const what = look === "truck" ? "per wheel" : look === "hopper" ? "per leg" : look === "berthing" ? (/FRICT/i.test(big.pat) ? "friction" : "berthing") : look === "mooring" ? "line pull" : "";
    tags.push({ at: big.p, text: `${val(big.v)} ${unit[kind]}${what ? ` ${what}` : ""}` });
  });
  if (look === "berthing") st.items.filter((o) => /FRICT/i.test(o.pat)).slice(0, 1).forEach((o) => tags.push({ at: o.p, text: `${val(o.v)} kN friction` }));
  return tags.slice(0, 3);
}
function loadFamilyShots(S, M, C, add, brief, pats) {
  if (!pats.length) return;
  const fams = loadFamilies(S), max = brief ? 2 : 8;
  fams.forEach((f, i) => {
    const steps = f.steps.slice(0, max), more = f.steps.length - steps.length, cases = f.steps.reduce((a, st) => a + st.parts.length + st.also.length, 0);
    const look = f.key;
    steps.forEach((st, j) => {
      st.look = look === "crane" && st.items.every((o) => o.kind === "point") ? "crane" : look === "crane" ? "construction" : look;
      st.head = `${f.label}: ${st.name}${st.parts.length > 1 ? ` with ${st.parts.slice(1).map((p) => p.replace(st.name, "").replace(/^-/, "")).join(", ")}` : ""}`;
      st.lines = [...describe(S, M, look === "crane" && st.look === "construction" ? "crane" : look, st),
        `Case ${j + 1} of ${steps.length}${more > 0 ? ` shown, ${more} more like these (${f.steps.slice(max, max + 3).map((x) => x.name).join(", ")}${more > 3 ? "…" : ""})` : ""}${cases > steps.length ? `; ${cases} load cases in this family` : ""}`];
      st.tags = stepTags(st.look, st);
      const pts = st.items.map((o) => o.p);
      st.focus = pts.length ? [0, 1, 2].map((n) => pts.reduce((a, q) => a + q[n], 0) / pts.length) : M.centre;
    });
    const seaward = (M.seaYaw * Math.PI) / 180;
    const cam = (() => {
      const fc = steps[0].focus, c = { ...C.quarter(), tx: lerp(M.centre[0], fc[0], 0.6), ty: lerp(M.centre[1], fc[1], 0.6) };
      if (look === "berthing" || look === "mooring") {
        // from the land side, looking out over the quay to the ship
        const o = 0.25 * M.L;
        return [{ ...C.high(), yaw: M.seaYaw + 180 - 40, pitch: 42, dist: 2.3 * M.L, tx: fc[0] + Math.cos(seaward) * o, ty: fc[1] + Math.sin(seaward) * o },
          { ...C.high(), yaw: M.seaYaw + 180 + 30, pitch: 36, dist: 2.0 * M.L, tx: fc[0] + Math.cos(seaward) * o, ty: fc[1] + Math.sin(seaward) * o }];
      }
      if (look === "wave") return [{ ...C.low(), yaw: M.seaYaw - 55, pitch: 7 }, { ...C.low(), yaw: M.seaYaw - 20, pitch: 5, dist: 0.95 * M.L }];
      if (look === "wind") return [C.high(), C.wide()];
      if (look === "truck" || look === "hopper" || (look === "crane" && steps.some((s2) => s2.look === "crane"))) return [{ ...c, dist: 1.15 * M.L }, { ...C.quarter2(), tx: c.tx, ty: c.ty, dist: 0.85 * M.L, pitch: 26 }];
      if (look === "buoy" || look === "seg") return [C.quarter(), C.low()];
      return [i % 2 ? C.quarter2() : C.quarter(), C.high()];
    })();
    add({ kind: "loads", real: true, dur: steps.length * STEP + 0.8, cut: i === 0, steps, look, cam0: cam[0], cam1: cam[1], head: steps[0].head, lines: steps[0].lines, legend: null, tags: steps[0].tags });
  });
}
// the case on the picture now, and how far into it
function stepOn(F, s, u) {
  const n = s.steps.length, su = clamp((u * s.dur - 0.4) / (s.dur - 0.8), 0, 0.99999) * n, j = Math.min(n - 1, Math.floor(su));
  const st = s.steps[j];
  s.head = st.head; s.lines = st.lines; s.tags = st.tags; s.step = st;
  s.lu = su - j; F.uTag = s.lu;
}
const PAL = ["#B03A2E", "#2E86C1", "#D68910", "#1E8449", "#7D3C98", "#A04000", "#117A65", "#5D6D7E"];
const hash = (a, b) => { const h = Math.sin(a * 127.1 + b * 311.7) * 43758.5453; return h - Math.floor(h); };
// plan helpers: the main direction of a set of points, their clusters
function axisOf(pts) {
  const c = [pts.reduce((a, p) => a + p[0], 0) / pts.length, pts.reduce((a, p) => a + p[1], 0) / pts.length];
  let xx = 0, yy = 0, xy = 0;
  pts.forEach((p) => { const x = p[0] - c[0], y = p[1] - c[1]; xx += x * x; yy += y * y; xy += x * y; });
  const an = 0.5 * Math.atan2(2 * xy, xx - yy), ex = [Math.cos(an), Math.sin(an), 0], ey = [-ex[1], ex[0], 0];
  const s = pts.map((p) => (p[0] - c[0]) * ex[0] + (p[1] - c[1]) * ex[1]), t = pts.map((p) => (p[0] - c[0]) * ey[0] + (p[1] - c[1]) * ey[1]);
  const z = Math.max(...pts.map((p) => p[2]));
  return { c, ex, ey, lx: Math.max(...s) - Math.min(...s), ly: Math.max(...t) - Math.min(...t), mid: [(Math.max(...s) + Math.min(...s)) / 2, (Math.max(...t) + Math.min(...t)) / 2], z };
}
function clusters(list, gap) {
  const out = [];
  list.forEach((o) => {
    const near = out.filter((cl) => cl.some((q) => Math.hypot(q.p[0] - o.p[0], q.p[1] - o.p[1]) <= gap));
    if (!near.length) out.push([o]);
    else { const m = near.flat(); m.push(o); near.forEach((cl) => out.splice(out.indexOf(cl), 1)); out.push(m); }
  });
  return out;
}
const onAxis = (A, s, t) => [A.c[0] + A.ex[0] * s + A.ey[0] * t, A.c[1] + A.ex[1] * s + A.ey[1] * t];
function realScene(M, s, u, time) {
  const S = M.S, st = s.step || s.steps[0], lu = s.lu ?? 0, g = ease(lu / 0.3), L = M.L, items = st.items, look = st.look;
  const out = [], body = new Mesh(), glass = new Mesh(), arr = new Mesh();
  const rr = L / 520, t = time;
  const deckZ = (o) => {
    if (o.kind === "area") { const k = S.areas.section[o.area], th = (M.sect[k]?.t || 0.3) / 2; return Math.max(...o.pts.map((q) => q[2])) + th + 0.02; }
    return o.p[2] + 0.2;
  };
  const rain = (p, z, col, k = 1) => { const ph = (t * 0.7 + hash(p[0], p[1])) % 1, tip = z + (1 - ph) * 0.1 * L; arr.arrow([p[0], p[1], tip + 0.05 * L * k], [p[0], p[1], tip], rr * 1.3, col); };
  const push = (o, col, len = 0.08 * L, r = L / 260) => {
    // an arrow onto the point along the load's direction, the point at its tip
    const e = o.d, k = Math.max(0.02, g);
    arr.arrow([o.p[0] - e[0] * len * k, o.p[1] - e[1] * len * k, o.p[2] - e[2] * len * k], o.p, r, col);
  };
  const thin = Math.max(1, Math.round(items.filter((o) => o.kind === "area").length / 70));
  const hull = (along, out0, wide, from) => {
    // a ship lying along the quay, its side `out0` m out to sea from the quay face
    // a hull with a pointed bow, red below the waterline, a bridge at the stern, hatch covers on deck
    const sea = M.seaDir, ex = [-sea[1], sea[0], 0], ey = [sea[0], sea[1], 0];
    const o = [from[0] + sea[0] * (out0 + wide / 2), from[1] + sea[1] * (out0 + wide / 2)];
    const P = (a, b, z) => [o[0] + ex[0] * a + ey[0] * b, o[1] + ex[1] * a + ey[1] * b, z];
    const h = along / 2, w = wide / 2, bow = 0.16 * along;
    const plan = [[-h, -w], [h - bow, -w], [h - bow * 0.35, -w * 0.62], [h, 0], [h - bow * 0.35, w * 0.62], [h - bow, w], [-h, w]];
    const band = (z0, z1, col) => plan.forEach((q, i) => { const r = plan[(i + 1) % plan.length]; body.quad(P(q[0], q[1], z0), P(r[0], r[1], z0), P(r[0], r[1], z1), P(q[0], q[1], z1), col); });
    band(M.wl - 7, M.wl - 0.2, hex("#7A2E24"));
    band(M.wl - 0.2, M.wl + 6, hex("#26313F"));
    body.panel(plan.map((q) => P(q[0], q[1], M.wl + 6)), hex("#7E868F"));
    for (let k = 0; k < 6; k++) { const a = -h + 0.22 * along + k * 0.11 * along; body.box([P(a, 0, 0)[0], P(a, 0, 0)[1]], ex, ey, 0.09 * along, wide * 0.7, M.wl + 6, M.wl + 7.2, hex(PAL[k % PAL.length]), hex("#9AA3AD")); }
    const bo = P(-h + 0.07 * along, 0, 0);
    body.box([bo[0], bo[1]], ex, ey, 0.08 * along, wide * 0.85, M.wl + 6, M.wl + 18, hex("#ECEAE4"), hex("#C9C6BF"));
    glass.box([bo[0] + ex[0] * 0.04 * along, bo[1] + ex[1] * 0.04 * along], ex, ey, 0.004 * along, wide * 0.8, M.wl + 15.5, M.wl + 17, hex("#4A6F8A"));
    return M.wl + 6;
  };
  if (look === "self") {
    // spread over the whole deck: a little arrow on every few panels, and the weight per square metre
    const W = Math.abs(S.base?.[st.name]?.[2] || 0), A2 = S.areas.ids.reduce((a, id) => a + areaOf(S, id), 0) || 1, every = Math.max(1, Math.round(S.areas.joints.length / 160));
    let at = null;
    S.areas.joints.forEach((js, k) => { const pts = js.map((j) => S.joints[j]); const c = [0, 1, 2].map((n) => pts.reduce((a, q) => a + q[n], 0) / pts.length); if (!at && k > S.areas.joints.length * 0.3) at = [c[0], c[1], M.deckTop + 0.3]; if (k % every === 0 && g > 0.2) { const ph = (t * 0.5 + hash(c[0], c[1])) % 1, z0 = M.deckTop + 0.15; arr.arrow([c[0], c[1], z0 + 0.035 * L + ph * 0.6], [c[0], c[1], z0 + ph * 0.6], rr * 1.1, hex("#8E99A4")); } });
    st.tags = W ? [{ at: at || [M.centre[0], M.centre[1], M.deckTop], text: `${n0(W / A2, 1)} kN/m² on average` }] : [];
  }
  items.forEach((o, n) => {
    const side = Math.abs(o.d[2]) < 0.5;
    if (look === "finish") {
      if (o.kind === "area") {
        const z0 = deckZ(o), h = 0.35 * g, sh = (k) => o.pts.map((q) => [q[0], q[1], z0 + k]);
        const col = hex(hash(o.p[0], o.p[1]) > 0.5 ? "#5A5E64" : "#52565C");
        body.panel(sh(h), col);
        if (n % thin === 0 && g > 0.6) rain(o.p, z0 + h, hex("#A9B1B9"));
      } else if (o.kind === "line") body.member([o.a[0], o.a[1], o.a[2] + 0.5], [o.b[0], o.b[1], o.b[2] + 0.5], 0.45, 0.6 * g + 0.01, hex("#CFC9BC"));
      else { body.frustum([o.p[0], o.p[1], o.p[2] + 0.3], [o.p[0], o.p[1], o.p[2] + 1.2], 0.45, 0.35, hex("#2B2F33")); push(o, hex("#E3543C"), 0.05 * L); }
    } else if (look === "live" || look === "construction") {
      if (o.kind === "area") {
        const z0 = deckZ(o), h = Math.max(0.05, Math.min(4, Math.max(0.3, o.v / (look === "live" ? 10 : 6))) * g);
        const c = o.p, sh = o.pts.map((q) => [c[0] + (q[0] - c[0]) * 0.9, c[1] + (q[1] - c[1]) * 0.9]);
        const k = Math.floor(hash(Math.floor(c[0] / 6), Math.floor(c[1] / 3)) * PAL.length);
        const col = look === "live" ? hex(PAL[k]) : hex(k % 2 ? "#B98A57" : "#A67C4E");
        const bot = sh.map((q) => [q[0], q[1], z0]), top = sh.map((q) => [q[0], q[1], z0 + h]);
        body.panel(top, col.map((v) => Math.min(1, v * 1.15)));
        for (let i = 0; i < bot.length; i++) { const j = (i + 1) % bot.length; body.quad(bot[i], bot[j], top[j], top[i], col); }
        if (n % thin === 0 && g > 0.9) rain(c, z0 + h, hex("#F2D388"), 0.7);
        o.tag = [c[0], c[1], z0 + h];
      } else if (o.kind === "line") push({ ...o, p: [o.p[0], o.p[1], o.p[2] + 0.3] }, hex("#E07B39"), 0.04 * L, L / 400);
    } else if (look === "seg") push({ ...o, p: [o.p[0], o.p[1], o.p[2] + 0.2] }, hex("#8E6FBF"), 0.05 * L + 0.07 * L * Math.min(1, o.v / 500), L / 220);
    else if (look === "buoy" && o.kind === "line") {
      [0.2, 0.5, 0.8].forEach((q) => { const p = o.a.map((v, k) => v + (o.b[k] - v) * q); if (p[2] > M.wl) return; const ph = (t * 0.6 + q) % 1; arr.arrow([p[0] + 1, p[1], p[2] - 1.5 + ph], [p[0] + 1, p[1], p[2] + 1.5 + ph], rr * 1.6, hex("#3FC1C9")); });
    } else if (look === "wind") {
      push(o.kind === "line" ? { ...o, p: o.p } : o, hex("#6BB7C9"), 0.07 * L, L / 300);
    } else if (look === "wave") {
      if (o.kind === "area") {
        if (n % thin) return;
        const up = o.d[2] > 0, z = Math.min(...o.pts.map((q) => q[2])) - 0.3, len = 0.05 * L * g, ph = (t * 1.2 + hash(o.p[0], o.p[1])) % 1;
        if (up) arr.arrow([o.p[0], o.p[1], z - len - ph * 2], [o.p[0], o.p[1], z - ph * 2 + 0.01], rr * 1.8, hex("#2F7FD0"));
        else arr.arrow([o.p[0], o.p[1], z], [o.p[0], o.p[1], z - len], rr * 1.8, hex("#1B4F8C"));
      } else if (o.kind === "line") { if (Math.abs(o.d[2]) < 0.5 || n % 4 === 0) push(o, hex("#2F7FD0"), 0.035 * L, L / 600); }
      else push(o, hex("#5FA8E8"), 0.03 * L, L / 500);
    }
  });
  if (look === "truck" || look === "hopper") {
    const pts = items.filter((o) => o.kind === "point");
    clusters(pts, look === "truck" ? 7 : 14).forEach((cl, ci) => {
      if (cl.length < 2) { cl.forEach((o) => push(o, hex("#E3543C"))); return; }
      const A = axisOf(cl.map((o) => o.p)), z = M.deckTop + 0.02;
      if (look === "truck") {
        // a tractor and its trailer standing on the deck, its wheels on the load points
        const span = A.lx, wid = Math.max(2.5, A.ly + 0.5), at = (s0, t0 = 0) => onAxis(A, A.mid[0] + s0, A.mid[1] + t0), R0 = 0.52;
        cl.forEach((o) => {
          const s0 = (o.p[0] - A.c[0]) * A.ex[0] + (o.p[1] - A.c[1]) * A.ex[1], t0 = (o.p[0] - A.c[0]) * A.ey[0] + (o.p[1] - A.c[1]) * A.ey[1];
          [-0.18, 0.18].forEach((dt) => { const w = onAxis(A, s0, t0 + dt * Math.sign(t0 - A.mid[1] || 1)); body.frustum([w[0] - A.ey[0] * 0.15, w[1] - A.ey[1] * 0.15, z + R0], [w[0] + A.ey[0] * 0.15, w[1] + A.ey[1] * 0.15, z + R0], R0, R0, hex("#1C1C1C"), 14); });
          const w = onAxis(A, s0, t0 + 0.38 * Math.sign(t0 - A.mid[1] || 1)); body.frustum([w[0], w[1], z + R0], [w[0] + A.ey[0] * 0.02 * Math.sign(t0 - A.mid[1] || 1), w[1] + A.ey[1] * 0.02 * Math.sign(t0 - A.mid[1] || 1), z + R0], 0.25, 0.25, hex("#B8BEC4"), 10);
        });
        body.box(at(0), A.ex, A.ey, span + 3.6, 1.0, z + 0.75, z + 1.15, hex("#2B2F33"));
        body.box(at(-0.6), A.ex, A.ey, span + 1.6, wid, z + 1.25, z + 1.45, hex("#4A4F55"));
        body.box(at(-0.6), A.ex, A.ey, span + 1.4, wid - 0.1, z + 1.45, z + 3.9, hex(PAL[(ci * 3 + 1) % PAL.length]), hex("#D9DDE1"));
        for (let q = 1; q < 6; q++) body.box(at(-0.6 - (span + 1.4) / 2 + (q * (span + 1.4)) / 6), A.ex, A.ey, 0.08, wid + 0.04, z + 1.5, z + 3.85, hex("#3B3F44"));
        const cab = span / 2 + 1.55;
        body.box(at(cab), A.ex, A.ey, 2.3, wid, z + 1.0, z + 3.3, hex("#C0392B"), hex("#A93226"));
        body.box(at(cab + 1.2), A.ex, A.ey, 0.12, wid * 0.95, z + 0.7, z + 1.2, hex("#B8BEC4"));
        glass.box(at(cab + 1.16), A.ex, A.ey, 0.04, wid * 0.82, z + 2.2, z + 3.1, hex("#6FA7C9"));
        if (g > 0.5) cl.forEach((o) => push({ ...o, p: [o.p[0], o.p[1], z] }, hex("#E3543C"), 0.05 * L, L / 300));
      } else {
        const dn = cl.filter((o) => o.d[2] <= 0), legs = cl, H = 7, at = (s0, t0) => onAxis(A, A.mid[0] + s0, A.mid[1] + t0);
        legs.forEach((o) => body.member([o.p[0], o.p[1], z], [o.p[0], o.p[1], z + H], 0.5, 0.5, hex("#7F8C8D")));
        const w = A.lx + 1.5, d = Math.max(A.ly + 1.5, 4), c = at(0, 0), fill = /LIVE|POS|FULL/i.test(st.name) ? 1 : /ECC/i.test(st.name) ? 0.6 : 0.15;
        body.box(c, A.ex, A.ey, w, d, z + H, z + H + 4.5, hex("#E3B23C"), hex("#C99A2E"));
        body.frustum([c[0], c[1], z + H], [c[0], c[1], z + H - 3.2], Math.min(w, d) * 0.48, 0.5, hex("#C99A2E"), 4);
        if (fill > 0.2) body.frustum([c[0], c[1], z + H + 4.5], [c[0], c[1], z + H + 4.5 + 2.2 * fill], Math.min(w, d) * 0.45, 0.4, hex("#8D8172"), 10);
        legs.forEach((o) => push({ ...o, p: [o.p[0], o.p[1], z] }, o.d[2] > 0 ? hex("#2F6FD0") : hex("#E3543C"), 0.04 * L * (0.5 + 0.5 * o.v / Math.max(...legs.map((q) => q.v))), L / 330));
        void dn;
      }
    });
    items.filter((o) => o.kind === "line").forEach((o) => { body.member([o.a[0], o.a[1], o.a[2] + 5.5], [o.b[0], o.b[1], o.b[2] + 5.5], 1.2, 0.9, hex("#95A5A6")); body.member([o.a[0], o.a[1], o.a[2]], [o.a[0], o.a[1], o.a[2] + 5.5], 0.3, 0.3, hex("#7F8C8D")); });
  } else if (look === "crane") {
    items.filter((o) => o.kind === "point").forEach((o) => {
      const sea = M.seaDir, ex = [sea[0], sea[1], 0], ey = [-sea[1], sea[0], 0], z = M.deckTop;
      body.box([o.p[0], o.p[1]], ex, ey, 4, 11, z + 0.8, z + 2.4, hex("#F2B705"), hex("#D9A404"));
      [[-1.6, -4.5], [1.6, -4.5], [-1.6, 4.5], [1.6, 4.5]].forEach(([a, b]) => body.frustum([o.p[0] + ex[0] * a + ey[0] * b, o.p[1] + ex[1] * a + ey[1] * b, z + 0.5], [o.p[0] + ex[0] * a + ey[0] * b + 0.3 * ey[0], o.p[1] + ex[1] * a + ey[1] * b + 0.3 * ey[1], z + 0.5], 0.5, 0.5, hex("#1C1C1C"), 10));
      body.box([o.p[0] - ex[0] * 0.5, o.p[1] - ex[1] * 0.5], ex, ey, 3, 4, z + 2.4, z + 4.4, hex("#E8E6E1"));
      const lift = 0.2 + 0.8 * g, top = [o.p[0] + ex[0] * 18, o.p[1] + ex[1] * 18, z + 4 + 26 * lift];
      body.member([o.p[0], o.p[1], z + 3.5], top, 0.9, 0.9, hex("#F2B705"));
      const hook = [top[0], top[1], Math.max(M.wl + 2, top[2] - 14)];
      body.member(top, hook, 0.08, 0.08, hex("#222222"));
      body.box([hook[0], hook[1]], ex, ey, 2.4, 2.4, hook[2] - 2.4, hook[2], hex("#6E7B85"));
      push(o, hex("#E3543C"), 0.06 * L, L / 260);
    });
  } else if (look === "berthing") {
    const main = items.filter((o) => o.kind === "point" && !/FRICT/i.test(o.pat));
    const from = main[0]?.p || M.centre, sea = M.seaDir;
    const off = 2.2 + (1 - g) * 12;
    hull(Math.min(1.1 * L, 200), off, 24, from);
    main.forEach((o) => {
      body.frustum([o.p[0] + sea[0] * 0.1, o.p[1] + sea[1] * 0.1, o.p[2]], [o.p[0] + sea[0] * 2.0, o.p[1] + sea[1] * 2.0, o.p[2]], 1.0, 1.0, hex("#1C1C1C"), 14);
      push(o, hex("#E3543C"), 0.12 * L, L / 160);
    });
    items.filter((o) => /FRICT/i.test(o.pat)).forEach((o) => push({ ...o, p: [o.p[0] + sea[0] * 2, o.p[1] + sea[1] * 2, o.p[2]] }, hex("#E3B23C"), 0.07 * L, L / 260));
  } else if (look === "mooring") {
    const sea = M.seaDir, faceAt = items[0]?.p || M.centre;
    const deckTop = hull(Math.min(1.1 * L, 200), 3, 24, faceAt);
    items.filter((o) => o.kind === "point").forEach((o) => {
      const z = M.deckTop, bt = [o.p[0], o.p[1], z + 1.0];
      body.frustum([o.p[0], o.p[1], z], bt, 0.42, 0.32, hex("#2B2F33"), 12);
      body.frustum(bt, [bt[0], bt[1], bt[2] + 0.18], 0.55, 0.55, hex("#2B2F33"), 12);
      const e = o.d, s0 = e[0] * sea[0] + e[1] * sea[1];
      let len = s0 > 0.2 ? Math.min(70, (3 + 16) / s0) : 40;
      if (e[2] > 0.3) len = Math.min(len, (deckTop - bt[2]) / e[2]);
      const k = 0.15 + 0.85 * g, end = bt.map((v, n) => v + e[n] * len * k), sag = (1 - g) * 3;
      const mid = [(bt[0] + end[0]) / 2, (bt[1] + end[1]) / 2, (bt[2] + end[2]) / 2 - sag];
      body.member(bt, mid, 0.14, 0.14, hex("#E3B23C"), 6); body.member(mid, end, 0.14, 0.14, hex("#E3B23C"), 6);
      arr.arrow(bt, bt.map((v, n) => v + e[n] * 0.1 * L * Math.max(0.05, g)), L / 230, hex("#E3543C"));
      o.tag = bt;
    });
  } else if (look === "wind") {
    // streaks of air blowing through the scene
    const F = sum3(items, S), h = Math.hypot(F[0], F[1]) || 1, e = [F[0] / h, F[1] / h, 0];
    for (let q = 0; q < 26; q++) {
      const a = hash(q, 1) - 0.5, b = hash(q, 2), ph = (t * 0.45 + hash(q, 3)) % 1, ey = [-e[1], e[0]];
      const p0 = [M.centre[0] + ey[0] * a * 1.4 * L + e[0] * (ph - 0.6) * 1.6 * L, M.centre[1] + ey[1] * a * 1.4 * L + e[1] * (ph - 0.6) * 1.6 * L, M.top + 1 + b * 12];
      glass.member(p0, [p0[0] + e[0] * 0.18 * L, p0[1] + e[1] * 0.18 * L, p0[2]], 0.12, 0.12, hex("#E9F4F7"));
    }
  } else if (look === "wave") {
    // a swell rolling in from the sea under the deck
    const sea = M.seaDir, ex = [-sea[1], sea[0]], R = 0.85 * L, n = 26, A = 1.4, k = (2 * Math.PI) / (0.45 * L);
    const z = (x, y) => M.wl + A * Math.sin(k * -((x - M.centre[0]) * sea[0] + (y - M.centre[1]) * sea[1]) - 2.4 * t);
    const P = (i, j) => { const a = -R + (2 * R * i) / n, b = -R + (2 * R * j) / n, x = M.centre[0] + ex[0] * a + sea[0] * b, y = M.centre[1] + ex[1] * a + sea[1] * b; return [x, y, z(x, y)]; };
    for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) glass.quad(P(i, j), P(i + 1, j), P(i + 1, j + 1), P(i, j + 1), hex("#3A8FC9"));
  }
  if (look === "generic" || look === "other" || look === "soil" || look === "temp") return loadScene(M, { ...s, items, lk: loadKind(st.name, items) }, lu);
  out.push({ mesh: body, opacity: 1 });
  out.push({ mesh: arr, opacity: 1, glow: 0.25 });
  if (glass.p.length) out.push({ mesh: glass, opacity: look === "wave" ? 0.5 : look === "wind" ? 0.55 : 0.8 });
  // tags follow the things as drawn
  st.tags.forEach((tg) => { const o = items.find((q) => q.p === tg.at); if (o?.tag && o.tag !== o.p) tg.at2 = o.tag; });
  return out;
}
// a family's total load as one big arrow onto the structure, pulsing, its parts split by direction
function familyArrows(M, f, u) {
  const m = new Mesh(), g = ease(u / 0.3), L = f.rep.L, tip = famTip(M, f), t = u * 6;
  if (f.mag < 1) return m;
  const r = M.L / 110, len0 = 0.28 * M.L * g * (1 + 0.04 * Math.sin(t * 3)), col = hex({ dead: "#7A6F5A", live: "#E07B39", berthing: "#3F7CC4", mooring: "#E3B23C", wind: "#6BB7C9", wave: "#2F6FD0", temp: "#C94C4C" }[f.key] || "#E3543C");
  const h = Math.hypot(L[0], L[1]);
  if (h >= 1) { const e = [L[0] / h, L[1] / h, 0], k = Math.max(0.35, h / f.mag); m.arrow([tip[0] - e[0] * len0 * k, tip[1] - e[1] * len0 * k, tip[2] + 0.4], [tip[0], tip[1], tip[2] + 0.4], r, col); }
  if (Math.abs(L[2]) >= 1) {
    // weight pressing down onto the deck; uplift drawn rising off it
    const k = Math.max(0.35, Math.abs(L[2]) / f.mag) * 0.75, top = [M.centre[0], M.centre[1], M.top + 0.3];
    if (L[2] < 0) m.arrow([top[0], top[1], top[2] + len0 * k], top, r, col);
    else m.arrow(top, [top[0], top[1], top[2] + len0 * k], r, col);
  }
  return m;
}
const DIRS = { gravity: [0, 0, -1], x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1], "1": [1, 0, 0], "2": [0, 1, 0], "3": [0, 0, 1], "x projected": [1, 0, 0], "y projected": [0, 1, 0], "gravity projected": [0, 0, -1] };
const dirOf = (d) => DIRS[String(d || "gravity").toLowerCase()] || [0, 0, -1];

function reactionArrows(M, gov, g) {
  const S = M.S, m = new Mesh(), sup = S.reactions.supports, fz = gov.fz, max = Math.max(...fz.map((v) => Math.abs(v || 0)), 1e-9), r = M.L / 300;
  sup.forEach((j, k) => {
    const v = fz[k] || 0, p = S.joints[j];
    if (!p || !v) return;
    const L = M.L * 0.16 * (0.25 + 0.75 * Math.abs(v) / max) * Math.max(0.02, g);
    const col = rel(v, max);
    if (v > 0) m.arrow([p[0], p[1], p[2] - L], p, r, col); // pushes up: the support in compression
    else m.arrow(p, [p[0], p[1], p[2] - L], r, hex("#2F6FD0")); // pulls down: uplift held
    m.box([p[0], p[1], 0], [1, 0, 0], [0, 1, 0], r * 5, r * 5, p[2] - r * 1.5, p[2] + r * 1.5, col);
  });
  return m;
}

// --- the camera along a shot ----------------------------------------------------------------------
function camAt(s, u) {
  const e = ease(u), c0 = s.cam0, c1 = s.cam1;
  let dy = c1.yaw - c0.yaw;
  while (dy > 180) dy -= 360;
  while (dy < -180) dy += 360;
  return { yaw: c0.yaw + dy * e, pitch: lerp(c0.pitch, c1.pitch, e), dist: Math.exp(lerp(Math.log(c0.dist), Math.log(c1.dist), e)), tx: lerp(c0.tx, c1.tx, e), ty: lerp(c0.ty, c1.ty, e), tz: lerp(c0.tz, c1.tz, e) };
}

// --- the writing over the picture ---------------------------------------------------------------
const FONT = () => getComputedStyle(document.body).fontFamily;
function panel(x, k, X, Y, W, H, a = 0.82) { x.fillStyle = `rgba(16,28,44,${a})`; x.beginPath(); if (x.roundRect) x.roundRect(X, Y, W, H, 8 * k); else x.rect(X, Y, W, H); x.fill(); }
function text(x, k, str, X, Y, px, col = "#fff", weight = 500, align = "left") { x.font = `${weight} ${Math.round(px * k)}px ${FONT()}`; x.fillStyle = col; x.textAlign = align; x.fillText(str, X, Y); }
function wrap(x, str, maxW) { const out = []; let line = ""; String(str).split(" ").forEach((w) => { const t = line ? line + " " + w : w; if (x.measureText(t).width > maxW && line) { out.push(line); line = w; } else line = t; }); if (line) out.push(line); return out; }

function legend(x, F, kind, k) {
  const W = F.w, X = W - 22 * k - 250 * k, Y = 96 * k;
  panel(x, k, X, Y, 250 * k, 62 * k, 0.78);
  if (kind === "axial") {
    const g2 = x.createLinearGradient(X + 14 * k, 0, X + 236 * k, 0);
    g2.addColorStop(0, "#1A9850"); g2.addColorStop(0.5, "#D5D9DC"); g2.addColorStop(1, "#D7301F");
    x.fillStyle = g2; x.fillRect(X + 14 * k, Y + 30 * k, 222 * k, 10 * k);
    text(x, k, "Axial force", X + 14 * k, Y + 22 * k, 12.5, "#DCE7EC", 600);
    text(x, k, "compression", X + 14 * k, Y + 54 * k, 11, "#9FE0B4", 600);
    text(x, k, "tension", X + 236 * k, Y + 54 * k, 11, "#F2A093", 600, "right");
    return;
  }
  const g = x.createLinearGradient(X + 14 * k, 0, X + 236 * k, 0);
  const stops = kind === "util" ? [[0, 0], [0.45, 0.5], [0.675, 0.75], [0.9, 0.999], [0.93, 1.01], [1, 1.01]] : [[0, 0], [0.5, 0.5], [0.75, 0.75], [1, 0.999]];
  stops.forEach(([p, u]) => g.addColorStop(p, heat(u)));
  x.fillStyle = g; x.fillRect(X + 14 * k, Y + 30 * k, 222 * k, 10 * k);
  text(x, k, kind === "util" ? "Utilisation ratio" : "Against the largest", X + 14 * k, Y + 22 * k, 12.5, "#DCE7EC", 600);
  const ticks = kind === "util" ? [["0", 0], ["0.5", 0.45], ["1.0", 0.9]] : [["low", 0], ["largest", 1]];
  ticks.forEach(([t, p]) => text(x, k, t, X + 14 * k + 222 * k * p, Y + 54 * k, 11, "#9FB4C2", 500, p === 1 ? "right" : p ? "center" : "left"));
}

function tags(x, F, s, k, film3d) {
  const S = F.M.S;
  (s.tags || []).forEach((tg, ti) => {
    let p = null;
    if (tg.at) p = tg.at;
    else if (tg.joint != null) {
      p = S.joints[tg.joint];
      if (p && tg.moved && s.disp) { const g = swing(s, F.u), d = s.disp.u[tg.joint] || [0, 0, 0]; p = [p[0] + d[0] * s.k * g, p[1] + d[1] * s.k * g, p[2] + d[2] * s.k * g]; }
      if (p && tg.moved && s.kind === "act" && s.U) { const g = swing(s, F.u), d = s.U[tg.joint] || [0, 0, 0]; p = [p[0] + d[0] * s.k * g, p[1] + d[1] * s.k * g, p[2] + d[2] * s.k * g]; }
      if (p && tg.mode && s.mode) { const g = swing(s, F.u), d = s.mode.u[tg.joint] || [0, 0, 0], k = 0.08 * F.M.L * g; p = [p[0] + d[0] * k, p[1] + d[1] * k, p[2] + d[2] * k]; }
    } else if (tg.frame != null && tg.frame >= 0) p = frameMid(S, tg.frame);
    if (!p) return;
    const q = film3d.project(p);
    if (!q) return;
    const a = clamp(((F.uTag ?? F.u) - 0.35) / 0.1, 0, 1);
    if (!a) return;
    x.save(); x.globalAlpha *= a;
    x.font = `700 ${Math.round(15 * k)}px ${FONT()}`;
    const tw = x.measureText(tg.text).width + 20 * k, bx = q[0] + (ti % 2 ? -tw - 18 * k : 18 * k), by = q[1] - (54 + 34 * ti) * k;
    x.strokeStyle = "#fff"; x.lineWidth = 2 * k; x.beginPath(); x.moveTo(q[0], q[1]); x.lineTo(ti % 2 ? bx + tw : bx, by + 28 * k); x.stroke();
    x.fillStyle = "#E3543C"; x.beginPath(); x.arc(q[0], q[1], 5 * k, 0, 7); x.fill();
    panel(x, k, bx, by, tw, 30 * k, 0.9);
    x.fillStyle = "#fff"; x.textAlign = "left"; x.fillText(tg.text, bx + 10 * k, by + 21 * k);
    x.restore();
  });
}

function summaryCard(x, F, k) {
  const S = F.M.S, W = F.w, H = F.h, pad = 40 * k;
  const rows = [];
  const R = S.reactions?.max || {}, D = S.displacements?.max || {}, Fo = S.forces || {};
  rows.push(["Model", `${n0(S.frames.ids.length)} members, ${n0(S.areas.ids.length)} plates, ${n0(S.supports.length)} supports`]);
  const nCombo = S.combos.length || S.cases.filter((c) => c.kind === "combo").length, nCase = S.patterns.length || Object.keys(S.loads).length || S.cases.filter((c) => c.kind !== "combo").length;
  if (nCombo || nCase) rows.push(["Loads", `${n0(nCase)} load cases, ${n0(nCombo)} combinations`]);
  if (R.down) rows.push(["Largest compression at a support", `${kN(R.down.value)} (${R.down.case})`]);
  if (R.up) rows.push(["Largest uplift", R.up.value < 0 ? `${kN(-R.up.value)} (${R.up.case})` : "none"]);
  if (R.h) rows.push(["Largest horizontal reaction", `${kN(R.h.value)} (${R.h.case})`]);
  if (D.h) rows.push(["Largest movement on plan", `${mm(D.h.value)} (${D.h.case})`]);
  if (D.v) rows.push(["Largest vertical movement", `${mm(D.v.value)} (${D.v.case})`]);
  if (Fo.M?.length) rows.push(["Largest bending moment", `${n0(Math.max(...S.frames.ids.map((i) => Fo.M[i] || 0)))} kN m`]);
  if (S.modes?.length || S.modal?.length) { const m0 = (S.modal || S.modes)[0]; rows.push(["First mode of vibration", `${n0(m0.period, 2)} s`]); }
  const lastM = S.modal?.[S.modal.length - 1];
  if (lastM?.sumux != null) rows.push([`Mass in ${S.modal.length} modes`, `${n0(100 * lastM.sumux)}% along X, ${n0(100 * lastM.sumuy)}% along Y`]);
  const eq = S.cases.filter((c) => c.kind !== "combo" && isQuake(c.name, c.type)).map((c) => [c.name, S.base?.[c.name]]).filter(([, b]) => b);
  if (eq.length) rows.push(["Earthquake base shear", eq.map(([n, b]) => `${kN(Math.max(Math.abs(b[0]), Math.abs(b[1])))} (${n})`).join(", ")]);
  const st = F.shots.find((q) => q.kind === "stress");
  let ds = Object.values(S.design || {}).map((d) => d.ratio).filter((v) => v != null);
  if (ds.length) rows.push(["Highest utilisation ratio", n0(Math.max(...ds), 2)]);
  else if (st) { ds = st.ratio.filter((v) => v != null); rows.push([`Highest stress ratio (fy ${n0(F.set.steel_fy || 355)} MPa)`, n0(Math.max(...ds), 2)]); }
  const pl = F.shots.filter((q) => q.kind === "piles");
  let pmax = null;
  pl.forEach((q) => { const v = Math.max(...q.ratio.values()); rows.push([`Highest pile ratio, ${{ comp: "compression", tens: "tension", lat: "lateral" }[q.what]}`, `${n0(v, 2)}${v > 1 ? ` (${[...q.ratio.values()].filter((x) => x > 1).length} piles over)` : ""}`]); pmax = Math.max(pmax ?? 0, v); });
  const rh = Math.min(34 * k, (H - 260 * k) / rows.length);
  const bw = Math.min(W - 2 * pad, 900 * k);
  panel(x, k, pad, 96 * k, bw, 90 * k + rows.length * rh + 50 * k, 0.88);
  text(x, k, "The results in brief", pad + 26 * k, 140 * k, 26, "#fff", 600);
  rows.forEach(([a, b], j) => {
    const y = 180 * k + (j + 0.7) * rh;
    if (j % 2 === 0) { x.fillStyle = "rgba(255,255,255,.05)"; x.fillRect(pad + 16 * k, 180 * k + j * rh, bw - 32 * k, rh); }
    text(x, k, a, pad + 26 * k, y, 16, "#9FB4C2", 500);
    text(x, k, b, pad + 26 * k + bw * 0.45, y, 16.5, "#fff", 600);
  });
  let max = ds.length ? Math.max(...ds) : null;
  let verdict = max == null ? "Utilisation ratios were not in the file (no design tables exported)." : max <= 1 ? (st ? "Every steel member is below yield." : "Every member designed is within its capacity.") : "Some members are above their capacity: see the members in red.";
  if (pmax != null) { max = Math.max(max ?? 0, pmax); verdict = pmax > 1 ? "Some piles carry more than their capacity: see the piles in red." : max <= 1 ? "Every pile is within its capacity." : verdict; }
  text(x, k, verdict, pad + 26 * k, 180 * k + rows.length * rh + 34 * k, 17, max == null ? "#DCE7EC" : max <= 1 ? "#8FE0C4" : "#F2A093", 600);
}

function topBars(x, F, s, k) {
  const W = F.w, X = W - 22 * k - 300 * k, Y = 172 * k, rows = s.top || [], S = F.M.S;
  panel(x, k, X, Y, 300 * k, 44 * k + rows.length * 26 * k, 0.8);
  text(x, k, "Most utilised members", X + 16 * k, Y + 28 * k, 14, "#fff", 600);
  rows.forEach((d, j) => {
    const y = Y + 44 * k + j * 26 * k, pos = S.frames.ids.indexOf(d.i), nm = S.frames.names?.[pos] ?? d.i;
    text(x, k, `Frame ${nm}`, X + 16 * k, y + 16 * k, 12.5, "#DCE7EC");
    const bx = X + 110 * k, bw = 140 * k;
    x.fillStyle = "rgba(255,255,255,.15)"; x.fillRect(bx, y + 6 * k, bw, 12 * k);
    x.fillStyle = heat(d.ratio); x.fillRect(bx, y + 6 * k, bw * clamp(d.ratio / 1.2, 0, 1), 12 * k);
    x.fillStyle = "#fff"; x.fillRect(bx + bw / 1.2, y + 3 * k, 2 * k, 18 * k);
    text(x, k, n0(d.ratio, 2), X + 288 * k, y + 16 * k, 12.5, "#fff", 600, "right");
  });
}

// each load case as bars of what it puts on the supports, vertical and horizontal, growing in turn
function casesCard(x, F, s, u, k) {
  const W = F.w, pad = 22 * k, rows = s.rows.slice(0, 14), bw = Math.min(W - 2 * pad, 820 * k), rh = 25 * k, Y = 96 * k;
  const maxV = Math.max(...rows.map((r) => Math.abs(r.fz)), 1), maxH = Math.max(...rows.map((r) => r.h), 1);
  panel(x, k, pad, Y, bw, 96 * k + rows.length * rh, 0.86);
  text(x, k, `${s.rows.length} load cases, by what each puts on the supports`, pad + 22 * k, Y + 36 * k, 21, "#fff", 600);
  const c1 = pad + 22 * k, c2 = pad + bw * 0.36, c3 = pad + bw * 0.68, cw = bw * 0.28;
  text(x, k, "Vertical", c2, Y + 64 * k, 12.5, "#9FB4C2", 600); text(x, k, "Horizontal", c3, Y + 64 * k, 12.5, "#9FB4C2", 600);
  rows.forEach((r, j) => {
    const y = Y + 76 * k + j * rh, g = ease((u - 0.1 - j * 0.03) / 0.25);
    x.font = `500 ${Math.round(13.5 * k)}px ${FONT()}`;
    let nm = r.n; while (x.measureText(nm).width > c2 - c1 - 12 * k && nm.length > 4) nm = nm.slice(0, -1);
    text(x, k, nm, c1, y + 14 * k, 13.5, "#DCE7EC");
    const bar = (X, v, max, label) => { x.fillStyle = "rgba(255,255,255,.12)"; x.fillRect(X, y + 4 * k, cw * 0.62, 12 * k); x.fillStyle = heat(Math.min(0.999, Math.abs(v) / max)); x.fillRect(X, y + 4 * k, cw * 0.62 * (Math.abs(v) / max) * g, 12 * k); text(x, k, label, X + cw * 0.66, y + 15 * k, 12.5, "#fff", 600); };
    bar(c2, r.fz, maxV, kN(r.fz)); bar(c3, r.h, maxH, kN(r.h));
  });
  if (s.rows.length > rows.length) text(x, k, `and ${s.rows.length - rows.length} more`, c1, Y + 84 * k + rows.length * rh, 12.5, "#9FB4C2");
}

// the modes: period and the share of the mass each moves, with the running totals reaching 90%
function modalCard(x, F, u, k) {
  const S = F.M.S, all = S.modal, W = F.w, pad = 22 * k;
  let last = all.findIndex((m) => (m.sumux ?? 0) >= 0.9 && (m.sumuy ?? 0) >= 0.9);
  last = last < 0 ? Math.min(all.length, 12) : Math.min(Math.max(last + 2, 6), 12, all.length);
  const rows = all.slice(0, last), rh = 25 * k, bw = Math.min(W - 2 * pad, 860 * k), Y = 96 * k;
  panel(x, k, pad, Y, bw, 112 * k + rows.length * rh, 0.88);
  text(x, k, `How it vibrates: ${all.length} modes`, pad + 22 * k, Y + 36 * k, 21, "#fff", 600);
  const cols = [["Mode", 0.03], ["Period", 0.12], ["Mass along X", 0.25], ["Mass along Y", 0.48], ["Twist", 0.71], ["Total X / Y", 0.85]];
  cols.forEach(([h, p]) => text(x, k, h, pad + bw * p, Y + 64 * k, 12.5, "#9FB4C2", 600));
  const bar = (X, v, g, col) => { const w = bw * 0.16; x.fillStyle = "rgba(255,255,255,.12)"; x.fillRect(X, curY + 5 * k, w, 11 * k); x.fillStyle = col; x.fillRect(X, curY + 5 * k, w * clamp(v || 0, 0, 1) * g, 11 * k); };
  let curY = 0;
  const hitX = all.findIndex((m) => (m.sumux ?? 0) >= 0.9), hitY = all.findIndex((m) => (m.sumuy ?? 0) >= 0.9);
  rows.forEach((m, j) => {
    curY = Y + 76 * k + j * rh;
    const g = ease((u - 0.08 - j * 0.035) / 0.2);
    if (j === hitX || j === hitY) { x.fillStyle = "rgba(143,224,196,.12)"; x.fillRect(pad + 10 * k, curY, bw - 20 * k, rh); }
    text(x, k, String(m.mode), pad + bw * 0.03, curY + 16 * k, 13.5, "#fff", 600);
    text(x, k, `${n0(m.period, 2)} s`, pad + bw * 0.12, curY + 16 * k, 13.5, "#DCE7EC");
    bar(pad + bw * 0.25, m.ux, g, "#3F7CC4"); text(x, k, `${n0(100 * (m.ux || 0), 1)}%`, pad + bw * 0.42, curY + 16 * k, 12.5, "#fff");
    bar(pad + bw * 0.48, m.uy, g, "#E3B23C"); text(x, k, `${n0(100 * (m.uy || 0), 1)}%`, pad + bw * 0.65, curY + 16 * k, 12.5, "#fff");
    text(x, k, `${n0(100 * (m.rz || 0), 1)}%`, pad + bw * 0.71, curY + 16 * k, 12.5, "#DCE7EC");
    text(x, k, m.sumux != null ? `${n0(100 * m.sumux)}% / ${n0(100 * m.sumuy)}%` : "–", pad + bw * 0.85, curY + 16 * k, 12.5, "#fff", 600);
  });
  const note = hitX >= 0 && hitY >= 0 ? `90% of the mass is reached by mode ${all[hitX].mode} along X and mode ${all[hitY].mode} along Y` : "The modes in the file do not reach 90% of the mass in both directions";
  text(x, k, note, pad + 22 * k, Y + 100 * k + rows.length * rh, 14, hitX >= 0 && hitY >= 0 ? "#8FE0C4" : "#F2A093", 600);
}

function combosCard(x, F, k) {
  const S = F.M.S, W = F.w, pad = 22 * k;
  const all = S.combos.length ? S.combos : S.cases.filter((c) => c.kind === "combo").map((c) => ({ name: c.name, parts: [] }));
  const list = all.slice(0, 12);
  const bw = Math.min(W - 2 * pad, 760 * k), rh = 25 * k;
  const Y = 96 * k;
  panel(x, k, pad, Y, bw, 70 * k + list.length * rh, 0.85);
  text(x, k, `How the loads are combined (${all.length})`, pad + 22 * k, Y + 38 * k, 22, "#fff", 600);
  list.forEach((c, j) => {
    const parts = c.parts.length ? c.parts.map(([n, f]) => `${n0(f, 2)} ${n}`).join(" + ") : "";
    x.font = `500 ${Math.round(14 * k)}px ${FONT()}`;
    const full = parts ? `${c.name}: ${parts}` : c.name;
    let t = full;
    while (x.measureText(t).width > bw - 44 * k && t.length > 10) t = t.slice(0, -2);
    text(x, k, t === full ? t : t + "…", pad + 22 * k, Y + 70 * k + j * rh, 14, "#DCE7EC");
  });
  if (all.length > list.length) text(x, k, `and ${all.length - list.length} more`, pad + 22 * k, Y + 70 * k + list.length * rh, 13, "#9FB4C2");
}

function mapOver(x, F, s, u) {
  const W = F.w, H = F.h, k = H / 720, g = s.geo, fine = WorldMap.get().fine;
  const home = F.home !== undefined ? F.home : (F.home = WorldMap.countryAt(g.lon, g.lat) || null);
  const b = home ? home.box : [g.lon - 3, g.lat - 3, g.lon + 3, g.lat + 3];
  const far = Math.max(6, Math.min(40, 0.9 * Math.max(b[3] - b[1], (b[2] - b[0]) * Math.cos(g.lat * Math.PI / 180) * H / W, Math.abs(g.lat - (b[1] + b[3]) / 2) * 2.2)));
  const near = fine ? 0.03 : 0.22, z = ease((u - 0.2) / 0.6), half = far * Math.pow(near / far, z);
  const c0 = home ? [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2] : [g.lon, g.lat], cz = Math.min(1, z * 1.6), lon = c0[0] + (g.lon - c0[0]) * cz, lat = c0[1] + (g.lat - c0[1]) * cz;
  const fadeOut = 1 - ease((u - 0.84) / 0.16);
  x.save(); x.globalAlpha = fadeOut;
  const m = WorldMap.draw(x, W, H, lon, lat, half, home), px = m.X(g.lon), py = m.Y(g.lat);
  if (home && z < 0.75) {
    x.globalAlpha = fadeOut * (1 - ease((z - 0.45) / 0.3));
    const cx = m.X((b[0] + b[2]) / 2), cy = m.Y((b[1] + b[3]) / 2);
    x.font = `700 ${Math.round(30 * k)}px ${FONT()}`; x.textAlign = "center"; x.fillStyle = "rgba(60,52,34,.85)"; x.fillText(home.name.toUpperCase().split("").join(" "), cx, cy - 26 * k);
    x.globalAlpha = fadeOut;
  }
  const pulse = (u * s.dur * 0.9) % 1;
  x.strokeStyle = `rgba(227,84,60,${0.9 * (1 - pulse)})`; x.lineWidth = 3 * k; x.beginPath(); x.arc(px, py, (8 + 26 * pulse) * k, 0, 7); x.stroke();
  x.fillStyle = "#E3543C"; x.beginPath(); x.arc(px, py, 7 * k, 0, 7); x.fill(); x.strokeStyle = "#fff"; x.lineWidth = 2.5 * k; x.stroke();
  x.font = `500 ${Math.round(13 * k)}px ${FONT()}`; x.fillStyle = "rgba(255,255,255,.8)"; x.textAlign = "right";
  x.fillText(`${n0(Math.abs(g.lat), 3)}° ${g.lat >= 0 ? "N" : "S"}, ${n0(Math.abs(g.lon), 3)}° ${g.lon >= 0 ? "E" : "W"}`, W - 22 * k, H - 22 * k);
  x.restore();
}

function hud(x, F, s, u, i, t) {
  const W = F.w, H = F.h, k = H / 720, pad = 22 * k, set = F.set;
  const fade = Math.min(1, (u * s.dur) / 0.5, ((1 - u) * s.dur) / 0.5);
  const nxt = F.shots[i + 1];
  const dip = s.cut && u * s.dur < 0.45 ? 1 - (u * s.dur) / 0.45 : nxt && nxt.cut && (1 - u) * s.dur < 0.45 ? 1 - ((1 - u) * s.dur) / 0.45 : 0;
  x.textBaseline = "alphabetic";
  const g = x.createLinearGradient(0, 0, 0, 86 * k); g.addColorStop(0, "rgba(16,28,44,.72)"); g.addColorStop(1, "rgba(16,28,44,0)");
  x.fillStyle = g; x.fillRect(0, 0, W, 86 * k);
  if (!s.title && s.kind !== "map") {
    text(x, k, F.title, pad, 34 * k, 19, "#fff", 600);
    text(x, k, s.kind === "summary" ? "Summary" : SHOTS[{ loads: "loads", cases: "loads", combos: "loads", reactions: "reactions", family: "loads", build: "model", piles: "piles", act: "quake", move: "movements", mode: "quake", modal: "quake", quake: "quake", forces: "forces", plates: "forces", design: "design", stress: "design" }[s.kind]] || "The model", pad, 58 * k, 15, "#CFE1E7", 500);
  }
  text(x, k, "Triton", W - pad, 34 * k, 14, "rgba(255,255,255,.75)", 600, "right");
  if (!SCENIC.has(s.kind)) {
    const hidden = [F.M.site.land.length ? "land" : "", F.M.site.breakwaters.some((r) => r.kind === "revetment") ? "revetment" : "", F.M.site.breakwaters.some((r) => r.kind !== "revetment") ? "breakwaters" : ""].filter(Boolean);
    if (hidden.length) text(x, k, `${hidden.join(", ").replace(/^./, (c) => c.toUpperCase()).replace(/, ([^,]*)$/, " and $1")} hidden for clarity`, W - pad, 56 * k, 13, "rgba(255,255,255,.75)", 500, "right");
  }
  x.globalAlpha = Math.max(0, fade);
  if (s.title || s.kind === "map") {
    if (s.kind === "map") x.globalAlpha = Math.max(0, Math.min(fade, 1 - (u - 0.55) / 0.12));
    const ttl = F.title, maxIn = W - 2 * pad - 52 * k;
    let fs = 38; x.font = `600 ${Math.round(fs * k)}px ${FONT()}`;
    while (fs > 26 && x.measureText(ttl).width > maxIn) { fs -= 2; x.font = `600 ${Math.round(fs * k)}px ${FONT()}`; }
    const tl = wrap(x, ttl, maxIn).slice(0, 3);
    const sub = s.kind === "end" ? "Thank you" : ["Structural analysis", set.client].filter(Boolean).join("  ·  ");
    x.font = `500 ${Math.round(20 * k)}px ${FONT()}`; const sw = x.measureText(sub).width;
    x.font = `600 ${Math.round(fs * k)}px ${FONT()}`;
    const bw = Math.min(W - 2 * pad, Math.max(420 * k, ...tl.map((q) => x.measureText(q).width), sw) + 52 * k), lh = fs * 1.22 * k, bh = 44 * k + tl.length * lh + 36 * k;
    panel(x, k, pad, H * 0.56, bw, bh);
    tl.forEach((q, j) => text(x, k, q, pad + 26 * k, H * 0.56 + 24 * k + (j + 0.8) * lh, fs, "#fff", 600));
    text(x, k, sub, pad + 26 * k, H * 0.56 + 24 * k + tl.length * lh + 30 * k, 20, "#CFE1E7");
  } else if (s.kind === "summary") summaryCard(x, F, k);
  else if (s.kind === "combos") combosCard(x, F, k);
  else if (s.kind === "cases") casesCard(x, F, s, u, k);
  else if (s.kind === "modal") modalCard(x, F, u, k);
  else if (s.head) {
    const lh = 27 * k;
    x.font = `500 ${Math.round(16.5 * k)}px ${FONT()}`;
    const maxW = Math.min(W - 2 * pad, 760 * k) - 44 * k;
    const lines = (s.lines || []).flatMap((l) => wrap(x, l, maxW));
    const hh = 62 * k + lines.length * lh;
    panel(x, k, pad, H - hh - 46 * k, maxW + 44 * k, hh);
    text(x, k, s.head, pad + 22 * k, H - hh - 46 * k + 40 * k, 25, "#fff", 600);
    lines.forEach((ln, j) => text(x, k, ln, pad + 22 * k, H - hh - 46 * k + 72 * k + j * lh, 16.5, /within its capacity|stays in compression/.test(ln) ? "#8FE0C4" : "#DCE7EC"));
  }
  if (s.legend === "relative" || s.legend === "util" || s.legend === "axial") legend(x, F, s.legend, k);
  if (s.kind === "design" || s.kind === "stress") topBars(x, F, s, k);
  if (s.legend === "sections") sectionKey(x, F, k);
  if (s.legend === "groups") groupKey(x, F, k);
  x.globalAlpha = 1;
  x.fillStyle = "rgba(255,255,255,.22)"; x.fillRect(0, H - 5 * k, W, 5 * k);
  x.fillStyle = "#E3B23C"; x.fillRect(0, H - 5 * k, W * Math.min(1, t / F.total), 5 * k);
  if (dip > 0) { x.fillStyle = `rgba(10,18,30,${Math.min(1, dip)})`; x.fillRect(0, 0, W, H); }
}
function groupKey(x, F, k) {
  const G = F.M.groups, X = F.w - 22 * k - 250 * k, Y = 96 * k;
  panel(x, k, X, Y, 250 * k, 22 * k + G.length * 24 * k, 0.78);
  G.forEach((g, j) => { x.fillStyle = g.colour; x.fillRect(X + 14 * k, Y + 14 * k + j * 24 * k, 14 * k, 14 * k); text(x, k, `${g.name} (${g.count})`, X + 36 * k, Y + 26 * k + j * 24 * k, 13, "#DCE7EC"); });
}
function sectionKey(x, F, k) {
  const S = F.M.S, X = F.w - 22 * k - 250 * k, Y = 96 * k, list = [["Steel members", STEEL], ["Concrete members", CONC], ["Plates", CONC_TOP]].filter(([n]) =>
    n === "Plates" ? S.areas.ids.length : S.frames.section.some((q) => (F.M.sect[q]?.steel ? "Steel members" : "Concrete members") === n));
  panel(x, k, X, Y, 250 * k, 22 * k + list.length * 24 * k, 0.78);
  list.forEach(([n, c], j) => { x.fillStyle = `rgb(${c.map((v) => Math.round(v * 255))})`; x.fillRect(X + 14 * k, Y + 14 * k + j * 24 * k, 14 * k, 14 * k); text(x, k, n, X + 36 * k, Y + 26 * k + j * 24 * k, 13, "#DCE7EC"); });
}

// --- one film: its picture, its clock ---------------------------------------------------------------
export class Player {
  constructor(stage, S, set) {
    this.stage = stage;
    this.gl = document.createElement("canvas");
    this.film3d = new Film3D(this.gl);
    this.cache = new Map();
    this.load(S, set);
  }
  load(S, set) {
    this.S = S; this.set = set;
    const [w, h] = String(set.size || "1280x720").split("x").map(Number);
    this.w = w || 1280; this.h = h || 720;
    this.M = prepare(S, set);
    this.cache = new Map();
    const d = direct(S, set, this.M);
    this.shots = d.shots; this.total = d.total; this.title = d.title;
    this.home = undefined;
    if (this.film3d.available()) {
      const m = new Mesh(), rv = new Mesh();
      surroundings(m, this.M.site, rv);
      this.film3d.setStatic({ solid: m, rev: rv, centre: this.M.centre, R: Math.max(this.M.ext * 3, this.M.L * 6), wl: this.M.wl, scale: this.M.L });
    }
  }
  frame(t) {
    const x = this.stage.getContext("2d");
    if (this.stage.width !== this.w) { this.stage.width = this.w; this.stage.height = this.h; }
    let i = this.shots.findIndex((s) => t < s.t0 + s.dur); if (i < 0) i = this.shots.length - 1;
    const s = this.shots[i], u = clamp((t - s.t0) / s.dur, 0, 1);
    this.u = u; this.uTag = null;
    x.setTransform(1, 0, 0, 1, 0, 0);
    if (s.steps) stepOn(this, s, u);
    if (this.film3d.available()) {
      this.film3d.setDynamic(layers(this, s, u));
      // the sea clear enough to see the piles and their colours, except in the opening shots;
      // the revetment only where the setting is shown (a note says so elsewhere)
      this.film3d.draw(camAt(s, u), { width: this.w, height: this.h, time: t, waterAlpha: SCENIC.has(s.kind) ? 0.5 : 0.13, staticAlpha: SCENIC.has(s.kind) ? 1 : 0, revAlpha: SCENIC.has(s.kind) ? 1 : 0 });
      x.drawImage(this.gl, 0, 0, this.w, this.h);
    } else {
      x.fillStyle = "#16283D"; x.fillRect(0, 0, this.w, this.h);
      text(x, this.h / 720, `The 3D picture needs WebGL: ${this.film3d.failed}`, 30, 60, 16, "#fff");
    }
    if (s.kind === "map") mapOver(x, this, s, u);
    if (s.tags) tags(x, this, s, this.h / 720, this.film3d);
    hud(x, this, s, u, i, t);
    return i;
  }
}

// --- the file: each frame drawn, encoded (H.264) and packed into an MP4, not tied to the clock -----
async function encoderFor(w, h) {
  if (typeof VideoEncoder === "undefined" || typeof VideoFrame === "undefined") return null;
  for (const codec of ["avc1.640028", "avc1.4d0028", "avc1.420028"]) {
    const cfg = { codec, width: w, height: h, bitrate: h >= 1080 ? 12e6 : 7e6, framerate: FPS, avc: { format: "avc" } };
    try { if ((await VideoEncoder.isConfigSupported(cfg)).supported) return cfg; } catch (e) { /* the next */ }
  }
  return null;
}
// an MP4 of one H.264 track at a constant frame rate, the index ahead of the pictures so it plays at once
export function mp4Of(samples, w, h, avcC, fps) {
  const te = new TextEncoder(), u32 = (n) => [(n >>> 24) & 255, (n >>> 16) & 255, (n >>> 8) & 255, n & 255], u16 = (n) => [(n >> 8) & 255, n & 255];
  const zeros = (n) => new Array(n).fill(0), str = (t) => Array.from(te.encode(t));
  const cat = (parts) => { const len2 = parts.reduce((a, q) => a + q.length, 0), o = new Uint8Array(len2); let at = 0; parts.forEach((q) => { o.set(q, at); at += q.length; }); return o; };
  const box = (type, ...parts) => { const body = cat(parts); return cat([u32(8 + body.length), str(type), body]); };
  const n = samples.length, TS = fps * 1000, durMs = Math.round((n * 1000) / fps), matrix = [].concat(u32(0x10000), zeros(12), u32(0x10000), zeros(12), u32(0x40000000));
  const keys = []; samples.forEach((q, i) => { if (q.key) keys.push(i + 1); });
  const sizes = new Uint8Array(4 * n), dv = new DataView(sizes.buffer); let bytes = 0;
  samples.forEach((q, i) => { dv.setUint32(4 * i, q.data.length); bytes += q.data.length; });
  const ftyp = box("ftyp", str("isom"), u32(0x200), str("isomiso2avc1mp41"));
  const moov = (off) => box("moov",
    box("mvhd", zeros(12), u32(1000), u32(durMs), u32(0x10000), u16(0x100), zeros(10), matrix, zeros(24), u32(2)),
    box("trak",
      box("tkhd", [0, 0, 0, 3], zeros(8), u32(1), zeros(4), u32(durMs), zeros(16), matrix, u32(w << 16), u32(h << 16)),
      box("mdia",
        box("mdhd", zeros(12), u32(TS), u32(n * 1000), u16(0x55c4), u16(0)),
        box("hdlr", zeros(8), str("vide"), zeros(12), str("VideoHandler"), [0]),
        box("minf",
          box("vmhd", [0, 0, 0, 1], zeros(8)),
          box("dinf", box("dref", zeros(4), u32(1), box("url ", [0, 0, 0, 1]))),
          box("stbl",
            box("stsd", zeros(4), u32(1), box("avc1", zeros(6), u16(1), zeros(16), u16(w), u16(h), u32(0x480000), u32(0x480000), zeros(4), u16(1), zeros(32), u16(24), u16(0xffff), box("avcC", avcC))),
            box("stts", zeros(4), u32(1), u32(n), u32(1000)),
            box("stss", zeros(4), u32(keys.length), ...keys.map(u32)),
            box("stsc", zeros(4), u32(1), u32(1), u32(n), u32(1)),
            box("stsz", zeros(4), u32(0), u32(n), sizes),
            box("stco", zeros(4), u32(1), u32(off)))))));
  const head = moov(ftyp.length + moov(0).length + 8);
  return new Blob([ftyp, head, new Uint8Array(u32(8 + bytes).concat(str("mdat")))].concat(samples.map((q) => q.data)), { type: "video/mp4" });
}

export async function makeVideo(player, onProgress, stopped) {
  const cfg = await encoderFor(player.w, player.h);
  const n = Math.max(1, Math.round(player.total * FPS));
  if (cfg) {
    const samples = [];
    let avcC = null, fail = null;
    const enc = new VideoEncoder({
      output: (chunk, meta) => {
        const d = new Uint8Array(chunk.byteLength); chunk.copyTo(d); samples.push({ data: d, key: chunk.type === "key" });
        const q = meta?.decoderConfig?.description;
        if (q && !avcC) avcC = q instanceof ArrayBuffer ? new Uint8Array(q.slice(0)) : new Uint8Array(q.buffer.slice(q.byteOffset, q.byteOffset + q.byteLength));
      },
      error: (e) => { fail = e; },
    });
    enc.configure(cfg);
    const idle = () => new Promise((r) => { const c = new MessageChannel(); c.port1.onmessage = () => r(); c.port2.postMessage(0); });
    for (let i = 0; i < n && !fail; i++) {
      if (stopped()) { try { enc.close(); } catch (e) { /* closed */ } return null; }
      player.frame(Math.min(player.total - 0.001, i / FPS));
      const vf = new VideoFrame(player.stage, { timestamp: Math.round((i * 1e6) / FPS), duration: Math.round(1e6 / FPS) });
      enc.encode(vf, { keyFrame: i % (2 * FPS) === 0 }); vf.close();
      onProgress((i + 1) / n);
      do { await idle(); } while (enc.encodeQueueSize > 4 && !fail);
    }
    if (!fail) await enc.flush();
    try { enc.close(); } catch (e) { /* closed */ }
    if (fail) throw fail;
    if (!avcC || !samples.length) throw new Error("the encoder gave no picture");
    return { blob: mp4Of(samples, player.w, player.h, avcC, FPS), ext: "mp4" };
  }
  // no encoder here: the picture is recorded while it plays (as long as the film)
  const mime = ["video/mp4;codecs=avc1.42E01E", "video/mp4", "video/webm;codecs=vp9", "video/webm"].find((m) => window.MediaRecorder?.isTypeSupported?.(m));
  if (!mime || !player.stage.captureStream) throw new Error("This browser cannot make a video. Use a current Chrome or Edge.");
  const rec = new MediaRecorder(player.stage.captureStream(FPS), { mimeType: mime, videoBitsPerSecond: 8e6 });
  const chunks = [];
  rec.ondataavailable = (e) => e.data?.size && chunks.push(e.data);
  rec.start(1000);
  const t0 = performance.now();
  await new Promise((done) => {
    const step = () => {
      const t = (performance.now() - t0) / 1000;
      if (t >= player.total || stopped()) return done();
      player.frame(t); onProgress(t / player.total);
      requestAnimationFrame(step);
    };
    step();
  });
  await new Promise((r) => { rec.onstop = r; setTimeout(() => rec.stop(), 300); });
  return { blob: new Blob(chunks, { type: mime.split(";")[0] }), ext: /mp4/.test(mime) ? "mp4" : "webm" };
}

// --- the tab ------------------------------------------------------------------------------------
export async function renderFilm(host, h) {
  const { api, esc, ROOT } = h;
  const base = `${ROOT}/api/projects/${h.project().id}/films`;
  host.innerHTML = `<p class="sub">A short film of a SAP2000 analysis to show the client: the site on the map, the structure in 3D in its sea with the breakwater,
      then the loads, the reactions, how it moves and how hard each member works, from green to red. Its length follows what the file holds.</p>
    <div class="panel film-add"><div class="row" style="flex-wrap:wrap;gap:10px;align-items:center">
      <label class="button-like"><input type="file" id="film-file" accept=".s2k,.$2k,.txt,.xlsx,.xlsm,.zip,.gz,.trsap" multiple hidden><span class="film-pick">Add SAP2000 results…</span></label>
      <button class="quiet" id="film-sample">Make a sample film</button>
      <span class="status" id="film-read"></span></div>
      <p class="status">SAP2000: File &gt; Export &gt; SAP2000 .s2k text file, or Display &gt; Show Tables &gt; Export to Excel (.xlsx). Tick the model definition tables
      (joint coordinates, connectivity, section assignments, restraints, load patterns and combinations) with the analysis results (joint reactions, joint displacements, element forces)
      and the design summary tables for the utilisation. The file is read on this computer, however large: what goes to the site is a small file of all its results
      (every case, joint, member and plate), which each film can download again as a .trsap file and which can be added here in place of the big one.
      Results of a meshed model often come without the loads and sections: pick the results file together with the model's own .$2k/.s2k file (hold Ctrl)
      and the two are put together, so the film shows every load case with its values.</p>
      <div class="film-bar" hidden><i></i></div></div>
    <div id="film-list"></div><div id="film-open"></div>`;
  const $ = (q) => host.querySelector(q);
  const status = $("#film-read"), bar = $(".film-bar");
  let films = [];
  const list = async () => {
    films = await api(base);
    $("#film-list").innerHTML = films.length ? `<div class="film-cards">${films.map((f) => `<div class="panel film-card" data-id="${esc(f.id)}">
        <b>${esc(f.settings.name)}</b><span class="status">${esc(f.file)}${f.sample ? " (made-up model)" : ""}</span>
        <span class="status">${f.counts.frames ?? 0} members, ${f.counts.areas ?? 0} plates, ${f.counts.combos ?? 0} combinations${f.counts.max_ratio != null ? `, highest ratio ${n0(f.counts.max_ratio, 2)}` : ""}</span>
        <div class="row"><button data-open="${esc(f.id)}">Open</button><button class="quiet" data-small="${esc(f.id)}" title="All the results in a small file (.trsap), to keep or to add again here">Download small file</button><button class="quiet danger" data-del="${esc(f.id)}">Delete</button></div></div>`).join("")}</div>`
      : `<p class="status">No films yet. Add a SAP2000 results file, or make the sample to see one.</p>`;
    $("#film-list").querySelectorAll("[data-open]").forEach((b) => (b.onclick = () => open(b.dataset.open)));
    $("#film-list").querySelectorAll("[data-small]").forEach((b) => (b.onclick = async () => {
      const f = films.find((x) => x.id === b.dataset.small);
      const text = await fetch(`${base}/${f.id}/summary`).then((r) => r.text());
      const gz = await new Response(new Blob([text]).stream().pipeThrough(new CompressionStream("gzip"))).blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(gz);
      a.download = `${(f.settings.name || "results").replace(/[\\/:*?"<>|]+/g, " ").trim()}.trsap`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    }));
    $("#film-list").querySelectorAll("[data-del]").forEach((b) => (b.onclick = async () => {
      if (!(await confirmBox("Delete this film? Its summary and settings go; the SAP2000 file on your computer stays.", { ok: "Delete" }))) return;
      await api(`${base}/${b.dataset.del}`, { method: "DELETE" });
      if (h.state.film === b.dataset.del) { h.state.film = null; $("#film-open").innerHTML = ""; }
      list();
    }));
  };
  $("#film-sample").onclick = async () => {
    status.textContent = "Making the sample…";
    const f = await api(`${base}/sample`, { method: "POST" });
    status.textContent = "";
    await list();
    open(f.id);
  };
  $("#film-file").onchange = async (e) => {
    const files = [...e.target.files];
    e.target.value = "";
    if (!files.length) return;
    bar.hidden = false;
    const began = performance.now();
    try {
      // one file, or a results file and its model file (sections and loads), put together
      const read = [];
      for (const [n, one] of files.entries()) read.push(await readSap(one, (f, step, rows) => {
        bar.firstElementChild.style.width = `${(f * 100).toFixed(1)}%`;
        const el = (performance.now() - began) / 1000, left = f > 0.03 ? (el / f) * (1 - f) : null;
        status.textContent = `${files.length > 1 ? `${one.name} (${n + 1} of ${files.length}): ` : ""}${step}: ${Math.floor(f * 100)}%, ${n0(rows)} rows${left != null ? `, about ${left < 90 ? Math.max(1, Math.round(left)) + " s" : Math.round(left / 60) + " min"} left` : ""}`;
      }));
      const results = (x) => (x.reactions?.cases?.length || 0) + (x.displacements?.cases?.length || 0) + Object.keys(x.forces || {}).length + (x.modes?.length || 0);
      const order = read.map((x, n) => n).sort((a, b) => results(read[b]) - results(read[a]));
      let S = read[order[0]];
      const file = files[order[0]];
      for (const n of order.slice(1)) S = mergeModel(S, read[n]);
      status.textContent = "Sending the summary to the site…";
      const gz = await new Response(new Blob([JSON.stringify(S)]).stream().pipeThrough(new CompressionStream("gzip"))).arrayBuffer();
      const f = await api(`${base}?file=${encodeURIComponent(file.name.replace(/\.trsap$/i, ""))}`, { method: "POST", body: gz, headers: { "Content-Type": "application/gzip" } });
      status.textContent = `Read ${n0(S.rows)} rows in ${Math.round((performance.now() - began) / 1000)} s${S.model ? `; the model file gave ${n0(S.model.loads)} loads in ${n0(Object.keys(S.loads).length)} load cases and the sections of ${n0(S.model.members)} of ${n0(S.model.of_members)} members` : ""}; kept ${(gz.byteLength / 1048576).toFixed(1)} MB on the site (Download small file keeps a copy).${Object.keys(S.tables || {}).length ? "" : " No tables Triton uses were found."}`;
      await list();
      open(f.id, S);
    } catch (err) {
      console.error(err);
      status.textContent = `Could not read the file: ${err.message}`;
    } finally {
      bar.hidden = true;
    }
  };
  await list();
  if (h.state.film && films.some((f) => f.id === h.state.film)) open(h.state.film);

  async function open(id, given) {
    h.state.film = id;
    const film = films.find((f) => f.id === id);
    const box = $("#film-open");
    box.innerHTML = `<p class="status">Loading the film…</p>`;
    const S = given || (await fetch(`${base}/${id}/summary`).then((r) => r.json()));
    const set = film.settings;
    box.innerHTML = `<div class="v3d-layout film-layout"><div><div class="panel">
        <div class="film-stage-wrap"><canvas class="film-stage"></canvas></div>
        <div class="row seq-bar"><button data-play>Play</button><input type="range" data-t min="0" max="1000" value="0" style="flex:1" aria-label="Time">
          <span class="status" data-clock>0:00</span><button class="quiet" data-video>Download video (MP4)</button></div>
        <p class="status" data-vstat></p>
        <div data-shots class="film-shots"></div></div>
        <div class="panel" style="margin-top:10px"><h3 style="margin-top:0">What the file held</h3><div data-held></div></div></div>
      <div class="panel v3d-side film-side"><h3 style="margin-top:0">The film</h3><div data-form></div>
        <h3>The site on the map</h3><p class="status">Drag the red pin to where the model's origin (0, 0) is, and set the direction of its X axis. Draw the breakwater and the land around it, or find them from OpenStreetMap.</p>
        <div class="row" style="gap:6px;flex-wrap:wrap"><button class="quiet" data-draw="rock">Draw a breakwater</button><button class="quiet" data-draw="rev">Draw a revetment</button><button class="quiet" data-draw="land">Draw land</button>
          <button class="quiet" data-osm>Find breakwaters and coast nearby</button><button class="quiet" data-trito>Take the site from a TritoMoor file</button><button class="quiet" data-clear>Clear drawn</button></div>
        <div class="film-map" data-map></div><p class="status" data-mapstat></p><div data-rocks></div>
        <div class="row" style="gap:8px;align-items:center;margin-top:12px"><button class="quiet" data-default title="Every film added to this project from now on starts with these settings: title, site, map, breakwaters, land, pile capacities">Use these settings for new films</button><span class="status" data-defstat></span></div></div></div>`;
    const q = (s2) => box.querySelector(s2);
    q("[data-default]").onclick = async () => {
      await api(`${base}/defaults`, { method: "PUT", body: JSON.stringify(set) });
      q("[data-defstat]").textContent = "Saved: new films in this project start with these settings.";
    };
    const stage = q(".film-stage");
    const player = new Player(stage, S, set);
    let t = 0, playing = false, last = 0, making = false;
    const clock = (s2) => `${Math.floor(s2 / 60)}:${String(Math.floor(s2 % 60)).padStart(2, "0")}`;
    const show = () => { player.frame(Math.min(t, player.total - 0.001)); q("[data-t]").value = Math.round((t / player.total) * 1000); q("[data-clock]").textContent = `${clock(t)} of ${clock(player.total)}`; markShot(); };
    const markShot = () => q("[data-shots]").querySelectorAll("[data-at]").forEach((b) => b.classList.toggle("on", t >= +b.dataset.at && t < +b.dataset.at + +b.dataset.dur));
    const shotList = () => {
      q("[data-shots]").innerHTML = player.shots.map((s2) => `<button class="quiet" data-at="${s2.t0}" data-dur="${s2.dur}">${esc(s2.kind === "map" ? "Map" : s2.kind === "intro" ? "Opening" : s2.kind === "end" ? "Close" : s2.kind === "summary" ? "Summary" : s2.kind === "combos" ? "Combinations" : s2.head || s2.kind)}</button>`).join("");
      q("[data-shots]").querySelectorAll("[data-at]").forEach((b) => (b.onclick = () => { t = +b.dataset.at + 0.01; show(); }));
    };
    shotList();
    const loop = (now) => {
      if (!playing) return;
      if (last) t += Math.min(0.25, (now - last) / 1000);
      last = now;
      if (t >= player.total) { t = player.total; playing = false; q("[data-play]").textContent = "Play"; }
      show();
      if (playing) requestAnimationFrame(loop);
    };
    q("[data-play]").onclick = () => {
      if (making) return;
      playing = !playing; last = 0;
      if (playing && t >= player.total - 0.05) t = 0;
      q("[data-play]").textContent = playing ? "Pause" : "Play";
      if (playing) requestAnimationFrame(loop);
    };
    q("[data-t]").oninput = (e) => { t = (+e.target.value / 1000) * player.total; show(); };
    q("[data-video]").onclick = async () => {
      if (making) { making = false; return; }
      playing = false; q("[data-play]").textContent = "Play";
      making = true; q("[data-video]").textContent = "Stop";
      const vs = q("[data-vstat]"), began = performance.now();
      try {
        const out = await makeVideo(player, (f) => {
          const el = (performance.now() - began) / 1000, left = f > 0.02 ? (el / f) * (1 - f) : null;
          vs.textContent = `Making the video: ${Math.floor(f * 100)}%${left != null ? `, about ${left < 90 ? Math.max(1, Math.round(left)) + " s" : Math.round(left / 60) + " min"} left` : ""}`;
          q("[data-clock]").textContent = `${clock(f * player.total)} of ${clock(player.total)}`;
        }, () => !making);
        if (out) {
          const a = document.createElement("a");
          a.href = URL.createObjectURL(out.blob);
          a.download = `${(set.name || "results-film").replace(/[^\w.-]+/g, "_")}.${out.ext}`;
          document.body.append(a); a.click(); a.remove();
          setTimeout(() => URL.revokeObjectURL(a.href), 60000);
          vs.textContent = `Saved to your downloads as ${a.download} (${clock(player.total)} long, ${player.w} x ${player.h}).`;
        } else vs.textContent = "Stopped: nothing was saved.";
      } catch (err) {
        console.error(err);
        vs.textContent = `The video stopped: ${err.message}`;
      } finally {
        making = false; q("[data-video]").textContent = "Download video (MP4)";
        show();
      }
    };
    held(q("[data-held]"), S, esc);
    // settings: saved as they change, the film laid out again
    const saveSoon = (() => { let tm; return () => { clearTimeout(tm); tm = setTimeout(async () => { film.settings = await api(`${base}/${id}`, { method: "PUT", body: JSON.stringify(set) }).then((f) => f.settings); }, 600); }; })();
    const redo = () => { player.load(S, set); shotList(); t = Math.min(t, player.total - 0.01); show(); saveSoon(); };
    form(q("[data-form]"), set, esc, redo);
    siteMap(q, set, esc, redo);
    show();
  }
}

function held(el, S, esc) {
  const n = (k) => Object.entries(S.tables).filter(([t]) => t.toUpperCase().includes(k)).reduce((a, [, v]) => a + v, 0);
  const rows = [
    ["Joints", S.joints.filter(Boolean).length], ["Members", S.frames.ids.length], ["Plates", S.areas.ids.length], ["Supports", S.supports.length],
    ["Load patterns", S.patterns.length || Object.keys(S.loads).length], ["Combinations", S.combos.length], ["Output cases", S.cases.length],
    ["Reaction rows", n("REACTIONS")], ["Displacement rows", n("DISPLACEMENTS")], ["Member force rows", n("ELEMENT FORCES - FRAMES")],
    ["Members with a design ratio", Object.keys(S.design).length],
  ];
  const missing = [];
  if (!S.reactions.cases?.length) missing.push("joint reactions");
  if (!S.displacements.cases?.length) missing.push("joint displacements");
  if (!S.forces.M) missing.push("element forces");
  if (!Object.keys(S.design).length) missing.push("design summary (utilisation ratios)");
  el.innerHTML = `<table class="grid compact"><tbody>${rows.map(([a, b]) => `<tr><td>${a}</td><td><b>${n0(b)}</b></td></tr>`).join("")}</tbody></table>
    ${missing.length ? `<p class="status"><span class="flag-warn">not in the file</span> ${esc(missing.join(", "))}: those shots are left out. Export those tables too to have them.</p>` : ""}
    ${Object.keys(S.ignored || {}).length ? `<details><summary class="status">Tables not used (${Object.keys(S.ignored).length})</summary><p class="status">${esc(Object.keys(S.ignored).join(", "))}</p></details>` : ""}`;
}

function form(el, set, esc, changed) {
  const num = (v) => (v === "" || v == null ? null : Number(v));
  el.innerHTML = `<label>Title <input data-k="title" value="${esc(set.title || "")}"></label>
    <label>Client <input data-k="client" value="${esc(set.client || "")}"></label>
    <div class="row" style="gap:8px"><label>Water level (m) <input data-k="water_level" type="number" step="0.01" value="${esc(set.water_level ?? 0)}"></label>
      <label>Seabed (m) <input data-k="seabed" type="number" step="0.01" value="${esc(set.seabed ?? "")}"></label></div>
    <div class="row" style="gap:8px"><label>Length <select data-k="mode"><option value="detailed">Detailed</option><option value="brief">Brief</option></select></label>
      <label>Size <select data-k="size"><option value="1280x720">1280 x 720</option><option value="1920x1080">1920 x 1080</option></select></label></div>
    <label>Steel yield strength fy (MPa), for stress ratios <input data-k="steel_fy" type="number" min="100" step="5" value="${esc(set.steel_fy ?? 355)}"></label>
    <label>Movements drawn larger by <input data-k="exaggerate" type="number" min="1" step="1" placeholder="chosen to show" value="${esc(set.exaggerate ?? "")}"></label>
    <fieldset class="film-piles"><legend>Pile capacities</legend>
      <p class="status">Piles over their capacity glow in the film. Match picks piles by frame or section name, or by row (Row A, Row B, lettered from the low side); commas between, * for any text; empty for every other pile. Tension left empty is the factor times compression. Checked under names the combinations (e.g. C1-* for D+L service); empty for every combination. Lateral under, when given, is used for the lateral check instead.</p>
      <label>Row names, from the low side <input data-k="pile_rows" placeholder="A, B, C…" value="${esc(set.pile_rows || "")}" style="width:9em"></label>
      <label>Capacities in <select data-k="capacity_unit"><option value="t">tonnes</option><option value="kN">kN</option></select></label>
      <div data-pgroups></div><button class="quiet" type="button" data-padd>Add a pile group</button></fieldset>
    <fieldset class="film-hide"><legend>Shots</legend>${Object.entries(SHOTS).map(([k, v]) => `<label class="toggle"><input type="checkbox" data-shot="${k}" ${(set.hide || []).includes(k) ? "" : "checked"}> ${v}</label>`).join("")}</fieldset>`;
  el.querySelector('[data-k="mode"]').value = set.mode || "detailed";
  el.querySelector('[data-k="size"]').value = set.size || "1280x720";
  el.querySelector('[data-k="capacity_unit"]').value = set.capacity_unit || "t";
  const groups = el.querySelector("[data-pgroups]");
  const PF = [["name", "Name", "text", "8em"], ["match", "Match", "text", "8em"], ["compression", "Compression", "number", "5.5em"],
    ["tension", "Tension", "number", "5.5em"], ["tension_factor", "or factor", "number", "4em"], ["lateral", "Lateral", "number", "5.5em"], ["cases", "Checked under", "text", "9em"], ["lateral_cases", "Lateral under", "text", "9em"]];
  const drawGroups = () => {
    groups.innerHTML = (set.piles || []).map((g, i) => `<div class="row film-pgroup" style="gap:6px;flex-wrap:wrap;align-items:end;border-top:1px solid var(--line, #ddd);padding-top:6px;margin-top:6px">${PF.map(([k, lab, t, w]) =>
      `<label>${lab} <input data-pg="${i}" data-pf="${k}" type="${t}" ${t === "number" ? 'step="any" min="0"' : ""} style="width:${w}" value="${esc(g[k] ?? (k === "tension_factor" ? 0.7 : ""))}"></label>`).join("")}
      <button class="quiet" type="button" data-prm="${i}" title="Remove">✕</button></div>`).join("") || '<p class="status">No groups: existing and new piles are told apart by their section names, with no capacity check.</p>';
    groups.querySelectorAll("[data-pf]").forEach((i) => (i.onchange = () => {
      const g = set.piles[+i.dataset.pg], k = i.dataset.pf;
      g[k] = i.type === "number" ? num(i.value) : i.value;
      if (k === "tension_factor" && g[k] == null) g[k] = 0.7;
      changed();
    }));
    groups.querySelectorAll("[data-prm]").forEach((b) => (b.onclick = () => { set.piles.splice(+b.dataset.prm, 1); drawGroups(); changed(); }));
  };
  drawGroups();
  el.querySelector("[data-padd]").onclick = () => {
    (set.piles ||= []).push({ name: `Piles ${set.piles.length + 1}`, match: "", compression: null, tension: null, tension_factor: 0.7, lateral: null, cases: "", lateral_cases: "" });
    drawGroups(); changed();
  };
  el.querySelectorAll("[data-k]").forEach((i) => (i.onchange = () => {
    const k = i.dataset.k;
    set[k] = ["water_level", "seabed", "exaggerate", "steel_fy"].includes(k) ? num(i.value) : i.value;
    if (k === "water_level" && set[k] == null) set[k] = 0;
    changed();
  }));
  el.querySelectorAll("[data-shot]").forEach((i) => (i.onchange = () => {
    const hide = new Set(set.hide || []);
    if (i.checked) hide.delete(i.dataset.shot); else hide.add(i.dataset.shot);
    set.hide = [...hide];
    changed();
  }));
}

// the map beside the film: the model's origin, its X axis, and the breakwaters and land drawn on it
async function siteMap(q, set, esc, changed) {
  const el = q("[data-map]"), stat = q("[data-mapstat]");
  const centre = set.lat != null ? [set.lat, set.lon] : [26.8, 30.8];
  const map = await makeMap(el, { center: centre, zoom: set.lat != null ? 15 : 4 });
  if (!map) return;
  const L = await loadLeaflet();
  searchBox(map, ([lat, lon]) => { set.lat = lat; set.lon = lon; pin.setLatLng([lat, lon]); redraw(); changed(); });
  const pin = L.marker(centre, { draggable: true, title: "The model's origin (0, 0)" }).addTo(map);
  pin.on("dragend", () => { const p = pin.getLatLng(); set.lat = p.lat; set.lon = p.lng; redraw(); changed(); });
  if (set.lat == null) stat.textContent = "Put the pin where the model is: search for the place, or drag the pin.";
  const group = L.layerGroup().addTo(map);
  const bearingRow = document.createElement("div");
  bearingRow.className = "row";
  bearingRow.innerHTML = `<label>Model X axis points <input type="number" step="1" data-bearing value="${esc(set.bearing || 0)}" style="width:6em"> degrees from north (clockwise)</label>`;
  el.after(bearingRow);
  bearingRow.querySelector("[data-bearing]").onchange = (e) => { set.bearing = Number(e.target.value) || 0; redraw(); changed(); };
  function redraw() {
    group.clearLayers();
    const back = toMap(set);
    if (back) {
      const a = back([0, 0]), b = back([40, 0]);
      L.polyline([a, b], { color: "#E3543C", weight: 3 }).addTo(group).bindTooltip("Model X axis");
    }
    (set.breakwaters || []).forEach((r, i) => {
      const pts = r.pts?.length ? r.pts : back ? (r.xy || []).map(back) : [];
      if (pts.length) L.polyline(pts, { color: r.kind === "revetment" ? "#A08C5B" : "#6A655A", dashArray: r.kind === "revetment" ? "8 5" : null, weight: 6, opacity: 0.85 }).addTo(group).bindTooltip(r.name || `Breakwater ${i + 1}`);
    });
    (set.land || []).forEach((l) => {
      const pts = l.pts?.length ? l.pts : back ? (l.xy || []).map(back) : [];
      if (pts.length) L.polygon(pts, { color: "#8B8679", weight: 1, fillColor: "#B9B2A0", fillOpacity: 0.5 }).addTo(group);
    });
    const rocks = q("[data-rocks]");
    rocks.innerHTML = (set.breakwaters || []).map((r, i) => `<div class="row film-rock"><b>${esc(r.name || `Breakwater ${i + 1}`)}</b>
      <label>crest <input type="number" step="0.1" data-crest="${i}" value="${esc(r.crest ?? 5)}" style="width:5em"> m</label>
      <label>width <input type="number" step="0.5" data-width="${i}" value="${esc(r.width ?? 12)}" style="width:5em"> m</label>
      <button class="quiet" data-rm="${i}" title="Remove">✕</button></div>`).join("");
    rocks.querySelectorAll("[data-crest]").forEach((i) => (i.onchange = () => { set.breakwaters[+i.dataset.crest].crest = Number(i.value); changed(); }));
    rocks.querySelectorAll("[data-width]").forEach((i) => (i.onchange = () => { set.breakwaters[+i.dataset.width].width = Number(i.value); changed(); }));
    rocks.querySelectorAll("[data-rm]").forEach((b) => (b.onclick = () => { set.breakwaters.splice(+b.dataset.rm, 1); redraw(); changed(); }));
  }
  redraw();
  // drawing: click points, double-click (or the button again) to finish
  let drawing = null, temp = null;
  const finish = () => {
    if (!drawing) return;
    const pts = drawing.pts;
    if (drawing.kind === "rock" && pts.length >= 2) (set.breakwaters ||= []).push({ name: `Breakwater ${(set.breakwaters || []).length + 1}`, pts, crest: 5, width: 12 });
    if (drawing.kind === "rev" && pts.length >= 2) (set.breakwaters ||= []).push({ name: `Revetment ${(set.breakwaters || []).filter((r) => r.kind === "revetment").length + 1}`, kind: "revetment", pts, crest: 3, width: 10 });
    if (drawing.kind === "land" && pts.length >= 3) (set.land ||= []).push({ pts, level: null });
    drawing = null; temp?.remove(); temp = null;
    map.doubleClickZoom.enable();
    q("[data-draw='rock']").textContent = "Draw a breakwater"; q("[data-draw='rev']").textContent = "Draw a revetment"; q("[data-draw='land']").textContent = "Draw land";
    stat.textContent = "";
    redraw(); changed();
  };
  q("[data-draw='rock']").onclick = () => { if (drawing) return finish(); start("rock"); };
  q("[data-draw='land']").onclick = () => { if (drawing) return finish(); start("land"); };
  q("[data-draw='rev']").onclick = () => { if (drawing) return finish(); start("rev"); };
  function start(kind) {
    drawing = { kind, pts: [] };
    map.doubleClickZoom.disable();
    q(`[data-draw='${kind}']`).textContent = "Finish";
    stat.textContent = kind === "rev" ? "Click along the top of the revetment slope; double-click or Finish to end it. It shows see-through in the results shots." : kind === "rock" ? "Click along the breakwater's crest; double-click or Finish to end it." : "Click round the land; double-click or Finish to close it.";
  }
  map.on("click", (e) => {
    if (!drawing) return;
    drawing.pts.push([+e.latlng.lat.toFixed(7), +e.latlng.lng.toFixed(7)]);
    temp?.remove();
    temp = (drawing.kind !== "land" ? L.polyline : L.polygon)(drawing.pts, { color: "#E3543C", dashArray: "4 4" }).addTo(map);
  });
  map.on("dblclick", finish);
  q("[data-clear]").onclick = async () => { if (!(await confirmBox("Remove every breakwater and land area drawn for this film?", { ok: "Remove" }))) return; set.breakwaters = []; set.land = []; redraw(); changed(); };
  // the site as settled in a TritoMoor project: its berth position, north and the traced land and breakwaters
  q("[data-trito]").onclick = () => {
    const box = document.createElement("div");
    box.className = "row film-trito";
    box.style.cssText = "gap:8px;flex-wrap:wrap;align-items:end;margin:6px 0";
    box.innerHTML = `<label>TritoMoor file <input type="file" accept=".trito,.json" data-tfile></label>
      <label>Model origin at TritoMoor X <input type="number" step="0.1" data-tx0 value="${esc(set.tritomoor?.x0 ?? 0)}" style="width:6em"> m</label>
      <label>The model's sea side <select data-tsea><option value="-Y">towards -Y (front row at low Y)</option><option value="+Y">towards +Y</option></select></label>
      <button class="quiet" data-tgo>Use it</button>`;
    q("[data-trito]").after(box);
    box.querySelector("[data-tsea]").value = set.tritomoor?.sea || "-Y";
    box.querySelector("[data-tgo]").onclick = async () => {
      const f = box.querySelector("[data-tfile]").files[0];
      if (!f) { stat.textContent = "Choose the TritoMoor project file (.trito) first."; return; }
      try {
        const tm = JSON.parse(await f.text());
        const opt = { x0: Number(box.querySelector("[data-tx0]").value) || 0, sea: box.querySelector("[data-tsea]").value };
        const got = fromTrito(tm, opt);
        Object.assign(set, { lat: got.lat, lon: got.lon, bearing: got.bearing, tritomoor: { ...opt, name: got.name } });
        set.breakwaters = [...(set.breakwaters || []).filter((r) => !r.fromTrito), ...got.rocks];
        set.land = [...(set.land || []).filter((l) => !l.fromTrito), ...got.land];
        pin.setLatLng([set.lat, set.lon]); map.setView([set.lat, set.lon], 15);
        bearingRow.querySelector("[data-bearing]").value = n0(set.bearing, 1);
        stat.textContent = `${got.name || "The TritoMoor site"}: ${got.rocks.length} breakwater${got.rocks.length === 1 ? "" : "s"} and ${got.land.length} land outline${got.land.length === 1 ? "" : "s"}, model X axis at ${n0(set.bearing, 1)} degrees from north.`;
        box.remove(); redraw(); changed();
      } catch (e) { stat.textContent = `That file could not be read: ${e.message}`; }
    };
  };
  q("[data-osm]").onclick = async () => {
    if (set.lat == null) { stat.textContent = "Put the pin on the site first."; return; }
    stat.textContent = "Asking OpenStreetMap for breakwaters and the coast within 3 km…";
    try {
      const found = await fromOsm(set.lat, set.lon, 3000);
      set.breakwaters = [...(set.breakwaters || []), ...found.rocks];
      set.land = [...(set.land || []), ...found.land];
      stat.textContent = `Found ${found.rocks.length} breakwater${found.rocks.length === 1 ? "" : "s"} and ${found.coast} stretch${found.coast === 1 ? "" : "es"} of coast. Check them on the map: remove any that are not right.`;
      redraw(); changed();
    } catch (e) {
      stat.textContent = `OpenStreetMap did not answer (${e.message}). Draw them by hand instead.`;
    }
  };
}

// A TritoMoor project's site in the model's axes. TritoMoor's berth axes: X along the berth, Y from the berthing face out
// to sea, the berth's lat/lon at its origin and site.north the direction of north from +X (degrees, anticlockwise).
// x0: where the model's origin sits along TritoMoor's X; sea "-Y" when the model's front row is at its low Y (turned round).
export function fromTrito(tm, { x0 = 0, sea = "-Y" } = {}) {
  const b = tm.berth || {}, site = tm.site || {};
  if (b.lat == null || b.lon == null) throw new Error("it has no berth position (berth lat and lon)");
  const north = Number(site.north) || 0, flip = sea !== "+Y";
  const xBearing = (((flip ? north + 180 : north) % 360) + 360) % 360;
  const toM = ([x, y]) => (flip ? [x0 - x, -y] : [x - x0, y]).map((v) => Math.round(v * 100) / 100);
  // the model's origin is x0 along TritoMoor's +X, whose bearing is site.north
  const r = (north * Math.PI) / 180, dN = x0 * Math.cos(r), dE = x0 * Math.sin(r);
  const lat = b.lat + dN / 110540, lon = b.lon + dE / (111320 * Math.cos((b.lat * Math.PI) / 180));
  const rocks = (site.rock || []).filter((k) => (k.pts || []).length >= 2).map((k) => ({ name: k.name || "Breakwater", pts: [], xy: k.pts.map(toM), crest: Number(k.top) || 5, width: Number(k.w) || 12, fromTrito: true }));
  const land = (site.land || []).filter((l) => l.length >= 3).map((l) => ({ pts: [], xy: l.map(toM), level: null, fromTrito: true }));
  return { name: site.name || tm.name || "", lat: +lat.toFixed(7), lon: +lon.toFixed(7), bearing: Math.round(xBearing * 10) / 10, rocks, land };
}

// breakwaters, groynes and moles, and the coastline as land strips (land is on the left of a coastline way)
export async function fromOsm(lat, lon, r) {
  const query = `[out:json][timeout:25];(way["man_made"~"breakwater|groyne"](around:${r},${lat},${lon});way["natural"="coastline"](around:${r},${lat},${lon}););out geom;`;
  const res = await fetch("https://overpass-api.de/api/interpreter", { method: "POST", body: "data=" + encodeURIComponent(query), headers: { "Content-Type": "application/x-www-form-urlencoded" } });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  const data = await res.json();
  const rocks = [], land = [];
  let coast = 0;
  const ky = 110540, kx = 111320 * Math.cos(lat * Math.PI / 180);
  for (const w of data.elements || []) {
    const pts = (w.geometry || []).map((g) => [g.lat, g.lon]);
    if (pts.length < 2) continue;
    if (w.tags?.natural === "coastline") {
      // a strip 250 m deep on the land side of each stretch, flat at the top of the model
      coast++;
      const off = pts.map((p, i) => {
        const a = pts[Math.max(0, i - 1)], b = pts[Math.min(pts.length - 1, i + 1)];
        const dx = (b[1] - a[1]) * kx, dy = (b[0] - a[0]) * ky, l = Math.hypot(dx, dy) || 1;
        return [p[0] + (dx / l) * 250 / ky, p[1] - (dy / l) * 250 / kx];
      });
      land.push({ pts: [...pts, ...off.reverse()], level: null, open: true });
    } else rocks.push({ name: w.tags?.name || (w.tags?.man_made === "groyne" ? "Groyne" : "Breakwater"), pts, crest: 5, width: 12 });
  }
  return { rocks, land, coast };
}
