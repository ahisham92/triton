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
