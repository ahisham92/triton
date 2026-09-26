"""Comparisons, option matrix: a deck designed for every combination of the options listed."""

import pytest
from test_costing import project, results
from test_trials import slab

from triton import matrix, trials
from triton.figures import slab_bars
from triton.project import PileInput, SlabInput
from triton.report import build_matrix_report, to_docx, to_pdf, to_xlsx


def section():
    p = project()
    s = p.sections[0]
    s.elements = {"Pile(1)": PileInput(), "Deck": SlabInput(thickness=700)}
    return p, s


SPEC = {
    "element": "Deck",
    "thickness": [700, 750, "", 700],
    "crack_width_limit": [0.2, 0.3],
    "peaks": ["face_mean"],
    "decks": [{"type": "solid"}, {"type": "voided", "diameter": 400, "spacing": 600}],
}


def test_the_matrix_is_every_combination():
    p, s = section()
    spec = matrix.clean_spec(SPEC, s)
    assert spec["thickness"] == [700.0, 750.0]  # blanks and repeats dropped
    variants = matrix.variants(spec, s)
    assert len(variants) == 2 * 2 * 1 * 2
    labels = [v["label"] for v in variants]
    assert "750 mm, voids Ø400 @ 600, wk 0.3, face mean" in labels
    voided = next(v for v in variants if "voids" in v["label"] and "750" in v["label"])
    deck = trials.variant_section(s, voided).elements["Deck"]
    assert deck.thickness == 750 and deck.voids.diameter == 400 and deck.voids.spacing == 600
    solid = next(v for v in variants if "solid" in v["label"])
    assert trials.variant_section(s, solid).elements["Deck"].voids is None
    # Every combination is its own variant: none shares a key.
    assert len({trials.variant_key(v) for v in variants}) == len(variants)


def test_the_matrix_is_checked():
    p, s = section()
    with pytest.raises(ValueError, match="slab"):
        matrix.clean_spec({**SPEC, "element": "Pile(1)"}, s)
    with pytest.raises(ValueError, match="check the lists"):
        matrix.clean_spec({**SPEC, "thickness": list(range(600, 3000, 10))}, s)
    # 80 and more are fine: the page designs them in steps and collects them in one table.
    assert (
        len(matrix.options(matrix.clean_spec({**SPEC, "thickness": list(range(600, 1600, 50))}, s), s)) == 80
    )
    with pytest.raises(ValueError, match="pile-face"):
        matrix.clean_spec({**SPEC, "peaks": ["mean"]}, s)
    with pytest.raises(ValueError, match="too little concrete"):
        matrix.variants(matrix.clean_spec({**SPEC, "decks": [{"type": "voided", "diameter": 650}]}, s), s)


def strip_design():
    rows = [
        {
            "layer": "bottom_x",
            "face": "bottom",
            "station": [1.0, 5.5],
            "strip": "column",
            "moment": "M11",
            "bars": "Ø25 @ 150 + Ø25 @ 150",
            "additional_bars": "Ø25 @ 150",
            "as_mm2_per_m": 6545,
            "M_kNm_per_m": 900.0,
            "combination": "ULS1",
            "ratio": 0.8,
            "wk_mm": 0.19,
            "wk_limit_mm": 0.2,
            "set_by": "crack width (QP)",
            "bar_layers": [
                {
                    "layer": 1,
                    "from_face_mm": 62,
                    "bars": [
                        {"diameter_mm": 25, "spacing_mm": 150, "kind": "mesh"},
                        {"diameter_mm": 25, "spacing_mm": 150, "kind": "between the mesh bars"},
                    ],
                }
            ],
        },
        {
            "layer": "bottom_x",
            "face": "bottom",
            "station": [5.5, 9.0],
            "strip": "field",
            "moment": "M11",
            "bars": "Ø25 @ 150",
            "additional_bars": None,
            "as_mm2_per_m": 3272,
            "ratio": 0.5,
            "wk_mm": 0.1,
            "wk_limit_mm": 0.2,
        },
    ]
    return {
        "along": "X",
        "start": 1.0,
        "end": 9.0,
        "from": "front beam centre",
        "stations": [5.5],
        "pile_rows_m": [3.0, 7.0],
        "lines": [0.0, 4.2],
        "rows": rows,
        "table": [],
        "across_profile": {"moment": "M22", "range": [0, 33.6]},
    }


def deck_design(kg=100.0, passed=True):
    return slab(
        steel={"kg_per_m2": kg, "area_m2": 672.0},
        utilisation=0.9,
        passed=passed,
        box={"X": [-20, 0], "Y": [0, 33.6]},
        layers={"bottom_x": {"basic": {"label": "Ø25 @ 150"}}, "top_x": {"basic": {"label": "Ø16 @ 150"}}},
        strip_design=strip_design(),
        punching_types=[
            {
                "pile": "Pile(1)",
                "heads": 8,
                "unified": True,
                "needs_reinforcement": True,
                "passed": True,
                "perimeters": 3,
                "radial_spacing_mm": 450,
                "asw_mm2_per_perimeter": 2000,
                "utilisation": 1.3,
            },
        ],
    )


def test_each_option_says_where_it_governs_and_if_punching_needs_links():
    d = matrix.deck_summary(deck_design())
    (g,) = d["governing"]
    assert g["name"] == "Bottom, bars along X" and g["where"] == "column strip, 1 to 5.5 m"
    assert g["utilisation"] == pytest.approx(0.95)  # the crack width governs
    assert g["mesh"] == "Ø25 @ 150"
    assert d["punching_needed"] and d["punching"][0]["links"].startswith("3 perimeters")
    assert slab_bars(deck_design(), "along")[:4] == b"\x89PNG"


def test_the_matrix_runs_costs_and_reports(tmp_path, monkeypatch):
    p, s = section()

    def fake(
        settings, section, workbook, progress=None, only=None, deadline=None, approach=None, furniture_at=None
    ):
        (name,) = only
        e = section.elements[name]
        if name == "Deck":
            kg = 100 * 0.2 / e.crack_width_limit * 700 / e.thickness
            return {"slabs": [deck_design(kg, passed=e.thickness > 700)]}
        return {"piles": [{**results()["piles"][0], "utilisation": 0.9, "passed": True}]}

    monkeypatch.setattr(trials, "run_section", fake)
    spec = matrix.clean_spec({**SPEC, "decks": []}, s)
    matrix.save_spec(tmp_path, spec)
    out = trials.run_scenarios(p, s, None, None, tmp_path, matrix.variants(spec, s), which="matrix")
    assert out["left"] == 0
    v = matrix.view(p, s, None, {}, tmp_path)
    assert v["spec"] == spec and v["count"] == 4
    options = [o for o in v["options"] if not o["base"]]
    assert all(o["deck"] for o in options)
    best = next(o for o in options if o["best"])
    assert best["label"] == "750 mm, wk 0.3, face mean"  # safe and least steel
    assert best["over_best_per_m"] == 0
    assert all(o["over_best_per_m"] >= 0 for o in options)
    designs = {o["key"]: matrix.design_of(tmp_path, o["design_key"]) for o in options}
    rep = build_matrix_report(p, s, v, designs)
    assert any(b.kind == "image" for b in rep.blocks)
    for render in (to_docx, to_pdf, to_xlsx):
        assert len(render(rep)) > 1000


def test_lists_can_be_left_out_and_mesh_and_punching_compared():
    p, s = section()
    spec = matrix.clean_spec(
        {**SPEC, "mesh": [150, 200], "punching_per": ["type", "head"], "use": {"crack_width_limit": False}},
        s,
        [150.0, 200.0],
    )
    options = matrix.options(spec, s)
    assert len(options) == 2 * 1 * 2 * 2 * 2  # thickness x peaks x decks x mesh x punching; no wk
    assert all(o["crack_width_limit"] is None for o in options)
    v = next(
        v
        for v in matrix.variants(spec, s)
        if "mesh @ 200" in v["label"] and "punching per head" in v["label"]
    )
    vs = trials.variant_section(s, v)
    assert vs.slab_strips["Deck"].spacing == 200 and vs.elements["Deck"].punching_per == "head"
    with pytest.raises(ValueError, match="meshes at 150, 200"):
        matrix.clean_spec({**SPEC, "mesh": [175]}, s, [150.0, 200.0])


def test_comparisons_have_their_own_export(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from triton.api import app

    monkeypatch.setenv("TRITON_DATA_DIR", str(tmp_path))
    c = TestClient(app)
    p = c.post("/api/projects", json={"element_names": ["Deck", "Pile(1)"]}).json()
    pid, sid = p["id"], p["sections"][0]["id"]
    base = f"/api/projects/{pid}/sections/{sid}/comparisons/report"
    for f in ("docx", "pdf", "xlsx"):
        r = c.get(f"{base}.{f}", params={"what": "element", "element": "Deck"})
        assert r.status_code == 200 and len(r.content) > 1000
        assert c.get(f"{base}.{f}", params={"what": "all"}).status_code == 200
    assert c.get(f"{base}.docx", params={"what": "ve"}).status_code == 200
    assert c.get(f"{base}.docx", params={"what": "element", "element": "Nope"}).status_code == 404
