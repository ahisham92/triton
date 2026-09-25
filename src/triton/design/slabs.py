"""Slabs (the deck) from the plate results: bars per metre by zone, shear, punching at the piles, restraint.

Directions. Bars run along global X and Y. The local axes of the plate come from the
directions check on upload (local 1 = global X when it had no answer), so for the bars
along X the slab takes Mx = M11 (or M22), Nx = N1 (or N2) and Vx = Q13 (or Q23).

Moments. Wood–Armer design moments from Mx, My and the twisting moment Mxy, bottom
(sagging +) and top (hogging -), at every node. Nodes inside a pile are FE peaks in
the connection and are left out (bending at the pile face); for shear, nodes within d
(or 2d) of a pile face are left out too. The moments just outside each pile are then used as
they are or averaged, by the slab's "Moments at the pile faces" method (Method tab).

Bars per metre, EN 1992-1-1, for each face and direction:

* ULS: tension steel of a 1 m strip under N and M, As = (M + N(d − h/2))/(z·fyd) − N/fyd,
  z = d/2·(1 + √(1 − 3.53K)) ≤ 0.95d with K = Ms/(b·d²·fck); K > 0.167 is flagged;
* minimum steel 9.3.1.1 (9.2.1.1): max(0.26·fctm/fyk, 0.0013)·b·d;
* QP crack width 7.3.4 per face and limit, from the cracked-section stress
  σs = (Ms/z − N)/As with z = d(1 − k/3);
* the outer layer is the bars along X, the bars along Y are one bar further in.

Zones. The slab is cut into square cells (1 m by default). Each face and direction gets a
basic mesh over the whole slab and, where a cell needs more, zones of heavier bars.
The basic mesh is the one with the least total steel counting a 10% premium for each
cell in a zone.

Column and field strips (the default, as the office's slab design): strips run from the front
beam to the rear beam. A column strip (2.2 m) is centred on each line of piles, a field strip
(2.0 m) between two lines. At every cut along a strip the moment and axial force are averaged
across the strip's width; every column strip is designed together, and every field strip, at
stations along the strips (2 m each side of every row of piles and the spans between, or the
user's). Each station and strip gets one set of bars for its worst cut (ULS) and QP crack width;
M/MRd uses the rectangular block with the tension steel only.

Shear per metre, 6.2.2: v = √(Vx² + Vy²) against VRd,c with σcp from compression; in
tension no concrete contribution. Cells where links are needed are listed.

Punching at each pile, 6.4: VEd = the pile's axial force at its top, β from the pile's
head moments (6.4.3(4), β = 1 + 0.6π·e/(D + 4d)), control perimeter u1 = π(D + 4d),
vRd,c with ρl = √(ρx·ρy) of the face in tension over the pile, and vRd,max = 0.4·ν·fcd on
u0 = πD. Where vEd > vRd,c, links on perimeters out to u_out,ef = β·VEd/(vRd,c·d) (6.52).
A sloped slab can use a different depth for punching. By default every pile type gets one
punching design, as detailed on site: its worst head's (failing, then needing links, then the
largest utilisation), with links enough for every head of the type; each head's own check is kept
for checking. The slab can instead give each head its own, and can limit the check to some pile types.

Restraint: as for beams (``crack.restraint_crack``), with the slab thickness as the height.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from ..axes import sag_factor
from ..elements import CombinationType, combination_type
from ..importer import SheetData
from ..materials import REINFORCEMENT_GRADES, STEEL_DENSITY, concrete
from ..project import DesignSettings, PileInput, SlabInput, SlabStrips, with_project_grades
from . import ductility
from . import voids as vd
from .crack import K1, K3, K4, KT, autogenous_shrinkage, restraint_crack, restraint_factor
from .governing import crack_terms
from .tension import slab_tension

E_S = 200_000.0
PREMIUM = 0.10  # extra weight per zoned cell when picking the basic mesh
MESH_SLACK = 0.05  # a lighter basic mesh is kept if the whole slab's steel is at most this much more
LAYER_PREMIUM = 0.25  # extra weight per layer of bars under the first: fewer layers where the steel allows
LAYERS = ("bottom_x", "bottom_y", "top_x", "top_y")
LAYER_TEXT = {
    "bottom_x": "bottom bars along X",
    "bottom_y": "bottom bars along Y",
    "top_x": "top bars along X",
    "top_y": "top bars along Y",
}


# --- Loads ---------------------------------------------------------------------------------------


def _map(axes: dict[str, str] | None) -> dict[str, str]:
    one = (axes or {}).get("1", "X")
    if one == "Y":
        return {"Mx": "M_22", "My": "M_11", "Nx": "N_2", "Ny": "N_1", "Vx": "Q_23", "Vy": "Q_13"}
    return {"Mx": "M_11", "My": "M_22", "Nx": "N_1", "Ny": "N_2", "Vx": "Q_13", "Vy": "Q_23"}


def slab_loads(
    sheets: dict[str, SheetData], axes: dict[str, str] | None, sag: float, qp: bool
) -> pd.DataFrame:
    """Node forces per metre in global directions: N compression +, M sagging +."""
    cols = _map(axes)
    parts = []
    for combo, sheet in sheets.items():
        ctype = combination_type(combo)
        if (ctype is CombinationType.SLS_QP) != qp or sheet.frame.empty:
            continue
        f = sheet.frame.drop_duplicates(["X", "Y", "Z"])
        parts.append(
            pd.DataFrame(
                {
                    "combination": combo,
                    "Node": f["Node"].to_numpy(),
                    "X": f["X"].to_numpy(float),
                    "Y": f["Y"].to_numpy(float),
                    "Z": f["Z"].to_numpy(float),
                    "Mx": sag * f[cols["Mx"]].to_numpy(float),
                    "My": sag * f[cols["My"]].to_numpy(float),
                    "Mxy": f["M_12"].to_numpy(float),
                    "Nx": -f[cols["Nx"]].to_numpy(float),
                    "Ny": -f[cols["Ny"]].to_numpy(float),
                    "Vx": f[cols["Vx"]].to_numpy(float),
                    "Vy": f[cols["Vy"]].to_numpy(float),
                    # Plan X, Y of a corner berth's turned part (crane areas are given in plan).
                    **({"PX": f["PX"].to_numpy(float), "PY": f["PY"].to_numpy(float)} if "PX" in f else {}),
                }
            )
        )
    if not parts:
        return pd.DataFrame(
            columns=["combination", "Node", "X", "Y", "Z", "Mx", "My", "Mxy", "Nx", "Ny", "Vx", "Vy"]
        )
    return pd.concat(parts, ignore_index=True)


def wood_armer(mx: np.ndarray, my: np.ndarray, mxy: np.ndarray) -> dict[str, np.ndarray]:
    """Wood–Armer design moments (kNm/m): bottom >= 0 (sagging), top <= 0 (hogging)."""
    a = np.abs(mxy)
    with np.errstate(divide="ignore", invalid="ignore"):
        bx, by = mx + a, my + a
        fix = bx < 0
        bx = np.where(fix, 0.0, bx)
        by = np.where(fix, my + np.abs(np.nan_to_num(mxy**2 / mx)), by)
        fix = by < 0
        by = np.where(fix, 0.0, by)
        bx = np.where(fix & ~(mx + a < 0), mx + np.abs(np.nan_to_num(mxy**2 / my)), bx)
        tx, ty = mx - a, my - a
        fix = tx > 0
        tx = np.where(fix, 0.0, tx)
        ty = np.where(fix, my - np.abs(np.nan_to_num(mxy**2 / mx)), ty)
        fix = ty > 0
        ty = np.where(fix, 0.0, ty)
        tx = np.where(fix & ~(mx - a > 0), mx - np.abs(np.nan_to_num(mxy**2 / my)), tx)
    return {
        "bottom_x": np.maximum(bx, 0.0),
        "bottom_y": np.maximum(by, 0.0),
        "top_x": np.minimum(tx, 0.0),
        "top_y": np.minimum(ty, 0.0),
    }


# --- Bars per metre -------------------------------------------------------------------------------


def bar_options(settings: DesignSettings) -> list[tuple[float, int, float, int]]:
    """(mm²/m, Ø, spacing, layers) of every bar size, spacing and layer count allowed, cheapest first."""
    r = settings.reinforcement
    out = []
    for phi in r.bar_diameters:
        if phi < 10:
            continue
        clear = max(r.slab_min_clear_spacing, phi)
        if r.slab_spacings:
            spacings = sorted({float(s) for s in r.slab_spacings if s >= phi + clear - 1e-9})
        else:
            spacings, s = [], r.max_spacing
            while s >= max(100.0, phi + clear) - 1e-9:
                spacings.append(s)
                s -= r.spacing_step
        for s in spacings:
            for layers in range(1, r.max_layers + 1):
                out.append((layers * 1000 * math.pi * phi * phi / 4 / s, phi, s, layers))
    # Cheapest first; every layer under the first costs more to place.
    return sorted(out, key=lambda o: (o[0] * (1 + LAYER_PREMIUM * (o[3] - 1)), -o[1]))


def label(o: tuple) -> str:
    return f"Ø{o[1]} @ {o[2]:g}" + (f" in {o[3]} layers" if o[3] > 1 else "")


K_BAL = 0.167  # K' without moment redistribution (fck <= 50 MPa)


def required_as(
    m: np.ndarray, n: np.ndarray, h: float, d: float, fck: float, fyd: float, d2: float | None = None
) -> tuple:
    """Steel (mm²/m) for |M| (kNm/m) with N (kN/m, compression +) on a 1 m strip.

    Returns (tension steel, K, compression steel). Where K > K' and the depth d2 of the
    compression face bars is given, the moment beyond K' is taken by the compression
    face bars (assumed to yield) with the tension bars.
    """
    m = np.abs(m) * 1e6
    n = n * 1e3
    ms = m + n * (d - h / 2)
    k = np.maximum(ms, 0) / (1000 * d * d * fck)
    z = np.minimum(0.5 * d * (1 + np.sqrt(np.clip(1 - 3.53 * k, 0, None))), 0.95 * d)
    a_s = ms / (z * fyd) - n / fyd
    a_s2 = np.zeros_like(a_s)
    if d2 is not None:
        over = k > K_BAL
        m_bal = K_BAL * 1000 * d * d * fck
        z_bal = 0.5 * d * (1 + math.sqrt(1 - 3.53 * K_BAL))
        a_s2 = np.where(over, (ms - m_bal) / (fyd * (d - d2)), 0.0)
        a_s = np.where(over, m_bal / (z_bal * fyd) + a_s2 - n / fyd, a_s)
    # Small eccentricity in tension: the tension shared by both faces.
    small = ms < 0
    a_s = np.where(small, (-n / 2 + m / (2 * d - h)) / fyd, a_s)
    return np.maximum(a_s, 0.0), k, np.maximum(a_s2, 0.0)


def extreme_sets(c, m, n, gov: int | None = None) -> list[dict]:
    """The sets a reviewer checks for one face: max N, min N and the largest moment on the face over every
    result and combination, each naming its combination, plus the one that sets the bars ("governing")
    when it is not already one of them. Nothing when the face has no moment to speak of."""
    m, n, c = np.asarray(m, float), np.asarray(n, float), np.asarray(c)
    if not len(m) or float(np.abs(m).max()) < 0.5:
        return []
    a = np.abs(m)
    picks = [
        ("max N", int(np.lexsort((a, n))[-1])),
        ("min N", int(np.lexsort((-a, n))[0])),
        ("max M" if m[int(np.argmax(a))] >= 0 else "min M", int(np.argmax(a))),
    ]
    if gov is not None:
        picks.append(("governing", int(gov)))
    out: dict[int, dict] = {}
    for case, i in picks:
        if i in out:
            out[i]["case"] += f", {case}"
            continue
        out[i] = {
            "case": case,
            "combination": str(c[i]),
            "N_kN_per_m": round(float(n[i]), 1),
            "M_kNm_per_m": round(float(m[i]), 1),
        }
    return list(out.values())


def crack_widths(
    m: np.ndarray,
    n: np.ndarray,
    area: float,
    phi: float,
    s: float,
    h: float,
    d: float,
    c: float,
    conc,
    e_eff: float,
    terms: bool = False,
):
    """7.3.4 crack widths (mm) of QP loads (kNm/m, kN/m) at one face with Ø at spacing s.

    With ``terms`` it returns (wk, σs, sr,max, x) instead of wk alone."""
    rho = area / (1000 * d)
    ae = E_S / e_eff
    k = math.sqrt(2 * ae * rho + (ae * rho) ** 2) - ae * rho
    z = d * (1 - k / 3)
    x = k * d
    ms = np.abs(m) * 1e6 + n * 1e3 * (d - h / 2)
    sigma = np.maximum((ms / z - n * 1e3) / area, 0.0)
    hc = min(2.5 * (h - d), (h - x) / 3, h / 2)
    rp = area / (1000 * hc)
    strain = np.maximum((sigma - KT * conc.fctm / rp * (1 + E_S / conc.ecm * rp)) / E_S, 0.6 * sigma / E_S)
    sr = 1.3 * (h - x) if s > 5 * (c + phi / 2) else K3 * c + K1 * 0.5 * K4 * phi / rp
    if terms:
        return sr * strain, sigma, sr, x
    return sr * strain


# --- Cells and zones ------------------------------------------------------------------------------


def _cells(x: np.ndarray, y: np.ndarray, x0: float, y0: float, size: float) -> tuple[np.ndarray, np.ndarray]:
    return np.floor((x - x0) / size + 1e-9).astype(int), np.floor((y - y0) / size + 1e-9).astype(int)


SUPPORT_ZONE = 2.0  # m each side of a row of piles, for the automatic stations


def auto_stations(
    rows: list[float], length: float, half: float = SUPPORT_ZONE, shortest: float = 1.0
) -> list[float]:
    """Station boundaries: a station ``half`` each side of every pile row and the spans between them.

    Two pile rows closer than 2·half + ``shortest`` share the boundary halfway between them.
    """
    zones = [[max(0.0, r - half), min(length, r + half)] for r in rows]
    for a, b in zip(zones, zones[1:], strict=False):
        if b[0] - a[1] < shortest:
            a[1] = b[0] = (a[1] + b[0]) / 2
    cuts = {0.0, round(length, 2)}
    for z in zones:
        cuts |= {round(v, 2) for v in z if shortest <= v <= length - shortest}
    return sorted(cuts)


def strip_frame(
    slab: SlabInput,
    box: dict,
    piles: list[tuple],
    beams: list[dict],
    stations: list[float] | None = None,
    lines: list[float] | None = None,
) -> dict | None:
    """Where the column and field strips lie: the lines of piles along the strips, the distance along
    them from the sea side (the front wall line, the front beam's centre, as the office's stations),
    and the station boundaries."""
    along = slab.strip_direction
    ai, ci = (0, 1) if along == "X" else (1, 0)
    lines = sorted(lines) if lines else sorted({round(p[ci], 1) for p in piles})
    if not lines:
        return None
    lo, hi = box[along]
    front = next((b for b in beams if b.get("type") == "front_beam"), None)
    mid = None if front is None else sum(front["box"][along]) / 2
    from_hi = mid is None or abs(mid - hi) <= abs(mid - lo)
    edge, sign = (hi, -1.0) if from_hi else (lo, 1.0)
    origin = edge if mid is None else mid
    length = hi - lo
    start = round((edge - origin) * sign, 2)
    end = round(start + length, 2)
    rows = sorted({round((p[ai] - origin) * sign, 2) for p in piles})
    rows = [r for r in rows if start < r < end]
    given = slab.stations if stations is None else stations
    if given:
        bounds = sorted({start, end, *(round(b, 2) for b in given if start < b < end)})
    else:
        bounds = [round(start + b, 2) for b in auto_stations([r - start for r in rows], length)]
    return {
        "along": along,
        "across": "Y" if along == "X" else "X",
        "origin": origin,
        "sign": sign,
        "length": length,
        "lines": lines,
        "rows": rows,
        "bounds": bounds,
        "column": slab.column_strip_width,
        "field": slab.field_strip_width,
        "from": "front wall line (front beam centre)" if front is not None else f"{along} = {origin:g}",
        "start": start,
        "end": end,
    }


def strip_key(layer: str, bounds: list[float], k: tuple) -> str:
    """'layer|from|to|strip' naming one station and strip kind of a layer."""
    return f"{layer}|{bounds[k[0]]:g}|{bounds[k[0] + 1]:g}|{'column' if k[1] == 0 else 'field'}"


def station_text(stations: list[list[float]], every: list[list[float]]) -> str:
    """'Station 4 to 8', 'All stations', 'All stations except 4 to 8' or a list."""
    if len(stations) == len(every):
        return "All stations"

    def runs(sts: list[list[float]]) -> list[list[float]]:
        out: list[list[float]] = []
        for a, b in sorted(sts):
            if out and abs(out[-1][1] - a) < 1e-6:
                out[-1][1] = b
            else:
                out.append([a, b])
        return out

    mine, rest = runs(stations), runs([s for s in every if s not in stations])
    if len(mine) == 1:
        return f"Station {mine[0][0]:g} to {mine[0][1]:g}"
    if len(rest) == 1:
        return f"All stations except {rest[0][0]:g} to {rest[0][1]:g}"
    return "Stations " + ", ".join(f"{a:g} to {b:g}" for a, b in mine)


def strip_table(rows: list[dict], frame: dict) -> list[dict]:
    """The office's slab table (calc report Table 5-4): for the bars along the strips one row per
    station and strip; for the bars along the berth, the stations with the same bars together
    ('Station 4 to 8', 'All stations except 4 to 8'). Each row carries its worst face."""
    every = [[a, b] for a, b in zip(frame["bounds"][:-1], frame["bounds"][1:], strict=False)]
    cells: dict[tuple, dict[str, dict]] = {}
    for r in rows:
        cells.setdefault((r["moment"], r["layer"].split("_")[1], tuple(r["station"]), r["strip"]), {})[
            r["face"]
        ] = r
    out = []
    along = frame["along"].lower()
    moments = sorted({(k[0], k[1]) for k in cells}, key=lambda m: (m[1] != along, m[0]))
    for moment, direction in moments:
        mine = {k: v for k, v in cells.items() if k[0] == moment}
        if direction == along:
            groups = [[k] for k in sorted(mine, key=lambda k: (k[2], k[3] != "column"))]
        else:
            by: dict[tuple, list] = {}
            for k in sorted(mine, key=lambda k: (k[3] != "column", k[2])):
                faces = mine[k]
                by.setdefault(
                    (k[3], *(faces[f]["bars"] if f in faces else "" for f in ("bottom", "top"))), []
                ).append(k)
            groups = list(by.values())
        for g in groups:
            faces = [r for k in g for r in mine[k].values()]

            def score(r: dict) -> float:
                return max(r["ratio"] or 0, (r["wk_mm"] or 0) / r["wk_limit_mm"])

            worst = max(faces, key=score)
            wk = max(faces, key=lambda r: (r["wk_mm"] or 0) / r["wk_limit_mm"])
            stations = [list(k[2]) for k in g]
            out.append(
                {
                    "moment": moment,
                    "along_strips": direction == along,
                    "strip": g[0][3],
                    "stations": stations,
                    "label": station_text(stations, every),
                    "wk_mm": wk["wk_mm"],
                    "wk_limit_mm": wk["wk_limit_mm"],
                    "ratio": worst["ratio"],
                    "M_kNm_per_m": worst["M_kNm_per_m"],
                    "N_kN_per_m": worst.get("N_kN_per_m"),
                    "MRd_kNm_per_m": worst["MRd_kNm_per_m"],
                    "combination": worst["combination"],
                    "face": worst["face"],
                    "qp": None
                    if wk["wk_mm"] is None
                    else {
                        **(wk.get("qp") or {}),
                        "combination": wk.get("qp_combination"),
                        "face": wk["face"],
                    },
                    "bars": {f: mine[g[0]][f]["bars"] for f in ("bottom", "top") if f in mine[g[0]]},
                    "additional": {
                        f: mine[g[0]][f]["additional"] for f in ("bottom", "top") if f in mine[g[0]]
                    },
                    "layers": {f: mine[g[0]][f]["layer"] for f in ("bottom", "top") if f in mine[g[0]]},
                    "keys": {f: [mine[k][f]["key"] for k in g if f in mine[k]] for f in ("bottom", "top")},
                    "user_set": any(r["user_set"] for r in faces),
                    "edit": [f for f in ("bottom", "top") if f in mine[g[0]]],
                    **{
                        field: {f: mine[g[0]][f].get(field) for f in ("bottom", "top") if f in mine[g[0]]}
                        for field in ("set_by", "bar_layers", "spec", "mesh")
                    },
                    "ductility": {
                        f: max(
                            (mine[k][f].get("ductility") or {} for k in g if f in mine[k]),
                            key=lambda q: q.get("x_d") or 0,
                        )
                        for f in ("bottom", "top")
                        if f in mine[g[0]]
                    },
                }
            )
    return out


def overall_table(entries: list[dict]) -> list[dict]:
    """Rows for the bars designed over the whole deck (across the strips): the basic mesh of both faces,
    then each zone of additional bars with the other face's mesh."""

    def score(r: dict) -> float:
        return max(r["ratio"] or 0, (r["wk_mm"] or 0) / r["wk_limit_mm"])

    out = []
    for moment in sorted({e["moment"] for e in entries}):
        es = [e for e in entries if e["moment"] == moment]
        mesh = {e["face"]: e for e in es if e["zone"] is None}
        groups = [("Whole deck, basic mesh", list(mesh.values()))]
        groups += [(e["label"], [e]) for e in es if e["zone"] is not None]
        for text, main in groups:
            if not main:
                continue
            faces = {e["face"]: e for e in main}
            for f, m in mesh.items():
                faces.setdefault(f, m)
            worst = max(main, key=score)
            wk = max(main, key=lambda r: (r["wk_mm"] or 0) / r["wk_limit_mm"])
            fs = [f for f in ("bottom", "top") if f in faces]
            out.append(
                {
                    "moment": moment,
                    "along_strips": False,
                    "strip": "all",
                    "stations": [],
                    "label": text,
                    "zone": main[0]["zone"],
                    "wk_mm": wk["wk_mm"],
                    "wk_limit_mm": wk["wk_limit_mm"],
                    "ratio": worst["ratio"],
                    "M_kNm_per_m": worst["M_kNm_per_m"],
                    "N_kN_per_m": worst.get("N_kN_per_m"),
                    "MRd_kNm_per_m": worst["MRd_kNm_per_m"],
                    "combination": worst["combination"],
                    "face": worst["face"],
                    "qp": None
                    if wk["wk_mm"] is None
                    else {
                        **(wk.get("qp") or {}),
                        "combination": wk.get("qp_combination"),
                        "face": wk["face"],
                    },
                    "bars": {f: faces[f]["bars"] for f in fs},
                    "additional": {f: faces[f]["additional"] for f in fs},
                    "layers": {f: faces[f]["layer"] for f in fs},
                    "keys": {f: [faces[f]["key"]] for f in fs},
                    "user_set": any(e["user_set"] for e in main),
                    "edit": [e["face"] for e in main],
                    **{
                        field: {f: faces[f][field] for f in fs}
                        for field in ("set_by", "bar_layers", "spec", "mesh")
                    },
                    "ductility": {f: faces[f].get("ductility") or {} for f in fs},
                }
            )
    return out


def across_profile(f: pd.DataFrame, frame: dict, size: float, axes: dict[str, str] | None) -> dict:
    """ULS envelope of the moment across the strips (M22 when the strips run along X), largest and
    smallest over the whole deck at every cut along the quay, with the lines of piles."""
    cx = frame["across"]
    mcol = "My" if cx == "Y" else "Mx"
    lo = float(f[cx].min())
    cut = np.floor((f[cx].to_numpy() - lo) / size + 1e-9).astype(int)
    g = pd.DataFrame({"cut": cut, "m": f[mcol].to_numpy()}).groupby("cut")["m"].agg(["max", "min"])
    return {
        "moment": _map(axes)[mcol].replace("_", ""),
        "axis": cx,
        "points": [
            {
                "s": round(lo + (c + 0.5) * size, 2),
                "max": round(float(r["max"]), 1),
                "min": round(float(r["min"]), 1),
            }
            for c, r in g.iterrows()
        ],
        "lines": frame["lines"],
        "range": [round(lo, 2), round(float(f[cx].max()), 2)],
    }


def strip_profile(f: pd.DataFrame, loc: dict, size: float, axes: dict[str, str] | None) -> dict[str, list]:
    """ULS envelope of the moment per metre averaged across each strip, column and field strips, at
    every cut from the sea side: for the diagram the stations are set on."""
    out = {}
    for mcol, ncol in (("Mx", "Nx"), ("My", "Ny")):
        env = strip_average(f, f[mcol].to_numpy(float), f[ncol].to_numpy(float), loc, size)
        g = env.groupby(["cut", "kind"])["m"].agg(["max", "min"]).reset_index()
        rows: dict[int, dict] = {}
        for r in g.itertuples(index=False):
            k = "column" if r.kind == 0 else "field"
            row = rows.setdefault(int(r.cut), {"s": round((r.cut + 0.5) * size, 2)})
            row[f"{k}_max"], row[f"{k}_min"] = round(float(r.max), 1), round(float(r.min), 1)
        out[_map(axes)[mcol].replace("_", "")] = [rows[c] for c in sorted(rows)]
    return out


def locate(frame: dict, along: np.ndarray, across: np.ndarray) -> dict[str, np.ndarray]:
    """Distance along the strips, station, strip kind (0 column, 1 field), which strip, and whether
    the point lies inside a strip's width (points between strips wider than the pile spacing are
    designed as field strip but not averaged)."""
    s = (along - frame["origin"]) * frame["sign"]
    b = frame["bounds"]
    st = np.clip(np.searchsorted(b, s + 1e-9, side="right") - 1, 0, len(b) - 2)
    lines = np.array(frame["lines"])
    dist = np.abs(across[:, None] - lines[None, :])
    k = dist.argmin(axis=1)
    col = dist[np.arange(len(k)), k] <= frame["column"] / 2 + 1e-6
    if len(lines) > 1:
        mids = (lines[1:] + lines[:-1]) / 2
        dm = np.abs(across[:, None] - mids[None, :])
        km = dm.argmin(axis=1)
        field = ~col & (dm[np.arange(len(km)), km] <= frame["field"] / 2 + 1e-6)
    else:  # one line of piles: a field strip each side of it
        km, field = (across > lines[0]).astype(int), ~col
    return {
        "s": s,
        "st": st,
        "kind": np.where(col, 0, 1),
        "inst": np.where(col, k, km),
        "averaged": col | field,
    }


def strip_average(
    f: pd.DataFrame, m: np.ndarray, n: np.ndarray, loc: dict, size: float, v: np.ndarray | None = None
) -> pd.DataFrame:
    """Moment and axial force per metre averaged across each strip's width, at every cut along it
    (``size`` apart) and for every combination; with ``v`` (1 where the slab is voided) the voided share."""
    df = pd.DataFrame(
        {
            "combination": f["combination"].to_numpy(),
            "st": loc["st"],
            "kind": loc["kind"],
            "inst": loc["inst"],
            "cut": np.floor(loc["s"] / size + 1e-9).astype(int),
            "m": m,
            "n": n,
            "v": np.zeros(len(m)) if v is None else np.asarray(v, float),
        }
    )[loc["averaged"]]
    keys = ["st", "kind", "inst", "cut", "combination"]
    return df.groupby(keys, sort=False)[["m", "n", "v"]].mean().reset_index()


def strip_mrd(area: float, d: float, h: float, n: float, fcd: float, fyd: float) -> float:
    """Moment capacity per metre (kNm/m) of a strip with tension steel only, under N (kN/m, compression +),
    about the mid-depth, rectangular block 0.8x."""
    fc = area * fyd + n * 1e3
    if fc <= 0:
        return 0.0
    x = fc / (0.8 * 1000 * fcd)
    return max(fc * (h / 2 - 0.4 * x) + area * fyd * (d - h / 2), 0.0) / 1e6


def add_crane(uls: pd.DataFrame, slab: SlabInput) -> tuple[pd.DataFrame, int]:
    """Add the mobile crane areas' extra actions (factored crane minus factored live load) to the ULS rows."""
    if not slab.crane or uls.empty:
        return uls, 0
    uls = uls.copy()
    hit = np.zeros(len(uls), bool)
    px, py = ("PX", "PY") if "PX" in uls else ("X", "Y")  # areas are in plan
    for a in slab.crane:
        m = (
            uls[px].between(min(a.x_from, a.x_to), max(a.x_from, a.x_to))
            & uls[py].between(min(a.y_from, a.y_to), max(a.y_from, a.y_to))
        ).to_numpy()
        for col, v in (
            ("Mx", a.mx),
            ("My", a.my),
            ("Vx", a.vx),
            ("Vy", a.vy),
            ("Nx", a.nx),
            ("Ny", a.ny),
        ):
            uls.loc[m, col] += v
        hit |= m
    return uls, int(hit.sum())


def average_peaks(f: pd.DataFrame, piles: list[tuple]) -> pd.DataFrame:
    """Moments within one pile diameter of each pile face replaced by their average there, per combination."""
    f = f.copy()
    x, y = f["X"].to_numpy(), f["Y"].to_numpy()
    for px, py, r in piles:
        dist = np.hypot(x - px, y - py)
        ring = (dist >= r - 1e-6) & (dist <= 3 * r + 1e-6)
        if not ring.any():
            continue
        sub = f.loc[ring]
        f.loc[ring, ["Mx", "My", "Mxy"]] = (
            sub.groupby("combination")[["Mx", "My", "Mxy"]].transform("mean").to_numpy()
        )
    return f


def face_average(f: pd.DataFrame, piles: list[tuple], h: float, envelope: bool = False) -> pd.DataFrame:
    """Moments at each pile face averaged over that face alone, per combination.

    At every pile and each of its four faces: the nodes outside the pile on that side of it, out to one
    slab thickness ``h`` (m) beyond the face, over the pile diameter plus ``h`` each side (so the nodes
    beside a round pile head count with the face they look onto), take the band's mean of the moment
    that face's bars carry (Mx at the ±X faces, My at the ±Y faces) and of the twisting moment Mxy.
    Opposite faces and the two directions are never mixed. With ``envelope`` the band takes the mean of
    each node's worst value over all combinations instead (the most hogging for combinations that hog
    the face on average, the most sagging for the others). Where two bands meet, Mxy keeps the larger.
    """
    out = f.copy()
    x, y = f["X"].to_numpy(), f["Y"].to_numpy()
    twist = np.full(len(f), np.nan)
    for px, py, r in piles:
        for along, across, col in ((x - px, y - py, "Mx"), (y - py, x - px, "My")):
            for side in (1, -1):
                a = side * along
                # The half of the slab in front of this face (nodes inside the pile are already out),
                # beside the pile too: round a circular head, those nodes carry the face's peak.
                front = a >= 0 if side > 0 else a > 0  # a node on the centre line goes to +X (+Y)
                band = front & (a <= r + h + 1e-6) & (np.abs(across) <= r + h + 1e-6)
                if not band.any():
                    continue
                sub = f.loc[band]
                mean = sub.groupby("combination")[[col, "Mxy"]].transform("mean")
                if envelope and sub["combination"].nunique() > 1:
                    node = sub["Node"] if "Node" in sub else sub[["X", "Y"]].round(3).astype(str).sum(axis=1)
                    hi = sub.groupby(node)[col].max().mean()
                    lo = sub.groupby(node)[col].min().mean()
                    out.loc[band, col] = np.where(mean[col].to_numpy() >= 0, hi, lo)
                else:
                    out.loc[band, col] = mean[col].to_numpy()
                idx = np.flatnonzero(band)
                v = mean["Mxy"].to_numpy()
                keep = np.isnan(twist[idx]) | (np.abs(v) > np.abs(twist[idx]))
                twist[idx[keep]] = v[keep]
    done = ~np.isnan(twist)
    out.loc[done, "Mxy"] = twist[done]
    return out


PEAK_METHODS = {
    "peak": "Moments at the pile faces used as they are (no averaging at the piles; the strips still "
    "average across their width).",
    "face_mean": "Moments at the pile faces averaged face by face: the nodes on that face's side of the "
    "pile (those beside the round head too) out to one slab thickness beyond the face, over the pile "
    "diameter plus the slab thickness each side, only the moment that face's bars carry, for each "
    "combination; the worst combination is designed.",
    "ring_mean": "Moments round each pile averaged over a ring one pile diameter wide, all round the pile "
    "and for each combination (this mixes opposite faces).",
    "envelope_face_mean": "Moments at the pile faces averaged face by face as for the face mean, but from "
    "each node's worst value over all combinations (mixes combinations; more conservative).",
}


def treat_pile_faces(f: pd.DataFrame, piles: list[tuple], method: str, h: float) -> pd.DataFrame:
    """The slab's moments at the pile faces under the chosen method (see ``PEAK_METHODS``)."""
    if not piles or not len(f) or method == "peak":
        return f
    if method == "ring_mean":
        return average_peaks(f, piles)
    return face_average(f, piles, h, envelope=method == "envelope_face_mean")


def _centroid(bar_layers: list[dict]) -> tuple[float, float]:
    """(mm²/m, depth of the centroid from the face) of a face's bar layers."""
    area = sum(q["as_mm2_per_m"] for q in bar_layers)
    return area, (sum(q["as_mm2_per_m"] * q["from_face_mm"] for q in bar_layers) / area if area else 0.0)


def row_ductility(
    rows: list[dict], layers: dict, h: float, fcd: float, fyd: float, vsec: dict | None = None
) -> None:
    """Give every designed row its ``ductility`` (see ``ductility``): the tension face's bars at their
    capacity under the row's N, with the other face's bars in the same direction as compression steel
    (the same strip and station where it has a row there, else that face's basic mesh). Rows in the
    voided slab take the compression block on the voided section (``vsec``)."""
    other = {"top": "bottom", "bottom": "top"}
    by_key = {(r["layer"], tuple(r["station"]), r["strip"]): r for r in rows if r.get("zone") is None}
    for r in rows:
        face, direction = r["layer"].split("_")
        opp_layer = f"{other[face]}_{direction}"
        opp = by_key.get((opp_layer, tuple(r["station"]), r["strip"])) if r.get("zone") is None else None
        opp_layers = (
            (opp or {}).get("bar_layers") or (layers.get(opp_layer) or {}).get("mesh_bar_layers") or []
        )
        a_t, c_t = _centroid(r.get("bar_layers") or [])
        a_c, c_c = _centroid(opp_layers)
        a_t = a_t or float(r["as_mm2_per_m"])
        d = h - c_t if c_t else h - 60.0
        block = None
        if r.get("voided") and vsec and direction in vsec:
            depth_, area_, _, _ = vsec[direction].cumulative(other[face])
            block = lambda a, depth_=depth_, area_=area_: float(np.interp(a, depth_, area_))  # noqa: E731
        dct = ductility.strip(a_t, d, float(r["N_kN_per_m"]), fcd, fyd, a_c, c_c, block=block)
        dct["ratio_pct"] = round(100 * a_t / (1000 * h), 2)
        dct["warnings"] = ductility.warnings(dct["x_d"], dct["eps_s"], dct["eps_yd"], dct["ratio_pct"])
        r["ductility"] = dct


def fits_between(phi_a: float, mesh_phi: float, mesh_spacing: float, settings: DesignSettings) -> bool:
    """Whether bars of Ø ``phi_a`` fit halfway between mesh bars with the clear spacing of EC2 8.2(2)."""
    clear = max(settings.reinforcement.slab_min_clear_spacing, phi_a, mesh_phi)
    return mesh_spacing / 2 - (phi_a + mesh_phi) / 2 >= clear - 1e-9


def additional_options(
    mesh: tuple, settings: DesignSettings, room: float | None = None
) -> tuple[list, list, list, list]:
    """The mesh alone, then the mesh with additional bars, least steel first.

    Additional bars go between the mesh bars: in every second gap, in every gap, in every gap in two
    layers, also behind the mesh bars (three per gap, two layers), or in every gap in 3, 4, ... layers,
    as many as the design needs (Maximum bar layers is for beams and slab meshes). Layer 2 is inside
    layer 1: above the bottom mesh, below the top mesh. ``room`` (mm) is how deep the layers may reach
    from the face (to mid-depth); without it the additional bars stop at 3 layers. For crack widths the
    mix has the equivalent Ø of 7.12 and the largest gap between tension bars. Returns (options as
    (mm²/m, Ø, spacing, layers), Ø setting the depth, labels, bar layers): the bar layers are the
    additional bars as [(Ø, spacing)] per layer, the first between the mesh bars.
    """
    area, phi_b, s_b, layers_b = mesh
    n_b = layers_b * 1000 / s_b
    least = settings.reinforcement.slab_min_bar_spacing
    out = [(mesh, phi_b, label(mesh), [])]
    for phi_a in settings.reinforcement.bar_diameters:
        if phi_a < 10 or not fits_between(phi_a, phi_b, s_b, settings):
            continue
        choices = [
            (1000 / s_b, s_b / 2, layers_b, f"Ø{phi_a} @ {s_b:g}", [(phi_a, s_b)]),
            (1000 / (2 * s_b), s_b, layers_b, f"Ø{phi_a} @ {2 * s_b:g}", [(phi_a, 2 * s_b)]),
            (
                2000 / s_b,
                s_b / 2,
                max(layers_b, 2),
                f"Ø{phi_a} @ {s_b:g} in 2 layers",
                [(phi_a, s_b), (phi_a, s_b)],
            ),
            (
                3000 / s_b,
                s_b / 2,
                max(layers_b, 2),
                f"Ø{phi_a} @ {s_b:g} in 2 layers + Ø{phi_a} behind the mesh bars",
                [(phi_a, s_b), (phi_a, s_b / 2)],
            ),
        ]
        # More layers in every gap, while the stack stays on its side of mid-depth.
        big = max(phi_a, phi_b)
        pitch = big + max(25.0, big)
        k = 3
        while k <= (3 if room is None else 12) and (room is None or big + (k - 1) * pitch <= room + 1e-9):
            choices.append(
                (
                    k * 1000 / s_b,
                    s_b / 2,
                    max(layers_b, k),
                    f"Ø{phi_a} @ {s_b:g} in {k} layers",
                    [(phi_a, s_b)] * k,
                )
            )
            k += 1
        if least is not None:
            # The user's least spacing of additional bars: no set closer than it (no bars @ 75 behind
            # the mesh bars on a 150 mesh; more layers at the mesh spacing carry that steel instead).
            choices = [c for c in choices if min(p[1] for p in c[4]) >= least - 1e-9]
        for n_a, spacing, lay, text, spec in choices:
            phi_eq = (n_b * phi_b**2 + n_a * phi_a**2) / (n_b * phi_b + n_a * phi_a)
            o = (area + n_a * math.pi * phi_a**2 / 4, phi_eq, spacing, lay)
            out.append((o, max(phi_a, phi_b), text, spec))
    out = out[:1] + sorted(out[1:], key=lambda t: t[0][0] * (1 + LAYER_PREMIUM * (t[0][3] - 1)))
    return [t[0] for t in out], [t[1] for t in out], [t[2] for t in out], [t[3] for t in out]


def parse_layers(text: str) -> list[tuple[float, float] | None] | None:
    """Bar layers set by the user: 'layers: Ø32@150 | Ø25@75', the first between the mesh bars ('–' for
    none), the next ones in layers 2, 3, ... inside the mesh (above the bottom mesh, below the top
    one). None when the text is not in this form."""
    if not text.startswith("layers:"):
        return None
    out: list[tuple[float, float] | None] = []
    for part in text[len("layers:") :].split("|"):
        part = part.strip().replace(" ", "")
        if part in ("", "-", "–"):
            out.append(None)
            continue
        try:
            phi, spacing = part.lstrip("Ø").split("@")
            out.append((float(phi), float(spacing)))
        except ValueError:
            return None
    return out


def layer_name(n: int) -> str:
    """A layer as the office numbers it: the mesh and the bars between its bars are at mesh level, L1 is
    the first layer inside the mesh (above the bottom mesh, below the top one), then L2, L3..."""
    return "mesh level" if n == 1 else f"L{n - 1}"


def layers_text(spec: list) -> str:
    parts = []
    for k, p in enumerate(spec):
        if p is None:
            continue
        where = "between the mesh bars" if k == 0 else f"in {layer_name(k + 1)}"
        parts.append(f"Ø{p[0]:g} @ {p[1]:g} {where}")
    return " + ".join(parts) if parts else "mesh only"


def bar_layers(mesh: tuple, spec: list, cover: float, shift: float = 0.0) -> list[dict]:
    """Every layer of bars of one face and direction, outermost first: its bars, how far its centre is
    from the face (mm; the layers go inward, above the bottom mesh and below the top mesh), and its
    steel. The mesh is layer 1 (and 2 for a mesh in two layers); ``shift`` is how much deeper these bars
    sit because the other direction's bars are outside them."""
    _, phi_m, s_m, layers_m = mesh
    rows: list[dict] = []
    n = max(layers_m, len(spec))
    for k in range(n):
        bars = []
        if k < layers_m:
            bars.append((phi_m, s_m, "mesh"))
        if k < len(spec) and spec[k] is not None:
            bars.append((spec[k][0], spec[k][1], "between the mesh bars" if k == 0 else "added"))
        if bars:
            rows.append({"layer": k + 1, "bars": bars})
    at = cover + shift
    out = []
    for i, r in enumerate(rows):
        big = max(b[0] for b in r["bars"])
        if i:
            prev = max(b[0] for b in rows[i - 1]["bars"])
            at += prev / 2 + max(25.0, prev, big) + big / 2
        else:
            at += big / 2
        area = sum(1000 * math.pi * b[0] ** 2 / 4 / b[1] for b in r["bars"])
        out.append(
            {
                "layer": r["layer"],
                "text": " + ".join(
                    f"Ø{b[0]:g} @ {b[1]:g}" + ("" if b[2] == "mesh" else f" ({b[2]})") for b in r["bars"]
                ),
                "from_face_mm": round(at),
                "as_mm2_per_m": round(area),
                "bars": [{"diameter_mm": b[0], "spacing_mm": b[1], "kind": b[2]} for b in r["bars"]],
                "_area": area,
            }
        )
    return out


def spec_option(mesh: tuple, spec: list, cover: float, shift: float) -> tuple[tuple, float]:
    """(mm²/m, equivalent Ø, largest gap, layers, centroid from the face) of the mesh with the user's bar
    layers, and the Ø setting the depth."""
    lay = bar_layers(mesh, spec, cover, shift)
    area = sum(r["_area"] for r in lay)
    centroid = sum(r["_area"] * r["from_face_mm"] for r in lay) / area
    _, phi_m, s_m, layers_m = mesh
    counts = [(layers_m * 1000 / s_m, phi_m)] + [(1000 / p[1], p[0]) for p in spec if p is not None]
    phi_eq = sum(c * f * f for c, f in counts) / sum(c * f for c, f in counts)
    gap = s_m / 2 if spec and spec[0] is not None and spec[0][1] <= s_m + 1e-9 else s_m
    big = max(f for _, f in counts)
    return (area, phi_eq, gap, len(lay), centroid - shift), big


def _touch(pieces: list[list], k: int, n: int) -> bool:
    lo, hi = sorted((k, n))
    return pieces[lo][1] + 1 == pieces[hi][0]


def zones_for(
    need: pd.DataFrame,
    ok: np.ndarray,
    options: list,
    size: float,
    x0: float,
    y0: float,
    along: str,
    min_zone: float = 2.5,
    basic: int | None = None,
    labels: list[str] | None = None,
    gaps: dict[tuple[int, int], tuple[list[tuple[int, int]], str]] | None = None,
) -> dict:
    """Basic mesh and zones for one layer.

    ``need["idx"]`` is the cheapest option each cell can take and ``ok[cell, option]`` whether
    it can take an option at all. The basic mesh is the option with the least total steel, cells
    that cannot take it getting their own option at a premium. ``gaps`` (``gap_cells``): squares with
    no result of their own, over a pile head or with no Plaxis node; a zone runs on through them with
    the bars of the zoned squares beside them along the bars, so it has no hole.
    """
    idx = need["idx"].to_numpy(int)
    areas = np.array([o[0] * (1 + LAYER_PREMIUM * (o[3] - 1)) for o in options])
    best, best_cost = int(idx.max()), math.inf
    for b in range(len(options)):
        if not ok[:, b].any():
            continue
        cost = np.where(ok[:, b], areas[b], areas[idx] * (1 + PREMIUM)).sum()
        if cost < best_cost - 1e-6:
            best, best_cost = b, cost
    if basic is not None:
        best = basic
    need = need.assign(zoned=~ok[:, best])
    zoned = need[need["zoned"]]
    if gaps:
        lvl_of = {
            (int(i), int(j)): int(v) for i, j, v in zip(zoned["i"], zoned["j"], zoned["idx"], strict=True)
        }
        extra = []
        for i, j in gaps:
            if (i, j) in lvl_of:
                continue
            beside = [(i - 1, j), (i + 1, j)] if along == "X" else [(i, j - 1), (i, j + 1)]
            lv = [lvl_of[c] for c in beside if c in lvl_of]
            if lv:
                extra.append({"i": i, "j": j, "idx": max(lv), "zoned": True})
        if extra:
            zoned = pd.concat([zoned, pd.DataFrame(extra)], ignore_index=True)
    zones = []
    # Runs of cells along the bars, split where the bars change, then merged across when they match.
    # Pieces shorter than MIN_ZONE take the heavier neighbour's bars, so bars are not cut too short.
    key_a, key_b = ("i", "j") if along == "X" else ("j", "i")
    min_cells = max(1, math.ceil(min_zone / size - 1e-9))
    runs = []
    for b_val, grp in zoned.groupby(key_b):
        grp = grp.sort_values(key_a)
        pieces: list[list] = []  # [start, end, level]
        for a_val, lvl in zip(grp[key_a], grp["idx"], strict=True):
            last = pieces[-1] if pieces else None
            if last and a_val == last[1] + 1 and lvl == last[2]:
                last[1] = a_val
            else:
                pieces.append([a_val, a_val, lvl])
        while True:
            short = [
                k
                for k, pc in enumerate(pieces)
                if pc[1] - pc[0] + 1 < min_cells
                and any(0 <= n < len(pieces) and _touch(pieces, k, n) for n in (k - 1, k + 1))
            ]
            if not short:
                break
            k = min(short, key=lambda k: (pieces[k][1] - pieces[k][0], k))
            nbrs = [n for n in (k - 1, k + 1) if 0 <= n < len(pieces) and _touch(pieces, k, n)]
            n = max(nbrs, key=lambda n: pieces[n][2])
            lo, hi = min(k, n), max(k, n)
            pieces[lo : hi + 1] = [[pieces[lo][0], pieces[hi][1], max(pieces[lo][2], pieces[hi][2])]]
            # Neighbours that now match join up.
            joined: list[list] = []
            for pc in pieces:
                if joined and joined[-1][2] == pc[2] and joined[-1][1] + 1 == pc[0]:
                    joined[-1][1] = pc[1]
                else:
                    joined.append(pc)
            pieces = joined
        runs += [(b_val, a0, a1, lvl) for a0, a1, lvl in pieces]
    merged: list[list] = []
    for b_val, a0, a1, lvl in sorted(runs, key=lambda r: (r[1], r[2], r[0])):
        last = merged[-1] if merged else None
        if last and last[1] == a0 and last[2] == a1 and last[3] == lvl and last[4] == b_val - 1:
            last[4] = b_val
        else:
            merged.append([b_val, a0, a1, lvl, b_val])
    for b0, a0, a1, lvl, b1 in merged:
        if along == "X":
            xr, yr = (x0 + a0 * size, x0 + (a1 + 1) * size), (y0 + b0 * size, y0 + (b1 + 1) * size)
        else:
            xr, yr = (x0 + b0 * size, x0 + (b1 + 1) * size), (y0 + a0 * size, y0 + (a1 + 1) * size)
        o = options[lvl]
        zones.append(
            {
                "x": [round(xr[0], 2), round(xr[1], 2)],
                "y": [round(yr[0], 2), round(yr[1], 2)],
                "label": labels[lvl] if labels else label(o),
                "as_mm2_per_m": round(o[0]),
            }
        )
    level_of = {}
    for b_val, a0, a1, lvl in runs:
        for a in range(a0, a1 + 1):
            level_of[(a, b_val) if along == "X" else (b_val, a)] = lvl
    cell_index = np.array(
        [level_of.get((i, j), best) for i, j in zip(need["i"], need["j"], strict=True)], dtype=int
    )
    o = options[best]
    return {
        "cell_index": cell_index,
        "basic": {
            "phi": o[1],
            "spacing_mm": o[2],
            "layers": o[3],
            "as_mm2_per_m": round(o[0]),
            "label": label(o),
        },
        "basic_index": best,
        "zones": zones,
    }


def _rects(cells: set, grow: int, gap: int = 0) -> list[list[int]]:
    """Rectangles [i0, i1, j0, j1] round each group of touching cells, each side at least ``grow`` + 1
    cells long, overlapping ones (or ones at most ``gap`` cells apart) merged."""
    boxes = []
    seen: set = set()
    for c0 in sorted(cells):
        if c0 in seen:
            continue
        stack, comp = [c0], []
        seen.add(c0)
        while stack:
            a, b = stack.pop()
            comp.append((a, b))
            for da in (-1, 0, 1):
                for db in (-1, 0, 1):
                    nb = (a + da, b + db)
                    if nb in cells and nb not in seen:
                        seen.add(nb)
                        stack.append(nb)
        boxes.append(
            [
                min(c[0] for c in comp),
                max(c[0] for c in comp),
                min(c[1] for c in comp),
                max(c[1] for c in comp),
            ]
        )
    for bx in boxes:
        for lo, hi in ((0, 1), (2, 3)):
            short_by = grow - (bx[hi] - bx[lo])
            if short_by > 0:
                bx[lo] -= short_by // 2
                bx[hi] += short_by - short_by // 2
    merged = True
    while merged:
        merged = False
        for m in range(len(boxes)):
            for n in range(m + 1, len(boxes)):
                p, q = boxes[m], boxes[n]
                g = 1 + gap
                if p[0] <= q[1] + g and q[0] <= p[1] + g and p[2] <= q[3] + g and q[2] <= p[3] + g:
                    boxes[m] = [min(p[0], q[0]), max(p[1], q[1]), min(p[2], q[2]), max(p[3], q[3])]
                    del boxes[n]
                    merged = True
                    break
            if merged:
                break
    return boxes


def zone_key(layer: str, zone: dict) -> str:
    """'layer|zone|x0|x1|y0|y1' naming a zone of additional bars, for bars the user sets there."""
    return f"{layer}|zone|{zone['x'][0]:g}|{zone['x'][1]:g}|{zone['y'][0]:g}|{zone['y'][1]:g}"


def area_zones(
    cell: pd.DataFrame, ok: np.ndarray, options: list, labels: list, size: float, x0, y0, box, min_zone
) -> dict:
    """The basic mesh (option 0) over the whole slab, and a rectangle of additional bars round each
    group of cells that needs more, at least min_zone each way; each zone takes the lightest bars
    every cell in it can take, so an area of higher moments is one design section."""
    first_ok = np.where(ok.any(axis=1), ok.argmax(axis=1), ok.shape[1] - 1)
    ci, cj = cell["i"].to_numpy(), cell["j"].to_numpy()
    need = {(int(i), int(j)) for i, j, f in zip(ci, cj, first_ok, strict=True) if f > 0}
    grow = max(0, math.ceil(min_zone / size - 1e-9) - 1)
    chosen = np.zeros(len(cell), int)
    zones = []
    # Zones closer than the shortest bars merge: no gap of plain mesh shorter than a zone.
    for i0, i1, j0, j1 in sorted(_rects(need, grow, grow + 1), key=lambda r: (r[0], r[2])):
        idxs = np.flatnonzero((ci >= i0) & (ci <= i1) & (cj >= j0) & (cj <= j1))
        both = ok[idxs].all(axis=0)
        lvl = int(np.argmax(both)) if both.any() else int(first_ok[idxs].max())
        chosen[idxs] = lvl
        o = options[lvl]
        zones.append(
            {
                "x": [
                    round(max(box["X"][0], x0 + i0 * size), 2),
                    round(min(box["X"][1], x0 + (i1 + 1) * size), 2),
                ],
                "y": [
                    round(max(box["Y"][0], y0 + j0 * size), 2),
                    round(min(box["Y"][1], y0 + (j1 + 1) * size), 2),
                ],
                "label": labels[lvl],
                "as_mm2_per_m": round(o[0]),
                "cells": int(len(idxs)),
            }
        )
    o = options[0]
    return {
        "cell_index": chosen,
        "basic": {
            "phi": o[1],
            "spacing_mm": o[2],
            "layers": o[3],
            "as_mm2_per_m": round(o[0]),
            "label": label(o),
        },
        "basic_index": 0,
        "zones": zones,
    }


def link_bands(cells, req, opts, labels, size, x0, y0, box, min_zone, along) -> tuple[list[dict], int]:
    """Shear links in bands across the deck, as the office's slab sheets (e.g. T16 from 0 to 6 m, T10
    from 6 to 9.8 m): along ``along`` each 1 m cut takes the links its worst cell needs; runs of equal
    links shorter than ``min_zone`` take the heavier neighbour's, and each band runs the full width.
    ``req`` is each cell's need (mm²/m²); ``opts`` (mm²/m², Ø, sx, sy) lightest first, 0 = none."""
    if not len(cells):
        return [], 0
    level = np.array(
        [next((m for m, o in enumerate(opts) if m and o[0] >= r - 1e-6), len(opts)) for r in req]
    )
    short = int((level >= len(opts)).sum())
    level = np.minimum(level, len(opts) - 1)
    key = cells["i" if along == "X" else "j"].to_numpy()
    by_cut = pd.Series(level).groupby(key).max()
    worst = pd.Series(req).groupby(key).max()
    lo, hi = int(by_cut.index.min()), int(by_cut.index.max())
    pieces = []  # [start, end, level]
    for c in range(lo, hi + 1):
        lv = int(by_cut.get(c, 0))
        if pieces and pieces[-1][2] == lv and pieces[-1][1] == c - 1:
            pieces[-1][1] = c
        else:
            pieces.append([c, c, lv])
    min_cells = max(1, math.ceil(min_zone / size - 1e-9))
    while len(pieces) > 1:
        shorts = [k for k, pc in enumerate(pieces) if pc[1] - pc[0] + 1 < min_cells]
        if not shorts:
            break
        k = min(shorts, key=lambda k: (pieces[k][1] - pieces[k][0], -pieces[k][2], k))
        nbrs = [n for n in (k - 1, k + 1) if 0 <= n < len(pieces)]
        n = max(nbrs, key=lambda n: pieces[n][2])
        a, b = min(k, n), max(k, n)
        pieces[a : b + 1] = [[pieces[a][0], pieces[b][1], max(pieces[a][2], pieces[b][2])]]
        joined: list[list] = []
        for pc in pieces:
            if joined and joined[-1][2] == pc[2]:
                joined[-1][1] = pc[1]
            else:
                joined.append(pc)
        pieces = joined
    ax, cx = ("X", "Y") if along == "X" else ("Y", "X")
    a0 = x0 if along == "X" else y0
    out = []
    for c0, c1, lv in pieces:
        if lv == 0:
            continue
        o = opts[lv]
        span = [round(max(box[ax][0], a0 + c0 * size), 2), round(min(box[ax][1], a0 + (c1 + 1) * size), 2)]
        inside = (key >= c0) & (key <= c1)
        out.append(
            {
                ax.lower(): span,
                cx.lower(): [round(box[cx][0], 2), round(box[cx][1], 2)],
                "phi": o[1],
                "sx_mm": o[2],
                "sy_mm": o[3],
                "asw_mm2_per_m2": round(o[0]),
                "needs_mm2_per_m2": round(float(worst.loc[c0:c1].max())),
                "cells": int(inside.sum()),
                "label": labels[lv],
            }
        )
    return out, short


def missing_piles(rows_of: dict[str, list[tuple[float, float]]]) -> list[str]:
    """Rows of piles with a gap where the other rows have a pile: the workbook has no results for a pile
    there, so the slab over it is designed as if it were not there."""
    lines: dict[str, tuple[int, float, set]] = {}
    for name, pts in rows_of.items():
        if len(pts) < 3:
            continue
        xs, ys = {p[0] for p in pts}, {p[1] for p in pts}
        # A row along Y has one X and many Y, and the other way round.
        axis = 1 if len(ys) >= len(xs) else 0
        lines[name] = (axis, float(np.median([p[1 - axis] for p in pts])), {p[axis] for p in pts})
    out = []
    for name, (axis, at, have) in sorted(lines.items()):
        others = set().union(*(v[2] for n, v in lines.items() if n != name and v[0] == axis))
        gaps = sorted(v for v in others if min(abs(v - h_) for h_ in have) > 0.3)
        gaps = [
            v for v in gaps if sum(v in lines[n][2] for n in lines if n != name and lines[n][0] == axis) >= 2
        ]
        if gaps:
            where = ", ".join(f"{'XY'[axis]} {v:g}" for v in gaps)
            out.append(
                f"{name} has no results at {where} ({'YX'[axis]} {at:g}), where the other rows of piles "
                "have a pile: if there is one, its rows are missing from the workbook and the slab over it "
                "is designed without it."
            )
    return out


# --- Punching -------------------------------------------------------------------------------------


def pile_heads(
    pile_sheets: dict[str, dict[str, SheetData]], elements: dict[str, Any], box: dict, above: float
) -> list[dict]:
    """ULS axial force and head moments of every pile whose head is inside the slab's plan box."""
    out = []
    for name, sheets in pile_sheets.items():
        el = elements.get(name)
        if not isinstance(el, PileInput):
            continue
        for combo, sheet in sheets.items():
            if combination_type(combo) is CombinationType.SLS_QP or sheet.frame.empty:
                continue
            f = sheet.frame
            if not {"N", "M_2", "M_3"} <= set(f.columns):
                continue
            if el.head_level is not None:
                f = f[f["Z"] <= el.head_level + above + 1e-9]
            f = f.assign(px=f["X"].round(2), py=f["Y"].round(2))
            # At the slab soffit, as in the pile design: the points from the pile's top level to
            # ``above`` inside the slab, or the topmost point when there are none.
            zmax = f.groupby(["px", "py"])["Z"].transform("max")
            if el.head_level is not None:
                top = f[f["Z"] >= np.minimum(zmax, el.head_level) - 1e-6]
            else:
                top = f[f["Z"] >= zmax - 1e-6]
            for _, r in top.iterrows():
                if not (box["X"][0] - 1e-6 <= r["X"] <= box["X"][1] + 1e-6):
                    continue
                if not (box["Y"][0] - 1e-6 <= r["Y"] <= box["Y"][1] + 1e-6):
                    continue
                out.append(
                    {
                        "pile": name,
                        "x": float(r["px"]),
                        "y": float(r["py"]),
                        "D": el.diameter,
                        "combination": combo,
                        "N": -float(r["N"]),  # concrete sign: compression +
                        "M": float(math.hypot(r["M_2"], r["M_3"])),
                    }
                )
    return out


KMAX = 1.5  # EN 1992-1-1/A1 6.4.5(1), recommended


def punching_depth(slab: SlabInput, x: float, y: float) -> tuple[float, str]:
    """Slab thickness for punching at a pile: its own entry, the slab's punching value, or the thickness."""
    for p in slab.punching_depths:
        if math.hypot(p.x - x, p.y - y) <= 0.5:
            return p.thickness, "set for this pile"
    if slab.punching_thickness:
        return slab.punching_thickness, "slab thickness for punching"
    return slab.thickness, "slab thickness"


def punching(
    heads: list[dict],
    slab: SlabInput,
    settings: DesignSettings,
    rho_at,
    conc,
    cover: float,
    beams: list[dict],
) -> list[dict]:
    """6.4 punching at each pile head, each with the slab thickness at that pile (see ``punching_depth``)."""
    pf = settings.partial_factors
    fck = conc.fck
    fcd = pf.alpha_cc * fck / pf.gamma_c
    fywd_ef = lambda d: min(250 + 0.25 * d, REINFORCEMENT_GRADES[settings.reinforcement.grade] / pf.gamma_s)  # noqa: E731
    nu = 0.6 * (1 - fck / 250)
    by_pile: dict[tuple, list[dict]] = {}
    for hd in heads:
        if any(
            b["box"]["X"][0] <= hd["x"] <= b["box"]["X"][1]
            and b["box"]["Y"][0] <= hd["y"] <= b["box"]["Y"][1]
            for b in beams
        ):
            continue  # under a beam: the beam carries it
        by_pile.setdefault((hd["pile"], hd["x"], hd["y"]), []).append(hd)
    out = []
    for (pile, x, y), rows in sorted(by_pile.items(), key=lambda kv: (kv[0][0], kv[0][2], kv[0][1])):
        h, h_from = punching_depth(slab, x, y)
        d = h - cover - 20  # to the mean of the two layers of Ø20
        k = min(1 + math.sqrt(200 / d), 2.0)
        v_min = 0.035 * k**1.5 * math.sqrt(fck)
        D = rows[0]["D"]
        u0 = math.pi * D
        u1 = math.pi * (D + 4 * d)
        worst = None
        for r in rows:
            v = abs(r["N"])
            if v <= 0:
                continue
            e = r["M"] / v * 1000  # mm
            beta = 1 + 0.6 * math.pi * e / (D + 4 * d)
            face = "top" if r["N"] >= 0 else "bottom"  # a pile pushing up: the top is in tension
            rho = min(rho_at(x, y, face), 0.02)
            vrdc = max(0.18 / pf.gamma_c * k * (100 * rho * fck) ** (1 / 3), v_min)
            ved = beta * v * 1e3 / (u1 * d)
            beta0 = beta  # EC2 6.4.5(3): the β of u1 at the face too
            if slab.punching_face_beta == "office":
                beta0 = max(beta, 1 + 0.6 * math.pi * e / D)  # from the pile diameter, as the office sheets
            ved0 = beta0 * v * 1e3 / (u0 * d)
            vrdmax = 0.4 * nu * fcd
            u = max(ved / vrdc, ved0 / vrdmax)
            if worst is None or u > worst["utilisation"]:
                worst = {
                    "pile": pile,
                    "x": x,
                    "y": y,
                    "D_mm": D,
                    "thickness_mm": h,
                    "thickness_from": h_from,
                    "d_mm": round(d),
                    "combination": r["combination"],
                    "V_kN": round(v, 1),
                    "direction": "pile pushes up" if r["N"] >= 0 else "pile pulls down",
                    "beta": round(beta, 3),
                    "rho_l": round(rho, 4),
                    "u1_mm": round(u1),
                    "vEd_MPa": round(ved, 3),
                    "vRd_c_MPa": round(vrdc, 3),
                    "vEd_face_MPa": round(ved0, 3),
                    "vRd_max_MPa": round(vrdmax, 2),
                    "utilisation": round(u, 3),
                    "_v": v,
                    "_beta": beta,
                    "_vrdc": vrdc,
                    "_e": e,
                }
        if worst is None:
            continue
        worst["r_u1_mm"] = round(D / 2 + 2 * d)
        needs = worst["vEd_MPa"] > worst["vRd_c_MPa"]
        worst["needs_reinforcement"] = bool(needs)
        worst["passed"] = worst["vEd_face_MPa"] <= worst["vRd_max_MPa"]
        # EN 1992-1-1/A1 6.4.5(1): links can raise the resistance to kmax·vRd,c at most.
        worst["kmax_ratio"] = round(worst["vEd_MPa"] / (KMAX * worst["vRd_c_MPa"]), 3)
        if needs and worst["kmax_ratio"] > 1:
            worst["passed"] = False
        if needs and worst["passed"]:
            # 6.52 with sr = 0.75d: Asw per perimeter.
            sr = 0.75 * d
            asw = (worst["vEd_MPa"] - 0.75 * worst["_vrdc"]) * u1 * sr / (1.5 * fywd_ef(d))
            u_out = worst["_beta"] * worst["_v"] * 1e3 / (worst["_vrdc"] * d)
            r_out = u_out / math.pi / 2 - D / 2  # from the pile face
            perimeters = max(2, math.ceil((r_out - 1.5 * d) / sr) + 1)
            worst |= {
                "utilisation_with_links": round(
                    max(worst["vEd_face_MPa"] / worst["vRd_max_MPa"], worst["kmax_ratio"]), 3
                ),
                "asw_mm2_per_perimeter": round(asw),
                "radial_spacing_mm": round(sr),
                "u_out_mm": round(u_out),
                "perimeters": perimeters,
                "reinforced_to_mm": round(0.5 * d + (perimeters - 1) * sr),
                "r_out_mm": round(u_out / (2 * math.pi)),
                "link_radii_mm": [round(D / 2 + 0.5 * d + i * sr) for i in range(perimeters)],
            }
        if not worst["passed"]:
            worst["fix"] = _punching_fix(worst, h, D, cover, fck, fcd, nu, pf.gamma_c)
        for k_ in ("_v", "_beta", "_vrdc", "_e"):
            worst.pop(k_)
        out.append(worst)
    return out


# What a pile head's punching design is: taken whole from the type's governing head when unified.
_PUNCH_DESIGN = (
    "combination",
    "V_kN",
    "direction",
    "beta",
    "rho_l",
    "u1_mm",
    "vEd_MPa",
    "vRd_c_MPa",
    "vEd_face_MPa",
    "vRd_max_MPa",
    "utilisation",
    "r_u1_mm",
    "needs_reinforcement",
    "passed",
    "kmax_ratio",
    "utilisation_with_links",
    "asw_mm2_per_perimeter",
    "radial_spacing_mm",
    "u_out_mm",
    "perimeters",
    "reinforced_to_mm",
    "r_out_mm",
    "link_radii_mm",
    "fix",
)
# The head's own check, kept for checking when the type is unified.
_PUNCH_OWN = (
    "combination",
    "V_kN",
    "direction",
    "beta",
    "rho_l",
    "vEd_MPa",
    "vRd_c_MPa",
    "vEd_face_MPa",
    "vRd_max_MPa",
    "utilisation",
    "utilisation_with_links",
    "needs_reinforcement",
    "passed",
    "perimeters",
    "asw_mm2_per_perimeter",
)


def _punch_rank(q: dict) -> tuple:
    """Worst first: failing, then needing links, then the largest vEd/vRd,c (or face) ratio."""
    return (not q.get("passed"), bool(q.get("needs_reinforcement")), q.get("utilisation") or 0.0)


def unify_punching(heads: list[dict], per: str = "type") -> tuple[list[dict], list[dict]]:
    """One punching design per pile type, as detailed on site: every head of a type takes the design of
    the type's governing head (the worst: failing, needing links, then the largest utilisation), with
    the links enveloping the type (most perimeters, most Asw per perimeter). Each head keeps its own
    check under ``own``. ``per="head"`` leaves each head its own design. Returns the heads and one
    summary row per type."""
    by: dict[str, list[dict]] = {}
    for q in heads:
        by.setdefault(q["pile"], []).append(q)
    out: list[dict] = []
    types: list[dict] = []
    for pile, rows in by.items():
        gov = max(rows, key=_punch_rank)
        design = {k: gov[k] for k in _PUNCH_DESIGN if k in gov}
        linked = [q for q in rows if q.get("needs_reinforcement") and q.get("perimeters")]
        if per == "type" and design.get("needs_reinforcement") and design.get("perimeters") and linked:
            # Enough links for every head of the type, on the governing head's perimeters.
            n = max(q["perimeters"] for q in linked)
            sr = design["radial_spacing_mm"]
            first = design["link_radii_mm"][0]
            design |= {
                "perimeters": n,
                "asw_mm2_per_perimeter": max(q["asw_mm2_per_perimeter"] for q in linked),
                "utilisation_with_links": max(q.get("utilisation_with_links", 0.0) for q in linked),
                "link_radii_mm": [round(first + i * sr) for i in range(n)],
                "reinforced_to_mm": round(design["reinforced_to_mm"] + (n - design["perimeters"]) * sr),
            }
        thick = sorted({q["thickness_mm"] for q in rows})
        types.append(
            {
                "pile": pile,
                "heads": len(rows),
                "D_mm": gov["D_mm"],
                "governing_x": gov["x"],
                "governing_y": gov["y"],
                "thickness_mm": gov["thickness_mm"],
                "thicknesses_mm": thick,
                "d_mm": gov["d_mm"],
                "heads_needing_links_alone": sum(1 for q in rows if q.get("needs_reinforcement")),
                "heads_failing_alone": sum(1 for q in rows if not q.get("passed")),
                **design,
                "passed": all(q.get("passed") for q in rows),
                "unified": per == "type",
            }
        )
        for q in rows:
            if per != "type":
                out.append(q)
                continue
            own = {k: q[k] for k in _PUNCH_OWN if k in q}
            one = {k: v for k, v in q.items() if k not in _PUNCH_DESIGN}
            one |= design | {
                "own": own,
                "unified": True,
                "governing": q is gov,
                "passed": types[-1]["passed"],
            }
            out.append(one)
    out.sort(key=lambda q: (q["pile"], q["y"], q["x"]))
    return out, types


def _punching_fix(
    w: dict, h: float, D: float, cover: float, fck: float, fcd: float, nu: float, gc: float
) -> dict:
    """What would make a failing pile head pass: more steel on the tension face (so links can take the
    rest, up to kmax·vRd,c), or a thicker slab at the pile, with and without links. Same V and e."""
    v, e, rho = w["_v"], w["_e"], w["rho_l"]

    def at(hh: float, rr: float) -> tuple[float, float, float]:
        d = hh - cover - 20
        k = min(1 + math.sqrt(200 / d), 2.0)
        vrdc = max(0.18 / gc * k * (100 * min(rr, 0.02) * fck) ** (1 / 3), 0.035 * k**1.5 * math.sqrt(fck))
        u1 = math.pi * (D + 4 * d)
        beta = 1 + 0.6 * math.pi * e / (D + 4 * d)
        ved = beta * v * 1e3 / (u1 * d)
        face = beta * v * 1e3 / (math.pi * D * d) / (0.4 * nu * fcd)
        return ved / vrdc, ved / (KMAX * vrdc), face

    out: dict = {}
    d = h - cover - 20
    k = min(1 + math.sqrt(200 / d), 2.0)
    need = w["vEd_MPa"] / KMAX / (0.18 / gc * k)
    rho_req = need**3 / (100 * fck)
    if w["vEd_face_MPa"] <= w["vRd_max_MPa"]:
        out["rho_l_with_links"] = round(rho_req, 4) if rho_req <= 0.02 else None
        if rho_req <= 0.02:
            out["as_mm2_per_m_with_links"] = round(rho_req * d * 1000)
    for label, pick in (
        ("with_links", lambda r: max(r[1], r[2])),
        ("without_links", lambda r: max(r[0], r[2])),
    ):
        for hh in range(int(h) + 50, int(h) + 2001, 50):
            if pick(at(hh, rho)) <= 1.0:
                out[f"thickness_mm_{label}"] = hh
                break
    return out


def _bars_for(need: float, mesh_spacing: float, diameters: list[int]) -> tuple[int, float]:
    """The smallest bar at the mesh spacing (one under each mesh bar), else at half of it, giving
    at least ``need`` mm²/m."""
    sizes = sorted(p for p in diameters if p >= 10) or [16]
    for s in (mesh_spacing, mesh_spacing / 2):
        for phi in sizes:
            if 1000 * math.pi * phi * phi / 4 / s >= need - 1e-6:
                return phi, s
    return sizes[-1], mesh_spacing / 2


def _punching_bars(punch, punch_types, slab, settings, layers, strip_rows, as_at, extra, rerun):
    """Heads that fail because links alone cannot carry the punching (vEd over kmax·vRd,c) get bars
    added over the pile on the face in tension, both ways, until ρl gives kmax·vRd,c ≥ vEd (the fix's
    ρl with links); links then carry the rest. With one design per pile type, every head of the type
    gets the same bars. Over a width of D + 6d (EN 1992-1-1 6.4.4(1): 3d each side of the pile), each
    bar running the lap length past it each way. Returns the punching and the bars."""
    r = settings.reinforcement
    groups: dict[str, dict] = {}
    for _ in range(4):
        changed = False
        rows = (
            [(t["pile"], t) for t in punch_types if t.get("unified")]
            if slab.punching_per == "type"
            else [(f"{q['pile']} {q['x']:g},{q['y']:g}", q) for q in punch]
        )
        for key, q in rows:
            fix = q.get("fix") or {}
            rho_req = fix.get("rho_l_with_links")
            if q.get("passed") or not rho_req:
                continue  # passes, or crushing at the face / over 2%: only a thicker slab
            gx, gy = (q["governing_x"], q["governing_y"]) if "governing_x" in q else (q["x"], q["y"])
            face = "top" if q["direction"] == "pile pushes up" else "bottom"
            g = groups.setdefault(key, {"face": face, "dirs": {}})
            for direction in ("x", "y"):
                lay = layers[f"{face}_{direction}"]
                need = rho_req * 1.001 * 1000 * lay["d_mm"] - as_at(gx, gy, face, direction)
                have = g["dirs"].get(direction, {}).get("as_mm2_per_m", 0.0)
                if need > have + 1e-6:
                    phi, sp = _bars_for(need, lay["basic"]["spacing_mm"], r.bar_diameters)
                    g["dirs"][direction] = {
                        "phi": phi,
                        "spacing_mm": sp,
                        "as_mm2_per_m": round(1000 * math.pi * phi * phi / 4 / sp),
                        "needed_mm2_per_m": round(need),
                    }
                    changed = True
            if slab.punching_per == "type":
                g["heads"] = [(h["x"], h["y"], h) for h in punch if h["pile"] == q["pile"]]
            else:
                g["heads"] = [(q["x"], q["y"], q)]
            g["pile"], g["D_mm"], g["d_mm"] = q["pile"], q["D_mm"], q["d_mm"]
            g["rho_required"] = rho_req
        if not changed:
            break
        for g in groups.values():
            for x, y, _ in g["heads"]:
                extra[(round(x, 2), round(y, 2), g["face"])] = {
                    k: v["as_mm2_per_m"] for k, v in g["dirs"].items()
                }
        punch, punch_types = rerun()
    out = []
    for g in groups.values():
        if not g["dirs"]:
            continue
        width = g["D_mm"] + 6 * g["d_mm"]
        dirs = {}
        for direction, b in g["dirs"].items():
            lay = layers[f"{g['face']}_{direction}"]
            length = math.ceil((width + 2 * settings.piles.lap_factor * b["phi"]) / 100) * 100
            count = math.floor(width / b["spacing_mm"]) + 1
            # Inside every layer already there at this face and direction (mesh, zones, strip rows).
            deepest = [
                (row["from_face_mm"], max(bb["diameter_mm"] for bb in row["bars"]))
                for src in (
                    [lay.get("mesh_bar_layers") or []]
                    + [z.get("bar_layers") or [] for z in lay.get("zones") or []]
                    + [
                        sr.get("bar_layers") or []
                        for sr in strip_rows
                        if sr.get("layer") == f"{g['face']}_{direction}"
                    ]
                )
                for row in src
                if row.get("bars")
            ]
            at, prev = max(deepest, default=(lay.get("cover_mm", 50) + b["phi"] / 2, 0))
            if prev:
                at += prev / 2 + max(25.0, prev, b["phi"]) + b["phi"] / 2
            dirs[direction] = {
                **b,
                "label": f"Ø{b['phi']} @ {b['spacing_mm']:g}",
                "count": count,
                "length_mm": length,
                "from_face_mm": round(at),
            }
        heads = [(x, y) for x, y, _ in g["heads"]]
        kg = sum(
            len(heads)
            * v["count"]
            * v["length_mm"]
            / 1000
            * math.pi
            * v["phi"] ** 2
            / 4
            * STEEL_DENSITY
            / 1e6
            for v in dirs.values()
        )
        text = f"{g['face']} bars over the pile: " + ", ".join(
            f"{v['count']} {v['label']} along {k.upper()}, {v['length_mm']:g} long" for k, v in dirs.items()
        )
        out.append(
            {
                "pile": g["pile"],
                "face": g["face"],
                "heads": [[round(x, 2), round(y, 2)] for x, y in heads],
                "width_mm": round(width),
                "rho_l_required": round(g["rho_required"], 4),
                "directions": dirs,
                "text": text,
                "kg": round(kg, 1),
            }
        )
    by_head = {(round(x, 2), round(y, 2)): b["text"] for b in out for x, y in b["heads"]}
    for q in punch:
        if (t := by_head.get((round(q["x"], 2), round(q["y"], 2)))) is not None:
            q["added_bars"] = t
    for t in punch_types:
        hit = next((b for b in out if b["pile"] == t["pile"]), None)
        if hit is not None:
            t["added_bars"] = hit["text"]
    return punch, punch_types, out


# --- Restraint --------------------------------------------------------------------------------------


def slab_restraint(o, face, direction, covers, h, settings, conc, R) -> dict:
    cr = settings.cracking
    c = covers[face] + (o[1] if direction == "y" else 0)
    return restraint_crack(
        restraint=R * cr.creep_factor,
        t1=cr.early_age_drop,
        t2=cr.seasonal_drop,
        alpha=cr.thermal_expansion * 1e-6,
        eps_ca=autogenous_shrinkage(conc.fck),
        fctm=conc.fctm,
        ecm=conc.ecm,
        area=o[0],
        b=1000,
        hc=min(2.5 * (c + o[1] / 2), h / 2),
        phi=o[1],
        cover=c,
        spacing=o[2],
        member=h,
    )


def restraint_floor(options, face, direction, covers, limits, h, slab, settings, conc, R) -> int:
    """The least option whose restraint crack width meets the face's limit."""
    for i, o in enumerate(options):
        if slab_restraint(o, face, direction, covers, h, settings, conc, R)["wk"] <= limits[face] + 1e-9:
            return i
    return len(options) - 1


# --- Slab design ----------------------------------------------------------------------------------


def design_slab_meshes(
    name: str,
    slab: SlabInput,
    settings: DesignSettings,
    sheets: dict[str, SheetData],
    geometry: list[dict],
    elements: dict[str, Any],
    axes: dict[str, str] | None,
    pile_sheets: dict[str, dict[str, SheetData]],
    choices: SlabStrips | None = None,
    sign: dict[str, Any] | None = None,
    standard: bool = False,
) -> dict[str, Any]:
    """The slab designed with each mesh spacing of Design settings on its own (150 and 200 mm by
    default): the one picked (``choices.spacing``, else the lighter whose bars are enough) is the slab's
    result,
    and ``mesh_choice`` gives every spacing's steel so the user can switch. ``standard``: the quick
    overview (Standard design), with one spacing only: the one picked, else the first in Design
    settings."""
    r = settings.reinforcement
    spacings = sorted({float(v) for v in r.slab_spacings})
    args = (sheets, geometry, elements, axes, pile_sheets, choices, sign)
    if standard and len(spacings) > 1:
        want = choices.spacing if choices is not None else None
        if want is None and choices is not None and choices.bars:
            want = _spacing_of_bars(choices.bars.values(), spacings)
        first = float(r.slab_spacings[0])
        sp = next((v for v in spacings if want is not None and abs(v - want) < 1e-6), first)
        one = settings.model_copy(update={"reinforcement": r.model_copy(update={"slab_spacings": [sp]})})
        d = design_slab(name, slab, one, *args)
        others = ", ".join(f"{v:g}" for v in spacings if v != sp)
        d["notes"].insert(
            0,
            f"Meshes at {sp:g} mm only (Standard design; {others} mm is compared in a Detailed design).",
        )
        return _with_openings(d, slab, settings, sheets, axes, sign)
    if len(spacings) < 2:
        return _with_openings(design_slab(name, slab, settings, *args), slab, settings, sheets, axes, sign)
    runs = {}
    for sp in spacings:
        one = settings.model_copy(update={"reinforcement": r.model_copy(update={"slab_spacings": [sp]})})
        runs[sp] = design_slab(name, slab, one, *args)
    options = []
    for sp, d in runs.items():
        kg = (d.get("steel") or {}).get("kg_per_m3")
        options.append(
            {
                "spacing_mm": sp,
                "kg_per_m3": kg,
                "ratio_pct": None if kg is None else round(100 * kg / STEEL_DENSITY, 2),
                "utilisation": d.get("utilisation"),
                "passed": d.get("passed"),
            }
        )
    # The lighter of those whose bars carry the loads (punching fails or passes with either mesh).
    lighter = min(options, key=lambda o: ((o["utilisation"] or 0) > 1 + 1e-6, o["kg_per_m3"] or math.inf))[
        "spacing_mm"
    ]
    want = choices.spacing if choices is not None else None
    if want is None and choices is not None and choices.bars:
        want = _spacing_of_bars(choices.bars.values(), spacings)
    picked = next((sp for sp in spacings if want is not None and abs(sp - want) < 1e-6), None)
    chosen = picked if picked is not None else lighter
    d = runs[chosen]
    d["mesh_choice"] = {
        "options": options,
        "chosen_mm": chosen,
        "picked": picked is not None,
        "lighter_mm": lighter,
        "from_bars": picked is not None and choices.spacing is None,
    }

    def text(o: dict) -> str:
        short = (o["utilisation"] or 0) > 1 + 1e-6
        kg = "no bars" if o["kg_per_m3"] is None else f"{o['kg_per_m3']:g} kg/m³"
        return f"{kg} at {o['spacing_mm']:g} mm" + (
            f", where the heaviest bars are not enough ({o['utilisation']:.2f})" if short else ""
        )

    others = "; ".join(text(o) for o in options if o["spacing_mm"] != chosen)
    how = (
        "as you picked"
        if picked is not None and choices.spacing is not None
        else "the spacing of the bars you set"
        if picked is not None
        else "the lighter whose bars are enough"
    )
    d["notes"].insert(0, f"Meshes at {chosen:g} mm ({how}; {others}). Everything below is for {chosen:g} mm.")
    if want is not None and picked is None:
        d["notes"].insert(
            1, f"The spacing picked ({want:g} mm) is not in Design settings: the lighter is used."
        )
    return _with_openings(d, slab, settings, sheets, axes, sign)


def _with_openings(d: dict, slab: SlabInput, settings: DesignSettings, sheets, axes, sign) -> dict:
    """The slab's manholes and channels (Openings tab), checked with the slab's bars as designed."""
    if not (slab.manholes or slab.channels) or not d.get("layers"):
        return d
    from .openings import design_slab_openings

    slab = with_project_grades(slab, settings.materials, settings.durability)
    sag, _ = sag_factor(settings.plate_positive_moment, sign)
    uls = slab_loads(sheets, axes, sag, qp=False)
    qp = slab_loads(sheets, axes, sag, qp=True)
    op = design_slab_openings(slab, settings, uls, qp, d)
    d["openings"] = op
    every = op["manholes"] + op["channels"]
    for o in every:
        d["notes"].append(
            f"{o['name']}: "
            + (f"passes, utilisation {o['utilisation']:.2f}." if o["passed"] else "fails (Openings tab).")
        )
    us = [o["utilisation"] for o in every if o.get("utilisation") is not None]
    if us:
        d["utilisation"] = round(max([d.get("utilisation") or 0.0, *us]), 3)
    d["passed"] = bool(d.get("passed")) and all(o["passed"] for o in every)
    return d


def _spacing_of_bars(labels, spacings: list[float]) -> float | None:
    """The mesh spacing bars set by the user were chosen on (saved before the spacing was picked): the one
    all their spacings fit (s/2, s or 2s), when only one does."""
    import re

    got = [float(v) for text in labels for v in re.findall(r"@\s*([\d.]+)", text)]
    if not got:
        return None
    fits = [sp for sp in spacings if all(any(abs(g - k * sp) < 1e-6 for k in (0.5, 1, 2)) for g in got)]
    return fits[0] if len(fits) == 1 else None


GAP_WHY = {
    "pile": "over a pile head: the results inside the pile are FE peaks in the connection and are left "
    "out, so this square shows the worst of the squares round it (the pile faces, where the bars are "
    "designed)",
    "no node": "no Plaxis node falls in this square (the Plaxis mesh is coarser than the 1 m grid here), "
    "so it shows the worst of the squares round it",
}


def gap_cells(
    have: set[tuple[int, int]],
    ni: int,
    nj: int,
    x0: float,
    y0: float,
    size: float,
    box: dict[str, list[float]],
    piles: list[tuple[float, float, float]],
) -> dict[tuple[int, int], tuple[list[tuple[int, int]], str]]:
    """Squares of the slab's grid with no result of their own, each with the squares it borrows from.

    A square counts when it lies over a pile head, or when it has results on at least three of its four
    sides along its row and column (a hole in the Plaxis mesh inside the slab or at its edge, not a
    square beyond the slab's outline, as past the sloped edge of a turned part's corner). It borrows
    from the nearest ring of squares with results round it.
    Returns ``{(i, j): (donor squares, "pile" | "no node")}``.
    """
    out: dict[tuple[int, int], tuple[list[tuple[int, int]], str]] = {}
    if not have:
        return out
    rows: dict[int, list[int]] = {}
    cols: dict[int, list[int]] = {}
    for i, j in have:
        rows.setdefault(j, []).append(i)
        cols.setdefault(i, []).append(j)
    for i in range(ni):
        for j in range(nj):
            if (i, j) in have:
                continue
            cx, cy = x0 + (i + 0.5) * size, y0 + (j + 0.5) * size
            if cx > box["X"][1] + 1e-6 or cy > box["Y"][1] + 1e-6:
                continue
            over_pile = any(math.hypot(cx - px, cy - py) <= pr + size * 0.75 for px, py, pr in piles)
            r, c = rows.get(j, []), cols.get(i, [])
            sides = (
                any(a < i for a in r) + any(a > i for a in r) + any(b < j for b in c) + any(b > j for b in c)
            )
            inside = sides >= 3  # an edge square of the slab has results on three sides
            if not (over_pile or inside):
                continue
            donors: list[tuple[int, int]] = []
            for ring in range(1, 4):
                donors = [
                    (i + di, j + dj)
                    for di in range(-ring, ring + 1)
                    for dj in range(-ring, ring + 1)
                    if max(abs(di), abs(dj)) == ring and (i + di, j + dj) in have
                ]
                if donors:
                    break
            if donors:
                out[(i, j)] = (donors, "pile" if over_pile else "no node")
    return out


def design_slab(
    name: str,
    slab: SlabInput,
    settings: DesignSettings,
    sheets: dict[str, SheetData],
    geometry: list[dict],
    elements: dict[str, Any],
    axes: dict[str, str] | None,
    pile_sheets: dict[str, dict[str, SheetData]],
    choices: SlabStrips | None = None,
    sign: dict[str, Any] | None = None,
) -> dict[str, Any]:
    slab = with_project_grades(slab, settings.materials, settings.durability)
    choices = choices or SlabStrips()
    # Labels saved before "under the mesh" was renamed "behind the mesh bars" (it is inside the mesh).
    old = {k: v.replace(" under the mesh", " behind the mesh bars") for k, v in choices.bars.items()}
    if old != choices.bars:
        choices = choices.model_copy(update={"bars": old})
    conc = concrete(slab.concrete)
    fyk = REINFORCEMENT_GRADES[settings.reinforcement.grade]
    fyd = fyk / settings.partial_factors.gamma_s
    e_eff = conc.ecm / (1 + settings.cracking.creep_coefficient)
    sag, sign_note = sag_factor(settings.plate_positive_moment, sign)
    h = slab.thickness
    size = slab.zone_size
    uls = slab_loads(sheets, axes, sag, qp=False)
    qp = slab_loads(sheets, axes, sag, qp=True)
    uls, crane_rows = add_crane(uls, slab)
    options = bar_options(settings)
    meshes = {}
    for layer in LAYERS:
        m = getattr(slab, f"mesh_{layer}")
        if m is not None:
            o = (m.layers * 1000 * math.pi * m.diameter**2 / 4 / m.spacing, m.diameter, m.spacing, m.layers)
            if o not in options:
                options.append(o)
            meshes[layer] = o
    options.sort(key=lambda o: (o[0] * (1 + LAYER_PREMIUM * (o[3] - 1)), -o[1]))
    notes = [
        "Bars along X take Mx = "
        + _map(axes)["Mx"].replace("_", "")
        + (" (from the directions check)." if axes else " (assumed: the directions check had no answer)."),
        sign_note,
        (
            "Wood–Armer moments from Mx, My and the twisting moment Mxy; "
            if slab.twisting == "wood_armer"
            else "Mx and My as they are, without the twisting moment Mxy (slab setting); "
        )
        + "bars along X take Mx with Nx only, bars along Y My with Ny only, so no action is counted twice.",
    ]
    if crane_rows:
        notes.append(
            f"Mobile crane additions from the SAP model added to every ULS combination at {crane_rows} "
            f"results over {len(slab.crane)} area(s)."
        )
    base = {"element": name, "kind": "slab", "thickness_mm": h, "concrete": slab.concrete, "notes": notes}
    if uls.empty:
        notes.append("No ULS results.")
        return {**base, "utilisation": None, "passed": False}

    # Piles under the slab: results inside them are left out.
    box = {a: [float(uls[a].min()), float(uls[a].max())] for a in ("X", "Y")}
    level = float(uls["Z"].median())
    piles = []
    rows_of: dict[str, list[tuple[float, float]]] = {}
    for g in geometry:
        el = elements.get(g["element"])
        if g.get("kind") != "beam" or not isinstance(el, PileInput):
            continue
        for x, y, ztop, _ in g.get("lines") or []:
            if (
                box["X"][0] - 1e-6 <= x <= box["X"][1] + 1e-6
                and box["Y"][0] - 1e-6 <= y <= box["Y"][1] + 1e-6
            ):
                if ztop >= level - 1.5:
                    piles.append((x, y, el.diameter / 2000))
                    rows_of.setdefault(g["element"], []).append((round(x, 2), round(y, 2)))
    notes_missing = missing_piles(rows_of)
    beams = [
        g
        for g in geometry
        if g.get("type", "").endswith("beam") and g.get("box") and g["element"] in elements
    ]

    def outside(f: pd.DataFrame, extra: float) -> np.ndarray:
        keep = np.ones(len(f), bool)
        for x, y, r in piles:
            keep &= np.hypot(f["X"].to_numpy() - x, f["Y"].to_numpy() - y) >= r + extra - 1e-6
        return keep

    uls_m = uls[outside(uls, 0.0)].reset_index(drop=True)
    qp_m = qp[outside(qp, 0.0)].reset_index(drop=True) if len(qp) else qp
    if piles:
        notes.append(f"{len(piles)} pile heads inside the slab: results inside them are left out.")
    notes.extend(notes_missing)
    if uls_m.empty:
        notes.append("No ULS results outside the pile heads.")
        return {**base, "utilisation": None, "passed": False}
    if piles:
        uls_m = treat_pile_faces(uls_m, piles, slab.peaks, h / 1000)
        qp_m = treat_pile_faces(qp_m, piles, slab.peaks, h / 1000)
        notes.append(PEAK_METHODS[slab.peaks] + " (slab setting)")

    covers = {"bottom": slab.cover_bottom, "top": slab.cover_top}
    limits = {"bottom": slab.crack_width_limit_bottom, "top": slab.crack_width_limit}
    x0, y0 = box["X"][0], box["Y"][0]
    # Circular voids (PVC pipes): where they lie, and the voided section of each direction of bars.
    vlay = vd.layout(slab.voids, h, box, piles, beams) if slab.voids is not None else None
    vsec: dict[str, vd.VoidSection] = {}
    if vlay is not None and vlay["positions"]:
        if vlay["flange_top_mm"] <= 0 or vlay["flange_bottom_mm"] <= 0:
            notes.append("Voids: the voids do not fit inside the slab's thickness at that depth; left out.")
            vlay = None
        else:
            for direction in ("x", "y"):
                kind = "circles" if direction == vlay["along"].lower() else "through"
                vsec[direction] = vd.VoidSection(
                    h, slab.voids.diameter, vlay["centre_depth_mm"], slab.voids.spacing, kind
                )
    vm_u = vd.mask(vlay, uls_m["X"], uls_m["Y"]) if vsec else np.zeros(len(uls_m), bool)
    vm_q = vd.mask(vlay, qp_m["X"], qp_m["Y"]) if vsec and len(qp_m) else np.zeros(len(qp_m), bool)
    ui, uj = _cells(uls_m["X"].to_numpy(), uls_m["Y"].to_numpy(), x0, y0, size)
    uls_m = uls_m.assign(i=ui, j=uj)
    # Squares of the grid with no result of their own: over a pile head (the results inside the pile are
    # left out) or where the Plaxis mesh has no node (its elements are larger than a cell there). The
    # zones run on through them and the plots show the worst of the squares round them.
    ni = int(math.floor((box["X"][1] - x0) / size + 1e-9)) + 1
    nj = int(math.floor((box["Y"][1] - y0) / size + 1e-9)) + 1
    gaps = gap_cells(set(zip(ui.tolist(), uj.tolist(), strict=True)), ni, nj, x0, y0, size, box, piles)
    if len(qp_m):
        qi, qj = _cells(qp_m["X"].to_numpy(), qp_m["Y"].to_numpy(), x0, y0, size)
        qp_m = qp_m.assign(i=qi, j=qj)
    twist = 1.0 if slab.twisting == "wood_armer" else 0.0
    wa = wood_armer(uls_m["Mx"].to_numpy(), uls_m["My"].to_numpy(), twist * uls_m["Mxy"].to_numpy())
    wq = (
        wood_armer(qp_m["Mx"].to_numpy(), qp_m["My"].to_numpy(), twist * qp_m["Mxy"].to_numpy())
        if len(qp_m)
        else None
    )
    frame = (
        strip_frame(slab, box, piles, beams, choices.stations, choices.lines)
        if slab.strips == "column_and_field"
        else None
    )
    strips = frame is not None
    if slab.strips == "column_and_field" and not strips:
        notes.append("No piles under the slab to set out the column strips: designed as a uniform slab.")
    strip_rows: list[dict] = []
    if strips:
        ax, cx = frame["along"], frame["across"]
        uloc = locate(frame, uls_m[ax].to_numpy(), uls_m[cx].to_numpy())
        qloc = locate(frame, qp_m[ax].to_numpy(), qp_m[cx].to_numpy()) if len(qp_m) else None

    fcd_s = settings.partial_factors.alpha_cc * conc.fck / settings.partial_factors.gamma_c
    R = (
        slab.restraint_factor
        if slab.restraint_factor is not None
        else restraint_factor(slab.joint_spacing, h / 1000)
    )
    # Restraint from the joints works along the quay: on the bars across the strips.
    rest_dir = "y" if slab.strip_direction == "X" else "x"
    design_rest = slab.restraint_check == "design"
    layers: dict[str, dict] = {}
    per_cell: dict[str, pd.DataFrame] = {}
    overall_rows: list[dict] = []
    worst_k, worst_k_at = 0.0, None
    phi_est = 20

    def depth(face: str, direction: str) -> float:
        return h - covers[face] - phi_est / 2 - (phi_est if direction == "y" else 0)

    def opt_depth(o: tuple, f: float, face: str, direction: str) -> float:
        """Effective depth of an option: bigger bars and more layers sit deeper in."""
        if len(o) > 4:  # the user's layers: their centroid
            return h - o[4] - (f if direction == "y" else 0)
        return h - covers[face] - f / 2 - (f if direction == "y" else 0) - (o[3] - 1) * (f + 25) / 2

    def req_as(m, n, d, face, direction, d2, voided):
        """``required_as``, on the voided section where ``voided``."""
        out = required_as(m, n, h, d, conc.fck, fyd, d2)
        voided = np.asarray(voided, bool)
        if direction not in vsec or not voided.any():
            return out
        v_out = vd.required_as(m, n, h, d, conc.fck, fyd, d2, vsec[direction], face)
        return tuple(np.where(voided, b_, a_) for a_, b_ in zip(out, v_out, strict=True))

    def cw(m, n, o, d_o, face, direction, voided, terms=False):
        """``crack_widths`` of option ``o``, on the voided section where ``voided``."""
        voided = np.asarray(voided, bool)
        if direction in vsec and voided.any():
            vw = vd.crack_widths(
                m,
                n,
                o[0],
                o[1],
                o[2],
                h,
                d_o,
                covers[face],
                conc,
                e_eff,
                vsec[direction],
                face,
                K1,
                K3,
                K4,
                KT,
                terms=terms,
            )
            if terms:  # one set at a time
                return vw
            if voided.all():
                return vw
            return np.where(
                voided, vw, crack_widths(m, n, o[0], o[1], o[2], h, d_o, covers[face], conc, e_eff)
            )
        return crack_widths(m, n, o[0], o[1], o[2], h, d_o, covers[face], conc, e_eff, terms=terms)

    def mrd_of(area, d_o, n_, face, direction, voided):
        if voided and direction in vsec:
            return vd.mrd(area, d_o, h, n_, fcd_s, fyd, vsec[direction], face)
        return strip_mrd(area, d_o, h, n_, fcd_s, fyd)

    mesh_labels = {label(o): o for o in options}
    # Steel per node for each layer; where K > K' the opposite face's bars work in compression.
    node_req = {layer: np.zeros(len(uls_m)) for layer in LAYERS}
    compression = {layer: np.zeros(len(uls_m)) for layer in LAYERS}
    for layer in LAYERS:
        face, direction = layer.split("_")
        other = "top" if face == "bottom" else "bottom"
        n = uls_m["Nx" if direction == "x" else "Ny"].to_numpy()
        d2 = h - depth(other, direction)
        a_req, k, a_s2 = req_as(wa[layer], n, depth(face, direction), face, direction, d2, vm_u)
        node_req[layer] = a_req
        compression[f"{other}_{direction}"] = a_s2
        if len(k) and float(k.max()) > worst_k:
            w = int(np.argmax(k))
            worst_k = float(k[w])
            worst_k_at = (layer, float(uls_m["X"].iloc[w]), float(uls_m["Y"].iloc[w]))
    for layer in LAYERS:
        face, direction = layer.split("_")
        # Column and field strips for the bars along the strips; the bars across them (M22) are one
        # design over the whole deck, with zones only where it needs more.
        lstrips = strips and direction == frame["along"].lower()
        d = depth(face, direction)
        n_u = uls_m["Nx" if direction == "x" else "Ny"].to_numpy()
        a_req = np.maximum(node_req[layer], compression[layer])
        a_min = max(0.26 * conc.fctm / fyk, 0.0013) * 1000 * d
        need = pd.DataFrame({"i": uls_m["i"], "j": uls_m["j"], "req": np.maximum(a_req, a_min)})
        gov_node = need.groupby(["i", "j"])["req"].idxmax()
        cell = need.groupby(["i", "j"])["req"].max().reset_index()
        cell_node = gov_node.to_numpy()  # the node setting each cell's need, in the order of ``cell``
        node_cell = pd.MultiIndex.from_arrays([cell["i"], cell["j"]]).get_indexer(
            pd.MultiIndex.from_arrays([uls_m["i"], uls_m["j"]])
        )
        nq = pos = None
        if wq is not None:
            nq = qp_m["Nx" if direction == "x" else "Ny"].to_numpy()
            key = pd.MultiIndex.from_arrays([qp_m["i"], qp_m["j"]])
            pos = pd.MultiIndex.from_arrays([cell["i"], cell["j"]]).get_indexer(key)
        groups: dict = {}
        members: dict = {}
        qgroups: dict = {}
        sets: dict = {}  # per strip and station: the governing cut of every combination, for AdSec
        if lstrips:
            # Every column strip together and every field strip together, station by station: the need
            # is the worst cut across a strip's width, averaged over that width.
            other = "top" if face == "bottom" else "bottom"
            env = strip_average(uls_m, wa[layer], n_u, uloc, size, vm_u)
            a_env, _, _ = req_as(
                env["m"].to_numpy(),
                env["n"].to_numpy(),
                d,
                face,
                direction,
                h - depth(other, direction),
                env["v"].to_numpy() > 0.5,
            )
            env["req"] = np.maximum(a_env, a_min)
            gov = env.loc[env.groupby(["st", "kind"])["req"].idxmax()]
            groups = {(int(r.st), int(r.kind)): r for r in gov.itertuples(index=False)}
            for k, g in env.groupby(["st", "kind"]):
                gi = int(np.argmax(g["req"].to_numpy()))
                sets.setdefault((int(k[0]), int(k[1])), {"uls": [], "qp": []})["uls"] = extreme_sets(
                    g["combination"].to_numpy(), g["m"].to_numpy(), g["n"].to_numpy(), gi
                )
            cxs = x0 + (cell["i"].to_numpy() + 0.5) * size
            cys = y0 + (cell["j"].to_numpy() + 0.5) * size
            cl = locate(frame, cxs if ax == "X" else cys, cys if ax == "X" else cxs)
            keys = list(zip(cl["st"].tolist(), cl["kind"].tolist(), strict=True))
            for c, k in enumerate(keys):
                members.setdefault(k, []).append(c)
            cell["req"] = [
                groups[k].req if k in groups else r for k, r in zip(keys, cell["req"], strict=True)
            ]
            if wq is not None and qloc is not None:
                qenv = strip_average(qp_m, wq[layer], nq, qloc, size, vm_q)
                for k, g in qenv.groupby(["st", "kind"]):
                    qgroups[(int(k[0]), int(k[1]))] = (
                        g["m"].to_numpy(),
                        g["n"].to_numpy(),
                        g["combination"].to_numpy(),
                        g["v"].to_numpy() > 0.5,
                    )

        def assess(
            opts: list[tuple],
            dphi: list[float],
            face=face,
            direction=direction,
            d=d,
            cell=cell,
            layer=layer,
            nq=nq,
            pos=pos,
            qgroups=qgroups,
            members=members,
            lstrips=lstrips,
        ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
            """Area worth at depth d, and ok[cell, option] for strength alone and for all checks.

            Bigger bars and a second layer sit deeper in, so they count for less. Options are
            (mm²/m, Ø for cracks, spacing for cracks, layers); ``dphi`` is the Ø setting the depth.
            """
            ar = np.array([o[0] for o in opts])
            d_opt = np.array([opt_depth(o, f, face, direction) for o, f in zip(opts, dphi, strict=True)])
            eff = ar * np.minimum(d_opt / d, 1.0)
            strength = eff[None, :] >= cell["req"].to_numpy()[:, None] - 1e-6
            ok = strength.copy()
            if wq is not None:
                crack_ok = np.ones_like(ok)
                for oi, o in enumerate(opts):
                    if lstrips:
                        for k, (qm, qn, _, qv) in qgroups.items():
                            if k not in members:
                                continue
                            w = cw(qm, qn, o, d_opt[oi], face, direction, qv)
                            if w.max() > limits[face] + 1e-9:
                                crack_ok[members[k], oi] = False
                        continue
                    w = cw(wq[layer], nq, o, d_opt[oi], face, direction, vm_q)
                    crack_ok[pos[(w > limits[face] + 1e-9) & (pos >= 0)], oi] = False
                ok &= crack_ok
            if design_rest and direction == rest_dir:
                rest = np.array(
                    [
                        slab_restraint(o, face, direction, covers, h, settings, conc, R)["wk"]
                        <= limits[face] + 1e-9
                        for o in opts
                    ]
                )
            else:
                rest = np.ones(len(opts), bool)
            return eff, strength, ok & rest[None, :], rest

        eff, strength_ok, ok, mesh_rest = assess(options, [o[1] for o in options])
        first = lambda m: np.where(m.any(axis=1), m.argmax(axis=1), m.shape[1] - 1)  # noqa: E731
        cell["idx"] = first(ok)
        cell["uidx"] = first(strength_ok)
        req_eff = cell["req"].to_numpy()
        forced = options.index(meshes[layer]) if layer in meshes else None
        user_mesh = choices.bars.get(f"{layer}|mesh")
        if user_mesh is not None:
            if user_mesh in mesh_labels:
                forced = options.index(mesh_labels[user_mesh])
            else:
                notes.append(
                    f"Mesh set for {LAYER_TEXT[layer]} ({user_mesh}) is not among the options: left out."
                )
        along = "X" if direction == "x" else "Y"
        mode = getattr(slab, f"layout_{layer}")
        if mode == "mesh_only":
            # One mesh strong enough everywhere.
            fits = np.flatnonzero(ok.all(axis=0))
            b = forced if forced is not None else (int(fits[0]) if len(fits) else len(options) - 1)
            z = zones_for(cell, ok, options, size, x0, y0, along, size, b)
            z["zones"], z["cell_index"] = [], np.full(len(cell), b)
            opts, eff_all, labels = options, eff, [label(o) for o in options]
            dphis = [o[1] for o in options]
            specs = [[] for _ in options]
            ok_all = ok
        else:
            # A mesh everywhere, and additional bars between its bars where it is not enough. The mesh
            # is the one with the least steel overall, its additional bars included.
            target = req_eff
            # A basic mesh is one layer that gives the minimum steel, with room for additional bars.
            cands = (
                [forced]
                if forced is not None
                else [
                    k
                    for k, o in enumerate(options)
                    if o[3] == 1
                    and eff[k] >= a_min - 1e-6
                    and fits_between(10, o[1], o[2], settings)
                    and mesh_rest[k]
                ]
                or [int(np.argmax(mesh_rest))]
            )
            tried = []
            for k in cands:
                combos, dphi, labels, specs = additional_options(options[k], settings, h / 2 - covers[face])
                eff2, _, ok2, _ = assess(combos, dphi)
                ok2 = (eff2[None, :] >= target[:, None] - 1e-6) & ok2
                tried.append((k, combos, labels, eff2, ok2, dphi, specs))
            # Cells no mesh fits with its additional bars (a thicker slab or a haunch there) do not choose
            # the mesh; they are reported. Cells this mesh cannot take but another can are priced at
            # twice the heaviest bars.
            somewhere = np.logical_or.reduce([t[4].any(axis=1) for t in tried])
            costed = []
            for k, combos, labels, eff2, ok2, dphi, specs in tried:
                idx = first(ok2)
                if not lstrips:  # bars across the strips: zones as they will be laid out
                    idx = area_zones(cell, ok2, combos, labels, size, x0, y0, box, slab.min_zone_length)[
                        "cell_index"
                    ]
                ar = np.array([o[0] * (1 + LAYER_PREMIUM * (o[3] - 1)) for o in combos])
                short = np.where(ok2.any(axis=1), 1.0, 2.0)
                cost = float((ar[idx] * np.where(idx > 0, 1 + PREMIUM, 1.0) * short)[somewhere].sum())
                costed.append((cost, k, combos, labels, eff2, ok2, dphi, specs))
            # The lightest mesh within MESH_SLACK of the least steel: a light mesh with additional bars
            # where needed, as the office's slabs, rather than a heavier mesh for a percent of steel.
            least = min(c[0] for c in costed)
            best = min(
                (c for c in costed if c[0] <= least * (1 + MESH_SLACK) + 1e-6),
                key=lambda c: (options[c[1]][0], c[0]),
            )
            _, b, opts, labels, eff_all, ok_all, dphis, specs = best
            opts, labels, dphis, specs = list(opts), list(labels), list(dphis), list(specs)
            if lstrips:
                z = zones_for(
                    cell.assign(idx=first(ok_all)),
                    ok_all,
                    opts,
                    size,
                    x0,
                    y0,
                    along,
                    slab.min_zone_length,
                    0,
                    labels,
                    gaps,
                )
            else:
                z = area_zones(cell, ok_all, opts, labels, size, x0, y0, box, slab.min_zone_length)
            for zz in z["zones"]:
                zz["additional_mm2_per_m"] = round(zz["as_mm2_per_m"] - options[b][0])
        n_std = len(opts)
        # The user's own bar layers on this mesh.
        if mode != "mesh_only":
            mesh_o = options[b]
            for text in sorted({v for kk, v in choices.bars.items() if kk.startswith(f"{layer}|")}):
                spec = parse_layers(text)
                if spec is None or text in labels:
                    continue
                bad = [
                    p
                    for k_, p in enumerate(spec)
                    if p is not None
                    and (
                        not fits_between(p[0], mesh_o[1], mesh_o[2], settings)
                        if k_ == 0
                        else p[1] - p[0] < max(settings.reinforcement.slab_min_clear_spacing, p[0]) - 1e-9
                    )
                ]
                least = settings.reinforcement.slab_min_bar_spacing
                close = [p for p in spec if p is not None and least is not None and p[1] < least - 1e-9]
                if close and not bad:
                    notes.append(
                        f"{LAYER_TEXT[layer].capitalize()}: {layers_text(spec)} has bars closer than the "
                        f"least spacing of additional bars ({least:g} mm, Design settings): left out."
                    )
                    continue
                if bad:
                    notes.append(
                        f"{LAYER_TEXT[layer].capitalize()}: {layers_text(spec)} leaves less than the clear "
                        "spacing between bars: left out."
                    )
                    continue
                o, big = spec_option(mesh_o, spec, covers[face], 0.0)
                opts.append(o)
                labels.append(text)
                dphis.append(big)
                specs.append(spec)
            if len(opts) > n_std:
                e2, _, k2, _ = assess(opts[n_std:], dphis[n_std:])
                k2 = (e2[None, :] >= req_eff[:, None] - 1e-6) & k2
                eff_all = np.concatenate([eff_all, e2])
                ok_all = np.hstack([ok_all, k2])
        z["basic"]["set_by"] = "user" if forced is not None else "least steel"
        z["mode"] = mode
        chosen = np.array(z.pop("cell_index"))  # the bars each cell gets: the mesh, or with additional bars
        # Bars the user set for a station and strip (or a zone): every cell of it gets them.
        user_keys: set = set()
        user_cells = np.zeros(len(cell), bool)

        def pick(want: str, where: str) -> int | None:
            if mode == "mesh_only" or want not in labels and want != "mesh only":
                notes.append(f"Bars set for {where} ({want}) are not among the options: left out.")
                return None
            return 0 if want == "mesh only" else labels.index(want)

        if lstrips:
            for k, idxs in members.items():
                key = strip_key(layer, frame["bounds"], k)
                want = choices.bars.get(key)
                if want is None or (oi := pick(want, key.replace("|", " "))) is None:
                    continue
                chosen[idxs] = oi
                user_keys.add(k)
                user_cells[idxs] = True
        else:
            ci, cj = cell["i"].to_numpy(), cell["j"].to_numpy()
            ccx, ccy = x0 + (ci + 0.5) * size, y0 + (cj + 0.5) * size
            for key, want in choices.bars.items():
                parts = key.split("|")
                if parts[0] != layer or len(parts) != 6 or parts[1] != "zone":
                    continue
                zx0, zx1, zy0, zy1 = (float(v) for v in parts[2:])
                inside = (ccx >= zx0) & (ccx <= zx1) & (ccy >= zy0) & (ccy <= zy1)
                if not inside.any() or (oi := pick(want, f"zone {key}")) is None:
                    continue
                chosen[inside] = oi
                user_keys.add(key)
                user_cells |= inside
        prov = np.array([o[0] for o in opts])[chosen]
        ratio = req_eff / eff_all[chosen]
        util = ratio.max()
        if util > 1 + 1e-6:
            w = int(np.argmax(ratio))
            wx, wy = x0 + (cell["i"].iloc[w] + 0.5) * size, y0 + (cell["j"].iloc[w] + 0.5) * size
            notes.append(
                f"{LAYER_TEXT[layer].capitalize()}: the heaviest bars ({labels[n_std - 1]}) are not enough "
                f"at X {wx:.1f}, Y {wy:.1f} ({util:.2f}): a thicker slab or a haunch is needed there."
                if not user_cells[w]
                else f"{LAYER_TEXT[layer].capitalize()}: the bars you set are not enough at X {wx:.1f}, "
                f"Y {wy:.1f} ({util:.2f})."
            )
        d_all = [opt_depth(o, f, face, direction) for o, f in zip(opts, dphis, strict=True)]
        # wk / limit per cell with the bars it gets, for the 3D view's "Crack width" mode.
        crack_u = np.full(len(cell), np.nan)
        if wq is not None:
            for oi in np.unique(chosen):
                o, d_o = opts[int(oi)], d_all[int(oi)]
                if lstrips:
                    for k, (qm, qn, _, qv) in qgroups.items():
                        idxs = [c for c in members.get(k, []) if chosen[c] == oi]
                        if idxs:
                            w = cw(qm, qn, o, d_o, face, direction, qv)
                            crack_u[idxs] = np.fmax(crack_u[idxs], float(w.max()) / limits[face])
                    continue
                rows = (pos >= 0) & (chosen[np.maximum(pos, 0)] == oi)
                if rows.any():
                    w = cw(wq[layer][rows], nq[rows], o, d_o, face, direction, vm_q[rows])
                    at = pos[rows]
                    vals = np.zeros(len(cell))
                    np.maximum.at(vals, at, w / limits[face])
                    hit = np.zeros(len(cell), bool)
                    hit[at] = True
                    crack_u[hit] = np.fmax(crack_u[hit], vals[hit])
        # What sets the bars of a group of cells: the lightest bars for strength alone and with the
        # crack widths, against the bars given.
        s_first = first(eff_all[None, :n_std] >= req_eff[:, None] - 1e-6)
        a_first = first(ok_all[:, :n_std])
        min_only = req_eff <= a_min + 1e-6

        def set_by(idxs, oi: int) -> str:
            idxs = np.asarray(idxs, int)
            if user_cells[idxs].any():
                return "your bars"
            s_i, a_i = int(s_first[idxs].max()), int(a_first[idxs].max())
            if oi == 0:
                if a_i == 0 and s_i == 0:
                    return "basic mesh (spare)" if not min_only[idxs].all() else "basic mesh (minimum steel)"
                return "basic mesh"
            if a_i > s_i:
                return "crack width (QP)"
            if oi > a_i:
                return "zone (the heaviest cell in it)"
            if min_only[idxs].all():
                return "minimum steel"
            return "bending (ULS)"

        name_m = _map(axes)["Mx" if direction == "x" else "My"].replace("_", "")

        def layer_rows(oi: int) -> list[dict]:
            """The layers of bars of an option, outermost first, with their distance from the face."""
            mesh_o = opts[oi] if mode == "mesh_only" else options[b]
            spec = [] if mode == "mesh_only" else specs[oi]
            shift = dphis[oi] if direction == "y" else 0
            return [
                {k: v for k, v in r.items() if k != "_area"}
                for r in bar_layers(mesh_o, spec, covers[face], shift)
            ]

        if lstrips:
            eff_c = eff_all[chosen]
            for k, g in sorted(groups.items()):
                idxs = members.get(k)
                if not idxs:
                    continue
                c = idxs[int(np.argmin(eff_c[idxs]))]
                oi = int(chosen[c])
                o = opts[oi]
                d_o = d_all[oi]
                g_void = bool(g.v > 0.5)
                mrd = mrd_of(o[0], d_o, float(g.n), face, direction, g_void)
                wk = qcomb = qcase = None
                strip_sets = sets.setdefault(k, {"uls": [], "qp": []})
                q_void = np.zeros(0, bool)
                if k in qgroups:
                    qm, qn, qc, q_void = qgroups[k]
                    w = cw(qm, qn, o, d_o, face, direction, q_void)
                    wi = int(np.argmax(w))
                    wk, qcomb = round(float(w[wi]), 3), str(qc[wi])
                    qcase = {"M_kNm_per_m": round(float(qm[wi]), 1), "N_kN_per_m": round(float(qn[wi]), 1)}
                    strip_sets["qp"] = extreme_sets(qc, qm, qn, wi)
                for q in strip_sets["qp"]:
                    t = cw(
                        np.array([q["M_kNm_per_m"]]),
                        np.array([q["N_kN_per_m"]]),
                        o,
                        d_o,
                        face,
                        direction,
                        [bool(q_void.any())],
                        terms=True,
                    )
                    q["crack"] = {
                        **crack_terms(t[0][0], limits[face], t[1][0], t[2], t[3], h),
                        "face": face,
                        "d_mm": round(d_o),
                        "phi_mm": o[1],
                        "spacing_mm": o[2],
                    }
                add_text = labels[oi] if oi < n_std else layers_text(specs[oi])
                bars = add_text if mode == "mesh_only" or oi == 0 else f"{label(options[b])} + {add_text}"
                mesh_o = opts[oi] if mode == "mesh_only" else options[b]
                strip_rows.append(
                    {
                        "key": strip_key(layer, frame["bounds"], k),
                        "user_set": k in user_keys,
                        "additional": "mesh only" if oi == 0 else labels[oi],
                        "moment": name_m,
                        "layer": layer,
                        "face": face,
                        "station": [frame["bounds"][k[0]], frame["bounds"][k[0] + 1]],
                        "strip": "column" if k[1] == 0 else "field",
                        "M_kNm_per_m": round(abs(float(g.m)), 1),
                        "N_kN_per_m": round(float(g.n), 1),
                        "combination": str(g.combination),
                        "as_req_mm2_per_m": round(float(g.req)),
                        "bars": bars,
                        "as_mm2_per_m": round(o[0]),
                        "MRd_kNm_per_m": round(mrd, 1),
                        "ratio": round(abs(float(g.m)) / mrd, 3) if mrd > 0 else None,
                        "wk_mm": wk,
                        "wk_limit_mm": limits[face],
                        "qp_combination": qcomb,
                        "qp": qcase,
                        "mesh": {"phi": mesh_o[1], "spacing_mm": mesh_o[2], "layers": mesh_o[3]},
                        "additional_bars": None if mode == "mesh_only" or oi == 0 else add_text,
                        "set_by": set_by(idxs, oi),
                        "bar_layers": layer_rows(oi),
                        "spec": [list(p) if p else None for p in specs[oi]],
                        "sets": strip_sets,
                        "voided": g_void,
                    }
                )
        else:
            # One row for the whole deck on the mesh, one for each zone of additional bars.
            ci, cj = cell["i"].to_numpy(), cell["j"].to_numpy()
            ccx, ccy = x0 + (ci + 0.5) * size, y0 + (cj + 0.5) * size
            base_oi = 0 if mode != "mesh_only" else int(b)
            groups_o: list[tuple[str, str, np.ndarray, list | None]] = [
                ("Whole deck, basic mesh", f"{layer}|mesh", np.flatnonzero(chosen == base_oi), None)
            ]
            for zz in z["zones"]:
                inside = np.flatnonzero(
                    (ccx >= zz["x"][0]) & (ccx <= zz["x"][1]) & (ccy >= zz["y"][0]) & (ccy <= zz["y"][1])
                )
                groups_o.append(("Zone", zone_key(layer, zz), inside, [zz["x"], zz["y"]]))
            for key in choices.bars:
                if key in user_keys and not any(g_[1] == key for g_ in groups_o):
                    parts = key.split("|")
                    xr, yr = [float(parts[2]), float(parts[3])], [float(parts[4]), float(parts[5])]
                    inside = np.flatnonzero((ccx >= xr[0]) & (ccx <= xr[1]) & (ccy >= yr[0]) & (ccy <= yr[1]))
                    groups_o.append(("Zone", key, inside, [xr, yr]))
            for text, key, idxs, rect in groups_o:
                if not len(idxs):
                    continue
                oi = int(np.bincount(chosen[idxs]).argmax()) if rect is not None else base_oi
                o, d_o = opts[oi], d_all[oi]
                w = int(idxs[np.argmax(ratio[idxs])])
                node = int(cell_node[w])
                m_ = float(wa[layer][node])
                n_ = float(n_u[node])
                g_void = bool(vm_u[node])
                mrd = mrd_of(o[0], d_o, n_, face, direction, g_void)
                wk = None
                cu = crack_u[idxs]
                if np.isfinite(cu).any():
                    wk = round(float(np.nanmax(cu)) * limits[face], 3)
                if rect is not None:
                    if frame is not None and frame["along"] == "X":
                        s_a = sorted(round((v - frame["origin"]) * frame["sign"], 2) for v in rect[0])
                        text = f"Zone at station {s_a[0]:g} to {s_a[1]:g}, Y {rect[1][0]:g} to {rect[1][1]:g}"
                    else:
                        text = (
                            f"Zone at X {rect[0][0]:g} to {rect[0][1]:g}, Y {rect[1][0]:g} to {rect[1][1]:g}"
                        )
                add_text = labels[oi] if oi < n_std else layers_text(specs[oi])
                bars = add_text if mode == "mesh_only" or oi == 0 else f"{label(options[b])} + {add_text}"
                # The governing result of every combination in these cells, for AdSec: ULS by the steel
                # it needs, QP by its moment.
                in_u = np.isin(node_cell, idxs)
                g_sets: dict[str, list] = {"uls": [], "qp": []}
                uu = pd.DataFrame(
                    {
                        "c": uls_m["combination"].to_numpy()[in_u],
                        "r": need["req"].to_numpy()[in_u],
                        "m": wa[layer][in_u],
                        "n": n_u[in_u],
                    }
                )
                if len(uu):
                    g_sets["uls"] = extreme_sets(
                        uu["c"].to_numpy(), uu["m"].to_numpy(), uu["n"].to_numpy(), int(np.argmax(uu["r"]))
                    )
                qcase = qcomb = None
                if wq is not None:
                    in_q = (pos >= 0) & np.isin(pos, idxs)
                    qq = pd.DataFrame(
                        {
                            "c": qp_m["combination"].to_numpy()[in_q],
                            "m": wq[layer][in_q],
                            "n": nq[in_q],
                            "v": vm_q[in_q],
                        }
                    )
                    if len(qq):
                        qm, qn, qc = qq["m"].to_numpy(), qq["n"].to_numpy(), qq["c"].to_numpy()
                        qv = qq["v"].to_numpy(bool)
                        w_all = cw(qm, qn, o, d_o, face, direction, qv)
                        wi = int(np.argmax(w_all))
                        qcomb = str(qc[wi])
                        qcase = {
                            "M_kNm_per_m": round(float(qm[wi]), 1),
                            "N_kN_per_m": round(float(qn[wi]), 1),
                        }
                        g_sets["qp"] = extreme_sets(qc, qm, qn, wi)
                        for q in g_sets["qp"]:
                            t = cw(
                                np.array([q["M_kNm_per_m"]]),
                                np.array([q["N_kN_per_m"]]),
                                o,
                                d_o,
                                face,
                                direction,
                                [bool(qv[wi])],
                                terms=True,
                            )
                            q["crack"] = {
                                **crack_terms(t[0][0], limits[face], t[1][0], t[2], t[3], h),
                                "face": face,
                                "d_mm": round(d_o),
                                "phi_mm": o[1],
                                "spacing_mm": o[2],
                            }
                mesh_o = opts[oi] if mode == "mesh_only" else options[b]
                overall_rows.append(
                    {
                        "key": key,
                        "moment": name_m,
                        "layer": layer,
                        "face": face,
                        "label": text,
                        "zone": rect,
                        "station": [frame["start"], frame["end"]] if frame is not None else [0, 0],
                        "strip": "all",
                        "user_set": key in user_keys or (rect is None and user_mesh is not None),
                        "additional": "mesh only" if oi == 0 else labels[oi],
                        "M_kNm_per_m": round(abs(m_), 1),
                        "N_kN_per_m": round(n_, 1),
                        "combination": str(uls_m["combination"].iloc[node]),
                        "as_req_mm2_per_m": round(float(req_eff[w])),
                        "bars": bars,
                        "as_mm2_per_m": round(o[0]),
                        "MRd_kNm_per_m": round(mrd, 1),
                        "ratio": round(abs(m_) / mrd, 3) if mrd > 0 else None,
                        "wk_mm": wk,
                        "wk_limit_mm": limits[face],
                        "qp_combination": qcomb,
                        "qp": qcase,
                        "mesh": {"phi": mesh_o[1], "spacing_mm": mesh_o[2], "layers": mesh_o[3]},
                        "additional_bars": None if mode == "mesh_only" or oi == 0 else add_text,
                        "set_by": "your mesh" if rect is None and forced is not None else set_by(idxs, oi),
                        "bar_layers": layer_rows(oi),
                        "spec": [list(p) if p else None for p in specs[oi]],
                        "sets": g_sets,
                        "voided": g_void,
                    }
                )
        crack_gov = int((a_first > s_first).sum())
        z.pop("basic_index")
        mesh_o = options[b] if mode != "mesh_only" else opts[int(chosen[0])]
        if mode != "mesh_only":
            # Each zone's bars layer by layer (for drawing tools): the mesh and the added bars.
            shift = mesh_o[1] if direction == "y" else 0
            for zz in z["zones"]:
                if zz.get("label") in labels:
                    zz["bar_layers"] = [
                        {k: v for k, v in r.items() if k != "_area"}
                        for r in bar_layers(mesh_o, specs[labels.index(zz["label"])], covers[face], shift)
                    ]
        layers[layer] = {
            **z,
            "additional_labels": ["mesh only", *labels[1:n_std]] if mode != "mesh_only" else [],
            "mesh_labels": list(mesh_labels),
            "d_mm": round(d),
            "as_min_mm2_per_m": round(a_min),
            "utilisation": round(float(util), 3),
            "cells_set_by_cracks": crack_gov,
            "strips": bool(lstrips),
            "cover_mm": covers[face],
            "mesh_bar_layers": [
                {k: v for k, v in r.items() if k != "_area"}
                for r in bar_layers(mesh_o, [], covers[face], mesh_o[1] if direction == "y" else 0)
            ],
        }
        per_cell[layer] = pd.DataFrame(
            {
                "i": cell["i"],
                "j": cell["j"],
                "a": prov,
                "used": ratio,  # bending steel needed over provided; cracking can need more
                "crack": crack_u,
            }
        )
    if worst_k > K_BAL:
        notes.append(
            f"K up to {worst_k:.2f} > {K_BAL} ({LAYER_TEXT[worst_k_at[0]]} at X {worst_k_at[1]:.1f}, "
            f"Y {worst_k_at[2]:.1f}): the opposite face's bars are designed as compression steel there."
        )

    # Provided steel at a point, for shear and punching.
    prov_at = {
        layer: {(int(i), int(j)): float(a) for i, j, a in zip(pc["i"], pc["j"], pc["a"], strict=True)}
        for layer, pc in per_cell.items()
    }

    # Bars added over pile heads for punching: (x, y, face) -> mm²/m added along x and along y.
    punch_extra: dict[tuple[float, float, str], dict[str, float]] = {}

    def as_at(x: float, y: float, face: str, direction: str) -> float:
        i, j = (int(v[0]) for v in _cells(np.array([x]), np.array([y]), x0, y0, size))
        lay = layers[f"{face}_{direction}"]
        return prov_at[f"{face}_{direction}"].get((i, j), lay["basic"]["as_mm2_per_m"])

    def rho_at(x: float, y: float, face: str) -> float:
        extra = punch_extra.get((round(x, 2), round(y, 2), face), {})
        vals = []
        for direction in ("x", "y"):
            a = as_at(x, y, face, direction) + extra.get(direction, 0.0)
            vals.append(a / (1000 * layers[f"{face}_{direction}"]["d_mm"]))
        return math.sqrt(vals[0] * vals[1])

    # Shear per metre.
    pf = settings.partial_factors
    dv = (h - max(covers.values()) - 20) / 1000 * (2 if settings.shear_check_distance == "2d" else 1)
    # Within 2d of a pile face the punching check (6.4) governs, so one-way shear starts there at the least.
    sh = uls[outside(uls, max(dv, 2 * (h - max(covers.values()) - 20) / 1000))].reset_index(drop=True)
    shear = {"cells_needing_links": 0, "utilisation": 0.0, "passed": True, "links": []}
    if len(sh):
        d_s = h - max(covers.values()) - 20
        vx, vy = sh["Vx"].to_numpy(), sh["Vy"].to_numpy()
        v = np.hypot(vx, vy)
        # Each shear with the axial force in its own direction (Q13 with N1, Q23 with N2): the axial
        # force per metre in the direction the shear flows.
        c2 = np.where(v > 0, (vx / np.where(v > 0, v, 1.0)) ** 2, 1.0)
        ncp = sh["Nx"].to_numpy() * c2 + sh["Ny"].to_numpy() * (1 - c2)
        fcd = pf.alpha_cc * conc.fck / pf.gamma_c
        sigma = np.minimum(ncp * 1e3 / (1000 * h), 0.2 * fcd)
        k = min(1 + math.sqrt(200 / d_s), 2.0)
        si, sj = _cells(sh["X"].to_numpy(), sh["Y"].to_numpy(), x0, y0, size)
        rho = np.array(
            [
                min(rho_at(float(x), float(y), "bottom"), rho_at(float(x), float(y), "top"))
                for x, y in zip(sh["X"], sh["Y"], strict=True)
            ]
        )
        vrdc = (
            np.maximum(
                0.18 / pf.gamma_c * k * (100 * np.minimum(rho, 0.02) * conc.fck) ** (1 / 3),
                0.035 * k**1.5 * math.sqrt(conc.fck),
            )
            + 0.15 * sigma
        )
        if slab.shear_in_tension == "ec2":
            vrdc = np.maximum(vrdc, 0) * d_s  # kN/m; 6.2.2(1) with the tension as a negative σcp
        else:
            # Office rule: no concrete contribution where the slab is in tension in the shear's direction.
            vrdc = np.where(ncp < 0, 0.0, np.maximum(vrdc, 0) * d_s)
        # Voided slab: only the webs between the voids, bw = 1000(1 − D/s) per metre.
        vm_s = vd.mask(vlay, sh["X"], sh["Y"]) if vsec else np.zeros(len(sh), bool)
        web = vsec["x"].web_factor() if vsec else 1.0
        wf = np.where(vm_s, web, 1.0)
        vrdc = vrdc * wf
        fyw = REINFORCEMENT_GRADES[settings.reinforcement.grade] / pf.gamma_s
        z_s = 0.9 * d_s
        nu1 = 0.6 * (1 - conc.fck / 250)
        vrd_max = 1000 * z_s * nu1 * fcd / (2.5 + 1 / 2.5) / 1e3  # kN/m at cot θ = 2.5
        vrd_max_at = vrd_max * wf
        need = v > vrdc
        if slab.shear_links == "office":
            fyk = REINFORCEMENT_GRADES[settings.reinforcement.grade]
            carried = 0.8 * d_s * 0.8 * fyk  # N per (mm²/mm): V = Asw/s · 0.8d · 0.8fyk
        else:
            carried = z_s * fyw * 2.5
        asw = np.where(need, v * 1e3 / carried, 0.0) * 1000  # mm² per m² of slab
        cells = (
            pd.DataFrame({"i": si, "j": sj, "need": need, "asw": asw, "void": vm_s}).groupby(["i", "j"]).max()
        )
        cells = cells[cells["need"]].reset_index()
        s_max = min(0.75 * d_s, 600.0)
        # Links hook round the bottom mesh bars, so they sit at the mesh spacing (or every second bar):
        # across X at the spacing of the bars along Y, across Y at the spacing of the bars along X.
        sx_mesh = layers["bottom_y"]["basic"]["spacing_mm"]
        sy_mesh = layers["bottom_x"]["basic"]["spacing_mm"]
        link_opts = [(0.0, 0, 0.0, 0.0)]  # none
        for phi in (10, 12, 16, 20):
            for sx in (sx_mesh, 2 * sx_mesh):
                for sy in (sy_mesh, 2 * sy_mesh):
                    if max(sx, sy) <= s_max + 1e-9:
                        link_opts.append((math.pi * phi * phi / 4 / (sx * sy / 1e6), phi, sx, sy))
        link_opts.sort()
        rho_min = (
            0.08 * math.sqrt(conc.fck) / REINFORCEMENT_GRADES[settings.reinforcement.grade] * 1e6
        )  # mm²/m²
        link_label = ["no links"] + [f"Ø{o[1]} @ {o[2]:g} × {o[3]:g}" for o in link_opts[1:]]
        in_void = cells["void"].to_numpy(bool)
        req = np.where(
            cells["asw"].to_numpy() > 0,
            np.maximum(cells["asw"].to_numpy(), rho_min * np.where(in_void, web, 1.0)),
            0.0,
        )
        solid = ~in_void
        links, short = link_bands(
            cells[solid].reset_index(drop=True),
            req[solid],
            link_opts,
            link_label,
            size,
            x0,
            y0,
            box,
            slab.min_zone_length,
            slab.strip_direction,
        )
        if in_void.any():
            # In the voided slab the links stand in the webs between the voids: one leg per web (two where
            # the web is 200 mm or wider), at the void spacing across the voids and the mesh spacing along.
            sv = slab.voids.spacing
            web_mm = sv - slab.voids.diameter
            legs = 2 if web_mm >= 200 else 1
            s_along = sx_mesh if vlay["along"] == "X" else sy_mesh
            v_opts = [(0.0, 0, 0.0, 0.0)]
            for phi in (10, 12, 16, 20):
                if web_mm < phi + 2 * 25:  # a leg and some concrete each side
                    continue
                for sa in (s_along, 2 * s_along):
                    if sa <= s_max + 1e-9:
                        a_ = legs * math.pi * phi * phi / 4 / (sa * sv / 1e6)
                        sx_, sy_ = (sa, sv) if vlay["along"] == "X" else (sv, sa)
                        v_opts.append((a_, phi, sx_, sy_))
            v_opts.sort()
            if sv > 1.5 * d_s:
                notes.append(
                    f"Voids: links at the void spacing ({sv:g} mm) are further apart across the slab than "
                    f"1.5d ({1.5 * d_s:.0f} mm, EN 1992-1-1 9.3.2(4))."
                )
            per = "2 legs per web" if legs == 2 else "1 leg per web"
            v_label = ["no links"] + [f"Ø{o[1]} @ {o[2]:g} × {o[3]:g}, {per}" for o in v_opts[1:]]
            if len(v_opts) == 1:
                notes.append(
                    f"Voids: the webs between the voids ({web_mm:g} mm) are too narrow for links, and "
                    f"{int(in_void.sum())} cells in the voided slab need them: wider webs or a solid zone "
                    "there."
                )
                short += int(in_void.sum())
            else:
                v_links, v_short = link_bands(
                    cells[in_void].reset_index(drop=True),
                    req[in_void],
                    v_opts,
                    v_label,
                    size,
                    x0,
                    y0,
                    box,
                    slab.min_zone_length,
                    slab.strip_direction,
                )
                for q in v_links:
                    q["in_webs"] = True
                    q["legs_per_web"] = legs
                    q[vlay["across"].lower()] = [
                        round(min(vlay["positions"]) - sv / 2000, 2),
                        round(max(vlay["positions"]) + sv / 2000, 2),
                    ]
                # For quantities: each band over its own part of the width (the voided part, or the rest).
                vw = len(vlay["positions"]) * sv / 1000
                full = box[vlay["across"]][1] - box[vlay["across"]][0]
                ak = vlay["along"].lower()

                def overlap(a, b):
                    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))

                for q in links:
                    span = q[ak]
                    covered = sum(overlap(span, w[ak]) for w in v_links)
                    q["area_m2"] = round((span[1] - span[0]) * full - covered * vw, 2)
                for q in v_links:
                    q["area_m2"] = round((q[ak][1] - q[ak][0]) * vw, 2)
                links += v_links
                short += v_short
        if frame is not None:
            for q in links:
                q["stations"] = sorted(
                    round((v_ - frame["origin"]) * frame["sign"], 2) for v_ in q[frame["along"].lower()]
                )
        if short:
            notes.append(
                f"{short} cells need more shear links than Ø20 at the mesh spacing: a thicker slab there."
            )
        heaviest = max(links, key=lambda q: q["asw_mm2_per_m2"]) if links else None
        j = int(np.argmax(v - vrdc))
        u_max = float((v / vrd_max_at).max())
        shear = {
            "zones": len(links),
            "link_spacing_mm": {"x": sx_mesh, "y": sy_mesh},
            "method": "EN 1992-1-1 6.2 per metre, v = √(Vx² + Vy²) with the axial force in its direction, at "
            + settings.shear_check_distance
            + " from the pile faces; "
            + (
                "VRd,c reduced by 0.15σcp in tension"
                if slab.shear_in_tension == "ec2"
                else "no concrete contribution in tension"
            )
            + "; links "
            + ("V = Asw/s · 0.8d · 0.8fyk" if slab.shear_links == "office" else "6.2.3, cot θ = 2.5"),
            "cells_needing_links": int(len(cells)),
            "links": links,
            "heaviest": heaviest,
            "governing": {
                "combination": sh["combination"].iloc[j],
                "x": round(float(sh["X"].iloc[j]), 2),
                "y": round(float(sh["Y"].iloc[j]), 2),
                "V_kN_per_m": round(float(v[j]), 1),
                "VRd_c_kN_per_m": round(float(vrdc[j]), 1),
                "VRd_max_kN_per_m": round(float(vrd_max_at[j]), 1),
                "in_voided_slab": bool(vm_s[j]),
            },
            "utilisation": round(max(u_max, 1.01 if short else 0.0), 3),
            "passed": u_max <= 1 and not short,
        }
        if links:
            notes.append(
                f"{len(cells)} cells need shear links (the slab is in tension there, or v > VRd,c), in "
                f"{len(links)} bands across the deck at the mesh spacing; heaviest {heaviest['label']}."
            )
        crush = v > vrd_max_at
        if crush.any():
            w = int(np.argmax(v / vrd_max_at))
            notes.append(
                f"Shear above VRd,max ({vrd_max_at[w]:.0f} kN/m) at {int(crush.sum())} results, the largest "
                f"{v[w]:.0f} kN/m at X {sh['X'].iloc[w]:.1f}, Y {sh['Y'].iloc[w]:.1f}: a thicker slab, or a "
                "pile missing from the workbook there."
            )

    # Punching.
    heads = pile_heads(pile_sheets, elements, box, settings.results_into_connection / 1e3)
    in_slab = sorted({hd["pile"] for hd in heads})
    if slab.punching_piles:
        heads = [hd for hd in heads if hd["pile"] in slab.punching_piles]
        left = [p for p in in_slab if p not in slab.punching_piles]
        if left:
            notes.append(f'Punching not checked for {", ".join(left)} (slab setting "Punching for").')

    def run_punching() -> tuple[list[dict], list[dict]]:
        return unify_punching(
            punching(heads, slab, settings, rho_at, conc, max(covers.values()), beams), slab.punching_per
        )

    punch, punch_types = run_punching()
    punch_bars: list[dict] = []
    if slab.punching_fix == "bars":
        punch, punch_types, punch_bars = _punching_bars(
            punch, punch_types, slab, settings, layers, strip_rows, as_at, punch_extra, run_punching
        )
        if punch_bars:
            notes.append(
                "Punching: where links alone cannot carry it (vEd over kmax·vRd,c), bars are added over the "
                "pile on the face in tension, both ways, to raise ρl; see the punching table. A thicker "
                "slab at the pile is given as the other way."
            )
    if slab.punching_per == "type" and any(len(t["thicknesses_mm"]) > 1 for t in punch_types):
        notes.append(
            "Punching: a pile type has heads with different slab thicknesses; its one design is the "
            "governing head's, at that head's thickness."
        )
    unset = sorted({p["pile"] for p in punch if getattr(elements.get(p["pile"]), "head_level", 0) is None})
    if unset:
        notes.append(
            f"Punching: {', '.join(unset)} {'has' if len(unset) == 1 else 'have'} no pile head level, so the "
            "pile's force and moment are read at its topmost node, inside the connection, where the moment "
            "is largest. Set the head level (the slab soffit) on the pile to read them there."
        )
    if any(p["direction"] == "pile pulls down" for p in punch):
        notes.append(
            "Punching where a pile pulls the slab down is checked as EN 1992-1-1 6.4, the pull entering at "
            "the top of the slab through the pile's bars anchored over the top bars, with the bottom bars "
            "in tension; the anchorage of the pile bars is checked on the pile."
        )
    if slab.punching_thickness:
        notes.append(
            f"Punching uses a depth of {slab.punching_thickness:g} mm (sloped slab); bending uses {h:g} mm."
        )

    # Restraint (basic meshes of the bars along the quay).
    rest = {}
    for layer in LAYERS:
        face, direction = layer.split("_")
        if direction != rest_dir or slab.restraint_check == "off":
            continue
        b = layers[layer]["basic"]
        res = slab_restraint(
            (b["as_mm2_per_m"], b["phi"], b["spacing_mm"], b["layers"]),
            face,
            direction,
            covers,
            h,
            settings,
            conc,
            R,
        )
        res["limit"] = limits[face]
        res["passed"] = res["wk"] <= limits[face] + 1e-9
        rest[layer] = res
    restraint = {
        "length_m": slab.joint_spacing,
        "R": round(R, 3),
        "R_from": "input" if slab.restraint_factor is not None else "length / thickness, ACI 207.2R",
        "check": slab.restraint_check,
        "layers": rest,
    }
    if slab.restraint_check == "off":
        notes.append(
            "No restraint cracking check (slab setting): temperature and shrinkage are taken as axial "
            "tension in the combinations, as the office's slab design."
        )
    elif slab.restraint_check == "report":
        notes.append(
            f"Restraint cracking of the bars along the quay (R = {R:.2f}) is reported, not designed for: "
            "temperature and shrinkage are taken as axial tension in the combinations (slab setting)."
        )
    elif not all(r["passed"] for r in rest.values()):
        notes.append(
            "The basic mesh does not control restraint cracking at every face: heavier basic bars are needed."
        )
    rest_counts = slab.restraint_check == "design"

    # Steel quantities over the slab's cells with results, and how much of each cell's steel is used.
    cell_area = size * size
    kg = sum(float(per_cell[layer]["a"].sum()) for layer in LAYERS) * cell_area / 1e6 * STEEL_DENSITY
    used = pd.concat(per_cell.values()).groupby(["i", "j"])["used"].max()
    area_m2 = len(used) * cell_area
    # The squares over the pile heads and those with no Plaxis node carry bars too: the basic mesh, or
    # the zone's bars where a zone covers them.
    for i, j in gaps:
        cx, cy = x0 + (i + 0.5) * size, y0 + (j + 0.5) * size
        for layer in LAYERS:
            lay = layers[layer]
            a = max(
                [lay["basic"]["as_mm2_per_m"]]
                + [
                    z["as_mm2_per_m"]
                    for z in lay.get("zones") or []
                    if z["x"][0] - 1e-6 <= cx <= z["x"][1] + 1e-6
                    and z["y"][0] - 1e-6 <= cy <= z["y"][1] + 1e-6
                ]
            )
            kg += a * cell_area / 1e6 * STEEL_DENSITY
        area_m2 += cell_area
    allc = pd.concat([pc.assign(layer=k) for k, pc in per_cell.items()])
    punch_kg = sum(b["kg"] for b in punch_bars)
    kg += punch_kg
    rho_dir = []
    for direction in ("x", "y"):
        both = allc[allc["layer"].str.endswith(direction)].groupby(["i", "j"])["a"].sum()
        rho_dir.append(both / (1000 * h))
    rho_cell = pd.concat(rho_dir, axis=1).max(axis=1)
    over = rho_cell[rho_cell > 0.04 + 1e-9]
    if len(over):
        (wi, wj), wr = over.idxmax(), float(over.max())
        notes.append(
            f"Steel over 4% of the slab section at {len(over)} cells, up to {100 * wr:.1f}% at X "
            f"{x0 + (wi + 0.5) * size:.1f}, Y {y0 + (wj + 0.5) * size:.1f}: couplers, or a thicker slab "
            "there."
        )
    # Concrete: the voids taken out (kg/m³ on the concrete that is cast).
    void_share = 0.0
    voids_out = None
    if vsec:
        void_share = min(vd.void_volume(vlay, box) / (area_m2 * h / 1000), 0.9) if area_m2 else 0.0
        voids_out = {
            **vlay,
            "void_share_pct": round(100 * void_share, 1),
            "void_m3": round(vd.void_volume(vlay, box), 1),
            "results_in_voided_slab": int(vm_u.sum()),
        }
        notes.append(
            f"Voids: {len(vlay['positions'])} voids Ø{vlay['diameter_mm']:g} at {vlay['spacing_mm']:g} mm, "
            f"centre {vlay['centre_depth_mm']:g} mm below the top, along {vlay['along']} from "
            f"{vlay['run'][0]:g} to {vlay['run'][1]:g} m ({vlay['run_from']}); solid "
            f"{vlay['flange_top_mm']:g} mm above and {vlay['flange_bottom_mm']:g} mm below them, webs "
            f"{vlay['web_mm']:g} mm. Bending and crack widths on the voided section there, shear on the webs "
            f"(bw {100 * vsec['x'].web_factor():.0f}% of the width) with links in the webs only; punching "
            "on the solid slab."
            + (
                f" The voids stop {vlay['solid_round_piles_m'] * 1000:g} mm short of every pile's face, "
                "so the slab is solid over the piles."
                if vlay.get("solid_round_piles_m") is not None
                else ""
            )
            + (
                f" {len(vlay['left_out'])} void(s) left out along the lines of piles "
                f"({', '.join(f'{p:g}' for p in vlay['left_out'])} m)."
                if vlay["left_out"]
                else ""
            )
        )
    elif slab.voids is not None:
        voids_out = {**vlay, "void_share_pct": 0.0, "void_m3": 0.0} if vlay else None
        if vlay is not None:
            notes.append(
                "Voids: no void fits between the piles with the clear distance set; the slab is solid."
            )
    net = 1 - void_share
    steel = {
        "kg_per_m2": round(kg / area_m2, 1) if area_m2 else None,
        "kg_per_m3": round(kg / area_m2 / (h / 1000 * net)) if area_m2 else None,
        "ratio_pct": round(100 * kg / STEEL_DENSITY / (area_m2 * h / 1000 * net), 2) if area_m2 else None,
        "concrete_share": round(net, 4),
        "area_m2": round(area_m2, 1),
        "total_t": round(kg / 1000, 2),
    }
    if punch_kg:
        steel["punching_bars_kg"] = round(punch_kg)
    bands = [
        [
            round(x0 + (i + 0.5) * size, 2),
            round(y0 + (j + 0.5) * size, 2),
            round(level, 2),
            round(float(u), 3),
            size,
        ]
        for (i, j), u in used.items()
    ]
    # The squares with no result of their own take the worst of the squares round them, so the plots
    # and the 3D cover the whole slab.
    for (i, j), (donors, why) in gaps.items():
        cx, cy = x0 + (i + 0.5) * size, y0 + (j + 0.5) * size
        u = round(max(float(used.loc[d]) for d in donors), 3)
        bands.append([round(cx, 2), round(cy, 2), round(level, 2), u, size, why])
    crack_cells = pd.concat(per_cell.values()).groupby(["i", "j"])["crack"].max().dropna()
    crack_bands = [
        [
            round(x0 + (i + 0.5) * size, 2),
            round(y0 + (j + 0.5) * size, 2),
            round(level, 2),
            round(float(u), 3),
            size,
        ]
        for (i, j), u in crack_cells.items()
    ]
    crack_have = {(int(i), int(j)) for i, j in crack_cells.index}
    for (i, j), (donors, why) in gaps.items():
        vals = [float(crack_cells.loc[d]) for d in donors if d in crack_have]
        if vals:
            cx, cy = x0 + (i + 0.5) * size, y0 + (j + 0.5) * size
            crack_bands.append([round(cx, 2), round(cy, 2), round(level, 2), round(max(vals), 3), size, why])
    punch_u = max(
        [p.get("utilisation_with_links", p["utilisation"]) for p in punch if p.get("passed")], default=0.0
    )
    lay_u = max(layers[layer]["utilisation"] for layer in LAYERS)
    row_ductility(strip_rows + overall_rows, layers, h, fcd_s, fyd, vsec)
    strip_design = None
    if strips:
        summary = {}
        for r in strip_rows:
            key = (r["moment"], tuple(r["station"]), r["strip"])
            score = max(r["ratio"] or 0, (r["wk_mm"] or 0) / r["wk_limit_mm"])
            if key not in summary or score > summary[key][0]:
                summary[key] = (score, r)
        strip_design = {
            "table": strip_table(strip_rows, frame) + overall_table(overall_rows),
            "profile": strip_profile(uls_m, uloc, size, axes),
            "across_profile": across_profile(uls_m, frame, size, axes),
            "profile_qp": strip_profile(qp_m, qloc, size, axes) if qloc is not None else {},
            "across_profile_qp": across_profile(qp_m, frame, size, axes) if len(qp_m) else None,
            "start": frame["start"],
            "end": frame["end"],
            "origin": frame["origin"],
            "sign": frame["sign"],
            "along": frame["along"],
            "from": frame["from"],
            "column_width_m": frame["column"],
            "field_width_m": frame["field"],
            "lines": frame["lines"],
            "pile_rows_m": frame["rows"],
            "stations": frame["bounds"],
            "rows": strip_rows + overall_rows,
            "summary": sorted(
                (v[1] for v in summary.values()), key=lambda r: (r["moment"], r["station"][0], r["strip"])
            ),
        }
        worst = max((r["ratio"] or 0 for r in strip_rows + overall_rows), default=0.0)
        cracks = [r["wk_mm"] / r["wk_limit_mm"] for r in strip_rows + overall_rows if r["wk_mm"] is not None]
        lay_u = max(lay_u, worst, max(cracks, default=0.0))
        notes.append(
            f"Column strips {frame['column']:g} m wide on the {len(frame['lines'])} lines of piles "
            f"along {frame['along']}, field strips {frame['field']:g} m between them; each strip's "
            "moments are averaged across its width and all column (field) strips are designed together "
            f"at {len(frame['bounds']) - 1} stations measured from the {frame['from']}. The bars along "
            f"{frame['across']} are one design over the whole deck, with zones only where it needs more."
        )
    passed = (
        shear["passed"]
        and all(p["passed"] for p in punch)
        and lay_u <= 1 + 1e-6
        and (not rest_counts or all(r["passed"] for r in rest.values()))
    )
    # Sections short of ductility or over-reinforced (x/d, bars not yielding, over 4%).
    duct_rows = []
    for r in strip_rows + overall_rows:
        dct = r.get("ductility") or {}
        if not dct.get("warnings"):
            continue
        where = r.get("label") or (f"{r['strip']} strip, station {r['station'][0]:g} to {r['station'][1]:g}")
        duct_rows.append(
            {
                "layer": r["layer"],
                "where": f"{LAYER_TEXT[r['layer']]}, {where}",
                "bars": r["bars"],
                "x_d": dct["x_d"],
                "ratio_pct": dct["ratio_pct"],
                "warnings": dct["warnings"],
            }
        )
    if duct_rows:
        notes.append(
            f"Over-reinforced? {len(duct_rows)} strip or zone sections are short of ductility or "
            "over-reinforced (x/d above 0.45, bars not yielding or more than 4% steel): see the warning "
            "at the top of the slab."
        )
    mc = uls_m.groupby(["i", "j"]).agg(
        mx_max=("Mx", "max"), mx_min=("Mx", "min"), my_max=("My", "max"), my_min=("My", "min")
    )
    moment_cells = {
        "size": size,
        "x0": x0,
        "y0": y0,
        "names": {k: _map(axes)[v].replace("_", "") for k, v in (("x", "Mx"), ("y", "My"))},
        "cells": [
            [int(i), int(j), *(round(float(v)) for v in r)]
            for (i, j), r in zip(mc.index, mc.to_numpy(), strict=True)
        ],
    }
    # Squares with no result of their own: the envelope of the squares round them, marked with why.
    mc_have = {(int(i), int(j)) for i, j in mc.index}
    for (i, j), (donors, why) in gaps.items():
        d = mc.loc[[c for c in donors if c in mc_have]]
        if len(d):
            moment_cells["cells"].append(
                [
                    i,
                    j,
                    round(float(d["mx_max"].max())),
                    round(float(d["mx_min"].min())),
                    round(float(d["my_max"].max())),
                    round(float(d["my_min"].min())),
                    why,
                ]
            )
    return {
        **base,
        "moment_cells": moment_cells,
        "cover_top_mm": slab.cover_top,
        "cover_bottom_mm": slab.cover_bottom,
        "voids": voids_out,
        "zone_size_m": size,
        "strips": "column_and_field" if strips else "uniform",
        "strip_design": strip_design,
        "ductility": duct_rows,
        "box": box,
        "level_m": round(level, 2),
        "layers": layers,
        "shear": shear,
        "punching": punch,
        "punching_types": punch_types,
        "punching_bars": punch_bars,
        "restraint": restraint,
        "steel": steel,
        "utilisation": round(
            max(
                lay_u,
                max((r["wk"] / r["limit"] for r in rest.values() if rest_counts), default=0),
                punch_u,
            ),
            3,
        ),
        "passed": bool(passed),
        "bands": bands,
        "crack_bands": crack_bands,
        "tension": slab_tension(
            [uls_m, qp_m], [(int(i), int(j)) for i, j in used.index], x0, y0, size, level, h, gaps
        ),
    }
