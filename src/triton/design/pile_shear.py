"""Shear in circular piles to EN 1992-1-1 6.2, and the links (hoops).

Circular section as an equivalent rectangle (Feltham, The Structural Engineer,
2004): width bw = D and effective depth d = r + 2·rs/π, with rs the radius of
the bar circle and z = 0.9·d. Half the longitudinal bars are taken as the
tension steel for ρl.

* VRd,c from 6.2.2(1) with CRd,c = 0.18/γc, k1 = 0.15 and σcp = NEd/Ac
  (compression +, at most 0.2·fcd), and at least vmin. Where the pile is in
  tension the concrete is given no shear resistance (project rule), so links
  carry all of VEd.
* Where VEd > VRd,c, links from 6.2.3: cot θ as large as possible up to 2.5
  while VEd <= VRd,max. A circular hoop is taken as two legs, each π/4
  effective, so VRd,s = (π/2)·(Asw/s)·z·fywd·cot θ with Asw one hoop bar.
* Detailing to 9.5.3: hoop diameter at least max(6 mm, φl,max/4), spacing at
  most min(20·φl,min, D, 400 mm), times 0.6 for a length D below the pile
  head (slab above) and over laps of bars larger than 14 mm.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..elements import CombinationType
from ..materials import REINFORCEMENT_GRADES, STEEL_DENSITY, concrete
from ..project import DesignSettings, PileInput

MIN_LINK_SPACING = 75.0  # mm, practical minimum pitch
STEP = 0.05  # m, level grid
MIN_LINK_ZONE = 1.0  # m, shorter zones join a neighbour at the closer spacing


@dataclass(frozen=True)
class CageZone:
    """Longitudinal steel over one length of pile, as the shear check needs it."""

    top: float
    bottom: float
    area: float  # mm², all bars
    bar_radius: float  # mm, outer row
    phi_max: float
    phi_min: float
    lap_below: float  # m, lap of this zone's bars below ``bottom``


def _factors(settings: DesignSettings, accidental: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    pf = settings.partial_factors
    gc = np.where(accidental, pf.gamma_c_accidental, pf.gamma_c)
    gs = np.where(accidental, pf.gamma_s_accidental, pf.gamma_s)
    return gc, gs


def design_shear(
    pile: PileInput, settings: DesignSettings, loads: pd.DataFrame, zones: list[CageZone]
) -> dict:
    D = pile.diameter
    r = D / 2
    ac = math.pi * r * r
    fck = concrete(pile.concrete).fck
    fyk = REINFORCEMENT_GRADES[settings.reinforcement.grade]
    alpha_cc = settings.partial_factors.alpha_cc
    head, toe = zones[0].top, zones[-1].bottom
    link = pile.link_diameter
    asw = math.pi * link * link / 4

    # Cage at each load's level.
    z = loads["Z"].to_numpy()
    idx = np.zeros(len(z), dtype=int)
    for i, zn in enumerate(zones):
        idx[(z <= zn.top + 1e-9) & (z >= zn.bottom - 1e-9) & (idx == 0)] = i
    area = np.array([zones[i].area for i in idx])
    rs = np.array([zones[i].bar_radius for i in idx])

    special = [CombinationType.SEISMIC.value, CombinationType.ACCIDENTAL.value]
    accidental = loads["category"].isin(special).to_numpy()
    gc, gs = _factors(settings, accidental)
    fcd = alpha_cc * fck / gc
    fywd = fyk / gs

    v_ed = np.hypot(loads["Q_12"].to_numpy(), loads["Q_13"].to_numpy())  # kN
    n_ed = loads["N"].to_numpy()  # kN, compression +
    bw = D
    d = r + 2 * rs / math.pi
    zlev = 0.9 * d
    k = np.minimum(1 + np.sqrt(200 / d), 2.0)
    rho = np.minimum(area / 2 / (bw * d), 0.02)
    sigma_cp = np.minimum(n_ed * 1e3 / ac, 0.2 * fcd)
    v_min = 0.035 * k**1.5 * math.sqrt(fck)
    crdc = 0.18 / gc
    vrdc = np.maximum(crdc * k * (100 * rho * fck) ** (1 / 3), v_min) + 0.15 * sigma_cp
    vrdc = np.maximum(vrdc, 0.0) * bw * d / 1e3  # kN
    # Project rule: no concrete contribution where the pile is in tension, so links carry all the shear.
    vrdc = np.where(n_ed < 0, 0.0, vrdc)

    nu1 = 0.6 * (1 - fck / 250)

    def vrd_max(cot: float) -> np.ndarray:
        return bw * zlev * nu1 * fcd / (cot + 1 / cot) / 1e3

    # Largest cot θ (<= 2.5) with VEd <= VRd,max; NaN where even θ = 45° crushes.
    cot = np.full(len(v_ed), 2.5)
    vmax25 = vrd_max(2.5)
    need = v_ed > vmax25
    if need.any():
        # VRd,max = A / (c + 1/c); solve c + 1/c = A / V for the root >= 1.
        a = bw * zlev[need] * nu1 * fcd[need] / 1e3 / v_ed[need]
        disc = a * a - 4
        cot[need] = np.where(disc >= 0, (a + np.sqrt(np.maximum(disc, 0))) / 2, np.nan)
    crushed = np.isnan(cot)
    cot = np.clip(np.nan_to_num(cot, nan=1.0), 1.0, 2.5)
    # Required hoop area per mm of pile (one bar), where links carry the shear.
    asw_s = np.where(v_ed > vrdc, v_ed * 1e3 / ((math.pi / 2) * zlev * fywd * cot), 0.0)

    # Detailing limits.
    phi_l_max = max(zn.phi_max for zn in zones)
    phi_l_min = min(zn.phi_min for zn in zones)
    s_max = min(20 * phi_l_min, D, 400.0)
    notes = []
    link_min = max(6.0, phi_l_max / 4)
    if link < link_min - 1e-9:
        notes.append(f"Links Ø{link:g} are below the 9.5.3(1) minimum Ø{link_min:g} for Ø{phi_l_max:g} bars.")
    if crushed.any():
        notes.append(
            "The concrete strut crushes (VEd > VRd,max at θ = 45°): a larger pile or concrete is needed."
        )

    # Required spacing on a level grid, then zones of equal spacing.
    n = max(1, math.ceil((head - toe) / STEP - 1e-9))
    band = np.clip(((head - z) / STEP).astype(int), 0, n - 1)
    need_band = np.zeros(n)
    np.maximum.at(need_band, band, asw_s)
    levels = head - (np.arange(n) + 0.5) * STEP
    reduced = levels > head - D / 1000  # below the slab
    for zn in zones[:-1]:
        if zn.phi_max > 14 and zn.lap_below > 0:
            reduced |= (levels <= zn.bottom) & (levels >= zn.bottom - zn.lap_below)
    step = settings.reinforcement.spacing_step
    spacing = np.empty(n)
    reason = np.empty(n, dtype=object)
    for j in range(n):
        limit = s_max * (0.6 if reduced[j] else 1.0)
        s_req = asw / need_band[j] if need_band[j] > 0 else math.inf
        s = min(limit, s_req)
        s = max(MIN_LINK_SPACING, math.floor(s / step + 1e-9) * step)
        spacing[j] = s
        if s_req < limit:
            reason[j] = "shear"
        elif reduced[j]:
            reason[j] = "near slab" if levels[j] > head - D / 1000 else "at lap"
        else:
            reason[j] = "minimum"
    if (need_band > 0).any() and asw / need_band.max() < MIN_LINK_SPACING - 1e-9:
        notes.append(f"Ø{link:g} links would be closer than {MIN_LINK_SPACING:g} mm: use larger links.")

    out_zones = _zones(head, toe, spacing, reason, link)
    hoop_len = math.pi * (D - 2 * pile.cover - link) / 1000  # m
    weight = sum((zz["top"] - zz["bottom"]) * 1000 / zz["spacing_mm"] * hoop_len for zz in out_zones)
    weight *= asw / 1e6 * STEEL_DENSITY
    provided = np.array(
        [next(zz["spacing_mm"] for zz in out_zones if zz["bottom"] - 1e-9 <= lv) for lv in levels]
    )
    vrds = _vrds(asw, provided[band], zlev, fywd, cot)
    util = v_ed / np.maximum(np.where(v_ed > vrdc, vrds, vrdc), 1e-9)
    util = np.where(crushed, np.inf, np.maximum(util, v_ed / vrd_max(cot)))
    i = int(np.argmax(util))
    g = loads.iloc[i]
    return {
        "method": "EN 1992-1-1 6.2, bw = D, d = r + 2rs/π, circular hoops (π/2)",
        "utilisation": round(float(util[i]), 3) if math.isfinite(util[i]) else None,
        "passed": bool(np.all(util <= 1.0 + 1e-6)) and not notes,
        "governing": {
            "combination": g["combination"],
            "z": round(float(g["Z"]), 2),
            "V_kN": round(float(v_ed[i]), 1),
            "N_kN": round(float(n_ed[i]), 1),
            "VRd_c_kN": round(float(vrdc[i]), 1),
            "VRd_max_kN": round(float(vrd_max(cot[i])[i]), 1),
            "cot_theta": round(float(cot[i]), 2),
        },
        "max_spacing_mm": s_max,
        "min_link_diameter_mm": link_min,
        "zones": out_zones,
        "links_kg": round(weight, 1),
        "profile": _profile(levels, band, v_ed, vrdc),
        "notes": notes,
    }


def _zones(head: float, toe: float, spacing: np.ndarray, reason: np.ndarray, link: float) -> list[dict]:
    """Zones of equal link spacing, each at least MIN_LINK_ZONE long (except a short pile)."""
    zones: list[list] = []  # [top, bottom, spacing, reason]
    for j, (s, why) in enumerate(zip(spacing, reason, strict=True)):
        top, bottom = head - j * STEP, max(head - (j + 1) * STEP, toe)
        if zones and zones[-1][2] == s and zones[-1][3] == why:
            zones[-1][1] = bottom
        else:
            zones.append([top, bottom, s, why])

    def merge_equal(zs: list[list]) -> list[list]:
        out = [zs[0]]
        for z in zs[1:]:
            if z[2] == out[-1][2] and z[3] == out[-1][3]:
                out[-1][1] = z[1]
            else:
                out.append(z)
        return out

    length = lambda z: z[0] - z[1]  # noqa: E731
    while len(zones) > 1:
        i = min(range(len(zones)), key=lambda k: length(zones[k]))
        if length(zones[i]) >= MIN_LINK_ZONE - 1e-9:
            break
        nbrs = [k for k in (i - 1, i + 1) if 0 <= k < len(zones)]
        if zones[i][2] < min(zones[k][2] for k in nbrs):
            # A short zone of closer links grows into its looser neighbour to the minimum length.
            k = max(nbrs, key=lambda k: (zones[k][2], length(zones[k])))
            need = MIN_LINK_ZONE - length(zones[i])
            if length(zones[k]) - need < MIN_LINK_ZONE - 1e-9:
                zones[k][2:] = zones[i][2:]  # the neighbour is short too: it takes the closer links
            elif k > i:
                zones[i][1] -= need
                zones[k][0] = zones[i][1]
            else:
                zones[i][0] += need
                zones[k][1] = zones[i][0]
        else:
            # A short zone of looser links takes the spacing of its closest-spaced neighbour.
            k = min(nbrs, key=lambda k: zones[k][2])
            zones[i][2:] = zones[k][2:]
        zones = merge_equal(zones)
    return [
        {
            "top": round(t, 2),
            "bottom": round(b, 2),
            "link": f"Ø{link:g} @ {s:g}",
            "spacing_mm": float(s),
            "reason": why,
        }
        for t, b, s, why in zones
    ]


def _vrds(asw: float, s: np.ndarray, z: np.ndarray, fywd: np.ndarray, cot: np.ndarray) -> np.ndarray:
    return (math.pi / 2) * asw / s * z * fywd * cot / 1e3


def _profile(levels: np.ndarray, band: np.ndarray, v_ed: np.ndarray, vrdc: np.ndarray) -> list[dict]:
    """Largest VEd and the smallest VRd,c at each 0.1 m level, for a chart."""
    frame = pd.DataFrame({"z": levels[band].round(1), "v": v_ed, "vrdc": vrdc})
    g = frame.groupby("z").agg(v=("v", "max"), vrdc=("vrdc", "min")).sort_index(ascending=False)
    return [
        {"z": float(z), "V": round(float(r.v), 1), "VRd_c": round(float(r.vrdc), 1)} for z, r in g.iterrows()
    ]
