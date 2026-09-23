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
