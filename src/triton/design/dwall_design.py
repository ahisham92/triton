"""Diaphragm wall design to EN 1992-1-1 from the Plaxis plate results, per metre run of wall.

A diaphragm wall is a vertical plate in Plaxis, as the sheet pile wall. Triton takes every node of every
combination below the wall's top level (results above it are in the capping beam):

    M_v = M_11 (vertical bending), N_v = −N_1 (compression +), V = |Q_13| (or |Q_23|)
    M_h = M_22 (horizontal bending), N_h = −N_2

Faces. The front (sea) face is in tension under M_11 of one sign: by default the sign of the largest
M_11, as a wall retaining soil bends towards the sea in its span; the element can set it. The other
sign puts the back (soil) face in tension. M_22 is read with the same sign.

Bars. Horizontal bars are the outer layer (cover to them), the vertical bars inside them, as a cage
lowered under bentonite. The wall is cut into zones of the shortest zone length from the top down; in
each zone, each face gets the cheapest vertical bars (Ø, spacing, layers) that carry

* ULS: the tension steel of a 1 m strip under N and M (``slabs.required_as``);
* the minimum steel: 9.2.1.1 max(0.26·fctm/fyk, 0.0013)·b·d and half of 9.6.2's 0.002·Ac per face;
* the QP crack width 7.3.4 at that face's limit.

The moment utilisation is then checked on the whole section (both faces' bars) with the N–M curve
(``rect.RectSection``). Horizontal bars per face: M_22 with N_2 at ULS and QP, and 9.6.3 at least 25% of
the vertical bars and 0.001·Ac over both faces.

Shear per metre, 6.2.2: VRd,c with ρl of the face in tension, σcp = N/Ac (tension reduces it, floored at
0), and v ≤ 0.5·ν·fcd·b·d. Where VEd > VRd,c, links to 6.2.3 with cot θ as large as VRd,max allows
(up to 2.5), at least ρw,min = 0.08·√fck/fyk (9.2.2(5)).

Neighbouring zones with the same bars are merged. Utilisation Uf is the largest of bending (N–M),
shear and each face's crack width over its limit.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from ..elements import CombinationType, combination_type
from ..importer import SheetData
from ..materials import REINFORCEMENT_GRADES, STEEL_DENSITY, concrete
from ..project import DesignSettings, DiaphragmWallInput
from .rect import Bars, RectSection, rect_laws
from .slabs import crack_widths, required_as
from .stop import checkpoint

E_S = 200_000.0
LAYER_PREMIUM = 0.25
LINK_SPACINGS = (300.0, 250.0, 200.0, 150.0, 125.0, 100.0)  # along the height, mm
LEG_SPACINGS = (500.0, 400.0, 300.0, 250.0, 200.0)  # across the wall's length, mm
END_CLEAR = 100.0  # mm from each end of a cage to its first vertical bar
FACES = ("front", "back")


def fmt_uf(u: float | None) -> str:
    if u is None or (isinstance(u, float) and math.isnan(u)):
        return "–"
    return "> 10, unsafe by far" if u > 10 else f"{u:.2f}"


def _columns(f: pd.DataFrame):
    return lambda c: f[c].to_numpy(float) if c in f.columns else np.zeros(len(f))


def _frame(
    sheets: dict[str, SheetData], wall: DiaphragmWallInput, top: float | None, qp: bool
) -> pd.DataFrame:
    parts = []
    for combo, sheet in sheets.items():
        if (combination_type(combo) is CombinationType.SLS_QP) != qp or sheet.frame.empty:
            continue
        f = sheet.frame
        if "M_11" not in f.columns or "Z" not in f.columns:
            continue
        if top is not None:
            f = f[f["Z"] <= top + 1e-6]
        f = f.drop_duplicates([c for c in ("Node", "X", "Y", "Z") if c in f.columns])
        col = _columns(f)
        parts.append(
            pd.DataFrame(
                {
                    "combination": combo,
                    "Node": f["Node"].to_numpy() if "Node" in f.columns else np.arange(len(f)),
                    "X": col("X"),
                    "Y": col("Y"),
                    "Z": col("Z"),
                    "M": col("M_11"),
                    "N": -col("N_1"),
                    "V": np.abs(col(wall.shear)),
                    "Mh": col("M_22"),
                    "Nh": -col("N_2"),
                }
            )
        )
    if not parts:
        return pd.DataFrame(columns=["combination", "Node", "X", "Y", "Z", "M", "N", "V", "Mh", "Nh"])
    return pd.concat(parts, ignore_index=True).dropna(subset=["Z", "M"])


def _options(bars: list[int], spacings: list[float], layers: int, clear: float) -> list[tuple]:
    """(mm²/m, Ø, spacing, layers), cheapest first (each layer under the first costs more to place)."""
    out = []
    for phi in bars:
        if phi < 12:
            continue
        for s in spacings:
            if s - phi < max(phi, clear) - 1e-9:
                continue
            for k in range(1, layers + 1):
                out.append((k * 1000 * math.pi * phi * phi / 4 / s, phi, float(s), k))
    return sorted(out, key=lambda o: (o[0] * (1 + LAYER_PREMIUM * (o[3] - 1)), -o[1]))


def label(o: tuple | None) -> str:
    if o is None:
        return "–"
    return f"Ø{o[1]} @ {o[2]:g}" + (f" in {o[3]} layers" if o[3] > 1 else "")


def _depth(h: float, cover: float, phi_h: float, o: tuple, clear: float) -> float:
    """d to the centroid of a face's vertical bars, inside the horizontal bars."""
    _, phi, _, k = o
    gap = phi + max(phi, clear)
    return h - cover - phi_h - phi / 2 - (k - 1) * gap / 2


class _Ctx:
    def __init__(self, wall: DiaphragmWallInput, settings: DesignSettings):
        pf = settings.partial_factors
        self.wall = wall
        self.h = wall.thickness
        self.cover = float(wall.cover if wall.cover is not None else settings.durability.covers.piles)
        self.conc = concrete(wall.concrete or settings.materials.concrete)
        self.fyk = REINFORCEMENT_GRADES[settings.reinforcement.grade]
        self.fyd = self.fyk / pf.gamma_s
        self.fcd = pf.alpha_cc * self.conc.fck / pf.gamma_c
        self.gamma_c = pf.gamma_c
        self.e_eff = self.conc.ecm / (1 + settings.cracking.creep_coefficient)
        self.clear = settings.reinforcement.slab_min_clear_spacing
        self.bars = settings.reinforcement.bar_diameters
        self.v_opts = _options(self.bars, wall.vertical_spacings, wall.max_layers, self.clear)
        self.h_opts = _options(self.bars, wall.horizontal_spacings, 1, self.clear)
        self.laws = rect_laws(self.conc.fck, pf.gamma_c, pf.gamma_s, pf.alpha_cc, self.fyk)
        self.limits = {"front": wall.crack_width_limit, "back": wall.crack_width_limit_back}

    def need(self, m: np.ndarray, n: np.ndarray, d: float) -> float:
        """ULS tension steel (mm²/m) of a face for its moments ``m`` (>= 0) with N."""
        if not len(m):
            return 0.0
        return float(required_as(m, n, self.h, d, self.conc.fck, self.fyd)[0].max())

    def as_min(self, d: float) -> float:
        return max(0.26 * self.conc.fctm / self.fyk, 0.0013) * 1000 * d

    def face(self, uls_m, uls_n, qp_m, qp_n, face: str, phi_h: float, floor: float = 0.0) -> dict:
        """The cheapest vertical bars of one face for the moments that put it in tension (kNm/m, >= 0)."""
        chosen = None
        for o in self.v_opts:
            d = _depth(self.h, self.cover, phi_h, o, self.clear)
            need = self.need(uls_m, uls_n, d)
            least = max(need, self.as_min(d), 0.0005 * 1000 * self.h, floor)
            if o[0] < least - 1e-6:
                continue
            wk = self._wk(qp_m, qp_n, o, d, phi_h)
            if wk > self.limits[face] + 1e-9:
                continue
            chosen = (o, d, need, least, wk)
            break
        ok = chosen is not None
        if chosen is None:  # nothing on the list is enough: the heaviest, flagged
            o = max(self.v_opts, key=lambda x: x[0])
            d = _depth(self.h, self.cover, phi_h, o, self.clear)
            need = self.need(uls_m, uls_n, d)
            chosen = (o, d, need, max(need, self.as_min(d)), self._wk(qp_m, qp_n, o, d, phi_h))
        o, d, need, least, wk = chosen
        return {
            "bars": label(o),
            "phi": o[1],
            "spacing": o[2],
            "layers": o[3],
            "area_mm2_per_m": round(o[0]),
            "required_mm2_per_m": round(need),
            "least_mm2_per_m": round(least),
            "d_mm": round(d),
            "wk_mm": round(wk, 3),
            "wk_limit_mm": self.limits[face],
            "found": ok,
            "_o": o,
        }

    def _wk(self, m, n, o, d, phi_h) -> float:
        if not len(m) or float(np.max(m)) <= 0:
            return 0.0
        c = self.cover + phi_h
        return float(np.max(crack_widths(m, n, o[0], o[1], o[2], self.h, d, c, self.conc, self.e_eff)))

    def horizontal(self, uls_m, uls_n, qp_m, qp_n, face: str, vertical_area: float) -> dict:
        floor = max(0.25 * vertical_area, 0.0005 * 1000 * self.h)
        chosen = None
        for o in self.h_opts:
            d = self.h - self.cover - o[1] / 2
            need = self.need(uls_m, uls_n, d)
            if o[0] < max(need, floor) - 1e-6:
                continue
            wk = self._wk(qp_m, qp_n, o, d, 0.0)
            if wk > self.limits[face] + 1e-9:
                continue
            chosen = (o, need, wk, True)
            break
        if chosen is None:
            o = max(self.h_opts, key=lambda x: x[0])
            chosen = (o, 0.0, 0.0, False)
        o, need, wk, ok = chosen
        return {
            "bars": label(o),
            "phi": o[1],
            "spacing": o[2],
            "area_mm2_per_m": round(o[0]),
            "required_mm2_per_m": round(max(need, floor)),
            "wk_mm": round(wk, 3),
            "found": ok,
        }

    def vrd_c(self, n: np.ndarray, rho: np.ndarray, d: np.ndarray) -> np.ndarray:
        """kN/m, 6.2.2(1) with σcp = N/Ac (tension negative), at least 0."""
        fck = self.conc.fck
        k = np.minimum(1 + np.sqrt(200 / d), 2.0)
        v_min = 0.035 * k**1.5 * math.sqrt(fck)
        sigma = np.minimum(n * 1e3 / (1000 * self.h), 0.2 * self.fcd)
        v = np.maximum(0.18 / self.gamma_c * k * (100 * np.minimum(rho, 0.02) * fck) ** (1 / 3), v_min)
        return np.maximum(v + 0.15 * sigma, 0.0) * 1000 * d / 1e3

    def links(self, v_ed: float, d: float) -> dict:
        """Links for v_ed (kN/m) on a 1 m strip, 6.2.3."""
        nu = 0.6 * (1 - self.conc.fck / 250)
        z = 0.9 * d
        fywd = self.fyd

        def vmax(cot):
            return 1000 * z * nu * self.fcd / (cot + 1 / cot) / 1e3

        cot = next((c for c in np.arange(2.5, 0.999, -0.05) if vmax(c) >= v_ed), None)
        if cot is None:
            return {"crushes": True, "VRd_max_kN_per_m": round(vmax(1.0), 1), "cot_theta": 1.0}
        need = max(v_ed * 1e3 / (z * fywd * cot), 0.08 * math.sqrt(self.conc.fck) / self.fyk * 1000)  # mm²/mm
        best = None
        for phi in [b for b in self.bars if b >= self.wall.link_diameter]:
            for s in LINK_SPACINGS:
                if s > 0.75 * d:
                    continue
                for st in LEG_SPACINGS:
                    if st > min(0.75 * d, 600):
                        continue
                    legs = 1000 / st
                    have = legs * math.pi * phi * phi / 4 / s
                    if have >= need and (best is None or have < best[0]):
                        best = (have, phi, s, st)
        if best is None:
            return {
                "crushes": False,
                "found": False,
                "cot_theta": round(cot, 2),
                "need_mm2_per_mm": round(need, 2),
            }
        have, phi, s, st = best
        return {
            "label": f"Ø{phi} @ {s:g}, legs @ {st:g}",
            "phi": phi,
            "spacing": s,
            "leg_spacing": st,
            "asw_s_mm2_per_mm": round(have, 2),
            "need_mm2_per_mm": round(need, 2),
            "cot_theta": round(cot, 2),
            "VRd_s_kN_per_m": round(have * z * fywd * cot / 1e3, 1),
            "VRd_max_kN_per_m": round(vmax(cot), 1),
            "found": True,
            "crushes": False,
        }


def _section(ctx: _Ctx, front: dict, back: dict) -> RectSection:
    """1 m strip: v up is the back face, so M tensioning the front face compresses the back (M_v +)."""
    conc, steel = ctx.laws
    h = ctx.h
    bars = Bars(
        np.zeros(2),
        np.array([-(h / 2 - (h - front["d_mm"])), h / 2 - (h - back["d_mm"])]),
        np.array([front["area_mm2_per_m"], back["area_mm2_per_m"]], float),
    )
    return RectSection(1000.0, h, bars, conc, steel, strips=120, deduct=False)


def _zone(ctx: _Ctx, uls: pd.DataFrame, qp: pd.DataFrame, phi_h: float) -> dict:
    checkpoint()
    m, n = uls["M"].to_numpy(float), uls["N"].to_numpy(float)
    qm, qn = qp["M"].to_numpy(float), qp["N"].to_numpy(float)
    faces = {}
    for face, sgn in (("front", 1.0), ("back", -1.0)):
        u = np.maximum(sgn * m, 0.0)
        q = np.maximum(sgn * qm, 0.0)
        faces[face] = ctx.face(u, n, q, qn, face, phi_h)
    sec = _section(ctx, faces["front"], faces["back"])
    util_m = sec.utilisation(n, m) if len(m) else np.zeros(0)
    # Horizontal bars.
    mh, nh = uls["Mh"].to_numpy(float), uls["Nh"].to_numpy(float)
    qmh, qnh = qp["Mh"].to_numpy(float), qp["Nh"].to_numpy(float)
    horiz = {
        face: ctx.horizontal(
            np.maximum(sgn * mh, 0.0),
            nh,
            np.maximum(sgn * qmh, 0.0),
            qnh,
            face,
            faces[face]["area_mm2_per_m"],
        )
        for face, sgn in (("front", 1.0), ("back", -1.0))
    }
    # Shear with ρl of the face in tension.
    v = uls["V"].to_numpy(float)
    tens_front = m >= 0
    d = np.where(tens_front, faces["front"]["d_mm"], faces["back"]["d_mm"]).astype(float)
    area = np.where(tens_front, faces["front"]["area_mm2_per_m"], faces["back"]["area_mm2_per_m"])
    vrdc = ctx.vrd_c(n, area / (1000 * d), d) if len(v) else np.zeros(0)
    nu = 0.6 * (1 - ctx.conc.fck / 250)
    v_top = 0.5 * 1000 * d * nu * ctx.fcd / 1e3 if len(v) else np.zeros(0)
    shear: dict[str, Any] = {"links": None}
    if len(v):
        j = int(np.argmax(v - vrdc))
        shear.update(
            {
                "V_kN_per_m": round(float(v.max()), 1),
                "VRd_c_kN_per_m": round(float(vrdc[j]), 1),
                "combination": str(uls["combination"].iloc[j]),
                "z": round(float(uls["Z"].iloc[j]), 2),
            }
        )
        if (v > vrdc + 1e-9).any():
            dd = float(d[j])
            lk = ctx.links(float(v.max()), dd)
            shear["links"] = lk
            cap = min(lk.get("VRd_s_kN_per_m") or 0.0, lk.get("VRd_max_kN_per_m") or 0.0)
            shear["util"] = round(float(v.max()) / cap, 3) if cap > 0 else 99.0
        else:
            shear["util"] = round(float(np.max(np.maximum(v / np.maximum(vrdc, 1e-9), v / v_top))), 3)
    else:
        shear["util"] = 0.0
    util = {
        "bending": round(float(util_m.max()), 3) if len(util_m) else 0.0,
        "shear": shear["util"],
        "crack_front": round(faces["front"]["wk_mm"] / ctx.limits["front"], 3),
        "crack_back": round(faces["back"]["wk_mm"] / ctx.limits["back"], 3),
    }
    uf = min(max(util.values()), 99.0)
    if not all(f["found"] for f in faces.values()):
        uf = max(uf, 1.01)
    gi = int(np.argmax(util_m)) if len(util_m) else None
    return {
        "front": faces["front"],
        "back": faces["back"],
        "horizontal": horiz,
        "shear": shear,
        "util": util,
        "uf": round(float(uf), 3),
        "governs": max(util, key=util.get),
        "bending_at": None
        if gi is None
        else {
            "combination": str(uls["combination"].iloc[gi]),
            "z": round(float(uls["Z"].iloc[gi]), 2),
            "M_kNm_per_m": round(float(m[gi]), 1),
            "N_kN_per_m": round(float(n[gi]), 1),
        },
        "M_max": round(float(m.max()), 1) if len(m) else 0.0,
        "M_min": round(float(m.min()), 1) if len(m) else 0.0,
        "_uf_points": util_m,
    }


def _key(z: dict) -> tuple:
    lk = z["shear"]["links"]
    return (
        z["front"]["bars"],
        z["back"]["bars"],
        z["horizontal"]["front"]["bars"],
        z["horizontal"]["back"]["bars"],
        lk.get("label") if lk else None,
    )


def _merge(zones: list[dict]) -> list[dict]:
    out: list[dict] = []
    for z in zones:
        if out and _key(out[-1]) == _key(z):
            prev = out[-1]
            keep = prev if prev["uf"] >= z["uf"] else z
            merged = {**keep, "top": prev["top"], "bottom": z["bottom"]}
            merged["M_max"] = max(prev["M_max"], z["M_max"])
            merged["M_min"] = min(prev["M_min"], z["M_min"])
            for face in FACES:
                merged[face] = {
                    **keep[face],
                    "required_mm2_per_m": max(
                        prev[face]["required_mm2_per_m"], z[face]["required_mm2_per_m"]
                    ),
                    "wk_mm": max(prev[face]["wk_mm"], z[face]["wk_mm"]),
                }
            out[-1] = merged
        else:
            out.append(z)
    return out


def _cage(ctx: _Ctx, wall: DiaphragmWallInput, s: float) -> int:
    """Vertical bars of one face of a cage (even, as every cage on the project)."""
    n = int(math.floor((wall.panel_width - 2 * END_CLEAR) / s)) + 1
    return n + (n % 2)


def _steel(
    ctx: _Ctx, wall: DiaphragmWallInput, settings: DesignSettings, zones: list[dict], height: float
) -> dict:
    """Bar weights per metre run of wall, per panel and for the element (laps and the anchorage into the
    capping beam counted)."""
    pr = settings.piles
    rho = STEEL_DENSITY / 1e6  # kg per mm² per m
    vertical = 0.0
    for z in zones:
        length = z["top"] - z["bottom"]
        for face in FACES:
            lap = 1 + pr.lap_factor * z[face]["phi"] / 1000 / max(pr.max_bar_length, 1.0)
            vertical += z[face]["area_mm2_per_m"] * length * lap * rho
    top = zones[0]
    anchor = (
        sum(top[f]["area_mm2_per_m"] * pr.head_anchorage_factor * top[f]["phi"] / 1000 for f in FACES) * rho
    )
    horizontal = (
        sum(
            (z["horizontal"]["front"]["area_mm2_per_m"] + z["horizontal"]["back"]["area_mm2_per_m"])
            * (z["top"] - z["bottom"])
            for z in zones
        )
        * rho
    )
    links = 0.0
    for z in zones:
        lk = z["shear"]["links"]
        if lk and lk.get("found"):
            # legs per m² × leg length (across the wall) × bar area
            per_m2 = (1000 / lk["leg_spacing"]) * (1000 / lk["spacing"]) * (ctx.h - 2 * ctx.cover) / 1000
            links += per_m2 * math.pi * lk["phi"] ** 2 / 4 * rho * (z["top"] - z["bottom"])
    per_m = vertical + anchor + horizontal + links
    return {
        "vertical_kg_per_m": round(vertical + anchor, 1),
        "horizontal_kg_per_m": round(horizontal, 1),
        "links_kg_per_m": round(links, 1),
        "kg_per_m": round(per_m, 1),
        "kg_per_m3": round(per_m / (ctx.h / 1000 * height), 1) if height > 0 else None,
        "total_kg": round(per_m * wall.panel_width / 1000, 1),  # one panel's cage
    }


def design_diaphragm_wall(
    name: str,
    wall: DiaphragmWallInput,
    settings: DesignSettings,
    sheets: dict[str, SheetData],
) -> dict[str, Any] | None:
    """The wall's design, or None when it has no results. ``wall`` carries the project grades and cover."""
    ctx = _Ctx(wall, settings)
    top_cut = None if wall.top_level is None else wall.top_level + settings.results_into_connection / 1e3
    uls = _frame(sheets, wall, top_cut, qp=False)
    if uls.empty:
        return None
    qp = _frame(sheets, wall, top_cut, qp=True)
    notes: list[str] = []
    # Which sign of M_11 tensions the front face.
    if wall.front_face == "auto":
        big = float(uls["M"].iloc[int(np.argmax(np.abs(uls["M"].to_numpy(float))))])
        sign = 1.0 if big >= 0 else -1.0
        notes.append(
            f"Front (sea) face taken in tension under {'positive' if sign > 0 else 'negative'} M_11, the "
            f"sign of the largest M_11 ({big:.0f} kNm/m); set it on the element if the model says otherwise."
        )
    else:
        sign = 1.0 if wall.front_face == "positive" else -1.0
    for f in (uls, qp):
        f["M"] *= sign
        f["Mh"] *= sign
    if qp.empty:
        notes.append("No QP combination in the workbook: crack widths are not checked.")
    z_all = uls["Z"].to_numpy(float)
    top = float(wall.top_level) if wall.top_level is not None else float(z_all.max())
    toe = float(z_all.min())
    height = max(top - toe, 0.0)
    step = wall.min_zone_length
    edges = [top]
    while edges[-1] - step > toe + 1e-6:
        edges.append(round(edges[-1] - step, 3))
    if len(edges) > 1 and edges[-1] - toe < 0.5 * step:
        edges.pop()  # a short last piece joins the zone above it
    edges.append(toe)

    def run(phi_h: float) -> list[dict]:
        zones = []
        for i in range(len(edges) - 1):
            hi, lo = edges[i], edges[i + 1]
            last = i == len(edges) - 2
            u, q = (f[(f["Z"] <= hi + (1e3 if i == 0 else 1e-6)) & ((f["Z"] > lo) | last)] for f in (uls, qp))
            if u.empty:
                continue
            z = _zone(ctx, u, q, phi_h)
            z["top"], z["bottom"] = round(hi, 2), round(lo, 2)
            z["_z"] = u["Z"].to_numpy(float)
            zones.append(z)
        return zones

    zones = run(16.0)
    phi_h = max(max(z["horizontal"][f]["phi"] for f in FACES) for z in zones) if zones else 16.0
    if phi_h != 16.0:
        zones = run(float(phi_h))
    profile_rows = []
    for z in zones:
        for zz, u in zip(z.pop("_z"), z.pop("_uf_points"), strict=False):
            profile_rows.append((round(float(zz), 1), float(u)))
    prof = (
        pd.DataFrame(profile_rows, columns=["z", "uf"]).groupby("z")["uf"].max().sort_index(ascending=False)
        if profile_rows
        else pd.Series(dtype=float)
    )
    zones = _merge(zones)
    g = max(zones, key=lambda z: z["uf"])
    ratio = max((z["front"]["area_mm2_per_m"] + z["back"]["area_mm2_per_m"]) / (1000 * ctx.h) for z in zones)
    if ratio > 0.04:
        notes.append(
            f"Vertical steel {100 * ratio:.1f}% of the section somewhere: over the 4% of EN 1992-1-1 "
            "9.6.2(1) outside laps. A thicker wall, or couplers."
        )
    if any(not z[f]["found"] for z in zones for f in FACES):
        notes.append(
            "No vertical bars on the list carry the moment or keep the crack width at some level: the "
            "heaviest are shown and flagged. A thicker wall, bigger bars or more layers."
        )
    crush = [z for z in zones if (z["shear"]["links"] or {}).get("crushes")]
    if crush:
        notes.append(
            f"Shear crushes the concrete struts (VRd,max) between {crush[0]['top']:g} and "
            f"{crush[-1]['bottom']:g} m: a thicker wall."
        )
    xy = uls[["X", "Y"]].to_numpy(float)
    length = float(max(np.ptp(xy[:, 0]), np.ptp(xy[:, 1]))) if len(xy) else 0.0
    count = wall.count or max(1, math.ceil(length * 1000 / wall.panel_width - 1e-6))
    steel = _steel(ctx, wall, settings, zones, height)
    run = max(length, count * wall.panel_width / 1000)
    steel["element_total_t"] = round(steel["kg_per_m"] * run / 1000, 2)
    for z in zones:
        for face in FACES:
            z[face].pop("_o", None)
            z[face]["per_cage"] = _cage(ctx, wall, z[face]["spacing"]) * z[face]["layers"]
    combos = []
    for c in sorted(uls["combination"].astype(str).unique()):
        f = uls[uls["combination"] == c]
        combos.append(
            {
                "combination": c,
                "max_M": round(float(f["M"].max()), 1),
                "min_M": round(float(f["M"].min()), 1),
                "max_N": round(float(f["N"].max()), 1),
                "max_V": round(float(f["V"].max()), 1),
                "max_Mh": round(float(np.abs(f["Mh"]).max()), 1),
            }
        )
    return {
        "thickness": ctx.h,
        "panel_width": wall.panel_width,
        "cover": ctx.cover,
        "concrete": ctx.conc.to_dict(),
        "fyk": ctx.fyk,
        "front_sign": "positive" if sign > 0 else "negative",
        "top": round(top, 2),
        "top_level_set": wall.top_level is not None,
        "toe": round(toe, 2),
        "height": round(height, 2),
        "length_m": round(length, 2),
        "count": count,
        "points": int(len(uls)),
        "qp_points": int(len(qp)),
        "zones": zones,
        "governing": {
            "zone": [g["top"], g["bottom"]],
            "governs": g["governs"],
            "uf": g["uf"],
            "bending_at": g["bending_at"],
            "shear": {k: v for k, v in g["shear"].items() if k != "links"},
        },
        "uf": g["uf"],
        "ok": bool(g["uf"] <= 1.0 + 1e-9),
        "utilisation": {k: max(z["util"][k] for z in zones) for k in zones[0]["util"]},
        "profile": [[float(z), round(float(u), 3)] for z, u in prof.items()],
        "steel": steel,
        "concrete_m3_per_m": round(ctx.h / 1000 * height, 2),
        "by_combination": combos,
        "notes": notes,
    }


CHECK_TITLES = {
    "bending": "Bending with N (N–M)",
    "shear": "Shear",
    "crack_front": "Crack width, front face",
    "crack_back": "Crack width, back face",
}
