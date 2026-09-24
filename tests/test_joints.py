"""Expansion joints: placed along the berth by the Design settings' rules, used by the restraint checks."""

import pytest
from test_alignment import ONLY, berth, section, turn_workbook

from triton import dxf
from triton.design.runner import run_section
from triton.joints import furniture_from_costing, joints_drawing, pile_rows, place_joints, section_joints
from triton.project import (
    DesignSettings,
    DrawingSettings,
    ExpansionJoints,
    FurnitureAt,
    OtherItem,
    Section,
    SectionCosting,
    SectionJoints,
)


def lengths(out):
    return [s["length"] for s in out["segments"]]


def test_fewest_nearly_equal_segments_midway_between_pile_rows():
    out = place_joints(ExpansionJoints(), [500.0], spacing=6.4)
    assert len(out["segments"]) == 9  # 500 / 58 → 9
    assert max(lengths(out)) <= 58.0
    assert sum(lengths(out)) == pytest.approx(500.0)
    # Rows at 3.2 + 6.4 k: every joint is half a bay from a row.
    for j in out["joints"]:
        assert (j["chainage"] - 3.2) % 6.4 == pytest.approx(3.2, abs=1e-6)
    assert max(lengths(out)) - min(lengths(out)) <= 6.4 + 1e-6


def test_a_short_berth_has_no_joint():
    out = place_joints(ExpansionJoints(), [58.0], spacing=6.4)
    assert out["joints"] == [] and lengths(out) == [58.0]


def test_at_a_row_and_anywhere():
    at_row = place_joints(ExpansionJoints(position="at_row"), [200.0], spacing=4.0, first_row=0.0)
    assert all(j["chainage"] % 4.0 == pytest.approx(0.0, abs=1e-6) for j in at_row["joints"])
    free = place_joints(ExpansionJoints(position="anywhere"), [200.0])
    assert lengths(free) == [50.0, 50.0, 50.0, 50.0]


def test_preferred_length_gives_more_joints():
    out = place_joints(ExpansionJoints(preferred_segment=30.0), [240.0])
    assert lengths(out) == [30.0] * 8


def test_joints_keep_clear_of_furniture():
    rules = ExpansionJoints(position="anywhere", furniture_clearance=2.0)
    furniture = [{"name": "Bollard", "chainage": 50.0}, {"name": "Fender", "chainage": 100.0}]
    out = place_joints(rules, [200.0], furniture=furniture)
    assert all(j["nearest_furniture_m"] >= 2.0 for j in out["joints"])
    assert max(lengths(out)) <= 58.0


def test_furniture_blocking_everything_is_dropped_with_a_warning():
    rules = ExpansionJoints(furniture_clearance=5.0)
    furniture = [{"name": "Fender", "chainage": float(c)} for c in range(0, 121, 4)]
    out = place_joints(rules, [120.0], spacing=6.4, furniture=furniture)
    assert out["joints"] and any("clearance" in w for w in out["warnings"])


def test_corners_are_joints_and_runs_restart_the_pile_grid():
    out = place_joints(ExpansionJoints(), [100.0, 80.0], spacing=5.0)
    assert {"chainage": 100.0, "from": "corner"}.items() <= next(
        j for j in out["joints"] if j["chainage"] == 100.0
    ).items()
    assert all(s["length"] <= 58.0 for s in out["segments"])
    assert out["longest_by_run"][0] == max(s["length"] for s in out["segments"] if 0 in s["runs"])
    free = place_joints(ExpansionJoints(at_corners=False, position="anywhere"), [100.0, 80.0])
    assert all(j["from"] == "rules" for j in free["joints"])
    assert any(len(s["runs"]) == 2 for s in free["segments"])


def test_joints_set_by_hand():
    out = place_joints(ExpansionJoints(position="anywhere"), [150.0], fixed=[40.0])
    assert out["joints"][0] == {**out["joints"][0], "chainage": 40.0, "from": "by hand"}
    assert max(lengths(out)) <= 58.0
    only = place_joints(ExpansionJoints(), [150.0], fixed=[40.0], manual=True)
    assert lengths(only) == [40.0, 110.0]
    assert any("longer than" in w for w in only["warnings"])


def test_pile_rows_from_positions():
    pts = [[x, y] for y in (-6.0, -2.0, 2.0, 6.0) for x in (-7.0, -3.5)]
    assert pile_rows(pts)[0] == pytest.approx(4.0)
    assert pile_rows([[0.0, 0.0]])[0] is None


def test_furniture_from_costing_items():
    s = Section(costing=SectionCosting(berth_length=100.0, items=[OtherItem(name="Bollards", spacing=25.0)]))
    got = furniture_from_costing(s, 100.0)
    assert [f["chainage"] for f in got] == [0.0, 25.0, 50.0, 75.0, 100.0]


def test_section_layout_needs_a_berth_length():
    s = Section()
    assert section_joints(DesignSettings(), s, {})["segments"] == []
    s = Section(
        costing=SectionCosting(berth_length=300.0),
        joints=SectionJoints(pile_spacing=6.0, furniture=[FurnitureAt(name="Bollard", chainage=60.0)]),
    )
    out = section_joints(DesignSettings(), s, {})
    assert out["pile_spacing_from"] == "given" and out["furniture_from"].startswith("the positions")
    assert len(out["segments"]) == 6
    text = dxf.to_dxf(joints_drawing(out, DrawingSettings()))
    assert "TRITON-JOINTS" in text and "EJ " in text


def test_beam_restraint_uses_the_longest_segment():
    wb = berth()
    plain = run_section(DesignSettings(), section(), wb, only=ONLY)
    assert plain["joints"]["segments"] == []
    assert plain["beams"][0]["restraint"]["length_m"] == 58.0
    s = section(costing=SectionCosting(berth_length=90.0))
    out = run_section(DesignSettings(), s, wb, only=ONLY)
    j = out["joints"]
    assert j["pile_spacing_m"] == pytest.approx(4.0)
    assert len(j["segments"]) == 2
    (beam,) = out["beams"]
    assert beam["restraint"]["length_m"] == j["longest_m"]
    assert beam["restraint"]["length_from"] == "expansion joints"
    assert [g["length_m"] for g in beam["restraint"]["segments"]] == sorted(
        {round(g["length"], 1) for g in j["segments"]}, reverse=True
    )
    off = DesignSettings(joints=ExpansionJoints(use_in_restraint=False))
    assert run_section(off, s, wb, only=ONLY)["beams"][0]["restraint"]["length_m"] == 58.0


def test_a_turned_berth_places_the_same_joints():
    wb = berth()
    s = section(costing=SectionCosting(berth_length=90.0))
    straight = run_section(DesignSettings(), s, wb, only=ONLY)["joints"]
    turned = run_section(DesignSettings(), s, turn_workbook(wb, 30.0), only=ONLY)["joints"]
    assert turned["pile_spacing_m"] == pytest.approx(straight["pile_spacing_m"], abs=0.01)  # X, Y to 0.01 m
    assert lengths(turned) == pytest.approx(lengths(straight), abs=0.1)
