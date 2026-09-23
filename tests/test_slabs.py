import math

import numpy as np
import pandas as pd
import pytest
from conftest import PLATE_HEADER, pile_sheet

from triton.design.runner import run_section
from triton.design.slabs import bar_options, required_as, wood_armer, zones_for
from triton.project import DesignSettings, PileInput, Section, SlabInput
from triton.validation import import_sheets


def test_wood_armer_moments():
    m = wood_armer(np.array([100.0, -80.0]), np.array([50.0, -40.0]), np.array([20.0, 10.0]))
    # All sagging: bottom = M + |Mxy|, nothing on top.
    assert (m["bottom_x"][0], m["bottom_y"][0]) == (120.0, 70.0)
    assert (m["top_x"][0], m["top_y"][0]) == (0.0, 0.0)
    # All hogging: top = M − |Mxy|, nothing at the bottom.
    assert (m["top_x"][1], m["top_y"][1]) == (-90.0, -50.0)
    assert (m["bottom_x"][1], m["bottom_y"][1]) == (0.0, 0.0)


def test_required_steel_per_metre():
    fyd = 500 / 1.15
    a, k, _ = required_as(np.array([500.0]), np.array([0.0]), 1000, 900, 40, fyd)
    assert k[0] == pytest.approx(500e6 / (1000 * 900**2 * 40))
    assert a[0] == pytest.approx(500e6 / (0.95 * 900 * fyd))  # z capped at 0.95d
    # Axial tension adds steel, compression takes some away.
    t, _, _ = required_as(np.array([500.0]), np.array([-200.0]), 1000, 900, 40, fyd)
    c, _, _ = required_as(np.array([500.0]), np.array([200.0]), 1000, 900, 40, fyd)
    assert c[0] < a[0] < t[0]


def test_zones_merge_cells_into_rectangles():
    options = bar_options(DesignSettings())
    cells = pd.DataFrame({"i": [0, 1, 2, 0, 1, 2], "j": [0, 0, 0, 1, 1, 1], "idx": [0, 0, 5, 0, 0, 5]})
    ok = np.zeros((6, len(options)), bool)
    ok[:, 5:] = True
    ok[[0, 1, 3, 4], :] = True
    z = zones_for(cells, ok, options, 1.0, 0.0, 0.0, "X")
    assert z["basic_index"] == 0
    assert z["zones"] == [
        {
            "x": [2.0, 3.0],
            "y": [0.0, 2.0],
            "label": z["zones"][0]["label"],
            "as_mm2_per_m": round(options[5][0]),
        }
    ]


def deck_rows(fn, x_range=(-8.0, 0.0), y_range=(-4.0, 4.0), step=0.5, z=2.7):
    rows = [PLATE_HEADER]
    node = 1
    for x in np.arange(x_range[0], x_range[1] + 1e-9, step):
        for y in np.arange(y_range[0], y_range[1] + 1e-9, step):
            row = ["Plate\\_1\\_1", node, 1, float(x), float(y), z]
            for v in fn(x, y):
                row += [v, min(v, 0.0), max(v, 0.0)]
            rows.append(row)
            node += 1
    return rows


def piles_at(points, n=1500.0, z_top=2.7):
    rows = pile_sheet()[:1]
    for i, (x, y) in enumerate(points):
        for j, z in enumerate((z_top, z_top - 5)):
            row = ["EmbeddedBeam\\_1\\_1", 500 + 2 * i + j, 1, x, y, z]
            row += [-n, -n - 1, 0.0] + [0.0, 0.0, 0.0] * 3 + [100.0, 99.0, 101.0] + [0.0, 0.0, 0.0]
            rows.append(row + [1.0, 2.0, 3.0, "N/A"])
    return rows


def test_slab_design_with_zones_punching_and_restraint():
    def forces(x, y):
        # N_1, N_2, Q_12, Q_23, Q_13, M_11, M_22, M_12: sagging mid-span, hogging over the piles at x = -4.
        near = math.hypot(x + 4, y) < 1.5
        mx = -600.0 if near else 150.0
        return [0.0, 0.0, 0.0, 20.0, 30.0, mx, 80.0, 0.0]

    pts = [(-4.0, 0.0)]
    raw = {
        "Deck-PT-B-Apron": deck_rows(forces),
        "Deck-QP": deck_rows(lambda x, y: [0.0] * 5 + [100.0, 50.0, 0.0]),
        "Pile(1)-PT-B-Apron": piles_at(pts),
        "Pile(1)-QP": piles_at(pts, 1000.0),
    }
    wb = import_sheets(raw)
    els = {"Deck": SlabInput(thickness=800), "Pile(1)": PileInput(head_level=2.7)}
    res = run_section(DesignSettings(), Section(elements=els), wb)
    (d,) = res["slabs"]
    assert d["passed"], d["notes"]
    top = d["layers"]["top_x"]
    # Hogging over the pile needs heavier top bars there than the basic mesh.
    assert top["zones"] and all(z["as_mm2_per_m"] > top["basic"]["as_mm2_per_m"] for z in top["zones"])
    assert all(-6 <= z["x"][0] and z["x"][1] <= -2 for z in top["zones"])
    (p,) = d["punching"]
    assert (p["pile"], p["x"], p["y"], p["V_kN"]) == ("Pile(1)", -4.0, 0.0, 1500.0)
    assert p["u1_mm"] == round(math.pi * (1200 + 4 * p["d_mm"]))
    assert p["beta"] == pytest.approx(
        1 + 0.6 * math.pi * 100 / 1500 * 1000 / (1200 + 4 * p["d_mm"]), abs=1e-3
    )
    assert set(d["restraint"]["layers"]) == {"bottom_x", "bottom_y", "top_x", "top_y"}
    assert d["steel"]["kg_per_m3"] > 0 and d["bands"] and len(d["bands"][0]) == 5


def test_zones_split_where_bars_change_but_not_into_short_pieces():
    options = bar_options(DesignSettings())
    idx = [3, 3, 3, 7, 3, 3] + [0] * 18
    cells = pd.DataFrame({"i": list(range(6)) * 4, "j": [0] * 6 + [1] * 6 + [2] * 6 + [3] * 6, "idx": idx})
    ok = np.zeros((len(idx), len(options)), bool)
    for c, k in enumerate(idx):
        ok[c, k:] = True
    z = zones_for(cells, ok, options, 1.0, 0.0, 0.0, "X")
    # The 1 m piece needing option 7 joins its neighbour; the rest keeps option 3.
    assert [(q["x"], q["as_mm2_per_m"]) for q in z["zones"]] == [
        ([0.0, 4.0], round(options[7][0])),
        ([4.0, 6.0], round(options[3][0])),
    ]
    assert list(z["cell_index"][:6]) == [7, 7, 7, 7, 3, 3] and set(z["cell_index"][6:]) == {0}
