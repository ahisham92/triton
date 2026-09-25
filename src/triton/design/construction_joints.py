"""Construction joints: where a pile, beam or slab is cast in two pours, and the bars the joint needs.

Each element takes its own joints in its settings: a pile at a level (the pile top under the slab or
beam, or a pour break), a beam at a level (cast in lifts, or up to the slab soffit) or at a position
along it (a stop end), and a slab along a line at an X or a Y or at a beam's face (the slab cast
against the beam). Every joint is checked with the Plaxis actions at it, as designed (load
multipliers, working zone and sign as for the element's own design, ULS combinations only):

The rules are in Design settings (Construction joints); their defaults follow the office's Final
Design Report, Appendix 18 "Shear check at construction joint", which gives 6040 mm² (front beam
joint, VEd 1294 kN/m, NEd 640 kN/m tension, h 700) and 3343 mm² (rear beam joint, 920 and 72 kN/m).

Shear across the joint, EN 1992-1-1 6.2.5
    vEdi = β VEd / (z bi) with β = 1 (all the force crosses the joint) and z = 0.8 d (office; 0.9 d
    is a setting), d = h − cover (office) or to the tension bars; bi = D for a pile, the beam width,
    1 m of slab. The resistance is
    vRdi = c fctd + μ σn + ρ fyd μ ≤ 0.5 ν fcd, bars at 90° to the joint (α = 90°), with c and μ of
    the joint's surface (6.2.5(2): very smooth 0.025 / 0.5, smooth 0.20 / 0.6, rough 0.40 / 0.7,
    indented 0.50 / 0.9; indented, a keyed joint such as Stremaform, is the default as the office's),
    fctd = fctk,0.05 / γc, σn = N / (b d) (office; N / (b h) is a setting; compression +, at most
    0.6 fcd) and c fctd = 0 when σn is tension. ρ = As / Ai over the whole joint area Ai = b h, so
    As = ρ b h. For a slab the shear is the largest at the joint (office), or the mean over the strip
    width as the slab design (a setting); results inside the pile heads are left out.

Tension across the joint
    A joint carries no tension in the concrete. As the office's check, N enters through σn and the
    joint bars are the shear-friction steel; bending is checked in the element's own design. With the
    setting "add", the tension steel of N with M is added on top (no bar counted twice): for a pile or
    a beam stop end the least share of its crossing bars that still carries every ULS result near the
    joint in N + M; for a slab, the tension face's steel for M with N (both faces when the whole depth
    is in tension). It is shown either way.

Bars needed
    The bars crossing the joint (all of them) must give the steel needed. For a horizontal joint in a
    beam the crossing bars are the links (Asw/s per metre). Where
    the bars crossing are not enough, the additional bars at that joint are chosen: the fewest dowels
    or starter bars (an even number on a ring inside a pile cage; bars per metre, half on each face, in
    a slab; spread round the perimeter at a beam stop end; U-bars with the links' legs in a horizontal
    beam joint), each 2 × the lap length long (the lap factor × Ø each side of the joint).

Laps
    A lap of a pile's curtailed cage that spans the joint is flagged, with the level to move it to.
    Slab and beam laps are placed by the detailer: keep them a lap length clear of the joints.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from ..axes import sag_factor
from ..importer import SheetData
from ..materials import REINFORCEMENT_GRADES, concrete
from ..project import (
    BeamInput,
    BeamJoint,
    DesignSettings,
    PileInput,
    SlabInput,
    SlabJoint,
    with_project_grades,
)
from .circular import CircularSection, ConcreteLaw, Ring, SteelLaw
from .rect import Bars, RectSection

# EN 1992-1-1 6.2.5(2): (c, μ) per surface.
SURFACES = {"very smooth": (0.025, 0.5), "smooth": (0.20, 0.6), "rough": (0.40, 0.7), "indented": (0.50, 0.9)}
SPACINGS = (100.0, 125.0, 150.0, 175.0, 200.0, 250.0, 300.0)
NEAR = 0.3  # m: results this close to the joint (beyond the nearest one) are taken at it
FAR = 1.0  # m: no result closer than this, no check


def _r(v: float | None, n: int = 1) -> float | None:
    return None if v is None or not math.isfinite(v) else round(float(v), n)


class Laws:
    """Strengths of the element's concrete and the bars (MPa)."""

    def __init__(self, grade: str, settings: DesignSettings):
        pf = settings.partial_factors
        c = concrete(grade)
        self.fck = c.fck
        self.fcd = pf.alpha_cc * c.fck / pf.gamma_c
        self.fctd = 0.7 * c.fctm / pf.gamma_c  # αct = 1
        self.fyk = REINFORCEMENT_GRADES[settings.reinforcement.grade]
        self.fyd = self.fyk / pf.gamma_s
        self.nu = 0.6 * (1 - c.fck / 250)
        self.v_max = 0.5 * self.nu * self.fcd
        self.concrete = ConcreteLaw(c.fck, pf.gamma_c, pf.alpha_cc)
        self.steel = SteelLaw(self.fyk, pf.gamma_s)


def shear_steel(v_edi: np.ndarray, sigma_n: np.ndarray, surface: str, laws: Laws) -> np.ndarray:
    """ρ (As / Ai) the joint needs for vEdi (MPa) under σn (MPa, compression +): 6.2.5(1)."""
    c, mu = SURFACES[surface]
    sn = np.minimum(sigma_n, 0.6 * laws.fcd)
    friction = np.where(sn >= 0, c * laws.fctd, 0.0) + mu * sn
    return np.maximum(v_edi - friction, 0.0) / (laws.fyd * mu)


def v_rdi(rho: float, sigma_n: float, surface: str, laws: Laws) -> float:
    c, mu = SURFACES[surface]
    sn = min(sigma_n, 0.6 * laws.fcd)
    return max(min((c * laws.fctd if sn >= 0 else 0.0) + mu * sn + rho * laws.fyd * mu, laws.v_max), 0.0)


def _lap(settings: DesignSettings, phi: float) -> float:
    return settings.piles.lap_factor * phi / 1e3


def _diameters(settings: DesignSettings, least: int = 12) -> list[int]:
    return sorted(d for d in settings.reinforcement.bar_diameters if d >= least) or [16]


def bars_per_m(need: float, settings: DesignSettings, legs: int = 1) -> dict | None:
    """The lightest Ø at a spacing (and ``legs`` bars at each) giving ``need`` mm²/m."""
    best = None
    for phi in _diameters(settings):
        for s in SPACINGS:
            a = legs * math.pi * phi * phi / 4 * 1000 / s
            if a >= need - 1e-6 and (best is None or a < best[2] - 1e-6):
                best = (phi, s, a)
    if best is None:
        return None
    phi, s, a = best
    words = f"Ø{phi} @ {s:g}" if legs == 1 else f"Ø{phi} U-bars, {legs} legs @ {s:g}"
    return {"diameter_mm": phi, "spacing_mm": s, "legs": legs, "area_mm2_per_m": round(a), "label": words}


def bars_count(need: float, settings: DesignSettings, fits: Any = None, even: bool = True) -> dict | None:
    """The fewest bars of the lightest set (area ≥ ``need`` mm²); ``fits(n, Ø)`` says whether they fit."""
    best = None
    for phi in _diameters(settings, 16):
        one = math.pi * phi * phi / 4
        n = max(4, math.ceil(need / one - 1e-9))
        if even and n % 2:
            n += 1
        if fits is not None and not fits(n, phi):
            continue
        if best is None or n * one < best[2] - 1e-6:
            best = (n, phi, n * one)
    if best is None:
        return None
    n, phi, a = best
    return {"count": n, "diameter_mm": phi, "area_mm2": round(a), "label": f"{n}Ø{phi}"}


def _status(passed: bool, crushes: bool, extra: dict | None) -> str:
    if crushes:
        return "joint fails: vEdi above 0.5 ν fcd, bars cannot help (roughen or indent it, or move it)"
    if passed:
        return "OK with the bars crossing it"
    return "additional bars needed" if extra else "additional bars needed: none of the allowed bars fit"


def _near(frame: pd.DataFrame, col: str, at: float) -> pd.DataFrame | None:
    if frame.empty:
        return None
    d = (frame[col].astype(float) - at).abs()
    if float(d.min()) > FAR:
        return None
    return frame[d <= float(d.min()) + NEAR]


# --- Piles --------------------------------------------------------------------------------------------


def _cage_at(design: dict, level: float) -> tuple[list[dict], dict | None, list[str]]:
    """The rings crossing ``level`` (the run's cage; at the head only the bars running on above it),
    the run and warnings for a lap spanning the joint."""
    runs = (design.get("curtailment") or {}).get("runs") or []
    warnings: list[str] = []
    if not runs:
        rings = [
            {
                "count": r["count"],
                "diameter": r.get("diameter") or r.get("diameter_mm"),
                "radius": r.get("radius") or r.get("radius_mm"),
            }
            for r in (design.get("arrangement") or {}).get("rings") or []
        ]
        return rings, None, warnings
    head = runs[0]["top"]
    if level >= head - 1e-6:
        run = runs[0]
        above = run.get("above_head_m") or []
        rings = [r for r, a in zip(run["cage"]["rings"], above, strict=False) if a > 1e-6]
        if level > head + 1e-6:
            rings = [r for r, a in zip(run["cage"]["rings"], above, strict=False) if a > level - head + 1e-6]
        return rings, run, warnings
    run = next((r for r in runs if r["bottom"] - 1e-6 <= level <= r["top"] + 1e-6), runs[-1])
    for upper in runs[:-1]:
        laps = upper.get("lap_below_m") or []
        lap = max(laps) if laps else 0.0
        b = upper["bottom"]
        if lap > 0 and b - lap - 1e-6 <= level <= b + 1e-6:
            warnings.append(
                f"The cage's lap from {b:g} m down to {b - lap:.2f} m spans the joint: move the bar joint so "
                f"the lap ends at least at {level + 0.05:.2f} m, or below {level - lap:.2f} m (lapped bars "
                "are counted once here)."
            )
    return list(run["cage"]["rings"]), run, warnings


def _circle(pile: PileInput, rings: list[dict], laws: Laws, k: float) -> CircularSection:
    s = math.sqrt(max(k, 1e-9))
    return CircularSection(
        pile.diameter,
        tuple(Ring(int(r["count"]), float(r["diameter"]) * s, float(r["radius"])) for r in rings),
        laws.concrete,
        laws.steel,
    )


def _least_share(util, top: float = 4.0) -> float:
    """The least factor k (0..top) on the bars' area with util(k) ≤ 1 (util falls as k grows)."""
    if util(0.0) <= 1.0:
        return 0.0
    if util(top) > 1.0:
        return math.inf
    lo, hi = 0.0, top
    for _ in range(18):
        mid = (lo + hi) / 2
        lo, hi = (lo, mid) if util(mid) <= 1.0 else (mid, hi)
    return hi


def pile_joints(
    name: str, pile: PileInput, settings: DesignSettings, design: dict, loads: pd.DataFrame
) -> list[dict]:
    """Every joint of one pile element. ``loads``: the pile's ULS results as designed (N compression +,
    columns Z, N, M, Q_12, Q_13, combination)."""
    if not pile.construction_joints:
        return []
    pile = with_project_grades(pile, settings.materials, settings.durability)
    laws = Laws(pile.concrete, settings)
    dg = settings.piles.aggregate_size
    rules = settings.construction_joints
    add = rules.tension == "add"
    out = []
    for i, j in enumerate(pile.construction_joints):
        e = _base(name, "pile", i, j.surface, j.note, f"Level {j.level:g} m, across the pile")
        e["level_m"] = j.level
        rings, run, warns = _cage_at(design, j.level)
        e["laps"] = warns
        near = _near(loads, "Z", j.level)
        if near is None:
            e["status"] = "no results within 1 m of this level: not checked"
            out.append(e)
            continue
        area = sum(int(r["count"]) * math.pi * float(r["diameter"]) ** 2 / 4 for r in rings)
        label = " + ".join(f"{int(r['count'])}Ø{int(r['diameter'])}" for r in rings) or "none"
        e["crossing"] = {"label": label, "area_mm2": round(area)}
        n = near["N"].to_numpy(float)
        m = near["M"].to_numpy(float)
        v = np.hypot(near["Q_12"].to_numpy(float), near["Q_13"].to_numpy(float))
        # Tension: the least share of these bars that still carries N with M there.
        if rings and area > 0:
            k = _least_share(
                lambda k, r=rings, n=n, m=m: float(_circle(pile, r, laws, k).utilisation(n, m).max())
            )
            as_t = k * area
        else:
            plain = CircularSection(pile.diameter, (), laws.concrete, laws.steel)
            as_t = 0.0 if float(plain.utilisation(n, m).max()) <= 1 else math.inf
        ac = math.pi * pile.diameter**2 / 4
        rs = max((float(r["radius"]) for r in rings), default=pile.diameter / 2 - 100)
        d = pile.diameter / 2 + 2 * rs / math.pi
        z = rules.lever_arm * d
        v_edi = v * 1e3 / (z * pile.diameter)
        sigma = n * 1e3 / ac
        need_v = shear_steel(v_edi, sigma, j.surface, laws) * ac
        g = int(np.argmax(need_v - 1e-9 * v_edi))  # the most steel, else the largest shear
        row = near.iloc[g]
        e["forces"] = {
            "combination": row.get("combination"),
            "z_m": _r(row["Z"], 2),
            "N_kN": _r(n[g]),
            "M_kNm": _r(m[g]),
            "V_kN": _r(v[g]),
            "results": int(len(near)),
        }
        spare = max(area - as_t, 0.0) if add else area
        _shear_terms(e, v_edi[g], sigma[g], spare / ac, j.surface, laws)
        _needs(e, as_t, float(need_v[g]), area, add=add)
        crushes = float(v_edi.max()) > laws.v_max + 1e-9
        extra = None
        if (e["additional_mm2"] or 0) > 0 and not crushes:
            inner = min(rings, key=lambda r: float(r["radius"])) if rings else None

            def radius(phi: float, inner: dict | None = inner) -> float:
                """The dowels' circle: a clear gap inside the innermost row (or inside the links)."""
                if inner is None:
                    return pile.diameter / 2 - pile.cover - pile.link_diameter - phi / 2
                return float(inner["radius"]) - float(inner["diameter"]) / 2 - phi / 2 - max(phi, dg + 5, 20)

            def fits(count: int, phi: float) -> bool:
                r0 = radius(phi)
                return r0 > 0 and 2 * math.pi * r0 / count - phi >= max(phi, dg + 5, 20)

            extra = bars_count(e["additional_mm2"], settings, fits)
            if extra:
                lap = _lap(settings, extra["diameter_mm"])
                extra["radius_mm"] = round(radius(extra["diameter_mm"]), 1)
                extra |= {
                    "length_m": round(2 * lap, 2),
                    "label": f"{extra['label']} dowels on a ring inside the cage, {2 * lap:.2f} m long "
                    f"({settings.piles.lap_factor:g}Ø each side of {j.level:g} m)",
                }
        e["additional"] = extra
        e["status"] = _status(e["passed"], crushes, extra)
        e["passed"] = e["passed"] and not crushes
        out.append(e)
    return out


# --- Beams --------------------------------------------------------------------------------------------


def beam_joints(
    name: str,
    beam: BeamInput,
    settings: DesignSettings,
    design: dict,
    uls: pd.DataFrame,
    top_level: float,
) -> list[dict]:
    """Every joint of one beam. ``uls``: its ULS station forces (s, N, Mv, Mh, V, Vh, combination) as
    designed; ``top_level``: the beam's top (m)."""
    if not beam.construction_joints or design.get("utilisation") is None:
        return []
    beam = with_project_grades(beam, settings.materials, settings.durability)
    laws = Laws(beam.concrete, settings)
    b, h = float(design["width_mm"]), float(design["depth_mm"])
    cage = design.get("cage") or {}
    bars = np.array(cage.get("bars") or [], float).reshape(-1, 3)
    out = []
    for i, j in enumerate(beam.construction_joints):
        if j.kind == "level":
            e = _base(name, "beam", i, j.surface, j.note, f"Level {j.at:g} m, horizontal through the beam")
            e["level_m"] = j.at
            out.append(_beam_level(e, j, settings, laws, design, uls, top_level, b, h))
        else:
            e = _base(
                name, "beam", i, j.surface, j.note, f"At {design.get('along', '')} = {j.at:g} m, a stop end"
            )
            e["at_m"] = j.at
            out.append(_beam_stop(e, j, settings, laws, design, uls, bars, b, h))
    return out


def _beam_level(e, j: BeamJoint, settings, laws: Laws, design, uls, top_level, b, h) -> dict:
    above = (top_level - j.at) * 1e3
    e["height_above_soffit_mm"] = _r(h - above, 0)
    if not 0 < above < h:
        e["status"] = (
            f"level {j.at:g} m is not inside the beam ({top_level - h / 1e3:.2f} to {top_level:.2f} m): "
            "not checked"
        )
        return e
    link = (design.get("shear") or {}).get("link") or {}
    legs, phi, s = int(link.get("legs") or 0), float(link.get("phi") or 0), float(link.get("spacing_mm") or 0)
    asw = legs * math.pi * phi * phi / 4 * 1000 / s if s else 0.0  # mm²/m
    e["crossing"] = {"label": link.get("label") or "no links", "area_mm2_per_m": round(asw)}
    bottom = (design.get("cage") or {}).get("bottom") or {}
    cover = float(design.get("cover_mm") or 50)
    rules = settings.construction_joints
    d = (
        h - cover
        if rules.effective_depth == "cover"
        else h - cover - phi - float(bottom.get("phi") or 25) / 2
    )
    z = rules.lever_arm * d
    v = uls["V"].abs().to_numpy(float)
    v_edi = v * 1e3 / (z * b)
    need = shear_steel(v_edi, np.zeros_like(v_edi), j.surface, laws) * b * 1000  # mm²/m
    g = int(np.argmax(need - 1e-9 * v_edi)) if len(need) else 0
    if not len(need):
        e["status"] = "no ULS results: not checked"
        return e
    row = uls.iloc[g]
    e["forces"] = {"combination": row.get("combination"), "s_m": _r(row["s"], 2), "V_kN": _r(v[g])}
    _shear_terms(e, v_edi[g], 0.0, asw / (b * 1000), j.surface, laws)
    e["method_note"] = (
        "Links cross the joint (Asw/s per metre); β = 1; σn = 0. The longitudinal bars run along it."
    )
    _needs(e, 0.0, float(need[g]), asw, per_m=True)
    crushes = float(v_edi.max()) > laws.v_max + 1e-9
    legs_u = max(legs, 2)

    def pick(amount: float, lo: float, hi: float) -> dict | None:
        extra = bars_per_m(amount, settings, legs_u)
        if extra:
            lap = _lap(settings, extra["diameter_mm"])
            extra |= {
                "length_m": round(2 * lap, 2),
                "label": f"{extra['label']} across the joint from {design.get('along', 's')} {lo:g} to "
                f"{hi:g} m, legs {2 * lap:.2f} m long ({settings.piles.lap_factor:g}Ø each side), "
                "in addition to the links",
            }
        return extra

    extra = None
    if not crushes:
        e["stretches"], extra = _stretches(uls["s"].to_numpy(float), need - asw, 0.5, pick)
    e["additional"] = extra
    e["status"] = _status(e["passed"], crushes, extra)
    e["passed"] = e["passed"] and not crushes
    return e


def _beam_stop(e, j: BeamJoint, settings, laws: Laws, design, uls, bars, b, h) -> dict:
    near = _near(uls, "s", j.at)
    if near is None or not len(bars):
        e["status"] = "no results within 1 m of this position: not checked"
        return e
    area0 = math.pi * bars[:, 2] ** 2 / 4
    area = float(area0.sum())
    e["crossing"] = {
        "label": (design.get("cage") or {}).get("label") or "the beam's bars",
        "area_mm2": round(area),
    }
    n, mv, mh = (near[c].to_numpy(float) for c in ("N", "Mv", "Mh"))

    def util(k: float) -> float:
        sec = RectSection(b, h, Bars(bars[:, 0], bars[:, 1], area0 * max(k, 1e-9)), laws.concrete, laws.steel)
        return float(sec.utilisation(n, mv, mh).max())

    as_t = _least_share(util) * area
    top = (design.get("cage") or {}).get("top") or {}
    rules = settings.construction_joints
    cover = float(design.get("cover_mm") or 50)
    d = h - cover if rules.effective_depth == "cover" else h - cover - 16 - float(top.get("phi") or 25) / 2
    v, vh = near["V"].abs().to_numpy(float), near["Vh"].abs().to_numpy(float)
    za = rules.lever_arm
    v_edi = np.hypot(v * 1e3 / (za * d * b), vh * 1e3 / (za * (b - (h - d)) * h))
    ai = b * h
    sigma = n * 1e3 / (b * d if rules.sigma_n_area == "d" else ai)
    need_v = shear_steel(v_edi, sigma, j.surface, laws) * ai
    g = int(np.argmax(need_v - 1e-9 * v_edi))
    row = near.iloc[g]
    e["forces"] = {
        "combination": row.get("combination"),
        "s_m": _r(row["s"], 2),
        "N_kN": _r(n[g]),
        "Mv_kNm": _r(mv[g]),
        "Mh_kNm": _r(mh[g]),
        "V_kN": _r(v[g]),
        "Vh_kN": _r(vh[g]),
        "results": int(len(near)),
    }
    add = rules.tension == "add"
    _shear_terms(e, v_edi[g], sigma[g], (max(area - as_t, 0.0) if add else area) / ai, j.surface, laws)
    _needs(e, as_t, float(need_v[g]), area, add=add)
    crushes = float(v_edi.max()) > laws.v_max + 1e-9
    extra = None
    if (e["additional_mm2"] or 0) > 0 and not crushes:
        extra = bars_count(e["additional_mm2"], settings)
        if extra:
            lap = _lap(settings, extra["diameter_mm"])
            extra |= {
                "length_m": round(2 * lap, 2),
                "label": f"{extra['label']} straight bars through the stop end, spread round the section "
                f"(more on the tension face), {2 * lap:.2f} m long "
                f"({settings.piles.lap_factor:g}Ø each side)",
            }
    e["additional"] = extra
    e["status"] = _status(e["passed"], crushes, extra)
    e["passed"] = e["passed"] and not crushes
    return e


# --- Slabs --------------------------------------------------------------------------------------------


def _zone_at(face: dict, x: float, y: float) -> dict | None:
    for z in face.get("zones") or []:
        if z["x_m"][0] - 1e-6 <= x <= z["x_m"][1] + 1e-6 and z["y_m"][0] - 1e-6 <= y <= z["y_m"][1] + 1e-6:
            return z
    return None


def _face_steel(drawing: dict, face: str, along: str, x: float, y: float) -> tuple[float, float, str]:
    """(mm²/m, depth of the layers' centroid from the face mm, text) of one face's bars along ``along``."""
    for f in drawing.get("faces") or []:
        if f["face"] != face or f["bars_along"] != along:
            continue
        zone = _zone_at(f, x, y)
        layers = (zone or {}).get("layers") or f["mesh"]["layers"]
        a = sum(float(ly.get("as_mm2_per_m") or 0) for ly in layers)
        if a <= 0:
            return 0.0, 0.0, "none"
        c = sum(float(ly.get("as_mm2_per_m") or 0) * float(ly.get("from_face_mm") or 0) for ly in layers) / a
        return a, c, (zone or {}).get("label") or " + ".join(ly.get("text", "") for ly in layers)
    return 0.0, 0.0, "none"


def slab_line(slab: SlabInput, j: SlabJoint, box: dict | None, beams: dict[str, dict]) -> tuple | None:
    """(bars along, fixed coordinate, range along the line or None, words) of a slab joint; None when
    the beam named is not found."""
    if j.line == "beam face":
        bm = beams.get(j.beam)
        if bm is None:
            return None
        across = "X" if bm["along"] == "Y" else "Y"
        half = bm["width_m"] / 2
        mid = None
        if box and box.get(across):
            mid = (box[across][0] + box[across][1]) / 2
        side = 1.0 if mid is None or mid >= bm["centre_m"] else -1.0
        at = bm["centre_m"] + side * half
        lo, hi = bm["start_m"], bm["end_m"]
        if j.start is not None:
            lo = max(lo, j.start)
        if j.end is not None:
            hi = min(hi, j.end)
        return across, at, (lo, hi), f"At the face of {j.beam}, {across} = {at:.2f} m"
    along = "X" if j.line == "at X" else "Y"
    if j.at is None:
        return along, None, None, "no X or Y given"
    rng = (
        None
        if j.start is None and j.end is None
        else (
            j.start if j.start is not None else -math.inf,
            j.end if j.end is not None else math.inf,
        )
    )
    return along, j.at, rng, f"Along the line {along} = {j.at:g} m"


def slab_joints(
    name: str,
    slab: SlabInput,
    settings: DesignSettings,
    design: dict,
    uls: pd.DataFrame,
    beams: dict[str, dict],
) -> list[dict]:
    """Every joint of one slab. ``uls``: its node forces per metre as designed (X, Y, Mx, My, Nx, Ny, Vx,
    Vy, combination; N compression +, M sagging +); ``beams``: each beam's line (along, centre_m,
    width_m, start_m, end_m)."""
    if not slab.construction_joints or design.get("utilisation") is None:
        return []
    from .export import _slab  # the slab's bars as drawn

    slab = with_project_grades(slab, settings.materials, settings.durability)
    laws = Laws(slab.concrete, settings)
    drawing = _slab(design)
    h = float(design.get("thickness_mm") or slab.thickness)
    # As the slab's own design: results inside the pile heads are left out, and the actions are the mean
    # over a strip width along the joint (the field strip, or a zone with a uniform slab).
    piles = [(float(p["x"]), float(p["y"]), float(p["D_mm"]) / 2000) for p in design.get("punching") or []]
    width = slab.field_strip_width if slab.strips == "column_and_field" else slab.zone_size
    out = []
    for i, j in enumerate(slab.construction_joints):
        line = slab_line(slab, j, design.get("box"), beams)
        if line is None:
            e = _base(name, "slab", i, j.surface, j.note, f"At the face of {j.beam or '(no beam named)'}")
            e["status"] = f"no beam named {j.beam!r} in this section: not checked"
            out.append(e)
            continue
        along, at, rng, words = line
        e = _base(name, "slab", i, j.surface, j.note, words)
        e["bars_along"] = along
        e["line"] = {
            "along": along,
            "at_m": _r(at, 3),
            "range_m": None if rng is None else [_r(v, 2) for v in rng],
        }
        if at is None:
            e["status"] = "no X or Y given: not checked"
            out.append(e)
            continue
        other = "Y" if along == "X" else "X"
        f = uls
        if rng is not None:
            f = f[(f[other] >= rng[0] - 1e-6) & (f[other] <= rng[1] + 1e-6)]
        near = _near(f, along, at)
        if near is not None and piles:
            keep = np.ones(len(near), bool)
            for px, py, pr in piles:
                keep &= np.hypot(near["X"].to_numpy(float) - px, near["Y"].to_numpy(float) - py) >= pr - 1e-6
            near = near[keep]
        if near is None or near.empty:
            e["status"] = "no slab results within 1 m of the line (outside the pile heads): not checked"
            out.append(e)
            continue
        if settings.construction_joints.actions == "strip_mean":
            near = strip_mean(near, other, width)
            e["averaged_over_m"] = width
        e["line"]["range_m"] = [_r(near[other].min(), 2), _r(near[other].max(), 2)]
        covers = (float(design.get("cover_top_mm") or 50), float(design.get("cover_bottom_mm") or 50))
        out.append(_slab_line_check(e, j, settings, laws, drawing, near, along, other, at, h, covers))
    return out


def strip_mean(near: pd.DataFrame, other: str, width: float) -> pd.DataFrame:
    """Each result with its combination's mean M, N and V over ``width`` along the line centred on it."""
    parts = []
    for _, g in near.groupby("combination", sort=False):
        g = g.sort_values(other)
        pos = g[other].to_numpy(float)
        cols = [c for c in ("Mx", "My", "Nx", "Ny", "Vx", "Vy") if c in g]
        vals = g[cols].to_numpy(float)
        mean = np.empty_like(vals)
        for k, p in enumerate(pos):
            m = np.abs(pos - p) <= width / 2 + 1e-9
            mean[k] = vals[m].mean(axis=0)
        parts.append(g.assign(**{c: mean[:, i] for i, c in enumerate(cols)}))
    return pd.concat(parts)


def _slab_line_check(
    e, j: SlabJoint, settings, laws: Laws, drawing, near, along, other, at, h, covers
) -> dict:
    from .slabs import required_as

    cover_top, cover_bot = covers
    m = near[f"M{along.lower()}"].to_numpy(float)
    n = near[f"N{along.lower()}"].to_numpy(float)
    v = near[f"V{along.lower()}"].abs().to_numpy(float)
    pos = near[other].to_numpy(float)
    # The bars crossing at each result: evaluated just on the line.
    steel = []
    for p in pos:
        x, y = (at, p) if along == "X" else (p, at)
        steel.append((_face_steel(drawing, "top", along, x, y), _face_steel(drawing, "bottom", along, x, y)))
    a_top = np.array([s[0][0] for s in steel])
    a_bot = np.array([s[1][0] for s in steel])
    c_top = np.array([s[0][1] or 60.0 for s in steel])
    c_bot = np.array([s[1][1] or 60.0 for s in steel])
    d = np.where(m >= 0, h - c_bot, h - c_top)
    as_t = np.array(
        [
            float(required_as(np.array([m[k]]), np.array([n[k]]), h, float(d[k]), laws.fck, laws.fyd)[0][0])
            for k in range(len(m))
        ]
    )
    whole = (n < 0) & (np.abs(m) * 1e6 < -n * 1e3 * (d - h / 2))  # the whole depth in tension
    as_t = np.where(whole, 2 * as_t, as_t)
    provided = a_top + a_bot
    rules = settings.construction_joints
    if rules.effective_depth == "cover":
        d = np.where(m >= 0, h - cover_bot, h - cover_top)
    v_edi = v * 1e3 / (rules.lever_arm * d * 1000)
    sigma = n * 1e3 / (1000 * (d if rules.sigma_n_area == "d" else h))
    need_v = shear_steel(v_edi, sigma, j.surface, laws) * 1000 * h
    add = rules.tension == "add"
    short = (as_t if add else 0.0) + need_v - provided
    g = int(np.argmax(short))
    row = near.iloc[g]
    e["forces"] = {
        "combination": row.get("combination"),
        other.lower() + "_m": _r(pos[g], 2),
        "N_kN_per_m": _r(n[g]),
        "M_kNm_per_m": _r(m[g]),
        "V_kN_per_m": _r(v[g]),
        "results": int(len(near)),
    }
    e["crossing"] = {
        "label": f"top {steel[g][0][2]}; bottom {steel[g][1][2]}",
        "area_mm2_per_m": round(float(provided[g])),
    }
    spare = max(provided[g] - as_t[g], 0.0) if add else provided[g]
    _shear_terms(e, v_edi[g], sigma[g], spare / (1000 * h), j.surface, laws)
    _needs(e, float(as_t[g]), float(need_v[g]), float(provided[g]), per_m=True, add=add)
    crushes = float(v_edi.max()) > laws.v_max + 1e-9

    def pick(amount: float, lo: float, hi: float) -> dict | None:
        face = bars_per_m(amount / 2, settings)
        if face is None:
            return None
        lap = _lap(settings, face["diameter_mm"])
        return face | {
            "area_mm2_per_m": 2 * face["area_mm2_per_m"],
            "faces": "top and bottom",
            "length_m": round(2 * lap, 2),
            "label": f"{face['label']} top and bottom, along {along}, across the joint from {other} "
            f"{lo:g} to {hi:g} m, {2 * lap:.2f} m long ({settings.piles.lap_factor:g}Ø each side)",
        }

    extra = None
    if not crushes:
        e["stretches"], extra = _stretches(pos, short, float(e.get("averaged_over_m") or 1.0) / 2, pick)
    e["additional"] = extra
    e["status"] = _status(e["passed"], crushes, extra)
    e["passed"] = e["passed"] and not crushes
    return e


# --- Shared -------------------------------------------------------------------------------------------


def _stretches(pos: np.ndarray, short: np.ndarray, reach: float, pick) -> tuple[list[dict], dict | None]:
    """The stretches along a joint where the bars fall short (mm²/m), each with its own additional bars
    ``pick(amount, from, to)`` over the results short there ± ``reach`` (m); and the heaviest one."""
    order = np.argsort(pos)
    p, sh = pos[order], short[order]
    runs: list[list[float]] = []
    for x, a in zip(p, sh, strict=True):
        if a <= 1e-6:
            continue
        if runs and x - runs[-1][1] <= 2 * reach + 1e-6:
            runs[-1][1] = x
            runs[-1][2] = max(runs[-1][2], a)
        else:
            runs.append([x, x, a])
    lo_all, hi_all = (float(p.min()), float(p.max())) if len(p) else (0.0, 0.0)
    out, worst = [], None
    for lo, hi, a in runs:
        lo, hi = round(max(lo - reach, lo_all), 2), round(min(hi + reach, hi_all), 2)
        bars = pick(float(a), lo, hi)
        item = {"from_m": lo, "to_m": hi, "additional_mm2_per_m": round(float(a)), "bars": bars}
        out.append(item)
        if bars and (worst is None or bars["area_mm2_per_m"] > worst["area_mm2_per_m"]):
            worst = bars
    if runs and any(r["bars"] is None for r in out):
        worst = None  # some stretch has no bars that fit: say so
    return out, worst


def _base(name: str, kind: str, i: int, surface: str, note: str, where: str) -> dict:
    c, mu = SURFACES[surface]
    return {
        "element": name,
        "kind": kind,
        "index": i,
        "where": where,
        "note": note,
        "surface": surface,
        "c": c,
        "mu": mu,
        "laps": [],
        "additional": None,
        "passed": None,
    }


def _shear_terms(e: dict, v_edi: float, sigma: float, rho: float, surface: str, laws: Laws) -> None:
    e |= {
        "v_Edi_MPa": _r(v_edi, 3),
        "sigma_n_MPa": _r(sigma, 3),
        "rho_pct": _r(100 * rho, 3),
        "v_Rdi_MPa": _r(v_rdi(rho, sigma, surface, laws), 3),
        "v_Rdi_max_MPa": _r(laws.v_max, 3),
        "fctd_MPa": _r(laws.fctd, 2),
        "fyd_MPa": _r(laws.fyd, 1),
    }


def _needs(
    e: dict, tension: float, shear: float, provided: float, per_m: bool = False, add: bool = True
) -> None:
    """The steel the joint needs: the shear-friction steel, plus the tension steel with ``add`` (else the
    tension is shown only: bending is checked in the element's own design)."""
    unit = "_mm2_per_m" if per_m else "_mm2"
    e["tension_added"] = add
    total = (tension if add else 0.0) + shear
    extra = max(total - provided, 0.0)
    e |= {
        "tension" + unit: None if not math.isfinite(tension) else round(tension),
        "shear" + unit: round(shear),
        "needed" + unit: None if not math.isfinite(total) else round(total),
        "provided" + unit: round(provided),
        "additional" + unit: math.inf if not math.isfinite(extra) else round(extra),
        "utilisation": _r(total / provided, 3) if provided > 0 else (0.0 if total <= 0 else None),
    }
    e["passed"] = total <= provided + 1e-6
    if not math.isfinite(extra):
        e["additional" + unit] = None


STEEL = 7850.0  # kg/m³


def add_weights(joints: list[dict], count: int = 1) -> list[dict]:
    """Each joint's additional bars' weight (kg; for a pile, over the ``count`` piles of its type)."""
    for j in joints:
        kg = 0.0
        for s in j.get("stretches") or []:
            b = s.get("bars")
            if b:
                kg += b["area_mm2_per_m"] * 1e-6 * max(s["to_m"] - s["from_m"], 0.0) * b["length_m"] * STEEL
        b = j.get("additional")
        if b and "count" in b:
            kg += b["area_mm2"] * 1e-6 * b["length_m"] * STEEL * count
        j["additional_kg"] = round(kg, 1)
    return joints


def summary(results: dict) -> list[dict]:
    """Every joint of the section, element by element, for the report and the drawings."""
    out = []
    for key in ("piles", "beams", "slabs"):
        for d in results.get(key) or []:
            out.extend(d.get("construction_joints") or [])
    return out


def assumptions() -> list[str]:
    return [
        "Construction joints: β = 1 (all the shear crosses the joint), bars at 90° to the joint; z, d, σn "
        "and the tension rule as Design settings (office report by default).",
        "Additional bars at a joint are 2 × the lap length long, the lap factor × Ø each side of it.",
        "Laps of slab and beam bars are kept a lap length clear of the joints by the detailer.",
    ]


# --- From the element's sheets ------------------------------------------------------------------------


def for_pile(
    name: str, pile: PileInput, settings: DesignSettings, own: dict[str, SheetData], design: dict
) -> list:
    from .piles import PileLoads

    if not pile.construction_joints:
        return []
    loads = PileLoads.from_sheets(own, pile.head_level, settings.results_into_connection / 1e3).frame
    return pile_joints(name, pile, settings, design, loads)


def for_beam(
    name: str,
    beam: BeamInput,
    settings: DesignSettings,
    own: dict[str, SheetData],
    geometry: list[dict],
    elements: dict[str, Any],
    axes: dict[str, str] | None,
    sign: dict[str, Any] | None,
    design: dict,
    top_level: float,
) -> list:
    from .beams import beam_loads, find_supports, layout

    if not beam.construction_joints:
        return []
    lay = layout(own, axes)
    sag, _ = sag_factor(settings.plate_positive_moment, sign)
    cut = find_supports(lay, geometry, elements) if settings.beam_support_results == "faces" else []
    peak = float(design.get("width_mm") or 0) / 1000 if settings.beam_actions == "peak_width" else None
    uls, _ = beam_loads(own, lay, sag, qp=False, supports=cut, peak_width=peak or None)
    return beam_joints(name, beam, settings, design, uls, top_level)


def beam_top(rule: Any, name: str, level: float, depth_mm: float) -> float:
    """The beam's top level (m): the plate is at mid-depth unless the Clashes tab says otherwise."""
    if name in rule.top_levels:
        return float(rule.top_levels[name])
    return level if rule.plate_level == "top" else level + depth_mm / 2e3


def beam_lines(
    sheets: dict[str, dict[str, SheetData]], elements: dict[str, Any], axes: dict
) -> dict[str, dict]:
    """Each beam's line in plan from its results: the axis along it, centre, width, start and end (m)."""
    from .beams import layout

    out = {}
    for name, e in elements.items():
        own = {c: s for c, s in (sheets.get(name) or {}).items() if not s.frame.empty}
        if not isinstance(e, BeamInput) or not own:
            continue
        lay = layout(own, axes.get(name))
        out[name] = {
            "along": lay.along,
            "centre_m": lay.centre,
            "width_m": e.width / 1000 if e.width else lay.width,
            "start_m": lay.start,
            "end_m": lay.end,
        }
    return out


def for_slab(
    name: str,
    slab: SlabInput,
    settings: DesignSettings,
    own: dict[str, SheetData],
    axes: dict[str, str] | None,
    sign: dict[str, Any] | None,
    design: dict,
    beams: dict[str, dict],
) -> list:
    from .slabs import add_crane, slab_loads

    if not slab.construction_joints:
        return []
    sag, _ = sag_factor(settings.plate_positive_moment, sign)
    uls, _ = add_crane(slab_loads(own, axes, sag, qp=False), slab)
    return slab_joints(name, slab, settings, design, uls, beams)
