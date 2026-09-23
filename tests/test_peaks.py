import pytest
from conftest import EMBEDDED_HEADER

from triton.design.runner import run_section
from triton.project import DesignSettings, Section
from triton.validation import import_sheets


def rows(piles):
    """Embedded beam rows; piles maps Y -> [(node, z, N, M_2)] in Plaxis signs."""
    out = [EMBEDDED_HEADER]
    for y, loads in piles.items():
        for node, z, n, m in loads:
            row = ["EmbeddedBeam\\_1\\_1", node, 1, 0.0, y, z]
            for v in (n, 0.0, 0.0, 0.0, m, 0.0):
                row += [v, v - 1.0, v + 1.0]
            out.append(row + [1.0, 2.0, 3.0, "N/A"])
    return out


def smooth(first_node, spike_at=None, spike=0.0):
    return [
        (first_node + i, -1.0 * i, -2000.0, spike if i == spike_at else 1500.0 - 100.0 * i) for i in range(11)
    ]


def run(section, piles):
    sheets = import_sheets({"Pile(1)-PT-B-Apron": rows(piles), "Pile(1)-QP": rows(piles)})
    section.add_elements(["Pile(1)"])
    section.elements["Pile(1)"].head_level = 0.0
    (pile,) = run_section(DesignSettings(), section, sheets)["piles"]
    return pile


def test_working_zone_leaves_out_edge_results_but_counts_every_pile():
    piles = {0.0: smooth(1), 16.0: [(100 + i, -1.0 * i, -2000.0, 6000.0) for i in range(11)]}
    everything = run(Section(), piles)
    inside = run(Section(y_min=-14, y_max=14), piles)
    assert everything["governing"]["M_kNm"] == pytest.approx(6000.0)
    assert inside["governing"]["M_kNm"] == pytest.approx(1500.0)
    assert inside["count"] == 2 and len(inside["positions"]) == 2
    assert any("Working zone Y -14 to 14 m" in n for n in inside["notes"])
    with pytest.raises(ValueError, match="X from must be less"):
        Section(x_min=5, x_max=-5)


def test_isolated_peak_used_averaged_or_left_out():
    piles = {0.0: smooth(1, spike_at=5, spike=4000.0)}
    raw = run(Section(), piles)
    (peak,) = [p for p in raw["peaks"] if p["combination"] == "PT-B-Apron"]
    assert (peak["node"], peak["z"], peak["M_kNm"]) == (6, -5.0, 4000.0)
    assert peak["treatment"] == "used as it is" and raw["governing"]["M_kNm"] == pytest.approx(4000.0)
    assert any("isolated peak" in n for n in raw["notes"])

    averaged = run(Section(peaks="average"), piles)
    assert averaged["peaks"][0]["treatment"] == "averaged"
    assert averaged["governing"]["M_kNm"] == pytest.approx(1500.0)
    # The averaged node takes the mean of the nodes either side (1100 and 900).
    # The averaged node takes the mean of the nodes either side (1100 and 900): the -5 m band
    # (-5.25 to -4.75) then holds only that node.
    assert max(m["M_kNm"] for m in averaged["moments"] if m["z"] == -5.0) == pytest.approx(1000.0)

    left = run(Section(excluded_peaks=[peak["key"]]), piles)
    assert {p["treatment"] for p in left["peaks"] if p["key"] == peak["key"]} == {"left out"}
    assert all(m["z"] != -5.0 or m["M_kNm"] < 4000 for m in left["moments"])


def test_no_peaks_in_smooth_results():
    assert run(Section(), {0.0: smooth(1)})["peaks"] == []


def test_peaks_inside_the_slab_are_not_reported():
    loads = smooth(1)
    loads[0] = (1, 0.0, -2000.0, 9000.0)  # at the top level: reported
    section = Section()
    assert run(section, {0.0: loads})["peaks"]
    high = [(1, 1.0, -2000.0, 9000.0)] + [(n + 1, z, nn, m) for n, z, nn, m in smooth(1)]
    assert run(Section(), {0.0: high})["peaks"] == []  # 1 m above the top level
