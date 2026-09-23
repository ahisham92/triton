"""Front, rear and transverse beams modelled as plates: section forces, cage, links and cracking.

Section forces. The beam is a strip of plate elements. At stations along it the
plate results are fitted across the width, over ±0.8 m along the beam, with a
line f = a + c·t (t across from the centre line), so for a width B

    N  = a_N·B            (in-plane force along the beam, compression +)
    Mv = a_M·B            (vertical bending, sagging +)
    Mh = c_N·B³/12        (horizontal bending, from the in-plane force varying across)
    V  = a_Q·B            (vertical shear),  Vh = a_Q12·B (horizontal shear)
    T  = a_M12·B − c_Q·B³/12  (torsion: twisting moments and the vertical shear's lever arm)

The span direction is the beam's longer plan extent; the local axis that runs
along it comes from the directions check on upload (local 1 = global X if it
could not tell). Across the beam, the per-metre moment, in-plane force and
shear of the other local direction are used node by node for the transverse
bars.

Connections. Piles and king piles that reach the beam are supports. Results
inside them are FE peaks: bending is taken at their face, shear at d (or 2d,
Design settings) from it, and where supports are so close that no station is
that far out, at the station midway between them.

Design, to EN 1992-1-1:

* one longitudinal cage for the whole beam (top, bottom and side bars): N with
  biaxial bending by 5.8.9(4) (``rect``); minimum steel 9.2.1.1; stepping up the face
  that governs, cheapest first;
* crack widths under QP loads at the top and bottom faces (7.3.4, ``crack``), each
  against its own limit;
* restrained early-age cooling, seasonal temperature and autogenous shrinkage
  (EN 1992-3 Annex M, CIRIA C660) over the length between joints;
* links, one arrangement for the whole beam: vertical shear with torsion (6.2,
  6.3), with σcp from compression and no concrete contribution in tension;
  horizontal shear; and the transverse shear per metre; minimum links 9.2.2;
* transverse top and bottom bars per metre from the transverse moments, with
  their own crack widths.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from ..elements import CombinationType, ElementType, combination_type
from ..importer import SheetData
from ..materials import REINFORCEMENT_GRADES, STEEL_DENSITY, concrete
from ..project import BeamInput, CombiWallInput, DesignSettings, PileInput, with_project_grades
from .bollard import check_bollard
from .circular import ConcreteLaw, SteelLaw
from .crack import autogenous_shrinkage, crack_width, restraint_crack, restraint_factor
from .governing import pick_sets
from .rect import Bars, RectSection

BEAM_TYPES = (ElementType.FRONT_BEAM, ElementType.REAR_BEAM, ElementType.TRANSVERSE_BEAM)
WINDOW = 0.8  # m, half-length of the fit along the beam (shorter fits pick up the nodal noise of the shears)
MIN_BAR = 12
MIN_LINK_SPACING = 75.0
BAND = 0.5  # m, heat-map bands along the beam


# --- Section forces ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Layout:
    along: str  # global axis along the beam
    across: str
    centre: float  # m, centre line (across coordinate)
    width: float  # m, width in the model
    level: float  # m, Z of the plate
    start: float  # m, along coordinate range
    end: float
    span_local: str  # "1" or "2": the local axis along the beam


def layout(sheets: dict[str, SheetData], axes: dict[str, str] | None) -> Layout:
    f = pd.concat([s.frame[["X", "Y", "Z"]] for s in sheets.values()])
    ext = {a: float(np.ptp(f[a].to_numpy(float))) for a in "XY"}
    along = "Y" if ext["Y"] >= ext["X"] else "X"
    across = "X" if along == "Y" else "Y"
    lo, hi = float(f[across].min()), float(f[across].max())
    one = (axes or {}).get("1", "X")
    return Layout(
        along,
        across,
        (lo + hi) / 2,
        hi - lo,
        float(f["Z"].median()),
        float(f[along].min()),
        float(f[along].max()),
        "1" if one == along else "2",
    )


def _columns(lay: Layout) -> dict[str, str]:
    """Plaxis columns for the span direction and the transverse one."""
    if lay.span_local == "1":
        return {"N": "N_1", "M": "M_11", "V": "Q_13", "Nt": "N_2", "Mt": "M_22", "Vt": "Q_23"}
    return {"N": "N_2", "M": "M_22", "V": "Q_23", "Nt": "N_1", "Mt": "M_11", "Vt": "Q_13"}


def station_forces(
    frame: pd.DataFrame, lay: Layout, sag: float, stations: np.ndarray | None = None
) -> pd.DataFrame:
    """Beam section forces at stations along the beam (kN, kNm; N compression +, Mv sagging +)."""
    cols = _columns(lay)
    f = frame.drop_duplicates(["X", "Y", "Z"])
    s = f[lay.along].to_numpy(float)
    t = f[lay.across].to_numpy(float) - lay.centre
    B = lay.width
    if stations is None:
        stations = np.unique(np.round(s / 0.05) * 0.05)
    values = {k: f[c].to_numpy(float) for k, c in (("N", cols["N"]), ("M", cols["M"]), ("V", cols["V"]))}
    values |= {"Q12": f["Q_12"].to_numpy(float), "M12": f["M_12"].to_numpy(float)}
    rows = []
    for s0 in stations:
        for w in (WINDOW, 1.2, 1.6):
            m = np.abs(s - s0) <= w + 1e-9
            if m.sum() >= 4 and np.ptp(t[m]) > 0.5 * B:
                break
        if m.sum() < 3 or np.ptp(t[m]) <= 1e-6:
            continue
        A = np.column_stack([np.ones(m.sum()), t[m]])  # no slope along: no extrapolation at faces
        pinv = np.linalg.pinv(A)
        fit = {k: pinv @ v[m] for k, v in values.items()}
        rows.append(
            {
                "s": round(float(s0), 3),
                "N": -fit["N"][0] * B,  # concrete sign
                "Mv": sag * fit["M"][0] * B,
                "Mh": -fit["N"][1] * B**3 / 12,
                "V": fit["V"][0] * B,
                "Vh": fit["Q12"][0] * B,
                "T": fit["M12"][0] * B - fit["V"][1] * B**3 / 12,
            }
        )
    return pd.DataFrame(rows, columns=["s", "N", "Mv", "Mh", "V", "Vh", "T"])


def beam_loads(
    sheets: dict[str, SheetData], lay: Layout, sag: float, qp: bool, supports: list[Support] = ()
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(station forces, transverse node forces) of the ULS (or QP) combinations.

    Nodes inside a support are FE peaks in the connection and are left out of the fits.
    """
    cols = _columns(lay)
    parts, nodes = [], []
    for combo, sheet in sheets.items():
        ctype = combination_type(combo)
        if (ctype is CombinationType.SLS_QP) != qp or sheet.frame.empty:
            continue
        f = sheet.frame.drop_duplicates(["X", "Y", "Z"])
        s, t = f[lay.along].to_numpy(float), f[lay.across].to_numpy(float) - lay.centre
        outside = np.ones(len(f), bool)
        for q in supports:
            outside &= np.hypot(s - q.s, t - q.t) >= q.r - 1e-6
        f = f[outside]
        if f.empty:
            continue
        st = station_forces(f, lay, sag)
        parts.append(st.assign(combination=combo, category=ctype.value))
        nodes.append(
            pd.DataFrame(
                {
                    "combination": combo,
                    "category": ctype.value,
                    "Node": f["Node"].to_numpy() if "Node" in f else None,
                    "s": f[lay.along].to_numpy(float),
                    "t": f[lay.across].to_numpy(float) - lay.centre,
                    "N": -f[cols["Nt"]].to_numpy(float),
                    "M": sag * f[cols["Mt"]].to_numpy(float),
                    "V": f[cols["Vt"]].to_numpy(float),
                }
            )
        )
    empty_st = pd.DataFrame(columns=["s", "N", "Mv", "Mh", "V", "Vh", "T", "combination", "category"])
    empty_nd = pd.DataFrame(columns=["combination", "category", "Node", "s", "t", "N", "M", "V"])
    return (
        pd.concat(parts, ignore_index=True) if parts else empty_st,
        pd.concat(nodes, ignore_index=True) if nodes else empty_nd,
    )


# --- Supports -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Support:
    element: str
    s: float  # m, along the beam
    t: float  # m, across from the centre line
    r: float  # m, radius


def find_supports(lay: Layout, geometry: list[dict], elements: dict[str, Any]) -> list[Support]:
    """Piles and king piles whose heads are inside the beam's plan footprint and reach its level."""
    out = []
    for g in geometry:
        if g.get("kind") != "beam":
            continue
        el = elements.get(g["element"])
        if isinstance(el, PileInput):
            r = el.diameter / 2000
        elif isinstance(el, CombiWallInput):
            r = el.tube_diameter / 2000
        else:
            r = 0.6
        for x, y, ztop, _ in g.get("lines") or []:
            p = {"X": x, "Y": y}
            s, t = p[lay.along], p[lay.across] - lay.centre
            inside = abs(t) <= lay.width / 2 + 1e-6 and lay.start - r <= s <= lay.end + r
            if inside and ztop >= lay.level - 1.0:
                out.append(Support(g["element"], round(s, 3), round(t, 3), r))
    return sorted(out, key=lambda q: q.s)


def moment_stations(s: np.ndarray, supports: list[Support]) -> np.ndarray:
    """Stations outside every support (bending at the face)."""
    keep = np.ones(len(s), bool)
    for q in supports:
        keep &= np.abs(s - q.s) >= q.r - 1e-6
    return keep


def shear_stations(s: np.ndarray, supports: list[Support], dv: float) -> tuple[np.ndarray, bool]:
    """Stations at least ``dv`` (m) beyond every support face; midway stations where none is.

    Returns the mask and whether any span fell back to its midway station.
    """
    keep = np.ones(len(s), bool)
    for q in supports:
        keep &= np.abs(s - q.s) >= q.r + dv - 1e-6
    fallback = False
    edges = sorted({q.s for q in supports})
    for a, b in zip(edges[:-1], edges[1:], strict=False):
        span = (s > a) & (s < b)
        if span.any() and not (keep & span).any():
            mid = (a + b) / 2
            i = np.where(span)[0]
            j = i[np.argmin(np.abs(s[i] - mid))]
            keep |= np.isclose(s, s[j])
            fallback = True
    return keep, fallback


def transverse_nodes(nodes: pd.DataFrame, supports: list[Support], extra: float) -> np.ndarray:
    """Nodes outside every support by ``extra`` (m) beyond its face."""
    keep = np.ones(len(nodes), bool)
    s, t = nodes["s"].to_numpy(float), nodes["t"].to_numpy(float)
    for q in supports:
        keep &= np.hypot(s - q.s, t - q.t) >= q.r + extra - 1e-6
    return keep


# --- Reinforcement --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Face:
    count: int  # bars per layer
    phi: int
    layers: int = 1

    @property
    def area(self) -> float:
        return self.count * self.layers * math.pi * self.phi**2 / 4

    @property
    def label(self) -> str:
        n = self.count * self.layers
        return f"{n}Ø{self.phi}" + (f" ({self.layers} layers)" if self.layers > 1 else "")


@dataclass(frozen=True)
class Cage:
    top: Face
    bottom: Face
    side: Face  # bars on each side face, between the top and bottom layers

    @property
    def area(self) -> float:
        return self.top.area + self.bottom.area + 2 * self.side.area

    @property
    def label(self) -> str:
        return f"Top {self.top.label} · Bottom {self.bottom.label} · Sides 2 × {self.side.label}"

    def to_dict(self, b: float, h: float) -> dict:
        return {
            "label": self.label,
            "top": {
                "count": self.top.count * self.top.layers,
                "phi": self.top.phi,
                "layers": self.top.layers,
            },
            "bottom": {
                "count": self.bottom.count * self.bottom.layers,
                "phi": self.bottom.phi,
                "layers": self.bottom.layers,
            },
            "side": {"count": self.side.count, "phi": self.side.phi},
            "area_mm2": round(self.area),
            "ratio_pct": round(100 * self.area / (b * h), 2),
            "kg_per_m": round(self.area / 1e6 * STEEL_DENSITY, 1),
        }


@dataclass(frozen=True)
class Geometry:
    b: float  # mm
    h: float
    cover: float  # to links
    link: float

    def inner(self, phi: float) -> float:
        """Distance from a face to the centre of a first-layer bar of diameter phi."""
        return self.cover + self.link + phi / 2

    def layer_gap(self, phi: float, dg: float) -> float:
        return phi + max(phi, dg + 5, 20)


def face_candidates(g: Geometry, settings: DesignSettings, width: float) -> list[Face]:
    """Bars along one face of clear width ``width`` (mm), cheapest first, by area."""
    r = settings.reinforcement
    dg = settings.piles.aggregate_size
    out = []
    for phi in [d for d in r.bar_diameters if d >= MIN_BAR]:
        span = width - 2 * (g.cover + g.link) - phi
        if span <= 0:
            continue
        clear_min = max(r.min_clear_spacing, phi, dg + 5, 20)
        n_min = max(2, math.ceil(span / r.max_spacing) + 1)
        n_max = int(span // (clear_min + phi)) + 1
        for n in range(n_min, n_max + 1):
            for layers in range(1, r.max_layers + 1):
                out.append(Face(n, phi, layers))
    return sorted(out, key=lambda f: (f.area, f.layers, -f.phi))


def side_candidates(g: Geometry, settings: DesignSettings, top: Face, bottom: Face) -> list[Face]:
    """Bars on each side face between the top and bottom layers, spaced at most the maximum spacing."""
    r = settings.reinforcement
    height = g.h - g.inner(top.phi) - g.inner(bottom.phi)
    out = []
    for phi in [d for d in r.bar_diameters if d >= MIN_BAR]:
        n_min = max(0, math.ceil(height / r.max_spacing) - 1)
        for n in range(n_min, n_min + 8):
            out.append(Face(n, phi))
    return sorted(out, key=lambda f: (f.area, -f.phi))


def cage_bars(g: Geometry, cage: Cage, dg: float) -> Bars:
    groups = []
    for face, up in ((cage.top, 1), (cage.bottom, -1)):
        half_w = g.b / 2 - g.inner(face.phi)
        for k in range(face.layers):
            v = up * (g.h / 2 - g.inner(face.phi) - k * g.layer_gap(face.phi, dg))
            groups.append(Bars.row(face.count, face.phi, v, half_w))
    if cage.side.count:
        v_top = g.h / 2 - g.inner(cage.top.phi)
        v_bot = -(g.h / 2 - g.inner(cage.bottom.phi))
        half_h = (v_top - v_bot) / 2
        mid = (v_top + v_bot) / 2
        for side in (1, -1):
            u = side * (g.b / 2 - g.inner(cage.side.phi))
            col = Bars.column(cage.side.count, cage.side.phi, u, half_h)
            groups.append(Bars(col.u, col.v + mid, col.area))
    return Bars.join(*groups)


def _laws(beam: BeamInput, settings: DesignSettings) -> tuple[ConcreteLaw, SteelLaw]:
    pf = settings.partial_factors
    fyk = REINFORCEMENT_GRADES[settings.reinforcement.grade]
    return ConcreteLaw(concrete(beam.concrete).fck, pf.gamma_c, pf.alpha_cc), SteelLaw(fyk, pf.gamma_s)


def as_min_beam(fctm: float, fyk: float, b: float, d: float) -> float:
    """9.2.1.1(1): max(0.26·fctm/fyk, 0.0013)·bt·d."""
    return max(0.26 * fctm / fyk, 0.0013) * b * d


# --- Cracking -------------------------------------------------------------------------------------


def face_crack(sec: RectSection, g: Geometry, face: Face, n: float, m: float, e_eff: float, conc) -> dict:
    """Crack width at the tension face of a QP load (N, Mv) with the face's bars ``face``."""
    res = sec.cracked(n, m, e_eff)
    H = g.h
    d = H - g.inner(face.phi)
    outer = np.isclose(np.abs(sec.bars.v), H / 2 - g.inner(face.phi))
    tension_side = -1 if res["sign"] > 0 else 1  # sagging: bottom bars
    in_face = outer & (np.sign(sec.bars.v) == tension_side)
    sigma = float(-res["stress"][in_face].min()) if in_face.any() else 0.0
    e1, e2 = (res["eps_bottom"], res["eps_top"]) if res["sign"] > 0 else (res["eps_top"], res["eps_bottom"])
    k2 = 0.5
    if res["x"] <= 0 and e1 < 0:
        a, c = -e1, -e2
        k2 = (max(a, c) + min(a, c)) / (2 * max(a, c)) if max(a, c) > 0 else 0.5
    spacing = (g.b - 2 * g.inner(face.phi)) / max(face.count - 1, 1)
    out = crack_width(
        sigma,
        h=H,
        d=d,
        x=res["x"],
        b=g.b,
        area=face.area,
        phi=face.phi,
        cover=g.cover + g.link,
        spacing=spacing,
        fctm=conc.fctm,
        ecm=conc.ecm,
        k2=k2,
    )
    out["x_mm"] = round(res["x"])
    return out


def crack_check(sec, g, cage: Cage, qp: pd.DataFrame, e_eff: float, conc) -> dict[str, dict]:
    """Worst QP crack width at the top and bottom faces over the QP stations."""
    worst: dict[str, dict] = {}
    for _, r in qp.iterrows():
        face_name = "bottom" if r["Mv"] >= 0 else "top"
        face = cage.bottom if face_name == "bottom" else cage.top
        c = face_crack(sec, g, face, float(r["N"]), float(r["Mv"]), e_eff, conc)
        if face_name not in worst or c["wk"] > worst[face_name]["wk"]:
            worst[face_name] = {
                **c,
                "combination": r["combination"],
                "s": round(float(r["s"]), 2),
                "N_kN": round(float(r["N"]), 1),
                "M_kNm": round(float(r["Mv"]), 1),
            }
    return worst


def restraint_check(beam: BeamInput, settings: DesignSettings, g: Geometry, cage: Cage, conc) -> dict:
    cr = settings.cracking
    R = beam.restraint_factor
    auto = R is None
    if auto:
        R = restraint_factor(beam.joint_spacing, g.h / 1000)
    common = {
        "restraint": R * cr.creep_factor,
        "t1": cr.early_age_drop,
        "t2": cr.seasonal_drop,
        "alpha": cr.thermal_expansion * 1e-6,
        "eps_ca": autogenous_shrinkage(conc.fck),
        "fctm": conc.fctm,
        "ecm": conc.ecm,
        "cover": g.cover + g.link,
    }
    faces = {}
    for name, face, width, member in (
        ("top", cage.top, g.b, g.h),
        ("bottom", cage.bottom, g.b, g.h),
        ("side", cage.side, None, g.b),
    ):
        if name == "side":
            if face.count == 0:
                height = g.h - 2 * g.inner(cage.top.phi)
                faces[name] = {"wk": math.inf, "note": "no side bars"} if height > 300 else {"wk": 0.0}
                continue
            width = g.h - g.inner(cage.top.phi) - g.inner(cage.bottom.phi)
            spacing = width / (face.count + 1)
        else:
            spacing = (g.b - 2 * g.inner(face.phi)) / max(face.count - 1, 1)
        hc = min(2.5 * g.inner(face.phi), member / 2)
        faces[name] = restraint_crack(
            **common,
            area=face.count * math.pi * face.phi**2 / 4,
            b=width,
            hc=hc,
            phi=face.phi,
            spacing=spacing,
            member=member,
        )
    return {
        "length_m": beam.joint_spacing,
        "R": round(R, 3),
        "R_from": "length / depth, ACI 207.2R" if auto else "input",
        "K1": cr.creep_factor,
        "T1": cr.early_age_drop,
        "T2": cr.seasonal_drop,
        "faces": faces,
    }


# --- Links ----------------------------------------------------------------------------------------


def _vrdc(fck, gc, bw, d, rho, sigma_cp) -> np.ndarray:
    k = min(1 + math.sqrt(200 / d), 2.0)
    v_min = 0.035 * k**1.5 * math.sqrt(fck)
    v = np.maximum(0.18 / gc * k * (100 * min(rho, 0.02) * fck) ** (1 / 3), v_min) + 0.15 * sigma_cp
    return np.maximum(v, 0.0) * bw * d / 1e3


def link_design(
    beam: BeamInput,
    settings: DesignSettings,
    g: Geometry,
    cage: Cage,
    shear: pd.DataFrame,
    trans: pd.DataFrame,
    trans_bars: dict | None,
) -> dict:
    """One link arrangement for the whole beam: shear with torsion, horizontal and transverse shear."""
    pf = settings.partial_factors
    conc = concrete(beam.concrete)
    fck = conc.fck
    fcd = pf.alpha_cc * fck / pf.gamma_c
    fyk = REINFORCEMENT_GRADES[settings.reinforcement.grade]
    fywd = fyk / pf.gamma_s
    b, h = g.b, g.h
    ac = b * h
    nu1 = 0.6 * (1 - fck / 250)
    d = h - max(g.inner(cage.top.phi), g.inner(cage.bottom.phi))
    z = 0.9 * d
    rho = min(cage.top.area, cage.bottom.area) / (b * d)
    notes = []

    # Torsion section (6.3.2): thin-walled equivalent.
    tef = max(ac / (2 * (b + h)), 2 * g.inner(max(cage.top.phi, cage.bottom.phi)))
    ak = (b - tef) * (h - tef)
    uk = 2 * ((b - tef) + (h - tef))

    V = np.abs(shear["V"].to_numpy(float)) if len(shear) else np.zeros(0)
    T = np.abs(shear["T"].to_numpy(float)) if len(shear) else np.zeros(0)
    Vh = np.abs(shear["Vh"].to_numpy(float)) if len(shear) else np.zeros(0)
    N = shear["N"].to_numpy(float) if len(shear) else np.zeros(0)
    sigma_cp = np.minimum(N * 1e3 / ac, 0.2 * fcd)
    vrdc = np.where(N < 0, 0.0, _vrdc(fck, pf.gamma_c, b, d, rho, sigma_cp))
    fctd = pf.alpha_cc * 0.7 * conc.fctm / pf.gamma_c
    trdc = fctd * 2 * ak * tef / 1e6  # kNm
    concrete_only = V / np.maximum(vrdc, 1e-9) + T / trdc <= 1.0

    def vmax(cot):
        return b * z * nu1 * fcd / (cot + 1 / cot) / 1e3

    def tmax(cot):
        return 2 * nu1 * fcd * ak * tef / (cot + 1 / cot) / 1e6

    cot = np.full(len(V), 2.5)
    ok = V / vmax(2.5) + T / tmax(2.5) <= 1
    for c in np.linspace(2.5, 1.0, 31):
        m = ~ok & (V / vmax(c) + T / tmax(c) <= 1)
        cot[m] = c
        ok |= m
    crushed = ~ok
    cot[crushed] = 1.0
    # Per mm of beam: all vertical legs for V, each outer leg for T.
    asw_v = np.where(concrete_only, 0.0, V * 1e3 / (z * fywd * cot))
    at_leg = np.where(concrete_only, 0.0, T * 1e6 / (2 * ak * fywd * cot))
    asl_t = float((T * 1e6 * uk * cot / (2 * ak * fywd)).max()) if len(T) else 0.0  # 6.3.2(3)

    # Horizontal shear across the width: link top and bottom legs (2), depth b, width h.
    dh = b - g.inner(cage.side.phi if cage.side.count else cage.top.phi)
    rho_h = (cage.side.area + math.pi * cage.top.phi**2 / 4 + math.pi * cage.bottom.phi**2 / 4) / (h * dh)
    vrdc_h = np.where(N < 0, 0.0, _vrdc(fck, pf.gamma_c, h, dh, rho_h, sigma_cp))
    ash = np.where(Vh > vrdc_h, Vh * 1e3 / (0.9 * dh * fywd * 2.5), 0.0) / 2  # per horizontal leg
    ash += at_leg  # torsion acts on every side of the outer link

    # Transverse shear per metre (vertical legs spread over the width).
    ast_m2 = np.zeros(0)
    if len(trans) and trans_bars:
        dt = trans_bars["d_mm"]
        rho_t = trans_bars["as_min_face"] / (1000 * dt)
        nt = trans["N"].to_numpy(float)
        sig_t = np.minimum(nt * 1e3 / (1000 * h), 0.2 * fcd)
        vt = np.abs(trans["V"].to_numpy(float))
        vrdc_t = np.where(nt < 0, 0.0, _vrdc(fck, pf.gamma_c, 1000, dt, rho_t, sig_t))
        # mm² of vertical leg per mm along the beam per mm across
        ast_m2 = np.where(vt > vrdc_t, vt * 1e3 / (0.9 * dt * fywd * 2.5) / 1000, 0.0)

    rho_w_min = 0.08 * math.sqrt(fck) / fyk
    s_max = min(0.75 * d, 600.0)
    if len(T) and (T > 0).any() and not concrete_only.all():
        s_max = min(s_max, uk / 8, b, h)
    leg_gap_max = min(0.75 * d, 600.0)
    inner_w = b - 2 * (g.cover + g.link / 2)
    legs_min = max(2, math.ceil(inner_w / leg_gap_max) + 1)
    step = settings.reinforcement.spacing_step
    phi = beam.link_diameter
    a_leg = math.pi * phi * phi / 4
    best = None
    for legs in range(legs_min, legs_min + 8):
        gap = inner_w / (legs - 1)
        need_v = max(
            (asw_v.max() / legs + at_leg.max()) if len(asw_v) else 0.0,
            (ast_m2.max() * gap) if len(ast_m2) else 0.0,
            rho_w_min * b / legs,
        )
        need_h = ash.max() if len(ash) else 0.0
        need = max(need_v, need_h)
        s = s_max if need <= 0 else min(s_max, a_leg / need)
        s = math.floor(s / step + 1e-9) * step
        if s < MIN_LINK_SPACING:
            continue
        per_set = 2 * (b + h - 4 * g.cover) + (legs - 2) * (h - 2 * g.cover)  # mm of bar
        kg = per_set / s * a_leg / 1e6 * STEEL_DENSITY  # kg per m of beam
        if best is None or kg < best["kg"] - 1e-9:
            best = {"legs": legs, "spacing_mm": s, "kg": kg, "kg_per_m": round(kg, 1), "leg_gap_mm": gap}
    if best is None:
        notes.append(f"Ø{phi:g} links would be closer than {MIN_LINK_SPACING:g} mm: use larger links.")
        return {"passed": False, "utilisation": None, "notes": notes}
    legs, s = best["legs"], best["spacing_mm"]
    vrds = legs * a_leg / s * z * fywd * cot / 1e3
    trds_leg = a_leg / s * 2 * ak * fywd * cot / 1e6  # torsion if one outer leg took it all
    util_v = np.where(concrete_only, V / np.maximum(vrdc, 1e-9) + T / trdc, V / vrds + T / trds_leg)
    util_v = np.maximum(util_v, V / vmax(cot) + T / tmax(cot))
    util_v = np.where(crushed, np.inf, util_v)
    u = float(util_v.max()) if len(util_v) else 0.0
    gi = int(np.argmax(util_v)) if len(util_v) else None
    if crushed.any():
        notes.append(
            "The concrete strut crushes under shear and torsion: a larger section or concrete is needed."
        )
    out = {
        "method": "EN 1992-1-1 6.2 and 6.3.2, one link arrangement for the whole beam",
        "link": {
            "phi": phi,
            "legs": legs,
            "spacing_mm": s,
            "label": f"Ø{phi:g} links, {legs} legs @ {s:g} mm",
            "kg_per_m": best["kg_per_m"],
        },
        "utilisation": round(u, 3) if math.isfinite(u) else None,
        "passed": bool(math.isfinite(u) and u <= 1 + 1e-6) and not crushed.any(),
        "max_spacing_mm": round(s_max),
        "torsion_long_steel_mm2": round(asl_t),
        "notes": notes,
    }
    if gi is not None:
        r = shear.iloc[gi]
        out["governing"] = {
            "combination": r["combination"],
            "s": round(float(r["s"]), 2),
            "V_kN": round(float(V[gi]), 1),
            "T_kNm": round(float(T[gi]), 1),
            "N_kN": round(float(N[gi]), 1),
            "VRd_c_kN": round(float(vrdc[gi]), 1),
            "VRd_max_kN": round(float(vmax(cot[gi])), 1),
            "TRd_max_kNm": round(float(tmax(cot[gi])), 1),
            "cot_theta": round(float(cot[gi]), 2),
        }
    if len(Vh):
        j = int(np.argmax(Vh))
        out["horizontal"] = {"V_kN": round(float(Vh[j]), 1), "VRd_c_kN": round(float(vrdc_h[j]), 1)}
    if len(ast_m2):
        out["transverse_shear_needs_links"] = bool((ast_m2 > 0).any())
    return out


# --- Transverse bars per metre --------------------------------------------------------------------


def transverse_design(beam, settings, g: Geometry, cage: Cage, uls: pd.DataFrame, qp: pd.DataFrame) -> dict:
    """Top and bottom transverse bars per metre from the transverse moments (per metre) at each node."""
    r = settings.reinforcement
    conc = concrete(beam.concrete)
    fyk = REINFORCEMENT_GRADES[r.grade]
    cl, sl = _laws(beam, settings)
    e_eff = conc.ecm / (1 + settings.cracking.creep_coefficient)
    h = g.h
    long_phi = max(cage.top.phi, cage.bottom.phi)
    options = []
    for phi in [d for d in r.bar_diameters if d >= MIN_BAR]:
        s = r.max_spacing
        while s >= max(100.0, phi + r.min_clear_spacing) - 1e-9:
            options.append((1000 * math.pi * phi * phi / 4 / s, phi, s))
            s -= r.spacing_step
    options.sort()

    def d_of(phi):
        return h - (g.cover + g.link + long_phi + phi / 2)

    def section(top, bot):
        bars = []
        for (_, phi, s), up in ((top, 1), (bot, -1)):
            n = max(2, round(1000 / s))
            v = up * (h / 2 - (g.cover + g.link + long_phi + phi / 2))
            bars.append(
                Bars(
                    np.linspace(-500 + s / 2, 500 - s / 2, n),
                    np.full(n, v),
                    np.full(n, math.pi * phi**2 / 4 * 1000 / s / n),
                )
            )
        return RectSection(
            1000.0, h, Bars.join(*bars), cl, sl, strips=120, deduct=settings.partial_factors.deduct_bar_area
        )

    as_min = as_min_beam(conc.fctm, fyk, 1000, d_of(16))
    ti = bi = next(i for i, o in enumerate(options) if o[0] >= as_min)
    limits = {"top": beam.crack_width_limit, "bottom": beam.crack_width_limit_bottom}
    status = "ok"
    for _ in range(200):
        sec = section(options[ti], options[bi])
        u = sec.utilisation(uls["N"].to_numpy(float), uls["M"].to_numpy(float)) if len(uls) else np.zeros(0)
        worst = {}
        for _, q in qp.iterrows():
            face = "bottom" if q["M"] >= 0 else "top"
            o = options[bi] if face == "bottom" else options[ti]
            res = sec.cracked(float(q["N"]), float(q["M"]), e_eff)
            sig = float(-res["stress"][(np.sign(sec.bars.v) == (-1 if face == "bottom" else 1))].min())
            c = crack_width(
                sig,
                h=h,
                d=d_of(o[1]),
                x=res["x"],
                b=1000,
                area=o[0],
                phi=o[1],
                cover=g.cover + g.link + long_phi,
                spacing=o[2],
                fctm=conc.fctm,
                ecm=conc.ecm,
            )
            if face not in worst or c["wk"] > worst[face]["wk"]:
                worst[face] = {**c, "combination": q["combination"], "M_kNm_per_m": round(float(q["M"]), 1)}
        fail = None
        if len(u) and u.max() > 1:
            j = int(np.argmax(u))
            fail = "bottom" if uls["M"].iloc[j] >= 0 else "top"
        else:
            for face in ("top", "bottom"):
                if face in worst and worst[face]["wk"] > limits[face] + 1e-9:
                    fail = face
                    break
        if fail is None:
            break
        if fail == "top":
            if ti + 1 >= len(options):
                status = "no bars"
                break
            ti += 1
        else:
            if bi + 1 >= len(options):
                status = "no bars"
                break
            bi += 1
    u_max = float(u.max()) if len(u) else 0.0
    j = int(np.argmax(u)) if len(u) else None

    def lab(o):
        return {"phi": o[1], "spacing_mm": o[2], "as_mm2_per_m": round(o[0]), "label": f"Ø{o[1]} @ {o[2]:g}"}

    cracks = {f: {**w, "limit": limits[f], "passed": w["wk"] <= limits[f] + 1e-9} for f, w in worst.items()}
    out = {
        "top": lab(options[ti]),
        "bottom": lab(options[bi]),
        "utilisation": round(u_max, 3) if math.isfinite(u_max) else None,
        "cracks": cracks,
        "passed": status == "ok" and u_max <= 1 + 1e-6 and all(c["passed"] for c in cracks.values()),
        "d_mm": round(d_of(options[min(ti, bi)][1])),
        "as_min_face": min(options[ti][0], options[bi][0]),
        "kg_per_m": round((options[ti][0] + options[bi][0]) / 1e6 * (g.b / 1000) * STEEL_DENSITY, 1),
    }
    if j is not None:
        q = uls.iloc[j]
        out["governing"] = {
            "combination": q["combination"],
            "s": round(float(q["s"]), 2),
            "t": round(float(q["t"]), 2),
            "N_kN_per_m": round(float(q["N"]), 1),
            "M_kNm_per_m": round(float(q["M"]), 1),
        }
    return out


# --- Beam design ----------------------------------------------------------------------------------


def design_beam(
    name: str,
    beam: BeamInput,
    settings: DesignSettings,
    sheets: dict[str, SheetData],
    geometry: list[dict],
    elements: dict[str, Any],
    axes: dict[str, str] | None,
) -> dict[str, Any]:
    beam = with_project_grades(beam, settings.materials, settings.durability)
    lay = layout(sheets, axes)
    sag = 1.0 if settings.plate_positive_moment == "sagging" else -1.0
    conc = concrete(beam.concrete)
    fyk = REINFORCEMENT_GRADES[settings.reinforcement.grade]
    b = beam.width if beam.width is not None else round(lay.width * 1000)
    g = Geometry(float(b), beam.depth, beam.cover, beam.link_diameter)
    dg = settings.piles.aggregate_size
    cl, sl = _laws(beam, settings)
    e_eff = conc.ecm / (1 + settings.cracking.creep_coefficient)
    supports = find_supports(lay, geometry, elements)
    uls, t_uls = beam_loads(sheets, lay, sag, qp=False, supports=supports)
    qp, t_qp = beam_loads(sheets, lay, sag, qp=True, supports=supports)
    notes = [
        f"Spans along global {lay.along} ({lay.start:.2f} to {lay.end:.2f} m, {lay.end - lay.start:.1f} m); "
        f"local {lay.span_local} is along the beam"
        + (" (from the directions check)." if axes else " (assumed: the directions check had no answer)."),
        f"Width in the model {lay.width * 1000:.0f} mm"
        + (f", designed as {b:.0f} mm." if beam.width is not None else ", used as the beam width."),
        "Positive plate moments taken as " + settings.plate_positive_moment + " (Design settings).",
    ]
    if supports:
        names = sorted({q.element for q in supports})
        notes.append(
            f"{len(supports)} supports inside the beam ({', '.join(names)}): bending at their faces, "
            f"shear at {settings.shear_check_distance} from them; results inside them are left out."
        )
    base = {
        "element": name,
        "kind": beam.kind,
        "width_mm": g.b,
        "depth_mm": g.h,
        "cover_mm": g.cover,
        "concrete": beam.concrete,
        "along": lay.along,
        "centre_m": round(lay.centre, 3),
        "level_m": round(lay.level, 2),
        "start_m": lay.start,
        "end_m": lay.end,
        "supports": [{"element": q.element, "s": q.s, "t": q.t, "r": q.r} for q in supports],
        "notes": notes,
    }
    if uls.empty:
        notes.append("No ULS results.")
        return {**base, "utilisation": None, "passed": False}

    s_all = uls["s"].to_numpy(float)
    mom = uls[moment_stations(s_all, supports)]
    d_est = (g.h - g.inner(25)) / 1000
    dv = d_est * (2 if settings.shear_check_distance == "2d" else 1)
    mask, fallback = shear_stations(s_all, supports, dv)
    shr = uls[mask]
    if fallback:
        notes.append(
            "Supports are closer than 2 × (radius + d): shear is checked at the station midway between them."
        )
    qp_m = qp[moment_stations(qp["s"].to_numpy(float), supports)] if len(qp) else qp

    tops = face_candidates(g, settings, g.b)
    if not tops:
        notes.append("No bars fit the beam width with the chosen spacing limits.")
        return {**base, "utilisation": None, "passed": False}
    as_min = as_min_beam(conc.fctm, fyk, g.b, g.h - g.inner(25))
    ti = bi = next((i for i, f in enumerate(tops) if f.area >= as_min), len(tops) - 1)
    sides = side_candidates(g, settings, tops[ti], tops[bi])
    si = 0
    limits = {"top": beam.crack_width_limit, "bottom": beam.crack_width_limit_bottom}
    limits["side"] = min(limits.values())
    status = "ok"
    n = mom["N"].to_numpy(float)
    mv = mom["Mv"].to_numpy(float)
    mh = mom["Mh"].to_numpy(float)
    for _ in range(400):
        cage = Cage(tops[ti], tops[bi], sides[si])
        sec = RectSection(
            g.b, g.h, cage_bars(g, cage, dg), cl, sl, deduct=settings.partial_factors.deduct_bar_area
        )
        u = sec.utilisation(n, mv, mh)
        cracks = crack_check(sec, g, cage, qp_m, e_eff, conc)
        restr = restraint_check(beam, settings, g, cage, conc)
        grow = None
        if u.max() > 1:
            j = int(np.argmax(u))
            rv = sec.m_rd("v", np.array([1 if mv[j] >= 0 else -1]), n[j : j + 1])[0]
            rh = sec.m_rd("h", np.array([1 if mh[j] >= 0 else -1]), n[j : j + 1])[0]
            tv = abs(mv[j]) / rv if rv > 0 else math.inf
            th = abs(mh[j]) / rh if rh > 0 else math.inf
            if th > tv:
                grow = "side"
            else:
                grow = "bottom" if mv[j] >= 0 else "top"
            if n[j] > 0 and abs(mv[j]) < 1e-6 and abs(mh[j]) < 1e-6:
                grow = "top"
        if grow is None:
            for face in ("top", "bottom"):
                if face in cracks and cracks[face]["wk"] > limits[face] + 1e-9:
                    grow = face
                    break
        if grow is None:
            for face in ("top", "bottom", "side"):
                if restr["faces"][face]["wk"] > limits[face] + 1e-9:
                    grow = face
                    break
        if grow is None:
            break
        if grow == "side":
            if si + 1 >= len(sides):
                status = "side bars exhausted"
                break
            si += 1
        elif grow == "top":
            if ti + 1 >= len(tops):
                status = "top bars exhausted"
                break
            ti += 1
            sides = side_candidates(g, settings, tops[ti], tops[bi])
            si = min(si, len(sides) - 1)
        else:
            if bi + 1 >= len(tops):
                status = "bottom bars exhausted"
                break
            bi += 1
            sides = side_candidates(g, settings, tops[ti], tops[bi])
            si = min(si, len(sides) - 1)
    if status != "ok":
        notes.append(f"No cage within the bar sizes and spacing limits passes every check ({status}).")
    u_max = float(u.max())
    j = int(np.argmax(u))
    gov = mom.iloc[j]
    rv = sec.m_rd("v", np.array([1 if mv[j] >= 0 else -1]), n[j : j + 1])[0]
    rh = sec.m_rd("h", np.array([1 if mh[j] >= 0 else -1]), n[j : j + 1])[0]
    n_rd = (sec.area_concrete * cl.fcd + sec.bars.total * sl.fyd) / 1e3
    bending = {
        "method": "EN 1992-1-1 5.8.9(4), N with vertical and horizontal bending",
        "utilisation": round(u_max, 3) if math.isfinite(u_max) else None,
        "governing": {
            "combination": gov["combination"],
            "s": round(float(gov["s"]), 2),
            "N_kN": round(float(n[j]), 1),
            "Mv_kNm": round(float(mv[j]), 1),
            "Mh_kNm": round(float(mh[j]), 1),
            "MRd_v_kNm": round(float(rv), 1),
            "MRd_h_kNm": round(float(rh), 1),
            "a": round(float(np.interp(n[j] / n_rd, [0.1, 0.7, 1.0], [1.0, 1.5, 2.0])), 2),
        },
        "extremes": {
            k: {"max": round(float(mom[k].max()), 1), "min": round(float(mom[k].min()), 1)}
            for k in ("N", "Mv", "Mh", "V", "Vh", "T")
        },
        "passed": bool(u_max <= 1 + 1e-6),
    }
    crack_out = {
        f: {**c, "limit": limits[f], "passed": c["wk"] <= limits[f] + 1e-9} for f, c in cracks.items()
    }
    for f, c in restr["faces"].items():
        c["limit"] = limits[f]
        c["passed"] = c["wk"] <= limits[f] + 1e-9

    # Transverse bars and links.
    t_keep = transverse_nodes(t_uls, supports, 0.0)
    trans = transverse_design(
        beam,
        settings,
        g,
        cage,
        t_uls[t_keep],
        t_qp[transverse_nodes(t_qp, supports, 0.0)] if len(t_qp) else t_qp,
    )
    t_shear = t_uls[transverse_nodes(t_uls, supports, dv)]
    links = link_design(beam, settings, g, cage, shr, t_shear, trans)
    if links.get("torsion_long_steel_mm2", 0) > 0:
        side_share = links["torsion_long_steel_mm2"] * g.h / (g.b + g.h)
        if 2 * cage.side.area < side_share:
            notes.append(
                f"Torsion needs about {side_share:.0f} mm² of longitudinal steel on the side faces; "
                f"the side bars "
                f"give {2 * cage.side.area:.0f} mm²."
            )

    # Steel and utilisation.
    length = lay.end - lay.start
    kg_m = (
        cage.to_dict(g.b, g.h)["kg_per_m"] + (links.get("link") or {}).get("kg_per_m", 0) + trans["kg_per_m"]
    )
    vol = g.b * g.h / 1e6
    steel = {
        "longitudinal_kg_per_m": cage.to_dict(g.b, g.h)["kg_per_m"],
        "links_kg_per_m": (links.get("link") or {}).get("kg_per_m"),
        "transverse_kg_per_m": trans["kg_per_m"],
        "kg_per_m": round(kg_m, 1),
        "kg_per_m3": round(kg_m / vol),
        "length_m": round(length, 2),
        "total_kg": round(kg_m * length),
        "element_total_t": round(kg_m * length / 1000, 2),
    }
    checks = [bending["utilisation"], links.get("utilisation"), trans.get("utilisation")]
    checks += [c["wk"] / c["limit"] for c in crack_out.values()]
    checks += [c["wk"] / c["limit"] for c in restr["faces"].values() if math.isfinite(c["wk"])]
    bollard = check_bollard(beam.bollard, beam.concrete, settings) if beam.bollard is not None else None
    if bollard is not None:
        checks.append(bollard["utilisation"])
    finite = [c for c in checks if c is not None]
    passed = (
        bending["passed"]
        and bool(links.get("passed"))
        and trans["passed"]
        and all(c["passed"] for c in crack_out.values())
        and all(c["passed"] for c in restr["faces"].values())
        and status == "ok"
        and (bollard is None or bollard["passed"])
    )
    # Links and restraint are one arrangement for the whole beam, so they colour every band.
    uniform = max([c for c in checks[1:] if c is not None and math.isfinite(c)], default=0.0)
    bands = beam_bands(lay, mom.assign(u=np.maximum(u, uniform)))
    sets = beam_sets(cage.label, mom, u, qp_m)
    return {
        **base,
        "cage": {
            **cage.to_dict(g.b, g.h),
            "bars": [
                [round(float(u), 1), round(float(v), 1), round(math.sqrt(4 * a / math.pi))]
                for u, v, a in zip(sec.bars.u, sec.bars.v, sec.bars.area, strict=True)
            ],
            "link_diameter_mm": g.link,
        },
        "utilisation": round(max(finite), 3) if finite else None,
        "passed": bool(passed),
        "bending": bending,
        "cracks": crack_out,
        "restraint": restr,
        "shear": links,
        "transverse": trans,
        "bollard": bollard,
        "steel": steel,
        "bands": bands,
        "profile": _profile(mom, u),
        "governing_sets": sets,
    }


def _profile(mom: pd.DataFrame, u: np.ndarray) -> list[dict]:
    f = mom.assign(u=u).groupby("s").agg(u=("u", "max"), Mv=("Mv", "max"), Mv_min=("Mv", "min"))
    return [
        {
            "s": float(s),
            "u": round(float(r.u), 3),
            "Mv_max": round(float(r.Mv), 1),
            "Mv_min": round(float(r.Mv_min), 1),
        }
        for s, r in f.iterrows()
    ]


def beam_bands(lay: Layout, frame: pd.DataFrame) -> list[list[float]]:
    """[x, y, z, utilisation] per 0.5 m band along the beam centre line."""
    k = np.floor((frame["s"].to_numpy(float) - lay.start) / BAND).astype(int)
    out = []
    for i, u in frame.assign(k=k).groupby("k")["u"].max().items():
        s = lay.start + (i + 0.5) * BAND
        x, y = (lay.centre, s) if lay.along == "Y" else (s, lay.centre)
        out.append([round(x, 3), round(y, 3), round(lay.level, 2), round(float(u), 3)])
    return out


def beam_sets(label: str, mom: pd.DataFrame, u: np.ndarray, qp: pd.DataFrame) -> list[dict]:
    """Seven ULS and seven QP sets for AdSec: M3 = vertical bending (sagging +), M2 = horizontal."""

    def frame(f: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "combination": f["combination"].to_numpy(),
                "Node": None,
                "Z": f["s"].to_numpy(float),
                "N": f["N"].to_numpy(float),
                "M_2": f["Mh"].to_numpy(float),
                "M_3": f["Mv"].to_numpy(float),
            }
        )

    return [
        {
            "top": float(mom["s"].min()),
            "bottom": float(mom["s"].max()),
            "cage": label,
            "uls": pick_sets(frame(mom), u, "most utilised"),
            "qp": pick_sets(frame(qp), None, "largest resultant M") if len(qp) else [],
        }
    ]
