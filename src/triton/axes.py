"""Which way each moment and in-plane force acts, worked out from the results.

The workbook gives Plaxis local-axis actions (M_2, M_3 for beams; N_1, N_2,
M_11, M_22 for plates) but not the local axes themselves. Equilibrium ties
them to the geometry:

* beams: along the member, dM_3/dz = Q_12 and dM_2/dz = Q_13, so the shear
  that follows each moment's gradient shows which pair belongs together, and
  the larger moment is the main bending;
* plates: Q_13 = dM_11/dx1 + dM_12/dx2 and Q_23 = dM_12/dx1 + dM_22/dx2. The
  gradients are fitted from neighbouring nodes in global coordinates, and the
  assignment of local 1 to a global axis that makes the shears fit is the one
  the model uses. N_1 and N_2 act along the same local axes;
* vertical plates (walls) whose moments are too small to tell: the in-plane
  force that grows steadily with depth is the vertical one.

The quay line is the longer horizontal extent of the walls (combi wall or
sheet pile wall), else of the beams. Results are inferred: they are shown for
the engineer to confirm, and flagged when the fit is weak or the elements of
one kind disagree.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .elements import CombinationType, ElementType, ResultKind, combination_type
from .importer import SheetData
from .issues import Issue, Severity

CLEAR = 0.3  # the better assignment's rank correlation must reach this
MARGIN = 0.2  # and beat the other one by this much
NEIGHBOURS = 16
WALLS = (ElementType.COMBI_WALL, ElementType.SHEET_PILE_WALL)


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    """Rank correlation, so single FE spikes (at connections, element ends) do not decide it."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < 5:
        return np.nan
    ra, rb = pd.Series(a[m]).rank().to_numpy(), pd.Series(b[m]).rank().to_numpy()
    if np.std(ra) == 0 or np.std(rb) == 0:
        return np.nan  # nothing varies: no evidence either way
    return float(np.corrcoef(ra, rb)[0, 1])


def _governing(combos: dict[str, SheetData], cols: tuple[str, ...]) -> tuple[str, pd.DataFrame] | None:
    """The ULS combination with the largest moments, one row per node."""
    best, size = None, -1.0
    for c, s in combos.items():
        if combination_type(c) is CombinationType.SLS_QP or not set(cols) <= set(s.frame.columns):
            continue
        m = float(s.frame[list(cols)].abs().to_numpy().max()) if len(s.frame) else 0.0
        if m > size:
            best, size = c, m
    if best is None:
        return None
    f = combos[best].frame
    return best, (f.drop_duplicates("Node") if "Node" in f.columns else f).reset_index(drop=True)


def _flat(frame: pd.DataFrame, cols: tuple[str, ...]) -> bool:
    """Nothing to read directions from: the actions do not vary."""
    return all(float(np.ptp(frame[c].to_numpy(float))) < 1e-6 for c in cols)


def quay_line(elements: dict[str, dict[str, SheetData]]) -> str | None:
    """Global axis ("X" or "Y") the quay runs along, from the walls or else the beams."""
    for kinds in (WALLS, (ElementType.FRONT_BEAM, ElementType.REAR_BEAM)):
        frames = [
            s.frame
            for combos in elements.values()
            for s in combos.values()
            if s.parsed and s.parsed.spec.type in kinds and {"X", "Y"} <= set(s.frame.columns)
        ]
        if frames:
            f = pd.concat(frames)
            dx, dy = np.ptp(f["X"].to_numpy(float)), np.ptp(f["Y"].to_numpy(float))
            if max(dx, dy) > 1.0:
                return "Y" if dy >= dx else "X"
    return None


def _across(line: str | None) -> str | None:
    return None if line is None else ("X" if line == "Y" else "Y")


def beam_axes(name: str, combos: dict[str, SheetData], line: str | None) -> dict[str, Any] | None:
    got = _governing(combos, ("M_2", "M_3"))
    if got is None or not {"Q_12", "Q_13", "X", "Y", "Z"} <= set(got[1].columns):
        return None
    combo, f = got
    if _flat(f, ("M_2", "M_3")):
        return None
    parts = []
    for _, g in f.groupby([f["X"].round(2), f["Y"].round(2)], sort=False):
        g = g.sort_values("Z").drop_duplicates("Z")
        z = g["Z"].to_numpy(float)
        if len(g) < 5:
            continue
        parts.append(
            pd.DataFrame(
                {
                    "dM2": np.gradient(g["M_2"].to_numpy(float), z),
                    "dM3": np.gradient(g["M_3"].to_numpy(float), z),
                    "Q12": g["Q_12"].to_numpy(float),
                    "Q13": g["Q_13"].to_numpy(float),
                }
            )
        )
    if not parts:
        return None
    r = pd.concat(parts)
    # dM3/dz pairs with Q12 and dM2/dz with Q13 in Plaxis; a swapped pairing means the columns are mixed up.
    usual = min(abs(_corr(r["dM3"], r["Q12"])), abs(_corr(r["dM2"], r["Q13"])))
    swapped = min(abs(_corr(r["dM3"], r["Q13"])), abs(_corr(r["dM2"], r["Q12"])))
    if not np.isfinite(usual) or not np.isfinite(swapped):
        return None
    m2, m3 = float(f["M_2"].abs().max()), float(f["M_3"].abs().max())
    main = "M_3" if m3 >= m2 else "M_2"
    ratio = max(m2, m3) / max(min(m2, m3), 1e-9)
    clear = usual >= CLEAR and usual - swapped >= MARGIN
    across = _across(line)
    other = "M2" if main == "M_3" else "M3"
    plane = f", bending in the {across}–Z plane (across the quay)" if across and main == "M_3" else ""
    text = (
        f"{main.replace('_', '')} is the main moment, {ratio:.0f}× {other}{plane}. "
        f"M3 follows Q12 and M2 follows Q13 (fit {usual:.2f}; swapped {swapped:.2f}), in {combo}."
    )
    return {
        "element": name,
        "kind": "beam",
        "combination": combo,
        "main_moment": main,
        "moment_ratio": round(ratio, 1),
        "fit": round(usual, 2),
        "swapped_fit": round(swapped, 2),
        "local": {"2": across, "3": line} if across and main == "M_3" else None,
        "clear": clear,
        "text": text,
    }


def _gradients(points: np.ndarray, frame: pd.DataFrame, cols: list[str]) -> dict[str, np.ndarray]:
    """Least-squares plane through each node and its nearest neighbours: (n, 2) gradients per column."""
    n = len(points)
    k = min(NEIGHBOURS, n)
    idx = np.empty((n, k), int)
    for s in range(0, n, 400):
        d = ((points[s : s + 400, None, :] - points[None, :, :]) ** 2).sum(-1)
        idx[s : s + 400] = np.argsort(d, axis=1)[:, :k]
    a = np.concatenate([points[idx] - points[:, None, :], np.ones((n, k, 1))], axis=2)
    pinv = np.linalg.pinv(a)  # (n, 3, k)
    return {c: np.einsum("nik,nk->ni", pinv, frame[c].to_numpy(float)[idx])[:, :2] for c in cols}


def plate_axes(
    name: str, combos: dict[str, SheetData], line: str | None, vertical: bool
) -> dict[str, Any] | None:
    got = _governing(combos, ("M_11", "M_22"))
    cols = ["M_11", "M_22", "M_12", "Q_13", "Q_23", "N_1", "N_2"]
    if got is None or not set(cols) <= set(got[1].columns) or len(got[1]) < 10:
        return None
    combo, f = got
    if _flat(f, ("M_11", "M_22", "N_1", "N_2")):
        return None
    ext = {a: float(np.ptp(f[a].to_numpy(float))) for a in "XYZ"}
    plane = sorted("XYZ", key=lambda a: -ext[a])[:2]
    if vertical and "Z" not in plane:
        plane = [plane[0], "Z"]
    if min(ext[a] for a in plane) < 0.5:
        return None  # results along a line: no plane to fit
    g = _gradients(f[plane].to_numpy(float), f, ["M_11", "M_22", "M_12", "N_1", "N_2"])
    q13, q23 = f["Q_13"].to_numpy(float), f["Q_23"].to_numpy(float)

    def fit(i: int, j: int) -> float:  # local 1 along plane[i], local 2 along plane[j]
        a = _corr(q13, g["M_11"][:, i] + g["M_12"][:, j])
        b = _corr(q23, g["M_12"][:, i] + g["M_22"][:, j])
        return (abs(a) + abs(b)) / 2

    fits = {plane[0]: fit(0, 1), plane[1]: fit(1, 0)}
    fits = {a: 0.0 if not np.isfinite(v) else v for a, v in fits.items()}
    one = max(fits, key=fits.get)
    other_fit = min(fits.values())
    clear = fits[one] >= CLEAR and fits[one] - other_fit >= MARGIN
    how = f"the shears fit M11 and M22 that way ({fits[one]:.2f}; the other way {other_fit:.2f})"
    if not clear and vertical:
        # Moments too small to tell: the vertical force is the one that builds up steadily with depth
        # (skin friction), far more than the other. Median per 1 m band, down to 80% of the wall.
        top, toe = f["Z"].max(), f["Z"].min()
        body = f[f["Z"] >= toe + 0.2 * (top - toe)]
        band = body.groupby(body["Z"].round(0))[["N_1", "N_2"]].median()
        z = band.index.to_numpy(float)
        steady = {n: np.nan_to_num(abs(_corr(band[n].to_numpy(float), z))) for n in ("N_1", "N_2")}
        rise = {n: float(np.ptp(band[n].to_numpy(float))) for n in ("N_1", "N_2")}
        big, small = sorted(rise, key=rise.get, reverse=True)
        if steady[big] >= 0.8 and rise[big] >= 3 * max(rise[small], 1e-9):
            other = next(a for a in plane if a != "Z")
            one = "Z" if big == "N_1" else other
            clear = True
            how = (
                f"{big.replace('_', '')} builds up steadily with depth, by {rise[big]:.0f} kN/m against "
                f"{rise[small]:.0f} for {small.replace('_', '')} (the moments are too small to tell)"
            )
    two = next(a for a in plane if a != one)
    label = {"Z": "vertical", line: "along the quay", _across(line): "across the quay"}

    def word(a: str) -> str:
        return f"{a} ({label[a]})" if label.get(a) else a

    text = (
        f"Local 1 is global {word(one)} and local 2 is global {word(two)}: M11 and N1 act along {one}, "
        f"M22 and N2 along {two}. Found because {how}, in {combo}."
    )
    return {
        "element": name,
        "kind": "plate",
        "combination": combo,
        "local": {"1": one, "2": two},
        "fit": round(fits[one], 2),
        "swapped_fit": round(other_fit, 2),
        "clear": clear,
        "text": text,
    }


def infer_axes(elements: dict[str, dict[str, SheetData]]) -> tuple[list[dict[str, Any]], list[Issue]]:
    """Axis findings per element, and warnings where they are unclear or disagree."""
    line = quay_line(elements)
    found, issues = [], []
    for name, combos in elements.items():
        spec = next((s.parsed.spec for s in combos.values() if s.parsed), None)
        if spec is None:
            continue
        try:
            if spec.kind is ResultKind.BEAM:
                a = beam_axes(name, combos, line)
            else:
                a = plate_axes(name, combos, line, vertical=spec.type in WALLS)
        except (ValueError, np.linalg.LinAlgError):
            a = None
        if a is None:
            continue
        a["type"] = spec.type.value
        found.append(a)
        if not a["clear"]:
            issues.append(
                Issue(
                    Severity.WARNING,
                    "axes_unclear",
                    f"{name}: the directions of its actions could not be confirmed from the results. "
                    f"{a['text']} Check the local axes in the Plaxis model.",
                    element=name,
                )
            )
    # Elements of one type should agree (all piles bending mainly about the same local axis, all decks alike).
    by_type: dict[str, dict[str, list[str]]] = {}
    for a in found:
        if not a["clear"]:
            continue
        key = a.get("main_moment") if a["kind"] == "beam" else a["local"]["1"]
        by_type.setdefault(a["type"], {}).setdefault(key, []).append(a["element"])
    for kind, groups in by_type.items():
        if len(groups) > 1:
            desc = "; ".join(f"{k.replace('_', '')}: {', '.join(v)}" for k, v in groups.items())
            issues.append(
                Issue(
                    Severity.WARNING,
                    "axes_differ",
                    f"Elements of type {kind} do not share their directions ({desc}). One of them may have "
                    "its local axes turned in the Plaxis model.",
                )
            )
    return found, issues
