"""Berths that are not one straight line: a corner, or a part of the quay that turns.

The office designs the inclined part of a corner berth by hand by rotating it (with sin and cos) so
that it lies straight, next to the straight part, and designing it like the straight part. Triton
does the same, part by part:

* The quay's line in plan (the alignment) is a polyline: the centre line of the front beam, found
  from its nodes, or the corner points given by hand. Each straight run of it is a part. Two parts
  meet at a corner, and the line that halves the angle at the corner splits the results between
  them.
* Each part is rotated about its corner so that it runs along the quay's axis (global Y when the
  slab strips run along X). Node coordinates turn with it, and so do the plate results: moments,
  in-plane forces and shears are tensors, so they are transformed in full, with c = cos α and
  s = sin α for a rotation α anticlockwise:

      M11' = c²·M11 − 2cs·M12 + s²·M22
      M22' = s²·M11 + 2cs·M12 + c²·M22
      M12' = cs·(M11 − M22) + (c² − s²)·M12

  and likewise N1, N2 and the in-plane shear Q12; the out-of-plane shears (Q13, Q23) turn as a
  vector. Beam elements (piles, king piles) only move: their forces are in their own axes.
* The rotated part is then designed exactly as a straight berth: column and field strips on its own
  lines of piles, stations from its front beam, zones, punching, cracking. Where a part runs
  straight along the quay's axis already it is not rotated at all.

Plate results are taken as given in global X/Y components (one deck plate, as in the office's
models). A part whose results are in its own axes (a plate drawn along the inclined part) can say so,
and is then only moved, not transformed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

import numpy as np
import pandas as pd

from .elements import ElementType, ResultKind
from .importer import SheetData

# The pairs of plate columns that make up each tensor or vector: (11, 22, 12) and (13, 23).
TENSORS = (("M_11", "M_22", "M_12"), ("N_1", "N_2", "Q_12"))
VECTORS = (("Q_13", "Q_23"),)
MIN_ANGLE = 2.0  # degrees: parts turned by less than this are straight
MAX_PARTS = 4
MIN_PART = 5.0  # m: shorter runs are not parts (their edges cannot tell a direction)


@dataclass(frozen=True)
class Part:
    index: int  # 0-based along the alignment
    name: str  # "Part 1"
    start: tuple[float, float]  # plan points of the part's run of the alignment
    end: tuple[float, float]
    rotation: float  # degrees, anticlockwise, that bring the part onto the quay's axis
    pivot: tuple[float, float]  # the part rotates about this point (its corner)
    own_axes: bool = False  # the part's plate results are in its own axes: move them only

    @property
    def turned(self) -> bool:
        return self.rotation != 0.0

    @property
    def angle(self) -> float:
        """Direction of the part's run in plan, degrees anticlockwise from global X."""
        return math.degrees(math.atan2(self.end[1] - self.start[1], self.end[0] - self.start[0]))

    @property
    def length(self) -> float:
        return math.hypot(self.end[0] - self.start[0], self.end[1] - self.start[1])

    def label(self) -> str:
        return f"{self.name}, turned {self.rotation:+.1f}°" if self.turned else f"{self.name}, straight"

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "start": [round(v, 3) for v in self.start],
            "end": [round(v, 3) for v in self.end],
            "length_m": round(self.length, 2),
            "direction_deg": round(self.angle, 2),
            "rotation_deg": round(self.rotation, 3),
            "pivot": [round(v, 3) for v in self.pivot],
            "own_axes": self.own_axes,
            "label": self.label(),
        }


# --- Finding the alignment ------------------------------------------------------------------------


def _line(p: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Total least squares line through points: centre, unit direction, sum of squared offsets."""
    c = p.mean(axis=0)
    q = p - c
    _, s, vt = np.linalg.svd(q, full_matrices=False)
    u = vt[0]
    return c, u, float(s[1] ** 2) if len(s) > 1 else 0.0


def _cross(a: np.ndarray, b: np.ndarray) -> Any:
    """z of the cross product of plan vectors (b may be rows)."""
    return a[0] * b[..., 1] - a[1] * b[..., 0]


def _edge_direction(p: np.ndarray, u: np.ndarray) -> np.ndarray:
    """The direction of the least-area rectangle round the points nearest ``u``: for a strip drawn
    with straight edges (a beam's nodes) it is the edges' direction exactly, where a fitted line
    carries the scatter of the mesh."""
    try:
        pts = np.unique(p, axis=0)
        if len(pts) < 3:
            return u
        # Convex hull (monotone chain).
        pts = pts[np.lexsort((pts[:, 1], pts[:, 0]))]

        def half(seq):
            h: list = []
            for q in seq:
                while len(h) >= 2 and _cross(h[-1] - h[-2], q - h[-2]) <= 1e-12:
                    h.pop()
                h.append(q)
            return h

        hull = np.array(half(pts)[:-1] + half(pts[::-1])[:-1])
        best = None
        for a, b in zip(hull, np.roll(hull, -1, axis=0), strict=True):
            e = b - a
            n = float(np.hypot(*e))
            if n < 1e-9:
                continue
            e = e / n
            w = np.array([-e[1], e[0]])
            area = float(np.ptp(hull @ e) * np.ptp(hull @ w))
            if best is None or area < best[0] - 1e-9:
                best = (area, e)
        if best is None:
            return u
        e = best[1]
        # The rectangle's long side runs along the strip.
        w = np.array([-e[1], e[0]])
        if np.ptp(hull @ w) > np.ptp(hull @ e):
            e = w
        if _angle_between(e, u) > 5.0:
            return u
        return e if float(e @ u) >= 0 else -e
    except (ValueError, np.linalg.LinAlgError):
        return u


def _angle_between(u: np.ndarray, v: np.ndarray) -> float:
    """Angle between two undirected lines, degrees 0..90."""
    return math.degrees(math.acos(min(1.0, abs(float(u @ v)))))


def _split(p: np.ndarray, t: np.ndarray, min_angle: float, depth: int) -> list[np.ndarray]:
    """Points ordered along ``t`` cut into straight runs, recursively, where two halves turn."""
    n = len(p)
    if depth <= 0 or n < 20:
        return [p]
    _, u, whole = _line(p)
    best = None
    for q in np.linspace(0.08, 0.92, 43):
        k = int(q * n)
        if k < 10 or n - k < 10 or t[k - 1] - t[0] < MIN_PART or t[-1] - t[k] < MIN_PART:
            continue
        _, ua, ea = _line(p[:k])
        _, ub, eb = _line(p[k:])
        cost = ea + eb
        if best is None or cost < best[0]:
            best = (cost, k, ua, ub)
    if best is None:
        return [p]
    cost, k, ua, ub = best
    # A real corner: the two runs' edges turn by more than the least angle (a straight strip cut
    # anywhere keeps its edges' direction exactly), and they fit better apart.
    ea, eb = _run_line(p[:k], ua)[1], _run_line(p[k:], ub)[1]
    if _angle_between(ea, eb) < min_angle or cost >= whole:
        return [p]
    return _split(p[:k], t[:k], min_angle, depth - 1) + _split(p[k:], t[k:], min_angle, depth - 1)


def _intersect(c1: np.ndarray, u1: np.ndarray, c2: np.ndarray, u2: np.ndarray) -> np.ndarray:
    a = np.array([u1, -u2]).T
    if abs(np.linalg.det(a)) < 1e-9:
        return (c1 + c2) / 2
    s = np.linalg.solve(a, c2 - c1)
    return c1 + s[0] * u1


def _run_line(r: np.ndarray, hint: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """A run's line: along its edges (else its fitted line), pointing the way ``hint`` does, through
    the middle of its width."""
    c, u, _ = _line(r)
    u = _edge_direction(r, u)
    if float(u @ hint) < 0:
        u = -u
    w = np.array([-u[1], u[0]])
    across = r @ w
    c = c + ((across.min() + across.max()) / 2 - float(c @ w)) * w
    return c, u


CENTRE_TOL = 0.3  # m: the centre line may wander this much off a straight run


def _centreline(p: np.ndarray, t: np.ndarray, step: float = 1.0) -> np.ndarray:
    """The middle of the points in each ``step`` along ``t``: [t, x, y] rows."""
    k = np.floor((t - t[0]) / step).astype(int)
    out = []
    for i in np.unique(k):
        m = k == i
        out.append([float(t[m].mean()), *p[m].mean(axis=0)])
    return np.array(out)


def _simplify(line: np.ndarray, tol: float) -> list[int]:
    """Douglas–Peucker: the rows of ``line`` ([t, x, y]) kept as the polyline's vertices."""
    xy = line[:, 1:]

    def dp(i: int, j: int) -> list[int]:
        if j <= i + 1:
            return [i, j]
        d = xy[j] - xy[i]
        n = float(np.hypot(*d))
        if n < 1e-9:
            off = np.hypot(*(xy[i + 1 : j] - xy[i]).T)
        else:
            off = np.abs(_cross(d, xy[i + 1 : j] - xy[i])) / n
        k = int(np.argmax(off))
        if off[k] <= tol:
            return [i, j]
        left = dp(i, i + 1 + k)
        return left[:-1] + dp(i + 1 + k, j)

    return dp(0, len(line) - 1)


def _bends(line: np.ndarray, keep: list[int], c: np.ndarray, u: np.ndarray) -> list[float]:
    """The ``t`` of each inner vertex of the simplified centre line."""
    return [float(line[k, 0]) for k in keep[1:-1]]


def _merge(
    runs: list[np.ndarray], fixed: list[tuple[np.ndarray, np.ndarray]], min_angle: float
) -> tuple[list[np.ndarray], list[tuple[np.ndarray, np.ndarray]]]:
    """Runs joined where a corner hardly turns, where a run is too short to be a part, and down to
    ``MAX_PARTS``."""
    runs, fixed = list(runs), list(fixed)
    while len(runs) > 1:
        turns = [_angle_between(a[1], b[1]) for a, b in zip(fixed, fixed[1:], strict=False)]
        lengths = [float(np.ptp(r @ f[1])) for r, f in zip(runs, fixed, strict=True)]
        if min(lengths) < MIN_PART:
            k = int(np.argmin(lengths))
            j = 0 if k == 0 else k - 1 if k == len(runs) - 1 or turns[k - 1] <= turns[k] else k
        elif min(turns) < min_angle or len(runs) > MAX_PARTS:
            j = int(np.argmin(turns))
        else:
            break
        runs[j : j + 2] = [np.concatenate(runs[j : j + 2])]
        fixed[j : j + 2] = [_run_line(runs[j], fixed[j][1])]
    return runs, fixed


def _refine(
    p: np.ndarray, runs: list[np.ndarray], fixed: list[tuple[np.ndarray, np.ndarray]]
) -> tuple[list[np.ndarray], list[tuple[np.ndarray, np.ndarray]]]:
    """Each point to its part by the lines halving the corners, then the runs' lines again, until the
    parts stop changing: the first cut need not have been at the corner."""
    owner = None
    for _ in range(8):
        if len(fixed) < 2:
            break
        corners = [_intersect(a[0], a[1], b[0], b[1]) for a, b in zip(fixed, fixed[1:], strict=False)]
        new = np.zeros(len(p), int)
        for i, c in enumerate(corners):
            new += ((p - c) @ (fixed[i][1] + fixed[i + 1][1]) >= -1e-9).astype(int)
        if owner is not None and np.array_equal(new, owner):
            break
        owner = new
        keep = [i for i in range(len(fixed)) if (owner == i).sum() >= 3]
        runs = [p[owner == i] for i in keep]
        fixed = [_run_line(r, fixed[i][1]) for r, i in zip(runs, keep, strict=True)]
    return runs, fixed


def fit_alignment(points: np.ndarray, min_angle: float = MIN_ANGLE) -> list[list[float]]:
    """Corner points of the polyline that best follows a strip of plan points (the front beam)."""
    p = np.unique(np.asarray(points, float), axis=0)
    if len(p) < 2:
        return []
    c, u, _ = _line(p)
    t = (p - c) @ u
    order = np.argsort(t)
    p, t = p[order], t[order]
    # First corners: where the strip's centre line (the middle of each metre along it) bends.
    line = _centreline(p, t)
    keep = _simplify(line, CENTRE_TOL)
    ends = [0] + [int(np.searchsorted(t, t_at)) for t_at in _bends(line, keep, c, u)] + [len(p)]
    runs = [p[i:j] for i, j in zip(ends, ends[1:], strict=False) if j - i >= 3]
    fixed = [_run_line(r, u) for r in runs]
    for _ in range(3 * MAX_PARTS):
        runs, fixed = _merge(runs, fixed, min_angle)
        before = [len(r) for r in runs]
        runs, fixed = _refine(p, runs, fixed)
        if [len(r) for r in runs] == before:
            break
    runs, fixed = _merge(runs, fixed, min_angle)
    first_c, first_u = fixed[0]
    last_c, last_u = fixed[-1]
    start = first_c + ((runs[0] - first_c) @ first_u).min() * first_u
    end = last_c + ((runs[-1] - last_c) @ last_u).max() * last_u
    corners = [_intersect(a[0], a[1], b[0], b[1]) for a, b in zip(fixed, fixed[1:], strict=False)]
    return [[float(v) for v in q] for q in [start, *corners, end]]


def land_point(elements: dict[str, dict[str, SheetData]]) -> tuple[float, float] | None:
    """The middle of the deck in plan (a point on the land side of the front beam)."""
    for combos in elements.values():
        spec = next((s.parsed.spec for s in combos.values() if s.parsed), None)
        if spec is None or spec.type is not ElementType.SLAB:
            continue
        frames = [s.frame[["X", "Y"]] for s in combos.values() if {"X", "Y"} <= set(s.frame.columns)]
        if frames:
            f = pd.concat(frames)
            return float(f["X"].mean()), float(f["Y"].mean())
    return None


def reference_points(elements: dict[str, dict[str, SheetData]]) -> tuple[str, np.ndarray] | None:
    """Plan points that trace the quay's line: the front beam's nodes, else the rear beam's."""
    for kind in (ElementType.FRONT_BEAM, ElementType.REAR_BEAM):
        for name, combos in elements.items():
            spec = next((s.parsed.spec for s in combos.values() if s.parsed), None)
            if spec is None or spec.type is not kind:
                continue
            frames = [s.frame[["X", "Y"]] for s in combos.values() if {"X", "Y"} <= set(s.frame.columns)]
            if frames:
                return name, pd.concat(frames).drop_duplicates().to_numpy(float)
    return None


# --- Parts ----------------------------------------------------------------------------------------


def _wrap(deg: float) -> float:
    """An undirected line's turn, reduced to (−90°, 90°]."""
    deg = (deg + 180.0) % 180.0
    return deg - 180.0 if deg > 90.0 else deg


def make_parts(
    points: list[list[float]],
    along: str = "Y",
    min_angle: float = MIN_ANGLE,
    own_axes: list[int] | None = None,
    land: tuple[float, float] | None = None,
) -> list[Part]:
    """The parts of an alignment and the rotation that lays each one along the quay's axis.

    A part within ``min_angle`` of the axis (either way round) is not turned. Others are turned so the
    land lies on the same side as in a straight berth: behind the front beam towards −X when the quay
    runs along Y (towards −Y when it runs along X). ``land``: a point on the land side (the deck's
    middle); without it the smaller turn is taken."""
    pts = [tuple(map(float, q)) for q in points]
    if len(pts) < 2:
        return []
    side = 0.0
    if land is not None:
        a, b = np.array(pts[0]), np.array(pts[1])
        side = float(_cross(b - a, np.array(land) - a))  # > 0: land on the left going along
    target = 90.0 if along == "Y" else 180.0  # travel direction that puts the land at −X (−Y)
    if side < 0:
        target -= 180.0
    out = []
    for i, (a, b) in enumerate(zip(pts, pts[1:], strict=False)):
        direction = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
        small = _wrap((90.0 if along == "Y" else 0.0) - direction)
        if abs(small) < min_angle:
            rot = 0.0
        elif side == 0.0:
            rot = small
        else:
            rot = (target - direction + 180.0) % 360.0 - 180.0
        # Turn about the corner with the part before it (the first part about its far corner).
        pivot = a if i > 0 else b
        out.append(
            Part(i, f"Part {i + 1}", a, b, rot, pivot, own_axes=bool(own_axes and (i + 1) in own_axes))
        )
    return out


def part_of(parts: list[Part], x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Which part each plan point belongs to: split at each corner by the line halving its angle."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    owner = np.zeros(len(x), int)
    for i in range(1, len(parts)):
        a, b = parts[i - 1], parts[i]
        ua = np.subtract(a.end, a.start) / max(a.length, 1e-9)
        ub = np.subtract(b.end, b.start) / max(b.length, 1e-9)
        n = ua + ub
        cx, cy = b.start
        owner += ((x - cx) * n[0] + (y - cy) * n[1] >= -1e-9).astype(int)
    return owner


def rotate_points(
    part: Part, x: np.ndarray, y: np.ndarray, back: bool = False
) -> tuple[np.ndarray, np.ndarray]:
    """Plan points turned with the part onto the quay's axis (``back``: the other way)."""
    a = math.radians(-part.rotation if back else part.rotation)
    c, s = math.cos(a), math.sin(a)
    px, py = part.pivot
    dx, dy = np.asarray(x, float) - px, np.asarray(y, float) - py
    return px + c * dx - s * dy, py + s * dx + c * dy


def rotate_tensor(a11, a22, a12, deg: float):
    """Components of a symmetric plane tensor after the body turns by ``deg`` anticlockwise."""
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    return (
        c * c * a11 - 2 * c * s * a12 + s * s * a22,
        s * s * a11 + 2 * c * s * a12 + c * c * a22,
        c * s * (a11 - a22) + (c * c - s * s) * a12,
    )


def rotate_vector(v1, v2, deg: float):
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    return c * v1 - s * v2, s * v1 + c * v2


def turn_frame(f: pd.DataFrame, part: Part, kind: ResultKind | None, local_one: str = "X") -> pd.DataFrame:
    """A result table turned with its part: coordinates always, plate results unless they are in the
    part's own axes already. ``local_one``: the global axis of the plate's local 1 (X or Y)."""
    if not part.turned or f.empty:
        return f
    f = f.copy()
    f["PX"], f["PY"] = f["X"].to_numpy(float), f["Y"].to_numpy(float)  # plan, for inputs given in plan
    f["X"], f["Y"] = rotate_points(part, f["X"].to_numpy(float), f["Y"].to_numpy(float))
    if kind is not ResultKind.PLATE or part.own_axes or local_one not in ("X", "Y"):
        return f
    # With local 1 along Y the (1, 2) frame is the (X, Y) frame mirrored: the turn goes the other way.
    deg = part.rotation if local_one == "X" else -part.rotation
    for c11, c22, c12 in TENSORS:
        if {c11, c22, c12} <= set(f.columns):
            f[c11], f[c22], f[c12] = rotate_tensor(
                f[c11].to_numpy(float), f[c22].to_numpy(float), f[c12].to_numpy(float), deg
            )
    for c1, c2 in VECTORS:
        if {c1, c2} <= set(f.columns):
            f[c1], f[c2] = rotate_vector(f[c1].to_numpy(float), f[c2].to_numpy(float), deg)
    # The min/max envelopes cannot be turned: they are dropped so nothing reads them unturned.
    return f.drop(columns=[c for c in f.columns if c.endswith(("_min", "_max"))])


def part_elements(
    elements: dict[str, dict[str, SheetData]],
    parts: list[Part],
    part: Part,
    axes: dict[str, dict[str, str] | None],
) -> dict[str, dict[str, SheetData]]:
    """Every element's results inside ``part``, turned with it. Beam elements (piles) are kept whole
    when their plan position is in the part."""
    out: dict[str, dict[str, SheetData]] = {}
    for name, combos in elements.items():
        local_one = ((axes.get(name) or {}).get("1")) or "X"
        own = {}
        for combo, sheet in combos.items():
            f = sheet.frame
            if {"X", "Y"} <= set(f.columns) and len(parts) > 1:
                f = f[part_of(parts, f["X"].to_numpy(float), f["Y"].to_numpy(float)) == part.index]
            if f.empty:
                continue
            kind = sheet.parsed.spec.kind if sheet.parsed else None
            own[combo] = replace(sheet, frame=turn_frame(f, part, kind, local_one))
        if own:
            out[name] = own
    return out


# --- Back to plan ---------------------------------------------------------------------------------


def _xy_list(part: Part, rows: list) -> list:
    """[[x, y, ...], ...] rows turned back to plan."""
    if not rows:
        return rows
    a = np.array([[r[0], r[1]] for r in rows], float)
    x, y = rotate_points(part, a[:, 0], a[:, 1], back=True)
    return [[round(float(px), 3), round(float(py), 3), *r[2:]] for px, py, r in zip(x, y, rows, strict=True)]


def _xy_dict(part: Part, d: dict, kx: str = "x", ky: str = "y") -> dict:
    if not isinstance(d, dict) or kx not in d or ky not in d or d[kx] is None or d[ky] is None:
        return d
    x, y = rotate_points(part, np.array([d[kx]], float), np.array([d[ky]], float), back=True)
    return {**d, kx: round(float(x[0]), 3), ky: round(float(y[0]), 3)}


def element_in_part(element: Any, part: Part | None) -> Any:
    """A slab's inputs given in plan (punching thickness by pile position) in the part's turned frame."""
    depths = getattr(element, "punching_depths", None)
    if part is None or not part.turned or not depths:
        return element
    x, y = rotate_points(part, np.array([p.x for p in depths]), np.array([p.y for p in depths]))
    moved = [
        p.model_copy(update={"x": float(a), "y": float(b)}) for p, a, b in zip(depths, x, y, strict=True)
    ]
    return element.model_copy(update={"punching_depths": moved})


def tag_part(result: dict[str, Any], part: Part, parts: list[Part]) -> dict[str, Any]:
    """A part's design, with the part it is for. Everything in it (stations, strips, zones, the 3D
    bands, X/Y of cells and piles) is in the part's turned frame; pile positions and the governing
    shear point also get their plan X/Y."""
    out = dict(result)
    out["part"] = part.to_dict()
    out["parts"] = len(parts)
    out["key"] = f"{result['element']} · {part.name}" if len(parts) > 1 else result["element"]
    if not part.turned:
        return out
    out["frame_note"] = (
        f"{part.name} is turned {part.rotation:+.1f}° about X {part.pivot[0]:.2f}, Y {part.pivot[1]:.2f} "
        "so that it lies along the quay's axis, and is designed as a straight berth: stations, strips, "
        "zones and X/Y below are in that turned frame (plan X/Y given for the piles)."
    )
    if isinstance(out.get("punching"), list):
        out["punching"] = [_plan_xy(part, q) for q in out["punching"]]
    s = out.get("shear")
    if isinstance(s, dict) and isinstance(s.get("governing"), dict):
        out["shear"] = {**s, "governing": _plan_xy(part, s["governing"])}
    return out


def _plan_xy(part: Part, d: dict) -> dict:
    if not isinstance(d, dict) or d.get("x") is None or d.get("y") is None:
        return d
    x, y = rotate_points(part, np.array([d["x"]], float), np.array([d["y"]], float), back=True)
    return {**d, "plan_x": round(float(x[0]), 2), "plan_y": round(float(y[0]), 2)}


def plan_geometry(
    geometry: list[dict[str, Any]],
    elements: dict[str, dict[str, SheetData]],
    parts: list[Part],
    axes: dict[str, dict[str, str] | None],
) -> list[dict[str, Any]]:
    """The 3D view's geometry with each plate that is not a wall drawn part by part: its box in the
    part's turned frame, and the turn that brings it back to plan."""
    if not parts:
        return geometry
    boxes: dict[str, list[dict[str, Any]]] = {}
    for part in parts:
        from .geometry import elements_geometry

        for g in elements_geometry(part_elements(elements, parts, part, axes)):
            if "box" not in g or g.get("type") == "sheet_pile_wall":
                continue
            key = f"{g['element']} · {part.name}" if len(parts) > 1 else g["element"]
            entry = {**g, "key": key, "part": part.name}
            if part.turned:
                entry["turn"] = {"deg": part.rotation, "pivot": list(part.pivot)}
            boxes.setdefault(g["element"], []).append(entry)
    out = []
    for g in geometry:
        out.extend(boxes.get(g["element"], [g]) if "box" in g and g.get("type") != "sheet_pile_wall" else [g])
    return out


def plan_outline(part: Part, box: dict[str, list[float]]) -> list[list[float]]:
    """The corners in plan of a part's turned plan box."""
    xs, ys = box["X"], box["Y"]
    pts = [[xs[0], ys[0]], [xs[1], ys[0]], [xs[1], ys[1]], [xs[0], ys[1]]]
    return [[round(v, 3) for v in q] for q in _xy_list(part, pts)]


def section_parts(
    alignment: Any, elements: dict[str, dict[str, SheetData]], along: str = "Y"
) -> tuple[list[Part], dict[str, Any]]:
    """The parts a section's slabs and beams are designed in, and what was found. No parts (``[]``)
    when the berth is one straight run along the quay's axis: it is designed as it is."""
    info: dict[str, Any] = {"mode": getattr(alignment, "mode", "auto"), "points": [], "parts": []}
    if alignment is None or alignment.mode == "straight":
        info["text"] = "Straight berth (section setting)."
        return [], info
    if alignment.mode == "manual":
        points = [list(map(float, q)) for q in alignment.points]
        info["from"] = "the points given by hand"
    else:
        ref = reference_points(elements)
        if ref is None:
            info["text"] = (
                "No front or rear beam to find the berth's line from: designed as a straight berth."
            )
            return [], info
        name, pts = ref
        points = fit_alignment(pts, alignment.min_angle)
        info["from"] = f"{name}'s nodes"
    info["points"] = points
    parts = make_parts(points, along, alignment.min_angle, alignment.own_axes, land_point(elements))
    info["parts"] = [p.to_dict() for p in parts]
    if not parts or (len(parts) == 1 and not parts[0].turned):
        info["text"] = f"One straight run along the quay's axis (from {info['from']})."
        return [], info
    corners = len(parts) - 1
    info["text"] = (
        f"{len(parts)} part{'s' if len(parts) > 1 else ''} from {info['from']}"
        + (f", {corners} corner{'s' if corners > 1 else ''}" if corners else "")
        + ": "
        + "; ".join(p.label() for p in parts)
        + ". Slabs and beams are designed part by part, each turned to lie along the quay's axis."
    )
    return parts, info


def combine_parts(results: dict[str, Any]) -> dict[str, Any]:
    """Results with a corner berth's parts of each slab or beam as one entry again (for quantities):
    lengths, areas and tonnes add up, steel per metre or per m² is weighted by them, and the
    utilisation is the worst part's."""
    out = dict(results)
    for kind, size in (("beams", "length_m"), ("slabs", "area_m2")):
        merged: list[dict] = []
        at: dict[str, int] = {}
        for d in results.get(kind) or []:
            if (d.get("parts") or 1) <= 1:
                merged.append(d)
                continue
            name = d["element"]
            if name not in at:
                at[name] = len(merged)
                merged.append({**d, "steel": dict(d.get("steel") or {}), "key": name, "part": None})
                continue
            m = merged[at[name]]
            a, b = m["steel"], d.get("steel") or {}
            la, lb = a.get(size) or 0.0, b.get(size) or 0.0
            for k, v in b.items():
                if not isinstance(v, int | float) or isinstance(v, bool):
                    continue
                if k in (size, "total_t", "total_kg") or k.endswith("_t"):
                    a[k] = round((a.get(k) or 0) + v, 3)
                elif la + lb > 0 and isinstance(a.get(k), int | float):
                    a[k] = round((a[k] * la + v * lb) / (la + lb), 3)
            a[size] = round(la + lb, 3)
            # Links are counted zone by zone and pile by pile: every part's.
            if isinstance(d.get("punching"), list):
                m["punching"] = [*(m.get("punching") or []), *d["punching"]]
            if isinstance(d.get("shear"), dict) and isinstance(d["shear"].get("links"), list):
                sm = dict(m.get("shear") or {})
                sm["links"] = [*(sm.get("links") or []), *d["shear"]["links"]]
                m["shear"] = sm
            if d.get("utilisation") is not None:
                m["utilisation"] = max(m.get("utilisation") or 0.0, d["utilisation"])
            m["passed"] = bool(m.get("passed")) and bool(d.get("passed"))
        out[kind] = merged
    return out


def named_parts(results: dict[str, Any]) -> dict[str, Any]:
    """Results with each part of a corner berth's slab or beam named by its part ("Deck · Part 2"),
    for reports and files that list elements by name."""
    out = dict(results)
    for kind in ("beams", "slabs"):
        out[kind] = [
            {**d, "element": d["key"], "source_element": d["element"]}
            if (d.get("parts") or 1) > 1 and d.get("key")
            else d
            for d in results.get(kind) or []
        ]
    return out
