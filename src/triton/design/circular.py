"""N-M interaction of circular reinforced concrete sections to EN 1992-1-1.

Assumptions, chosen to match AdSec's EC2 defaults:

* concrete: parabola-rectangle diagram (3.1.7(1)), fcd = alpha_cc * fck / gamma_c,
  no tensile strength;
* reinforcement: bilinear with a horizontal top branch (3.2.7(2) b), so the
  steel strain is not limited, Es = 200 GPa;
* strain limits: epsilon_cu2 at the extreme compression fibre, or pivot about
  the point at depth (1 - epsilon_c2 / epsilon_cu2) h when the whole section is
  in compression (6.1(5));
* the concrete displaced by the bars is deducted.

Sign convention: N is positive in compression (the AdSec convention, i.e.
Plaxis N x -1), M is the resultant moment and is always >= 0.
Units: mm, N, MPa internally; kN and kNm at the interface.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

E_S = 200_000.0  # MPa


@dataclass(frozen=True)
class ConcreteLaw:
    fck: float
    gamma_c: float = 1.5
    alpha_cc: float = 0.85

    @property
    def fcd(self) -> float:
        return self.alpha_cc * self.fck / self.gamma_c

    @property
    def eps_c2(self) -> float:
        if self.fck <= 50:
            return 0.002
        return (2.0 + 0.085 * (self.fck - 50) ** 0.53) / 1000

    @property
    def eps_cu2(self) -> float:
        if self.fck <= 50:
            return 0.0035
        return (2.6 + 35 * ((90 - self.fck) / 100) ** 4) / 1000

    @property
    def n(self) -> float:
        if self.fck <= 50:
            return 2.0
        return 1.4 + 23.4 * ((90 - self.fck) / 100) ** 4

    def stress(self, eps: np.ndarray) -> np.ndarray:
        """Compressive stress (MPa, >= 0) for strain eps (compression positive)."""
        e = np.clip(eps, 0.0, None)
        parabola = self.fcd * (1 - (1 - np.minimum(e, self.eps_c2) / self.eps_c2) ** self.n)
        return np.where(e >= self.eps_c2, self.fcd, parabola)


@dataclass(frozen=True)
class SteelLaw:
    fyk: float = 500.0
    gamma_s: float = 1.15
    es: float = E_S

    @property
    def fyd(self) -> float:
        return self.fyk / self.gamma_s

    def stress(self, eps: np.ndarray) -> np.ndarray:
        return np.clip(self.es * eps, -self.fyd, self.fyd)


@dataclass(frozen=True)
class Ring:
    """Equal bars evenly spaced on one circle, the first one at angle 0."""

    count: int
    diameter: float  # mm
    radius: float  # mm, radius of the circle through the bar centres

    @property
    def area(self) -> float:
        return self.count * math.pi * self.diameter**2 / 4


@dataclass(frozen=True)
class CircularSection:
    diameter: float  # mm
    rings: tuple[Ring, ...]  # outer row first
    concrete: ConcreteLaw
    steel: SteelLaw
    strips: int = 400
    _cache: dict = field(default_factory=dict, compare=False, hash=False, repr=False)

    @property
    def area_concrete(self) -> float:
        return math.pi * self.diameter**2 / 4

    @property
    def area_steel(self) -> float:
        return sum(r.area for r in self.rings)

    @property
    def rotations(self) -> tuple[float, ...]:
        """Bar orientations to check: a bar on the bending axis, and midway between bars.

        With a half row behind every second bar the pattern repeats every
        2π / (bars in the half row), so its midway orientation is checked too.
        """
        outer = self.rings[0].count
        fewest = min(r.count for r in self.rings)
        return tuple(sorted({0.0, math.pi / outer, math.pi / fewest}))

    def _bars(self, rotation: float) -> tuple[np.ndarray, np.ndarray]:
        """Depth of each bar below the top fibre, and its area, for a given rotation (rad)."""
        ys, areas = [], []
        for r in self.rings:
            angles = rotation + 2 * math.pi * np.arange(r.count) / r.count
            ys.append(self.diameter / 2 - r.radius * np.cos(angles))
            areas.append(np.full(r.count, math.pi * r.diameter**2 / 4))
        return np.concatenate(ys), np.concatenate(areas)

    def _strips(self) -> tuple[np.ndarray, np.ndarray]:
        """Depth of strip centroids and exact strip areas of the circle."""
        r = self.diameter / 2
        edges = np.linspace(0.0, self.diameter, self.strips + 1)
        z = edges - r  # from -r to r

        # Area of the circle above height z: segment area formula.
        def above(zz: np.ndarray) -> np.ndarray:
            zz = np.clip(zz, -r, r)
            return r * r * np.arccos(zz / r) - zz * np.sqrt(r * r - zz * zz)

        areas = above(z[:-1]) - above(z[1:])
        # Chord-weighted centroid is close enough to the midpoint for 400 strips.
        y = (edges[:-1] + edges[1:]) / 2
        return y, areas

    def _resultants(self, eps_top: np.ndarray, curvature: np.ndarray, rotation: float) -> tuple:
        """N (N) and M about the centroid (Nmm) for strain eps(y) = eps_top - curvature * y."""
        y, a = self._strips()
        eps_c = eps_top[:, None] - curvature[:, None] * y[None, :]
        sc = self.concrete.stress(eps_c)
        n_c = (sc * a).sum(axis=1)
        m_c = (sc * a * (self.diameter / 2 - y)).sum(axis=1)

        yb, ab = self._bars(rotation)
        eps_s = eps_top[:, None] - curvature[:, None] * yb[None, :]
        ss = self.steel.stress(eps_s) - self.concrete.stress(eps_s)  # deduct displaced concrete
        n_s = (ss * ab).sum(axis=1)
        m_s = (ss * ab * (self.diameter / 2 - yb)).sum(axis=1)
        return n_c + n_s, m_c + m_s

    def interaction(self, rotation: float = 0.0, points: int = 240) -> np.ndarray:
        """Upper half of the N-M diagram as rows (N kN, M kNm), from pure compression to pure tension."""
        key = (rotation, points)
        if key in self._cache:
            return self._cache[key]
        h = self.diameter
        ecu, ec2 = self.concrete.eps_cu2, self.concrete.eps_c2
        # Neutral axis depths: beyond h (whole section compressed) down to almost zero.
        x_beyond = h * (1 + np.geomspace(1e-3, 50, points // 3))[::-1]
        x_within = h * np.geomspace(1.0, 2e-4, points - points // 3)
        x = np.concatenate([x_beyond, x_within])
        eps_top = np.where(x <= h, ecu, 0.0)
        h_c = (1 - ec2 / ecu) * h
        eps_top = np.where(x > h, ec2 * x / (x - h_c), eps_top)
        curvature = eps_top / x
        n, m = self._resultants(eps_top, curvature, rotation)

        # Exact end points: uniform compression at eps_c2, and all steel yielding in tension.
        n0, _ = self._resultants(np.array([ec2]), np.array([0.0]), rotation)
        n_t = -self.area_steel * self.steel.fyd
        curve = np.vstack([[n0[0], 0.0], np.column_stack([n, np.abs(m)]), [n_t, 0.0]])
        curve = curve / np.array([1e3, 1e6])  # kN, kNm
        self._cache[key] = curve
        return curve

    def utilisation(self, n_ed: np.ndarray, m_ed: np.ndarray) -> np.ndarray:
        """Radial utilisation of loads (kN, kNm): load / capacity along the ray from the origin.

        The worst bar orientation is taken (see ``rotations``), since the moment
        direction varies along the pile.
        """
        worst = np.zeros(len(n_ed))
        for rotation in self.rotations:
            worst = np.maximum(worst, radial_utilisation(self.interaction(rotation), n_ed, m_ed))
        return worst

    def moment_capacity(self, n_ed: float) -> float:
        """M_Rd (kNm) at a given axial force (kN), worst bar orientation."""
        caps = []
        for rotation in self.rotations:
            c = self.interaction(rotation)
            if not c[-1, 0] <= n_ed <= c[0, 0]:
                return 0.0
            order = np.argsort(c[:, 0])
            caps.append(float(np.interp(n_ed, c[order, 0], c[order, 1])))
        return min(caps)


def radial_utilisation(curve: np.ndarray, n_ed: np.ndarray, m_ed: np.ndarray) -> np.ndarray:
    """Load / capacity along rays from the origin for a convex N-M boundary (upper half).

    ``curve`` runs from pure compression (angle 0) to pure tension (angle pi).
    Returns inf for loads outside the axial range when the origin is not inside.
    """
    n_ed = np.asarray(n_ed, dtype=float)
    m_ed = np.abs(np.asarray(m_ed, dtype=float))
    phi_c = np.arctan2(curve[:, 1], curve[:, 0])
    phi_c = np.maximum.accumulate(phi_c)  # guard tiny numerical non-monotonicity
    phi = np.arctan2(m_ed, n_ed)
    idx = np.clip(np.searchsorted(phi_c, phi) - 1, 0, len(curve) - 2)
    p1, p2 = curve[idx], curve[idx + 1]
    # Intersect ray t*(n, m) with segment p1 + s*(p2 - p1).
    d = p2 - p1
    denom = n_ed * d[:, 1] - m_ed * d[:, 0]
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (p1[:, 0] * d[:, 1] - p1[:, 1] * d[:, 0]) / denom
        util = np.where(t > 0, 1.0 / t, np.inf)
    util[(n_ed == 0) & (m_ed == 0)] = 0.0
    return util


def hull_indices(n: np.ndarray, m: np.ndarray) -> np.ndarray:
    """Indices of the convex hull vertices of the load points (N, |M|).

    Radial utilisation against a convex capacity domain around the origin is a
    convex function of the load, so its maximum over any set of points is at a
    vertex of their convex hull. Checking only these points gives the same
    maximum utilisation at a fraction of the cost.
    """
    pts = np.column_stack([np.asarray(n, float), np.abs(np.asarray(m, float))])
    if len(pts) <= 3:
        return np.arange(len(pts))
    order = np.lexsort((pts[:, 1], pts[:, 0]))

    def cross(o, a, b) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    def chain(idx) -> list[int]:
        out: list[int] = []
        for i in idx:
            while len(out) >= 2 and cross(pts[out[-2]], pts[out[-1]], pts[i]) <= 0:
                out.pop()
            out.append(i)
        return out

    lower, upper = chain(order), chain(order[::-1])
    return np.unique(np.array(lower[:-1] + upper[:-1]))
