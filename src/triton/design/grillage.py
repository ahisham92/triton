"""A grillage of the deck on pile springs, for moving piles without a new Plaxis run.

The deck (slab and beams, as plates) is a square grillage of members ``h`` apart in plan: each node
has a deflection ``w`` (down +) and the slopes ``θx = ∂w/∂x`` and ``θy = ∂w/∂y``. A member along X
bends with (w, θx) at its ends and twists with θy; one along Y bends with (w, θy) and twists with θx.
Per metre of width the members have the plate's stiffness: EI = D·h and GJ = D·(1 − ν)·h, with
D = E·t³/12, so the grillage takes the plate's bending and twisting energy (ν's coupling is left out).

A pile is a vertical spring at its head, shared between the four nodes round it by their bilinear
weights, so it can sit anywhere, not only on a node.

The nodes are numbered line by line along the longer side, so the stiffness matrix is block
tridiagonal (a member or a spring only joins a line to the next one) and is solved line by line with
numpy alone.

Moments per metre, sagging +: Mx = −D·∂²w/∂x², My = −D·∂²w/∂y² (from the members' end curvatures,
averaged at each node) and the twisting moment Mxy = −D·(1 − ν)·∂²w/∂x∂y, one tensor with Mx and My;
shears Qx = ∂Mx/∂x + ∂Mxy/∂y and Qy = ∂My/∂y + ∂Mxy/∂x, so that ∂Qx/∂x + ∂Qy/∂y = −q.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

NU = 0.2  # concrete
SOFT = 1e-4  # stiffness share of members and nodes outside the deck: keeps the matrix regular


@dataclass
class Grid:
    """Plan grid: nodes (i, j) at x0 + i·h, y0 + j·h. ``thickness`` in m per node, 0 off the deck."""

    x0: float
    y0: float
    h: float
    nx: int
    ny: int
    thickness: np.ndarray  # (nx, ny) m
    e_kpa: float  # Young's modulus, kPa

    @property
    def long_x(self) -> bool:
        """Lines of nodes are taken along the longer side: fewer nodes in each line."""
        return self.nx >= self.ny

    @property
    def lines(self) -> int:
        return self.nx if self.long_x else self.ny

    @property
    def per_line(self) -> int:
        return self.ny if self.long_x else self.nx

    def line_of(self, i: int, j: int) -> tuple[int, int]:
        return (i, j) if self.long_x else (j, i)

    @property
    def active(self) -> np.ndarray:
        return self.thickness > 0

    def xy(self) -> tuple[np.ndarray, np.ndarray]:
        x = self.x0 + self.h * np.arange(self.nx)
        y = self.y0 + self.h * np.arange(self.ny)
        return np.meshgrid(x, y, indexing="ij")

    def rigidity(self) -> np.ndarray:
        """D per node, kNm²/m; a small share of the thinnest part off the deck."""
        t = self.thickness
        D = self.e_kpa * t**3 / 12
        floor = SOFT * (D[t > 0].min() if (t > 0).any() else 1.0)
        return np.where(t > 0, D, floor)

    def weights(self, x: float, y: float) -> list[tuple[int, int, float]]:
        """Bilinear weights of the four nodes round (x, y)."""
        u = (x - self.x0) / self.h
        v = (y - self.y0) / self.h
        i = int(np.clip(np.floor(u), 0, self.nx - 2))
        j = int(np.clip(np.floor(v), 0, self.ny - 2))
        a, b = u - i, v - j
        out = [
            (i, j, (1 - a) * (1 - b)),
            (i + 1, j, a * (1 - b)),
            (i, j + 1, (1 - a) * b),
            (i + 1, j + 1, a * b),
        ]
        return [(p, q, w) for p, q, w in out if abs(w) > 1e-12]


@dataclass
class Spring:
    x: float
    y: float
    k: float  # kN/m


def _beam(ei: float, L: float) -> np.ndarray:
    return (
        ei
        / L**3
        * np.array(
            [
                [12, 6 * L, -12, 6 * L],
                [6 * L, 4 * L * L, -6 * L, 2 * L * L],
                [-12, -6 * L, 12, -6 * L],
                [6 * L, 2 * L * L, -6 * L, 4 * L * L],
            ]
        )
    )


@dataclass
class Solver:
    """The grillage with its springs, factorised line by line (block tridiagonal Cholesky)."""

    grid: Grid
    springs: list[Spring]
    _linv: list[np.ndarray] = field(default_factory=list, repr=False)
    _lsub: list[np.ndarray | None] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        g = self.grid
        n = 3 * g.per_line
        diag = [np.zeros((n, n)) for _ in range(g.lines)]
        off = [np.zeros((n, n)) for _ in range(g.lines - 1)]  # off[a] = K[a+1, a]

        def add(nodes: list[tuple[int, int]], dofs: list[int], k: np.ndarray) -> None:
            idx = [(*g.line_of(*nd), d) for nd, d in zip(nodes, dofs, strict=True)]
            for r, (la, pa, da) in enumerate(idx):
                for c, (lb, pb, db) in enumerate(idx):
                    v = k[r, c]
                    if v == 0:
                        continue
                    ra, cb = 3 * pa + da, 3 * pb + db
                    if la == lb:
                        diag[la][ra, cb] += v
                    elif la == lb + 1:
                        off[lb][ra, cb] += v
                    # la == lb - 1: its transpose is added when (r, c) are swapped

        D = g.rigidity()
        h = g.h
        for i in range(g.nx):
            for j in range(g.ny):
                for di, dj in ((1, 0), (0, 1)):
                    p, q = i + di, j + dj
                    if p >= g.nx or q >= g.ny:
                        continue
                    d = 0.5 * (D[i, j] + D[p, q])
                    # A member on the grid's edge carries half a spacing of width.
                    edge = (j in (0, g.ny - 1)) if di else (i in (0, g.nx - 1))
                    b = h / 2 if edge else h
                    ei, gj = d * b, d * (1 - NU) * b
                    bend = 1 if di else 2  # θx along X, θy along Y
                    twist = 3 - bend
                    add([(i, j), (i, j), (p, q), (p, q)], [0, bend, 0, bend], _beam(ei, h))
                    add([(i, j), (p, q)], [twist, twist], gj / h * np.array([[1.0, -1.0], [-1.0, 1.0]]))
        # Off the deck each node gets a small spring so that nothing there is free to move.
        kmax = max((s.k for s in self.springs), default=1.0)
        for i, j in zip(*np.nonzero(~g.active), strict=True):
            add([(i, j)], [0], np.array([[SOFT * 1e-3 * kmax]]))
        for s in self.springs:
            w = g.weights(s.x, s.y)
            vec = np.array([c for *_, c in w])
            add([(p, q) for p, q, _ in w], [0] * len(w), s.k * np.outer(vec, vec))
        self._factor(diag, off)

    def _factor(self, diag: list[np.ndarray], off: list[np.ndarray]) -> None:
        self._linv, self._lsub = [], []
        for a, A in enumerate(diag):
            if a == 0:
                sub = None
                Dm = A
            else:
                sub = off[a - 1] @ self._linv[a - 1].T
                Dm = A - sub @ sub.T
            L = np.linalg.cholesky(Dm)
            self._linv.append(np.linalg.inv(L))
            self._lsub.append(sub)

    def solve(self, f: np.ndarray) -> np.ndarray:
        """Displacements for loads ``f`` of shape (nx, ny, 3, m) (m load cases), same shape back."""
        g = self.grid
        m = f.shape[-1]
        lines = f if g.long_x else f.transpose(1, 0, 2, 3)
        lines = lines.reshape(g.lines, 3 * g.per_line, m)
        y = []
        for a in range(g.lines):
            r = lines[a] if a == 0 else lines[a] - self._lsub[a] @ y[a - 1]
            y.append(self._linv[a] @ r)
        x: list[np.ndarray] = [None] * g.lines  # type: ignore[list-item]
        for a in range(g.lines - 1, -1, -1):
            r = y[a] if a == g.lines - 1 else y[a] - self._lsub[a + 1].T @ x[a + 1]
            x[a] = self._linv[a].T @ r
        out = np.stack(x).reshape(g.lines, g.per_line, 3, m)
        return out if g.long_x else out.transpose(1, 0, 2, 3)

    def reactions(self, u: np.ndarray) -> np.ndarray:
        """Spring forces (m cases × springs), kN, compression (pushing the deck up) +."""
        g = self.grid
        out = np.zeros((u.shape[-1], len(self.springs)))
        for n, s in enumerate(self.springs):
            for p, q, c in g.weights(s.x, s.y):
                out[:, n] += s.k * c * u[p, q, 0, :]
        return out


def actions(grid: Grid, u: np.ndarray) -> dict[str, np.ndarray]:
    """Mx, My, Mxy (kNm/m, sagging +) and Qx, Qy (kN/m) per node, each (nx, ny, m)."""
    h = grid.h
    D = grid.rigidity()
    w, tx, ty = u[:, :, 0, :], u[:, :, 1, :], u[:, :, 2, :]

    def curvature(w: np.ndarray, t: np.ndarray, axis: int) -> np.ndarray:
        """∂²w/∂s² per node along ``axis``: the mean of the end curvatures of the members meeting there."""
        w1, w2 = np.moveaxis(w, axis, 0)[:-1], np.moveaxis(w, axis, 0)[1:]
        t1, t2 = np.moveaxis(t, axis, 0)[:-1], np.moveaxis(t, axis, 0)[1:]
        start = (-6 * w1 - 4 * h * t1 + 6 * w2 - 2 * h * t2) / h**2  # at node 1 of each member
        end = (6 * w1 + 2 * h * t1 - 6 * w2 + 4 * h * t2) / h**2  # at node 2
        n = np.moveaxis(w, axis, 0).shape[0]
        total = np.zeros_like(np.moveaxis(w, axis, 0))
        count = np.zeros((n,) + (1,) * (w.ndim - 1))
        total[:-1] += start
        total[1:] += end
        count[:-1] += 1
        count[1:] += 1
        return np.moveaxis(total / count, 0, axis)

    Dn = D[:, :, None]
    mx = -Dn * curvature(w, tx, 0)
    my = -Dn * curvature(w, ty, 1)
    # Twist from the torsion members: θy along X and θx along Y, averaged.
    twist = 0.5 * (np.gradient(ty, h, axis=0) + np.gradient(tx, h, axis=1))
    mxy = -Dn * (1 - NU) * twist
    qx = np.gradient(mx, h, axis=0) + np.gradient(mxy, h, axis=1)
    qy = np.gradient(my, h, axis=1) + np.gradient(mxy, h, axis=0)
    return {"Mx": mx, "My": my, "Mxy": mxy, "Qx": qx, "Qy": qy}


def sample(grid: Grid, field_: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Bilinear values of a node field (nx, ny, m) at points; (points, m)."""
    u = np.clip((np.asarray(x, float) - grid.x0) / grid.h, 0, grid.nx - 1 - 1e-9)
    v = np.clip((np.asarray(y, float) - grid.y0) / grid.h, 0, grid.ny - 1 - 1e-9)
    i, j = np.floor(u).astype(int), np.floor(v).astype(int)
    a, b = (u - i)[:, None], (v - j)[:, None]
    return (
        field_[i, j] * (1 - a) * (1 - b)
        + field_[i + 1, j] * a * (1 - b)
        + field_[i, j + 1] * (1 - a) * b
        + field_[i + 1, j + 1] * a * b
    )
