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
top and bottom faces per metre. ``level_m`` is the plate's level in the model.

Slabs (``slabs``): per face and direction, the basic mesh and each zone of
added bars (a plan rectangle), layer by layer with the distance of each layer
from its face (mm), and the shear link zones.

Coordinates stay in the Plaxis model system; the script maps them to the
Revit project base point.
"""

from __future__ import annotations

from typing import Any

FORMAT = "triton.pile-cages/1"


def pile_cages(project_name: str, results: dict[str, Any], section: str = "") -> dict[str, Any]:
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
    }


def _slab(d: dict[str, Any]) -> dict[str, Any]:
    faces = []
    for key, lay in (d.get("layers") or {}).items():
        face, direction = key.split("_")
        faces.append(
            {
                "face": face,
                "bars_along": direction.upper(),
                "cover_mm": lay.get("cover_mm"),
                "mesh": {
                    "diameter_mm": (lay.get("basic") or {}).get("phi"),
                    "spacing_mm": (lay.get("basic") or {}).get("spacing_mm"),
                    "layers": lay.get("mesh_bar_layers") or [],
                },
                "zones": [
                    {
                        "x_m": z["x"],
                        "y_m": z["y"],
                        "label": z.get("label"),
                        "layers": z.get("bar_layers") or [],
                    }
                    for z in lay.get("zones") or []
                ],
            }
        )
    return {
        "element": d["element"],
        "thickness_mm": d.get("thickness_mm"),
        "level_m": d.get("level_m"),
        "box_m": d.get("box"),
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
