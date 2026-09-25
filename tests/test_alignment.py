"""Corner berths: parts found from the front beam, turned onto the quay's axis and designed as straight."""

import copy
import math
from dataclasses import replace

import numpy as np
import pytest
from test_slabs import deck_rows, piles_at

from triton.alignment import (
    TENSORS,
    VECTORS,
    combine_parts,
    fit_alignment,
    make_parts,
    named_parts,
    part_of,
    rotate_points,
    rotate_tensor,
    rotate_vector,
    stations,
    trim_ends,
    turn_frame,
)
from triton.design.runner import run_section
from triton.elements import ResultKind
from triton.project import Alignment, DesignSettings, PileInput, Section, SlabInput, default_element
from triton.validation import import_sheets


def strip(x0, x1, y0, y1, step=0.25):
    nx, ny = round((x1 - x0) / step) + 1, round((y1 - y0) / step) + 1
    xs, ys = np.meshgrid(np.linspace(x0, x1, nx), np.linspace(y0, y1, ny))
    return np.c_[xs.ravel(), ys.ravel()]


def turned(points, deg, pivot=(0.0, 0.0)):
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    d = points - pivot
    return np.c_[pivot[0] + c * d[:, 0] - s * d[:, 1], pivot[1] + s * d[:, 0] + c * d[:, 1]]


def test_straight_front_beam_is_one_part_along_y():
    pts = strip(-1, 1, -16.8, 16.8)
    assert fit_alignment(pts) == [[0.0, -16.8], [0.0, 16.8]]
    (part,) = make_parts(fit_alignment(pts), land=(-10.0, 0.0))
    assert not part.turned


@pytest.mark.parametrize("deg", [-3.0, -25.0, -60.0, 20.0])
def test_a_corner_is_found_where_the_front_beam_turns(deg):
    pts = strip(-1, 1, -16.8, 16.8)
    upper = pts[:, 1] >= 0
    pts[upper] = turned(pts[upper], deg)
    points = fit_alignment(pts)
    assert len(points) == 3
    assert points[1] == pytest.approx([0.0, 0.0], abs=1e-6)
    parts = make_parts(points, land=(-10.0, -5.0))
    assert not parts[0].turned
    assert parts[1].rotation == pytest.approx(-deg, abs=1e-6)
    # The line halving the corner splits the plan.
    probe = np.array([[-5.0, -5.0], turned(np.array([[-5.0, 5.0]]), deg)[0]])
    assert list(part_of(parts, probe[:, 0], probe[:, 1])) == [0, 1]


def test_two_corners_and_short_runs():
    pts = strip(-1, 1, -16.8, 16.8)
    top = pts[:, 1] >= 5
    pts[top] = turned(pts[top], -20, (0, 5))
    mid = pts[:, 1] >= -8
    pts[mid] = turned(pts[mid], -15, (0, -8))
    points = fit_alignment(pts)
    assert len(points) == 4
    assert points[1] == pytest.approx([0.0, -8.0], abs=1e-6)
    rot = [p.rotation for p in make_parts(points, land=(-10.0, -10.0))]
    assert rot == pytest.approx([0.0, 15.0, 35.0], abs=1e-6)


def test_land_side_decides_which_way_a_part_turns():
    # A quay running along X with the land at −Y: turned −90° it runs along Y with the land at −X.
    (p,) = make_parts([[-10.0, 0.0], [10.0, 0.0]], land=(0.0, -8.0))
    assert p.rotation == pytest.approx(-90.0)
    x, y = rotate_points(p, np.array([0.0]), np.array([-8.0]))
    assert x[0] < p.pivot[0]


def test_tensor_and_vector_turn():
    m11, m22, m12 = rotate_tensor(np.array([100.0]), np.array([0.0]), np.array([0.0]), 90.0)
    assert (m11[0], m22[0], m12[0]) == pytest.approx((0.0, 100.0, 0.0), abs=1e-9)
    a = np.array([120.0, -40.0, 15.0])
    b = rotate_tensor(*a, 37.0)
    assert b[0] + b[1] == pytest.approx(a[0] + a[1])  # the trace
    assert b[0] * b[1] - b[2] ** 2 == pytest.approx(a[0] * a[1] - a[2] ** 2)  # the determinant
    assert rotate_vector(1.0, 0.0, 90.0) == pytest.approx((0.0, 1.0))


# --- A small berth: deck, front beam and piles, then the same berth turned or with a corner ---------


def plate(x, y):
    # Hogging over the piles, sagging between, a twisting moment and shears: every component non-zero.
    m = 300.0 * math.cos(math.pi * y / 4) - 150.0 * math.sin(math.pi * x / 8)
    return [-50.0 + 3 * x, -80.0 + y, 12.0, 30.0 + y, 20.0 - x, m, 0.4 * m + 60.0, 25.0 + 2 * y]


def berth():
    piles = [(-7.0, y) for y in (-6.0, -2.0, 2.0, 6.0)] + [(-3.5, y) for y in (-6.0, -2.0, 2.0, 6.0)]
    beam = dict(x_range=(-1.0, 1.0), y_range=(-8.0, 8.0), step=0.25)
    deck = dict(x_range=(-9.0, -1.0), y_range=(-8.0, 8.0), step=0.5)
    return import_sheets(
        {
            "Deck-PT-B-Apron": deck_rows(plate, **deck),
            "Deck-QP": deck_rows(lambda x, y: [0.6 * v for v in plate(x, y)], **deck),
            "Front Beam-PT-B-Apron": deck_rows(plate, **beam),
            "Front Beam-QP": deck_rows(lambda x, y: [0.6 * v for v in plate(x, y)], **beam),
            "Pile(1)-PT-B-Apron": piles_at(piles),
            "Pile(1)-QP": piles_at(piles, 1000.0),
        }
    )


def turn_workbook(wb, deg, pivot=(0.0, 0.0), where=None):
    out = copy.copy(wb)
    sheets = []
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    for sh in wb.sheets:
        f = sh.frame
        if sh.parsed is None or f.empty:
            sheets.append(sh)
            continue
        f = f.copy()
        m = np.ones(len(f), bool) if where is None else where(f["X"].to_numpy(), f["Y"].to_numpy())
        dx, dy = f["X"].to_numpy() - pivot[0], f["Y"].to_numpy() - pivot[1]
        f.loc[m, "X"] = (pivot[0] + c * dx - s * dy)[m]
        f.loc[m, "Y"] = (pivot[1] + s * dx + c * dy)[m]
        if sh.parsed.spec.kind is ResultKind.PLATE:
            for cols in TENSORS:
                new = rotate_tensor(*(f[k].to_numpy() for k in cols), deg)
                for k, v in zip(cols, new, strict=True):
                    f.loc[m, k] = v[m]
            for cols in VECTORS:
                new = rotate_vector(*(f[k].to_numpy() for k in cols), deg)
                for k, v in zip(cols, new, strict=True):
                    f.loc[m, k] = v[m]
        sheets.append(replace(sh, frame=f))
    out.sheets = sheets
    return out


def section(**kw):
    els = {
        "Deck": SlabInput(thickness=700, crack_width_limit=0.3, crack_width_limit_bottom=0.3),
        "Front Beam": default_element("Front Beam"),
        "Pile(1)": PileInput(head_level=2.7),
    }
    # The model's ends are kept unless a test cuts them: turned models then match node for node.
    return Section(elements=els, **{"end_trim": 0.0, **kw})


ONLY = ["Deck", "Front Beam"]


def summary(d):
    return (d["utilisation"], d["steel"].get("kg_per_m3"), d["steel"].get("kg_per_m"), d.get("passed"))


def test_a_turned_berth_designs_as_the_straight_one():
    wb = berth()
    straight = run_section(DesignSettings(), section(), wb, only=ONLY)
    assert straight["alignment"]["text"].startswith("One straight run")
    assert "part" not in straight["slabs"][0]
    for deg in (30.0, -120.0):
        out = run_section(DesignSettings(), section(), turn_workbook(wb, deg), only=ONLY)
        (slab,) = out["slabs"]
        (beam,) = out["beams"]
        assert slab["part"]["rotation_deg"] == pytest.approx(-deg, abs=1e-6)
        assert slab["key"] == "Deck" and "turned" in slab["frame_note"]
        assert summary(slab) == summary(straight["slabs"][0])
        assert summary(beam) == summary(straight["beams"][0])
        for k, lay in straight["slabs"][0]["layers"].items():
            # The same bars; the zones' X, Y are in the part's turned frame.
            assert slab["layers"][k]["basic"] == lay["basic"]
            assert [z["label"] for z in slab["layers"][k]["zones"]] == [z["label"] for z in lay["zones"]]
        # Pile positions for the punching table in plan too: where the turned model has them.
        plan = turned(np.array([[q["x"], q["y"]] for q in straight["slabs"][0]["punching"]]), deg)
        got = np.array([[q["plan_x"], q["plan_y"]] for q in slab["punching"]])
        assert got == pytest.approx(np.round(plan, 2), abs=0.02)


def test_a_corner_berth_is_designed_part_by_part():
    wb = berth()
    corner = turn_workbook(wb, -25.0, where=lambda x, y: y >= 0)
    out = run_section(DesignSettings(), section(), corner, only=ONLY)
    assert [p["rotation_deg"] for p in out["alignment"]["parts"]] == pytest.approx([0.0, 25.0], abs=1e-6)
    assert [d["key"] for d in out["slabs"]] == ["Deck · Part 1", "Deck · Part 2"]
    assert [d["key"] for d in out["beams"]] == ["Front Beam · Part 1", "Front Beam · Part 2"]
    # Each part is the straight berth's half of the same nodes: the same design.
    ua, ub = np.array([0.0, 1.0]), np.array([math.sin(math.radians(25)), math.cos(math.radians(25))])
    n = ua + ub
    nb = turned(np.array([n]), 25.0)[0]
    half = {
        0: lambda x, y: (y < 0) & (x * n[0] + y * n[1] < -1e-9),
        1: lambda x, y: (y >= 0) & (x * nb[0] + y * nb[1] >= -1e-9),
    }
    for i, keep in half.items():
        part_wb = copy.copy(wb)
        part_wb.sheets = [
            replace(s, frame=s.frame[keep(s.frame["X"].to_numpy(), s.frame["Y"].to_numpy())])
            if s.parsed is not None and not s.frame.empty
            else s
            for s in wb.sheets
        ]
        ref = run_section(DesignSettings(), section(alignment=Alignment(mode="straight")), part_wb, only=ONLY)
        assert summary(out["slabs"][i]) == summary(ref["slabs"][0])
        assert summary(out["beams"][i]) == summary(ref["beams"][0])
    # Quantities add the parts up; reports name each part.
    whole = combine_parts(out)
    assert [d["element"] for d in whole["slabs"]] == ["Deck"]
    assert whole["slabs"][0]["steel"]["area_m2"] == pytest.approx(
        sum(d["steel"]["area_m2"] for d in out["slabs"]), abs=1e-3
    )
    assert [d["element"] for d in named_parts(out)["beams"]] == ["Front Beam · Part 1", "Front Beam · Part 2"]
    # "Straight" by hand designs it in one piece, as before.
    one = run_section(DesignSettings(), section(alignment=Alignment(mode="straight")), corner, only=ONLY)
    assert [d.get("key") for d in one["slabs"]] == [None]


def test_corner_points_by_hand():
    corner = turn_workbook(berth(), -25.0, where=lambda x, y: y >= 0)
    end = turned(np.array([[0.0, 8.0]]), -25.0)[0].tolist()
    manual = Alignment(mode="manual", points=[[0.0, -8.0], [0.0, 0.0], end])
    out = run_section(DesignSettings(), section(alignment=manual), corner, only=["Deck"])
    auto = run_section(DesignSettings(), section(), corner, only=["Deck"])
    assert [summary(d) for d in out["slabs"]] == [summary(d) for d in auto["slabs"]]


def test_turn_frame_moves_beam_elements_only():
    wb = berth()
    (part,) = make_parts([[0.0, 0.0], [10.0, 10.0]], land=(-5.0, 5.0))
    pile = next(s for s in wb.sheets if s.name.startswith("Pile(1)"))
    f = turn_frame(pile.frame, part, pile.parsed.spec.kind)
    assert (f["M_3"] == pile.frame["M_3"]).all() and not np.allclose(f["X"], pile.frame["X"])
    deck = next(s for s in wb.sheets if s.name == "Deck-PT-B-Apron")
    g = turn_frame(deck.frame, part, deck.parsed.spec.kind)
    assert "M_11_min" not in g and np.allclose(g["M_11"] + g["M_22"], deck.frame["M_11"] + deck.frame["M_22"])


def test_a_corner_takes_every_ultimate_sheet():
    # A second, larger ultimate case: every part is designed for both and the larger one governs,
    # the same as designing the corner with that case alone.
    wb = berth()
    big = {
        "Deck-PT-B-Yard": ("Deck-PT-B-Apron", 1.5),
        "Front Beam-PT-B-Yard": ("Front Beam-PT-B-Apron", 1.5),
        "Pile(1)-PT-B-Yard": ("Pile(1)-PT-B-Apron", 1.0),
    }
    both = copy.copy(wb)
    both.sheets = list(wb.sheets)
    for name, (src, k) in big.items():
        sh = next(s for s in wb.sheets if s.name == src)
        f = sh.frame.copy()
        cols = [c for c in f.columns if c not in ("X", "Y", "Z", "Node", "Element")]
        f[cols] = f[cols].apply(lambda c, k=k: c * k if c.dtype.kind == "f" else c)
        both.sheets.append(
            replace(sh, name=name, frame=f, parsed=replace(sh.parsed, combination="PT-B-Yard"))
        )
    corner = turn_workbook(both, -25.0, where=lambda x, y: y >= 0)
    out = run_section(DesignSettings(), section(), corner, only=ONLY)
    alone = copy.copy(corner)
    alone.sheets = [s for s in corner.sheets if s.parsed is None or s.parsed.combination != "PT-B-Apron"]
    ref = run_section(DesignSettings(), section(), alone, only=ONLY)
    assert len(out["slabs"]) == len(out["beams"]) == 2
    for a, b in zip(out["slabs"] + out["beams"], ref["slabs"] + ref["beams"], strict=True):
        assert summary(a) == summary(b)
    assert {d["shear"]["governing"]["combination"] for d in out["beams"]} == {"PT-B-Yard"}


# --- The FE edges: results near the model's ends are left out, never at a corner --------------------


def test_stations_run_round_a_corner_and_past_the_ends():
    pts = [[0.0, -10.0], [0.0, 0.0], [10.0, 10.0]]
    x = np.array([0.5, -0.5, 0.0, 3.0, 12.0])
    y = np.array([-12.0, -5.0, 0.0, 3.0, 12.0])
    st = stations(pts, "Y", x, y)
    diag = math.hypot(10, 10)
    assert st == pytest.approx([-2.0, 5.0, 10.0, 10 + math.hypot(3, 3), 10 + diag + math.hypot(2, 2)])
    assert stations(None, "Y", x, y) == pytest.approx(y)


def test_trim_leaves_out_each_elements_ends_but_not_the_corner_or_piles():
    wb = berth()
    corner = turn_workbook(wb, -25.0, where=lambda x, y: y >= 0)
    elements = corner.elements()
    pts = [[0.0, -8.0], [0.0, 0.0], turned(np.array([[0.0, 8.0]]), -25.0)[0].tolist()]
    out, info = trim_ends(elements, pts, "Y", 2.0, keep={"Pile(1)"})
    assert info["corner"] and set(info["ends_m"]) == {"Deck", "Front Beam"}
    for name, (lo, hi) in info["ends_m"].items():
        for combo, sh in out[name].items():
            before = elements[name][combo].frame
            st = stations(pts, "Y", sh.frame["X"].to_numpy(), sh.frame["Y"].to_numpy())
            assert st.min() >= lo + 2.0 - 1e-6 and st.max() <= hi - 2.0 + 1e-6
            # Round the corner (8 m in from the start) nothing is lost.
            all_st = stations(pts, "Y", before["X"].to_numpy(), before["Y"].to_numpy())
            assert len(sh.frame) == int(((all_st >= lo + 2.0 - 1e-6) & (all_st <= hi - 2.0 + 1e-6)).sum())
            assert (np.abs(all_st - 8.0) < 1.0).sum() == (np.abs(st - 8.0) < 1.0).sum() > 0
    assert out["Pile(1)"] == elements["Pile(1)"]
    assert trim_ends(elements, pts, "Y", 0.0) == (elements, None)


def test_a_trimmed_corner_keeps_its_parts_and_designs_without_the_ends():
    corner = turn_workbook(berth(), -25.0, where=lambda x, y: y >= 0)
    whole = run_section(DesignSettings(), section(), corner, only=ONLY)
    cut = run_section(DesignSettings(), section(end_trim=2.0), corner, only=ONLY)
    assert cut["end_trim"]["trim_m"] == 2.0 and whole["end_trim"] is None
    assert [p["rotation_deg"] for p in cut["alignment"]["parts"]] == pytest.approx([0.0, 25.0], abs=1e-6)
    for a, b in zip(whole["beams"], cut["beams"], strict=True):
        assert b["end_m"] - b["start_m"] < a["end_m"] - a["start_m"]
        assert any("2 m at each end along the berth" in n for n in b["notes"])


def test_every_section_leaves_out_2_m_along_the_berth_and_nothing_across():
    from fastapi.testclient import TestClient

    from triton.api import app
    from triton.fresh import fingerprint
    from triton.project import Project

    c = TestClient(app)
    p = c.post("/api/projects", json={"info": {"name": "Trim"}}).json()
    assert (p["sections"][0]["end_trim"], p["sections"][0]["side_trim"]) == (2.0, 0.0)
    saved = Section.model_validate({"name": "Old"})
    assert (saved.end_trim, saved.side_trim) == (2.0, 0.0)
    project = Project(sections=[saved])
    trimmed = fingerprint(project, saved, None)["working zone and peaks"]
    none = saved.model_copy(update={"end_trim": 0.0})
    before = fingerprint(project, none, None)["working zone and peaks"]
    assert trimmed != before  # the 2 m asks for one redesign
    sides = none.model_copy(update={"side_trim": 1.0})
    assert fingerprint(project, sides, None)["working zone and peaks"] != before


def test_both_ends_are_cut_on_one_line_inside_the_element_that_stops_first():
    # The deck stops 3 m before the front beam at the start and 1 m at the far end: every element is
    # cut 2 m inside the deck's ends, so the front beam keeps the deck's length of one structural system.
    beam = dict(x_range=(-1.0, 1.0), y_range=(-8.0, 8.0), step=0.25)
    deck = dict(x_range=(-9.0, -1.0), y_range=(-5.0, 7.0), step=0.5)
    wb = import_sheets(
        {
            "Deck-PT-B-Apron": deck_rows(plate, **deck),
            "Front Beam-PT-B-Apron": deck_rows(plate, **beam),
            "Pile(1)-PT-B-Apron": piles_at([(-4.0, 0.0)]),
        }
    )
    elements = wb.elements()
    pts = [[0.0, -8.0], [0.0, 8.0]]
    out, info = trim_ends(elements, pts, "Y", 2.0, keep={"Pile(1)"})
    assert info["cut_m"] == pytest.approx([5.0, 13.0]) and info["skew_deg"] == [0.0, 0.0]
    for name in ("Deck", "Front Beam"):
        y = out[name]["PT-B-Apron"].frame["Y"]
        assert y.min() == pytest.approx(-3.0) and y.max() == pytest.approx(5.0)
    # A skewed end (the pile rows at 30° there): the cut runs parallel to it.
    t = math.tan(math.radians(30))
    skew = copy.copy(wb)
    skew.sheets = [
        replace(sh, frame=sh.frame[sh.frame["Y"] >= -5.0 - t * (sh.frame["X"] + 1.0) - 1e-9])
        if sh.parsed is not None and sh.parsed.element != "Pile(1)"
        else sh
        for sh in wb.sheets
    ]
    _, info = trim_ends(skew.elements(), pts, "Y", 2.0, keep={"Pile(1)"})
    assert info["skew_deg"][0] == pytest.approx(30.0, abs=1.0) and info["skew_deg"][1] == 0.0
