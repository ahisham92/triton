"""Rectangular reinforced concrete sections to EN 1992-1-1: N with biaxial bending, and cracked stresses.

The N–M curve about each axis uses the same material laws and strain limits as
the circular sections (``circular.ConcreteLaw`` and ``SteelLaw``, 6.1(5)), from
horizontal strips of the rectangle and the bars at their own depths. The
curves are worked out for both senses of each moment, since the top and bottom
bars need not be equal.

Biaxial bending uses 5.8.9(4):

    (M_Edv / M_Rdv)^a + (M_Edh / M_Rdh)^a <= 1

with M_Rd about each axis at the acting N, and a = 1 for N_Ed / N_Rd <= 0.1,
1.5 at 0.7 and 2.0 at 1.0 (linear in between), N_Rd = Ac·fcd + As·fyd. The
utilisation reported is the left-hand side to the power 1/a, which scales
with the moments at the acting N, or N over the axial capacity when that is
larger.

A rectangular hole can be taken out of the section (``void``: u from, u to, v from,
v to, mm), e.g. a room cut into a beam; the strips then have the width that is
left at their level.

Coordinates: u across the width (mm, from the centre), v up (mm, from the
centre). N is positive in compression. M_v (vertical bending) is positive when
it compresses the top (sagging); M_h is positive when it compresses the +u side.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .circular import ConcreteLaw, SteelLaw


@dataclass(frozen=True)
class Bars:
    u: np.ndarray  # mm
    v: np.ndarray  # mm
    area: np.ndarray  # mm²

    @classmethod
    def join(cls, *groups: Bars) -> Bars:
        return cls(*(np.concatenate([getattr(g, k) for g in groups]) for k in ("u", "v", "area")))

    @classmethod
    def row(cls, count: int, phi: float, v: float, half_width: float) -> Bars:
        """``count`` bars of diameter ``phi`` at height v, evenly spread over ±half_width."""
        u = np.linspace(-half_width, half_width, count) if count > 1 else np.zeros(count)
        return cls(u, np.full(count, v), np.full(count, math.pi * phi * phi / 4))

    @classmethod
    def column(cls, count: int, phi: float, u: float, half_height: float) -> Bars:
        """``count`` bars at width position u between ±half_height, ends excluded (corners are elsewhere)."""
        v = np.linspace(-half_height, half_height, count + 2)[1:-1]
        return cls(np.full(count, u), v, np.full(count, math.pi * phi * phi / 4))

    @property
    def total(self) -> float:
        return float(self.area.sum())


@dataclass(frozen=True)
class RectSection:
    b: float  # mm, width
    h: float  # mm, depth
    bars: Bars
    concrete: ConcreteLaw
    steel: SteelLaw
    strips: int = 200
    deduct: bool = True  # deduct the concrete displaced by the bars
    void: tuple[float, float, float, float] | None = None  # (u0, u1, v0, v1) mm, no concrete inside
    _cache: dict = field(default_factory=dict, compare=False, hash=False, repr=False)

    @property
    def area_concrete(self) -> float:
        if self.void is None:
            return self.b * self.h
        u0, u1, v0, v1 = self.void
        return self.b * self.h - (u1 - u0) * (v1 - v0)

    def _strip_areas(self, axis: str, sign: int) -> np.ndarray:
        """Concrete area of each strip (mm²), strips counted from the compressed face."""
        H, B, _ = self._frame(axis, sign)
        t = H / self.strips
        full = np.full(self.strips, B * t)
        if self.void is None:
            return full
        u0, u1, v0, v1 = self.void
        # The void's extent along the strips' depth (a) and across them (w), in the section's coordinates.
        (a0, a1), w = ((v0, v1), u1 - u0) if axis == "v" else ((u0, u1), v1 - v0)
        lo = np.arange(self.strips) * t
        # Strip i spans y in [lo, lo + t] from the compressed face; its coordinate is sign·(H/2 − y).
        c_hi, c_lo = sign * (H / 2 - lo), sign * (H / 2 - lo - t)
        c_lo, c_hi = np.minimum(c_lo, c_hi), np.maximum(c_lo, c_hi)
        overlap = np.clip(np.minimum(c_hi, a1) - np.maximum(c_lo, a0), 0.0, None)
        return full - w * overlap

    def _frame(self, axis: str, sign: int) -> tuple[float, float, np.ndarray]:
        """(depth H, strip width B, bar depths below the compression fibre) for bending about ``axis``."""
        if axis == "v":
            return self.h, self.b, self.h / 2 - sign * self.bars.v
        return self.b, self.h, self.b / 2 - sign * self.bars.u

    def _resultants(self, axis: str, sign: int, eps_top: np.ndarray, curvature: np.ndarray):
        H, B, yb = self._frame(axis, sign)
        y = (np.arange(self.strips) + 0.5) * H / self.strips
        a = self._strip_areas(axis, sign)
        eps_c = eps_top[:, None] - curvature[:, None] * y[None, :]
        sc = self.concrete.stress(eps_c)
        n_c = (sc * a).sum(axis=1)
        m_c = (sc * a * (H / 2 - y)).sum(axis=1)
        eps_s = eps_top[:, None] - curvature[:, None] * yb[None, :]
        ss = self.steel.stress(eps_s)
        if self.deduct:
            ss = ss - self.concrete.stress(eps_s)
        n_s = (ss * self.bars.area).sum(axis=1)
        m_s = (ss * self.bars.area * (H / 2 - yb)).sum(axis=1)
        return n_c + n_s, m_c + m_s

    def curve(self, axis: str, sign: int, points: int = 160) -> np.ndarray:
        """N–M curve (kN, kNm) for bending about ``axis`` ('v' or 'h') in the sense ``sign`` (+1 or -1).

        Rows run from pure compression to pure tension; M is the moment in that sense.
        """
        key = (axis, sign, points)
        if key in self._cache:
            return self._cache[key]
        H = self.h if axis == "v" else self.b
        ecu, ec2 = self.concrete.eps_cu2, self.concrete.eps_c2
        x_beyond = H * (1 + np.geomspace(1e-3, 50, points // 3))[::-1]
        x_within = H * np.geomspace(1.0, 2e-4, points - points // 3)
        x = np.concatenate([x_beyond, x_within])
        h_c = (1 - ec2 / ecu) * H
        eps_top = np.where(x <= H, ecu, ec2 * x / np.maximum(x - h_c, 1e-9))
        n, m = self._resultants(axis, sign, eps_top, eps_top / x)
        n0, m0 = self._resultants(axis, sign, np.array([ec2]), np.array([0.0]))
        nt = -self.bars.total * self.steel.fyd
        _, _, yb = self._frame(axis, sign)
        mt = -float((self.bars.area * self.steel.fyd * (H / 2 - yb)).sum())
        c = np.vstack([[n0[0], m0[0]], np.column_stack([n, m]), [nt, mt]]) / np.array([1e3, 1e6])
        self._cache[key] = c
        return c

    def ductility(self, axis: str, sign: int, n: float) -> dict:
        """Neutral axis at the moment capacity under ``n`` (kN, compression +), bending about ``axis`` in
        the sense ``sign``: its depth x, the depth d of the furthest tension bar, x/d and that bar's strain.

        EN 1992-1-1 5.5(4) with no redistribution keeps x/d within (1 − k1)/k2 = 0.448 (fck <= 50 MPa);
        beyond εcu2·(d − x)/x < fyd/Es the tension bars do not yield (an over-reinforced section).
        """
        H = self.h if axis == "v" else self.b
        ecu = self.concrete.eps_cu2
        _, _, yb = self._frame(axis, sign)
        d = float(yb.max())

        def n_at(x: float) -> float:
            return float(self._resultants(axis, sign, np.array([ecu]), np.array([ecu / x]))[0][0]) / 1e3

        lo, hi = 1e-3 * H, H
        if n >= n_at(hi):
            x = H
        elif n <= n_at(lo):
            x = lo
        else:
            for _ in range(60):
                mid = 0.5 * (lo + hi)
                lo, hi = (mid, hi) if n_at(mid) < n else (lo, mid)
            x = 0.5 * (lo + hi)
        return {
            "x_mm": round(x),
            "d_mm": round(d),
            "x_d": round(x / d, 3) if d > 0 else None,
            "eps_s": round(ecu * (d - x) / x, 5),
            "eps_yd": round(self.steel.fyd / self.steel.es, 5),
        }

    def n_rd(self) -> tuple[float, float]:
        """Axial capacities (kN): compression (+) and tension (-)."""
        c = self.curve("v", 1)
        return float(c[0, 0]), float(c[-1, 0])

    def m_rd(self, axis: str, sign: np.ndarray, n: np.ndarray) -> np.ndarray:
        """Moment capacity (kNm, >= 0) about ``axis`` in the sense of each ``sign`` at axial force n (kN)."""
        out = np.zeros(len(n))
        for s in (1, -1):
            m = sign == s
            if not m.any():
                continue
            c = self.curve(axis, s)
            order = np.argsort(c[:, 0])
            inside = (n[m] >= c[-1, 0]) & (n[m] <= c[0, 0])
            out[m] = np.where(inside, np.maximum(np.interp(n[m], c[order, 0], c[order, 1]), 0.0), 0.0)
        return out

    def utilisation(self, n: np.ndarray, mv: np.ndarray, mh: np.ndarray | None = None) -> np.ndarray:
        """EC2 5.8.9(4) utilisation of loads (kN, kNm); mh None for bending in the vertical plane only."""
        n = np.asarray(n, float)
        mv = np.asarray(mv, float)
        mh = np.zeros_like(n) if mh is None else np.asarray(mh, float)
        nc, nt = self.n_rd()
        with np.errstate(divide="ignore", invalid="ignore"):
            # No steel left for tension (torsion took it all): any tension fails.
            u_n = np.where(n >= 0, n / nc, np.where(n < 0, n / nt if nt else np.inf, 0.0))
        rv = self.m_rd("v", np.where(mv >= 0, 1, -1), n)
        rh = self.m_rd("h", np.where(mh >= 0, 1, -1), n)
        n_rd = (self.area_concrete * self.concrete.fcd + self.bars.total * self.steel.fyd) / 1e3
        a = np.interp(n / n_rd, [0.1, 0.7, 1.0], [1.0, 1.5, 2.0])
        with np.errstate(divide="ignore", invalid="ignore"):
            tv = np.where(np.abs(mv) > 1e-9, np.abs(mv) / rv, 0.0)
            th = np.where(np.abs(mh) > 1e-9, np.abs(mh) / rh, 0.0)
            u_m = (tv**a + th**a) ** (1 / a)
        return np.maximum(np.nan_to_num(u_m, nan=np.inf, posinf=np.inf), u_n)

    def cracked(self, n: float, m: float, e_c: float, e_s: float = 200_000.0) -> dict:
        """Elastic cracked section in the vertical plane under N (kN, compression +) and M_v (kNm).

        Concrete takes no tension; e_c is the (long-term) concrete modulus in MPa.
        Returns the bar stresses (MPa, tension -), the neutral axis depth x (mm) from the
        compressed face (0 when all in tension, h when all in compression) and the strains
        at the top and bottom faces.
        """
        sign = 1 if m >= 0 else -1
        H, B, yb = self._frame("v", sign)
        y = (np.arange(self.strips) + 0.5) * H / self.strips
        a = self._strip_areas("v", sign)
        area = self.bars.area
        target = np.array([n * 1e3, abs(m) * 1e6])
        # Unknowns: strain at the compressed face e0 and curvature k; eps(y) = e0 - k·y.
        e0, k = 0.0, 0.0
        for _ in range(60):
            eps_c = e0 - k * y
            live = eps_c > 0
            ec_live = np.where(live, e_c, 0.0) * a
            es = e_s * area
            # Stiffness: [N; M] = K [e0; k]
            lever_c, lever_s = H / 2 - y, H / 2 - yb
            k11 = ec_live.sum() + es.sum()
            k12 = -(ec_live * y).sum() - (es * yb).sum()
            k21 = (ec_live * lever_c).sum() + (es * lever_s).sum()
            k22 = -(ec_live * y * lever_c).sum() - (es * yb * lever_s).sum()
            kk = np.array([[k11, k12], [k21, k22]])
            try:
                sol = np.linalg.solve(kk, target)
            except np.linalg.LinAlgError:
                break
            if abs(sol[0] - e0) < 1e-10 and abs(sol[1] - k) < 1e-13:
                e0, k = sol
                break
            e0, k = sol
        eps_s = e0 - k * yb
        stress = e_s * eps_s
        eps_bottom = e0 - k * H
        if e0 <= 0 and eps_bottom <= 0:
            x = 0.0
        elif k <= 0 or eps_bottom >= 0:
            x = H
        else:
            x = min(e0 / k, H)
        top, bottom = (e0, eps_bottom) if sign > 0 else (eps_bottom, e0)
        return {
            "stress": stress,
            "x": float(x),
            "eps_top": float(top),
            "eps_bottom": float(bottom),
            "sign": sign,
        }


def rect_laws(fck: float, gamma_c: float, gamma_s: float, alpha_cc: float, fyk: float):
    return ConcreteLaw(fck, gamma_c, alpha_cc), SteelLaw(fyk, gamma_s)
