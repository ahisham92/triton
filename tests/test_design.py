import io
import math

import numpy as np
import pytest
from conftest import EMBEDDED_HEADER, pile_sheet
from fastapi.testclient import TestClient
from openpyxl import Workbook

from triton.api import app
from triton.design.circular import (
    CircularSection,
    ConcreteLaw,
    Ring,
    SteelLaw,
    hull_indices,
    radial_utilisation,
)
from triton.design.piles import (
    MAX_RATIO,
    MIN_BAR,
    MIN_BARS,
    candidate_arrangements,
    design_pile,
    make_arrangement,
    min_area_pile,
)
from triton.design.runner import run_piles
from triton.project import Casing, DesignSettings, PileInput, Section
from triton.validation import import_sheets

C40 = ConcreteLaw(40)
B500 = SteelLaw(500)


def section(n=20, phi=32, radius=497.0, *inner):
    return CircularSection(1200, (Ring(n, phi, radius), *inner), C40, B500)


# --- N-M interaction ---------------------------------------------------------------


def test_interaction_end_points():
    s = section()
    curve = s.interaction()
    fcd, fyd = C40.fcd, B500.fyd
    # Uniform strain eps_c2 = 0.002: steel at 400 MPa (below fyd), displaced concrete deducted.
    n0 = fcd * (s.area_concrete - s.area_steel) + 400 * s.area_steel
    assert curve[0] == pytest.approx([n0 / 1e3, 0.0], rel=1e-3)
    assert curve[-1] == pytest.approx([-s.area_steel * fyd / 1e3, 0.0])
    assert (curve[:, 1] >= 0).all()


def fibre_moment_at_zero_axial(s: CircularSection, rotation=0.0, cells=600) -> float:
    """Independent check: square grid of concrete fibres, neutral axis found by bisection."""
    r = s.diameter / 2
    g = (np.arange(cells) + 0.5) / cells * s.diameter - r
    yy, xx = np.meshgrid(g, g)
    inside = xx**2 + yy**2 <= r * r
    y = yy[inside]  # height above the centre
    da = (s.diameter / cells) ** 2
    yb = np.concatenate(
        [
            ring.radius * np.cos(rotation + 2 * math.pi * np.arange(ring.count) / ring.count)
            for ring in s.rings
        ]
    )
    ab = np.concatenate([np.full(ring.count, math.pi * ring.diameter**2 / 4) for ring in s.rings])

    def forces(x):
        eps = lambda h: C40.eps_cu2 * (h - (r - x)) / x  # noqa: E731
        sc = C40.stress(eps(y))
        ss = B500.stress(eps(yb)) - C40.stress(eps(yb))
        return (sc * da).sum() + (ss * ab).sum(), (sc * da * y).sum() + (ss * ab * yb).sum()

    lo, hi = 1.0, s.diameter
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (lo, mid) if forces(mid)[0] > 0 else (mid, hi)
    return forces(lo)[1] / 1e6


@pytest.mark.parametrize(
    "s, rotation",
    [
        (section(), 0.0),
        (section(), math.pi / 20),
        (section(26, 32, 497.0, Ring(13, 16, 441.0)), math.pi / 13),
    ],
)
def test_pure_bending_matches_independent_fibre_model(s, rotation):
    expected = fibre_moment_at_zero_axial(s, rotation)
    c = s.interaction(rotation)
    order = np.argsort(c[:, 0])
    assert np.interp(0.0, c[order, 0], c[order, 1]) == pytest.approx(expected, rel=0.01)


def test_radial_utilisation():
    curve = section().interaction()
    mid = curve[len(curve) // 2]
    u = radial_utilisation(curve, np.array([mid[0], mid[0] / 2, 0.0]), np.array([mid[1], mid[1] / 2, 0.0]))
    assert u == pytest.approx([1.0, 0.5, 0.0], abs=1e-6)


def test_utilisation_takes_the_worse_bar_orientation():
    s = section(n=8)
    n, m = np.array([2000.0]), np.array([1500.0])
    each = [radial_utilisation(s.interaction(r), n, m)[0] for r in (0.0, math.pi / 8)]
    assert s.utilisation(n, m)[0] == pytest.approx(max(each))
    assert s.moment_capacity(2000.0) < 1500.0 / max(each) * 1.01


def test_more_steel_means_more_capacity():
    assert section(n=30).moment_capacity(3000) > section(n=20).moment_capacity(3000)
    assert section(26, 32, 497.0, Ring(13, 16, 441.0)).moment_capacity(3000) > section(26).moment_capacity(
        3000
    )


def test_half_row_orientations():
    s = section(26, 32, 497.0, Ring(13, 16, 441.0))
    assert s.rotations == pytest.approx(sorted([0.0, math.pi / 26, math.pi / 13]))
    assert s.area_steel == pytest.approx(26 * math.pi * 256 + 13 * math.pi * 64)


def test_hull_points_give_the_same_maximum():
    rng = np.random.default_rng(1)
    n = rng.uniform(-3000, 15000, 2000)
    m = rng.uniform(0, 3000, 2000) * (1 - np.abs(n - 6000) / 12000)
    idx = hull_indices(n, m)
    assert len(idx) < 60
    s = section()
    assert s.utilisation(n[idx], m[idx]).max() == pytest.approx(s.utilisation(n, m).max())


# --- Pile detailing ----------------------------------------------------------------------


def test_minimum_pile_steel_table_9_6n():
    assert min_area_pile(0.4e6) == pytest.approx(2000)
    assert min_area_pile(0.8e6) == 2500
    assert min_area_pile(math.pi * 1200**2 / 4) == pytest.approx(0.0025 * math.pi * 1200**2 / 4)


def test_candidates_respect_detailing_rules():
    pile, settings = PileInput(), DesignSettings()
    pr = settings.piles
    ac = math.pi * pile.diameter**2 / 4
    cands = candidate_arrangements(pile, settings)
    assert {a.rows for a in cands} == {1, 1.5, 2, 2.5, 3}
    for a in cands:
        assert a.outer.diameter >= MIN_BAR and a.outer.count >= MIN_BARS and a.outer.count % 2 == 0
        assert a.clear_spacing <= pr.max_clear_spacing + 1e-9
        for ring in a.rings:
            assert ring.clear_spacing >= max(pr.min_clear_spacing, ring.diameter) - 1e-9
            assert ring.diameter <= a.outer.diameter
        assert a.area <= MAX_RATIO * ac
        assert a.outer.radius == pytest.approx(600 - 75 - 12 - a.outer.diameter / 2)
    keys = [(a.area, a.rows, a.bar_count) for a in cands]
    assert keys == sorted(keys)


def test_one_and_a_half_rows_sit_behind_every_second_bar():
    a = make_arrangement(PileInput(), DesignSettings(), 1.5, 26, 32, 16)
    outer, inner = a.rings
    assert a.label == "26Ø32 + 13Ø16" and a.bar_count == 39
    # Radial gap is the EN 1992-1-1 8.2 minimum: the larger bar, 32 mm.
    assert inner.radius == pytest.approx(outer.radius - 16 - 32 - 8)
    assert make_arrangement(PileInput(), DesignSettings(), 1.5, 25, 32, 16) is None
    settings = DesignSettings()
    settings.piles.row_clear_spacing = 60
    assert make_arrangement(PileInput(), settings, 2, 26, 32, 16).rings[1].radius == pytest.approx(
        outer.radius - 16 - 60 - 8
    )


def test_spacing_limits_come_from_the_settings():
    settings = DesignSettings()
    settings.piles.min_clear_spacing = 100
    settings.piles.max_clear_spacing = 150
    settings.piles.even_bar_count = False
    cands = candidate_arrangements(PileInput(), settings)
    assert any(a.outer.count % 2 for a in cands)
    assert all(100 - 1e-9 <= r.clear_spacing for a in cands for r in a.rings)
    assert all(a.clear_spacing <= 150 + 1e-9 for a in cands)


def test_fixed_outer_bar_count():
    cands = candidate_arrangements(PileInput(bar_count=26), DesignSettings())
    assert cands and {a.outer.count for a in cands} == {26}


def test_candidates_by_cost():
    settings = DesignSettings()
    settings.reinforcement.objective = "min_cost"
    cands = candidate_arrangements(PileInput(), settings)
    assert [a.cost_per_m for a in cands] == sorted(a.cost_per_m for a in cands)


def pile_rows(loads, extras=(1.0, 2.0, 3.0, "N/A")):
    """Embedded beam rows from (node, z, N, M_2, M_3) in Plaxis signs."""
    rows = [EMBEDDED_HEADER]
    for node, z, n, m2, m3 in loads:
        values = [n, 0.0, 0.0, 0.0, m2, m3]
        row = ["EmbeddedBeam\\_1\\_1", node, 1, 0.0, 0.0, z]
        for v in values:
            row += [v, v - 1.0, v + 1.0]
        rows.append(row + list(extras))
    return rows


def pile_sheets(loads, qp_loads=None):
    raw = {"Pile(1)-PT-B-Apron": pile_rows(loads), "Pile(1)-QP": pile_rows(qp_loads or loads)}
    return import_sheets(raw).elements()["Pile(1)"]


def test_light_loads_get_minimum_steel():
    sheets = pile_sheets([(1, 0.0, -3000.0, 100.0, 0.0), (2, -5.0, -3500.0, 200.0, 0.0)])
    d = design_pile("Pile(1)", PileInput(head_level=1.0), DesignSettings(), sheets)
    assert d.passed and d.utilisation < 1
    singles = [a for a in candidate_arrangements(PileInput(), DesignSettings()) if a.rows == 1]
    first = [a for a in singles if a.area >= d.area_min][0]
    assert d.arrangement.label == first.label
    assert d.steel_ratio_kg_m3 == pytest.approx(d.arrangement.area / (math.pi * 1200**2 / 4) * 7850)


def test_design_uses_resultant_moment_and_concrete_sign():
    # Plaxis tension (+1000 kN) becomes -1000 kN in the design sign; M = hypot(3000, 4000).
    sheets = pile_sheets([(1, 0.0, 1000.0, 3000.0, 4000.0)])
    d = design_pile("Pile(1)", PileInput(head_level=1.0), DesignSettings(), sheets)
    assert d.passed
    assert d.governing["N_kN"] == -1000.0 and d.governing["M_kNm"] == 5000.0
    assert d.governing["M_Rd_kNm"] >= 5000.0
    assert d.utilisation == pytest.approx(max(p[3] for p in d.points), abs=1e-3)


def test_qp_is_not_used_for_ultimate_design():
    uls = [(1, 0.0, -3000.0, 100.0, 0.0)]
    heavy_qp = [(1, 0.0, -3000.0, 50_000.0, 0.0)]
    d = design_pile("Pile(1)", PileInput(head_level=1.0), DesignSettings(), pile_sheets(uls, heavy_qp))
    assert d.passed and {p[0] for p in d.points} == {"PT-B-Apron"}


def test_results_above_the_pile_head_are_ignored():
    loads = [(1, 2.7, -3000.0, 50_000.0, 0.0), (2, 0.0, -3000.0, 100.0, 0.0)]
    inside = design_pile("Pile(1)", PileInput(), DesignSettings(), pile_sheets(loads))
    below = design_pile("Pile(1)", PileInput(head_level=1.7), DesignSettings(), pile_sheets(loads))
    assert not inside.passed and any("head level" in n for n in inside.notes)
    assert below.passed and below.governing["z"] == 0.0


def test_overloaded_pile_reports_the_strongest_arrangement():
    d = design_pile(
        "Pile(1)", PileInput(head_level=1.0), DesignSettings(), pile_sheets([(1, 0.0, 0, 50_000, 0)])
    )
    assert not d.passed and d.utilisation > 1
    assert any("4%" in n for n in d.notes)
    assert d.to_dict()["arrangement"]["label"] == d.arrangement.label


def test_extra_rows_only_when_needed():
    sheets = pile_sheets([(1, 0.0, -2000.0, 3000.0, 0.0)])
    pile = PileInput(head_level=1.0)
    default = design_pile("Pile(1)", pile, DesignSettings(), sheets)
    assert default.passed and default.arrangement.rows == 1
    lightest = DesignSettings()
    lightest.piles.extra_rows_only_when_needed = False
    free = design_pile("Pile(1)", pile, lightest, sheets)
    assert free.passed and free.arrangement.area <= default.arrangement.area
    assert any(a["rows"] > 1 for a in default.alternatives)
    assert default.alternatives[0]["chosen"] and default.alternatives[0]["label"] == default.arrangement.label
    only = DesignSettings()
    only.piles.rows = [1.5]
    assert design_pile("Pile(1)", pile, only, sheets).arrangement.rows == 1.5


def test_structural_casing_is_noted():
    pile = PileInput(head_level=1.0, casing=Casing(role="structural"))
    d = design_pile("Pile(1)", pile, DesignSettings(), pile_sheets([(1, 0.0, -3000.0, 100.0, 0.0)]))
    assert any("casing" in n for n in d.notes)


def test_runner_designs_piles_and_skips_missing_ones(workbook):
    section = Section()
    section.add_elements(["Pile(1)", "Pile(2)", "Pile(9)", "Deck"])
    out = run_piles(DesignSettings(), section, import_sheets(workbook))
    assert [p["element"] for p in out["piles"]] == ["Pile(1)", "Pile(2)"]
    assert out["skipped"] == ["Pile(9): no usable sheets in the workbook."]


# --- API ---------------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TRITON_DATA_DIR", str(tmp_path))
    return TestClient(app)


def xlsx_bytes(sheets):
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_design_endpoints(client):
    p = client.post("/api/projects", json={"element_names": ["Pile(1)"]}).json()
    url = f"/api/projects/{p['id']}/sections/{p['sections'][0]['id']}"
    assert client.post(f"{url}/design/piles").status_code == 409
    assert client.get(f"{url}/workbook").status_code == 404

    data = xlsx_bytes({"Pile(1)-PT-B-Apron": pile_sheet(), "Pile(1)-QP": pile_sheet()})
    r = client.post(f"{url}/workbook", files={"file": ("s.xlsx", data)})
    assert r.status_code == 200, r.text
    assert client.get(f"{url}/workbook").json()["file"] == "s.xlsx"
    assert client.get(f"{url}/design/piles").status_code == 404

    r = client.post(f"{url}/design/piles")
    assert r.status_code == 200, r.text
    (pile,) = r.json()["piles"]
    assert pile["element"] == "Pile(1)" and pile["passed"]
    assert client.get(f"{url}/design/piles").json() == r.json()

    # A new workbook makes the old results stale.
    client.post(f"{url}/workbook", files={"file": ("s2.xlsx", data)})
    assert client.get(f"{url}/design/piles").status_code == 404
