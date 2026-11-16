"""Crack widths of circular piles under QP loads, EN 1992-1-1 7.3.4.

The bar stresses come from the elastic cracked section: concrete with no tensile strength and
the long-term modulus Ec,eff = Ecm / (1 + φ), bars at Es, under N (compression +) and the
resultant moment M. The section is taken with a bar of the outer row on the bending axis, so
one bar sits at the extreme tension fibre.

The crack width is worked out at that bar (7.3.4, see ``crack.crack_width``):

* in bending, Ac,eff is the circular segment of depth hc,ef = min(2.5(h − d), (h − x)/3, h/2)
  at the tension face and As the outer bars inside it;
* when the whole section is in tension, Ac,eff is the ring of depth min(2.5(h − d), h/2) round
  the perimeter and As the whole outer row, with k2 = (ε1 + ε2)/2ε1.

The bar spacing for sr,max is the centre spacing of the outer row round its circle. No crack
width is worked out where the pile has a steel casing.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .crack import E_S, K1, K3, K4, KT

STRIPS = 200


def cracked_stress(
    diameter: float,
    bar_depth: np.ndarray,
    bar_area: np.ndarray,
    n: np.ndarray,
    m: np.ndarray,
    e_c: float,
    iterations: int = 40,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Cracked elastic circle under N (kN, compression +) and M (kNm, >= 0), vectorised over loads.

    ``bar_depth`` is each bar's depth below the compressed fibre (mm). Returns the strain at the
    compressed fibre, the curvature (1/mm, strain falls by it per mm of depth) and the neutral
    axis depth x (mm; 0 when all in tension, the diameter when all in compression).
    """
    r = diameter / 2
    edges = np.linspace(0.0, diameter, STRIPS + 1)

    def above(z: np.ndarray) -> np.ndarray:
        z = np.clip(z, -r, r)
        return r * r * np.arccos(z / r) - z * np.sqrt(r * r - z * z)

    ya = (edges[:-1] + edges[1:]) / 2
    area = above(edges[:-1] - r) - above(edges[1:] - r)
    lever_c, lever_s = r - ya, r - bar_depth
    es = E_S * bar_area
    target = np.column_stack([np.asarray(n, float) * 1e3, np.abs(np.asarray(m, float)) * 1e6])
    e0 = np.zeros(len(target))
    k = np.zeros(len(target))
    live = np.ones((len(target), STRIPS), bool)
    for _ in range(iterations):
        ec = np.where(live, e_c, 0.0) * area
        k11 = ec.sum(1) + es.sum()
        k12 = -(ec * ya).sum(1) - (es * bar_depth).sum()
        k21 = (ec * lever_c).sum(1) + (es * lever_s).sum()
        k22 = -(ec * ya * lever_c).sum(1) - (es * bar_depth * lever_s).sum()
        det = k11 * k22 - k12 * k21
        det = np.where(np.abs(det) < 1e-30, 1e-30, det)
        e0 = (target[:, 0] * k22 - k12 * target[:, 1]) / det
        k = (k11 * target[:, 1] - k21 * target[:, 0]) / det
        new = (e0[:, None] - k[:, None] * ya[None, :]) > 0
        if (new == live).all():
            break
        live = new
    bottom = e0 - k * diameter
    x = np.where(
        (e0 <= 0) & (bottom <= 0),
        0.0,
        np.where((k <= 0) | (bottom >= 0), diameter, np.clip(e0 / np.where(k > 0, k, 1), 0, diameter)),
    )
    return e0, k, x


def _segment(radius: float, depth: np.ndarray) -> np.ndarray:
    """Area of a circle's segment of the given depth (mm²)."""
    t = np.clip(radius - depth, -radius, radius)
    return radius * radius * np.arccos(t / radius) - t * np.sqrt(radius * radius - t * t)


def pile_crack_widths(
    diameter: float,
    rings: list[tuple[int, float, float]],
    qp: pd.DataFrame,
    *,
    fctm: float,
    ecm: float,
    creep: float,
) -> pd.DataFrame:
    """wk (mm) and its terms for each QP row (N kN compression +, M kNm) of a cage.

    ``rings`` are (count, bar diameter, radius of the bar centres), the outer row first.
    """
    if qp.empty:
        return pd.DataFrame(columns=["wk", "sigma_s", "sr_max", "rho_eff", "x"])
    depth, areas = [], []
    for count, phi, radius in rings:
        ang = 2 * math.pi * np.arange(count) / count
        depth.append(diameter / 2 - radius * np.cos(ang))
        areas.append(np.full(count, math.pi * phi * phi / 4))
    bar_depth, bar_area = np.concatenate(depth), np.concatenate(areas)
    e_eff = ecm / (1 + creep)
    n, m = qp["N"].to_numpy(float), qp["M"].to_numpy(float)
    e0, k, x = cracked_stress(diameter, bar_depth, bar_area, n, m, e_eff)

    count, phi, radius = rings[0]
    R = diameter / 2
    d = R + radius  # extreme outer bar
    sigma = -E_S * (e0 - k * d)  # tension +
    cover = R - radius - phi / 2
    spacing = 2 * math.pi * radius / count
    outer_depth = depth[0]
    a_bar = math.pi * phi * phi / 4

    all_tension = x <= 0
    hc_bend = np.minimum(np.minimum(2.5 * (diameter - d), (diameter - x) / 3), R)
    hc_ring = min(2.5 * (diameter - d), R)
    hc_bend = np.maximum(hc_bend, 1.0)  # a wholly compressed section has no tension zone; wk is 0 there
    ac = np.where(all_tension, math.pi * (R * R - (R - hc_ring) ** 2), _segment(R, hc_bend))
    in_seg = (outer_depth[None, :] >= diameter - hc_bend[:, None] - 1e-6).sum(axis=1)
    a_s = np.where(all_tension, count * a_bar, np.maximum(in_seg, 1) * a_bar)
    rho = a_s / ac

    # k2 from the strains at the two extreme fibres when the section is wholly in tension.
    e_top, e_bot = -e0, -(e0 - k * diameter)
    e1, e2 = np.maximum(e_top, e_bot), np.minimum(e_top, e_bot)
    k2 = np.where(all_tension & (e1 > 0), (e1 + np.maximum(e2, 0)) / (2 * np.where(e1 > 0, e1, 1)), 0.5)

    ae = E_S / ecm
    strain = np.maximum((sigma - KT * fctm / rho * (1 + ae * rho)) / E_S, 0.6 * sigma / E_S)
    if spacing > 5 * (cover + phi / 2):
        sr = 1.3 * (diameter - x)
    else:
        sr = K3 * cover + K1 * k2 * K4 * phi / rho
    wk = np.where(sigma > 0, sr * strain, 0.0)
    return pd.DataFrame(
        {
            "wk": wk,
            "sigma_s": np.maximum(sigma, 0.0),
            "sr_max": np.where(sigma > 0, sr, np.nan),
            "rho_eff": rho,
            "x": x,
        },
        index=qp.index,
    )
