"""Quantities and cost of a designed section along its berth, and sections side by side.

The design model covers a length of berth (the front or rear beam's length, else the slab's extent
along the berth). Elements that repeat along the berth (piles, king piles, transverse beams) are
counted at the model's spacing unless a spacing or a number is given; elements that run along it
(front and rear beams, slab, sheet pile wall, combi wall sheets) are taken per metre of berth.

Prices are the project's unit prices: concrete and reinforcement for the slab and beams, bored
piles per linear metre with an allowance of reinforcement included (designed reinforcement above
it is added at the reinforcement price and flagged), and steel elements per tonne, m² or metre.
"""

from __future__ import annotations

import math
from typing import Any

from .alignment import combine_parts
from .materials import STEEL_DENSITY
from .project import CombiWallInput, ElementCosting, PileInput, Prices, Project, Section, SheetPileInput


def _money(x: float | None) -> float | None:
    return None if x is None else round(x, 0)


class _Row:
    def __init__(self, element: str, kind: str):
        self.element, self.kind = element, kind
        self.basis: list[str] = []
        self.concrete_m3 = 0.0
        self.rebar_t = 0.0
        self.steel_t = 0.0
        self.cost: float | None = 0.0
        self.missing: list[str] = []
        self.flags: list[str] = []
        self.count: int | None = None
        self.count_auto: int | None = None
        self.spacing: float | None = None
        self.length: float | None = None

    def add(self, amount: float | None, what: str) -> None:
        """Add a cost; a missing price leaves the row without a total and says which."""
        if amount is None:
            self.missing.append(what)
            self.cost = None
        elif self.cost is not None:
            self.cost += amount

    def to_dict(self, berth: float) -> dict[str, Any]:
        return {
            "element": self.element,
            "kind": self.kind,
            "basis": "; ".join(self.basis),
            "count": self.count,
            "count_auto": self.count_auto,
            "spacing_m": round(self.spacing, 3) if self.spacing else None,
            "length_m": round(self.length, 2) if self.length else None,
            "concrete_m3": round(self.concrete_m3, 1),
            "rebar_t": round(self.rebar_t, 2),
            "steel_t": round(self.steel_t, 2),
            "cost": _money(self.cost),
            "cost_per_m": _money(self.cost / berth) if self.cost is not None and berth else None,
            "missing": self.missing,
            "flags": self.flags,
        }


def _price(value: float | None, qty: float) -> float | None:
    if qty <= 0:
        return 0.0
    return None if value is None else value * qty


def _steel_item(prices: Prices, name: str):
    name = (name or "").strip().lower()
    return next((s for s in prices.steel_elements if s.name.strip().lower() == name), None) if name else None


def _steel_cost(
    row: _Row, prices: Prices, name: str, tonnes: float, metres: float, area_m2: float, what: str
):
    """Cost of steel by the named price (per t, m or m²), else the structural steel price per t."""
    item = _steel_item(prices, name)
    if item is None or item.price is None:
        row.add(_price(prices.steel, tonnes), f"{name or what} price" if name else "structural steel price")
        return
    qty = {"t": tonnes, "m": metres, "m²": area_m2}[item.unit]
    row.add(item.price * qty, item.name)


def model_length(section: Section, results: dict[str, Any]) -> float | None:
    """The length of berth the design model covers."""
    if section.costing.model_length:
        return section.costing.model_length
    along = [
        (b.get("steel") or {}).get("length_m")
        for b in results.get("beams", [])
        if b.get("kind") in ("front_beam", "rear_beam")
    ]
    along = [x for x in along if x]
    if along:
        return max(along)
    for s in results.get("slabs", []):
        box = s.get("box") or {}
        across = (s.get("strip_design") or {}).get("along") or "X"
        other = "Y" if str(across).upper().startswith("X") else "X"
        if box.get(other):
            lo, hi = box[other]
            return hi - lo
    return None


def _count(row: _Row, c: ElementCosting, in_model: int | None, berth: float, length: float | None) -> int:
    """How many along the berth: the number given, else the berth over the spacing (given, or the
    model's length over its count)."""
    spacing = c.spacing
    if spacing is None and in_model and length:
        spacing = length / in_model
    row.spacing = spacing
    row.count_auto = math.ceil(berth / spacing - 1e-6) if spacing else (in_model or 0)
    row.count = c.count if c.count is not None else row.count_auto
    return row.count


def _how(row: _Row, c: ElementCosting, what: str) -> str:
    if c.count is not None:
        return f"{row.count} {what} (number given)"
    if row.spacing:
        return f"{row.count} {what} at {row.spacing:.2f} m" + ("" if c.spacing else " (model spacing)")
    return f"{row.count} {what} (as in the model)"


def slab_links(slab: dict[str, Any]) -> dict[str, float]:
    """Mass (kg) of a designed slab's shear links (laid over each link zone) and punching links (the
    perimeters round each pile head that needs them), in the part of the slab the model covers. Each
    leg runs between the top and bottom covers with 10 bar diameters of hook at each end."""
    h = slab.get("thickness_mm") or 0.0
    inside = h - (slab.get("cover_top_mm") or 50.0) - (slab.get("cover_bottom_mm") or 50.0)
    shear = 0.0
    for z in (slab.get("shear") or {}).get("links") or []:
        phi, asw = z.get("phi"), z.get("asw_mm2_per_m2")
        if not phi or not asw or not z.get("x") or not z.get("y"):
            continue
        area = abs(z["x"][1] - z["x"][0]) * abs(z["y"][1] - z["y"][0])
        shear += asw * area * (inside + 20 * phi) / 1e3 * STEEL_DENSITY / 1e6
    punching = 0.0
    for q in slab.get("punching") or []:
        if not q.get("needs_reinforcement") or not q.get("asw_mm2_per_perimeter"):
            continue
        phi = q.get("link_phi_mm") or 12.0
        legs = q["asw_mm2_per_perimeter"] * (q.get("perimeters") or 1)
        punching += legs * (inside + 20 * phi) / 1e3 * STEEL_DENSITY / 1e6
    return {"shear_kg": shear, "punching_kg": punching}


def cost_section(
    project: Project,
    section: Section,
    results: dict[str, Any],
    length: float | None = None,
    berth: float | None = None,
) -> dict[str, Any]:
    """``length``: the length of berth the model covers, when ``results`` do not hold the beams it is
    taken from; ``berth``: a berth length to use when the section has none (a trial costed per metre)."""
    results = combine_parts(results)  # a corner berth's parts: one row per element
    prices = project.prices
    L = length or model_length(section, results)
    berth = section.costing.berth_length or berth
    notes = []
    if not berth:
        return {
            "section": section.name,
            "section_id": section.id,
            "rows": [],
            "notes": [
                "Enter the berth length of this section, e.g. 500 m. It is never taken from the model: "
                "the model covers only a short piece of the berth."
            ],
        }
    if L:
        notes.append(
            f"The model covers {L:.1f} m of berth; each element's count is the berth length over its "
            "spacing unless you give the number."
        )
    per_berth = berth / L if L else None
    ec = section.costing.elements
    rows: list[_Row] = []

    for p in results.get("piles", []):
        name = p["element"]
        el = section.elements.get(name)
        c = ec.get(name, ElementCosting())
        st = p.get("steel") or {}
        geom = p.get("section") or {}
        d = geom.get("diameter_mm") or (el.diameter if isinstance(el, PileInput) else 1200.0)
        area = math.pi * (d / 1000) ** 2 / 4
        length = c.length or (
            st["concrete_m3"] / area
            if st.get("concrete_m3")
            else (geom.get("head_level_m", 0) - geom.get("toe_level_m", 0)) or None
        )
        row = _Row(name, "pile")
        n = _count(row, c, p.get("count"), berth, L)
        how = _how(row, c, "piles")
        row.length = length
        if not length:
            row.basis.append(f"{how}; pile length unknown, give it")
            row.add(None, "pile length")
            rows.append(row)
            continue
        kg_per_m = (st.get("total_kg") or 0) / length if st.get("concrete_m3") else 0.0
        row.basis.append(f"{how}, Ø{d:.0f} × {length:.1f} m, {kg_per_m:.0f} kg/m of reinforcement")
        row.concrete_m3 = n * area * length
        row.rebar_t = n * kg_per_m * length / 1000
        price = next((q for q in prices.piles if abs(q.diameter - d) < 1), None)
        if price is None or price.price_per_m is None:
            row.add(None, f"Ø{d:.0f} pile price per metre")
        else:
            row.add(price.price_per_m * n * length, "")
            extra = kg_per_m - price.rebar_included
            if extra > 0.5:
                tonnes = extra * length * n / 1000
                row.flags.append(
                    f"Designed reinforcement {kg_per_m:.0f} kg/m is {extra:.0f} kg/m above the "
                    f"{price.rebar_included:.0f} kg/m the pile price includes: {tonnes:.1f} t extra."
                )
                row.add(_price(prices.rebar, tonnes), "reinforcement price")
            else:
                row.flags.append(
                    f"Within the {price.rebar_included:.0f} kg/m the pile price includes "
                    f"({price.rebar_included - kg_per_m:.0f} kg/m spare)."
                )
        rows.append(row)

    for w in results.get("combi_walls", []):
        name = w["element"]
        el = section.elements.get(name)
        c = ec.get(name, ElementCosting())
        tube = (w.get("tube") or {}).get("section") or {}
        D = tube.get("diameter_mm") or (el.tube_diameter if isinstance(el, CombiWallInput) else 1626.0)
        t = tube.get("thickness_mm") or (el.tube_thickness if isinstance(el, CombiWallInput) else 18.0)
        length = c.length or ((w.get("tube") or {}).get("column") or {}).get("length_m")
        row = _Row(name, "combi_wall")
        n = _count(row, c, w.get("count"), berth, L)
        how = _how(row, c, "king piles")
        row.length = length
        kg_m = math.pi * (D - t) * t / 1e6 * STEEL_DENSITY
        if not length:
            row.basis.append(f"{how}; tube length unknown, give it")
            row.add(None, "tube length")
            rows.append(row)
            continue
        tube_t = n * kg_m * length / 1000
        row.basis.append(f"{how}, tube {D:.0f} × {t:.0f} × {length:.1f} m ({kg_m:.0f} kg/m)")
        row.steel_t += tube_t
        _steel_cost(row, prices, c.steel_element, tube_t, n * length, n * math.pi * D / 1000 * length, "tube")
        inf = (w.get("infill") or {}).get("steel") or {}
        if inf.get("concrete_m3"):
            row.concrete_m3 += n * inf["concrete_m3"]
            row.rebar_t += n * (inf.get("total_kg") or 0) / 1000
            row.add(
                _price(prices.concrete_infill or prices.concrete_beams, n * inf["concrete_m3"]),
                "infill concrete price",
            )
            row.add(_price(prices.rebar, n * (inf.get("total_kg") or 0) / 1000), "reinforcement price")
        if c.intermediate_element:
            item = _steel_item(prices, c.intermediate_element)
            gaps = max(berth - n * D / 1000, 0.0)
            ilen = c.intermediate_length or length
            area_m2 = gaps * ilen
            mass = item.mass if item and item.mass else None
            tonnes = area_m2 * mass / 1000 if mass else 0.0
            row.steel_t += tonnes
            row.basis.append(f"intermediate {c.intermediate_element}: {area_m2:.0f} m² ({ilen:.1f} m long)")
            if item and item.unit == "t" and not mass:
                row.add(None, f"{item.name} mass per m²")
            else:
                _steel_cost(row, prices, c.intermediate_element, tonnes, gaps, area_m2, "intermediate sheets")
        rows.append(row)

    for w in results.get("sheet_pile_walls", []):
        name = w["element"]
        el = section.elements.get(name)
        c = ec.get(name, ElementCosting())
        row = _Row(name, "sheet_pile_wall")
        length = c.length or w.get("length_m")
        row.length = length
        section_name = (el.section_name if isinstance(el, SheetPileInput) else "") or ""
        # No price picked: the one named like the wall's section, else the first AZ in the list.
        first_az = next((x.name for x in prices.steel_elements if x.name.upper().startswith("AZ")), "")
        item_name = c.steel_element or section_name or first_az
        item = _steel_item(prices, item_name)
        if not length:
            row.basis.append("sheet pile length unknown, give it")
            row.add(None, "sheet pile length")
            rows.append(row)
            continue
        area_m2 = berth * length
        kg_m2 = (el.area * 0.785 if isinstance(el, SheetPileInput) and el.area else None) or (
            item.mass if item else None
        )
        tonnes = area_m2 * kg_m2 / 1000 if kg_m2 else 0.0
        row.steel_t = tonnes
        row.basis.append(
            f"{item_name or 'sheet piles'}, {length:.1f} m long, {area_m2:.0f} m²"
            + (f", {kg_m2:.0f} kg/m²" if kg_m2 else "")
        )
        if (item is None or item.unit == "t") and not kg_m2:
            row.add(None, "sheet pile mass per m² (area on the element or mass in the price list)")
        else:
            _steel_cost(row, prices, item_name, tonnes, berth, area_m2, "sheet piles")
        rows.append(row)

    for b in results.get("beams", []):
        name = b["element"]
        c = ec.get(name, ElementCosting())
        st = b.get("steel") or {}
        row = _Row(name, "beam")
        area = (b.get("width_mm") or 0) * (b.get("depth_mm") or 0) / 1e6
        kg_m = st.get("kg_per_m") or 0.0
        if b.get("kind") == "transverse_beam":
            length = c.length or st.get("length_m") or 0.0
            n = _count(row, c, 1, berth, L)  # each transverse beam is its own element in the model
            row.length = length
            run = n * length
            row.basis.append(f"{_how(row, c, 'beams')}, {length:.1f} m each")
        else:
            run = c.length or berth
            row.length = run
            row.basis.append(f"{run:.1f} m along the berth")
        row.basis.append(f"{b.get('width_mm', 0):.0f} × {b.get('depth_mm', 0):.0f}, {kg_m:.0f} kg/m")
        row.concrete_m3 = area * run
        row.rebar_t = kg_m * run / 1000
        row.add(_price(prices.concrete_beams, row.concrete_m3), "beam concrete price")
        row.add(_price(prices.rebar, row.rebar_t), "reinforcement price")
        rows.append(row)

    for s in results.get("slabs", []):
        name = s["element"]
        c = ec.get(name, ElementCosting())
        st = s.get("steel") or {}
        row = _Row(name, "slab")
        h = (s.get("thickness_mm") or 0) / 1000
        width = c.length or ((st.get("area_m2") or 0) / L if L else None)
        row.length = width
        if not width:
            row.basis.append("slab width across the quay unknown, give it")
            row.add(None, "slab width")
            rows.append(row)
            continue
        area_m2 = width * berth
        links = slab_links(s)
        model_area = st.get("area_m2") or 0
        link_kg_m2 = (links["shear_kg"] + links["punching_kg"]) / model_area if model_area else 0.0
        row.basis.append(
            f"{width:.1f} m wide × {h * 1000:.0f} mm, {st.get('kg_per_m2') or 0:.0f} kg/m² of bars"
            + (f" + {link_kg_m2:.0f} kg/m² of shear and punching links" if link_kg_m2 >= 0.5 else "")
        )
        row.concrete_m3 = area_m2 * h * (st.get("concrete_share") or 1.0)
        if (st.get("concrete_share") or 1.0) < 1:
            row.basis.append(f"voids take {100 * (1 - st['concrete_share']):.1f}% of the concrete")
        row.rebar_t = area_m2 * ((st.get("kg_per_m2") or 0) + link_kg_m2) / 1000
        row.add(_price(prices.concrete_slab, row.concrete_m3), "slab concrete price")
        row.add(_price(prices.rebar, row.rebar_t), "reinforcement price")
        rows.append(row)

    out_rows = [r.to_dict(berth) for r in rows]
    complete = all(r["cost"] is not None for r in out_rows)
    total = sum(r["cost"] or 0 for r in out_rows)
    totals = {
        "concrete_m3": round(sum(r["concrete_m3"] for r in out_rows), 1),
        "rebar_t": round(sum(r["rebar_t"] for r in out_rows), 2),
        "steel_t": round(sum(r["steel_t"] for r in out_rows), 2),
        "cost": _money(total),
        "complete": complete,
    }
    per_m = {k: round(v / berth, 3) for k, v in totals.items() if k in ("concrete_m3", "rebar_t", "steel_t")}
    per_m["cost"] = _money(total / berth)
    missing = sorted({m for r in out_rows for m in r["missing"] if m})
    if missing:
        notes.append("Prices or inputs missing, so the totals leave them out: " + ", ".join(missing) + ".")
    return {
        "section": section.name,
        "section_id": section.id,
        "berth_length_m": round(berth, 2),
        "model_length_m": round(L, 2) if L else None,
        "scale": round(per_berth, 3) if per_berth else None,
        "rows": out_rows,
        "totals": totals,
        "per_m": per_m,
        "notes": notes,
        "run_at": results.get("run_at"),
        "changed": results.get("changed"),
    }


def cost_project(project: Project, results_by_section: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    """Every section's costing, and the project's total."""
    sections = []
    for s in project.sections:
        res = results_by_section.get(s.id)
        if res is None:
            sections.append(
                {"section": s.name, "section_id": s.id, "rows": [], "notes": ["Not designed yet."]}
            )
            continue
        sections.append(cost_section(project, s, res))
    costed = [s for s in sections if s.get("totals")]
    total = {
        "berth_length_m": round(sum(s["berth_length_m"] for s in costed), 2),
        "cost": _money(sum(s["totals"]["cost"] or 0 for s in costed)),
        "concrete_m3": round(sum(s["totals"]["concrete_m3"] for s in costed), 1),
        "rebar_t": round(sum(s["totals"]["rebar_t"] for s in costed), 2),
        "steel_t": round(sum(s["totals"]["steel_t"] for s in costed), 2),
        "complete": all(s["totals"]["complete"] for s in costed),
    }
    return {"currency": project.prices.currency, "sections": sections, "total": total}
