from triton.design.piles import util_bands
from triton.design.runner import combi_bands
from triton.geometry import section_geometry
from triton.validation import import_sheets


def test_section_geometry(workbook):
    geo = {e["element"]: e for e in section_geometry(import_sheets(workbook))}
    pile = geo["Pile(1)"]
    assert pile["kind"] == "beam" and pile["lines"] == [[0.0, 0.0, -1.0, -5.0]]
    assert geo["SPW"]["kind"] == "plate" and set(geo["SPW"]["box"]) == {"X", "Y", "Z"}


def test_util_bands_take_the_highest_per_pile_and_band():
    import pandas as pd

    loads = pd.DataFrame(
        {
            "X": [0.0, 0.0, 0.0, 1.0],
            "Y": [0.0, 0.0, 0.0, 0.0],
            "Z": [0.1, -0.1, -2.0, 0.0],
            "util": [0.5, 0.7, 0.2, 0.9],
        }
    )
    assert sorted(util_bands(loads)) == [[0.0, 0.0, -2.0, 0.2], [0.0, 0.0, 0.0, 0.7], [1.0, 0.0, 0.0, 0.9]]


def test_combi_bands_add_the_tube_below_the_infill():
    wall = {
        "infill": {"bands": [[0.0, 0.0, 0.0, 0.6]]},
        "tube": {"profile": [{"z": 0.0, "util": 0.8}, {"z": -30.0, "util": 0.4}]},
    }
    bands = sorted(combi_bands(wall, [[0.0, 0.0], [0.0, 3.2]]))
    assert bands == [
        [0.0, 0.0, -30.0, 0.4],
        [0.0, 0.0, 0.0, 0.8],
        [0.0, 3.2, -30.0, 0.4],
        [0.0, 3.2, 0.0, 0.8],
    ]
