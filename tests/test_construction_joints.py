"""Construction joints: EN 1992-1-1 6.2.5 at each joint set on a pile, beam or slab, and the bars it needs."""

import math

import numpy as np
import pytest
from test_beams import beam_rows, pile_line, uniform
from test_slabs import deck_rows, deck_workbook, piles_at

from triton import fresh
from triton.design import construction_joints as CJ
from triton.design.runner import run_section
from triton.drawings import drawings
from triton.project import (
    BeamInput,
    BeamJoint,
    DesignSettings,
    DrawingSettings,
    PileInput,
    PileJoint,
    Project,
    Section,
    SlabInput,
    SlabJoint,
)
from triton.report import build_report
from triton.validation import import_sheets


def test_interface_shear_to_6_2_5():
    laws = CJ.Laws("C40/50", DesignSettings())
    # fctd = 0.7 fctm / 1.5 with fctm 3.51 MPa; fyd 500 / 1.15.
    assert laws.fctd == pytest.approx(0.7 * 3.51 / 1.5, abs=0.01)
    assert laws.v_max == pytest.approx(0.5 * 0.6 * (1 - 40 / 250) * 40 / 1.5, abs=1e-6)
    # Rough, no normal stress: c fctd carries 0.655 MPa, the rest by ρ fyd μ.
    v = np.array([0.5, 1.5])
    rho = CJ.shear_steel(v, np.zeros(2), "rough", laws)
    assert rho[0] == 0
    assert rho[1] == pytest.approx((1.5 - 0.4 * laws.fctd) / (laws.fyd * 0.7))
    assert CJ.v_rdi(float(rho[1]), 0.0, "rough", laws) == pytest.approx(1.5)
    # Tension across the joint: no cohesion, and σn counts against it.
    t = CJ.shear_steel(np.array([0.5]), np.array([-0.5]), "rough", laws)[0]
    assert t == pytest.approx((0.5 + 0.7 * 0.5) / (laws.fyd * 0.7))
    # Compression is capped at 0.6 fcd; very smooth takes c = 0.025, μ = 0.5.
    assert CJ.shear_steel(np.array([30.0]), np.array([100.0]), "very smooth", laws)[0] == pytest.approx(
        max(30 - 0.025 * laws.fctd - 0.5 * 0.6 * laws.fcd, 0) / (laws.fyd * 0.5)
    )


def test_bars_are_the_lightest_that_give_the_area():
    s = DesignSettings()
    b = CJ.bars_per_m(1000.0, s)
    assert b["area_mm2_per_m"] >= 1000
    assert b["area_mm2_per_m"] == min(
        round(math.pi * d * d / 4 * 1000 / sp)
        for d in s.reinforcement.bar_diameters
        if d >= 12
        for sp in CJ.SPACINGS
        if math.pi * d * d / 4 * 1000 / sp >= 1000
    )
    n = CJ.bars_count(3000.0, s)
    assert n["count"] % 2 == 0 and n["area_mm2"] >= 3000


def test_a_lap_across_the_joint_is_flagged():
    design = {
        "curtailment": {
            "runs": [
                {
                    "top": 2.7,
                    "bottom": -1.8,
                    "cage": {"rings": [{"count": 26, "diameter": 32, "radius": 499}]},
                    "lap_below_m": [1.45],
                    "above_head_m": [1.45],
                },
                {
                    "top": -1.8,
                    "bottom": -10.0,
                    "cage": {"rings": [{"count": 26, "diameter": 20, "radius": 505}]},
                    "lap_below_m": [0.0],
                    "above_head_m": [],
                },
            ]
        }
    }
    rings, run, warns = CJ._cage_at(design, -2.5)
    assert rings[0]["diameter"] == 20 and warns and "spans the joint" in warns[0]
    assert not CJ._cage_at(design, -5.0)[2]
    # At the head only the bars running on above it cross the joint.
    assert CJ._cage_at(design, 2.7)[0][0]["diameter"] == 32
    design["curtailment"]["runs"][0]["above_head_m"] = [0.0]
    assert CJ._cage_at(design, 2.7)[0] == []


def _deck_section(**joints):
    els = {
        "Deck": SlabInput(thickness=800, construction_joints=joints.get("slab", [])),
        "Pile(1)": PileInput(head_level=2.7, construction_joints=joints.get("pile", [])),
    }
    return Section(name="S1", elements=els, end_trim=0.0)  # the small model whole


def test_pile_and_slab_joints_in_the_design_report_and_drawings():
    section = _deck_section(
        pile=[PileJoint(level=2.7, note="pile top"), PileJoint(level=40.0)],
        slab=[
            SlabJoint(line="at X", at=-6.0),
            SlabJoint(line="at Y", at=2.0, surface="very smooth"),
            SlabJoint(line="beam face", beam="Front Beam"),
        ],
    )
    res = run_section(DesignSettings(), section, deck_workbook())
    (pile,) = res["piles"]
    top, far = pile["construction_joints"]
    assert top["note"] == "pile top" and top["crossing"]["area_mm2"] == pile["arrangement"]["area_mm2"]
    # As the office report: the joint bars are the shear-friction steel; tension is shown only.
    assert top["needed_mm2"] == top["shear_mm2"] and top["tension_added"] is False
    assert top["passed"] == (top["needed_mm2"] <= top["provided_mm2"])
    assert "not checked" in far["status"] and far["passed"] is None
    (slab,) = res["slabs"]
    at_x, at_y, face = slab["construction_joints"]
    assert at_x["bars_along"] == "X" and at_x["line"]["at_m"] == -6.0 and "averaged_over_m" not in at_x
    assert at_x["provided_mm2_per_m"] > 0 and at_x["v_Edi_MPa"] > 0
    assert at_y["bars_along"] == "Y" and at_y["surface"] == "very smooth"
    assert "no beam named" in face["status"]
    for j in (at_x, at_y):
        extra = sum(s["bars"]["area_mm2_per_m"] for s in j.get("stretches") or [] if s["bars"])
        assert j["passed"] or extra >= min(s["additional_mm2_per_m"] for s in j["stretches"])

    rep = build_report(
        Project(sections=[section]), section, {**res, "run_at": "2026-09-24T22:00"}, "detailed"
    )
    heads = [b.text for b in rep.blocks if b.kind.startswith("h")]
    assert "3.7 Construction joints" in heads and "Deck: construction joints" in heads
    d = drawings("P", res, DrawingSettings(), "S1")
    texts = [i["text"] for v in d["views"] for i in v["items"] if i["type"] == "text"]
    assert any(t.startswith("CONSTRUCTION JOINT +2.70") for t in texts)
    assert sum("Construction joint:" in t for t in texts) >= 2


def test_a_weak_joint_gets_bars_where_it_needs_them():
    # Shear of 600 kN/m across X = -2 over Y 0..4 only, in tension: a smooth joint there needs dowels.
    def forces(x, y):
        v = 600.0 if y >= 0 else 20.0
        return [150.0, 0.0, 0.0, 0.0, v, 80.0, 50.0, 0.0]

    wb = import_sheets(
        {
            "Deck-PT-B-Apron": deck_rows(forces),
            "Deck-QP": deck_rows(lambda x, y: [0.0] * 5 + [50.0, 30.0, 0.0]),
            "Pile(1)-PT-B-Apron": piles_at([(-4.0, 0.0)]),
            "Pile(1)-QP": piles_at([(-4.0, 0.0)], 1000.0),
        }
    )
    section = _deck_section(slab=[SlabJoint(line="at X", at=-2.0, surface="smooth")])
    (j,) = run_section(DesignSettings(), section, wb)["slabs"][0]["construction_joints"]
    assert not j["passed"] and j["status"] == "additional bars needed"
    (s,) = j["stretches"]
    assert s["from_m"] >= -1.0 and s["to_m"] == 4.0  # only where the shear is
    assert s["bars"]["area_mm2_per_m"] >= s["additional_mm2_per_m"]
    assert s["bars"]["length_m"] == pytest.approx(2 * 45 * s["bars"]["diameter_mm"] / 1000)
    assert j["additional_kg"] > 0


def test_beam_joints_level_and_stop_end():
    raw = {
        "Front Beam-PT-B-Apron": beam_rows(uniform(m22=150.0, q23=400.0, m11=300.0)),
        "Front Beam-QP": beam_rows(uniform(m22=100.0, m11=200.0)),
        "Pile(1)-PT-B-Apron": pile_line([0.0, 6.0, 12.0]),
        "Pile(1)-QP": pile_line([0.0, 6.0, 12.0]),
    }
    joints = [
        BeamJoint(kind="level", at=2.4, surface="smooth"),
        BeamJoint(kind="along", at=3.0),
        BeamJoint(kind="level", at=9.0),
    ]
    els = {
        "Front Beam": BeamInput(depth=1500, construction_joints=joints),
        "Pile(1)": PileInput(head_level=2.7),
    }
    res = run_section(DesignSettings(), Section(elements=els), import_sheets(raw))
    (b,) = res["beams"]
    level, stop, outside = b["construction_joints"]
    # Plate at mid-depth: top 2.7 + 0.75, so 2.4 m is 450 mm above the soffit; the links cross it.
    assert level["height_above_soffit_mm"] == 450
    assert level["crossing"]["label"] == b["shear"]["link"]["label"]
    assert level["tension_mm2_per_m"] == 0 and level["v_Edi_MPa"] > 0
    assert stop["crossing"]["area_mm2"] == pytest.approx(b["cage"]["area_mm2"], abs=5)
    assert stop["tension_mm2"] <= stop["provided_mm2"]
    assert "not inside the beam" in outside["status"]


def test_designs_without_joints_keep_their_fingerprint():
    section = _deck_section()
    project = Project(sections=[section])
    before = fresh.fingerprint(project, section, None)
    # The element's own hash does not see the empty list, so designs made before joints stay current.
    own = section.elements["Deck"].model_dump(mode="json")
    punching = ("punching_piles", "punching_per", "punching_fix")  # empty or at their defaults
    for key in ("construction_joints", "manholes", "channels", *punching):
        own.pop(key, None)
    assert before["Deck"] == fresh._hash(own)
    with_joint = _deck_section(slab=[SlabJoint(at=-2.0)])
    assert fresh.fingerprint(Project(sections=[with_joint]), with_joint, None)["Deck"] != before["Deck"]


def test_the_office_report_joint_checks_come_out_the_same():
    """Final Design Report Appendix 18: front beam joint VEd 1294 kN/m, NEd 640 kN/m tension, rear beam
    920 and 72 kN/m; h 700, d = 700 − 50, z = 0.8 d, keyed (μ 0.9), c fctd = 0: 6040 and 3343 mm²/m."""
    s = DesignSettings()
    laws = CJ.Laws("C40/50", s)
    r = s.construction_joints
    assert (r.lever_arm, r.effective_depth, r.sigma_n_area, r.actions, r.tension) == (
        0.8,
        "cover",
        "d",
        "peak",
        "separate",
    )
    assert SlabJoint().surface == PileJoint().surface == BeamJoint().surface == "indented"
    h, d = 700.0, 650.0
    for v, n, office in ((1294.0, -640.0, 6040), (920.0, -72.0, 3343)):
        v_edi = v * 1e3 / (r.lever_arm * d * 1000)
        sigma = n * 1e3 / (1000 * d)
        a = CJ.shear_steel(np.array([v_edi]), np.array([sigma]), "indented", laws)[0] * 1000 * h
        assert a == pytest.approx(office, rel=0.005)


def test_a_slab_joint_as_the_office_report_through_the_design():
    # A uniform slab in tension: V 1294 kN/m across X = -2, N 640 kN/m tension, 700 thick with 50 covers.
    def forces(x, y):
        return [640.0, 0.0, 0.0, 0.0, 1294.0, 10.0, 10.0, 0.0]  # N_1 + is tension (sign flipped to design)

    wb = import_sheets(
        {
            "Deck-PT-B-Apron": deck_rows(forces),
            "Deck-QP": deck_rows(lambda x, y: [0.0] * 5 + [10.0, 10.0, 0.0]),
            "Pile(1)-PT-B-Apron": piles_at([(-6.0, 0.0)]),
            "Pile(1)-QP": piles_at([(-6.0, 0.0)], 1000.0),
        }
    )
    els = {
        "Deck": SlabInput(thickness=700, construction_joints=[SlabJoint(line="at X", at=-2.0)]),
        "Pile(1)": PileInput(head_level=2.7),
    }
    (j,) = run_section(DesignSettings(), Section(elements=els), wb)["slabs"][0]["construction_joints"]
    assert j["forces"]["V_kN_per_m"] == pytest.approx(1294, abs=1)
    assert j["forces"]["N_kN_per_m"] == pytest.approx(-640, abs=1)
    assert j["shear_mm2_per_m"] == pytest.approx(6040, rel=0.005)
    assert j["needed_mm2_per_m"] == j["shear_mm2_per_m"]
    # The conservative rules: z = 0.9 d and the tension steel added on top.
    rules = {"lever_arm": 0.9, "tension": "add"}
    own = DesignSettings(construction_joints=rules)
    (k,) = run_section(own, Section(elements=els), wb)["slabs"][0]["construction_joints"]
    assert k["shear_mm2_per_m"] < j["shear_mm2_per_m"] and k["needed_mm2_per_m"] > k["shear_mm2_per_m"]
