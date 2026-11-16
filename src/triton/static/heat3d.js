// Values for the 3D view's "Cost" and "Reinforcement ratio" colours (Ahmed, 2026-09-26): the same
// green-to-red colouring as utilisation, from the cheapest (least reinforced) to the dearest (most).

const STEEL_DENSITY = 7850; // kg/m³

const num = (v) => (typeof v === "number" && isFinite(v) ? v : null);

// Each element's cost per metre of berth, from the Costing tab's numbers for one section, so a pile
// row and the slab compare on one scale: what each costs along the quay.
// -> { values: {element: {value, tip}}, unit, note }
export function costValues(costing, sectionId) {
  const c = (costing?.sections || []).find((s) => s.section_id === sectionId);
  const cur = costing?.currency || "";
  const unit = `${cur ? `${cur} ` : ""}per m of berth`;
  if (!c) return { values: {}, unit, note: "No costing for this section yet." };
  if (!c.totals) return { values: {}, unit, note: (c.notes || []).join(" ") || "No costing for this section yet." };
  const rows = c.rows.filter((r) => r.kind !== "item");
  const total = rows.reduce((s, r) => s + (r.cost || 0), 0);
  const values = {};
  const fmt = (v) => Math.round(v).toLocaleString("en-GB");
  for (const r of rows) {
    const what = r.basis ? ` (${r.basis})` : "";
    if (r.cost_per_m == null) {
      values[r.element] = { value: null, tip: `no cost, ${r.missing?.length ? `missing ${r.missing.join(", ")}` : "not priced"}` };
      continue;
    }
    const share = total ? ` (${Math.round((100 * r.cost) / total)}% of the structure)` : "";
    values[r.element] = {
      value: r.cost_per_m,
      tip: `${fmt(r.cost_per_m)} ${unit}${share}; ${fmt(r.cost)} ${cur} over ${c.berth_length_m} m${what}`,
    };
  }
  const priced = Object.values(values).filter((v) => v.value != null);
  const note = !priced.length
    ? "No prices yet, so nothing is coloured: enter the unit prices on the Project tab and the berth length on the Costing tab."
    : priced.length < rows.length
      ? "Grey: no cost yet (a price or an input is missing; the Costing tab names it)."
      : "";
  return { values, unit, note };
}

// Reinforcement in kg per m³ of concrete at each band of the 3D view, with the % of steel by volume:
// piles zone by zone (their cage runs and link zones), slabs square by square (the bars of the four
// layers there), beams and combi wall infills as one value over the element.
// -> {element: [[x, y, z, kg/m³, size?, why?, pct]]}
export function rebarBands(res, bands) {
  const out = {};
  for (const p of res?.piles || []) {
    const b = bands?.[p.element] || [];
    const D = num(p.section?.diameter_mm);
    if (!b.length || !D) continue;
    const ac = (Math.PI * D * D) / 4; // mm²
    const runs = p.curtailment?.runs || [];
    const whole = num(p.arrangement?.area_mm2);
    // Links: the pile's link weight shared among its zones by length over pitch.
    const zones = (p.shear?.zones || []).filter((z) => z.spacing_mm > 0);
    const weight = zones.reduce((s, z) => s + (z.top - z.bottom) / z.spacing_mm, 0);
    const linksKg = num(p.shear?.links_kg) || 0;
    const areaAt = (z) => {
      const r = runs.find((q) => z <= q.top + 1e-6 && z >= q.bottom - 1e-6);
      return r ? r.cage.area_mm2 : runs.length ? null : whole;
    };
    const linksAt = (z) => {
      const q = zones.find((q) => z <= q.top + 1e-6 && z >= q.bottom - 1e-6);
      if (!q || !weight) return 0;
      return (linksKg * (1 / q.spacing_mm)) / weight / (ac / 1e6); // kg per m³
    };
    out[p.element] = b.map(([x, y, z]) => {
      const a = areaAt(z);
      if (a == null) return [x, y, z, null];
      const pct = (100 * a) / ac;
      return [x, y, z, Math.round((pct / 100) * STEEL_DENSITY + linksAt(z)), null, null, Math.round(pct * 100) / 100];
    });
  }
  const flat = (key, st, b) => {
    const kg = num(st?.kg_per_m3);
    if (kg == null || !b?.length) return;
    const pct = num(st?.ratio_pct);
    out[key] = b.map(([x, y, z, , size, why]) => [x, y, z, kg, size ?? null, why ?? null, pct]);
  };
  for (const w of res?.combi_walls || []) flat(w.element, w.infill?.steel, bands?.[w.element]);
  for (const d of res?.beams || []) flat(d.key || d.element, d.steel, bands?.[d.key || d.element]);
  for (const d of res?.slabs || []) {
    const key = d.key || d.element;
    const b = bands?.[key] || [];
    const h = num(d.thickness_mm);
    const layers = Object.values(d.layers || {});
    if (!b.length || !h || !layers.length) {
      flat(key, d.steel, b);
      continue;
    }
    const net = (h / 1000) * (d.steel?.concrete_share || 1); // m³ of concrete per m² of deck
    out[key] = b.map(([x, y, z, , size, why]) => {
      // Each layer: its basic mesh, or the zone's bars where a zone covers the square.
      let a = 0; // mm² per m, the four layers together
      for (const lay of layers) {
        let here = num(lay.basic?.as_mm2_per_m) || 0;
        for (const zn of lay.zones || [])
          if (x >= zn.x[0] - 1e-6 && x <= zn.x[1] + 1e-6 && y >= zn.y[0] - 1e-6 && y <= zn.y[1] + 1e-6)
            here = Math.max(here, zn.as_mm2_per_m || 0);
        a += here;
      }
      const m3 = a / 1e6; // m³ of steel per m² of deck
      return [x, y, z, Math.round((m3 * STEEL_DENSITY) / net), size ?? null, why ?? null, Math.round((10000 * m3) / net) / 100];
    });
  }
  return out;
}

// The range the colours run over: the lowest value green, the highest red.
export function span(values) {
  let lo = Infinity;
  let hi = -Infinity;
  for (const v of values) {
    if (v == null || !isFinite(v)) continue;
    lo = Math.min(lo, v);
    hi = Math.max(hi, v);
  }
  return lo <= hi ? [lo, hi] : null;
}
