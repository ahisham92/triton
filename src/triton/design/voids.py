"""Circular voids in a slab (PVC pipes cast in the deck): where they lie and what they do to the section.

Layout. The voids run along one global axis (X: across the quay, from the front beam to the rear
beam), from ``start_offset`` behind the front beam's face to ``end_offset`` before the rear beam's
face, at a regular spacing across (or at the positions given). A void that would come within
``clear_to_piles`` of a pile in its run is left out, so the slab stays solid along that line of piles.
With ``solid_round_piles`` the voids instead stop that far from each pile's face and start again beyond
it, and no void is left out. A result is in the voided slab when it lies inside the run, within half a
spacing of a void and outside those solid zones.

Section of a 1 m strip, at a depth t below the top:

* bars along the voids (bending about the axis across them): the cut shows the circles, so the
  concrete width is 1000 − n·2√(r² − (t − tc)²) with n = 1000/s voids per metre;
* bars across the voids: the worst cut runs along a void's axis, where only the concrete above
  and below it is left (1000 over the solid depth, 0 over the void's depth).

Bending (ULS): the rectangular block 0.8x at 0.567fck (as the solid slab's K formula, which it
matches while the block stays in the solid flange) on the concrete width at each depth. Where the
block needs more than the depth at K' (x = 0.45d), the compression face's bars take the rest.

Crack width (QP, EN 1992-1-1 7.3.4): the cracked neutral axis and lever arm of the voided compression
zone; ρp,eff on the full width over hc,ef (the voids there would only make the crack spacing smaller).

Shear: the webs between the voids, bw = 1000·(1 − D/s) per metre, for VRd,c, VRd,max and the minimum
links; links only in the webs, at the void spacing across them.

Punching (6.4.2(3), openings): the parts of the control perimeters over a void are left out.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

E_S = 200_000.0
DZ = 2.0  # mm, slices of the section
X_LIM = 0.449  # x/d at K' = 0.167 (z = 0.82d)


class VoidSection:
    """Concrete width (mm per metre of slab) at every depth of a voided 1 m strip."""

    def __init__(self, h: float, diameter: float, centre: float, spacing: float, kind: str):
        self.h, self.D, self.tc, self.s, self.kind = h, diameter, centre, spacing, kind
        n = int(math.ceil(h / DZ))
        self.dz = h / n
        self.t = (np.arange(n) + 0.5) * self.dz  # slice centres below the top
        r = diameter / 2
        inside = np.abs(self.t - centre) < r
        chord = np.where(inside, 2 * np.sqrt(np.clip(r * r - (self.t - centre) ** 2, 0, None)), 0.0)
        self.circles = np.clip(1000.0 - 1000.0 / spacing * chord, 0.0, 1000.0)
        self.b = self.circles if kind == "circles" else np.where(inside, 0.0, 1000.0)

    def from_face(self, face: str) -> tuple[np.ndarray, np.ndarray]:
        """(depth of slice centres from ``face``, widths), from that face inward."""
        b = self.b
        if face == "top":
            return self.t, b
        return self.t, b[::-1]

    def cumulative(self, face: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Depth a from the face, area A(a), first moment S(a) and second moment Q(a) about the face."""
        t, b = self.from_face(face)
        a = np.concatenate([[0.0], (np.arange(len(t)) + 1) * self.dz])
        area = np.concatenate([[0.0], np.cumsum(b * self.dz)])
        s1 = np.concatenate([[0.0], np.cumsum(b * t * self.dz)])
        s2 = np.concatenate([[0.0], np.cumsum(b * t * t * self.dz)])
        return a, area, s1, s2

    def flange(self, face: str) -> float:
        """Solid depth from ``face`` to the void (mm)."""
        return self.tc - self.D / 2 if face == "top" else self.h - self.tc - self.D / 2

    def web_factor(self) -> float:
        return max(1.0 - self.D / self.s, 0.0)


def other(face: str) -> str:
    return "top" if face == "bottom" else "bottom"


def required_as(
    m: np.ndarray,
    n: np.ndarray,
    h: float,
    d: float,
    fck: float,
    fyd: float,
    d2: float | None,
    sec: VoidSection,
    face: str,
) -> tuple:
    """As ``slabs.required_as`` (tension steel on ``face``, K, compression steel) on the voided section."""
    m = np.abs(np.asarray(m, float)) * 1e6
    n = np.asarray(n, float) * 1e3
    ms = m + n * (d - h / 2)
    k = np.maximum(ms, 0) / (1000 * d * d * fck)
    fc = 0.567 * fck
    a, area, s1, _ = sec.cumulative(other(face))
    keep = a <= 0.8 * d + 1e-9
    a, area, s1 = a[keep], area[keep], s1[keep]
    mc = np.maximum.accumulate(fc * (area * d - s1))
    a_lim = 0.8 * X_LIM * d
    area_lim = float(np.interp(a_lim, a, area))
    m_lim = float(np.interp(a_lim, a, mc))
    ms_pos = np.maximum(ms, 0)
    # The depth of block that balances Ms (mc is flat where the void takes the whole width).
    a_req = np.interp(ms_pos, mc, a, right=a[-1])
    a_s = fc * np.interp(a_req, a, area) / fyd - n / fyd
    a_s = np.maximum(a_s, ms / (0.95 * d * fyd) - n / fyd)
    a_s2 = np.zeros_like(a_s)
    if d2 is not None:
        over = ms > m_lim
        a_s2 = np.where(over, (ms - m_lim) / (fyd * (d - d2)), 0.0)
        a_s = np.where(over, fc * area_lim / fyd + a_s2 - n / fyd, a_s)
        k = np.where(over, np.maximum(k, 0.1671), np.minimum(k, 0.167))
    else:
        k = np.where(ms > m_lim, np.maximum(k, 0.1671), k)
    small = ms < 0
    a_s = np.where(small, (-n / 2 + m / (2 * d - h)) / fyd, a_s)
    return np.maximum(a_s, 0.0), k, np.maximum(a_s2, 0.0)


def mrd(
    area_s: float, d: float, h: float, n: float, fcd: float, fyd: float, sec: VoidSection, face: str
) -> float:
    """As ``slabs.strip_mrd`` (kNm/m, tension bars on ``face`` only, block 0.8x at fcd), voided section."""
    fc_needed = area_s * fyd + n * 1e3
    if fc_needed <= 0:
        return 0.0
    a, area, s1, _ = sec.cumulative(other(face))
    c = fcd * area
    if fc_needed > c[-1]:
        return 0.0
    ai = float(np.interp(fc_needed, np.maximum.accumulate(c), a))
    ar = float(np.interp(ai, a, area))
    ybar = float(np.interp(ai, a, s1)) / ar if ar > 0 else 0.0
    return max(fc_needed * (h / 2 - ybar) + area_s * fyd * (d - h / 2), 0.0) / 1e6


def cracked_axis(area_s: float, d: float, ae: float, sec: VoidSection, face: str) -> tuple[float, float]:
    """Cracked neutral axis x from the compression face and lever arm z (mm), tension bars on ``face``."""
    a, area, s1, s2 = sec.cumulative(other(face))
    keep = a <= d
    a, area, s1, s2 = a[keep], area[keep], s1[keep], s2[keep]
    f = a * area - s1 - ae * area_s * (d - a)
    i = int(np.searchsorted(f, 0.0))
    if i <= 0:
        x = 0.0
    elif i >= len(a):
        x = float(a[-1])
    else:
        x = float(a[i - 1] + (a[i] - a[i - 1]) * (0 - f[i - 1]) / (f[i] - f[i - 1]))
    ar, s_1, s_2 = (float(np.interp(x, a, v)) for v in (area, s1, s2))
    force = x * ar - s_1
    ybar = (x * s_1 - s_2) / force if force > 0 else x / 3
    return x, d - ybar


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
    sec: VoidSection,
    face: str,
    k1: float,
    k3: float,
    k4: float,
    kt: float,
    terms: bool = False,
):
    """As ``slabs.crack_widths`` on the voided section (bars on ``face``)."""
    ae = E_S / e_eff
    x, z = cracked_axis(area, d, ae, sec, face)
    m = np.asarray(m, float)
    n = np.asarray(n, float)
    ms = np.abs(m) * 1e6 + n * 1e3 * (d - h / 2)
    sigma = np.maximum((ms / z - n * 1e3) / area, 0.0)
    hc = min(2.5 * (h - d), (h - x) / 3, h / 2)
    rp = area / (1000 * hc)  # the full width: fewer cracks with the voids' smaller area is not counted on
    strain = np.maximum((sigma - kt * conc.fctm / rp * (1 + E_S / conc.ecm * rp)) / E_S, 0.6 * sigma / E_S)
    sr = 1.3 * (h - x) if s > 5 * (c + phi / 2) else k3 * c + k1 * 0.5 * k4 * phi / rp
    if terms:
        return sr * strain, sigma, sr, x
    return sr * strain


# --- Layout -----------------------------------------------------------------------------------------


def _beam_face(beams: list[dict], kind: str, along: str, centre: float) -> float | None:
    b = next((b for b in beams if b.get("type") == kind), None)
    if b is None:
        return None
    lo, hi = b["box"][along]
    return lo if (lo + hi) / 2 > centre else hi


def layout(voids, h: float, box: dict, piles: list[tuple], beams: list[dict]) -> dict[str, Any]:
    """Where the voids lie: axis they run along, the run [from, to] along it, the positions across and the
    ones left out at piles."""
    along = voids.direction
    across = "Y" if along == "X" else "X"
    ai, ci = (0, 1) if along == "X" else (1, 0)
    lo, hi = box[along]
    centre = (lo + hi) / 2
    front = _beam_face(beams, "front_beam", along, centre)
    rear = _beam_face(beams, "rear_beam", along, centre)
    if front is None and rear is None:
        ends = [lo + voids.start_offset, hi - voids.end_offset]
        run_from = f"{voids.start_offset:g} m and {voids.end_offset:g} m in from the slab's edges"
    else:
        if front is None:
            front = hi if abs(rear - lo) < abs(rear - hi) else lo
        if rear is None:
            rear = hi if abs(front - lo) < abs(front - hi) else lo
        sgn = 1.0 if rear > front else -1.0
        ends = [front + sgn * voids.start_offset, rear - sgn * voids.end_offset]
        run_from = (
            f"{voids.start_offset:g} m behind the front beam's face to {voids.end_offset:g} m before the "
            "rear beam's face"
        )
    run = [round(max(min(ends), lo), 3), round(min(max(ends), hi), 3)]
    s = voids.spacing / 1000
    c0, c1 = box[across]
    if voids.positions:
        pos = sorted(float(p) for p in voids.positions)
    else:
        first = voids.first_at if voids.first_at is not None else c0 + s / 2
        k0 = math.ceil((c0 + voids.diameter / 2000 - first) / s - 1e-9)
        pos = []
        k = k0
        while first + k * s <= c1 - voids.diameter / 2000 + 1e-9:
            pos.append(round(first + k * s, 3))
            k += 1
    r = voids.diameter / 2000
    kept, dropped = [], []
    solid = voids.solid_round_piles
    for p in pos:
        if solid is not None:  # the voids stop short of every pile instead
            kept.append(p)
            continue
        clash = next(
            (
                (px, py)
                for px, py, pr in piles
                if run[0] - pr <= (px, py)[ai] <= run[1] + pr
                and abs(p - (px, py)[ci]) < pr + r + voids.clear_to_piles / 1000 - 1e-9
            ),
            None,
        )
        (dropped if clash else kept).append(p)
    tc = voids.centre_depth if voids.centre_depth is not None else h / 2
    return {
        "along": along,
        "across": across,
        "run": run,
        "run_from": run_from,
        "positions": kept,
        "left_out": dropped,
        "diameter_mm": voids.diameter,
        "spacing_mm": voids.spacing,
        "centre_depth_mm": tc,
        "flange_top_mm": round(tc - voids.diameter / 2, 1),
        "flange_bottom_mm": round(h - tc - voids.diameter / 2, 1),
        "web_mm": round(voids.spacing - voids.diameter, 1),
        "solid_round_piles_m": solid,
        "piles": [[round(px, 3), round(py, 3), pr] for px, py, pr in piles] if solid is not None else [],
    }


def _near_piles(lay: dict[str, Any], x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Points in the solid zone round a pile (where the voids stop short of it)."""
    out = np.zeros(len(x), bool)
    for px, py, pr in lay.get("piles") or []:
        out |= np.hypot(x - px, y - py) <= pr + lay["solid_round_piles_m"] + 1e-9
    return out


def mask(lay: dict[str, Any], x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Points in the voided slab: inside the run and within half a spacing of a void."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    if not lay["positions"]:
        return np.zeros(len(x), bool)
    a, c = (x, y) if lay["along"] == "X" else (y, x)
    pos = np.array(lay["positions"])
    near = np.abs(c[:, None] - pos[None, :]).min(axis=1) <= lay["spacing_mm"] / 2000 + 1e-9
    return near & (a >= lay["run"][0] - 1e-9) & (a <= lay["run"][1] + 1e-9) & ~_near_piles(lay, x, y)


def perimeter_over_voids(lay: dict[str, Any], x: float, y: float, radius: float) -> float:
    """Share (0 to 1) of a circle of ``radius`` (m) round (x, y) that lies over a void."""
    if not lay["positions"]:
        return 0.0
    th = np.linspace(0, 2 * math.pi, 720, endpoint=False)
    px, py = x + radius * np.cos(th), y + radius * np.sin(th)
    a, c = (px, py) if lay["along"] == "X" else (py, px)
    pos = np.array(lay["positions"])
    over = np.abs(c[:, None] - pos[None, :]).min(axis=1) < lay["diameter_mm"] / 2000
    over &= (a >= lay["run"][0]) & (a <= lay["run"][1]) & ~_near_piles(lay, px, py)
    return float(over.mean())


def void_volume(lay: dict[str, Any], box: dict) -> float:
    """m³ of void inside the slab's plan box."""
    length = max(lay["run"][1] - lay["run"][0], 0.0)
    total = len(lay["positions"]) * length
    ci = 1 if lay["along"] == "X" else 0
    for pile in lay.get("piles") or []:  # the lengths cut out round the piles
        big = pile[2] + lay["solid_round_piles_m"]
        if not lay["run"][0] - big < pile[1 - ci] < lay["run"][1] + big:
            continue
        for p in lay["positions"]:
            e = abs(p - pile[ci])
            if e < big:
                total -= 2 * math.sqrt(big * big - e * e)
    return max(total, 0.0) * math.pi * (lay["diameter_mm"] / 2000) ** 2
