"""The .ads writer against a real AdSec 8.3 pile file (tests/data/adsec_pile2.py)."""

import math
import struct

import pytest
from data.adsec_pile2 import RECORDS

from triton import adsec
from triton.design.circular import CircularSection, ConcreteLaw, Ring, SteelLaw

# The office file: 1200 mm pile, C40/50, 26Ø25 on the perimeter at r = 512.5 mm and 13Ø32 on a
# circle through (0.06, 0.47) m; loads 1-6 QP (SLS cases) and 7-12 ULS.
FORCES = [struct.unpack("<3d", item[-24:]) for item in RECORDS["forces"][27:].split(b"####")]
KN = [(n / 1e3, my / 1e3, mz / 1e3) for n, my, mz in FORCES]
# AdSec's ULS moment capacity at each ULS load's N (Section Results record of the same file).
ADSEC_MRD = [4863.3, 4325.4, 4392.3, 4504.4, 4583.6, 4482.7]


def _same(ours: bytes, theirs: bytes) -> None:
    """Byte-equal except 4-byte floats that agree to 1e-6."""
    assert len(ours) == len(theirs)
    i = 0
    while i < len(ours):
        if ours[i] != theirs[i]:
            for s in range(max(0, i - 3), i + 1):  # floats are not word-aligned: try each window
                a = struct.unpack("<f", ours[s : s + 4])[0]
                b = struct.unpack("<f", theirs[s : s + 4])[0]
                if math.isfinite(a) and abs(a - b) < 1e-6:
                    i = s + 4
                    break
            else:
                raise AssertionError(
                    f"byte {i} differs: {ours[i - 8 : i + 8].hex()} vs {theirs[i - 8 : i + 8].hex()}"
                )
        else:
            i += 1


def test_generated_records_match_the_office_file():
    assert adsec.forces(KN) == RECORDS["forces"]
    assert adsec.load_titles([f"Load Case {k}" for k in range(1, 13)]) == RECORDS["load_titles"]
    sls = bytearray(RECORDS["sls"])
    sls[534] = 0  # the last SLS case carries a flag the program sets when saving (the selected case)
    assert adsec.sls_cases(list(range(1, 7))) == bytes(sls)
    assert adsec.uls_cases(list(range(7, 13))) == RECORDS["uls"]
    assert adsec.analysis(6) == RECORDS["analysis"]
    groups = [
        adsec.circle_group(32, 13, (0.06, 0.47), "500B"),
        adsec.perimeter_group(25, 26, 512.5, "500B"),
    ]
    _same(adsec.section("Concrete Pile", 1200, "C40/50", 75, groups), RECORDS["sections"])


def test_pile_file_round_trip():
    rows = [
        {"case": "max N", "combination": "PT-C", "z": -1.0, "N_kN": n, "M2_kNm": a, "M3_kNm": b}
        for n, a, b in KN
    ]
    data = adsec.pile_file(
        job="J1",
        title="Pile(2) - Part 1",
        diameter_mm=1200,
        concrete="C40/50",
        rebar="B500B",
        cover_mm=75,
        rings=[
            {"count": 26, "diameter": 25, "radius": 512.5},
            {"count": 13, "diameter": 32, "radius": 473.8},
        ],
        qp=rows[:6],
        uls=rows[6:],
    )
    assert data.startswith(b"OAWIN") and data.endswith(b"####@@@@")
    recs = adsec.read_records(data)
    assert {
        "Titles",
        "Sections",
        "Rebar",
        "Section Forces",
        "SLS Analysis Cases",
        "ULS Analysis Cases",
    } <= set(recs)
    assert "Section Results" not in recs
    assert adsec.read_forces(data) == pytest.approx(KN)
    assert b"500B\0GRP_ARC" in recs["Sections"] and b"STD%C%1200." in recs["Sections"]


def test_triton_capacity_matches_adsec():
    """Same section in Triton (gross area, αcc 1.0): within 1% of AdSec's MRd at each ULS load."""
    first = math.hypot(0.06, 0.47) * 1e3
    sec = CircularSection(
        1200,
        (Ring(26, 25, 512.5), Ring(13, 32, first)),
        ConcreteLaw(40, 1.5, 1.0),
        SteelLaw(500, 1.15),
        deduct=False,
    )
    for (n, _, _), mrd in zip(KN[6:], ADSEC_MRD, strict=True):
        assert sec.moment_capacity(n) == pytest.approx(mrd, rel=0.01)


def test_section_files_one_per_station():
    rings = [{"count": 26, "diameter": 25, "radius": 512.5}]
    row = {"case": "max N", "combination": "PT-C", "z": -1.0, "N_kN": 100.0, "M2_kNm": 1.0, "M3_kNm": 50.0}
    station = {"top": 1.7, "bottom": -5.0, "cage": "26Ø25", "rings": rings, "qp": [row] * 7, "uls": [row] * 7}
    results = {"piles": [{"element": "Pile(2)", "governing_sets": [station, {**station, "top": -5.0}]}]}
    info = {"Pile(2)": {"diameter": 1200.0, "concrete": "C40/50", "cover": 75.0}}
    files = adsec.section_files("Job", "Section 1", results, info, "B500B")
    assert list(files) == ["Pile(2) - 1200mm - Part 1.ads", "Pile(2) - 1200mm - Part 2.ads"]
    assert len(adsec.read_forces(files["Pile(2) - 1200mm - Part 1.ads"])) == 14


def test_rectangles_match_the_office_beam_and_slab_files():
    from data.adsec_rect import BEAM_SECTIONS, SLAB_SECTIONS

    # The slab strip: 700 x 1050, three user bar lines, then a slab group Triton does not write.
    rest = SLAB_SECTIONS[SLAB_SECTIONS.index(b"\0GRP_SLAB") :]
    groups = [
        adsec.line_group(32, 7, (0.45, -0.23), (-0.45, -0.23), "500B"),
        adsec.line_group(20, 7, (0.45, 0.25), (-0.45, 0.25), "500B"),
        adsec.line_group(16, 7, (0.45, -0.19), (-0.45, -0.19), "500B"),
        rest,
    ]
    _same(adsec.rect_section("Slab 700mm", 700, 1050, "C40/50", (50, 75, 75, 75), 0, groups), SLAB_SECTIONS)
    # The front beam: 4500 wide, 2000 deep, 50 mm cover to 16 mm links.
    rest = BEAM_SECTIONS[BEAM_SECTIONS.index(b"\0GRP_BEAM") :]
    _same(adsec.rect_section("Front Beam", 2000, 4500, "C40/50", (50,) * 4, 16, [rest]), BEAM_SECTIONS)


def test_slab_strip_bars():
    # The office strip's bottom mesh: 7Ø25 at 150 over 1050, 75 mm cover: centres 87.5 mm up.
    assert adsec.slab_bars({"phi": 25, "spacing_mm": 150, "layers": 1}, None, 0, 75, 1050) == [
        (25, 7, -450.0, 450.0, 87.5)
    ]
    # Additional bars in every gap in two layers and under the mesh: 4 bars per mesh spacing.
    bars = adsec.slab_bars(
        {"phi": 32, "spacing_mm": 200, "layers": 1}, "Ø32 @ 200 in 2 layers + Ø32 under the mesh", 0, 50, 1000
    )
    assert sum(b[1] for b in bars) == 20
    assert {b[4] for b in bars} == {66.0, 123.0}
    # Every second gap: half as many additional bars; bars along Y sit inside the X bars.
    bars = adsec.slab_bars({"phi": 16, "spacing_mm": 200, "layers": 1}, "Ø20 @ 400", 25, 50, 1200)
    assert [(b[0], b[1]) for b in bars] == [(16, 6), (20, 3)]
    assert bars[0][4] == 50 + 25 + 8
    assert adsec.strip_width(150) == 1050 and adsec.strip_width(200) == 1000


def test_beam_and_slab_files():
    beam = {
        "element": "Front Beam",
        "width_mm": 2000,
        "depth_mm": 1600,
        "cover_mm": 50,
        "concrete": "C40/50",
        "cage": {
            "label": "Top 10Ø16",
            "link_diameter_mm": 16,
            "lines": [{"phi": 16, "count": 10, "a": [-926, 726], "b": [926, 726]}],
        },
        "governing_sets": [
            {
                "qp": [{"case": "max M3", "combination": "QP", "N_kN": 10.0, "M2_kNm": 5.0, "M3_kNm": 700.0}],
                "uls": [
                    {"case": "max M3", "combination": "PT-C", "N_kN": -20.0, "M2_kNm": 6.0, "M3_kNm": 900.0}
                ],
            }
        ],
    }
    files = adsec.beam_files("Job", "Section 1", beam, "B500B")
    data = files["Front Beam 2000X1600.ads"]
    assert [tuple(round(v) for v in f) for f in adsec.read_forces(data)] == [(10, 700, 5), (-20, 900, 6)]
    row = {
        "mesh": {"phi": 25, "spacing_mm": 150, "layers": 1},
        "additional_bars": None,
        "station": [8.0, 12.0],
        "sets": {
            "uls": [{"combination": "PT-C", "N_kN_per_m": -100.0, "M_kNm_per_m": 1000.0}],
            "qp": [{"combination": "QP", "N_kN_per_m": -50.0, "M_kNm_per_m": 600.0}],
        },
    }
    slab = {
        "thickness_mm": 700,
        "cover_top_mm": 50,
        "cover_bottom_mm": 75,
        "concrete": "C40/50",
        "layers": {},
        "strip_design": {
            "rows": [
                {**row, "key": "b", "layer": "bottom_x"},
                {**row, "key": "t", "layer": "top_x", "sets": {"uls": [], "qp": []}},
            ],
            "table": [
                {
                    "moment": "M11",
                    "strip": "column",
                    "label": "Station 8 to 12",
                    "keys": {"bottom": ["b"], "top": ["t"]},
                }
            ],
        },
    }
    files = adsec.slab_files("Job", "Section 1", slab, "B500B")
    data = files["SLAB 700 - m11 - 8 to 12 - CS.ads"]
    # A strip 1050 wide: forces per metre x 1.05, QP first.
    assert [tuple(round(v, 1) for v in f) for f in adsec.read_forces(data)] == [
        (-52.5, 630.0, 0.0),
        (-105.0, 1050.0, 0.0),
    ]
    assert b"STD%R%700.%1050." in data


def test_slab_sets_are_the_four_extremes_and_the_governing_ones():
    from triton.design.slabs import extreme_sets

    c = ["A", "B", "C", "D", "E"]
    m = [100.0, 300.0, 50.0, 200.0, 10.0]
    n = [-50.0, 0.0, 400.0, 10.0, -300.0]
    bottom = extreme_sets(c, m, n, gov=3)
    assert [(x["case"], x["combination"]) for x in bottom] == [
        ("max N", "C"),
        ("min N", "E"),
        ("max M", "B"),
        ("governing", "D"),
    ]
    top = extreme_sets(["F", "G"], [-400.0, -20.0], [5.0, 600.0], gov=0)
    assert [(x["case"], x["combination"]) for x in top] == [("max N", "G"), ("min N, min M, governing", "F")]
    both = adsec.slab_sets({"bottom": [{"sets": {"uls": bottom}}], "top": [{"sets": {"uls": top}}]}, "uls")
    assert [(x["case"], x["combination"], x["face"]) for x in both] == [
        ("max N", "G", "top"),
        ("min N", "E", "bottom"),
        ("max M", "B", "bottom"),
        ("min M, governing hogging", "F", "top"),
        ("governing sagging", "D", "bottom"),
    ]


def test_slab_strip_width_follows_the_tension_face_mesh():
    row = {
        "mesh": {"phi": 20, "spacing_mm": 200, "layers": 1},
        "additional_bars": None,
        "station": [0.0, 4.0],
    }
    sets = {
        "uls": [{"case": "max M", "combination": "U", "N_kN_per_m": 0.0, "M_kNm_per_m": -100.0}],
        "qp": [],
    }
    slab = {
        "thickness_mm": 700,
        "cover_top_mm": 50,
        "cover_bottom_mm": 75,
        "concrete": "C40/50",
        "layers": {},
        "strip_design": {
            "rows": [
                {**row, "key": "b", "layer": "bottom_x", "sets": {"uls": [], "qp": []}},
                {
                    **row,
                    "key": "t",
                    "layer": "top_x",
                    "sets": sets,
                    "mesh": {**row["mesh"], "spacing_mm": 150},
                },
            ],
            "table": [
                {
                    "moment": "M11",
                    "strip": "column",
                    "label": "Station 0 to 4",
                    "face": "top",
                    "keys": {"bottom": ["b"], "top": ["t"]},
                }
            ],
        },
    }
    data = adsec.slab_files("Job", "S", slab, "B500B")["SLAB 700 - m11 - 0 to 4 - CS.ads"]
    assert b"STD%R%700.%1050." in data
    assert [tuple(round(v, 1) for v in f) for f in adsec.read_forces(data)] == [(0.0, -105.0, 0.0)]
