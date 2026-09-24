"""Sheet pile wall design to EN 1993-5, as ArcelorMittal Durability 4.2.1 checks it.

Durability takes a few load levels per wall; Triton checks every Plaxis result instead
(every node of every ULS combination below the wall's top level, points inside the combi wall
king piles left out), each with its own concurrent actions:

    M = |M_11| + |N| e          vertical bending, kNm/m (Durability adds N e to M)
    V = |Q_13| (or |Q_23|)      the shear chosen on the element
    N = −N_1                    compression positive (Plaxis gives compression negative)

and with the corrosion loss (front + back) of the zone the point lies in. M_22, the horizontal
bending, is not a sheet pile action and is not checked.

Plaxis sometimes gives axial forces or shears a sheet pile does not really carry (the plate
smears the wall). For each combination, or all of them, N or V can be left out; the result is
then given twice: with every action (as Plaxis) and with the actions left out (as designed).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from ..importer import SheetData
from ..materials import SHEET_PILE_GRADES
from ..project import SheetPileInput
from .governing import uls_frame
from .sheet_piles import (
    CHECK_TITLES,
    CHECKS,
    SECTION_NAMES,
    Options,
    check,
    normalise,
    reduced,
    section,
    web_angle,
)

ALL = "All combinations"


def loss_at(z: np.ndarray, wall: SheetPileInput) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Front and back loss (mm) and zone number (1-based) at levels ``z``. A zone runs from the one
    above (or the top of the wall) down to its bottom level; points below the last zone take it."""
    zones = wall.corrosion_zones
    if not zones:
        zero = np.zeros(len(z))
        return zero, zero, np.ones(len(z), int)
    bottoms = np.array([zz.bottom_level for zz in zones])
    idx = np.searchsorted(-bottoms, -np.asarray(z, float) - 1e-6, side="left")
    idx = np.minimum(idx, len(zones) - 1)
    front = np.array([zz.front for zz in zones])[idx]
    back = np.array([zz.back for zz in zones])[idx]
    return front, back, idx + 1


def class_floor(class_from: str, sec, flange: float | None) -> int:
    """catalogue: never better than the catalogue's class; flange: from b / tf / ε only; auto:
    flange where the real flange width is known (given, or held for the section), else catalogue."""
    known = flange is not None or sec.b is not None
    use_catalogue = class_from == "catalogue" or (class_from == "auto" and not known)
    return sec.catalogue_class if use_catalogue else 1


def _options(wall: SheetPileInput, fy: float, length: float) -> tuple[Options, str]:
    name = normalise(wall.section_name)
    sec = section(name)
    l_b = wall.buckling_length or round(0.7 * length, 2)
    return (
        Options(
            fy=fy,
            gamma_m0=wall.gamma_m0,
            gamma_m1=wall.gamma_m1,
            buckling_length=max(l_b, 0.1),
            wel_only=wall.use_wel_only,
            class_floor=class_floor(wall.class_from, sec, wall.flange_width),
            head=wall.differential_head,
            welded=wall.welded_interlocks,
            flange=wall.flange_width,
            angle=wall.web_angle,
        ),
        name,
    )


def _frame(sheets: dict[str, SheetData], wall: SheetPileInput, king_piles) -> pd.DataFrame:
    f = uls_frame(sheets)
    if f.empty or "M_11" not in f.columns:
        return pd.DataFrame()
    for x, y, r in king_piles:
        f = f[np.hypot(f["X"] - x, f["Y"] - y) >= r - 1e-6]
    keep = ["combination", "Node", "X", "Y", "Z", "M_11", "N_1", wall.shear]
    f = f[[c for c in dict.fromkeys(keep) if c in f.columns]].copy()
    for c in ("N_1", wall.shear):
        if c not in f.columns:
            f[c] = 0.0
    f = f.dropna(subset=["Z", "M_11"])
    return f.drop_duplicates(subset=[c for c in ("combination", "Node", "X", "Y", "Z") if c in f.columns])


def _ignored(wall: SheetPileInput, combos: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    n = np.zeros(len(combos), bool)
    q = np.zeros(len(combos), bool)
    for rule in wall.ignore:
        hit = (
            np.ones(len(combos), bool)
            if rule.combination in ("", ALL)
            else (combos == rule.combination).to_numpy()
        )
        n |= hit & rule.ignore_n
        q |= hit & rule.ignore_q
    return n, q


def _row(f: pd.DataFrame, i: int, res: dict[str, np.ndarray], M, V, N, loss, zone) -> dict[str, Any]:
    r = f.iloc[i]
    checks = {k: (None if np.isnan(res[k][i]) else round(float(res[k][i]), 3)) for k in CHECKS}
    return {
        "z": round(float(r["Z"]), 2),
        "node": int(r["Node"]) if "Node" in f.columns and pd.notna(r["Node"]) else None,
        "combination": str(r["combination"]),
        "zone": int(zone[i]),
        "M": round(float(M[i]), 1),
        "V": round(float(V[i]), 1),
        "N": round(float(N[i]), 1),
        "loss": round(float(loss[i]), 2),
        "uf": round(float(res["uf"][i]), 3),
        "governs": CHECKS[int(res["governs"][i])],
        "checks": checks,
        "values": {
            k: (None if np.isnan(v := float(res[k][i])) else round(v, 3 if k in ("chi", "rho_p") else 1))
            for k in (
                "class",
                "fy_used",
                "rho_p",
                "W",
                "Mc",
                "Mv",
                "Vpl",
                "Vb",
                "c_tw_eps",
                "Npl",
                "Ncr",
                "chi",
                "Mn",
                "slender",
                "tf",
                "tw",
                "h",
                "area",
                "inertia",
                "wel",
                "wpl",
                "av",
            )
        },
    }


def _pass(f, M, V, N, loss, zone, name, opts) -> dict[str, Any]:
    res = check(name, opts, M, V, N, loss)
    uf = res["uf"]
    i = int(np.argmax(uf))
    per_zone = []
    for k in sorted(set(zone.tolist())):
        idx = np.flatnonzero(zone == k)
        j = int(idx[np.argmax(uf[idx])])
        per_zone.append(_row(f, j, res, M, V, N, loss, zone))
    per_check = {}
    for c in CHECKS:
        vals = np.nan_to_num(res[c], nan=-1.0)
        j = int(np.argmax(vals))
        per_check[c] = _row(f, j, res, M, V, N, loss, zone) if vals[j] >= 0 else None
    # Utilisation profile down the wall: the largest Uf at each level (to 0.1 m).
    lv = np.round(f["Z"].to_numpy(float), 1)
    prof = pd.DataFrame({"z": lv, "uf": uf}).groupby("z")["uf"].max().sort_index(ascending=False)
    return {
        "uf": round(float(uf[i]), 3),
        "ok": bool(uf[i] <= 1.0 + 1e-9),
        "governing": _row(f, i, res, M, V, N, loss, zone),
        "zones": per_zone,
        "checks": per_check,
        "profile": [[float(z), round(float(u), 3)] for z, u in prof.items()],
        "_uf": uf,
    }


def summary_table(f, M, V, N, loss, opts: Options, class_from: str = "auto") -> list[dict[str, Any]]:
    """Durability's Uf summary: the wall's largest Uf for every AZ section and grade, with the
    actions as designed and the element's corrosion, settings and buckling length."""
    rows = []
    for name in SECTION_NAMES:
        s = section(name)
        cells = {}
        for grade, fy in SHEET_PILE_GRADES.items():
            o = Options(**{**opts.__dict__, "fy": fy, "flange": None, "angle": None})
            o.class_floor = class_floor(class_from, s, None)
            if np.any(loss >= min(s.tf, s.tw)):
                cells[grade] = None
                continue
            cells[grade] = round(float(check(name, o, M, V, N, loss)["uf"].max()), 2)
        rows.append({"section": name, "mass": s.mass, "uf": cells})
    return rows


def design_spw(
    name: str,
    wall: SheetPileInput,
    fy: float,
    sheets: dict[str, SheetData],
    king_piles: list[tuple[float, float, float]] = (),
    uf_summary: bool = True,
) -> dict[str, Any] | None:
    f = _frame(sheets, wall, king_piles)
    if f.empty:
        return None
    z = f["Z"].to_numpy(float)
    top = wall.top_level if wall.top_level is not None else float(z.max())
    firm = wall.firm_soil_level if wall.firm_soil_level is not None else float(z.min())
    length = max(top - firm, 0.1)
    opts, sec_name = _options(wall, fy, length)
    sec = section(sec_name)
    front, back, zone = loss_at(z, wall)
    loss = front + back
    notes: list[str] = []
    too_much = loss >= min(sec.tf, sec.tw)
    if too_much.any():
        worst = float(loss.max())
        return {
            "error": f"The corrosion loss ({worst:g} mm, front + back) eats the whole {sec_name} "
            f"({sec.tf:g} mm flange, {sec.tw:g} mm web). Choose a thicker section or check the zones."
        }
    e = wall.eccentricity / 1000
    m11 = f["M_11"].to_numpy(float)
    n1 = f["N_1"].to_numpy(float)
    q = f[wall.shear].to_numpy(float)
    N_all = -n1  # compression positive
    M_all = np.abs(m11) + np.abs(N_all) * e
    V_all = np.abs(q)
    ign_n, ign_q = _ignored(wall, f["combination"])
    N_des = np.where(ign_n, 0.0, N_all)
    M_des = np.abs(m11) + np.abs(N_des) * e
    V_des = np.where(ign_q, 0.0, V_all)
    as_plaxis = _pass(f, M_all, V_all, N_all, loss, zone, sec_name, opts)
    adjusted = ign_n.any() or ign_q.any()
    designed = _pass(f, M_des, V_des, N_des, loss, zone, sec_name, opts) if adjusted else as_plaxis
    uf_p, uf_d = as_plaxis.pop("_uf"), designed.pop("_uf", None)
    if not wall.buckling_length and (N_des > 0).any():
        where = (
            f"the firm soil level {firm:g} m"
            if wall.firm_soil_level is not None
            else f"the toe {firm:g} m (firm soil level not given)"
        )
        notes.append(
            f"Buckling length assumed: 0.7 L as the office's combi sheet, L from the top {top:g} m to "
            f"{where}: l = 0.7 × {length:.2f} = {opts.buckling_length:g} m. It only matters where N is "
            "checked; give it, or the firm soil level, on the element (EN 1993-5 Figure 5.8)."
        )
    below = (
        z < wall.corrosion_zones[-1].bottom_level - 1e-6 if wall.corrosion_zones else np.zeros(len(z), bool)
    )
    if below.any():
        notes.append(
            f"{int(below.sum())} results lie below the last corrosion zone "
            f"({wall.corrosion_zones[-1].bottom_level:g} m); they take its losses."
        )
    if (N_all < 0).any():
        notes.append(
            "Where N_1 is tension, the sheet pile is checked for N with bending (EN 1993-5 5.2.3 (9)) "
            "and not for buckling."
        )
    base = reduced(sec_name, 0.0, wall.flange_width, wall.web_angle)
    bf = float(base["b"])
    rules = [
        {
            "combination": r.combination or ALL,
            "ignore_n": r.ignore_n,
            "ignore_q": r.ignore_q,
            "rows": int(
                (
                    np.ones(len(f), bool)
                    if r.combination in ("", ALL)
                    else (f["combination"] == r.combination).to_numpy()
                ).sum()
            ),
        }
        for r in wall.ignore
        if r.ignore_n or r.ignore_q
    ]
    combos = sorted(f["combination"].astype(str).unique())
    by_combo = []
    for c in combos:
        m = (f["combination"] == c).to_numpy()
        by_combo.append(
            {
                "combination": c,
                "uf_plaxis": round(float(uf_p[m].max()), 3),
                "uf": round(float((uf_d if uf_d is not None else uf_p)[m].max()), 3),
                "ignore_n": bool(ign_n[m].any()),
                "ignore_q": bool(ign_q[m].any()),
                "max_N": round(float(N_all[m].max()), 1),
                "max_V": round(float(V_all[m].max()), 1),
                "max_M": round(float(np.abs(m11[m]).max()), 1),
            }
        )
    out = {
        "section": sec_name,
        "steel": {"fy": fy},
        "properties": {
            "width": sec.width,
            "h": sec.h,
            "tf": sec.tf,
            "tw": sec.tw,
            "area": sec.area,
            "inertia": sec.inertia,
            "wel": sec.wel,
            "wpl": sec.wpl,
            "mass": sec.mass,
            "catalogue_class": sec.catalogue_class,
            "flange": round(bf, 1),
            "flange_given": wall.flange_width is not None,
            "flange_known": wall.flange_width is None and sec.b is not None,
            "angle": round(wall.web_angle or web_angle(sec_name, wall.flange_width), 1),
            "angle_given": wall.web_angle is not None,
            "c": round(float(base["c"]), 1),
            "av": round(float(base["av"]), 1),
        },
        "settings": {
            "gamma_m0": opts.gamma_m0,
            "gamma_m1": opts.gamma_m1,
            "buckling_length": opts.buckling_length,
            "buckling_length_given": bool(wall.buckling_length),
            "buckling_from": [round(top, 2), round(firm, 2), wall.firm_soil_level is not None],
            "eccentricity_mm": wall.eccentricity,
            "wel_only": opts.wel_only,
            "class_from": wall.class_from,
            "head": opts.head,
            "welded": opts.welded,
            "shear": wall.shear,
        },
        "zones": [
            {
                "zone": k,
                "bottom": zz.bottom_level,
                "front": zz.front,
                "back": zz.back,
                "total": zz.front + zz.back,
            }
            for k, zz in enumerate(wall.corrosion_zones, 1)
        ],
        "top": round(float(z.max()), 2),
        "toe": round(float(z.min()), 2),
        "points": int(len(f)),
        "left_out_king_piles": len(king_piles),
        "ignored": rules,
        "adjusted": bool(adjusted),
        "as_plaxis": as_plaxis,
        "designed": designed,
        "uf": designed["uf"],
        "ok": designed["ok"],
        "by_combination": by_combo,
        "check_titles": CHECK_TITLES,
        "notes": notes,
    }
    if uf_summary:
        out["uf_summary"] = {
            "grades": list(SHEET_PILE_GRADES),
            "rows": summary_table(f, M_des, V_des, N_des, loss, opts, wall.class_from),
        }
    return out


def fmt_uf(u: float | None) -> str:
    return "–" if u is None or (isinstance(u, float) and math.isnan(u)) else f"{u:.2f}"
