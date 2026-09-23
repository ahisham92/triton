import numpy as np
import pandas as pd

from triton.axes import infer_axes
from triton.elements import parse_sheet_name
from triton.importer import SheetData


def sheet(name, frame):
    return SheetData(name, parse_sheet_name(name), frame)


def piles(swap=False):
    """Two piles loaded across the quay: M3 = 100 z², Q12 = dM3/dz; a small M2 with its Q13."""
    rows = []
    node = 1
    for y in (-4.0, 4.0):
        for z in np.linspace(-20, 0, 41):
            m3, q12 = 100 * z**2, 200 * z
            m2, q13 = 5 * np.sin(z), 5 * np.cos(z)
            if swap:  # the shear columns mixed up
                q12, q13 = q13, q12
            rows.append(
                {
                    "Node": node,
                    "X": -7.5,
                    "Y": y,
                    "Z": z,
                    "N": -1000.0,
                    "Q_12": q12,
                    "Q_13": q13,
                    "M_1": 0.0,
                    "M_2": m2,
                    "M_3": m3,
                }
            )
            node += 1
    return pd.DataFrame(rows)


def deck(local_one="X"):
    """A horizontal plate spanning along its local 1 axis: Q13 = dM11/dx1, Q23 = dM22/dx2."""
    xs, ys = np.meshgrid(np.linspace(-20, 0, 21), np.linspace(-15, 15, 31))
    x, y = xs.ravel(), ys.ravel()
    u, v = (x, y) if local_one == "X" else (y, x)
    frame = pd.DataFrame(
        {
            "Node": np.arange(len(x)) + 1,
            "X": x,
            "Y": y,
            "Z": 2.7,
            "N_1": 100.0 * np.sin(u / 3),
            "N_2": 10.0,
            "Q_12": 0.0,
            "Q_23": 30 * np.cos(v / 4) / 4 * 20,
            "Q_13": 300 * np.cos(u / 3) / 3,
            "M_11": 300 * np.sin(u / 3),
            "M_22": 30 * np.sin(v / 4) * 20,
            "M_12": 0.0,
        }
    )
    return frame


def wall():
    """A vertical wall along Y: vertical N1 builds up with depth, tiny noisy moments."""
    rng = np.random.default_rng(1)
    ys, zs = np.meshgrid(np.linspace(-16, 16, 17), np.linspace(-19, 2.5, 44))
    y, z = ys.ravel(), zs.ravel()
    n = len(y)
    return pd.DataFrame(
        {
            "Node": np.arange(n) + 1,
            "X": 0.0,
            "Y": y,
            "Z": z,
            "N_1": -300 + 25 * z,
            "N_2": -10 + 0.5 * z,
            "Q_12": 0.0,
            "Q_23": rng.normal(0, 1, n),
            "Q_13": rng.normal(0, 1, n),
            "M_11": rng.normal(0, 1, n),
            "M_22": rng.normal(0, 1, n),
            "M_12": rng.normal(0, 1, n),
        }
    )


def elements(**frames):
    return {el: {"PT-B-Apron": sheet(f"{el}-PT-B-Apron", f)} for el, f in frames.items()}


def test_piles_bend_across_the_quay():
    found, issues = infer_axes(elements(**{"Pile(1)": piles(), "SPW": wall()}))
    pile = next(a for a in found if a["element"] == "Pile(1)")
    assert pile["clear"] and pile["main_moment"] == "M_3" and pile["fit"] > 0.9
    assert "X–Z plane (across the quay)" in pile["text"]
    assert not [i for i in issues if i.element == "Pile(1)"]


def test_mixed_up_shears_are_flagged():
    found, issues = infer_axes(elements(**{"Pile(1)": piles(swap=True)}))
    assert not found[0]["clear"]
    assert [i.code for i in issues] == ["axes_unclear"]


def test_plate_local_axes_from_the_shears():
    for one, two in (("X", "Y"), ("Y", "X")):
        found, _ = infer_axes(elements(Deck=deck(one), SPW=wall()))
        d = next(a for a in found if a["element"] == "Deck")
        assert d["clear"] and d["local"] == {"1": one, "2": two}
    assert (
        "Local 1 is global X (across the quay)" in infer_axes(elements(Deck=deck(), SPW=wall()))[0][0]["text"]
    )


def test_wall_vertical_force_from_depth():
    found, _ = infer_axes(elements(SPW=wall()))
    (w,) = found
    assert w["clear"] and w["local"]["1"] == "Z" and "N1 builds up steadily with depth" in w["text"]


def test_elements_of_one_type_that_disagree():
    found, issues = infer_axes(
        elements(
            **{
                "Pile(1)": piles(),
                "Pile(2)": piles().rename(
                    columns={"M_2": "M_3", "M_3": "M_2", "Q_12": "Q_13", "Q_13": "Q_12"}
                ),
            }
        )
    )
    assert [i.code for i in issues] == ["axes_differ"]
