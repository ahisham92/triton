import math

import numpy as np
import pandas as pd
import pytest
from conftest import PLATE_HEADER, pile_sheet

from triton.design.runner import run_section
from triton.design.slabs import bar_options, required_as, wood_armer, zones_for
from triton.project import CraneArea, DesignSettings, PileInput, PunchingDepth, Section, SlabInput, SlabMesh
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
    els = {
        "Deck": SlabInput(thickness=800, crack_width_limit=0.3, crack_width_limit_bottom=0.3),
        "Pile(1)": PileInput(head_level=2.7),
    }
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
    z = zones_for(cells, ok, options, 1.0, 0.0, 0.0, "X", 2.0)
    # The 1 m piece needing option 7 joins its neighbour; the rest keeps option 3.
    assert [(q["x"], q["as_mm2_per_m"]) for q in z["zones"]] == [
        ([0.0, 4.0], round(options[7][0])),
        ([4.0, 6.0], round(options[3][0])),
    ]
    assert list(z["cell_index"][:6]) == [7, 7, 7, 7, 3, 3] and set(z["cell_index"][6:]) == {0}


def deck_workbook():
    def forces(x, y):
        mx = -600.0 if math.hypot(x + 4, y) < 1.5 else 150.0
        return [0.0, 0.0, 0.0, 20.0, 30.0, mx, 80.0, 0.0]

    pts = [(-4.0, 0.0)]
    return import_sheets(
        {
            "Deck-PT-B-Apron": deck_rows(forces),
            "Deck-QP": deck_rows(lambda x, y: [0.0] * 5 + [100.0, 50.0, 0.0]),
            "Pile(1)-PT-B-Apron": piles_at(pts),
            "Pile(1)-QP": piles_at(pts, 1000.0),
        }
    )


def design_deck(**slab):
    limits = {"crack_width_limit": 0.3, "crack_width_limit_bottom": 0.3} | slab
    els = {"Deck": SlabInput(thickness=800, **limits), "Pile(1)": PileInput(head_level=2.7)}
    return run_section(DesignSettings(), Section(elements=els), deck_workbook())["slabs"][0]


def test_slab_user_meshes_punching_depth_crane_and_peaks():
    plain = design_deck()
    d = design_deck(
        mesh_top_x=SlabMesh(diameter=20, spacing=150),
        punching_depths=[PunchingDepth(x=-4.0, y=0.0, thickness=900)],
        crane=[CraneArea(x_from=-8, x_to=-6, y_from=-4, y_to=4, my=900.0)],
    )
    top = d["layers"]["top_x"]
    assert top["basic"]["label"] == "Ø20 @ 150" and top["basic"]["set_by"] == "user"
    assert plain["layers"]["top_x"]["basic"]["set_by"] == "least steel"
    (p,) = d["punching"]
    assert p["thickness_mm"] == 900 and p["thickness_from"] == "set for this pile"
    assert p["d_mm"] == plain["punching"][0]["d_mm"] + 100
    assert p["r_u1_mm"] == 600 + 2 * p["d_mm"]
    # The crane adds sagging My over X -8 to -6: heavier bottom bars along Y there only.
    zones = d["layers"]["bottom_y"]["zones"]
    assert zones and all(z["x"][0] >= -8 and z["x"][1] <= -5 for z in zones)
    assert not plain["layers"]["bottom_y"]["zones"]
    assert any("Mobile crane" in n for n in d["notes"])
    # Averaging the hogging over a ring round the pile needs less top steel there.
    avg = design_deck(peaks="average")
    most = lambda r: max([z["as_mm2_per_m"] for z in r["layers"]["top_x"]["zones"]] or [0])  # noqa: E731
    assert most(avg) < most(plain)


def test_mesh_with_additional_bars_or_mesh_only():
    d = design_deck()
    top = d["layers"]["top_x"]
    assert top["mode"] == "mesh_and_additional" and top["zones"]
    # Zones add bars between the mesh bars; the mesh itself stays everywhere.
    assert all(0 < z["additional_mm2_per_m"] < z["as_mm2_per_m"] for z in top["zones"])
    assert all(
        z["as_mm2_per_m"] - z["additional_mm2_per_m"] == top["basic"]["as_mm2_per_m"] for z in top["zones"]
    )
    only = design_deck(layout_top_x="mesh_only")["layers"]["top_x"]
    assert only["mode"] == "mesh_only" and not only["zones"] and only["utilisation"] <= 1
    assert only["basic"]["as_mm2_per_m"] >= max(z["as_mm2_per_m"] for z in top["zones"]) * 0.8
