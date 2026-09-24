"""The quay furniture calculation (Word, PDF or Excel through ``report``'s renderers), its plan as a
picture, and its drawings (the berth plan and each item's bolts) in the drawings format."""

from __future__ import annotations

import io
import math
from typing import Any

from PIL import Image, ImageDraw

from . import clock
from .figures import INK, MUTED, _font
from .furniture import LABEL
from .project import Project, Section
from .report import Report

COL = {
    "fenders": (40, 90, 170),
    "bollards": (200, 120, 20),
    "ladders": (40, 140, 70),
    "storm_pins": (120, 70, 150),
    "crane_stoppers": (200, 40, 40),
}
PILE = (170, 176, 186)
BEAM = (238, 238, 232)
RED = (200, 40, 40)


def _ok(p: Any) -> str:
    return "passes" if p else "fails"


def plan_png(
    res: dict[str, Any], per_row: float = 60.0, start: float = 0.0, end: float | None = None
) -> bytes:
    """The berth in plan from ``start`` to ``end`` (m), ``per_row`` metres to a row: beams, pile
    heads, rails, joints and items."""
    lay = res["layout"]
    L = min(res["berth_length_m"], end) if end is not None else res["berth_length_m"]
    fr = res["frame"]
    depth = max(
        (fr["rear_beam"]["centre_m"] + fr["rear_beam"]["width_mm"] / 2000) if fr.get("rear_beam") else 0,
        fr["front_beam"]["width_mm"] / 1000 + 1,
    )
    rows = max(1, int(-(-(L - start) // per_row)))
    k = 22.0  # px per m
    w = int(per_row * k) + 80
    row_h = int(depth * k) + 70
    pro = res.get("protrusion")
    out_m = pro["projection"] / 1000 if pro else 0.0  # the fender blocks stand out from the face
    row_h += int(out_m * k)
    img = Image.new("RGB", (w, rows * row_h + 40), "white")
    d = ImageDraw.Draw(img)
    f = _font(13)
    for r in range(rows):
        s0 = start + r * per_row
        top = 20 + r * row_h + out_m * k

        def P(s: float, dd: float, s0: float = s0, top: float = top) -> tuple[float, float]:
            return 40 + (s - s0) * k, top + dd * k

        e = min(L, s0 + per_row)
        d.rectangle([P(s0, 0), P(e, fr["front_beam"]["width_mm"] / 1000)], fill=BEAM, outline=MUTED)
        if fr.get("rear_beam"):
            rb = fr["rear_beam"]
            d.rectangle(
                [P(s0, rb["centre_m"] - rb["width_mm"] / 2000), P(e, rb["centre_m"] + rb["width_mm"] / 2000)],
                fill=BEAM,
                outline=MUTED,
            )
        d.line([P(s0, 0), P(e, 0)], fill=INK, width=2)
        for h in lay["pile_heads"]:
            if s0 - 2 <= h["s"] <= e + 2:
                x, y = P(h["s"], h["d"])
                rr = h["r"] * k
                d.ellipse([x - rr, y - rr, x + rr, y + rr], outline=PILE, width=1)
        for rl in lay["rails"]:
            d.line([P(s0, rl["across_m"]), P(e, rl["across_m"])], fill=(90, 90, 90), width=3)
        for j in lay["joints_m"]:
            if s0 <= j <= e:
                d.line([P(j, -0.3), P(j, depth + 0.3)], fill=RED, width=1)
        if pro:
            for it in lay["items"].get("fenders", []):
                if s0 - 2 <= it["s_m"] <= e + 2:
                    half = pro["length"] / 2000
                    d.rectangle(
                        [P(it["s_m"] - half, -out_m), P(it["s_m"] + half, 0)], fill=BEAM, outline=MUTED, width=1
                    )
        for kind, items in lay["items"].items():
            c = COL.get(kind, INK)
            for it in items:
                if not (s0 - 1 <= it["s_m"] <= e + 1):
                    continue
                a0, a1 = it["across_m"]
                if it["plane"] == "face":
                    a0, a1 = -0.6, -0.1  # drawn just off the face
                box = [P(it["from_m"], a0), P(it["to_m"], a1)]
                d.rectangle(box, outline=RED if it["status"] == "clash" else c, width=2, fill=None)
                d.text((box[0][0], box[1][1] + 1), it["label"].split()[-1], fill=c, font=f)
        for m in range(int(s0), int(e) + 1, 10):
            x, y = P(m, depth + 0.2)
            d.text((x, y), f"{m} m", fill=MUTED, font=f)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _anchor_block(r: Report, a: dict[str, Any]) -> None:
    r.kv(
        [
            (
                "Bolts",
                f"{a['bolts']} × {a['bolt']}, As {a['As_mm2']:g} mm², fyk / fuk {a['fyk_MPa']:g} / {a['fuk_MPa']:g} MPa",
            ),
            ("Embedment and head", f"hef {a['hef_mm']:g} mm, head Ø{a['head_mm']:g} mm"),
            (
                "Edges",
                f"{a['edges_mm']['+v']:g} mm and {a['edges_mm']['-v']:g} mm, member {a['member_mm']:g} mm, {a['concrete']}",
            ),
            (
                "Design loads",
                f"N {a['loads']['N_kN']:g} kN, Vu {a['loads']['Vu_kN']:g} kN, Vv {a['loads']['Vv_kN']:g} kN, "
                f"Mu {a['loads']['Mu_kNm']:g} kNm, Mv {a['loads']['Mv_kNm']:g} kNm",
            ),
            (
                "Bolt tension",
                f"most loaded {a['N_max_kN']:g} kN, group {a['N_group_kN']:g} kN; shear {a['V_bolt_kN']:g} kN per bolt",
            ),
            ("γMs tension / shear", f"{a['gamma_Ms_tension']:g} / {a['gamma_Ms_shear']:g}; γMc 1.5"),
        ]
    )
    r.table(
        ["Check", "Clause", "Ed (kN)", "Rd (kN)", "Utilisation", "Result"],
        [
            [
                c["check"],
                c["clause"],
                c.get("Ed_kN", "–"),
                c.get("Rd_kN", "–"),
                c["utilisation"],
                _ok(c["passed"]),
            ]
            for c in a["checks"]
        ],
    )
    re_ = a["reinforcement"]
    lines = []
    if "tension" in re_:
        t = re_["tension"]
        lines.append(
            f"Anchor reinforcement for the tension: {t['text']}"
            + (
                f"; NRd,re {t['NRd_re_kN']:g} kN, anchorage l1 {t['l1_mm']:g} mm gives {t['NRd_a_kN']:g} kN; utilisation {t['utilisation']}"
                if t.get("utilisation") is not None
                else ""
            )
            + " (EN 1992-4 7.2.1.9)."
        )
    if "shear" in re_:
        s = re_["shear"]
        lines.append(
            f"Anchor reinforcement for the shear: {s['text']}; VRd,re {s['VRd_re_kN']:g} kN, utilisation {s['utilisation']} (EN 1992-4 7.2.2.6)."
        )
    lines.append(f"Splitting reinforcement: {re_['splitting_mm2']:g} mm² (EN 1992-4 7.2.1.7(2)b).")
    r.bullets(lines)


def build_calc(project: Project, section: Section, res: dict[str, Any]) -> Report:
    r = Report(
        f"{project.info.name}: quay furniture",
        f"{section.name}, {clock.now().strftime('%d %b %Y %H:%M')}",
    )
    lay = res["layout"]
    r.h(1, "The berth")
    r.kv(
        [
            ("Berth length", f"{res['berth_length_m']:g} m ({res['berth_length_from']})"),
            ("Cope level", f"{res['cope_m']:g} m"),
            (
                "Front beam",
                f"{res['frame']['front_beam']['width_mm']:g} × {res['frame']['front_beam']['depth_mm']:g} mm, {res['frame']['front_beam']['concrete']}",
            ),
            (
                "Expansion joints",
                ", ".join(f"{j:g} m" for j in lay["joints_m"]) + f" ({lay['joints_from']})"
                if lay["joints_m"]
                else "none",
            ),
            (
                "Crane rails",
                ", ".join(f"{x['tag']} {x['across_m']:g} m from the face" for x in lay["rails"]) or "none",
            ),
        ]
    )
    r.h(2, "Arrangement")
    L = res["berth_length_m"]
    page = 180.0  # three rows of 60 m to a picture, so each fits a page
    starts = [k * page for k in range(max(1, math.ceil(L / page)))]
    for a in starts:
        b = min(a + page, L)
        r.image(
            plan_png(res, start=a, end=b),
            f"The berth in plan, {a:g} to {b:g} m: fenders and ladders on the face (drawn just off it), bollards, "
            "stoppers and storm pins on the beams, pile heads grey, joints red. Items in red could not be cleared.",
        )
    rows = []
    for items in lay["items"].values():
        for it in items:
            rows.append(
                [it["label"], it["tag"], it["s_m"], it["moved_m"], it["status"], "; ".join(it["clashes"])]
            )
    r.table(["Item", "Where", "At (m)", "Moved (m)", "Status", "Clashes with"], rows)
    r.table(
        ["Item", "Number"],
        [[LABEL.get(k, k) + "s", n] for k, n in lay["counts"].items()],
    )
    r.h(1, "Design")
    r.table(
        ["Item", "Utilisation", "Result"],
        [[i["title"], i["utilisation"], _ok(i["passed"])] for i in res["items"]],
    )
    for i in res["items"]:
        r.h(2, i["title"])
        r.table(
            ["Check", "Utilisation", "Result"],
            [[p["part"], p["utilisation"], _ok(p["passed"])] for p in i["parts"]],
        )
        if i.get("loads"):
            r.kv([(k.replace("_", " "), v) for k, v in i["loads"].items()])
        for key, title in (
            ("bearing", "Bearing on the concrete"),
            ("bearing_along", "Socket bearing, along"),
            ("bearing_across", "Socket bearing, across"),
        ):
            b = i.get(key)
            if b:
                r.h(3, title)
                r.kv(
                    [
                        (
                            "F Ed",
                            f"{b['F_Ed_kN']:g} kN on {b['loaded_mm'][0]} × {b['loaded_mm'][1]} mm, spreading to {b['spread_mm'][0]} × {b['spread_mm'][1]} mm",
                        ),
                        ("FRdu", f"{b['FRdu_kN']:g} kN ({b['clause']})"),
                        (
                            "Bursting",
                            f"T {b['bursting_T_kN']:g} kN, {b['bursting_As_mm2']:g} mm²: {b['bursting_bars']}",
                        ),
                    ]
                )
        if i.get("loops"):
            lp = i["loops"]
            r.p(
                f"Loops round the socket: {lp['bars']}, {lp['As_prov_mm2']:g} mm² for {lp['As_req_mm2']:g} mm² (F / fyd)."
            )
        if i["item"].startswith("crane_rails"):
            r.kv(
                [
                    (
                        "Rail",
                        f"{i['rail']['name']}: I {i['rail']['I']:g} cm⁴, W head {i['rail']['W_head_cm3']:g} cm³, foot {i['rail']['foot']:g} mm",
                    ),
                    ("β", f"{i['beta_per_m']:g} /m"),
                    (
                        "Moment",
                        f"{i['M_max_kNm']:g} kNm (hogging {i['M_min_kNm']:g}), σ {i['sigma_MPa']:g} MPa",
                    ),
                    ("Pad pressure", f"{i['pad_pressure_MPa']:g} MPa against {i['pad_limit_MPa']:g} MPa"),
                    (
                        "Lateral",
                        f"{i['lateral']['H_wheel_kN']:g} kN per wheel over {i['lateral']['clips_sharing']:g} clips: {i['lateral']['per_clip_kN']:g} kN each, uplift {i['lateral']['uplift_kN']:g} kN",
                    ),
                ]
            )
        if i["item"] == "tie_rods":
            r.kv(
                [
                    ("Areas after corrosion", f"Ag {i['Ag_mm2']:g} mm², As {i['As_mm2']:g} mm²"),
                    ("Ftg,Rd / Ftt,Rd", f"{i['Ftg_Rd_kN']:g} / {i['Ftt_Rd_kN']:g} kN"),
                    ("Ft,ser,Rd", f"{i['Ft_ser_Rd_kN']:g} kN"),
                ]
            )
        if i["item"] == "ladders":
            r.kv(
                [
                    ("Length", f"{i['length_m']:g} m, {i['rungs']} rungs"),
                    (
                        "Rung",
                        f"Ø{i['rung']['diameter_after_loss_mm']:g} after loss, M {i['rung']['M_Ed_kNm']:g} kNm, σ {i['rung']['sigma_MPa']:g} MPa, δ {i['rung']['deflection_mm']:g} mm",
                    ),
                    (
                        "Stringer",
                        f"{i['stringer']['section_after_loss']}, σ {i['stringer']['sigma_MPa']:g} MPa",
                    ),
                ]
            )
        for key, title in (
            ("section", "Section at the joint to the beam"),
            ("shear", "Short cantilever shear"),
            ("joint", "Joint to the beam (EN 1992-1-1 6.2.5)"),
            ("downstand", "Downstand below the beam's soffit"),
            ("beam", "Into the front beam: torsion and shear (extra to its own design)"),
            ("bars", "Bars"),
            ("quantities", "Quantities per block"),
            ("standoff", "Stand-off of the ship's side from the quay face"),
            ("reach", "Crane outreach"),
            ("legs", "Ship's flare and the crane's legs"),
        ):
            block = i.get(key)
            if isinstance(block, dict) and block:
                r.h(3, title)
                r.kv([(k.replace("_", " "), v) for k, v in block.items() if v not in (None, "")])
        for t in i.get("details") or []:
            r.h(3, t["title"])
            r.table(t["headers"], t["rows"])
        if i.get("anchors"):
            r.h(
                3,
                "Anchor bolts (EN 1992-4)" + (f", {i['governing_case']}" if i.get("governing_case") else ""),
            )
            _anchor_block(r, i["anchors"])
        for n in i.get("notes") or []:
            r.note(n)
    r.h(1, "Assumptions to confirm")
    r.bullets(res["assumptions"] + res["notes"])
    return r


# --- Drawings ----------------------------------------------------------------------------------------

LAYERS = {
    "furniture": {"cad_layer": "TRITON-FURNITURE", "revit_line_style": "TRITON-FURNITURE"},
    "anchors": {"cad_layer": "TRITON-ANCHORS", "revit_line_style": "TRITON-ANCHORS"},
    "concrete": {"cad_layer": "CONCRETE", "revit_line_style": "CONCRETE"},
    "zones": {"cad_layer": "ZONES", "revit_line_style": "ZONES"},
    "text": {"cad_layer": "TEXT", "revit_text_type": ""},
}


def views(res: dict[str, Any]) -> list[dict[str, Any]]:
    """The berth plan (1:200, mm) and each anchored item's bolt pattern (1:20), in the drawings format."""
    from .drawings import View
    from .furniture_inputs import Anchors  # noqa: F401 (the patterns come from the results)

    lay = res["layout"]
    L = res["berth_length_m"] * 1000
    fr = res["frame"]
    plan = View("Furniture plan", f"{res['section']}: quay furniture", 200, "Furniture")
    fw = fr["front_beam"]["width_mm"]
    plan.rect("concrete", (0, 0), (L, -fw))
    if fr.get("rear_beam"):
        rb = fr["rear_beam"]
        c = rb["centre_m"] * 1000
        plan.rect("concrete", (0, -(c - rb["width_mm"] / 2)), (L, -(c + rb["width_mm"] / 2)))
    for h in lay["pile_heads"]:
        plan.circle("zones", (h["s"] * 1000, -h["d"] * 1000), h["r"] * 1000)
    for rl in lay["rails"]:
        plan.line("furniture", (0, -rl["across_m"] * 1000), (L, -rl["across_m"] * 1000))
    for j in lay["joints_m"]:
        plan.line("zones", (j * 1000, 1000), (j * 1000, -fw - 1000))
    pro = res.get("protrusion")
    if pro:
        for it in lay["items"].get("fenders", []):
            s0 = it["s_m"] * 1000
            plan.rect("concrete", (s0 - pro["length"] / 2, pro["projection"]), (s0 + pro["length"] / 2, 0))
    for items in lay["items"].values():
        for it in items:
            a0, a1 = it["across_m"]
            if it["plane"] == "face":
                a0, a1 = -0.6, -0.1
            plan.rect("furniture", (it["from_m"] * 1000, -a0 * 1000), (it["to_m"] * 1000, -a1 * 1000))
            plan.text(
                (it["from_m"] * 1000, -a0 * 1000 + 300 if it["plane"] == "face" else -a1 * 1000 - 900),
                it["label"],
            )
    out = [plan.as_dict()]
    blk = next((i for i in res["items"] if i["item"] == "fender_blocks"), None)
    if blk:
        g = blk["geometry"]
        v = View("Fender protrusion", "Fender protrusion: cross-section through the front beam", 50, "Furniture")
        # Across the quay (sea to the left, x from the block's sea face), up from the cope (y = 0 at the cope).
        a, B = g["projection_mm"], g["beam_width_mm"]
        v.rect("concrete", (a, 0), (a + B, -g["beam_depth_mm"]))
        v.rect("concrete", (0, 0), (a, -g["depth_mm"]))
        c = g["cover_mm"] + 16
        v.line("furniture", (c, -c), (a + 1000, -c))  # the U-bars' top leg, on into the beam
        v.line("furniture", (c, -g["joined_depth_mm"] + c), (a + 1000, -g["joined_depth_mm"] + c))
        v.text((0, 400), f"Top ties: {blk['bars']['top_ties']}")
        v.text((0, 250), f"Links: {blk['bars']['links']}")
        v.text((0, -g["depth_mm"] - 300), f"Faces: {blk['bars']['face_mesh']}")
        out.append(v.as_dict())
    for i in res["items"]:
        a = i.get("anchors")
        pts = a.get("positions") if a else None
        if not pts:
            continue
        v = View(
            f"{i['title']} bolts",
            f"{i['title']}: {a['bolts']} × {a['bolt']}, hef {a['hef_mm']:g} mm",
            20,
            "Furniture",
        )
        for u, w in pts:
            v.circle("anchors", (u, w), a["head_mm"] / 2)
            v.circle("anchors", (u, w), float(a["bolt"].split()[0][1:]) / 2)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        v.rect(
            "concrete",
            (min(xs) - 300, min(ys) - a["edges_mm"]["-v"]),
            (max(xs) + 300, max(ys) + a["edges_mm"]["+v"]),
        )
        v.text((min(xs) - 300, max(ys) + a["edges_mm"]["+v"] + 100), i["title"])
        out.append(v.as_dict())
    return out
