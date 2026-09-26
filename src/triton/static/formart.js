// The long input forms (Design settings, each element) as pages of related fields, each page with a
// line saying what it is for and, where a picture helps, a small drawing that follows the values as
// they are typed. Presentation only: the fields and what they save are unchanged.

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const n = (v, d = 0) => (v == null || !isFinite(v) ? "–" : Number(v).toLocaleString("en-GB", { maximumFractionDigits: d, minimumFractionDigits: d }));
const C = { concrete: "#9aa3ab", concreteSoft: "#e3e6e9", steel: "#1e6fb0", bar: "#c0392b", sea: "#1e8fc0", soil: "#b08d57", dim: "#6b6b66", ok: "#2f7d4f" };
// Each drawing gets its own pattern and marker ids: one in a hidden page would otherwise hide them all.
let uid = 0;
const svg = (w, h, body, label) => {
  const k = ++uid;
  return `<svg class="page-art-svg" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(label)}">${body.replace(/pa-(arr|conc|soil)/g, `pa-$1-${k}`)}</svg>`;
};
const dim = (x1, y1, x2, y2, text, { side = -1, anchor = "middle" } = {}) => {
  const hor = Math.abs(y2 - y1) < Math.abs(x2 - x1);
  const tx = hor ? (x1 + x2) / 2 : x1 + side * 6, ty = hor ? (side > 0 ? y1 + 13 : y1 - 5) : (y1 + y2) / 2 + 4;
  return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${C.dim}" stroke-width="1" marker-start="url(#pa-arr)" marker-end="url(#pa-arr)"/>
    <text x="${tx}" y="${ty}" class="pa-dim" text-anchor="${hor ? "middle" : side < 0 ? "end" : "start"}">${esc(text)}</text>`;
};
const defs = `<defs><marker id="pa-arr" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="${C.dim}"/></marker>
  <pattern id="pa-conc" width="7" height="7" patternUnits="userSpaceOnUse"><rect width="7" height="7" fill="${C.concreteSoft}"/><circle cx="2" cy="2" r=".9" fill="${C.concrete}"/><circle cx="5.5" cy="5" r=".6" fill="${C.concrete}"/></pattern>
  <pattern id="pa-soil" width="8" height="8" patternUnits="userSpaceOnUse"><rect width="8" height="8" fill="#efe4d0"/><circle cx="2" cy="3" r=".9" fill="${C.soil}"/></pattern></defs>`;
const grade = (list, g) => (list || []).find((x) => x.grade === g);
const barsRing = (cx, cy, r, count, rb) => Array.from({ length: count }, (_, i) => {
  const a = (2 * Math.PI * i) / count;
  return `<circle cx="${(cx + r * Math.cos(a)).toFixed(1)}" cy="${(cy + r * Math.sin(a)).toFixed(1)}" r="${rb}" fill="${C.bar}"/>`;
}).join("");

// ---------------------------------------------------------------- Design settings
export const DESIGN_PAGES = [
  { title: "Codes and life", keys: ["code", "design_life_years"],
    intro: "The codes every check follows and the design life, which sets the corrosion allowances and the durability covers." },
  { title: "Project grades", keys: ["materials"],
    intro: "One grade per material for the whole project. An element can still use its own grade on the Elements tab.",
    art: (o, h) => {
      const m = o.materials || {};
      const cards = [
        ["Concrete", m.concrete, "piles, slabs, beams", C.concrete, grade(h.materials?.concrete, m.concrete)?.fck, "fck"],
        ["Infill concrete", m.infill_concrete, "combi wall infill", "#7d8790", grade(h.materials?.concrete, m.infill_concrete)?.fck, "fck"],
        ["Structural steel", m.structural_steel, "tubes and casings", C.steel, parseFloat(String(m.structural_steel).replace(/\D+/g, "")), "fy"],
        ["Sheet pile steel", m.sheet_pile_steel, "sheet pile wall", "#8c6d1f", parseFloat(String(m.sheet_pile_steel).replace(/\D+/g, "")), "fy"],
      ];
      return svg(700, 96, defs + cards.map(([t, g, what, col, f, sym], i) => {
        const x = 6 + i * 173;
        return `<rect x="${x}" y="6" width="164" height="84" rx="10" fill="${col}" fill-opacity=".12" stroke="${col}"/>
          <rect x="${x + 10}" y="18" width="26" height="26" rx="${sym === "fck" ? 3 : 13}" fill="${sym === "fck" ? "url(#pa-conc)" : col}" stroke="${col}"/>
          <text x="${x + 44}" y="30" class="pa-t">${esc(t)}</text><text x="${x + 44}" y="46" class="pa-big" fill="${col}">${esc(g || "–")}</text>
          <text x="${x + 10}" y="66" class="pa-s">${esc(what)}</text><text x="${x + 10}" y="82" class="pa-s">${sym} ${n(f)} MPa</text>`;
      }).join(""), "Project grades");
    } },
  { title: "Covers and corrosion", keys: ["durability"],
    intro: "Concrete cover to the outermost bars, and the steel lost to corrosion over the design life. Elements left empty use these.",
    art: (o) => {
      const c = o.durability?.covers || {}, k = o.durability?.corrosion || {};
      // A slab over a pile, and a beam: covers drawn to a common scale (1 px = 2 mm), then a steel tube wall.
      const s = 0.5;
      const slab = `<rect x="10" y="20" width="230" height="90" fill="url(#pa-conc)" stroke="${C.concrete}"/>
        <line x1="10" x2="240" y1="${20 + c.slab_top * s}" y2="${20 + c.slab_top * s}" stroke="${C.bar}" stroke-width="3"/>
        <line x1="10" x2="240" y1="${110 - c.slab_bottom * s}" y2="${110 - c.slab_bottom * s}" stroke="${C.bar}" stroke-width="3"/>
        ${dim(250, 20, 250, 20 + c.slab_top * s, `top ${n(c.slab_top)} mm`, { side: 1 })}
        ${dim(250, 110 - c.slab_bottom * s, 250, 110, `bottom ${n(c.slab_bottom)} mm`, { side: 1 })}
        <text x="14" y="14" class="pa-t">Slab</text>
        <rect x="95" y="110" width="60" height="70" fill="url(#pa-conc)" stroke="${C.concrete}"/><line x1="${95 + c.piles * s}" x2="${95 + c.piles * s}" y1="112" y2="180" stroke="${C.bar}" stroke-width="3"/>
        ${dim(95, 190, 95 + c.piles * s, 190, `pile ${n(c.piles)}`, { side: 1 })}`;
      const beam = `<rect x="360" y="20" width="110" height="130" rx="2" fill="url(#pa-conc)" stroke="${C.concrete}"/>
        <rect x="${360 + c.beams * s}" y="${20 + c.beams * s}" width="${110 - 2 * c.beams * s}" height="${130 - 2 * c.beams * s}" fill="none" stroke="${C.bar}" stroke-width="2.5"/>
        ${dim(360, 162, 360 + c.beams * s, 162, `${n(c.beams)} mm`, { side: 1 })}<text x="360" y="14" class="pa-t">Beams</text>
        <circle cx="415" cy="200" r="0" />`;
      const t = 18, loss = Math.max(k.combi_tube || 0, 0);
      const tube = `<text x="500" y="14" class="pa-t">Steel lost to corrosion</text>
        ${[["Tube", k.combi_tube], ["Casing", k.casing], ["Sheet pile / face", k.sheet_pile_per_face]].map(([name, v], i) => {
          const y = 30 + i * 50, w = 90;
          return `<rect x="500" y="${y}" width="${w}" height="26" fill="${C.steel}" fill-opacity=".85"/>
            <rect x="${500 + w - (v || 0) * 3}" y="${y}" width="${(v || 0) * 3}" height="26" fill="#c96f2d"/>
            <text x="500" y="${y + 40}" class="pa-s">${esc(name)}: ${n(v, 2)} mm</text>`;
        }).join("")}`;
      return svg(680, 210, defs + slab + beam + tube, "Covers and corrosion allowances") + (loss > t ? "" : "");
    } },
  { title: "Partial factors", keys: ["partial_factors"],
    intro: "Material factors on the characteristic strengths. The design strengths they give with the project grades are worked out below.",
    art: (o, h) => {
      const pf = o.partial_factors || {};
      const fck = grade(h.materials?.concrete, o.materials?.concrete)?.fck;
      const fyk = parseFloat(String(o.reinforcement?.grade || "B500").replace(/\D+/g, "")) || 500;
      const fy = parseFloat(String(o.materials?.structural_steel || "").replace(/\D+/g, ""));
      const rows = [
        ["Concrete", `fcd = αcc·fck / γc = ${n(pf.alpha_cc, 2)} × ${n(fck)} / ${n(pf.gamma_c, 2)}`, fck && pf.gamma_c ? (pf.alpha_cc * fck) / pf.gamma_c : null, fck, C.concrete],
        ["Reinforcement", `fyd = fyk / γs = ${n(fyk)} / ${n(pf.gamma_s, 2)}`, fyk / (pf.gamma_s || 1), fyk, C.bar],
        ["Steel section", `fy / γM0 = ${n(fy)} / ${n(pf.gamma_m0, 2)}`, fy && pf.gamma_m0 ? fy / pf.gamma_m0 : null, fy, C.steel],
      ];
      return svg(700, 124, defs + rows.map(([t, f, d, k, col], i) => {
        const y = 12 + i * 34, W = 200;
        return `<text x="8" y="${y + 14}" class="pa-t">${esc(t)}</text>
          <rect x="110" y="${y}" width="${W}" height="20" rx="4" fill="${col}" fill-opacity=".2"/>
          <rect x="110" y="${y}" width="${k ? Math.min(1, (d || 0) / k) * W : 0}" height="20" rx="4" fill="${col}"/>
          <text x="${120 + W}" y="${y + 14}" class="pa-s">${esc(f)} = <tspan class="pa-strong">${n(d, 1)} MPa</tspan> <tspan>of ${n(k)}</tspan></text>`;
      }).join("") + `<text x="110" y="118" class="pa-s">full bar: characteristic strength · filled: design strength</text>`, "Design strengths");
    } },
  { title: "Reinforcement", keys: ["reinforcement"],
    intro: "The bars the project uses and how close or far apart they may be. The drawing shows the bar sizes ticked, to scale.",
    art: (o) => {
      const r = o.reinforcement || {};
      const list = r.bar_diameters || [];
      let x = 14;
      const bars = list.map((d) => {
        const rr = d / 2 * 0.9, cx = x + rr;
        x += 2 * rr + 18;
        return `<circle cx="${cx}" cy="40" r="${rr}" fill="${C.bar}"/><text x="${cx}" y="84" text-anchor="middle" class="pa-s">Ø${d}</text>`;
      }).join("");
      const sp = `<text x="360" y="12" class="pa-t">Spacing in slabs and beams</text>
        ${[0, 1, 2, 3].map((i) => `<circle cx="${380 + i * 60}" cy="50" r="8" fill="${C.bar}"/>`).join("")}
        ${dim(388, 66, 432, 66, `≥ ${n(r.min_clear_spacing)} mm clear`, { side: 1 })}
        ${dim(380, 34, 440, 34, `≤ ${n(r.max_spacing)} mm`, { side: -1 })}
        <text x="360" y="94" class="pa-s">steps of ${n(r.spacing_step)} mm · slab meshes at ${esc((r.slab_spacings || []).join(" or "))} mm</text>
        <text x="360" y="106" class="pa-s">up to ${n(r.max_layers)} layers per face</text>`;
      return svg(700, 112, defs + bars + sp, "Bar sizes and spacing");
    } },
  { title: "Pile reinforcement", keys: ["piles"],
    intro: "How pile cages are chosen: rows of bars, their spacing, laps and the zones down the pile.",
    art: (o) => {
      const p = o.piles || {};
      const rows = (p.rows || [1]).map(Number);
      const most = Math.max(...rows);
      const cx = 110, cy = 105, R = 90;
      let rings = barsRing(cx, cy, R - 16, 26, 4.5);
      if (most >= 1.5) rings += barsRing(cx, cy, R - 30, most >= 2 ? 26 : 13, 4.5);
      if (most >= 2.5) rings += barsRing(cx, cy, R - 44, most >= 3 ? 26 : 13, 4.5);
      const lap = p.lap_factor || 45;
      return svg(660, 210, defs + `<circle cx="${cx}" cy="${cy}" r="${R}" fill="url(#pa-conc)" stroke="${C.concrete}"/>${rings}
        <text x="${cx}" y="206" text-anchor="middle" class="pa-s">up to ${most} row${most > 1 ? "s" : ""} (${esc(rows.join(", "))} allowed)</text>
        <text x="240" y="24" class="pa-t">Clear spacing between bars</text>
        <text x="240" y="42" class="pa-s">${n(p.min_clear_spacing)} to ${n(p.max_clear_spacing)} mm${p.even_bar_count ? ", even bar counts" : ""}</text>
        <text x="240" y="72" class="pa-t">Laps and anchorage</text>
        <rect x="240" y="82" width="200" height="8" fill="${C.bar}"/><rect x="${440 - lap * 1.2}" y="94" width="200" height="8" fill="${C.bar}" fill-opacity=".7"/>
        ${dim(440 - lap * 1.2, 110, 440, 110, `lap ${n(lap)}φ`, { side: 1 })}
        <text x="240" y="142" class="pa-s">bars ${n(p.head_anchorage_factor)}φ into the slab or beam above · steel ratio ≤ ${n(p.max_steel_ratio, 1)}%</text>
        <text x="240" y="160" class="pa-s">${p.curtail ? `reinforcement reduced down the pile in zones ≥ ${n(p.min_zone_length, 1)} m` : "the same cage the whole length"}</text>
        <text x="240" y="180" class="pa-s">bars cut to ${esc((p.standard_bar_lengths || []).join(", "))} m, longest ${n(p.max_bar_length)} m</text>`, "Pile cage rules");
    } },
  { title: "Cracking and restraint", keys: ["cracking"],
    intro: "Values for the long-term crack widths and for restraint cracking from temperature and shrinkage.",
    art: (o) => {
      const c = o.cracking || {};
      const eps = ((c.early_age_drop || 0) + (c.seasonal_drop || 0)) * (c.thermal_expansion || 0) * (c.creep_factor || 0);
      const s = 3.2;
      return svg(660, 110, defs + `<text x="8" y="16" class="pa-t">Temperature drops the concrete must follow</text>
        <rect x="8" y="28" width="${(c.early_age_drop || 0) * s}" height="22" rx="4" fill="#e59a3c"/><text x="${14 + (c.early_age_drop || 0) * s}" y="44" class="pa-s">T1 early age ${n(c.early_age_drop)} °C</text>
        <rect x="8" y="56" width="${(c.seasonal_drop || 0) * s}" height="22" rx="4" fill="${C.sea}"/><text x="${14 + (c.seasonal_drop || 0) * s}" y="72" class="pa-s">T2 seasonal ${n(c.seasonal_drop)} °C</text>
        <text x="330" y="40" class="pa-s">restrained strain ≈ K1 · α · (T1 + T2)</text>
        <text x="330" y="60" class="pa-s">= ${n(c.creep_factor, 2)} × ${n(c.thermal_expansion)}×10⁻⁶ × ${n((c.early_age_drop || 0) + (c.seasonal_drop || 0))}</text>
        <text x="330" y="84" class="pa-strong">≈ ${n(eps)} × 10⁻⁶ (before R)</text>
        <text x="8" y="102" class="pa-s">QP stresses use creep φ = ${n(c.creep_coefficient, 1)}</text>`, "Cracking values");
    } },
  { title: "Reading the actions", keys: ["plate_positive_moment", "beam_actions", "beam_support_results", "shear_check_distance", "results_into_connection"],
    intro: "How the Plaxis results are turned into design actions: which way plate moments act, what the beams take, and where piles stop.",
    art: (o) => svg(660, 150, defs + `
      <rect x="10" y="20" width="300" height="46" fill="url(#pa-conc)" stroke="${C.concrete}"/><text x="14" y="14" class="pa-t">Slab or beam</text>
      <rect x="130" y="66" width="60" height="80" fill="url(#pa-conc)" stroke="${C.concrete}"/><text x="196" y="140" class="pa-s">pile</text>
      <line x1="120" x2="200" y1="${66 - Math.min(40, (o.results_into_connection || 0) / 5)}" y2="${66 - Math.min(40, (o.results_into_connection || 0) / 5)}" stroke="${C.bar}" stroke-dasharray="4 3"/>
      ${dim(206, 66 - Math.min(40, (o.results_into_connection || 0) / 5), 206, 66, `${n(o.results_into_connection)} mm into the slab`, { side: 1 })}
      <line x1="130" x2="130" y1="20" y2="66" stroke="${C.dim}" stroke-dasharray="2 3"/><line x1="190" x2="190" y1="20" y2="66" stroke="${C.dim}" stroke-dasharray="2 3"/>
      ${dim(80, 76, 130, 76, `shear at ${esc(o.shear_check_distance)} from the face`, { side: 1 })}
      <text x="360" y="30" class="pa-t">Positive plate moment</text>
      <text x="360" y="48" class="pa-s">${o.plate_positive_moment === "auto" ? "read from each workbook (Auto)" : `taken as ${esc(o.plate_positive_moment)}`}</text>
      <text x="360" y="78" class="pa-t">Beams from the plates</text>
      <text x="360" y="96" class="pa-s">${esc(String(o.beam_actions).replace(/_/g, " "))}; over piles: ${esc(String(o.beam_support_results).replace(/_/g, " "))}</text>`, "How actions are read") },
  { title: "Expansion joints", keys: ["joints"],
    intro: "Where movement joints go along the berth. The strip shows segments at the longest and shortest lengths allowed.",
    art: (o) => {
      const j = o.joints || {};
      const L = [j.max_segment || 58, j.preferred_segment || j.max_segment || 58, j.min_segment || 20];
      const tot = L.reduce((a, b) => a + b, 0), W = 620;
      let x = 20;
      const segs = L.map((l, i) => {
        const w = (l / tot) * W, s = `<rect x="${x}" y="30" width="${w - 4}" height="34" rx="3" fill="${["#1e8fc0", "#2a9d8f", "#e0a100"][i]}" fill-opacity=".3" stroke="${["#1e8fc0", "#2a9d8f", "#e0a100"][i]}"/>
          <text x="${x + w / 2}" y="52" text-anchor="middle" class="pa-s">${["longest", "preferred", "shortest"][i]} ${n(l, 1)} m</text>`;
        x += w;
        return s;
      }).join("");
      return svg(660, 90, defs + `<text x="20" y="18" class="pa-t">Segments along the berth</text>${segs}
        <text x="20" y="84" class="pa-s">joints ${esc(String(j.position || "").replace(/_/g, " "))} between pile rows, ${n(j.furniture_clearance, 1)} m clear of fenders and bollards${j.at_corners ? ", one at each corner" : ""}</text>`, "Expansion joints");
    } },
  { title: "Construction joints", keys: ["construction_joints"],
    intro: "How the shear across a construction joint is checked (EN 1992 6.2.5), as in the office's joint check." },
];

// ---------------------------------------------------------------- elements
const pileArt = (e, h) => {
  const D = e.diameter || 1200, cov = e.cover ?? h.design?.durability?.covers?.piles ?? 75;
  const R = 70, s = R / (D / 2), cx = 90, cy = 90;
  return svg(620, 190, defs + `<circle cx="${cx}" cy="${cy}" r="${R}" fill="url(#pa-conc)" stroke="${C.concrete}"/>
    ${e.casing ? `<circle cx="${cx}" cy="${cy}" r="${R + 4}" fill="none" stroke="${C.steel}" stroke-width="5"/>` : ""}
    <circle cx="${cx}" cy="${cy}" r="${R - cov * s}" fill="none" stroke="${C.dim}" stroke-dasharray="3 3"/>
    ${barsRing(cx, cy, R - cov * s - 6, e.bar_count || 24, 4)}
    ${dim(cx - R, cy + R + 12, cx + R, cy + R + 12, `Ø ${n(D)} mm`, { side: 1 })}
    <text x="200" y="30" class="pa-t">${e.casing ? "Cased pile" : "Bored pile"}</text>
    <text x="200" y="50" class="pa-s">cover to links ${n(cov)} mm${e.cover == null ? " (project value)" : ""} · links from Ø${n(e.link_diameter)}</text>
    <text x="200" y="70" class="pa-s">${e.bar_count ? `${e.bar_count} bars in the outer row (fixed)` : "bars chosen by Triton"}</text>
    <text x="200" y="90" class="pa-s">top level ${e.head_level == null ? "not set: every result is used" : `${n(e.head_level, 2)} m (slab soffit)`}</text>
    <text x="200" y="110" class="pa-s">crack width limit ${n(e.crack_width_limit, 2)} mm (QP)</text>`, "Pile section");
};
const beamArt = (e, h) => {
  const b = e.width || 2000, d = e.depth || 2000, cov = e.cover ?? h.design?.durability?.covers?.beams ?? 50;
  const s = 120 / Math.max(b, d), W = b * s, H = d * s;
  return svg(620, 170, defs + `<rect x="70" y="20" width="${W}" height="${H}" fill="url(#pa-conc)" stroke="${C.concrete}"/>
    <rect x="${70 + cov * s}" y="${20 + cov * s}" width="${W - 2 * cov * s}" height="${H - 2 * cov * s}" fill="none" stroke="${C.bar}" stroke-width="2"/>
    ${dim(70, 30 + H, 70 + W, 30 + H, `b ${e.width ? n(b) : "from the model"} mm`, { side: 1 })}
    ${dim(62, 20, 62, 20 + H, `h ${n(d)}`, { side: -1 })}
    <text x="${100 + W}" y="30" class="pa-t">Beam cross-section</text>
    <text x="${100 + W}" y="50" class="pa-s">cover ${n(cov)} mm${e.cover == null ? " (project value)" : ""} · links Ø${n(e.link_diameter)}</text>
    <text x="${100 + W}" y="70" class="pa-s">crack limits: top ${n(e.crack_width_limit, 2)} · bottom ${n(e.crack_width_limit_bottom, 2)} mm</text>
    <text x="${100 + W}" y="90" class="pa-s">joints every ${n(e.joint_spacing, 1)} m · restraint R ${e.restraint_factor == null ? "ACI (empty)" : n(e.restraint_factor, 2)}</text>`, "Beam section");
};
const slabArt = (e, h) => {
  const t = e.thickness || 700, cv = h.design?.durability?.covers || {};
  const ct = e.cover_top ?? cv.slab_top ?? 50, cb = e.cover_bottom ?? cv.slab_bottom ?? 50;
  const s = 0.12, H = t * s;
  const cs = e.column_strip_width || 2.2, fs = e.field_strip_width || 2;
  const strips = e.strips === "column_and_field";
  return svg(620, 180, defs + `<rect x="20" y="20" width="260" height="${H}" fill="url(#pa-conc)" stroke="${C.concrete}"/>
    <line x1="20" x2="280" y1="${20 + ct * s + 3}" y2="${20 + ct * s + 3}" stroke="${C.bar}" stroke-width="3"/>
    <line x1="20" x2="280" y1="${20 + H - cb * s - 3}" y2="${20 + H - cb * s - 3}" stroke="${C.bar}" stroke-width="3"/>
    ${dim(292, 20, 292, 20 + H, `${n(t)} mm`, { side: 1 })}
    <rect x="80" y="${20 + H}" width="40" height="50" fill="url(#pa-conc)" stroke="${C.concrete}"/><rect x="200" y="${20 + H}" width="40" height="50" fill="url(#pa-conc)" stroke="${C.concrete}"/>
    <text x="20" y="14" class="pa-t">Slab: covers ${n(ct)} top / ${n(cb)} bottom mm</text>
    <text x="360" y="14" class="pa-t">${strips ? "Column and field strips (plan)" : "One uniform slab (plan)"}</text>
    ${strips ? [0, 1, 2, 3, 4].map((i) => {
      const col = i % 2 === 0, w = (col ? cs : fs) * 22, x = 360 + [0, cs, cs + fs, 2 * cs + fs, 2 * cs + 2 * fs].map((v) => v * 22)[i];
      return `<rect x="${x}" y="26" width="${w}" height="110" fill="${col ? "#2a78d6" : "#1f9e89"}" fill-opacity="${col ? 0.28 : 0.14}" stroke="${col ? "#2a78d6" : "#1f9e89"}"/>
        ${col ? `<circle cx="${x + w / 2}" cy="81" r="7" fill="${C.concrete}"/>` : ""}`;
    }).join("") + `<text x="360" y="152" class="pa-s">column ${n(cs, 1)} m over the piles · field ${n(fs, 1)} m between</text>
    <text x="360" y="166" class="pa-s">strips run along ${esc(e.strip_direction)}</text>` : ""}`, "Slab section and strips");
};
const combiArt = (e) => {
  const lv = [3, e.concrete_bottom_level ?? -25, -39];
  const y = (z) => 16 + ((3 - z) / 42) * 160;
  return svg(620, 190, defs + `<rect x="60" y="${y(2.7)}" width="44" height="${y(-39) - y(2.7)}" fill="${C.steel}" fill-opacity=".2" stroke="${C.steel}" stroke-width="3"/>
    <rect x="66" y="${y(2.7)}" width="32" height="${y(lv[1]) - y(2.7)}" fill="url(#pa-conc)"/>
    <line x1="40" x2="120" y1="${y(lv[1])}" y2="${y(lv[1])}" stroke="${C.dim}" stroke-dasharray="4 3"/><text x="124" y="${y(lv[1]) + 4}" class="pa-s">infill bottom ${n(lv[1], 2)} m</text>
    ${e.top_level_to_ignore != null ? `<line x1="40" x2="120" y1="${y(e.top_level_to_ignore)}" y2="${y(e.top_level_to_ignore)}" stroke="${C.bar}" stroke-dasharray="4 3"/><text x="124" y="${y(e.top_level_to_ignore) + 4}" class="pa-s">top ${n(e.top_level_to_ignore, 2)} m</text>` : ""}
    <text x="250" y="30" class="pa-t">King pile Ø${n(e.tube_diameter)} × ${n(e.tube_thickness, 1)} mm</text>
    <text x="250" y="50" class="pa-s">steel tube, filled with reinforced concrete down to ${n(lv[1], 2)} m</text>
    <text x="250" y="70" class="pa-s">actions split by ${e.tube_share === "ei_split" ? "stiffness (E·I) between tube and infill" : esc(String(e.tube_share).replace(/_/g, " "))}</text>
    <text x="250" y="90" class="pa-s">buckling length ${n(e.buckling_length_factor, 2)} × L, curve ${esc(e.buckling_curve)}</text>`, "Combi wall king pile");
};
const spwArt = (e) => svg(620, 120, defs + `<path d="M10 70 l30 -40 h50 l30 40 h50 l30 -40 h50 l30 40 h50 l30 -40 h50" fill="none" stroke="#8c6d1f" stroke-width="5" stroke-linejoin="round"/>
  <text x="10" y="100" class="pa-s">${esc(e.section_name)} · steel ${esc(e.steel || "project grade")} · class ${esc(e.class_from)}</text>
  <text x="10" y="116" class="pa-s">top level ${e.top_level == null ? "not set" : `${n(e.top_level, 2)} m`} · buckling length ${e.buckling_length == null ? "0.7 L (assumed)" : `${n(e.buckling_length, 2)} m`} · shear from ${esc(e.shear)}</text>`, "Sheet pile profile");

const BEAM_PAGES = [
  { title: "Size and cover", keys: ["width", "depth", "cover", "link_diameter", "concrete"], intro: "The beam's cross-section, its concrete and cover. Empty values use the project's.", art: beamArt },
  { title: "Cracking", keys: ["crack_width_limit", "crack_width_limit_bottom", "joint_spacing", "restraint_factor"], intro: "Crack width limits on each face and the restraint check along the beam." },
];
export const ELEMENT_PAGES = {
  pile: [{ title: "Pile", keys: ["concrete", "diameter", "cover", "link_diameter", "count", "bar_count", "head_level", "crack_width_limit"], intro: "The pile's size, cover and top level. Empty values use the project's.", art: pileArt }],
  front_beam: BEAM_PAGES,
  rear_beam: BEAM_PAGES,
  transverse_beam: BEAM_PAGES,
  slab: [
    { title: "Thickness and covers", keys: ["concrete", "thickness", "cover_top", "cover_bottom", "crack_width_limit", "crack_width_limit_bottom"], intro: "The slab section. Empty values use the project's.", art: slabArt },
    { title: "Strips and stations", keys: ["strips", "strip_direction", "column_strip_width", "field_strip_width", "stations", "zone_size", "peaks", "twisting", "min_zone_length"], intro: "How the slab is cut into column and field strips and design stations." },
    { title: "Bar layout", keys: ["layout_bottom_x", "layout_bottom_y", "layout_top_x", "layout_top_y", "mesh_bottom_x", "mesh_bottom_y", "mesh_top_x", "mesh_top_y"], intro: "The basic mesh on each face and whether additional bars go on top of it." },
    { title: "Punching and shear", keys: ["punching_thickness", "punching_piles", "punching_per", "punching_fix", "punching_depths", "punching_face_beta", "shear_links", "shear_in_tension"], intro: "Punching round the pile heads and one-way shear." },
    { title: "Openings and crane", keys: ["manholes", "channels", "voids", "crane"], intro: "Manholes, service channels, PVC voids and mobile crane areas." },
    { title: "Restraint", keys: ["joint_spacing", "restraint_factor", "restraint_check"], intro: "Cracking from temperature and shrinkage restrained by the piles." },
  ],
  combi_wall: [
    { title: "Tube", keys: ["tube_diameter", "tube_thickness", "steel", "tube_fy", "corrosion_loss", "corrosion_zones", "fabrication_class", "count"], intro: "The steel king pile and the steel lost to corrosion.", art: combiArt },
    { title: "Concrete infill", keys: ["concrete", "concrete_bottom_level", "cover", "link_diameter", "bar_count", "top_level_to_ignore"], intro: "The reinforced concrete inside the tube, down to its bottom level." },
    { title: "Checks", keys: ["tube_share", "tube_check", "buckling_length_factor", "firm_soil_level", "column_ei", "buckling_curve"], intro: "How the actions are shared and how the tube is checked for buckling." },
  ],
  sheet_pile_wall: [
    { title: "Section and steel", keys: ["section_name", "steel", "class_from", "use_wel_only", "flange_width", "web_angle", "welded_interlocks"], intro: "The sheet pile section and how its class is found.", art: spwArt },
    { title: "Levels and buckling", keys: ["top_level", "firm_soil_level", "buckling_length", "eccentricity", "differential_head", "gamma_m0", "gamma_m1"], intro: "Where the wall is checked from, and its buckling length." },
    { title: "Corrosion and actions", keys: ["corrosion_zones", "shear", "ignore"], intro: "Steel lost in each zone, and which Plaxis actions are used." },
  ],
};

// ---------------------------------------------------------------- sections
const siteArt = (sec) => {
  const st = sec.site || {};
  const water = (st.water_levels || []).filter((w) => !(st.water_hidden || []).includes(w.name));
  const bed = st.seabed_level ?? -16;
  const top = Math.max(3, ...water.map((w) => w.level)), bot = Math.min(bed - 2, -18);
  const y = (z) => 24 + ((top - z) / (top - bot)) * 160;
  const deckTop = 3.5;
  return svg(660, 200, defs + `
    <rect x="200" y="${y(bed)}" width="330" height="${200 - y(bed)}" fill="url(#pa-soil)"/>
    <rect x="200" y="${y(Math.max(...water.map((w) => w.level), 0))}" width="330" height="${y(bed) - y(Math.max(...water.map((w) => w.level), 0))}" fill="${C.sea}" fill-opacity=".18"/>
    <rect x="30" y="${y(deckTop)}" width="170" height="${200 - y(deckTop)}" fill="url(#pa-soil)"/>
    <rect x="186" y="${y(deckTop)}" width="14" height="${200 - y(deckTop)}" fill="${C.steel}" fill-opacity=".6"/>
    ${(() => {
      // Tide levels close together: labels stacked 12 px apart with a leader to their line.
      let last = -Infinity;
      return [...water].sort((a, b) => b.level - a.level).map((w, i) => {
        const ly = Math.max(y(w.level) + 4, last + 12);
        last = ly;
        return `<line x1="200" x2="530" y1="${y(w.level)}" y2="${y(w.level)}" stroke="${C.sea}" stroke-width="1" stroke-dasharray="${i ? "4 3" : ""}"/>
          <line x1="530" x2="560" y1="${y(w.level)}" y2="${ly - 4}" stroke="${C.sea}" stroke-width=".7"/>
          <text x="564" y="${ly}" class="pa-s">${esc(w.name)} ${n(w.level, 2)} m</text>`;
      }).join("");
    })()}
    <line x1="200" x2="530" y1="${y(bed)}" y2="${y(bed)}" stroke="${C.soil}" stroke-width="2"/>
    <text x="210" y="${y(bed) + 14}" class="pa-s">seabed ${n(bed, 2)} m</text>
    <text x="34" y="${y(deckTop) - 4}" class="pa-t">land side</text><text x="210" y="${y(top) - 2}" class="pa-t">sea side</text>`, "Seabed and water levels");
};
export const SECTION_PAGES = [
  { title: "Name and edges", keys: ["main", "edges"], intro: "The section's name and which results near the model's edges are left out (the plan shows the cut)." },
  { title: "Load combinations", keys: ["combos"], intro: "The combinations this section's workbook should have. A workbook is checked against them on upload." },
  { title: "Seabed, water and soil", keys: ["site"], intro: "How the site is drawn in the 3D views. Never a design input, so it can change while the model is locked.", art: siteArt },
  { title: "Quay furniture", keys: ["quay"], intro: "Whether this berth has fender protrusions and STS cranes. Their sizes are on the Furniture tab." },
  { title: "Berth line", keys: ["alignment"], intro: "The berth's line in plan: straight, or a corner with an inclined part." },
  { title: "Joints", keys: ["joints"], intro: "Expansion joints along this section's berth." },
];
