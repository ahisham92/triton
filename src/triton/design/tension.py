"""Where the concrete is in tension, for the 3D view's "Tension zones" mode.

Stresses are those of the uncracked concrete section, σ = N/A ± M/W (N compression +), per result
point and combination (ULS and QP). A face is in tension when its stress is below −0.1 MPa, so
numerical noise around zero does not paint a face.

Each element returns ``{"points", "combinations", "codes", "legend"}``: ``points`` match the
utilisation bands (``[x, y, z]``, or ``[x, y, z, size]`` for slab cells and ``[x, y, z, size, why]``
for a slab square that shows the squares round it) and ``codes`` holds one character per point for
every combination and for the envelope (key ``""``). A character is ``chr(48 + bits)``, or a space
where the combination has no result at that point.

Bits: plates and beams, per direction (the Y direction of a slab shifted by 3 bits)
``1`` bottom face in tension, ``2`` top face, ``4`` whole section in tension (net axial tension);
piles ``1`` one side in tension from bending, ``2`` the tension side changes between combinations
(envelope only), ``4`` whole section in tension.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

LIMIT = 0.1  # MPa: tensile stress below this counts as none

PLATE = {1: "bottom in tension", 2: "top in tension", 4: "whole section in tension"}
PILE = {1: "one side in tension", 2: "tension side changes", 4: "whole section in tension"}


def face_bits(n_kn: np.ndarray, m_knm: np.ndarray, area_m2: float, w_m3: float) -> np.ndarray:
    """Bits per row for a section bent about one axis (M sagging +: bottom in tension)."""
    base = np.asarray(n_kn, float) / area_m2 / 1e3
    bend = np.asarray(m_knm, float) / w_m3 / 1e3
    bottom = base - bend < -LIMIT
    top = base + bend < -LIMIT
    return np.where(bottom & top, 4, np.where(bottom, 1, 0) | np.where(top, 2, 0)).astype(int)


def pile_bits(n_kn, m2_knm, m3_knm, diameter_mm: float) -> np.ndarray:
    d = diameter_mm / 1e3
    area = math.pi * d * d / 4
    w = math.pi * d**3 / 32
    m = np.hypot(np.asarray(m2_knm, float), np.asarray(m3_knm, float))
    base = np.asarray(n_kn, float) / area / 1e3
    bend = m / w / 1e3
    low, high = base - bend < -LIMIT, base + bend < -LIMIT
    return np.where(low & high, 4, np.where(low, 1, 0)).astype(int)


def _combos(names: pd.Series) -> list[str]:
    return list(dict.fromkeys(str(c) for c in names))


def pack(
    point: np.ndarray, combination: np.ndarray, bits: np.ndarray, n: int
) -> tuple[list[str], dict[str, str]]:
    """OR the bits per point and combination; one string per combination and the envelope."""
    frame = pd.DataFrame({"p": np.asarray(point, int), "c": np.asarray(combination).astype(str), "b": bits})
    frame = frame[(frame["p"] >= 0) & (frame["p"] < n)]
    combos = _combos(frame["c"])
    grouped = {
        bit: frame.assign(v=(frame["b"] & bit) > 0).groupby(["c", "p"])["v"].max()
        for bit in sorted({1 << k for k in range(6)})
    }
    seen = frame.groupby(["c", "p"]).size()
    codes: dict[str, str] = {}
    env = np.zeros(n, int)
    env_seen = np.zeros(n, bool)
    for c in combos:
        vals = np.zeros(n, int)
        mask = np.zeros(n, bool)
        idx = seen.loc[c].index.to_numpy(int)
        mask[idx] = True
        for bit, g in grouped.items():
            s = g.loc[c]
            vals[s.index.to_numpy(int)[s.to_numpy(bool)]] |= bit
        env |= vals
        env_seen |= mask
        codes[c] = "".join(chr(48 + v) if m else " " for v, m in zip(vals, mask, strict=True))
    codes[""] = "".join(chr(48 + v) if m else " " for v, m in zip(env, env_seen, strict=True))
    return combos, codes


def pile_tension(loads: pd.DataFrame, diameter_mm: float) -> dict[str, Any]:
    """Per pile position and 0.5 m band (as the utilisation bands). ``loads`` needs X, Y, Z, N, M_2, M_3."""
    need = {"X", "Y", "Z", "N", "M_2", "M_3", "combination"}
    if loads is None or loads.empty or not need <= set(loads.columns):
        return {}
    key = pd.DataFrame(
        {"x": loads["X"].round(2), "y": loads["Y"].round(2), "z": loads["Z"].mul(2).round() / 2}
    )
    pts = key.drop_duplicates().reset_index(drop=True)
    index = {tuple(r): i for i, r in enumerate(pts.itertuples(index=False))}
    point = np.array([index[tuple(r)] for r in key.itertuples(index=False)], int)
    m2 = loads["M_2"].to_numpy(float)
    m3 = loads["M_3"].to_numpy(float)
    bits = pile_bits(loads["N"].to_numpy(float), m2, m3, diameter_mm)
    combos, codes = pack(point, loads["combination"].to_numpy(), bits, len(pts))
    # The envelope flags points where bending puts different sides in tension in different combinations.
    bent = bits == 1
    if bent.any():
        f = pd.DataFrame({"p": point[bent], "a": m3[bent], "b": m2[bent]})
        f["m"] = np.hypot(f["a"], f["b"])
        f = f[f["m"] > 0]
        ref = f.loc[f.groupby("p")["m"].idxmax()].set_index("p")
        r = ref.loc[f["p"]]
        dot = (f["a"].to_numpy() * r["a"].to_numpy() + f["b"].to_numpy() * r["b"].to_numpy()) / (
            f["m"].to_numpy() * r["m"].to_numpy()
        )
        flips = set(f["p"].to_numpy()[dot < 0].tolist())
        env = list(codes[""])
        for p in flips:
            if env[p] != " ":
                env[p] = chr(ord(env[p]) | 2)
        codes[""] = "".join(env)
    return {
        "kind": "pile",
        "points": [[float(r.x), float(r.y), float(r.z)] for r in pts.itertuples(index=False)],
        "combinations": combos,
        "codes": codes,
    }


def beam_tension(
    frames: list[pd.DataFrame], start: float, band: float, locate, width_mm: float, depth_mm: float
) -> dict[str, Any]:
    """Per 0.5 m band along the beam from the vertical bending Mv (sagging +) and N (compression +).

    ``locate(i)`` gives the band's [x, y, z]."""
    f = pd.concat([x for x in frames if x is not None and len(x)], ignore_index=True) if frames else None
    if f is None or f.empty:
        return {}
    b, h = width_mm / 1e3, depth_mm / 1e3
    k = np.floor((f["s"].to_numpy(float) - start) / band).astype(int)
    ks = sorted(set(k.tolist()))
    pos = {v: i for i, v in enumerate(ks)}
    point = np.array([pos[v] for v in k], int)
    bits = face_bits(f["N"].to_numpy(float), f["Mv"].to_numpy(float), b * h, b * h * h / 6)
    combos, codes = pack(point, f["combination"].to_numpy(), bits, len(ks))
    return {"kind": "beam", "points": [locate(v) for v in ks], "combinations": combos, "codes": codes}


def slab_tension(
    frames: list[pd.DataFrame],
    cells: list[tuple[int, int]],
    x0: float,
    y0: float,
    size: float,
    level: float,
    h_mm: float,
    gaps: dict[tuple[int, int], tuple[list[tuple[int, int]], str]] | None = None,
) -> dict[str, Any]:
    """Per zone cell, for bars along X (Mx, Nx) in the low 3 bits and along Y (My, Ny) in the next 3.

    ``gaps``: squares with no result of their own (over a pile head, or no Plaxis node) and the squares
    they borrow from (``slabs.gap_cells``); they show the results of those squares together.
    """
    f = pd.concat([x for x in frames if x is not None and len(x)], ignore_index=True) if frames else None
    if f is None or f.empty or not cells:
        return {}
    why: dict[tuple[int, int], str] = {}
    if gaps:
        cells = list(cells)
        key = pd.MultiIndex.from_arrays([f["i"].astype(int), f["j"].astype(int)])
        extra = []
        for cell, (donors, reason) in gaps.items():
            rows = f[key.isin(donors)]
            if len(rows):
                extra.append(rows.assign(i=cell[0], j=cell[1]))
                cells.append(cell)
                why[cell] = reason
        if extra:
            f = pd.concat([f, *extra], ignore_index=True)
    h = h_mm / 1e3
    index = {c: i for i, c in enumerate(cells)}
    point = np.array([index.get((int(i), int(j)), -1) for i, j in zip(f["i"], f["j"], strict=True)], int)
    bx = face_bits(f["Nx"].to_numpy(float), f["Mx"].to_numpy(float), h, h * h / 6)
    by = face_bits(f["Ny"].to_numpy(float), f["My"].to_numpy(float), h, h * h / 6)
    combos, codes = pack(point, f["combination"].to_numpy(), bx | (by << 3), len(cells))
    return {
        "kind": "slab",
        "points": [
            [round(x0 + (i + 0.5) * size, 2), round(y0 + (j + 0.5) * size, 2), round(level, 2), size]
            + ([why[(i, j)]] if (i, j) in why else [])
            for i, j in cells
        ],
        "combinations": combos,
        "codes": codes,
    }
