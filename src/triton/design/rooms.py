"""Rooms cut into a beam (e.g. for electrical work): the section that is left, and its extra bars.

A room is cut into the beam from the top over part of its length, so the beam there is a channel:
a floor under the room (the concrete below it, e.g. 300 mm under a 1.7 m room in a 2.0 m beam), a
wall on each side and, when the room does not reach the top, a roof. Triton checks that section
at every station over the room's length with the same Plaxis actions as the rest of the beam
(the model has the beam solid, so its actions are used as they are):

* N with biaxial bending (EN 1992-1-1 5.8.9(4)) on the channel: the strips of the section have
  the width that is left at their level, and the moments stay about the beam's mid-depth, where
  Plaxis gives them;
* the bars: the beam's cage where it still has concrete round it; the top bars that run through
  the room are cut, and bars at the top of each wall replace them (at least their area, shared
  between the walls) and grow until bending and cracks pass. More bottom bars go in a layer above
  the beam's bottom bars when the floor is thick enough; the walls' inside faces get bars at the
  largest spacing;
* crack widths under the QP loads (7.3.4) at the top of the walls and at the bottom, each face
  against its own limit;
* shear and torsion (6.2, 6.3): the vertical shear is carried by the two walls (shared by their
  thickness); torsion is shared between the walls, the floor and the roof by their St Venant
  stiffness (6.3.1(3)), each part checked as its own thin-walled closed section with its own
  closed links. The walls' links are designed (spacing and, if needed, a larger bar); torsion's
  longitudinal steel comes out of each part's bars before the bending check. The horizontal shear
  goes through the floor (and roof);
* frame action across the room (the U-frame): the transverse moment per metre from the plates
  (at the nodes over the room's length) has to pass through the floor and round its corners into
  the walls. The floor is checked per metre on its own thickness for that moment with the
  transverse N, plus its own weight and the floor load over the room width (ULS 1.35 G + 1.5 Q,
  QP G + 0.6 Q; the span's moment wl²/8 is added in sagging and wl²/12 in hogging); the walls per
  metre for the same moment at the corner. Their bars per metre are designed (bending and QP
  cracks), the shear of the floor per metre is checked (with shear links if it needs them), and
  the inside corners get L-bars as large as the larger of the two;
* detailing at the room's ends: the wall top bars run a lap length (the piles' lap factor × Ø)
  past each end, and each corner of the opening gets two diagonal bars of the cut bars' size (at
  least Ø16), a lap length long.

If the room fails, Triton says what concrete below it would pass (in 50 mm steps, the room
getting lower), or, when a thicker floor does not help, what walls would.

Assumed: the Plaxis model has the beam solid, so the actions do not see the room's lower
stiffness (conservative for the room); a pile or king pile head under the room is flagged, since
its bars must anchor in the floor.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

import numpy as np
import pandas as pd

from ..materials import REINFORCEMENT_GRADES, STEEL_DENSITY, concrete
from ..project import BeamInput, BeamRoom, DesignSettings
from .crack import crack_width
from .rect import Bars, RectSection

GAMMA_G, GAMMA_Q, PSI2 = 1.35, 1.5, 0.6
CONCRETE_WEIGHT = 25.0  # kN/m³
STEP = 50.0  # mm, steps of the concrete below (or walls) when looking for a size that passes
EDGE = 0.25  # m, stations this far beyond the room's ends are taken too
MIN_LINK_SPACING = 75.0


@dataclass(frozen=True)
class Shape:
    """The channel in the section's coordinates (mm): u across (+ towards ``land``), v up."""

    b: float
    h: float
    width: float  # room
    height: float
    bottom: float  # floor under the room
    top: float  # roof over it (0: open)
    wall_sea: float
    wall_land: float
    land: int  # +1: the land side is +u

    @property
    def void(self) -> tuple[float, float, float, float]:
        if self.land > 0:
            u0, u1 = -self.b / 2 + self.wall_sea, self.b / 2 - self.wall_land
        else:
            u0, u1 = -self.b / 2 + self.wall_land, self.b / 2 - self.wall_sea
        return (u0, u1, -self.h / 2 + self.bottom, self.h / 2 - self.top)

    def walls(self) -> list[tuple[str, float, float, float]]:
        """(name, thickness, u from, u to) of each wall."""
        u0, u1, _, _ = self.void
        lo = ("land", self.wall_land) if self.land < 0 else ("sea", self.wall_sea)
        hi = ("sea", self.wall_sea) if self.land < 0 else ("land", self.wall_land)
        return [(lo[0], lo[1], -self.b / 2, u0), (hi[0], hi[1], u1, self.b / 2)]

    def to_dict(self) -> dict:
        return {
            "width_mm": round(self.width),
            "height_mm": round(self.height),
            "bottom_mm": round(self.bottom),
            "top_mm": round(self.top),
            "wall_sea_mm": round(self.wall_sea),
            "wall_land_mm": round(self.wall_land),
            "land_side": "+" if self.land > 0 else "-",
            "void_mm": [round(x, 1) for x in self.void],
        }


def room_shape(room: BeamRoom, b: float, h: float, land: int) -> tuple[Shape | None, list[str]]:
    notes = []
    top = room.top
    if room.bottom is None:
        bottom, height = h - top - room.height, room.height
    else:
        bottom, height = room.bottom, h - top - room.bottom
        if abs(height - room.height) > 1:
            notes.append(
                f"The room is {height:.0f} mm high: the beam's {h:.0f} mm less {bottom:.0f} mm below"
                + (f" and a {top:.0f} mm roof" if top else "")
                + f" (the room height given, {room.height:.0f} mm, is not used)."
            )
    if room.front_wall is None:
        sea = land_w = (b - room.width) / 2
        width = room.width
    else:
        sea, width = room.front_wall, room.width
        land_w = b - width - sea
    if bottom <= 0 or height <= 0:
        return None, [
            f"{room.name} does not fit: the room ({room.height:.0f} mm) and roof leave no floor in a "
            f"{h:.0f} mm deep beam."
        ]
    if sea <= 0 or land_w <= 0:
        return None, [
            f"{room.name} does not fit: a {room.width:.0f} mm room leaves no wall in a {b:.0f} mm wide beam."
        ]
    return Shape(b, h, width, height, bottom, top, sea, land_w, land), notes


def land_side(geometry: list[dict], centre: float, across: str) -> tuple[int, bool]:
    """+1 when the deck (land) is on the +across side of the beam, and whether a deck was found."""
    for g in geometry:
        box = g.get("box")
        if g.get("type") == "slab" and box and across in box:
            mid = sum(box[across]) / 2
            if abs(mid - centre) > 1e-6:
                return (1 if mid > centre else -1), True
    return -1, False  # the sample berth: land towards −X (−Y)


# --- Bars in the room's section -------------------------------------------------------------------


@dataclass(frozen=True)
class Row:
    """Extra bars: ``count`` bars of ``phi`` in each wall (or across the floor), in rows."""

    count: int
    phi: int

    @property
    def area(self) -> float:
        return self.count * math.pi * self.phi**2 / 4

    @property
    def label(self) -> str:
        return f"{self.count}Ø{self.phi}" if self.count else "none"


def _grown(void, margin: float) -> tuple[float, float, float, float]:
    u0, u1, v0, v1 = void
    return u0 - margin, u1 + margin, v0 - margin, v1 + margin


def split_cage(shape: Shape, bars: Bars, clear: float) -> tuple[Bars, Bars]:
    """(kept, cut): bars closer to the room than ``clear`` + Ø/2 (cover to their surface) are cut."""
    u0, u1, v0, v1 = shape.void
    r = np.sqrt(bars.area / math.pi)
    cut = (
        (bars.u > u0 - clear - r)
        & (bars.u < u1 + clear + r)
        & (bars.v > v0 - clear - r)
        & (bars.v < v1 + clear + r)
    )
    keep = ~cut
    return Bars(bars.u[keep], bars.v[keep], bars.area[keep]), Bars(bars.u[cut], bars.v[cut], bars.area[cut])


def wall_rows(
    shape: Shape, row: Row, v_first: float, clear_edge: float, gap: float, min_clear: float
) -> Bars:
    """``row.count`` bars in each wall, from ``v_first`` down, as many to a row as fit the wall."""
    groups = []
    for _, t, a, b in shape.walls():
        span = t - 2 * clear_edge - row.phi
        per = max(1, int(span // (row.phi + min_clear)) + 1) if span >= 0 else 1
        left, k = row.count, 0
        while left > 0:
            n = min(per, left)
            mid = (a + b) / 2
            u = np.linspace(mid - span / 2, mid + span / 2, n) if n > 1 and span > 0 else np.full(n, mid)
            groups.append(Bars(u, np.full(n, v_first - k * gap), np.full(n, math.pi * row.phi**2 / 4)))
            left -= n
            k += 1
    return Bars.join(*groups) if groups else Bars(np.zeros(0), np.zeros(0), np.zeros(0))


def wall_fit(shape: Shape, phi: float, clear_edge: float, min_clear: float) -> int:
    t = min(w[1] for w in shape.walls())
    span = t - 2 * clear_edge - phi
    return max(1, int(span // (phi + min_clear)) + 1) if span >= 0 else 0


# --- Shear and torsion of the parts ---------------------------------------------------------------


def st_venant(a: float, t: float) -> float:
    """Torsion constant of a rectangle a × t (mm⁴), J ≈ (1/3)(1 − 0.63 t/a)·a·t³ with t the thinner side."""
    a, t = max(a, t), min(a, t)
    return (1 - 0.63 * t / a) * a * t**3 / 3


def _vrdc(fck, gc, bw, d, rho, sigma_cp) -> float:
    k = min(1 + math.sqrt(200 / d), 2.0)
    v_min = 0.035 * k**1.5 * math.sqrt(fck)
    v = max(0.18 / gc * k * (100 * min(rho, 0.02) * fck) ** (1 / 3), v_min) + 0.15 * sigma_cp
    return max(v, 0.0) * bw * d / 1e3


def part_shear(
    *,
    bw: float,
    depth: float,
    d: float,
    V: np.ndarray,
    T: np.ndarray,
    N: np.ndarray,
    rho: float,
    fck: float,
    gc: float,
    alpha_cc: float,
    fctm: float,
    fywd: float,
    fyk: float,
    area_share: float,
    link_phis: list[float],
    step: float,
    cover: float,
    area: float,
    walls_need_links: bool = True,
) -> dict:
    """One part (a wall, the floor or the roof) as a closed thin-walled section with its own closed
    links (two vertical legs): V and T with cot θ from 2.5 down to 1 (6.2.3, 6.3.2(4))."""
    fcd = alpha_cc * fck / gc
    nu1 = 0.6 * (1 - fck / 250)
    z = 0.9 * d
    tef = max(bw * depth / (2 * (bw + depth)), 2 * cover)
    tef = min(tef, bw / 2 - 1e-6, depth / 2 - 1e-6)
    ak = (bw - tef) * (depth - tef)
    uk = 2 * ((bw - tef) + (depth - tef))
    sigma_cp = np.minimum(N * area_share * 1e3 / area, 0.2 * fcd)
    vrdc = np.array(
        [0.0 if n < 0 else _vrdc(fck, gc, bw, d, rho, s) for n, s in zip(N, sigma_cp, strict=True)]
    )
    trdc = alpha_cc * 0.7 * fctm / gc * 2 * ak * tef / 1e6
    concrete_only = V / np.maximum(vrdc, 1e-9) + T / trdc <= 1.0

    def vmax(c):
        return bw * z * nu1 * fcd / (c + 1 / c) / 1e3

    def tmax(c):
        return 2 * nu1 * fcd * ak * tef / (c + 1 / c) / 1e6

    cot = np.full(len(V), 2.5)
    ok = V / vmax(2.5) + T / tmax(2.5) <= 1
    for c in np.linspace(2.5, 1.0, 31):
        m = ~ok & (V / vmax(c) + T / tmax(c) <= 1)
        cot[m] = c
        ok |= m
    crushed = ~ok
    cot[crushed] = 1.0
    # Per leg, per mm along the beam: half the shear's links and all of torsion's.
    need_leg = np.where(concrete_only, 0.0, V * 1e3 / (z * fywd * cot) / 2 + T * 1e6 / (2 * ak * fywd * cot))
    asl = float((T * 1e6 * uk * cot / (2 * ak * fywd)).max()) if len(T) else 0.0
    asl = 0.0 if concrete_only.all() else asl
    need_leg_max = float(need_leg.max()) if len(need_leg) else 0.0
    need_leg_max = max(need_leg_max, 0.08 * math.sqrt(fck) / fyk * bw / 2)
    s_max = min(0.75 * d, 600.0)
    if (T > 0).any() and not concrete_only.all():
        s_max = min(s_max, uk / 8, bw, depth)
    link = None
    for phi in link_phis:
        a_leg = math.pi * phi**2 / 4
        s = math.floor(min(s_max, a_leg / need_leg_max) / step + 1e-9) * step
        if s >= MIN_LINK_SPACING:
            link = {"phi": phi, "spacing_mm": s, "label": f"Ø{phi:g} closed links @ {s:g} mm"}
            break
    if not walls_need_links and concrete_only.all():
        link = None  # a floor or roof the concrete alone carries needs no links of its own
        util = V / np.maximum(vrdc, 1e-9) + T / trdc
    elif link is None:
        util = np.full(len(V), np.inf)
    else:
        a_leg = math.pi * link["phi"] ** 2 / 4
        vrds = 2 * a_leg / link["spacing_mm"] * z * fywd * cot / 1e3
        trds = a_leg / link["spacing_mm"] * 2 * ak * fywd * cot / 1e6
        util = np.where(concrete_only, V / np.maximum(vrdc, 1e-9) + T / trdc, V / vrds + T / trds)
        util = np.maximum(util, V / vmax(cot) + T / tmax(cot))
        util = np.where(crushed, np.inf, util)
    u = float(util.max()) if len(util) else 0.0
    j = int(np.argmax(util)) if len(util) else None
    out = {
        "b_mm": round(bw),
        "h_mm": round(depth),
        "link": link,
        "utilisation": round(u, 3) if math.isfinite(u) else None,
        "passed": bool(math.isfinite(u) and u <= 1 + 1e-6 and not crushed.any()),
        "crushed": bool(crushed.any()),
        "torsion_long_steel_mm2": round(asl),
        "concrete_only": bool(concrete_only.all()),
    }
    if j is not None:
        out["governing"] = {
            "V_kN": round(float(V[j]), 1),
            "T_kNm": round(float(T[j]), 1),
            "VRd_c_kN": round(float(vrdc[j]), 1),
            "VRd_max_kN": round(float(vmax(cot[j])), 1),
            "TRd_max_kNm": round(float(tmax(cot[j])), 1),
            "cot_theta": round(float(cot[j]), 2),
        }
    if link is not None:
        a_leg = math.pi * link["phi"] ** 2 / 4
        out["link"]["kg_per_m"] = round(
            (2 * (bw + depth - 4 * cover)) / link["spacing_mm"] * a_leg / 1e6 * STEEL_DENSITY, 1
        )
    return out


# --- Frame action across the room, per metre ------------------------------------------------------


def _options(settings: DesignSettings) -> list[tuple[float, int, float]]:
    """(mm²/m, Ø, spacing) of one layer of bars per metre, least steel first."""
    r = settings.reinforcement
    out = []
    for phi in [d for d in r.bar_diameters if d >= 12]:
        s = r.max_spacing
        while s >= max(100.0, phi + r.min_clear_spacing) - 1e-9:
            out.append((1000 * math.pi * phi * phi / 4 / s, phi, s))
            s -= r.spacing_step
    return sorted(out)


def per_metre(
    *,
    t: float,
    n: np.ndarray,
    m: np.ndarray,
    qn: np.ndarray,
    qm: np.ndarray,
    cover: float,
    limits: dict[str, float],
    settings: DesignSettings,
    laws,
    conc,
    e_eff: float,
    start: dict[str, float],
    symmetric: bool = False,
) -> dict:
    """Bars per metre on the two faces of a plate ``t`` thick for N (kN/m, compression +) and M
    (kNm/m, + puts the "bottom" face in tension): bending and QP cracks, the failing face stepped up."""
    fyk = REINFORCEMENT_GRADES[settings.reinforcement.grade]
    opts = _options(settings)
    # Each distinct load once; the QP loads that can give the widest crack first (largest moments and
    # tensions each way), then all of them once the bars are set.
    if len(n):
        n, m = np.unique(np.round(np.column_stack([n, m]), 1), axis=0).T
    qp_all = np.unique(np.round(np.column_stack([qn, qm]), 1), axis=0) if len(qn) else np.zeros((0, 2))
    pick = set()
    for side in (qp_all[:, 1] >= 0, qp_all[:, 1] < 0):
        i = np.where(side)[0]
        if len(i):
            pick.update(i[np.argsort(-np.abs(qp_all[i, 1]))[:4]])
            pick.update(i[np.argsort(qp_all[i, 0])[:2]])
    qp_few = qp_all[sorted(pick)] if len(qp_all) else qp_all
    as_min = max(0.26 * conc.fctm / fyk, 0.0013) * 1000 * (t - cover - 10)
    idx = {}
    for f in ("top", "bottom"):
        need = max(as_min, start.get(f, 0.0))
        idx[f] = next((i for i, o in enumerate(opts) if o[0] >= need - 1e-6), len(opts) - 1)
    if symmetric:
        idx["top"] = idx["bottom"] = max(idx.values())

    def depth(o):
        return cover + o[1] / 2

    def section():
        bars = []
        for f, up in (("top", 1), ("bottom", -1)):
            o = opts[idx[f]]
            k = max(2, round(1000 / o[2]))
            bars.append(
                Bars(
                    np.linspace(-500 + o[2] / 2, 500 - o[2] / 2, k),
                    np.full(k, up * (t / 2 - depth(o))),
                    np.full(k, o[0] / k),
                )
            )
        return RectSection(
            1000.0, t, Bars.join(*bars), *laws, strips=80, deduct=settings.partial_factors.deduct_bar_area
        )

    def crack_rows(sec, rows) -> dict:
        cracks: dict[str, dict] = {}
        for nn, mm in rows:
            f = "bottom" if mm >= 0 else "top"
            o = opts[idx[f]]
            res = sec.cracked(float(nn), float(mm), e_eff)
            side = -1 if f == "bottom" else 1
            sel = np.sign(sec.bars.v) == side
            sig = float(-res["stress"][sel].min()) if sel.any() else 0.0
            c = crack_width(
                sig,
                h=t,
                d=t - depth(o),
                x=res["x"],
                b=1000,
                area=o[0],
                phi=o[1],
                cover=cover,
                spacing=o[2],
                fctm=conc.fctm,
                ecm=conc.ecm,
            )
            if f not in cracks or c["wk"] > cracks[f]["wk"]:
                cracks[f] = {**c, "N_kN_per_m": round(float(nn), 1), "M_kNm_per_m": round(float(mm), 1)}
        return cracks

    status = "ok"
    for _ in range(300):
        sec = section()
        u = sec.utilisation(n, m) if len(n) else np.zeros(0)
        fail = None
        if len(u) and u.max() > 1 + 1e-9:
            j = int(np.argmax(u))
            fail = "bottom" if m[j] >= 0 else "top"
        else:
            for rows in (qp_few, qp_all):
                cracks = crack_rows(sec, rows)
                fail = next(
                    (f for f in ("top", "bottom") if f in cracks and cracks[f]["wk"] > limits[f] + 1e-9), None
                )
                if fail is not None or len(rows) == len(qp_all):
                    break
        if fail is None:
            break
        faces = ("top", "bottom") if symmetric else (fail,)
        if any(idx[f] + 1 >= len(opts) for f in faces):
            status = "no bars"
            break
        for f in faces:
            idx[f] += 1
    sec = section()
    u = sec.utilisation(n, m) if len(n) else np.zeros(0)
    cracks = crack_rows(sec, qp_all)
    um = float(u.max()) if len(u) else 0.0

    def lab(o):
        return {"phi": o[1], "spacing_mm": o[2], "as_mm2_per_m": round(o[0]), "label": f"Ø{o[1]} @ {o[2]:g}"}

    crack_out = {
        f: {**c, "limit": limits[f], "passed": c["wk"] <= limits[f] + 1e-9} for f, c in cracks.items()
    }
    return {
        "thickness_mm": round(t),
        "top": lab(opts[idx["top"]]),
        "bottom": lab(opts[idx["bottom"]]),
        "utilisation": round(um, 3) if math.isfinite(um) else None,
        "cracks": crack_out,
        "passed": status == "ok" and um <= 1 + 1e-6 and all(c["passed"] for c in crack_out.values()),
        "d_mm": round(t - depth(opts[idx["bottom"]])),
        "rho": min(opts[idx["top"]][0], opts[idx["bottom"]][0]) / (1000 * (t - cover)),
        "status": status,
    }


# --- The check ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Context:
    beam: BeamInput
    settings: DesignSettings
    cover: float  # to links
    link: float
    cage_bars: Bars  # the beam's cage, every bar at its real size
    top_phi: int
    bottom_phi: int
    bottom_layers: int
    side_phi: int
    dg: float
    laws: tuple
    conc: Any
    e_eff: float
    limits: dict[str, float]
    uls: pd.DataFrame  # stations over the room
    qp: pd.DataFrame
    t_uls: pd.DataFrame  # transverse nodes over the room
    t_qp: pd.DataFrame
    trans_bottom: float  # the beam's transverse bottom bars, mm²/m


def _extremes(f: pd.DataFrame) -> dict:
    return {
        k: {"max": round(float(f[k].max()), 1), "min": round(float(f[k].min()), 1)}
        for k in ("N", "Mv", "Mh", "V", "Vh", "T")
        if k in f and len(f)
    }


def check_shape(ctx: Context, shape: Shape, room: BeamRoom) -> dict:
    """Every check of one room's section with its bars designed."""
    st = ctx.settings
    r = st.reinforcement
    pf = st.partial_factors
    conc = ctx.conc
    fyk = REINFORCEMENT_GRADES[r.grade]
    fywd = fyk / pf.gamma_s
    clear_edge = ctx.cover + ctx.link
    min_clear = max(r.min_clear_spacing, ctx.dg + 5, 20)
    gap_of = lambda phi: phi + max(phi, ctx.dg + 5, 20)  # noqa: E731
    u0, u1, v0, v1 = shape.void
    notes: list[str] = []

    kept, cut = split_cage(shape, ctx.cage_bars, clear_edge)
    cut_top = cut.v > 0
    cut_area_top = float(cut.area[cut_top].sum())
    cut_phi = (
        int(round(math.sqrt(4 * float(cut.area[cut_top].max()) / math.pi))) if cut_top.any() else ctx.top_phi
    )
    lost_bottom = float(cut.area[~cut_top].sum())
    if lost_bottom > 0:
        notes.append(
            f"The floor is too thin for the beam's bottom bars: {lost_bottom:.0f} mm² of them has no cover "
            "under the room."
        )

    # Wall top bars: the top of each wall, from the beam's top bar level down.
    v_wall = (shape.h / 2 - shape.top if shape.top > 0 else shape.h / 2) - clear_edge
    wall_cands = []
    for phi in [d for d in r.bar_diameters if d >= 16]:
        fit = wall_fit(shape, phi, clear_edge, min_clear)
        if fit == 0:
            continue
        rows_max = min(6, max(1, int(shape.height / 2 // gap_of(phi))))
        for n in range(0, fit * rows_max + 1):
            wall_cands.append(Row(n, phi))
    wall_cands.sort(key=lambda x: (x.area, -x.phi))
    # The cut top bars come back at the top of the walls (half in each).
    wi = next((i for i, c in enumerate(wall_cands) if 2 * c.area >= cut_area_top - 1e-6), len(wall_cands) - 1)
    # Extra bottom bars: a layer above the beam's bottom bars, across the width, when the floor has room.
    v_bot_layer = -shape.h / 2 + clear_edge + ctx.bottom_phi / 2 + ctx.bottom_layers * gap_of(ctx.bottom_phi)
    bot_cands = [Row(0, ctx.bottom_phi)]
    for phi in [d for d in r.bar_diameters if d >= 16]:
        if v_bot_layer + phi / 2 + clear_edge > v0 + 1e-6:
            continue
        span = shape.b - 2 * clear_edge - phi
        n_max = int(span // (phi + min_clear)) + 1
        bot_cands += [Row(n, phi) for n in range(2, n_max + 1)]
    bot_cands.sort(key=lambda x: (x.area, -x.phi))
    bi = 0
    # Inside faces of the walls: bars at the largest spacing over the room's height.
    side_phi = max(ctx.side_phi, 16)
    n_side = max(0, math.ceil((shape.height - clear_edge) / r.max_spacing) - 1)

    def inner_sides() -> Bars:
        if n_side == 0:
            return Bars(np.zeros(0), np.zeros(0), np.zeros(0))
        groups = []
        vs = np.linspace(v0, v1, n_side + 2)[1:-1]
        for u in (u0 - clear_edge - side_phi / 2, u1 + clear_edge + side_phi / 2):
            groups.append(Bars(np.full(n_side, u), vs, np.full(n_side, math.pi * side_phi**2 / 4)))
        return Bars.join(*groups)

    def bars_for(wr: Row, br: Row) -> tuple[Bars, Bars, Bars]:
        wall = wall_rows(shape, wr, v_wall - wr.phi / 2, clear_edge, gap_of(wr.phi), min_clear)
        # Keep the wall bars clear of the kept top bars at the same level: step them down a layer.
        if len(kept.v) and len(wall.v):
            top_kept = kept.v.max()
            if top_kept > v_wall - wr.phi - 1e-6:
                wall = Bars(wall.u, wall.v - gap_of(max(wr.phi, ctx.top_phi)), wall.area)
        bottom = (
            Bars.row(br.count, br.phi, v_bot_layer, shape.b / 2 - clear_edge - br.phi / 2)
            if br.count
            else Bars(np.zeros(0), np.zeros(0), np.zeros(0))
        )
        return wall, bottom, inner_sides()

    # Loads.
    uls, qp = ctx.uls, ctx.qp
    n = uls["N"].to_numpy(float)
    mv = uls["Mv"].to_numpy(float)
    mh = uls["Mh"].to_numpy(float)
    V = np.abs(uls["V"].to_numpy(float))
    T = np.abs(uls["T"].to_numpy(float))
    Vh = np.abs(uls["Vh"].to_numpy(float))

    # Shear and torsion (they set the torsion steel taken out of each part before bending).
    parts = [(name, t, "wall") for name, t, _, _ in shape.walls()] + [("floor", shape.bottom, "floor")]
    if shape.top > 0:
        parts.append(("roof", shape.top, "roof"))
    js = {p[0]: st_venant(shape.h if p[2] == "wall" else shape.width, p[1]) for p in parts}
    j_tot = sum(js.values())
    area_tot = shape.b * shape.h - shape.width * shape.height
    wall_t = sum(t for _, t, k in parts if k == "wall")
    d_wall = shape.h - clear_edge - ctx.bottom_phi / 2
    rho_l = float(kept.area[kept.v < 0].sum()) / (shape.b * d_wall)
    link_phis = [ctx.link] + [p for p in r.bar_diameters if p > ctx.link and p <= 25]
    shear = {}
    for name, t, kind in parts:
        tshare = T * js[name] / j_tot
        if kind == "wall":
            area = t * shape.h
            shear[name] = part_shear(
                bw=t,
                depth=shape.h,
                d=d_wall,
                V=V * t / wall_t,
                T=tshare,
                N=n,
                rho=rho_l,
                fck=conc.fck,
                gc=pf.gamma_c,
                alpha_cc=pf.alpha_cc,
                fctm=conc.fctm,
                fywd=fywd,
                fyk=fyk,
                area_share=area / area_tot,
                link_phis=link_phis,
                step=r.spacing_step,
                cover=ctx.cover,
                area=area,
            )
        else:
            area = t * shape.width
            shear[name] = part_shear(
                bw=shape.width,
                depth=t,
                d=max(t - clear_edge - 8, 0.6 * t),
                V=np.zeros(len(T)),
                T=tshare,
                N=n,
                rho=0.002,
                fck=conc.fck,
                gc=pf.gamma_c,
                alpha_cc=pf.alpha_cc,
                fctm=conc.fctm,
                fywd=fywd,
                fyk=fyk,
                area_share=area / area_tot,
                link_phis=link_phis,
                step=r.spacing_step,
                cover=ctx.cover,
                area=area,
                walls_need_links=False,
            )
        shear[name]["T_share"] = round(js[name] / j_tot, 3)
        shear[name]["V_share"] = round(t / wall_t, 3) if kind == "wall" else 0.0
    # Horizontal shear through the floor (and roof): web thickness = their depth, lever arm across.
    hz_t = shape.bottom + shape.top
    d_h = shape.b - clear_edge - side_phi / 2
    vrdc_h = np.array([0.0 if x < 0 else _vrdc(conc.fck, pf.gamma_c, hz_t, d_h, 0.002, 0.0) for x in n])
    horizontal = {
        "V_kN": round(float(Vh.max()), 1) if len(Vh) else 0.0,
        "VRd_c_kN": round(float(vrdc_h[int(np.argmax(Vh))]), 1) if len(Vh) else None,
        "utilisation": round(float((Vh / np.maximum(vrdc_h, 1e-9)).max()), 3) if len(Vh) else 0.0,
    }
    need_h = np.where(Vh > vrdc_h, Vh * 1e3 / (0.9 * d_h * fywd * 2.5), 0.0)  # mm² of leg per mm
    horizontal["legs_mm2_per_m"] = round(float(need_h.max()) * 1000) if len(need_h) else 0

    def weakened(bars: Bars) -> Bars:
        """Each part's bars less its torsion steel (6.3.2(3)), by the part's share of its own bars."""
        area = bars.area.copy()
        for name, _, kind in parts:
            asl = shear[name]["torsion_long_steel_mm2"]
            if not asl:
                continue
            if kind == "wall":
                a, b = next((w[2], w[3]) for w in shape.walls() if w[0] == name)
                sel = (bars.u >= a) & (bars.u <= b)
            elif kind == "floor":
                sel = (bars.u > u0) & (bars.u < u1) & (bars.v < v0)
            else:
                sel = (bars.u > u0) & (bars.u < u1) & (bars.v > v1)
            tot = float(area[sel].sum())
            if tot > 0:
                area[sel] *= max(0.0, 1 - asl / tot)
        return Bars(bars.u, bars.v, area)

    def section(bars: Bars, torsion: bool) -> RectSection:
        return RectSection(
            shape.b,
            shape.h,
            weakened(bars) if torsion else bars,
            *ctx.laws,
            deduct=pf.deduct_bar_area,
            void=shape.void,
        )

    def cracks_of(sec: RectSection, wall: Bars, bottom_extra: Bars, rows: pd.DataFrame) -> dict:
        out: dict[str, dict] = {}
        wall_all = Bars.join(kept_top(), wall)
        for _, q in rows.iterrows():
            m = float(q["Mv"])
            face = "bottom" if m >= 0 else "top"
            res = sec.cracked(float(q["N"]), m, ctx.e_eff)
            if face == "bottom":
                sel = sec.bars.v < v0
                grp = Bars.join(kept_bottom(), bottom_extra)
                bw, phi = shape.b, ctx.bottom_phi
                count = max(1, int(round((kept.v < v0).sum() / max(ctx.bottom_layers, 1))))
                spacing = (shape.b - 2 * clear_edge - phi) / max(count - 1, 1)
            else:
                sel = sec.bars.v > 0
                grp = wall_all
                bw = shape.wall_sea + shape.wall_land
                phi = (
                    int(round(math.sqrt(4 * float(grp.area.max()) / math.pi)))
                    if len(grp.area)
                    else ctx.top_phi
                )
                per_wall = max(1, wall_fit(shape, phi, clear_edge, min_clear))
                spacing = (min(w[1] for w in shape.walls()) - 2 * clear_edge - phi) / max(per_wall - 1, 1)
            sig = float(-res["stress"][sel].min()) if sel.any() else 0.0
            d = shape.h - clear_edge - phi / 2
            c = crack_width(
                sig,
                h=shape.h,
                d=d,
                x=res["x"],
                b=bw,
                area=float(grp.area.sum()),
                phi=phi,
                cover=clear_edge,
                spacing=max(spacing, phi),
                fctm=conc.fctm,
                ecm=conc.ecm,
            )
            if face not in out or c["wk"] > out[face]["wk"]:
                out[face] = {
                    **c,
                    "x_mm": round(res["x"]),
                    "combination": q["combination"],
                    "s": round(float(q["s"]), 2),
                    "N_kN": round(float(q["N"]), 1),
                    "M_kNm": round(m, 1),
                }
        return out

    def kept_top() -> Bars:
        sel = kept.v > 0
        return Bars(kept.u[sel], kept.v[sel], kept.area[sel])

    def kept_bottom() -> Bars:
        sel = kept.v < v0
        return Bars(kept.u[sel], kept.v[sel], kept.area[sel])

    # QP rows that can give the widest crack (largest moments and steel forces each way).
    qp_c = qp
    if len(qp) > 24:
        keep = set()
        for side in (qp["Mv"] >= 0, qp["Mv"] < 0):
            f = qp[side]
            if len(f):
                keep.update(f["Mv"].abs().nlargest(6).index)
                keep.update((-f["N"]).nlargest(4).index)
        qp_c = qp.loc[sorted(keep)]

    status = "ok"
    for _ in range(400):
        wr, br = wall_cands[wi], bot_cands[bi]
        wall, bottom_extra, sides = bars_for(wr, br)
        bars = Bars.join(kept, wall, bottom_extra, sides)
        sec = section(bars, True)
        u = sec.utilisation(n, mv, mh) if len(n) else np.zeros(0)
        grow = None
        if len(u) and u.max() > 1 + 1e-9:
            j = int(np.argmax(u))
            if abs(mh[j]) > 0 and abs(mv[j]) < 1e-6:
                grow = "top"
            else:
                grow = "bottom" if mv[j] >= 0 else "top"
            if n[j] < 0 and grow == "bottom" and bi + 1 >= len(bot_cands):
                grow = "top"
        cracks = None
        if grow is None:
            full = section(bars, False)
            for rows in (qp_c, qp):
                cracks = cracks_of(full, wall, bottom_extra, rows) if len(rows) else {}
                grow = next(
                    (f for f in ("top", "bottom") if f in cracks and cracks[f]["wk"] > ctx.limits[f] + 1e-9),
                    None,
                )
                if grow is not None or len(rows) == len(qp):
                    break
        if grow is None:
            break
        if grow == "top":
            if wi + 1 >= len(wall_cands):
                status = "the walls have no room for more bars"
                break
            wi += 1
        else:
            if bi + 1 >= len(bot_cands):
                status = "the floor has no room for more bottom bars"
                break
            bi += 1
    wr, br = wall_cands[wi], bot_cands[bi]
    wall, bottom_extra, sides = bars_for(wr, br)
    bars = Bars.join(kept, wall, bottom_extra, sides)
    sec = section(bars, True)
    full = section(bars, False)
    u = sec.utilisation(n, mv, mh) if len(n) else np.zeros(0)
    cracks = cracks_of(full, wall, bottom_extra, qp) if len(qp) else {}
    crack_out = {
        f: {**c, "limit": ctx.limits[f], "passed": c["wk"] <= ctx.limits[f] + 1e-9} for f, c in cracks.items()
    }
    um = float(u.max()) if len(u) else 0.0
    bending: dict[str, Any] = {
        "method": "EN 1992-1-1 5.8.9(4) on the section left by the room",
        "utilisation": round(um, 3) if math.isfinite(um) else None,
        "passed": bool(um <= 1 + 1e-6),
    }
    if len(u):
        j = int(np.argmax(u))
        q = uls.iloc[j]
        rv = sec.m_rd("v", np.array([1 if mv[j] >= 0 else -1]), n[j : j + 1])[0]
        rh = sec.m_rd("h", np.array([1 if mh[j] >= 0 else -1]), n[j : j + 1])[0]
        bending["governing"] = {
            "combination": q["combination"],
            "s": round(float(q["s"]), 2),
            "N_kN": round(float(n[j]), 1),
            "Mv_kNm": round(float(mv[j]), 1),
            "Mh_kNm": round(float(mh[j]), 1),
            "MRd_v_kNm": round(float(rv), 1),
            "MRd_h_kNm": round(float(rh), 1),
        }
    # Ductility at the capacity each way, under the largest compression over the room.
    n_c = max(float(n.max()), 0.0) if len(n) else 0.0
    duct = {f: full.ductility("v", s, n_c) for f, s in (("bottom", 1), ("top", -1))}

    # Frame action across the room, per metre.
    frame = frame_action(ctx, shape, room)

    # Detailing at the room's ends.
    lap = st.piles.lap_factor
    diag_phi = max(16, cut_phi)
    corners = {
        "wall_top_bars_past_ends_mm": round(lap * wr.phi) if wr.count else 0,
        "diagonals": f"2Ø{diag_phi} at each corner of the opening, {lap * diag_phi:.0f} mm long",
        "diagonal_phi": diag_phi,
        "diagonal_length_mm": round(lap * diag_phi),
    }

    links_ok = all(s["passed"] for s in shear.values())
    checks = [bending["utilisation"]] + [s["utilisation"] for s in shear.values()]
    checks += [c["wk"] / c["limit"] for c in crack_out.values()]
    checks += [
        frame["floor"]["utilisation"],
        frame["walls"]["utilisation"],
        frame["floor_shear"]["utilisation"],
    ]
    checks += [c["wk"] / c["limit"] for part in ("floor", "walls") for c in frame[part]["cracks"].values()]
    finite = [c for c in checks if c is not None]
    crushed = [k for k, s in shear.items() if s["crushed"]]
    if crushed:
        notes.append(
            f"The concrete crushes under shear and torsion in the {', '.join(crushed)}: thicker walls needed."
        )
    if status != "ok":
        notes.append(f"No bars pass every check: {status}.")
    passed = (
        bending["passed"]
        and links_ok
        and all(c["passed"] for c in crack_out.values())
        and frame["passed"]
        and status == "ok"
        and lost_bottom == 0
    )
    wall_lab = f"{wr.count}Ø{wr.phi} in each wall" if wr.count else "none"
    extra_kg = (
        (2 * wr.area + br.area + 2 * n_side * math.pi * side_phi**2 / 4 - cut_area_top) / 1e6 * STEEL_DENSITY
    )
    return {
        "section": shape.to_dict(),
        "utilisation": round(max(finite), 3) if finite and all(math.isfinite(c) for c in finite) else None,
        "passed": bool(passed),
        "bars": {
            "cut_top": {"count": int(cut_top.sum()), "phi": cut_phi, "area_mm2": round(cut_area_top)},
            "wall_top": {
                "count_each": wr.count,
                "phi": wr.phi,
                "label": wall_lab,
                "area_mm2": round(2 * wr.area),
            },
            "bottom_extra": {"count": br.count, "phi": br.phi, "label": br.label, "area_mm2": round(br.area)},
            "inner_sides": {
                "count_each": n_side,
                "phi": side_phi,
                "label": f"{n_side}Ø{side_phi} on each inside face" if n_side else "none",
            },
            "extra_kg_per_m": round(extra_kg, 1),
            "all": [
                [round(float(a), 1), round(float(b), 1), round(math.sqrt(4 * c / math.pi))]
                for a, b, c in zip(full.bars.u, full.bars.v, full.bars.area, strict=True)
            ],
        },
        "bending": bending,
        "ductility": duct,
        "cracks": crack_out,
        "shear": shear,
        "horizontal_shear": horizontal,
        "frame": frame,
        "corners": corners,
        "notes": notes,
        "status": status,
    }


def frame_action(ctx: Context, shape: Shape, room: BeamRoom) -> dict:
    """The floor and the walls per metre, for the transverse moment across the room (the U-frame)."""
    st = ctx.settings
    pf = st.partial_factors
    conc = ctx.conc
    fyk = REINFORCEMENT_GRADES[st.reinforcement.grade]
    fywd = fyk / pf.gamma_s
    span = shape.width / 1000
    g = CONCRETE_WEIGHT * shape.bottom / 1000
    q_uls = GAMMA_G * g + GAMMA_Q * room.floor_load
    q_qp = g + PSI2 * room.floor_load
    tn, tm = ctx.t_uls["N"].to_numpy(float), ctx.t_uls["M"].to_numpy(float)
    qn, qm = ctx.t_qp["N"].to_numpy(float), ctx.t_qp["M"].to_numpy(float)
    if not len(tm):
        tn, tm = np.zeros(1), np.zeros(1)

    def with_span(nn, mm, q):
        # The floor's own span over the room: wl²/8 sagging, wl²/12 hogging at the walls.
        return (
            np.concatenate([nn, nn]),
            np.concatenate([mm + q * span**2 / 8, mm - q * span**2 / 12]),
        )

    fn, fm = with_span(tn, tm, q_uls)
    fqn, fqm = with_span(qn, qm, q_qp) if len(qm) else (np.zeros(0), np.zeros(0))
    cover = ctx.cover
    floor = per_metre(
        t=shape.bottom,
        n=fn,
        m=fm,
        qn=fqn,
        qm=fqm,
        cover=cover,
        limits=ctx.limits,
        settings=st,
        laws=ctx.laws,
        conc=conc,
        e_eff=ctx.e_eff,
        start={"bottom": ctx.trans_bottom},
    )
    wall_t = min(shape.wall_sea, shape.wall_land)
    lim_w = {"top": min(ctx.limits.values()), "bottom": min(ctx.limits.values())}
    walls = per_metre(
        t=wall_t,
        n=np.zeros(2 * len(tm)),
        m=np.concatenate([np.abs(tm), -np.abs(tm)]),
        qn=np.zeros(2 * len(qm)),
        qm=np.concatenate([np.abs(qm), -np.abs(qm)]) if len(qm) else np.zeros(0),
        cover=cover,
        limits=lim_w,
        settings=st,
        laws=ctx.laws,
        conc=conc,
        e_eff=ctx.e_eff,
        start={},
        symmetric=True,
    )
    # Shear of the floor per metre: the transverse shear and the floor's own span.
    tv = np.abs(ctx.t_uls["V"].to_numpy(float)) if len(ctx.t_uls) else np.zeros(1)
    v_ed = float(tv.max()) + q_uls * span / 2
    d = floor["d_mm"]
    sig = max(float(tn.min()), 0.0) * 1e3 / (1000 * shape.bottom)
    vrdc = (
        0.0
        if float(tn.min()) < 0
        else _vrdc(conc.fck, pf.gamma_c, 1000, d, floor["rho"], min(sig, 0.2 * conc.fck / pf.gamma_c))
    )
    need = 0.0 if v_ed <= vrdc else v_ed * 1e3 / (0.9 * d * fywd * 2.5)  # mm² of leg per mm, per metre width
    fs: dict[str, Any] = {
        "V_kN_per_m": round(v_ed, 1),
        "VRd_c_kN_per_m": round(vrdc, 1),
        "utilisation": round(v_ed / vrdc, 3) if vrdc > 0 else None,
        "needs_links": need > 0,
    }
    if need > 0:
        # Shear links in the floor on a square grid, Ø12 upwards.
        fs["utilisation"] = None
        for phi in (12, 16, 20):
            a = math.pi * phi**2 / 4
            s = (
                math.floor(math.sqrt(a * 1000 / need) / st.reinforcement.spacing_step)
                * st.reinforcement.spacing_step
            )
            s = min(s, 0.75 * d)
            if s >= 100:
                fs["links"] = f"Ø{phi} legs at {s:.0f} × {s:.0f} mm"
                vrds = a * (1000 / s) / s * 0.9 * d * fywd * 2.5 / 1e3
                vmax = (
                    1000
                    * 0.9
                    * d
                    * 0.6
                    * (1 - conc.fck / 250)
                    * pf.alpha_cc
                    * conc.fck
                    / pf.gamma_c
                    / (2.5 + 0.4)
                    / 1e3
                )
                fs["utilisation"] = round(max(v_ed / vrds, v_ed / vmax), 3)
                break
        if fs["utilisation"] is None:
            fs["utilisation"] = math.inf
    fs["passed"] = fs["utilisation"] is not None and fs["utilisation"] <= 1 + 1e-6
    corner = max(floor["top"]["as_mm2_per_m"], walls["top"]["as_mm2_per_m"])
    corner_bar = (
        floor["top"] if floor["top"]["as_mm2_per_m"] >= walls["top"]["as_mm2_per_m"] else walls["top"]
    )
    j = int(np.argmax(np.abs(tm)))
    return {
        "method": "U-frame per metre: the transverse moment through the floor and round its corners "
        "into the walls",
        "M_kNm_per_m": {"max": round(float(tm.max()), 1), "min": round(float(tm.min()), 1)},
        "governing_N_kN_per_m": round(float(tn[j]), 1),
        "floor_load": {
            "g_kPa": round(g, 1),
            "q_kPa": room.floor_load,
            "uls_kPa": round(q_uls, 1),
            "span_m": round(span, 3),
        },
        "floor": floor,
        "walls": walls,
        "floor_shear": fs,
        "corner_bars": {
            "label": f"L-bars {corner_bar['label']} at the inside corners",
            "as_mm2_per_m": corner,
        },
        "passed": floor["passed"] and walls["passed"] and fs["passed"],
    }


def design_room(ctx: Context, room: BeamRoom, shape: Shape) -> dict:
    out = check_shape(ctx, shape, room)
    if out["passed"]:
        return out
    # A floor that passes: the concrete below in 50 mm steps (the room getting lower), then the walls.
    tries: list[tuple[str, Shape]] = []
    t = math.ceil((shape.bottom + 1) / STEP) * STEP
    while t <= shape.h - shape.top - 300 + 1e-6:
        tries.append(("bottom", replace(shape, bottom=t, height=shape.h - shape.top - t)))
        t += STEP
    found = _first_pass(ctx, room, tries)
    if found is not None:
        kind, s = found
        out["suggestion"] = {
            "bottom_mm": round(s.bottom),
            "height_mm": round(s.height),
            "text": f"{s.bottom:.0f} mm of concrete below the room passes (room {s.height:.0f} mm high).",
        }
        return out
    walls = []
    w = STEP
    while shape.width - 2 * w >= 300:
        walls.append(
            (
                "walls",
                replace(
                    shape,
                    width=shape.width - 2 * w,
                    wall_sea=shape.wall_sea + w,
                    wall_land=shape.wall_land + w,
                ),
            )
        )
        w += STEP
    found = _first_pass(ctx, room, walls)
    if found is not None:
        _, s = found
        out["suggestion"] = {
            "wall_sea_mm": round(s.wall_sea),
            "wall_land_mm": round(s.wall_land),
            "width_mm": round(s.width),
            "text": f"A thicker floor does not help; walls of {s.wall_sea:.0f} and {s.wall_land:.0f} mm pass "
            f"(room {s.width:.0f} mm wide).",
        }
    else:
        out["suggestion"] = {
            "text": "No floor or wall thickness that leaves a room at least 300 mm high and wide passes."
        }
    return out


def _first_pass(ctx: Context, room: BeamRoom, tries: list[tuple[str, Shape]]):
    """The first shape that passes, by bisection over the list (thicker passes more)."""
    if not tries or not check_shape(ctx, tries[-1][1], room)["passed"]:
        return None
    lo, hi = -1, len(tries) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if check_shape(ctx, tries[mid][1], room)["passed"]:
            hi = mid
        else:
            lo = mid
    return tries[hi]


def design_rooms(
    beam: BeamInput,
    settings: DesignSettings,
    *,
    b: float,
    h: float,
    cover: float,
    link: float,
    cage_bars: Bars,
    top_phi: int,
    bottom_phi: int,
    bottom_layers: int,
    side_phi: int,
    laws: tuple,
    e_eff: float,
    limits: dict[str, float],
    uls: pd.DataFrame,
    qp: pd.DataFrame,
    t_uls: pd.DataFrame,
    t_qp: pd.DataFrame,
    trans_bottom: float,
    supports: list,
    geometry: list[dict],
    centre: float,
    across: str,
    start: float,
    end: float,
) -> list[dict]:
    """Each room of the beam checked on its own section (see the module's description)."""
    if not beam.rooms:
        return []
    land, found = land_side(geometry, centre, across)
    conc = concrete(beam.concrete)
    out = []
    for room in beam.rooms:
        base = {"name": room.name, "start_m": room.start, "end_m": room.end}
        if room.end < start - 1e-6 or room.start > end + 1e-6:
            continue  # another part of the beam (corner berths)
        shape, notes = room_shape(room, b, h, land)
        if shape is None:
            out.append({**base, "passed": False, "utilisation": None, "notes": notes})
            continue
        if not found:
            notes.append(
                f"No deck in the workbook to tell the land side: the sea side is taken at +{across}."
            )
        lo, hi = room.start - EDGE, room.end + EDGE
        su = uls[(uls["s"] >= lo) & (uls["s"] <= hi)]
        sq = qp[(qp["s"] >= lo) & (qp["s"] <= hi)] if len(qp) else qp
        if su.empty and len(uls):
            near = (uls["s"] - (room.start + room.end) / 2).abs()
            su = uls[near <= near.min() + 1e-6]
            if len(qp):
                nq = (qp["s"] - (room.start + room.end) / 2).abs()
                sq = qp[nq <= nq.min() + 1e-6]
            notes.append("No station inside the room: the nearest one is used.")
        tu = t_uls[(t_uls["s"] >= room.start) & (t_uls["s"] <= room.end)] if len(t_uls) else t_uls
        tq = t_qp[(t_qp["s"] >= room.start) & (t_qp["s"] <= room.end)] if len(t_qp) else t_qp
        under = [
            q
            for q in supports
            if room.start - q.r < q.s < room.end + q.r
            and shape.void[0] - q.r * 1000 < q.t * 1000 < shape.void[1] + q.r * 1000
        ]
        warnings = []
        if under:
            names = ", ".join(sorted({f"{q.element} at {q.s:g} m" for q in under}))
            warnings.append(
                f"{names} is under the room: its bars must anchor in the {shape.bottom:.0f} mm floor. Detail "
                "the connection (the room check does not cover it)."
            )
        ctx = Context(
            beam,
            settings,
            cover,
            link,
            cage_bars,
            top_phi,
            bottom_phi,
            bottom_layers,
            side_phi,
            settings.piles.aggregate_size,
            laws,
            conc,
            e_eff,
            limits,
            su,
            sq,
            tu,
            tq,
            trans_bottom,
        )
        res = design_room(ctx, room, shape)
        length = room.end - room.start
        out.append(
            {
                **base,
                **res,
                "length_m": round(length, 2),
                "stations": int(su["s"].nunique()),
                "actions": _extremes(su),
                "warnings": warnings,
                "notes": notes + res["notes"],
                "extra_steel_kg": round(
                    res["bars"]["extra_kg_per_m"] * length
                    + sum((s.get("link") or {}).get("kg_per_m", 0) for s in res["shear"].values()) * length
                ),
            }
        )
    return out
