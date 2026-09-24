"""Slabs (the deck) from the plate results: bars per metre by zone, shear, punching at the piles, restraint.

Directions. Bars run along global X and Y. The local axes of the plate come from the
directions check on upload (local 1 = global X when it had no answer), so for the bars
along X the slab takes Mx = M11 (or M22), Nx = N1 (or N2) and Vx = Q13 (or Q23).

Moments. Wood–Armer design moments from Mx, My and the twisting moment Mxy, bottom
(sagging +) and top (hogging -), at every node. Nodes inside a pile are FE peaks in
the connection and are left out (bending at the pile face); for shear, nodes within d
(or 2d) of a pile face are left out too.

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
A sloped slab can use a different depth for punching.

Restraint: as for beams (``crack.restraint_crack``), with the slab thickness as the height.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from ..elements import CombinationType, combination_type
from ..importer import SheetData
from ..materials import REINFORCEMENT_GRADES, STEEL_DENSITY, concrete
from ..project import DesignSettings, PileInput, SlabInput, SlabStrips, with_project_grades
from .crack import K1, K3, K4, KT, autogenous_shrinkage, restraint_crack, restraint_factor
from .governing import crack_terms
from .tension import slab_tension

E_S = 200_000.0
PREMIUM = 0.10  # extra weight per zoned cell when picking the basic mesh
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
        if r.slab_spacings:
            spacings = sorted({float(s) for s in r.slab_spacings if s >= phi + r.min_clear_spacing - 1e-9})
        else:
            spacings, s = [], r.max_spacing
            while s >= max(100.0, phi + r.min_clear_spacing) - 1e-9:
                spacings.append(s)
                s -= r.spacing_step
        for s in spacings:
            for layers in range(1, (r.max_layers if phi >= 25 else 1) + 1):
                out.append((layers * 1000 * math.pi * phi * phi / 4 / s, phi, s, layers))
    # Cheapest first; a second layer costs 10% more to place.
    return sorted(out, key=lambda o: (o[0] * (1 + 0.1 * (o[3] - 1)), -o[1]))


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
    slab: SlabInput, box: dict, piles: list[tuple], beams: list[dict], stations: list[float] | None = None
) -> dict | None:
    """Where the column and field strips lie: the lines of piles along the strips, the distance along
    them from the sea side (the front wall line, the front beam's centre, as the office's stations),
    and the station boundaries."""
    along = slab.strip_direction
    ai, ci = (0, 1) if along == "X" else (1, 0)
    lines = sorted({round(p[ci], 1) for p in piles})
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
                    "MRd_kNm_per_m": worst["MRd_kNm_per_m"],
                    "combination": worst["combination"],
                    "face": worst["face"],
                    "bars": {f: mine[g[0]][f]["bars"] for f in ("bottom", "top") if f in mine[g[0]]},
                    "additional": {
                        f: mine[g[0]][f]["additional"] for f in ("bottom", "top") if f in mine[g[0]]
                    },
                    "layers": {f: mine[g[0]][f]["layer"] for f in ("bottom", "top") if f in mine[g[0]]},
                    "keys": {f: [mine[k][f]["key"] for k in g if f in mine[k]] for f in ("bottom", "top")},
                    "user_set": any(r["user_set"] for r in faces),
                }
            )
    return out


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


def strip_average(f: pd.DataFrame, m: np.ndarray, n: np.ndarray, loc: dict, size: float) -> pd.DataFrame:
    """Moment and axial force per metre averaged across each strip's width, at every cut along it
    (``size`` apart) and for every combination."""
    df = pd.DataFrame(
        {
            "combination": f["combination"].to_numpy(),
            "st": loc["st"],
            "kind": loc["kind"],
            "inst": loc["inst"],
            "cut": np.floor(loc["s"] / size + 1e-9).astype(int),
            "m": m,
            "n": n,
        }
    )[loc["averaged"]]
    keys = ["st", "kind", "inst", "cut", "combination"]
    return df.groupby(keys, sort=False)[["m", "n"]].mean().reset_index()


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
    for a in slab.crane:
        m = (
            uls["X"].between(min(a.x_from, a.x_to), max(a.x_from, a.x_to))
            & uls["Y"].between(min(a.y_from, a.y_to), max(a.y_from, a.y_to))
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


def additional_options(mesh: tuple, settings: DesignSettings) -> tuple[list, list, list]:
    """The mesh alone, then the mesh with additional bars, least steel first.

    Additional bars go between the mesh bars: in every second gap, in every gap, in every gap in two
    layers, or also under the mesh bars (three per gap, two layers). For crack widths the mix has
    the equivalent Ø of 7.12 and the largest gap between tension bars. Returns (options as
    (mm²/m, Ø, spacing, layers), Ø setting the depth, labels).
    """
    area, phi_b, s_b, layers_b = mesh
    n_b = layers_b * 1000 / s_b
    out = [(mesh, phi_b, label(mesh))]
    for phi_a in settings.reinforcement.bar_diameters:
        if phi_a < 10 or s_b / 2 - max(phi_a, phi_b) < settings.reinforcement.min_clear_spacing:
            continue
        for n_a, spacing, lay, text in (
            (1000 / s_b, s_b / 2, layers_b, f"Ø{phi_a} @ {s_b:g}"),
            (1000 / (2 * s_b), s_b, layers_b, f"Ø{phi_a} @ {2 * s_b:g}"),
            (2000 / s_b, s_b / 2, max(layers_b, 2), f"Ø{phi_a} @ {s_b:g} in 2 layers"),
            (
                3000 / s_b,
                s_b / 2,
                max(layers_b, 2),
                f"Ø{phi_a} @ {s_b:g} in 2 layers + Ø{phi_a} under the mesh",
            ),
        ):
            phi_eq = (n_b * phi_b**2 + n_a * phi_a**2) / (n_b * phi_b + n_a * phi_a)
            o = (area + n_a * math.pi * phi_a**2 / 4, phi_eq, spacing, lay)
            out.append((o, max(phi_a, phi_b), text))
    out = out[:1] + sorted(out[1:], key=lambda t: t[0][0] * (1 + 0.1 * (t[0][3] - 1)))
    return [t[0] for t in out], [t[1] for t in out], [t[2] for t in out]


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
) -> dict:
    """Basic mesh and zones for one layer.

    ``need["idx"]`` is the cheapest option each cell can take and ``ok[cell, option]`` whether
    it can take an option at all. The basic mesh is the option with the least total steel, cells
    that cannot take it getting their own option at a premium.
    """
    idx = need["idx"].to_numpy(int)
    areas = np.array([o[0] * (1 + 0.1 * (o[3] - 1)) for o in options])
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


def _rects(cells: set, grow: int) -> list[list[int]]:
    """Rectangles [i0, i1, j0, j1] round each group of touching cells, each side at least ``grow`` + 1
    cells long, overlapping ones merged."""
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
                if p[0] <= q[1] + 1 and q[0] <= p[1] + 1 and p[2] <= q[3] + 1 and q[2] <= p[3] + 1:
                    boxes[m] = [min(p[0], q[0]), max(p[1], q[1]), min(p[2], q[2]), max(p[3], q[3])]
                    del boxes[n]
                    merged = True
                    break
            if merged:
                break
    return boxes


def link_zones(cells, req, opts, labels, size, x0, y0, box, min_zone) -> tuple[list[dict], int]:
    """Shear link zones, independent of the bending zones: the lightest links that are needed over the
    widest area, then heavier links in the zones inside it that need them, as the bars are zoned.
    ``req`` is each cell's need (mm²/m²); ``opts`` (mm²/m², Ø, sx, sy) lightest first, 0 = none."""
    level = np.array(
        [next((m for m, o in enumerate(opts) if m and o[0] >= r - 1e-6), len(opts)) for r in req]
    )
    short = int((level >= len(opts)).sum())
    level = np.minimum(level, len(opts) - 1)
    at = list(zip(cells["i"].tolist(), cells["j"].tolist(), strict=True))
    grow = max(0, math.ceil(min_zone / size - 1e-9) - 1)
    out = []
    covered: dict = {}  # cell -> level its zone already gives
    for lv in sorted(set(level.tolist())):
        # Cells the zones so far do not cover with enough links.
        todo = {c for c, v in zip(at, level, strict=True) if v >= lv and covered.get(c, 0) < v}
        if not todo:
            continue
        for i0, i1, j0, j1 in _rects(todo, grow):
            inside = [
                (c, v, r)
                for c, v, r in zip(at, level, req, strict=True)
                if i0 <= c[0] <= i1 and j0 <= c[1] <= j1
            ]
            # This level's links over the zone; its cells that need more get a heavier zone inside it.
            give = lv
            for c, _, _ in inside:
                covered[c] = max(covered.get(c, 0), give)
            o = opts[give]
            out.append(
                {
                    "x": [
                        round(max(x0, x0 + i0 * size), 2),
                        round(min(box["X"][1], x0 + (i1 + 1) * size), 2),
                    ],
                    "y": [
                        round(max(y0, y0 + j0 * size), 2),
                        round(min(box["Y"][1], y0 + (j1 + 1) * size), 2),
                    ],
                    "phi": o[1],
                    "sx_mm": o[2],
                    "sy_mm": o[3],
                    "asw_mm2_per_m2": round(o[0]),
                    "needs_mm2_per_m2": round(float(max(r for c, _, r in inside if c in todo))),
                    "cells": sum(1 for c, _, _ in inside if c in todo),
                    "label": labels[give],
                }
            )
    return out, short


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
        for k_ in ("_v", "_beta", "_vrdc"):
            worst.pop(k_)
        out.append(worst)
    return out


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
) -> dict[str, Any]:
    slab = with_project_grades(slab, settings.materials, settings.durability)
    choices = choices or SlabStrips()
    conc = concrete(slab.concrete)
    fyk = REINFORCEMENT_GRADES[settings.reinforcement.grade]
    fyd = fyk / settings.partial_factors.gamma_s
    e_eff = conc.ecm / (1 + settings.cracking.creep_coefficient)
    sag = 1.0 if settings.plate_positive_moment == "sagging" else -1.0
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
    options.sort(key=lambda o: (o[0] * (1 + 0.1 * (o[3] - 1)), -o[1]))
    notes = [
        "Bars along X take Mx = "
        + _map(axes)["Mx"].replace("_", "")
        + (" (from the directions check)." if axes else " (assumed: the directions check had no answer)."),
        "Positive plate moments taken as " + settings.plate_positive_moment + " (Design settings).",
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
    if uls_m.empty:
        notes.append("No ULS results outside the pile heads.")
        return {**base, "utilisation": None, "passed": False}
    if slab.peaks == "average" and piles:
        uls_m = average_peaks(uls_m, piles)
        qp_m = average_peaks(qp_m, piles) if len(qp_m) else qp_m
        notes.append(
            "Moments at the pile faces averaged over a ring one pile diameter wide round each pile "
            "(slab setting)."
        )

    covers = {"bottom": slab.cover_bottom, "top": slab.cover_top}
    limits = {"bottom": slab.crack_width_limit_bottom, "top": slab.crack_width_limit}
    x0, y0 = box["X"][0], box["Y"][0]
    ui, uj = _cells(uls_m["X"].to_numpy(), uls_m["Y"].to_numpy(), x0, y0, size)
    uls_m = uls_m.assign(i=ui, j=uj)
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
        strip_frame(slab, box, piles, beams, choices.stations) if slab.strips == "column_and_field" else None
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
    layers: dict[str, dict] = {}
    per_cell: dict[str, pd.DataFrame] = {}
    worst_k, worst_k_at = 0.0, None
    areas = np.array([o[0] for o in options])
    phi_est = 20

    def depth(face: str, direction: str) -> float:
        return h - covers[face] - phi_est / 2 - (phi_est if direction == "y" else 0)

    # Steel per node for each layer; where K > K' the opposite face's bars work in compression.
    node_req = {layer: np.zeros(len(uls_m)) for layer in LAYERS}
    compression = {layer: np.zeros(len(uls_m)) for layer in LAYERS}
    for layer in LAYERS:
        face, direction = layer.split("_")
        other = "top" if face == "bottom" else "bottom"
        n = uls_m["Nx" if direction == "x" else "Ny"].to_numpy()
        d2 = h - depth(other, direction)
        a_req, k, a_s2 = required_as(wa[layer], n, h, depth(face, direction), conc.fck, fyd, d2)
        node_req[layer] = a_req
        compression[f"{other}_{direction}"] = a_s2
        if len(k) and float(k.max()) > worst_k:
            w = int(np.argmax(k))
            worst_k = float(k[w])
            worst_k_at = (layer, float(uls_m["X"].iloc[w]), float(uls_m["Y"].iloc[w]))
    for layer in LAYERS:
        face, direction = layer.split("_")
        d = depth(face, direction)
        a_req = np.maximum(node_req[layer], compression[layer])
        a_min = max(0.26 * conc.fctm / fyk, 0.0013) * 1000 * d
        need = pd.DataFrame({"i": uls_m["i"], "j": uls_m["j"], "req": np.maximum(a_req, a_min)})
        cell = need.groupby(["i", "j"])["req"].max().reset_index()
        nq = pos = None
        if wq is not None:
            nq = qp_m["Nx" if direction == "x" else "Ny"].to_numpy()
            key = pd.MultiIndex.from_arrays([qp_m["i"], qp_m["j"]])
            pos = pd.MultiIndex.from_arrays([cell["i"], cell["j"]]).get_indexer(key)
        groups: dict = {}
        members: dict = {}
        qgroups: dict = {}
        sets: dict = {}  # per strip and station: the governing cut of every combination, for AdSec
        if strips:
            # Every column strip together and every field strip together, station by station: the need
            # is the worst cut across a strip's width, averaged over that width.
            other = "top" if face == "bottom" else "bottom"
            n_u = uls_m["Nx" if direction == "x" else "Ny"].to_numpy()
            env = strip_average(uls_m, wa[layer], n_u, uloc, size)
            a_env, _, _ = required_as(
                env["m"].to_numpy(), env["n"].to_numpy(), h, d, conc.fck, fyd, h - depth(other, direction)
            )
            env["req"] = np.maximum(a_env, a_min)
            gov = env.loc[env.groupby(["st", "kind"])["req"].idxmax()]
            groups = {(int(r.st), int(r.kind)): r for r in gov.itertuples(index=False)}
            for r in env.loc[env.groupby(["st", "kind", "combination"])["req"].idxmax()].itertuples(
                index=False
            ):
                if abs(r.m) >= 0.5:
                    sets.setdefault((int(r.st), int(r.kind)), {"uls": [], "qp": []})["uls"].append(
                        {
                            "combination": str(r.combination),
                            "N_kN_per_m": round(float(r.n), 1),
                            "M_kNm_per_m": round(float(r.m), 1),
                        }
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
                qenv = strip_average(qp_m, wq[layer], nq, qloc, size)
                qgov = qenv.loc[
                    qenv.assign(a=qenv["m"].abs()).groupby(["st", "kind", "combination"])["a"].idxmax()
                ]
                for r in qgov.itertuples(index=False):
                    if abs(r.m) >= 0.5:
                        sets.setdefault((int(r.st), int(r.kind)), {"uls": [], "qp": []})["qp"].append(
                            {
                                "combination": str(r.combination),
                                "N_kN_per_m": round(float(r.n), 1),
                                "M_kNm_per_m": round(float(r.m), 1),
                            }
                        )
                for k, g in qenv.groupby(["st", "kind"]):
                    qgroups[(int(k[0]), int(k[1]))] = (
                        g["m"].to_numpy(),
                        g["n"].to_numpy(),
                        g["combination"].to_numpy(),
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
        ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
            """Area worth at depth d, and ok[cell, option] for strength alone and for all checks.

            Bigger bars and a second layer sit deeper in, so they count for less. Options are
            (mm²/m, Ø for cracks, spacing for cracks, layers); ``dphi`` is the Ø setting the depth.
            """
            ar = np.array([o[0] for o in opts])
            d_opt = np.array(
                [
                    h - covers[face] - f / 2 - (f if direction == "y" else 0) - (o[3] - 1) * (f + 25) / 2
                    for o, f in zip(opts, dphi, strict=True)
                ]
            )
            eff = ar * np.minimum(d_opt / d, 1.0)
            strength = eff[None, :] >= cell["req"].to_numpy()[:, None] - 1e-6
            ok = strength.copy()
            if wq is not None:
                crack_ok = np.ones_like(ok)
                for oi, o in enumerate(opts):
                    if strips:
                        for k, (qm, qn, _) in qgroups.items():
                            if k not in members:
                                continue
                            w = crack_widths(
                                qm, qn, o[0], o[1], o[2], h, d_opt[oi], covers[face], conc, e_eff
                            )
                            if w.max() > limits[face] + 1e-9:
                                crack_ok[members[k], oi] = False
                        continue
                    w = crack_widths(wq[layer], nq, o[0], o[1], o[2], h, d_opt[oi], covers[face], conc, e_eff)
                    crack_ok[pos[(w > limits[face] + 1e-9) & (pos >= 0)], oi] = False
                ok &= crack_ok
            rest = np.array(
                [
                    slab_restraint(o, face, direction, covers, h, settings, conc, R)["wk"]
                    <= limits[face] + 1e-9
                    for o in opts
                ]
            )
            return eff, strength, ok & rest[None, :], rest

        eff, strength_ok, ok, mesh_rest = assess(options, [o[1] for o in options])
        first = lambda m: np.where(m.any(axis=1), m.argmax(axis=1), m.shape[1] - 1)  # noqa: E731
        cell["idx"] = first(ok)
        cell["uidx"] = first(strength_ok)
        req_eff = cell["req"].to_numpy()
        forced = options.index(meshes[layer]) if layer in meshes else None
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
        else:
            # A mesh everywhere, and additional bars between its bars where it is not enough. The mesh
            # is the one with the least steel overall, its additional bars included.
            target = req_eff
            min_clear = settings.reinforcement.min_clear_spacing
            cands = (
                [forced]
                if forced is not None
                else [
                    k for k, o in enumerate(options) if o[2] / 2 - max(o[1], 10) >= min_clear and mesh_rest[k]
                ]
                or [int(np.argmax(mesh_rest))]
            )
            best = None
            for k in cands:
                combos, dphi, labels = additional_options(options[k], settings)
                eff2, _, ok2, _ = assess(combos, dphi)
                ok2 = (eff2[None, :] >= target[:, None] - 1e-6) & ok2
                idx = first(ok2)
                ar = np.array([o[0] for o in combos])
                # A cell nothing fits is priced at twice the heaviest bars, so that a few cells a thicker
                # slab must solve do not drive the mesh everywhere.
                short = np.where(ok2.any(axis=1), 1.0, 2.0)
                cost = float((ar[idx] * np.where(idx > 0, 1 + PREMIUM, 1.0) * short).sum())
                if best is None or cost < best[0] - 1e-6:
                    best = (cost, k, combos, labels, eff2, ok2, dphi)
            _, b, opts, labels, eff_all, ok2, dphis = best
            z = zones_for(
                cell.assign(idx=first(ok2)), ok2, opts, size, x0, y0, along, slab.min_zone_length, 0, labels
            )
            for zz in z["zones"]:
                zz["additional_mm2_per_m"] = round(zz["as_mm2_per_m"] - options[b][0])
        z["basic"]["set_by"] = "user" if forced is not None else "least steel"
        z["mode"] = mode
        chosen = np.array(
            z.pop("cell_index")
        )  # the bars each cell gets: the mesh, or the mesh with additional bars
        # Bars the user set for a station and strip: every cell of that strip at that station gets them.
        user_keys: set = set()
        if strips:
            for k, idxs in members.items():
                key = strip_key(layer, frame["bounds"], k)
                want = choices.bars.get(key)
                if want is None:
                    continue
                if mode == "mesh_only" or want not in labels and want != "mesh only":
                    notes.append(
                        f"Bars set for {key.replace('|', ' ')} ({want}) are not among the options: left out."
                    )
                    continue
                chosen[idxs] = 0 if want == "mesh only" else labels.index(want)
                user_keys.add(k)
        prov = np.array([o[0] for o in opts])[chosen]
        ratio = req_eff / eff_all[chosen]
        util = ratio.max()
        if util > 1 + 1e-6:
            w = int(np.argmax(ratio))
            wx, wy = x0 + (cell["i"].iloc[w] + 0.5) * size, y0 + (cell["j"].iloc[w] + 0.5) * size
            notes.append(
                f"{LAYER_TEXT[layer].capitalize()}: the heaviest bars ({labels[-1]}) are not enough "
                f"at X {wx:.1f}, Y {wy:.1f} ({util:.2f}): a thicker slab or a haunch is needed there."
            )
        # wk / limit per cell with the bars it gets, for the 3D view's "Crack width" mode.
        crack_u = np.full(len(cell), np.nan)
        if wq is not None:
            d_all = [
                h - covers[face] - f / 2 - (f if direction == "y" else 0) - (o[3] - 1) * (f + 25) / 2
                for o, f in zip(opts, dphis, strict=True)
            ]
            for oi in np.unique(chosen):
                o, d_o = opts[int(oi)], d_all[int(oi)]
                if strips:
                    for k, (qm, qn, _) in qgroups.items():
                        idxs = [c for c in members.get(k, []) if chosen[c] == oi]
                        if idxs:
                            w = crack_widths(qm, qn, o[0], o[1], o[2], h, d_o, covers[face], conc, e_eff)
                            crack_u[idxs] = np.fmax(crack_u[idxs], float(w.max()) / limits[face])
                    continue
                rows = (pos >= 0) & (chosen[np.maximum(pos, 0)] == oi)
                if rows.any():
                    w = crack_widths(
                        wq[layer][rows], nq[rows], o[0], o[1], o[2], h, d_o, covers[face], conc, e_eff
                    )
                    at = pos[rows]
                    vals = np.zeros(len(cell))
                    np.maximum.at(vals, at, w / limits[face])
                    hit = np.zeros(len(cell), bool)
                    hit[at] = True
                    crack_u[hit] = np.fmax(crack_u[hit], vals[hit])
        if strips:
            eff_c = eff_all[chosen]
            name_m = _map(axes)["Mx" if direction == "x" else "My"].replace("_", "")
            for k, g in sorted(groups.items()):
                idxs = members.get(k)
                if not idxs:
                    continue
                c = idxs[int(np.argmin(eff_c[idxs]))]
                oi = int(chosen[c])
                o, f = opts[oi], dphis[oi]
                d_o = h - covers[face] - f / 2 - (f if direction == "y" else 0) - (o[3] - 1) * (f + 25) / 2
                mrd = strip_mrd(o[0], d_o, h, float(g.n), fcd_s, fyd)
                wk = qcomb = None
                if k in qgroups:
                    qm, qn, qc = qgroups[k]
                    w = crack_widths(qm, qn, o[0], o[1], o[2], h, d_o, covers[face], conc, e_eff)
                    wi = int(np.argmax(w))
                    wk, qcomb = round(float(w[wi]), 3), str(qc[wi])
                strip_sets = sets.get(k, {"uls": [], "qp": []})
                for q in strip_sets["qp"]:
                    t = crack_widths(
                        np.array([q["M_kNm_per_m"]]),
                        np.array([q["N_kN_per_m"]]),
                        o[0],
                        o[1],
                        o[2],
                        h,
                        d_o,
                        covers[face],
                        conc,
                        e_eff,
                        terms=True,
                    )
                    q["crack"] = {
                        **crack_terms(t[0][0], limits[face], t[1][0], t[2], t[3], h),
                        "face": face,
                        "d_mm": round(d_o),
                        "phi_mm": o[1],
                        "spacing_mm": o[2],
                    }
                bars = labels[oi] if mode == "mesh_only" or oi == 0 else f"{label(options[b])} + {labels[oi]}"
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
                        "mesh": {"phi": mesh_o[1], "spacing_mm": mesh_o[2], "layers": mesh_o[3]},
                        "additional_bars": None if mode == "mesh_only" or oi == 0 else labels[oi],
                        "sets": strip_sets,
                    }
                )
        crack_gov = int((areas[cell["idx"].to_numpy()] > areas[cell["uidx"].to_numpy()] + 1e-6).sum())
        z.pop("basic_index")
        layers[layer] = {
            **z,
            "additional_labels": ["mesh only", *labels[1:]] if mode != "mesh_only" else [],
            "d_mm": round(d),
            "as_min_mm2_per_m": round(a_min),
            "utilisation": round(float(util), 3),
            "cells_set_by_cracks": crack_gov,
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

    def rho_at(x: float, y: float, face: str) -> float:
        i, j = (int(v[0]) for v in _cells(np.array([x]), np.array([y]), x0, y0, size))
        vals = []
        for direction in ("x", "y"):
            lay = layers[f"{face}_{direction}"]
            a = prov_at[f"{face}_{direction}"].get((i, j), lay["basic"]["as_mm2_per_m"])
            vals.append(a / (1000 * lay["d_mm"]))
        return math.sqrt(vals[0] * vals[1])

    # Shear per metre.
    pf = settings.partial_factors
    dv = (h - max(covers.values()) - 20) / 1000 * (2 if settings.shear_check_distance == "2d" else 1)
    # Within 2d of a pile face the punching check (6.4) governs, so one-way shear starts there at the least.
    sh = uls[outside(uls, max(dv, 2 * (h - max(covers.values()) - 20) / 1000))].reset_index(drop=True)
    shear = {"cells_needing_links": 0, "utilisation": 0.0, "passed": True, "links": []}
    if len(sh):
        d_s = h - max(covers.values()) - 20
        v = np.hypot(sh["Vx"].to_numpy(), sh["Vy"].to_numpy())
        ncp = np.minimum(sh["Nx"].to_numpy(), sh["Ny"].to_numpy())
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
        # Project rule: no concrete contribution where the slab is in tension.
        vrdc = np.where(ncp < 0, 0.0, np.maximum(vrdc, 0) * d_s)  # kN/m
        fyw = REINFORCEMENT_GRADES[settings.reinforcement.grade] / pf.gamma_s
        z_s = 0.9 * d_s
        nu1 = 0.6 * (1 - conc.fck / 250)
        vrd_max = 1000 * z_s * nu1 * fcd / (2.5 + 1 / 2.5) / 1e3  # kN/m at cot θ = 2.5
        need = v > vrdc
        if slab.shear_links == "office":
            fyk = REINFORCEMENT_GRADES[settings.reinforcement.grade]
            carried = 0.8 * d_s * 0.8 * fyk  # N per (mm²/mm): V = Asw/s · 0.8d · 0.8fyk
        else:
            carried = z_s * fyw * 2.5
        asw = np.where(need, v * 1e3 / carried, 0.0) * 1000  # mm² per m² of slab
        cells = pd.DataFrame({"i": si, "j": sj, "need": need, "asw": asw}).groupby(["i", "j"]).max()
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
        req = np.where(cells["asw"].to_numpy() > 0, np.maximum(cells["asw"].to_numpy(), rho_min), 0.0)
        links, short = link_zones(cells, req, link_opts, link_label, size, x0, y0, box, slab.min_zone_length)
        if short:
            notes.append(
                f"{short} cells need more shear links than Ø20 at the mesh spacing: a thicker slab there."
            )
        heaviest = max(links, key=lambda q: q["asw_mm2_per_m2"]) if links else None
        j = int(np.argmax(v - vrdc))
        u_max = float((v / vrd_max).max())
        shear = {
            "zones": len(links),
            "link_spacing_mm": {"x": sx_mesh, "y": sy_mesh},
            "method": "EN 1992-1-1 6.2 per metre, v = √(Vx² + Vy²), at "
            + settings.shear_check_distance
            + " from the pile faces; no concrete contribution in tension; links "
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
                "VRd_max_kN_per_m": round(vrd_max, 1),
            },
            "utilisation": round(max(u_max, 1.01 if short else 0.0), 3),
            "passed": u_max <= 1 and not short,
        }
        if links:
            notes.append(
                f"{len(cells)} cells need shear links (the slab is in tension there, or v > VRd,c), in "
                f"{len(links)} zones at the mesh spacing; heaviest {heaviest['label']}."
            )

    # Punching.
    heads = pile_heads(pile_sheets, elements, box, settings.results_into_connection / 1e3)
    punch = punching(heads, slab, settings, rho_at, conc, max(covers.values()), beams)
    if slab.punching_thickness:
        notes.append(
            f"Punching uses a depth of {slab.punching_thickness:g} mm (sloped slab); bending uses {h:g} mm."
        )

    # Restraint (basic meshes).
    rest = {}
    for layer in LAYERS:
        face, direction = layer.split("_")
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
        "layers": rest,
    }
    if not all(r["passed"] for r in rest.values()):
        notes.append(
            "The basic mesh does not control restraint cracking at every face: heavier basic bars are needed."
        )

    # Steel quantities over the slab's cells with results, and how much of each cell's steel is used.
    cell_area = size * size
    kg = sum(float(per_cell[layer]["a"].sum()) for layer in LAYERS) * cell_area / 1e6 * STEEL_DENSITY
    used = pd.concat(per_cell.values()).groupby(["i", "j"])["used"].max()
    area_m2 = len(used) * cell_area
    steel = {
        "kg_per_m2": round(kg / area_m2, 1) if area_m2 else None,
        "kg_per_m3": round(kg / area_m2 / (h / 1000)) if area_m2 else None,
        "ratio_pct": round(100 * kg / STEEL_DENSITY / (area_m2 * h / 1000), 2) if area_m2 else None,
        "area_m2": round(area_m2, 1),
        "total_t": round(kg / 1000, 2),
    }
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
    # Cells of the slab's grid without a result: over a pile head (left out) or with no Plaxis node.
    ni = int(math.floor((box["X"][1] - x0) / size + 1e-9)) + 1
    nj = int(math.floor((box["Y"][1] - y0) / size + 1e-9)) + 1
    have = set(used.index)
    for i in range(ni):
        for j in range(nj):
            if (i, j) in have:
                continue
            cx, cy = x0 + (i + 0.5) * size, y0 + (j + 0.5) * size
            if cx > box["X"][1] + 1e-6 or cy > box["Y"][1] + 1e-6:
                continue
            over_pile = any(math.hypot(cx - px, cy - py) <= pr + size * 0.75 for px, py, pr in piles)
            bands.append(
                [round(cx, 2), round(cy, 2), round(level, 2), None, size, "pile" if over_pile else "no node"]
            )
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
    punch_u = max(
        [p.get("utilisation_with_links", p["utilisation"]) for p in punch if p.get("passed")], default=0.0
    )
    lay_u = max(layers[layer]["utilisation"] for layer in LAYERS)
    strip_design = None
    if strips:
        summary = {}
        for r in strip_rows:
            key = (r["moment"], tuple(r["station"]), r["strip"])
            score = max(r["ratio"] or 0, (r["wk_mm"] or 0) / r["wk_limit_mm"])
            if key not in summary or score > summary[key][0]:
                summary[key] = (score, r)
        strip_design = {
            "table": strip_table(strip_rows, frame),
            "profile": strip_profile(uls_m, uloc, size, axes),
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
            "rows": strip_rows,
            "summary": sorted(
                (v[1] for v in summary.values()), key=lambda r: (r["moment"], r["station"][0], r["strip"])
            ),
        }
        worst = max((r["ratio"] or 0 for r in strip_rows), default=0.0)
        cracks = [r["wk_mm"] / r["wk_limit_mm"] for r in strip_rows if r["wk_mm"] is not None]
        lay_u = max(lay_u, worst, max(cracks, default=0.0))
        notes.append(
            f"Column strips {frame['column']:g} m wide on the {len(frame['lines'])} lines of piles "
            f"along {frame['along']}, field strips {frame['field']:g} m between them; each strip's "
            "moments are averaged across its width and all column (field) strips are designed together "
            f"at {len(frame['bounds']) - 1} stations measured from the {frame['from']}."
        )
    passed = (
        shear["passed"]
        and all(p["passed"] for p in punch)
        and lay_u <= 1 + 1e-6
        and all(r["passed"] for r in rest.values())
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
    return {
        **base,
        "moment_cells": moment_cells,
        "cover_top_mm": slab.cover_top,
        "cover_bottom_mm": slab.cover_bottom,
        "zone_size_m": size,
        "strips": "column_and_field" if strips else "uniform",
        "strip_design": strip_design,
        "box": box,
        "level_m": round(level, 2),
        "layers": layers,
        "shear": shear,
        "punching": punch,
        "restraint": restraint,
        "steel": steel,
        "utilisation": round(
            max(lay_u, max((r["wk"] / r["limit"] for r in rest.values()), default=0), punch_u), 3
        ),
        "passed": bool(passed),
        "bands": bands,
        "crack_bands": crack_bands,
        "tension": slab_tension(
            [uls_m, qp_m], [(int(i), int(j)) for i, j in used.index], x0, y0, size, level, h
        ),
    }
