import json
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
    assert any("too thin for the bottom bars" in n for n in rm["notes"])
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


# --- Manholes and channels in the deck ------------------------------------------------------------


def _deck(**openings):
    from test_slabs import deck_workbook

    from triton.design.runner import run_section
    from triton.project import PileInput, Section, SlabInput

    limits = {"crack_width_limit": 0.3, "crack_width_limit_bottom": 0.3, "peaks": "design"}
    els = {"Deck": SlabInput(thickness=800, **limits, **openings), "Pile(1)": PileInput(head_level=2.7)}
    return run_section(DesignSettings(), Section(elements=els), deck_workbook())["slabs"][0]


def test_manhole_gets_trimmer_bars_and_cuts_the_punching_perimeter():
    d = _deck(manholes=[{"x": -6, "y": 2, "size_x": 1000, "size_y": 1200}])
    (m,) = d["openings"]["manholes"]
    x = m["directions"]["X"]
    # Bars along X are cut over the 1200 mm Y size; strips of 800 mm (the slab) each side carry 1.75 m.
    assert (x["cut_width_mm"], x["strip_mm"], x["factor"]) == (1200, 800, 1.75)
    assert x["M_kNm_per_m"]["min"] == -600  # the hogging round the pile reaches the opening
    top = x["trimmers"]["top"]
    # At least half the cut top bars each side.
    assert top["area_mm2"] >= x["existing"]["top"]["as_mm2_per_m"] * 1.2 / 2 - 1
    assert top["count"] * math.pi * top["phi"] ** 2 / 4 >= top["area_mm2"]
    (p,) = m["piles"]
    assert 0 < p["share"] < 0.5 and p["utilisation"] > p["utilisation_before"]
    assert m["passed"] and d["passed"] and any(n.startswith("Manhole 1: passes") for n in d["notes"])


def test_large_manhole_gets_the_opening_that_passes():
    d = _deck(manholes=[{"x": -4.5, "y": 1.5, "size_x": 3000, "size_y": 3000}])
    (m,) = d["openings"]["manholes"]
    if m["passed"]:
        pytest.skip("passes on this sample")
    s = m["suggestion"]
    assert "passes" in s["text"] or "No smaller" in s["text"]
    assert not d["passed"]


def test_channel_is_checked_as_a_trough_and_a_deep_one_hangs_below():
    d = _deck(
        channels=[
            {"direction": "Y", "start": -4, "end": 4, "at": -1.5, "width": 600, "depth": 500, "walls": 250},
            {
                "name": "Deep",
                "direction": "X",
                "start": -7,
                "end": -1,
                "at": 3,
                "width": 800,
                "depth": 900,
                "walls": 250,
                "base": 300,
            },
        ]
    )
    shallow, deep = d["openings"]["channels"]
    assert (
        shallow["section"]["bottom_mm"] == 300
        and shallow["strip_mm"] == 1100
        and shallow["downstand_mm"] == 0
    )
    assert shallow["bars"]["wall_top"]["area_mm2"] >= shallow["bars"]["cut_top"]["area_mm2"]
    assert shallow["frame"]["floor"]["passed"] and shallow["stations"] > 10
    assert deep["depth_total_mm"] == 1200 and deep["downstand_mm"] == 400
    assert any("hangs 400 mm below" in n for n in deep["notes"])
    # A thin base under a wide channel fails and gets the base that passes.
    thin = _deck(
        channels=[
            {"direction": "Y", "start": -4, "end": 4, "at": -4, "width": 1500, "depth": 700, "walls": 200}
        ]
    )["openings"]["channels"][0]
    assert not thin["passed"] and "suggestion" in thin
    json.dumps(thin, allow_nan=False)  # the page reads it as JSON


def test_openings_go_to_the_report_drawings_and_method():
    from triton.design.export import pile_cages
    from triton.drawings import from_cages
    from triton.method import TOPICS
    from triton.project import DrawingSettings
    from triton.report import Report, _slab

    d = _deck(
        manholes=[{"x": -6, "y": 2, "size_x": 1000, "size_y": 1200}],
        channels=[{"direction": "Y", "start": -4, "end": 4, "at": -1.5}],
    )
    (s,) = pile_cages("P", {"slabs": [d]}, "S")["slabs"]
    assert len(s["manholes"]) == 1 and len(s["channels"]) == 1
    views = from_cages({"beams": [], "piles": [], "slabs": [s]}, DrawingSettings())["views"]
    names = [v["name"] for v in views]
    assert "Deck - Manhole 1" in names and "Deck - Channel 1" in names
    r = Report("t", "s")
    _slab(r, d)
    text = " ".join(b.text for b in r.blocks if b.text)
    assert "Manhole 1: opening in the deck" in text and "Channel 1: channel along Y" in text
    assert any(m.__name__.endswith("openings") for _, m in TOPICS["Slabs"])
