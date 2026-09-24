import math

import numpy as np
import pytest
from test_beams import beam_rows, uniform

from triton.design.beams import design_beam
from triton.design.circular import ConcreteLaw, SteelLaw
from triton.design.rect import Bars, RectSection
from triton.design.rooms import room_shape, st_venant
from triton.project import BeamInput, BeamRoom, DesignSettings
from triton.validation import import_sheets

LAWS = (ConcreteLaw(40, 1.5, 1.0), SteelLaw(500))


def test_channel_section_takes_the_room_out():
    # 2000 × 2000 beam, 1000 × 1700 room from the top: 300 mm floor, 500 mm walls.
    bars = Bars.join(Bars.row(10, 25, -900, 900), Bars.row(2, 25, 900, 850))
    solid = RectSection(2000, 2000, bars, *LAWS)
    room = RectSection(2000, 2000, bars, *LAWS, void=(-500, 500, -700, 1000))
    assert room.area_concrete == pytest.approx(2000 * 2000 - 1000 * 1700)
    # Sagging: the compression block sits in the two 500 mm walls only.
    a_s = 10 * math.pi * 25**2 / 4
    fyd, fcd = 500 / 1.15, 40 / 1.5
    x = a_s * fyd / (0.8 * 1000 * fcd)
    sag = room.m_rd("v", np.array([1]), np.array([0.0]))[0]
    assert sag == pytest.approx(a_s * fyd * (1900 - 0.4 * x) / 1e6, rel=0.01)
    assert sag < solid.m_rd("v", np.array([1]), np.array([0.0]))[0]
    # Squash load drops with the concrete taken out.
    assert room.n_rd()[0] < 0.6 * solid.n_rd()[0]
    # Cracked section: the neutral axis goes deeper when the top is only two walls.
    assert room.cracked(0.0, 1000.0, 12_000)["x"] > solid.cracked(0.0, 1000.0, 12_000)["x"]


def test_room_shape_from_the_inputs():
    s, notes = room_shape(BeamRoom(start=0, end=3, height=1700, width=1000), 2000, 2000, -1)
    assert (s.bottom, s.height, s.wall_sea, s.wall_land) == (300, 1700, 500, 500) and not notes
    # A thicker floor makes the room lower.
    s, notes = room_shape(BeamRoom(start=0, end=3, height=1700, width=1000, bottom=450), 2000, 2000, -1)
    assert (s.bottom, s.height) == (450, 1550) and "1550 mm high" in notes[0]
    # Sea-side wall given: land side +u, so the room sits towards +u less its wall.
    s, _ = room_shape(BeamRoom(start=0, end=3, width=1000, front_wall=300), 2000, 2000, 1)
    assert (s.wall_sea, s.wall_land) == (300, 700) and s.void[:2] == (-700, 300)
    assert room_shape(BeamRoom(start=0, end=3, height=2100), 2000, 2000, 1)[0] is None
    with pytest.raises(ValueError):
        BeamRoom(start=3, end=2)
    assert st_venant(2000, 500) > st_venant(1000, 300)


def _sheets(uls, qp):
    raw = {"Front Beam-PT-B-Apron": beam_rows(uls), "Front Beam-QP": beam_rows(qp)}
    return import_sheets(raw).elements()["Front Beam"]


def _design(beam, sheets):
    return design_beam("Front Beam", beam, DesignSettings(), sheets, [], {}, {"1": "X", "2": "Y"})


def test_room_in_the_front_beam_is_checked_and_its_bars_designed():
    sheets = _sheets(uniform(m22=-1400.0, q23=900.0, m11=-250.0, n2=50.0), uniform(m22=-1000.0, m11=-180.0))
    room = {"start": 4, "end": 7, "height": 1700, "width": 1000}
    d = _design(BeamInput(width=2000, depth=2000, rooms=[room]), sheets)
    (rm,) = d["rooms"]
    assert rm["section"]["bottom_mm"] == 300 and rm["stations"] > 5
    # The top bars through the room are cut and come back, at least as much, at the top of the walls.
    assert rm["bars"]["cut_top"]["count"] > 0
    assert rm["bars"]["wall_top"]["area_mm2"] >= rm["bars"]["cut_top"]["area_mm2"]
    # Hogging over the room: the walls' tops crack, the floor carries the transverse moment.
    assert set(rm["cracks"]) == {"top"} and rm["cracks"]["top"]["passed"]
    assert {"sea", "land", "floor"} <= set(rm["shear"])
    assert rm["shear"]["sea"]["V_share"] == 0.5 and rm["shear"]["sea"]["link"]
    assert rm["frame"]["floor"]["passed"] and rm["frame"]["walls"]["passed"]
    assert rm["passed"] and d["passed"] and rm["utilisation"] <= d["utilisation"]
    assert (
        "L-bars" in rm["frame"]["corner_bars"]["label"] and "diagonal" not in rm["corners"]["diagonals"][:2]
    )
    assert any(n.startswith("Room 1 (4 to 7 m): passes") for n in d["notes"])
    # The same beam without the room has no room results.
    assert _design(BeamInput(width=2000, depth=2000), sheets)["rooms"] == []


def test_a_room_that_fails_gets_the_floor_that_passes():
    sheets = _sheets(uniform(m22=-1400.0, q23=900.0, m11=-250.0, n2=50.0), uniform(m22=-1000.0, m11=-180.0))
    thin = {"start": 4, "end": 7, "height": 1850, "width": 1400}
    d = _design(BeamInput(width=2000, depth=2000, rooms=[thin]), sheets)
    (rm,) = d["rooms"]
    assert not rm["passed"] and not d["passed"]
    assert any("too thin for the beam's bottom bars" in n for n in rm["notes"])
    t = rm["suggestion"]["bottom_mm"]
    assert t > 150 and t % 50 == 0
    # The suggested floor passes; 50 mm less does not.
    ok = _design(BeamInput(width=2000, depth=2000, rooms=[{**thin, "bottom": t}]), sheets)["rooms"][0]
    assert ok["passed"] and ok["section"]["height_mm"] == 2000 - t
    less = _design(BeamInput(width=2000, depth=2000, rooms=[{**thin, "bottom": t - 50}]), sheets)["rooms"][0]
    assert not less["passed"]


def test_room_goes_to_the_drawings_and_report():
    from triton.design.export import pile_cages
    from triton.drawings import from_cages
    from triton.project import DrawingSettings
    from triton.report import Report, _beam

    sheets = _sheets(uniform(m22=400.0, q23=150.0, m11=300.0), uniform(m22=250.0, m11=200.0))
    d = _design(BeamInput(width=2000, depth=2000, rooms=[{"start": 4, "end": 7}]), sheets)
    (b,) = pile_cages("P", {"beams": [d]}, "S")["beams"]
    (room,) = b["rooms"]
    assert room["void_mm"] == d["rooms"][0]["section"]["void_mm"] and len(room["bars"]) > 20
    out = from_cages({"beams": [b], "piles": [], "slabs": []}, DrawingSettings())
    views = [v for v in out["views"] if "Room 1" in v["name"]]
    assert len(views) == 1 and any(i["type"] == "bar" for i in views[0]["items"])
    r = Report("t", "s")
    _beam(r, d)
    assert any("Room 1: room in the beam" in blk.text for blk in r.blocks if blk.text)
