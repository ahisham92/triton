// The construction sequence's plant and people, drawn round the work of a stage: a crawler crane with a
// vibro hammer for the steel pipes and sheet piles, a crane lifting the cages, a piling rig boring the
// piles, a concrete pump and truck mixers for every pour, an excavator with a breaker for the demolition
// and the pile heads, a mobile crane for the furniture and a cutter suction dredger. Sizes are typical
// ones, for show only (m). Everything is boxes and lines on the berth frame: s along the berth from its
// start, d inland from the quay face.

const C = {
  track: "#27272a", crane: "#dc2626", rig: "#f59e0b", body: "#fbbf24", cab: "#334155", steel: "#52525b",
  cage: "#b45309", pump: "#facc15", truck: "#e5e7eb", drum: "#d4d4d8", exc: "#f59e0b", hull: "#b91c1c",
  deck: "#f4f4f5", vest: "#f97316", helmet: "#ffffff", legs: "#1e3a8a", concrete: "#9ca3af",
};

export function frameOf(site) {
  const f = site.frame;
  const at = (s, d, z) => {
    const p = { [f.along]: f.start + s, [f.across]: f.face + f.inland * d };
    return [p.X, p.Y, z];
  };
  const sd = (x, y) => {
    const p = { X: x, Y: y };
    return [p[f.along] - f.start, (p[f.across] - f.face) * f.inland];
  };
  return { at, sd, along: f.along, across: f.across };
}

// A kit of solids and lines, added to with (s, d, z) values.
function kit(F) {
  const solids = [];
  const lines = [];
  const box = (s0, s1, d0, d1, z0, z1, color, tip) => {
    const a = F.at(s0, d0, z0);
    const b = F.at(s1, d1, z1);
    solids.push({ box: { X: [a[0], b[0]].sort((p, q) => p - q), Y: [a[1], b[1]].sort((p, q) => p - q), Z: [z0, z1] }, color, tip });
  };
  const line = (p, q, color, width, size, tip) =>
    lines.push({ a: F.at(...p), b: F.at(...q), color, width, size: size || null, cap: "butt", tip });
  return { solids, lines, box, line };
}

function tracks(K, s, d, z, len, tip) {
  K.box(s - 2.4, s - 1.5, d - len / 2, d + len / 2, z, z + 1.1, C.track, tip);
  K.box(s + 1.5, s + 2.4, d - len / 2, d + len / 2, z, z + 1.1, C.track, tip);
}

// A crawler crane standing at (s, d) and its hook over (hs, hd) at hz; what hangs is up to the caller.
function crawlerCrane(K, s, d, z, hs, hd, hz, tip) {
  tracks(K, s, d, z, 7.5, tip);
  K.box(s - 2.2, s + 2.2, d - 2.4, d + 2.8, z + 1.1, z + 2.9, C.crane, tip);
  K.box(s - 2.0, s + 2.0, d + 2.8, d + 4.0, z + 1.1, z + 3.4, C.steel, `${tip}: counterweight`);
  K.box(s + 0.9, s + 2.1, d - 2.4, d - 0.8, z + 2.9, z + 4.6, C.cab, tip);
  const top = Math.max(hz + 9, z + 22);
  for (const o of [-0.7, 0.7]) K.line([s + o, d - 1.5, z + 2.9], [hs + o * 0.3, hd, top], C.crane, 2.5, null, `${tip}: lattice boom`);
  K.line([s, d + 2.8, z + 3.4], [hs, hd, top], C.steel, 1, null, `${tip}: luffing ropes`);
  K.line([hs, hd, top], [hs, hd, hz], C.track, 1, null, `${tip}: hoist rope`);
}

function pilingRig(K, s, d, z, ps, pd, top, depth, tip) {
  tracks(K, s, d, z, 6.5, tip);
  K.box(s - 2.0, s + 2.0, d - 2.0, d + 2.6, z + 1.1, z + 3.0, C.rig, tip);
  K.box(s - 1.8, s + 1.8, d + 2.6, d + 3.6, z + 1.1, z + 3.2, C.steel, `${tip}: counterweight`);
  K.box(s + 0.8, s + 2.0, d - 2.0, d - 0.6, z + 3.0, z + 4.6, C.cab, tip);
  const mastTop = z + 26;
  K.line([ps, pd - 0.9, z + 0.5], [ps, pd - 0.9, mastTop], C.rig, 5, 0.7, `${tip}: mast`);
  K.line([s, d - 2.0, z + 3.0], [ps, pd - 0.9, z + 12], C.rig, 3, null, `${tip}: mast arm`);
  K.line([ps, pd, mastTop], [ps, pd, depth], C.steel, 2.5, 0.35, `${tip}: kelly bar`);
  K.box(ps - 0.6, ps + 0.6, pd - 0.6, pd + 0.6, depth - 1.8, depth, C.steel, `${tip}: auger`);
  if (top != null) K.box(ps - 0.8, ps + 0.8, pd - 0.8, pd + 0.8, z, z + 0.3, C.steel, `${tip}: temporary casing collar`);
}

function pumpTruck(K, s, d, z, ts, td, tz, tip) {
  K.box(s - 5.5, s + 4.0, d - 1.25, d + 1.25, z + 0.4, z + 1.6, C.steel, tip);
  K.box(s + 4.0, s + 6.0, d - 1.25, d + 1.25, z + 0.4, z + 3.2, C.pump, `${tip}: cab`);
  K.box(s - 5.0, s + 3.5, d - 1.1, d + 1.1, z + 1.6, z + 2.6, C.pump, tip);
  for (const o of [-3.5, 0.5]) K.box(s + o - 0.3, s + o + 0.3, d - 3.2, d + 3.2, z, z + 0.6, C.steel, `${tip}: outrigger`);
  const turret = [s + 2.5, d, z + 3.4];
  const reach = Math.hypot(ts - s, td - d);
  const peak = z + Math.min(26, 10 + reach * 0.5);
  const p1 = [s + (ts - s) * 0.35, d + (td - d) * 0.35, peak];
  const p2 = [s + (ts - s) * 0.75, d + (td - d) * 0.75, peak - 2];
  const p3 = [ts, td, tz + 4];
  for (const [a, b] of [[turret, p1], [p1, p2], [p2, p3]]) K.line(a, b, C.pump, 3.5, 0.35, `${tip}: placing boom`);
  K.line(p3, [ts, td, tz + 0.6], C.track, 2, 0.15, `${tip}: end hose`);
}

function mixer(K, s, d, z, tip) {
  K.box(s - 4.0, s + 3.0, d - 1.2, d + 1.2, z + 0.4, z + 1.4, C.steel, tip);
  K.box(s + 3.0, s + 4.8, d - 1.2, d + 1.2, z + 0.4, z + 3.0, C.truck, `${tip}: cab`);
  K.box(s - 3.8, s + 2.4, d - 1.05, d + 1.05, z + 1.4, z + 3.4, C.drum, `${tip}: drum`);
  K.box(s - 3.9, s - 3.2, d - 0.6, d + 0.6, z + 2.2, z + 3.0, C.cage, `${tip}: chute`);
}

function excavator(K, s, d, z, ts, td, tz, tip) {
  tracks(K, s, d, z, 4.6, tip);
  K.box(s - 1.6, s + 1.6, d - 1.6, d + 2.2, z + 1.1, z + 2.4, C.exc, tip);
  K.box(s + 0.2, s + 1.5, d - 1.6, d - 0.3, z + 2.4, z + 3.9, C.cab, tip);
  const elbow = [s + (ts - s) * 0.5, d + (td - d) * 0.5, Math.max(z, tz) + 4.5];
  K.line([s - 0.4, d - 1.4, z + 2.2], elbow, C.exc, 4, 0.5, `${tip}: boom`);
  K.line(elbow, [ts, td, tz + 1.4], C.exc, 3.5, 0.4, `${tip}: stick`);
  K.line([ts, td, tz + 1.4], [ts, td, tz], C.steel, 3, 0.25, `${tip}: hydraulic breaker`);
}

// Bow towards the quay at d = bow, the hull running out to sea; the cutter working at the wall's toe.
function dredger(K, s, bow, sea, bed, tip) {
  const stern = bow - 34;
  K.box(s - 5.5, s + 5.5, stern, bow, sea - 2.2, sea + 1.3, C.hull, tip);
  K.box(s - 4.5, s + 4.5, stern, stern + 9, sea + 1.3, sea + 7.5, C.deck, `${tip}: accommodation and bridge`);
  K.box(s - 2.5, s + 2.5, stern + 2, stern + 6, sea + 7.5, sea + 9.5, C.cab, `${tip}: wheelhouse`);
  K.line([s, bow - 2, sea + 1.2], [s, bow + 11, bed + 1.2], C.steel, 4, 1.1, `${tip}: cutter ladder`);
  K.box(s - 1.4, s + 1.4, bow + 10, bow + 13, bed, bed + 2.4, C.rig, `${tip}: cutter head`);
  K.line([s - 3, stern + 1, bed], [s - 3, stern + 1, sea + 12], C.steel, 3, 0.6, `${tip}: spud`);
  K.line([s, stern, sea + 0.8], [s + 12, stern - 30, sea + 0.3], C.track, 2, 0.8, `${tip}: floating discharge pipe`);
}

function worker(K, s, d, z, n) {
  const tip = "Worker";
  K.line([s - 0.12, d, z], [s - 0.12, d, z + 0.85], C.legs, 2, 0.16, tip);
  K.line([s + 0.12, d, z], [s + 0.12, d, z + 0.85], C.legs, 2, 0.16, tip);
  K.line([s, d, z + 0.85], [s, d, z + 1.5], n % 3 ? C.vest : "#facc15", 4, 0.46, `${tip}, hi-vis vest`);
  K.box(s - 0.14, s + 0.14, d - 0.14, d + 0.14, z + 1.52, z + 1.78, C.helmet, `${tip}, helmet`);
}

function crew(K, s, d, z, count, seed) {
  const spots = [[1.8, 1.2], [-2.2, 0.6], [0.8, -2.4], [-1.2, 2.6], [3.0, -0.8], [-3.2, -1.8]];
  for (let i = 0; i < count; i++) {
    const [ds, dd] = spots[(i + seed) % spots.length];
    worker(K, s + ds, d + dd, z, i + seed);
  }
}

export const LABEL = {
  steel_pipes: "Crawler crane with vibro hammer",
  sheet_piles: "Crawler crane with vibro hammer",
  combi_cages: "Crawler crane lifting the cage",
  combi_infill: "Concrete pump and truck mixer",
  pile_cages: "Piling rig boring the pile",
  pile_concrete: "Concrete by tremie, pump and truck mixer",
  pile_heads: "Excavator with a hydraulic breaker",
  demolition: "Excavator with a hydraulic breaker",
  front_beam: "Concrete pump and truck mixers",
  rear_beam: "Concrete pump and truck mixers",
  transverse_beam: "Concrete pump and truck mixers",
  slab: "Concrete pump and truck mixers",
  approach_slab: "Concrete pump and truck mixers",
  furniture: "Mobile crane placing the fenders",
  dredging: "Cutter suction dredger",
};

// The plant and crew of one work, at the spot being worked (s, d, top) or along the berth at t.
// ctx: { F, platform, water, seabed, before, after, extent: [s0, s1], face, rear, existing }
export function plant(work, spot, t, ctx) {
  const F = ctx.F;
  const K = kit(F);
  const tip = LABEL[work] || "Plant";
  const z = ctx.platform;
  const [s0, s1] = ctx.extent;
  const along = s0 + (s1 - s0) * t;
  if (work === "dredging") {
    dredger(K, along, -14, ctx.water, ctx.before + (ctx.after - ctx.before) * t, tip);
    return K;
  }
  if (work === "demolition") {
    const ex = ctx.existing;
    const d = ex ? ex.edge_d + 1.0 : 2;
    excavator(K, along + 4.5, d + 6, z, along + 0.3, d, (ex?.cope ?? z), tip);
    K.box(along + 9, along + 16, d + 7, d + 10, z, z + 3.0, C.cage, "Dump truck taking the broken concrete");
    crew(K, along + 5, d + 2.5, z, 2, 1);
    return K;
  }
  if (work === "furniture") {
    const d = 6;
    crawlerCrane(K, along, d + 2, z, along, -1.2, ctx.cope - 0.5, tip);
    K.box(along - 1.0, along + 1.0, -2.2, -0.2, ctx.cope - 2.5, ctx.cope - 0.5, C.track, "Fender being placed");
    crew(K, along + 1.5, 1.2, ctx.cope, 3, 2);
    return K;
  }
  if (!spot) return K;
  const { s, d, top, bottom } = spot;
  if (work === "steel_pipes" || work === "sheet_piles") {
    const hz = top + 3.0;
    crawlerCrane(K, s, d + 12, z, s, d, hz, tip);
    K.box(s - 0.9, s + 0.9, d - 0.9, d + 0.9, top, top + 3.0, C.steel, "Vibro hammer on the pile");
    crew(K, s, d + 5, z, 3, 0);
  } else if (work === "combi_cages" || (work === "pile_cages" && spot.phase === "cage")) {
    const hz = top + 12;
    crawlerCrane(K, s, d + 12, z, s, d, hz + 0.5, tip);
    const bottomCage = top - 12 * (spot.progress ?? 1);
    K.line([s, d, hz], [s, d, Math.max(bottomCage, top - 12)], C.cage, 2.5, spot.dia ? spot.dia * 0.8 : 1.0, "Reinforcement cage being lowered");
    crew(K, s, d + 3, z, 4, 3);
  } else if (work === "pile_cages") {
    pilingRig(K, s, d + 4.5, z, s, d, top, bottom + (top - bottom) * (1 - (spot.progress ?? 0.5)), tip);
    crew(K, s, d + 2.5, z, 3, 4);
    K.box(s + 6, s + 12, d + 7, d + 10, z, z + 1.6, "#78350f", "Spoil from the bore");
  } else if (work === "pile_concrete" || work === "combi_infill") {
    pumpTruck(K, s + 12, d + 14, z, s, d, top, tip);
    mixer(K, s + 12, d + 18, z, "Truck mixer");
    K.line([s, d, top + 6], [s, d, top], C.steel, 2, 0.3, "Tremie pipe");
    crew(K, s, d + 2.5, z, 3, 1);
  } else if (work === "pile_heads") {
    excavator(K, s + 3, d + 5, z, s, d, top + 0.8, tip);
    crew(K, s - 1.5, d + 1, z, 2, 5);
  } else {
    // Pours: a slab from the part already cast behind the pour front; beams from the ground beside them.
    const slab = work === "slab" || work === "approach_slab";
    const at = slab ? { s: s - 10, d, z: top } : { s: s - 8, d: (ctx.rear ?? d) + 5, z };
    pumpTruck(K, at.s, at.d, at.z, s, d, top, tip);
    mixer(K, at.s - 12, at.d, at.z, "Truck mixer");
    if (slab) mixer(K, at.s - 24, at.d + 3, at.z, "Truck mixer, waiting");
    crew(K, s + 1, d, top, 5, 2);
  }
  return K;
}
