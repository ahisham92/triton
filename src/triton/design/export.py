"""Reinforcement data for drawing tools (a Revit / Dynamo script reads this file).

Everything a script needs to place the bars is spelled out, so it does not
have to repeat any design logic:

* each pile of each pile element, and the concrete infill of each combi wall
  king pile (``part`` "infill"), at its Plaxis X, Y (m), from the head level
  down to the toe level (m, same datum as the model);
* each bar run: its cage, row by row, with the radius of the bar circle (mm),
  the angle of the first bar (degrees, anticlockwise from the model X axis;
  the other bars follow at equal steps) and the top and bottom level of the
  bars, including the lap below the run.

Beams (``beams``): the straight cage between the beam's ends along global X or
Y, each longitudinal bar at its place in the section (y across from the
centreline, z up from mid-depth, mm), the links, and the transverse bars of the
top and bottom faces per metre. ``level_m`` is the plate's level in the model. Rooms cut into the
beam (``rooms``) give their hole in the section and the bars over their length.

Slabs (``slabs``): per face and direction, the basic mesh and each zone of
added bars (a plan rectangle), layer by layer with the distance of each layer
from its face (mm), and the shear link zones.

Coordinates stay in the Plaxis model system; the script maps them to the
Revit project base point.
"""

from __future__ import annotations

from typing import Any

from ..alignment import named_parts

FORMAT = "triton.pile-cages/1"


def pile_cages(project_name: str, results: dict[str, Any], section: str = "") -> dict[str, Any]:
    results = named_parts(results)  # a corner berth's parts by their own names
    piles = []
    cages = [(p, "pile") for p in results.get("piles", [])]
    cages += [(w["infill"], "infill") for w in results.get("combi_walls", [])]
    for p, part in cages:
        c = p.get("curtailment") or {}
        link = (p.get("shear") or {}).get("link_diameter_mm") or p["section"]["link_diameter_mm"]
        a = p.get("arrangement")
        if a is None:
            continue
        runs = c.get("runs") or [_single_run(p)]
        piles.append(
            {
                "element": p["element"],
                "part": part,
                "count": p.get("count", len(p.get("positions", [])) or 1),
                "diameter_mm": p["section"]["diameter_mm"],
                "cover_mm": p["section"]["cover_mm"],
                "link_diameter_mm": (p.get("shear") or {}).get("link_diameter_mm")
                or p["section"]["link_diameter_mm"],
                "head_level_m": p["section"].get("head_level_m"),
                "toe_level_m": p["section"].get("toe_level_m"),
                "positions": [{"x": x, "y": y} for x, y in p.get("positions", [])],
                "splice": c.get("splice", "lap"),
                "user_set": bool(p.get("user_set")),
                "runs": [
                    {
                        "top_m": r["top"],
                        "bottom_m": r["bottom"],
                        "label": r["cage"]["label"],
                        # Inner link rings (same size and pitch as the outer links), one per inner row.
                        "inner_link_hoops_mm": [
                            round(2 * (ring["radius"] + ring["diameter"] / 2 + link / 2), 1)
                            for ring in r["cage"]["rings"][1:]
                        ],
                        "rows": [
                            {
                                "count": ring["count"],
                                "diameter_mm": ring["diameter"],
                                "radius_mm": ring["radius"],
                                "first_bar_angle_deg": 0.0,
                                "bar_top_m": round(r["top"] + above, 3),
                                "bar_bottom_m": round(r["bottom"] - lap, 3),
                                "bar_length_m": length,
                            }
                            for ring, lap, length, above in zip(
                                r["cage"]["rings"],
                                r["lap_below_m"],
                                r["bar_lengths_m"],
                                r.get("above_head_m") or [0.0] * len(r["cage"]["rings"]),
                                strict=True,
                            )
                        ],
                    }
                    for r in runs
                ],
            }
        )
    return {
        "format": FORMAT,
        "project": project_name,
        "section": section,
        "run_at": results.get("run_at"),
        "piles": piles,
        "beams": [_beam(b) for b in results.get("beams", []) if b.get("cage")],
        "slabs": [_slab(d) for d in results.get("slabs", []) if d.get("layers")],
        "approach": [_approach(a) for a in results.get("approach_slabs", []) if a.get("bending")],
    }


def _approach(a: dict[str, Any]) -> dict[str, Any]:
    """The approach slab and its ledge: sizes and bars per metre, for the section drawing."""
    led = a.get("ledge") or {}
    return {
        "element": a["element"],
        "length_m": a.get("length_m"),
        "thickness_mm": a.get("thickness_mm"),
        "cover_top_mm": a.get("cover_top_mm"),
        "cover_bottom_mm": a.get("cover_bottom_mm"),
        "joint_mm": a.get("joint_mm"),
        "bottom": {k: (a["bending"].get("bottom") or {}).get(k) for k in ("bars", "phi", "spacing_mm")},
        "top": {k: (a["bending"].get("top") or {}).get(k) for k in ("bars", "phi", "spacing_mm")},
        "distribution": {f: (a.get("distribution") or {}).get(f, {}).get("bars") for f in ("bottom", "top")},
        "links": (a.get("shear") or {}).get("links_mm2_per_m2"),
        "links_zone_m": (a.get("shear") or {}).get("links_zone_m"),
        "ledge": {
            k: led.get(k)
            for k in (
                "projection_mm",
                "depth_mm",
                "top_below_beam_top_mm",
                "cover_mm",
                "bearing_width_mm",
                "bearing_thickness_mm",
                "edge_distance_mm",
            )
        }
        | {
            "tie": (led.get("tie") or {}).get("bars"),
            "tie_phi": (led.get("tie") or {}).get("phi"),
            "links": (led.get("links") or {}).get("bars"),
            "hanger": (led.get("hanger") or {}).get("bars"),
        },
    }


def _beam(b: dict[str, Any]) -> dict[str, Any]:
    cage = b["cage"]
    link = (b.get("shear") or {}).get("link") or {}
    trans = b.get("transverse") or {}
    return {
        "element": b["element"],
        "kind": b.get("kind"),
        "along": b.get("along"),
        "start_m": b.get("start_m"),
        "end_m": b.get("end_m"),
        "centre_m": b.get("centre_m"),
        "level_m": b.get("level_m"),
        "width_mm": b.get("width_mm"),
        "depth_mm": b.get("depth_mm"),
        "cover_mm": b.get("cover_mm"),
        "user_set": bool(b.get("user_set")),
        "label": cage.get("label"),
        "bars": [{"y_mm": y, "z_mm": z, "diameter_mm": phi} for y, z, phi in cage.get("bars") or []],
        "links": {
            "diameter_mm": link.get("phi"),
            "legs": link.get("legs"),
            "spacing_mm": link.get("spacing_mm"),
        }
        if link
        else None,
        "transverse": {
            face: {
                "diameter_mm": t.get("phi"),
                "spacing_mm": t.get("spacing_mm"),
                "layers": t.get("layers", 1),
            }
            for face, t in trans.items()
            if face in ("top", "bottom") and isinstance(t, dict)
        },
        "rooms": [_room(rm) for rm in b.get("rooms") or [] if rm.get("section")],
    }


def _room(rm: dict[str, Any]) -> dict[str, Any]:
    """A room cut into the beam: where it is, the hole in the section (y from, y to, z from, z to, mm)
    and every longitudinal bar over its length."""
    walls = {k: (v.get("link") or {}) for k, v in (rm.get("shear") or {}).items() if k in ("sea", "land")}
    fr = rm.get("frame") or {}
    return {
        "name": rm["name"],
        "start_m": rm["start_m"],
        "end_m": rm["end_m"],
        "void_mm": rm["section"]["void_mm"],
        "land_side": rm["section"]["land_side"],
        "bars": [{"y_mm": y, "z_mm": z, "diameter_mm": phi} for y, z, phi in rm["bars"]["all"]],
        "wall_links": {
            k: {"diameter_mm": v.get("phi"), "spacing_mm": v.get("spacing_mm")} for k, v in walls.items() if v
        },
        "wall_top_bars_past_ends_mm": (rm.get("corners") or {}).get("wall_top_bars_past_ends_mm"),
        "diagonals": (rm.get("corners") or {}).get("diagonals"),
        "floor_per_metre": {
            f: {
                "diameter_mm": (fr.get("floor") or {}).get(f, {}).get("phi"),
                "spacing_mm": (fr.get("floor") or {}).get(f, {}).get("spacing_mm"),
            }
            for f in ("top", "bottom")
        },
        "walls_per_metre": {
            "diameter_mm": (fr.get("walls") or {}).get("top", {}).get("phi"),
            "spacing_mm": (fr.get("walls") or {}).get("top", {}).get("spacing_mm"),
        },
    }


def _slab(d: dict[str, Any]) -> dict[str, Any]:
    h = d.get("thickness_mm") or 0

    def placed(face: str, rows: list[dict]) -> list[dict]:
        # Height of each layer above the soffit: the layers go inward from their mesh.
        return [
            {**r, "above_soffit_mm": r["from_face_mm"] if face == "bottom" else round(h - r["from_face_mm"])}
            for r in rows
            if "from_face_mm" in r
        ]

    strip_zones = _strip_zones(d)
    faces = []
    for key, lay in (d.get("layers") or {}).items():
        face, direction = key.split("_")
        # With column and field strips the designed bars are the strip design's rows (per station and
        # strip along the strips, per zone across them), not the cell zones.
        zones = strip_zones.get(key)
        if zones is None:
            zones = [
                {"x_m": z["x"], "y_m": z["y"], "label": z.get("label"), "layers": z.get("bar_layers") or []}
                for z in lay.get("zones") or []
            ]
        faces.append(
            {
                "face": face,
                "bars_along": direction.upper(),
                "cover_mm": lay.get("cover_mm"),
                "mesh": {
                    "diameter_mm": (lay.get("basic") or {}).get("phi"),
                    "spacing_mm": (lay.get("basic") or {}).get("spacing_mm"),
                    "layers": placed(face, lay.get("mesh_bar_layers") or []),
                },
                "zones": [{**z, "layers": placed(face, z["layers"])} for z in zones],
            }
        )
    return {
        "element": d["element"],
        "thickness_mm": d.get("thickness_mm"),
        "level_m": d.get("level_m"),
        "box_m": d.get("box"),
        "voids": _voids(d.get("voids")),
        "faces": faces,
        "links": [
            {
                "x_m": z["x"],
                "y_m": z["y"],
                "diameter_mm": z.get("phi"),
                "sx_mm": z.get("sx_mm"),
                "sy_mm": z.get("sy_mm"),
            }
            for z in (d.get("shear") or {}).get("links") or []
        ],
    }


def _strip_zones(d: dict[str, Any]) -> dict[str, list[dict]]:
    """Zones of additional bars per layer from the strip design's rows: along the strips, one rectangle
    per station and strip (column strips centred on the pile lines, field strips between them); across
    them, each zone row's rectangle. Layers the strips do not cover are left out (the cell zones stand)."""
    sd = d.get("strip_design")
    if not sd or not sd.get("rows"):
        return {}
    box = d.get("box") or {}
    along, sign, origin = sd["along"], sd["sign"], sd["origin"]
    across = "Y" if along == "X" else "X"
    lines = sorted(sd.get("lines") or [])
    cw, fw = sd.get("column_width_m") or 0.0, sd.get("field_width_m") or 0.0
    lo, hi = (box.get(across) or [min(lines, default=0.0), max(lines, default=0.0)])[:2]
    if len(lines) > 1:
        field = [((a + b) / 2 - fw / 2, (a + b) / 2 + fw / 2) for a, b in zip(lines, lines[1:], strict=False)]
    else:  # one line of piles: a field strip each side of it
        field = [(lo, lines[0] - cw / 2), (lines[0] + cw / 2, hi)] if lines else []
    strips = {"column": [(c - cw / 2, c + cw / 2) for c in lines], "field": field}
    out: dict[str, list[dict]] = {}
    for r in sd["rows"]:
        layer = r["layer"]
        out.setdefault(layer, [])
        if not r.get("additional_bars"):
            continue  # the mesh alone
        entry = {"label": r["bars"], "layers": r.get("bar_layers") or [], "row": r.get("key")}
        if r.get("zone"):
            (x0, x1), (y0, y1) = r["zone"]
            out[layer].append({"x_m": [x0, x1], "y_m": [y0, y1], **entry})
            continue
        a, b = sorted(origin + sign * s for s in r["station"])
        for t0, t1 in strips.get(r.get("strip"), []):
            t0, t1 = max(t0, lo), min(t1, hi)
            if t1 <= t0:
                continue
            rect = {along: [round(a, 3), round(b, 3)], across: [round(t0, 3), round(t1, 3)]}
            out[layer].append({"x_m": rect["X"], "y_m": rect["Y"], **entry})
    return out


def _voids(v: dict[str, Any] | None) -> dict[str, Any] | None:
    """Circular voids for drawing: each void's axis (global, m), its diameter and depth below the top."""
    if not v or not v.get("positions"):
        return None
    return {
        "diameter_mm": v["diameter_mm"],
        "centre_below_top_mm": v["centre_depth_mm"],
        "along": v["along"],
        "from_m": v["run"][0],
        "to_m": v["run"][1],
        "positions_m": v["positions"],
    }


def _single_run(p: dict[str, Any]) -> dict[str, Any]:
    """One run over the design length when the pile was not curtailed."""
    top, bottom = p["section"].get("head_level_m"), p["section"].get("toe_level_m")
    length = round(top - bottom, 2) if top is not None and bottom is not None else None
    rings = p["arrangement"]["rings"]
    above = p["section"].get("above_head_m") or [0.0] * len(rings)
    return {
        "top": top,
        "bottom": bottom,
        "cage": p["arrangement"],
        "lap_below_m": [0.0] * len(rings),
        "above_head_m": above,
        "bar_lengths_m": [None if length is None else round(length + a, 2) for a in above],
    }
