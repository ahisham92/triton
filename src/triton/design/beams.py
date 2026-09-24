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

from ..axes import sag_factor
from ..elements import CombinationType, ElementType, combination_type
from ..importer import SheetData
from ..materials import REINFORCEMENT_GRADES, STEEL_DENSITY, concrete
from ..project import BeamCage, BeamInput, CombiWallInput, DesignSettings, PileInput, with_project_grades
from . import ductility
from .bollard import check_bollard
from .circular import ConcreteLaw, SteelLaw
from .crack import autogenous_shrinkage, crack_width, restraint_crack, restraint_factor
from .governing import crack_terms, pick_sets
from .rect import Bars, RectSection
from .tension import beam_tension
from .truss import check_truss, spacing_from_supports

BEAM_TYPES = (ElementType.FRONT_BEAM, ElementType.REAR_BEAM, ElementType.TRANSVERSE_BEAM)
WINDOW = 0.8  # m, half-length of the fit along the beam (shorter fits pick up the nodal noise of the shears)
MIN_BAR = 12
MIN_LINK_SPACING = 75.0
BAND = 0.5  # m, heat-map bands along the beam
PEAK = 0.4  # m, half-length along the beam over which the peak nodal values are taken


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
    frame: pd.DataFrame,
    lay: Layout,
    sag: float,
    stations: np.ndarray | None = None,
    peak_width: float | None = None,
) -> pd.DataFrame:
    """Beam section forces at stations along the beam (kN, kNm; N compression +, Mv sagging +).

    With ``peak_width`` (m), Mv and V are the peak nodal values per metre within ±PEAK of the
    station times that width, as hand calculations take them: one row with the largest sagging
    moment and one with the largest hogging moment. N, Mh, Vh and T stay integrated.
    """
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
        row = {
            "s": round(float(s0), 3),
            "N": -fit["N"][0] * B,  # concrete sign
            "Mv": sag * fit["M"][0] * B,
            "Mh": -fit["N"][1] * B**3 / 12,
            "V": fit["V"][0] * B,
            "Vh": fit["Q12"][0] * B,
            "T": fit["M12"][0] * B - fit["V"][1] * B**3 / 12,
        }
        if peak_width is None:
            rows.append(row)
            continue
        near = np.abs(s - s0) <= PEAK + 1e-9
        if not near.any():
            near = m
        mv = sag * values["M"][near]
        vv = values["V"][near]
        row["V"] = float(vv[np.argmax(np.abs(vv))]) * peak_width
        rows.append({**row, "Mv": float(mv.max()) * peak_width})
        if mv.min() < mv.max():
            rows.append({**row, "Mv": float(mv.min()) * peak_width})
    return pd.DataFrame(rows, columns=["s", "N", "Mv", "Mh", "V", "Vh", "T"])


def beam_loads(
    sheets: dict[str, SheetData],
    lay: Layout,
    sag: float,
    qp: bool,
    supports: list[Support] = (),
    peak_width: float | None = None,
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
        st = station_forces(f, lay, sag, peak_width=peak_width)
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


def torsion_shares(g: Geometry, cage: Cage, asl: float) -> dict[str, float]:
    """The torsion longitudinal steel (6.3.2(3)) each face gives up, by the face's share of the
    perimeter; without side bars the top and bottom bars take it all."""
    per = 2 * (g.b + g.h)
    side = asl * g.h / per if cage.side.count else 0.0
    rest = (asl - 2 * side) / 2
    return {"top": rest, "bottom": rest, "side": side}


def cage_bars(g: Geometry, cage: Cage, dg: float, torsion: float = 0.0) -> Bars:
    """The cage's bars; with ``torsion`` (Asl, mm²) each face's bars keep only what torsion leaves."""
    share = torsion_shares(g, cage, torsion)

    def left(face: Face, name: str) -> float:
        return max(0.0, 1 - share[name] / face.area) if face.area else 1.0

    groups = []
    for face, up, name in ((cage.top, 1, "top"), (cage.bottom, -1, "bottom")):
        half_w = g.b / 2 - g.inner(face.phi)
        for k in range(face.layers):
            v = up * (g.h / 2 - g.inner(face.phi) - k * g.layer_gap(face.phi, dg))
            row = Bars.row(face.count, face.phi, v, half_w)
            groups.append(Bars(row.u, row.v, row.area * left(face, name)))
    if cage.side.count:
        v_top = g.h / 2 - g.inner(cage.top.phi)
        v_bot = -(g.h / 2 - g.inner(cage.bottom.phi))
        half_h = (v_top - v_bot) / 2
        mid = (v_top + v_bot) / 2
        for side in (1, -1):
            u = side * (g.b / 2 - g.inner(cage.side.phi))
            col = Bars.column(cage.side.count, cage.side.phi, u, half_h)
            groups.append(Bars(col.u, col.v + mid, col.area * left(cage.side, "side")))
    return Bars.join(*groups)


def adsec_lines(g: Geometry, cage: Cage, dg: float) -> list[dict]:
    """The cage as bar lines for AdSec: {phi, count, a: [u, v], b: [u, v]} in mm, u across, v up."""
    out = []
    for face, up in ((cage.top, 1), (cage.bottom, -1)):
        half_w = g.b / 2 - g.inner(face.phi)
        for k in range(face.layers):
            v = up * (g.h / 2 - g.inner(face.phi) - k * g.layer_gap(face.phi, dg))
            ends = (-half_w, half_w) if face.count > 1 else (0.0, 0.0)
            out.append({"phi": face.phi, "count": face.count, "a": [ends[0], v], "b": [ends[1], v]})
    if cage.side.count:
        col = Bars.column(cage.side.count, cage.side.phi, 0.0, 1.0)
        v_top = g.h / 2 - g.inner(cage.top.phi)
        v_bot = -(g.h / 2 - g.inner(cage.bottom.phi))
        half_h, mid = (v_top - v_bot) / 2, (v_top + v_bot) / 2
        lo, hi = float(col.v.min()) * half_h + mid, float(col.v.max()) * half_h + mid
        for side in (-1, 1):
            u = side * (g.b / 2 - g.inner(cage.side.phi))
            out.append({"phi": cage.side.phi, "count": cage.side.count, "a": [u, hi], "b": [u, lo]})
    return [{**d, "a": [round(x, 1) for x in d["a"]], "b": [round(x, 1) for x in d["b"]]} for d in out]


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


def crack_candidates(qp: pd.DataFrame, z: float, k: int = 8) -> pd.DataFrame:
    """The QP rows that can give the widest crack at each face: the largest moments, the largest
    tensions and the largest steel force estimate |M|/z − N/2 (z in m), per face. The crack width
    grows with each, so the worst row is among them."""
    if len(qp) <= 4 * k:
        return qp
    keep = set()
    for side in (qp["Mv"] >= 0, qp["Mv"] < 0):
        f = qp[side]
        if f.empty:
            continue
        m = f["Mv"].abs()
        for score in (m, -f["N"], m / z - f["N"] / 2):
            keep.update(score.nlargest(k).index)
    return qp.loc[sorted(keep)]


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
            # One layer, or two (the second under the first, a clear gap of max(25 mm, Ø) between).
            for layers in range(1, r.max_layers + 1):
                options.append((layers * 1000 * math.pi * phi * phi / 4 / s, phi, s, layers))
            s -= r.spacing_step
    # Least steel first; a second layer is placed only where one layer is not enough.
    options.sort(key=lambda o: (o[0] * (1 + 0.25 * (o[3] - 1)), o[3]))

    def pitch(phi):
        return phi + max(25.0, phi)

    def d_of(phi, layers=1):
        return h - (g.cover + g.link + long_phi + phi / 2) - (layers - 1) * pitch(phi) / 2

    def section(top, bot):
        bars = []
        for (_, phi, s, layers), up in ((top, 1), (bot, -1)):
            n = max(2, round(1000 / s))
            for k in range(layers):
                v = up * (h / 2 - (g.cover + g.link + long_phi + phi / 2) - k * pitch(phi))
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
    ti = bi = next(i for i, o in enumerate(options) if o[0] >= as_min and o[3] == 1)
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
                d=d_of(o[1], o[3]),
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
        text = f"Ø{o[1]} @ {o[2]:g}" + (f" in {o[3]} layers" if o[3] > 1 else "")
        return {"phi": o[1], "spacing_mm": o[2], "layers": o[3], "as_mm2_per_m": round(o[0]), "label": text}

    cracks = {f: {**w, "limit": limits[f], "passed": w["wk"] <= limits[f] + 1e-9} for f, w in worst.items()}
    out = {
        "top": lab(options[ti]),
        "bottom": lab(options[bi]),
        "utilisation": round(u_max, 3) if math.isfinite(u_max) else None,
        "cracks": cracks,
        "passed": status == "ok" and u_max <= 1 + 1e-6 and all(c["passed"] for c in cracks.values()),
        "d_mm": round(min(d_of(options[ti][1], options[ti][3]), d_of(options[bi][1], options[bi][3]))),
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
    user_cage: BeamCage | None = None,
    sign: dict[str, Any] | None = None,
    joint_lengths: list[float] | None = None,
) -> dict[str, Any]:
    """Choose the beam's longitudinal bars, or check the ones the user set (``user_cage``).
    ``joint_lengths``: the lengths of the berth's segments between expansion joints the beam runs
    through; the restraint crack width is also given for each of them."""
    beam = with_project_grades(beam, settings.materials, settings.durability)
    lay = layout(sheets, axes)
    sag, sign_note = sag_factor(settings.plate_positive_moment, sign)
    conc = concrete(beam.concrete)
    fyk = REINFORCEMENT_GRADES[settings.reinforcement.grade]
    b = beam.width if beam.width is not None else round(lay.width * 1000)
    g = Geometry(float(b), beam.depth, beam.cover, beam.link_diameter)
    dg = settings.piles.aggregate_size
    cl, sl = _laws(beam, settings)
    e_eff = conc.ecm / (1 + settings.cracking.creep_coefficient)
    supports = find_supports(lay, geometry, elements)
    # The supports whose results are left out (Design settings); all of them still set the truss spans.
    cut = supports if settings.beam_support_results == "faces" else []
    peak = g.b / 1000 if settings.beam_actions == "peak_width" else None
    uls, t_uls = beam_loads(sheets, lay, sag, qp=False, supports=cut, peak_width=peak)
    qp, t_qp = beam_loads(sheets, lay, sag, qp=True, supports=cut, peak_width=peak)
    notes = [
        f"Spans along global {lay.along} ({lay.start:.2f} to {lay.end:.2f} m, {lay.end - lay.start:.1f} m); "
        f"local {lay.span_local} is along the beam"
        + (" (from the directions check)." if axes else " (assumed: the directions check had no answer)."),
        f"Width in the model {lay.width * 1000:.0f} mm"
        + (
            f", designed as {b:.0f} mm."
            if beam.width is not None
            else ", used as the beam width because the element has no width. Set the real width on "
            "the element: it sets the bars, the minimum steel and the capacity."
        ),
        (
            f"Vertical bending and shear: peak nodal M and Q per metre within ±{PEAK:g} m of each station "
            f"× the beam width {b / 1000:g} m, as the calc report takes them. N, horizontal bending and "
            f"torsion are integrated over the model's {lay.width:g} m width (Design settings)."
            if peak is not None
            else "Section forces integrated over the model's width (Design settings)."
        ),
        sign_note,
    ]
    if supports and cut:
        names = sorted({q.element for q in supports})
        notes.append(
            f"{len(supports)} supports inside the beam ({', '.join(names)}): bending at their faces, "
            f"shear at {settings.shear_check_distance} from them; results inside them are left out "
            "(Design settings)."
        )
    elif supports:
        names = sorted({q.element for q in supports})
        notes.append(
            f"{len(supports)} supports inside the beam ({', '.join(names)}): every result along the beam is "
            "designed, over them too (Design settings: leave them out to take bending at their faces)."
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
        "support_results": settings.beam_support_results,
        "notes": notes,
    }
    if uls.empty:
        notes.append("No ULS results.")
        return {**base, "utilisation": None, "passed": False}

    s_all = uls["s"].to_numpy(float)
    mom = uls[moment_stations(s_all, cut)]
    d_est = (g.h - g.inner(25)) / 1000
    dv = d_est * (2 if settings.shear_check_distance == "2d" else 1)
    mask, fallback = shear_stations(s_all, cut, dv)
    shr = uls[mask]
    if fallback:
        notes.append(
            "Supports are closer than 2 × (radius + d): shear is checked at the station midway between them."
        )
    qp_m = qp[moment_stations(qp["s"].to_numpy(float), cut)] if len(qp) else qp
    qp_all = qp_m
    qp_m = crack_candidates(qp_m, 0.9 * d_est) if len(qp_m) else qp_m

    tops = face_candidates(g, settings, g.b)
    if not tops:
        notes.append("No bars fit the beam width with the chosen spacing limits.")
        return {**base, "utilisation": None, "passed": False}
    as_min = as_min_beam(conc.fctm, fyk, g.b, g.h - g.inner(25))

    king_spacing = spacing_from_supports([q.s for q in supports])

    def truss_for(cage: Cage) -> dict | None:
        """The office's truss between king piles, tied by this cage's bottom bars."""
        if beam.truss is None:
            return None
        z = g.h - g.inner(cage.top.phi) - (cage.top.layers - 1) * g.layer_gap(cage.top.phi, dg) / 2
        z -= g.inner(cage.bottom.phi) + (cage.bottom.layers - 1) * g.layer_gap(cage.bottom.phi, dg) / 2
        return check_truss(beam.truss, g.b, g.h, z, cage.bottom.area, king_spacing)

    def grow_cage(asl: float, use_truss: bool = True):
        """Step the faces up until bending (with ``asl`` of torsion steel taken out of the faces),
        cracking, restraint and (with ``use_truss``) the truss tie pass."""
        ti = bi = next((i for i, f in enumerate(tops) if f.area >= as_min), len(tops) - 1)
        sides = side_candidates(g, settings, tops[ti], tops[bi])
        si = 0
        status = "ok"
        for _ in range(400):
            cage = Cage(tops[ti], tops[bi], sides[si])
            sec = RectSection(
                g.b, g.h, cage_bars(g, cage, dg, asl), cl, sl, deduct=settings.partial_factors.deduct_bar_area
            )
            u = sec.utilisation(n, mv, mh)
            full = sec
            if asl:
                full = RectSection(
                    g.b, g.h, cage_bars(g, cage, dg), cl, sl, deduct=settings.partial_factors.deduct_bar_area
                )
            restr = restraint_check(beam, settings, g, cage, conc)
            cracks = None  # the slow check, left to last
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
                for face in ("top", "bottom", "side"):
                    if restr["faces"][face]["wk"] > limits[face] + 1e-9:
                        grow = face
                        break
            jump = None
            if grow is None and use_truss:
                tr = truss_for(cage) or {}
                if (tr.get("utilisation") or 0.0) > 1:
                    grow = "bottom"
                    need = max(c["As_req_mm2"] for c in tr["cases"])
                    jump = next((i for i in range(bi + 1, len(tops)) if tops[i].area >= need), None)
            if grow is None:
                # The likeliest QP rows first (fast), then every QP row, so no row is missed.
                for rows in (qp_m, qp_all):
                    cracks = crack_check(full, g, cage, rows, e_eff, conc)
                    grow = next(
                        (f for f in ("top", "bottom") if f in cracks and cracks[f]["wk"] > limits[f] + 1e-9),
                        None,
                    )
                    if grow is not None or len(rows) == len(qp_all):
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
                bi = jump if jump is not None else bi + 1
                sides = side_candidates(g, settings, tops[ti], tops[bi])
                si = min(si, len(sides) - 1)
        if cracks is None or status != "ok":
            cracks = crack_check(full, g, cage, qp_all, e_eff, conc)
        return cage, sec, u, cracks, restr, status

    def fixed_cage(asl: float):
        """The user's bars, checked as they are."""
        uc = user_cage
        cage = Cage(
            Face(uc.top.count, uc.top.diameter, uc.top.layers),
            Face(uc.bottom.count, uc.bottom.diameter, uc.bottom.layers),
            Face(uc.side.count, uc.side.diameter),
        )
        sec = section(cage, asl)
        u = sec.utilisation(n, mv, mh)
        cracks = crack_check(section(cage), g, cage, qp_all, e_eff, conc)
        restr = restraint_check(beam, settings, g, cage, conc)
        return cage, sec, u, cracks, restr, "ok"

    design_cage = grow_cage if user_cage is None else lambda asl, use_truss=True: fixed_cage(asl)

    def section(cage: Cage, asl: float = 0.0) -> RectSection:
        bars = cage_bars(g, cage, dg, asl)
        return RectSection(g.b, g.h, bars, cl, sl, deduct=settings.partial_factors.deduct_bar_area)

    def face_needs(cage: Cage, asl: float) -> dict[str, dict]:
        """Steel each check needs on each face with the other faces as designed (mm², by bisection
        over the candidates), and the check that sets the face."""
        checks = {
            "bending": lambda c, f: float(section(c, asl).utilisation(n, mv, mh).max()) <= 1 + 1e-9,
            "crack": lambda c, f: (
                crack_check(section(c), g, c, qp_all, e_eff, conc).get(f, {"wk": 0.0})["wk"]
                <= limits[f] + 1e-9
            ),
            "restraint": lambda c, f: (
                restraint_check(beam, settings, g, c, conc)["faces"][f]["wk"] <= limits[f] + 1e-9
            ),
            "truss": lambda c, f: ((truss_for(c) or {}).get("utilisation") or 0.0) <= 1 + 1e-9,
        }
        names = {
            "minimum": "minimum steel",
            "bending": "bending (Plaxis actions)",
            "crack": "QP crack width (Plaxis actions)",
            "restraint": "restraint cracking",
            "truss": "truss tie",
        }
        out = {}
        for f in ("top", "bottom", "side"):
            if f == "side":
                cands = side_candidates(g, settings, cage.top, cage.bottom)
                lo, minimum = 0, cands[0].area
            else:
                cands = tops
                lo = next((i for i, x in enumerate(tops) if x.area >= as_min), len(tops) - 1)
                minimum = as_min
            final = getattr(cage, f)
            hi = cands.index(final) if final in cands else len(cands) - 1
            hi = max(hi, lo)

            def with_face(x, f=f):
                return Cage(**{"top": cage.top, "bottom": cage.bottom, "side": cage.side, f: x})

            needs = {"minimum": round(minimum)}
            for name, ok in checks.items():
                if (name == "truss" and (f != "bottom" or beam.truss is None)) or (
                    name == "crack" and f == "side"
                ):
                    continue
                if ok(with_face(cands[lo]), f):
                    needs[name] = 0
                    continue
                if not ok(with_face(cands[hi]), f):
                    needs[name] = None  # more than the bars given
                    continue
                a, b = lo, hi
                while b - a > 1:
                    m = (a + b) // 2
                    a, b = (a, m) if ok(with_face(cands[m]), f) else (m, b)
                needs[name] = round(cands[b].area)
            real = {k: v for k, v in needs.items() if v}
            most = max(real.values())
            gov = [names[k] for k, v in real.items() if v >= most - 1]
            if f == "side" and gov == ["minimum steel"]:
                gov = ["maximum bar spacing"]
            out[f] = {"needs_mm2": needs, "governed_by": " and ".join(gov)}
        return out

    limits = {"top": beam.crack_width_limit, "bottom": beam.crack_width_limit_bottom}
    limits["side"] = min(limits.values())
    n = mom["N"].to_numpy(float)
    mv = mom["Mv"].to_numpy(float)
    mh = mom["Mh"].to_numpy(float)
    cage, sec, u, cracks, restr, status = design_cage(0.0)

    # Torsion (6.3.2(3)): its longitudinal steel is shared round the perimeter and comes out of the
    # bars that bending uses, so the cage is grown again with it taken out of each face.
    # The transverse bars (per metre across the beam) never take the nodes inside a support: there the
    # plate moments are point peaks at the pile or king pile head, whatever the setting for the beam.
    t_keep = transverse_nodes(t_uls, supports, 0.0)
    t_qp_keep = t_qp[transverse_nodes(t_qp, supports, 0.0)] if len(t_qp) else t_qp
    t_shear = t_uls[transverse_nodes(t_uls, supports, dv)]
    trans = transverse_design(beam, settings, g, cage, t_uls[t_keep], t_qp_keep)
    asl = float(
        link_design(beam, settings, g, cage, shr, t_shear, trans).get("torsion_long_steel_mm2") or 0.0
    )
    if asl > 0:
        cage, sec, u, cracks, restr, status = design_cage(asl)
    # The same cage from the Plaxis actions alone, to show what the truss adds.
    plaxis_cage = grow_cage(asl, use_truss=False)[0] if beam.truss is not None else cage
    if user_cage is not None:
        notes.append(f"Bars set by you: {cage.label}. Triton checks them; it does not choose them.")
        for f in ("top", "bottom"):
            face = getattr(cage, f)
            span = g.b - 2 * (g.cover + g.link) - face.phi
            clear = span / max(face.count - 1, 1) - face.phi
            least = max(settings.reinforcement.min_clear_spacing, face.phi, dg + 5, 20)
            if face.count < 2 or clear < least - 1e-9:
                status = f"{f} bars: clear spacing {clear:.0f} mm is below the {least:.0f} mm minimum"
            elif clear + face.phi > settings.reinforcement.max_spacing + 1e-9:
                status = (
                    f"{f} bars: spacing {clear + face.phi:.0f} mm is over the "
                    f"{settings.reinforcement.max_spacing:g} mm maximum"
                )
    needs = face_needs(cage, asl)
    if status != "ok" and user_cage is not None:
        notes.append(f"Your bars break a spacing rule: {status}.")
    elif status != "ok":
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
    if joint_lengths:
        restr["segments"] = []
        for length in joint_lengths:
            r = restraint_check(beam.model_copy(update={"joint_spacing": length}), settings, g, cage, conc)
            restr["segments"].append(
                {
                    "length_m": length,
                    "R": r["R"],
                    "faces": {
                        f: {"wk": c["wk"], "limit": limits[f], "passed": c["wk"] <= limits[f] + 1e-9}
                        for f, c in r["faces"].items()
                    },
                }
            )

    # Transverse bars and links, on the final cage.
    trans = transverse_design(beam, settings, g, cage, t_uls[t_keep], t_qp_keep)
    links = link_design(beam, settings, g, cage, shr, t_shear, trans)
    if asl > 0:
        sh_t = torsion_shares(g, cage, asl)
        bending["torsion_steel"] = {"asl_mm2": round(asl), **{f"{k}_mm2": round(v) for k, v in sh_t.items()}}
        notes.append(
            f"Torsion needs {asl:.0f} mm² of longitudinal steel (6.3.2(3)). It is taken out of the cage "
            f"by perimeter share before the bending check: {sh_t['top']:.0f} mm² from the top and from "
            "the bottom" + (f", {sh_t['side']:.0f} mm² from each side." if sh_t["side"] else ".")
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
        "ratio_pct": round(100 * cage.to_dict(g.b, g.h)["kg_per_m"] / STEEL_DENSITY / vol, 2),
        "length_m": round(length, 2),
        "total_kg": round(kg_m * length),
        "element_total_t": round(kg_m * length / 1000, 2),
    }
    checks = [bending["utilisation"], links.get("utilisation"), trans.get("utilisation")]
    checks += [c["wk"] / c["limit"] for c in crack_out.values()]
    checks += [c["wk"] / c["limit"] for c in (trans.get("cracks") or {}).values() if c.get("limit")]
    checks += [c["wk"] / c["limit"] for c in restr["faces"].values() if math.isfinite(c["wk"])]
    bollard = check_bollard(beam.bollard, beam.concrete, settings) if beam.bollard is not None else None
    if bollard is not None:
        checks.append(bollard["utilisation"])
    truss = truss_for(cage)
    if truss is not None:
        checks.append(truss["utilisation"])
    finite = [c for c in checks if c is not None]
    passed = (
        bending["passed"]
        and bool(links.get("passed"))
        and trans["passed"]
        and all(c["passed"] for c in crack_out.values())
        and all(c["passed"] for c in restr["faces"].values())
        and status == "ok"
        and (bollard is None or bollard["passed"])
        and (truss is None or truss["passed"])
    )
    # Links and restraint are one arrangement for the whole beam, so they colour every band.
    uniform = max([c for c in checks[1:] if c is not None and math.isfinite(c)], default=0.0)
    bands = beam_bands(lay, mom.assign(u=np.maximum(u, uniform)))
    sets = beam_sets(cage.label, mom, u, qp_all)
    crack_sec = section(cage)  # all the bars: the crack check takes no torsion steel out
    for st in sets:
        for r in st["qp"]:
            r["crack"] = beam_crack_terms(crack_sec, g, cage, r["N_kN"], r["M3_kNm"], e_eff, conc, limits)
            r["utilisation"] = r["crack"]["util"]  # SLS: crack width over its limit
    # Ductility of the whole cage at its capacity, under the largest compression along the beam.
    n_c = max(float(mom["N"].max()), 0.0) if len(mom) else 0.0
    duct_faces = {}
    for f, sense in (("bottom", 1), ("top", -1)):
        r = crack_sec.ductility("v", sense, n_c)
        r["N_kN"] = round(n_c, 1)
        r["warnings"] = ductility.warnings(r["x_d"], r["eps_s"], r["eps_yd"], None)
        duct_faces[f] = r
    ratio_all = 100 * float(crack_sec.bars.total) / (g.b * g.h)
    duct = {
        "faces": duct_faces,
        "ratio_pct": round(ratio_all, 2),
        "warnings": [
            f"{'Bottom' if f == 'bottom' else 'Top'} bars in tension: {w}"
            for f, r in duct_faces.items()
            for w in r["warnings"]
        ]
        + ductility.warnings(None, None, 0.0, ratio_all),
    }
    notes.extend(f"Over-reinforced? {w}." for w in duct["warnings"])
    faces = [
        {
            "face": f,
            "final": getattr(cage, f).label,
            "final_mm2": round(getattr(cage, f).area),
            "plaxis": getattr(plaxis_cage, f).label,
            "plaxis_mm2": round(getattr(plaxis_cage, f).area),
            **needs[f],
        }
        for f in ("top", "bottom", "side")
    ]
    return {
        **base,
        "user_set": user_cage is not None,
        "faces": faces,
        "cage": {
            **cage.to_dict(g.b, g.h),
            # Every bar at its real size (the bending section takes the torsion steel out of the bar
            # areas, which would draw them smaller than they are).
            "bars": [
                [round(float(u), 1), round(float(v), 1), round(math.sqrt(4 * a / math.pi))]
                for u, v, a in zip(crack_sec.bars.u, crack_sec.bars.v, crack_sec.bars.area, strict=True)
            ],
            "link_diameter_mm": g.link,
            "lines": adsec_lines(g, cage, dg),
        },
        "utilisation": round(max(finite), 3) if finite else None,
        "passed": bool(passed),
        "bending": bending,
        "cracks": crack_out,
        "restraint": restr,
        "shear": links,
        "transverse": trans,
        "bollard": bollard,
        "truss": truss,
        "steel": steel,
        "bands": bands,
        "tension": beam_tension([mom, qp_all], lay.start, BAND, lambda i: _band_at(lay, i), g.b, g.h),
        "crack_bands": beam_crack_bands(crack_sec, g, cage, qp_all, e_eff, conc, limits, lay),
        "profile": _profile(mom, u),
        "profile_qp": _profile_qp(crack_sec, g, cage, qp_all, e_eff, conc, limits),
        "governing_sets": sets,
        "ductility": duct,
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


def _profile_qp(sec, g, cage, qp: pd.DataFrame, e_eff, conc, limits) -> list[dict]:
    """As ``_profile`` for the QP results: the vertical bending envelope and, as the utilisation, the
    crack width over its limit at the largest sagging and hogging moment of each position."""
    if qp is None or qp.empty:
        return []
    out = []
    for s, rows in qp.groupby("s"):
        u = 0.0
        for i in {rows["Mv"].idxmax(), rows["Mv"].idxmin()}:
            r = rows.loc[i]
            face = "bottom" if r["Mv"] >= 0 else "top"
            c = face_crack(sec, g, getattr(cage, face), float(r["N"]), float(r["Mv"]), e_eff, conc)
            u = max(u, c["wk"] / limits[face])
        out.append(
            {
                "s": float(s),
                "u": round(u, 3),
                "Mv_max": round(float(rows["Mv"].max()), 1),
                "Mv_min": round(float(rows["Mv"].min()), 1),
            }
        )
    return out


def beam_crack_terms(sec, g: Geometry, cage: Cage, n: float, mv: float, e_eff: float, conc, limits) -> dict:
    """A QP set's crack width at its tension face (vertical bending, as the crack check)."""
    face = "bottom" if mv >= 0 else "top"
    c = face_crack(sec, g, getattr(cage, face), float(n), float(mv), e_eff, conc)
    out = crack_terms(c["wk"], limits[face], c["sigma_s"], c["sr_max"], c["x_mm"], g.h)
    return {**out, "face": face, "b_mm": g.b}


def beam_crack_bands(sec, g, cage, qp: pd.DataFrame, e_eff, conc, limits, lay: Layout) -> list[list[float]]:
    """[x, y, z, wk / limit] per 0.5 m band, the worst QP row of each band at its tension face."""
    if qp is None or qp.empty:
        return []
    k = np.floor((qp["s"].to_numpy(float) - lay.start) / BAND).astype(int)
    out = []
    for i, rows in qp.assign(k=k).groupby("k"):
        worst = 0.0
        for _, r in rows.iterrows():
            face = "bottom" if r["Mv"] >= 0 else "top"
            c = face_crack(sec, g, getattr(cage, face), float(r["N"]), float(r["Mv"]), e_eff, conc)
            worst = max(worst, c["wk"] / limits[face])
        out.append([*_band_at(lay, int(i)), round(worst, 3)])
    return out


def _band_at(lay: Layout, i: int) -> list[float]:
    s = lay.start + (i + 0.5) * BAND
    x, y = (lay.centre, s) if lay.along == "Y" else (s, lay.centre)
    return [round(x, 3), round(y, 3), round(lay.level, 2)]


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
