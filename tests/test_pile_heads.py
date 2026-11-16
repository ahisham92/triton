"""An empty pile top level is taken from the plate over the pile, less its thickness."""

from __future__ import annotations

import pandas as pd

from triton.design.pile_heads import assumed_heads
from triton.project import Project


class _Sheet:
    def __init__(self, frame: pd.DataFrame):
        self.frame = frame


class _Book:
    def __init__(self, frames: dict[str, pd.DataFrame]):
        self._frames = frames

    def elements(self):
        return {n: {"QP": _Sheet(f)} for n, f in self._frames.items()}


def _section():
    return Project.model_validate(
        {
            "id": "0123456789ab",
            "info": {"name": "Q"},
            "sections": [
                {
                    "id": "abcdef12",
                    "name": "S",
                    "elements": {
                        "Pile(1)": {"kind": "pile", "diameter": 1200},
                        "Pile(2)": {"kind": "pile", "diameter": 1200, "head_level": 1.7},
                        "Pile(4)": {"kind": "pile", "diameter": 1200},
                        "Deck": {"kind": "slab", "thickness": 700},
                        "Rear Beam": {"kind": "rear_beam", "depth": 2000},
                    },
                }
            ],
        }
    ).sections[0]


def _pile(x: float) -> pd.DataFrame:
    return pd.DataFrame({"X": [x] * 3, "Y": [0.0] * 3, "Z": [-20.0, 0.0, 2.7]})


def test_empty_top_level_from_the_plate_over_the_pile():
    book = _Book(
        {
            "Pile(1)": _pile(-7.5),
            "Pile(2)": _pile(-12.0),
            "Pile(4)": _pile(-24.0),
            "Deck": pd.DataFrame({"X": [-7.5, -12.0, -18.0], "Y": [0.0] * 3, "Z": [2.7] * 3}),
            "Rear Beam": pd.DataFrame({"X": [-24.0, -24.0], "Y": [0.0, 3.0], "Z": [2.7, 2.7]}),
        }
    )
    stored = _section()
    section, notes = assumed_heads(stored, book)
    assert section.elements["Pile(1)"].head_level == 2.0  # 2.7 less the 700 mm deck
    assert section.elements["Pile(4)"].head_level == 0.7  # under the 2000 mm rear beam
    assert section.elements["Pile(2)"].head_level == 1.7  # set by the user: kept
    assert set(notes) == {"Pile(1)", "Pile(4)"} and "assumed" in notes["Pile(1)"]
    assert stored.elements["Pile(1)"].head_level is None  # the stored input is not changed


def test_no_plate_over_the_pile_leaves_it_empty():
    book = _Book({"Pile(1)": _pile(0.0)})
    section, notes = assumed_heads(_section(), book)
    assert section.elements["Pile(1)"].head_level is None and notes == {}
