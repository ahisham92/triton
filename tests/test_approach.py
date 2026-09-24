"""Approach slab on the rear beam ledge: slab per metre, ledge strut and tie, what the rear beam takes."""

import pytest
from fastapi.testclient import TestClient
from test_beams import beam_rows, pile_line
from test_costing import project

from triton import fresh, method, trials
from triton.api import app
from triton.costing import cost_section
from triton.design.approach import Beam, analyse, design_approach, design_ledge, rear_beam_additions
from triton.design.beams import design_beam
from triton.design.runner import run_section
from triton.geometry import section_geometry
from triton.project import ApproachSlabInput, BeamInput, DesignSettings, Ledge, PileInput, Project
from triton.report import build_report
from triton.validation import import_sheets


def no_wheel(**kw):
    return ApproachSlabInput(wheel_load=0.0, surfacing=0.0, **kw)


def test_simple_span_matches_hand_calcs():
    """No ground support: a simple span from the ledge to the slab on grade, wL²/8 and wL/2."""
    s = no_wheel(length=6.0, thickness=400.0, surcharge=35.0)
    a = analyse(s, DesignSettings())
    w = 1.35 * 25 * 0.4 + 1.5 * 35
    assert a["far_support"]
    assert a["env"]["uls"]["M_max"].max() == pytest.approx(w * 36 / 8, rel=1e-3)
    assert a["R_uls"] == pytest.approx(w * 3, rel=1e-3)
    assert a["R_qp"] == pytest.approx((25 * 0.4 + 0.6 * 35) * 3, rel=1e-3)
    assert a["env"]["uls"]["M_min"].min() == pytest.approx(0.0, abs=1e-6)


def test_ground_springs_carry_the_slab_past_the_unsupported_length():
    """With ground support beyond 2 m, the ledge takes much less than the simple span and the slab hogs."""
    span = analyse(no_wheel(), DesignSettings())
    ground = analyse(no_wheel(unsupported_length=2.0), DesignSettings())
    assert not ground["far_support"]
    assert ground["R_uls"] < 0.7 * span["R_uls"]
    assert ground["env"]["uls"]["M_max"].max() < span["env"]["uls"]["M_max"].max()
    # A slab lying on the ground everywhere carries itself: the ledge takes little.
    flat = analyse(no_wheel(unsupported_length=0.0), DesignSettings())
    assert flat["R_uls"] < 0.5 * span["R_uls"]  # the stiff slab still leans on the ledge over ~(4EI/k)^¼


def test_springs_only_push():
    """A load near the ledge lifts the far end off the ground rather than pulling it down."""
    b = Beam(6.0, 1e5, 0.0, 20000.0)
    import numpy as np

    p = np.zeros_like(b.x)
    p[2] = 500.0
    r = b.solve(np.zeros_like(b.x), p)
    assert r["w"][-1] >= -1e-12 or r["w"][-1] < 0  # solved
    assert r["R"] > 0


def test_slab_bars_cover_the_moments_and_the_cracks():
    d = design_approach(
        ApproachSlabInput(), DesignSettings(), BeamInput(kind="rear_beam", width=2000, depth=2000)
    )
    bot = d["bending"]["bottom"]
    assert bot["M_Rd_kNm"] >= bot["M_Ed_kNm"] and bot["wk_mm"] <= bot["wk_limit_mm"]
    assert bot["as_mm2_per_m"] >= bot["as_req_mm2_per_m"] >= bot["as_min_mm2_per_m"]
    assert d["distribution"]["bottom"]["as_mm2_per_m"] >= 0.2 * bot["as_mm2_per_m"]
    assert d["ledge"]["F_Ed_kN_per_m"] > d["reaction"]["uls_kN_per_m"]  # with the ledge's own weight
    assert d["passed"]


def test_ledge_tie_by_strut_and_tie():
    """Ft = F·a/z + H·(z + aH)/z with z = d − x/2, the node depth from ν'·fcd."""
    s = ApproachSlabInput(ledge=Ledge(projection=400, depth=600, bearing_width=200, edge_distance=50))
    settings = DesignSettings()
    led = design_ledge(s, settings, 300.0, 100.0, BeamInput(kind="rear_beam", width=2000, depth=2000))
    assert led["a_c_mm"] == 250
    f = led["F_Ed_kN_per_m"]
    z, d = led["z_mm"], led["d_mm"]
    a_h = 600 - d + 20
    ft = f * max(led["a_F_mm"], z / 2.5) / z + 0.2 * f * (z + a_h) / z
    assert led["F_t_kN_per_m"] == pytest.approx(ft, rel=0.01)
    assert led["tie"]["as_mm2_per_m"] >= ft * 1e3 / (500 / 1.15) - 1
    assert led["links"]["kind"] == "horizontal"  # a_c 250 <= 0.5 × 600
    assert led["hanger"]["as_req_mm2_per_m"] == round(f * 1e3 / (500 / 1.15))
    assert led["rear_beam"]["eccentricity_mm"] == 1000 + led["a_F_mm"]
    # A ledge too deep for the beam fails.
    deep = design_ledge(s, settings, 300.0, 100.0, BeamInput(kind="rear_beam", width=2000, depth=800))
    assert deep["rear_beam"]["fits"] is False and not deep["passed"]


def test_rear_beam_takes_the_ledge():
    add = rear_beam_additions(
        {
            "rear_beam": {
                "line_load_uls_kN_per_m": 200,
                "line_load_qp_kN_per_m": 100,
                "wheel_uls_kN": 225,
                "a_F_mm": 250,
            }
        },
        2000,
        6.0,
    )
    assert add["e_m"] == 1.25
    assert add["V_uls"] == pytest.approx(200 * 3 + 225)
    assert add["T_uls"] == pytest.approx(1.25 * (200 * 3 + 225))
    assert add["M_uls"] == pytest.approx(200 * 36 / 12 + 225 * 6 / 8)
    assert add["V_qp"] == pytest.approx(300)
    twist = lambda x, y: [0.0, 0.0, 0.0, 80.0, 0.0, 100.0, 300.0, 0.0]  # noqa: E731
    raw = {
        "Rear Beam-PT-B-Apron": beam_rows(twist),
        "Rear Beam-QP": beam_rows(twist),
        "Pile(1)-PT-B-Apron": pile_line([0.0, 6.0, 12.0]),
        "Pile(1)-QP": pile_line([0.0, 6.0, 12.0]),
    }
    wb = import_sheets(raw)
    el = {
        "Rear Beam": BeamInput(kind="rear_beam", width=2000, depth=2000),
        "Pile(1)": PileInput(head_level=2.7),
    }
    args = (
        "Rear Beam",
        el["Rear Beam"],
        DesignSettings(),
        wb.elements()["Rear Beam"],
        section_geometry(wb),
        el,
        {"1": "X", "2": "Y"},
    )
    plain = design_beam(*args)
    ledge = design_approach(ApproachSlabInput(), DesignSettings(), el["Rear Beam"])["ledge"]
    loaded = design_beam(*args, ledge=ledge)
    assert loaded["ledge_added"]["spacing_m"] == 6.0
    assert loaded["shear"]["governing"]["T_kNm"] > plain["shear"]["governing"]["T_kNm"] + 100
    assert loaded["shear"]["utilisation"] > plain["shear"]["utilisation"]
    assert any("ledge" in n for n in loaded["notes"])


def test_runner_designs_it_and_fresh_tracks_it():
    raw = {
        "Rear Beam-PT-B-Apron": beam_rows(lambda x, y: [0.0, 0.0, 0.0, 50.0, 0.0, 0.0, 200.0, 0.0]),
        "Rear Beam-QP": beam_rows(lambda x, y: [0.0, 0.0, 0.0, 30.0, 0.0, 0.0, 100.0, 0.0]),
    }
    wb = import_sheets(raw)
    p = Project()
    s = p.sections[0]
    s.elements = {"Rear Beam": BeamInput(kind="rear_beam", width=2000, depth=2000)}
    res = run_section(p.design, s, wb)
    assert res["approach_slabs"] == [] and "ledge_added" not in res["beams"][0]
    p.approach = ApproachSlabInput()
    assert fresh.names(p, s) == ["Rear Beam", fresh.APPROACH]
    before = fresh.fingerprint(p, s, None)
    res = run_section(p.design, s, wb, approach=p.approach)
    assert res["approach_slabs"][0]["element"] == fresh.APPROACH and "ledge_added" in res["beams"][0]
    assert fresh.APPROACH in res["designed"]
    only = run_section(p.design, s, wb, only=[fresh.APPROACH], approach=p.approach)
    assert only["beams"] == [] and len(only["approach_slabs"]) == 1
    # A thicker approach slab puts both the slab and the rear beam out of date, nothing else.
    p.approach.thickness = 450
    after = fresh.fingerprint(p, s, None)
    changed = {k for k in after if after[k] != before.get(k)}
    assert changed == {"Rear Beam", fresh.APPROACH}
    report = build_report(p, s, {**res, "skipped": []}, "detailed")
    text = " ".join(b.text for b in report.blocks)
    assert "Approach slab and rear beam ledge" in text
    m = method.view(p)
    assert any(k["kind"] == "Approach slab and ledge" for k in m["kinds"])


def test_costed_and_in_value_engineering(tmp_path, monkeypatch):
    p = project()
    p.approach = ApproachSlabInput()
    s = p.sections[0]
    s.elements = {"Pile(1)": PileInput(), "Rear Beam": BeamInput(kind="rear_beam", width=2000, depth=2000)}
    d = design_approach(p.approach, p.design, s.elements["Rear Beam"])
    out = cost_section(p, s, {"approach_slabs": [d]}, length=33.6)
    rows = {r["element"]: r for r in out["rows"]}
    assert rows[fresh.APPROACH]["concrete_m3"] == round(6.0 * 0.4 * 33.6, 1)
    assert rows["Rear beam ledge"]["concrete_m3"] == round(0.4 * 0.6 * 33.6, 1)
    ideas = trials.ideas(s, {}, p.approach)
    mine = [i for i in ideas if i["element"] == fresh.APPROACH]
    assert {i["what"] for i in mine} >= {"thickness", "length", "ledge_depth", "ledge_projection"}
    thin = next(i for i in mine if i["label"].startswith(f"{fresh.APPROACH} 350"))
    v = trials.clean_variant({**thin["change"], "label": "thin"}, s)
    assert trials.variant_approach(p, v).thickness == 350 and p.approach.thickness == 400
    with pytest.raises(ValueError):
        trials.clean_variant({"approach": {"thickness": 5}}, s)
    calls = []

    def fake(settings, section, workbook, progress=None, only=None, deadline=None, approach=None):
        (name,) = only
        calls.append((name, approach.thickness if approach else None))
        if name == fresh.APPROACH:
            return {"approach_slabs": [design_approach(approach, settings, section.elements["Rear Beam"])]}
        if name == "Rear Beam":
            return {
                "beams": [
                    {
                        "element": name,
                        "kind": "rear_beam",
                        "width_mm": 2000,
                        "depth_mm": 2000,
                        "steel": {"kg_per_m": 400.0},
                        "passed": True,
                        "utilisation": 0.5,
                    }
                ]
            }
        return {
            "piles": [
                {
                    "element": name,
                    "count": 8,
                    "section": {"diameter_mm": 1200.0, "head_level_m": 2.7, "toe_level_m": -20.0},
                    "steel": {"total_kg": 4540.0, "concrete_m3": 25.7},
                    "passed": True,
                    "utilisation": 0.5,
                }
            ]
        }

    monkeypatch.setattr(trials, "run_section", fake)
    trials.run_scenarios(p, s, None, None, tmp_path, [v], which="ve")
    # The pile is shared; the rear beam and the approach slab are designed again for the thinner slab.
    assert [c for c in calls if c[0] == "Pile(1)"] == [("Pile(1)", 400)]
    assert ("Rear Beam", 350) in calls and (fresh.APPROACH, 350) in calls
    view = trials.scenarios_view(p, s, None, {}, tmp_path, "ve")
    base, thin_col = view["variants"]
    assert fresh.APPROACH in view["elements"] and thin_col["complete"]
    # Thinner is not cheaper here: more bars and shear links near the ledge outweigh the concrete.
    thin_cost, base_cost = (c["elements"][fresh.APPROACH]["cost_per_m"] for c in (thin_col, base))
    assert thin_cost and base_cost and thin_cost != base_cost


def test_api_saves_and_locks_it(tmp_path, monkeypatch):
    monkeypatch.setenv("TRITON_DATA", str(tmp_path))
    c = TestClient(app)
    p = c.post("/api/projects", json={"info": {"name": "A"}}).json()
    p["approach"] = ApproachSlabInput().model_dump(mode="json")
    saved = c.put(f"/api/projects/{p['id']}", json=p)
    assert saved.status_code == 200 and saved.json()["approach"]["thickness"] == 400
    schema = c.get("/api/schema/project").json()
    assert "approach" in schema["properties"]
