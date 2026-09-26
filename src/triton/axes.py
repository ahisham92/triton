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
GRID = 1.0  # m, cells the plate sign is read on
SUPPORT_CLEAR = 1.8  # m, cells this close to a pile or wall are left out of it
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
    name: str,
    combos: dict[str, SheetData],
    line: str | None,
    vertical: bool,
    supports: np.ndarray | None = None,
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
    g = _gradients(f[plane].to_numpy(float), f, ["M_11", "M_22", "M_12", "N_1", "N_2", "Q_13", "Q_23"])
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
    out = {
        "element": name,
        "kind": "plate",
        "combination": combo,
        "local": {"1": one, "2": two},
        "fit": round(fits[one], 2),
        "swapped_fit": round(other_fit, 2),
        "clear": clear,
        "text": text,
        "nodes": len(f),
    }
    if not vertical:
        out.update(plate_sign(combos, plane, plane.index(one), supports) or {})
    return out


def plate_sign(
    combos: dict[str, SheetData], plane: list[str], i: int, supports: np.ndarray | None = None
) -> dict[str, Any] | None:
    """Whether positive M11/M22 is sagging or hogging, from the plate's own equilibrium.

    Q = s·dM/dx ties the shear to the moment (s is Plaxis's shear sign, unknown here), and the load the
    plate carries between its supports, mostly gravity, makes div Q = -s·p. So positive M is hogging
    when the slope and the median div Q share a sign, whatever sign Plaxis gives Q. Nodal shears are
    noisy, so both are read on a 1 m grid of averages, away from the plate's edges and from anything
    that holds it up (pile heads, walls: `supports`, plan coordinates), where the reactions come in.
    Read in each combination; the answer is the one most of them give.
    """
    j = 1 - i
    votes: list[tuple[str, float, float, str]] = []
    for combo in sorted(combos, key=lambda c: (combination_type(c) is not CombinationType.SLS_QP, c)):
        f = combos[combo].frame
        cols = ["M_11", "M_22", "M_12", "Q_13", "Q_23"]
        if f is None or not {*cols, *plane} <= set(f.columns) or len(f) < 10:
            continue
        f = f.drop_duplicates("Node") if "Node" in f.columns else f
        u, v = f[plane[i]].to_numpy(float), f[plane[j]].to_numpy(float)
        far = np.ones(len(f), bool)
        if supports is not None and len(supports):
            su, sv = supports[:, "XYZ".index(plane[i])], supports[:, "XYZ".index(plane[j])]
            for s0 in range(0, len(su), 200):
                du = u[:, None] - su[None, s0 : s0 + 200]
                dv = v[:, None] - sv[None, s0 : s0 + 200]
                far &= (np.hypot(du, dv) > SUPPORT_CLEAR).all(1)
        cell = pd.DataFrame(
            {"i": np.floor(u / GRID).astype(int), "j": np.floor(v / GRID).astype(int), "far": far}
        )
        cell[cols] = f[cols].to_numpy(float)
        g = cell.groupby(["i", "j"])
        avg, ok = g[cols].mean(), g["far"].all()
        ii = np.arange(avg.index.get_level_values(0).min(), avg.index.get_level_values(0).max() + 1)
        jj = np.arange(avg.index.get_level_values(1).min(), avg.index.get_level_values(1).max() + 1)
        grid = {c: avg[c].unstack().reindex(index=ii, columns=jj).to_numpy(float) for c in cols}
        m = ok.unstack().reindex(index=ii, columns=jj).fillna(False).to_numpy(bool).copy()
        if m.shape[0] < 6 or m.shape[1] < 6:
            continue
        m[[0, 1, -2, -1], :] = False
        m[:, [0, 1, -2, -1]] = False

        def d1(x: np.ndarray, axis: int) -> np.ndarray:
            return (np.roll(x, -1, axis) - np.roll(x, 1, axis)) / (2 * GRID)

        lhs1 = d1(grid["M_11"], 0) + d1(grid["M_12"], 1)
        lhs2 = d1(grid["M_22"], 1) + d1(grid["M_12"], 0)
        div = d1(grid["Q_13"], 0) + d1(grid["Q_23"], 1)
        k = m & np.isfinite(lhs1) & np.isfinite(lhs2) & np.isfinite(div)
        if k.sum() < 20:
            continue
        a, b = _corr(grid["Q_13"][k], lhs1[k]), _corr(grid["Q_23"][k], lhs2[k])
        med = float(np.median(div[k]))
        if not (np.isfinite(a) and np.isfinite(b)) or np.sign(a) != np.sign(b) or abs(a + b) / 2 < 0.5:
            continue
        if abs(med) < 1.0 or np.mean(np.sign(div[k]) == np.sign(med)) < 0.6:
            continue  # no clear load between the supports
        votes.append(("hogging" if np.sign(a) * np.sign(med) > 0 else "sagging", (a + b) / 2, med, combo))
    if not votes:
        return None
    hog = sum(v[0] == "hogging" for v in votes)
    sign = "hogging" if hog * 2 > len(votes) else "sagging"
    agree = max(hog, len(votes) - hog)
    ex = next(v for v in votes if v[0] == sign)
    text = (
        f"Positive M11 and M22 are {sign}: away from the supports the shears follow the moment slopes with "
        f"a {'positive' if ex[1] > 0 else 'negative'} sign (r {abs(ex[1]):.2f}) and the load between the "
        f"supports gives a median div Q of {ex[2]:+.1f} kN/m² in {ex[3]}; {agree} of {len(votes)} "
        "combinations read agree."
    )
    return {"positive": sign, "positive_clear": agree == len(votes), "sign_text": text}


def _supports(elements: dict[str, dict[str, SheetData]], specs: dict[str, Any], name: str) -> np.ndarray:
    """Nodes (X, Y, Z) of everything that could hold a horizontal plate up: piles, walls, other plates' edges
    are not included (beams sharing the deck's edge carry it, but so does the deck them)."""
    pts = []
    for other, combos in elements.items():
        spec = specs.get(other)
        if other == name or spec is None or not (spec.kind is ResultKind.BEAM or spec.type in WALLS):
            continue
        f = next((s.frame for s in combos.values() if s.frame is not None), None)
        if f is not None and {"X", "Y", "Z"} <= set(f.columns):
            pts.append(f[["X", "Y", "Z"]].drop_duplicates().to_numpy(float))
    return np.concatenate(pts) if pts else np.empty((0, 3))


def _share_sign(found: list[dict[str, Any]], issues: list[Issue]) -> None:
    """Plates of one model share Plaxis's sign convention: a plate whose own results cannot tell (a narrow
    beam, a deck without clear load) takes the sign of the largest plate with the same local axes that can."""
    told = [a for a in found if a["kind"] == "plate" and a.get("positive") and a.get("positive_clear")]
    for a in found:
        if a["kind"] != "plate" or a.get("positive_clear") or a["type"] in {t.value for t in WALLS}:
            continue
        same = [t for t in told if t["local"] == a["local"]]
        if not same:
            continue
        src = max(same, key=lambda t: t.get("nodes", 0))
        own = a.get("positive")
        a["positive"], a["positive_clear"] = src["positive"], True
        a["sign_text"] = (
            f"Positive M11 and M22 are {src['positive']}, as in {src['element']}, which has the same "
            "local axes and shows it clearly."
        )
        a["sign_from"] = src["element"]
        if own and own != src["positive"]:
            a["sign_text"] += f" Its own results lean {own}, but not clearly."
    signs = {a["positive"] for a in told}
    if len(signs) > 1:
        desc = "; ".join(f"{a['element']}: {a['positive']}" for a in told)
        issues.append(
            Issue(
                Severity.WARNING,
                "plate_sign_differs",
                f"The plates do not agree on what positive M11 and M22 mean ({desc}). Check their local "
                "axes in the Plaxis model, and set 'Positive plate moments' in Design settings yourself.",
            )
        )


def sag_factor(setting: str, found: dict[str, Any] | None) -> tuple[float, str]:
    """+1 when positive plate moments are sagging, -1 when hogging, and the note that says why."""
    if setting != "auto":
        sag = 1.0 if setting == "sagging" else -1.0
        return sag, f"Positive plate moments taken as {setting} (Design settings)."
    if found and found.get("positive"):
        sag = 1.0 if found["positive"] == "sagging" else -1.0
        return sag, f"{found.get('sign_text', '')} (Design settings: Auto.)"
    return 1.0, (
        "Positive plate moments taken as sagging: Auto could not read the sign from the results. Check a "
        "moment you know (hogging over a pile head) and set 'Positive plate moments' in Design settings."
    )


def _corner(elements: dict[str, dict[str, SheetData]]) -> bool:
    """Whether the berth turns a corner (its front beam, else rear beam, is not one straight run)."""
    from .alignment import fit_alignment, reference_points

    ref = reference_points(elements)
    try:
        return ref is not None and len(fit_alignment(ref[1])) > 2
    except (ValueError, IndexError, np.linalg.LinAlgError):
        return False


CORNER_NOTE = (
    "The berth turns a corner, so the actions turn with it and act both ways: the directions cannot be "
    "confirmed from the results alone. Nothing to do if every element of the model keeps Plaxis's default "
    "local axes"
)


def infer_axes(elements: dict[str, dict[str, SheetData]]) -> tuple[list[dict[str, Any]], list[Issue]]:
    """Axis findings per element, and warnings where they are unclear or disagree. On a corner berth the
    actions act both ways, so piles and walls (designed for the resultant moment) and a beam plate that
    agrees with the deck are only noted, not warned about."""
    line = quay_line(elements)
    corner = _corner(elements)
    found, issues = [], []
    specs = {n: next((s.parsed.spec for s in c.values() if s.parsed), None) for n, c in elements.items()}
    for name, combos in elements.items():
        spec = specs[name]
        if spec is None:
            continue
        try:
            if spec.kind is ResultKind.BEAM:
                a = beam_axes(name, combos, line)
            else:
                vertical = spec.type in WALLS
                held = None if vertical else _supports(elements, specs, name)
                a = plate_axes(name, combos, line, vertical=vertical, supports=held)
        except (ValueError, np.linalg.LinAlgError):
            a = None
        if a is None:
            continue
        a["type"] = spec.type.value
        found.append(a)
        if not a["clear"] and corner and (a["kind"] == "beam" or spec.type in WALLS):
            a["corner"] = True
            issues.append(
                Issue(
                    Severity.INFO,
                    "axes_corner",
                    f"{name}: {CORNER_NOTE}"
                    + (
                        "; it is designed from N1, M11 and its shear column as a sheet pile wall always is. "
                        if spec.type is ElementType.SHEET_PILE_WALL
                        else "; it is designed for the resultant of its two moments, whichever local axis "
                        "carries more. "
                    )
                    + a["text"],
                    element=name,
                )
            )
        elif not a["clear"]:
            issues.append(
                Issue(
                    Severity.WARNING,
                    "axes_unclear",
                    f"{name}: the directions of its actions could not be confirmed from the results. "
                    f"{a['text']} Check the local axes in the Plaxis model.",
                    element=name,
                )
            )
    if corner:
        # A beam plate the results cannot confirm, read the same way as the deck: it keeps the deck's axes.
        deck = next(
            (a for a in found if a["kind"] == "plate" and a["clear"] and a["type"] == ElementType.SLAB.value),
            None,
        )
        for a in found:
            if a["kind"] == "plate" and not a["clear"] and deck and a["local"] == deck["local"]:
                if a["type"] in (ElementType.FRONT_BEAM.value, ElementType.REAR_BEAM.value):
                    a["clear"], a["corner"] = True, True
                    a["text"] += f" The same as the deck ({deck['element']}), which the results confirm."
                    issues[:] = [
                        i for i in issues if not (i.code == "axes_unclear" and i.element == a["element"])
                    ]
                    issues.append(
                        Issue(
                            Severity.INFO,
                            "axes_corner",
                            f"{a['element']}: {CORNER_NOTE}. Its results read the same way as the deck's, "
                            f"so it takes the deck's directions. {a['text']}",
                            element=a["element"],
                        )
                    )
    _share_sign(found, issues)
    # Elements of one type should agree (all piles bending mainly about the same local axis, all decks alike).
    by_type: dict[str, dict[str, list[str]]] = {}
    for a in found:
        if not a["clear"] or a.get("corner") or (corner and a["kind"] == "beam"):
            continue  # at a corner piles bend both ways: which local moment is larger varies
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
