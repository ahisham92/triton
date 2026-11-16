"""Suggested mapping for sheets named by hand."""

import pytest

from triton.suggest import combination_key, element_of, suggest


def sheets(*rows):
    return [{"name": n, "element": e, "combination": c} for n, e, c in rows]


WORKBOOK = sheets(
    ("Pile(1)-PT-B-Apron", "Pile(1)", "PT-B-Apron"),
    ("Pile(1)-PT-C-Yard", "Pile(1)", "PT-C-Yard"),
    ("Pile(1)-QP", "Pile(1)", "QP"),
    ("Deck-PT-B-Yard", "Deck", "PT-B-Yard"),
    ("Front Beam-PT-B-Apron", "Front Beam", "PT-B-Apron"),
)


@pytest.mark.parametrize(
    "name, element, combination",
    [
        ("Pile 3 - PT B Apron", "Pile(3)", "PT-B-Apron"),
        ("P4 QP", "Pile(4)", "QP"),
        ("pile(2)_PTC_yard", "Pile(2)", "PT-C-Yard"),
        ("Frnt Beam-QP", "Front Beam", "QP"),
        ("Rear beem-PT-C-Yard", "Rear Beam", "PT-C-Yard"),
        ("Combi-PT-C-Apron", "Combi Wall", "PT-C-Apron"),
        ("Sheet pile wall - Set B apron", "SPW", "PT-B-Apron"),
        ("Slab-PT-B-Yrd", "Deck", "PT-B-Yard"),
    ],
)
def test_names_typed_differently_are_read(name, element, combination):
    got = suggest([*WORKBOOK, *sheets((name, None, None))])[name]
    assert (got["element"], got["combination"]) == (element, combination)


def test_a_recognised_sheet_with_a_misspelled_combination_gets_the_workbooks_spelling():
    got = suggest([*WORKBOOK, *sheets(("Pile(2)-PT-B-Aprn", "Pile(2)", "PT-B-Aprn"))])
    assert got["Pile(2)-PT-B-Aprn"]["combination"] == "PT-B-Apron"
    assert got["Pile(2)-PT-B-Aprn"]["sure"]
    assert set(got) == {"Pile(2)-PT-B-Aprn"}  # the well-named sheets are left alone


def test_the_workbooks_own_spelling_wins():
    """If the team writes 'SetB-Apron' everywhere, that is the spelling suggested."""
    book = sheets(
        ("Pile(1)-SetB-Apron", "Pile(1)", "SetB-Apron"),
        ("Pile(2)-SetB-Apron", "Pile(2)", "SetB-Apron"),
        ("Pile 3 PT-B Apron", None, None),
    )
    assert suggest(book)["Pile 3 PT-B Apron"]["combination"] == "SetB-Apron"


def test_close_spellings_are_marked_to_check():
    got = suggest([*WORKBOOK, *sheets(("Rear beem-PT-C-Yard", None, None))])["Rear beem-PT-C-Yard"]
    assert not got["sure"] and "close spelling" in got["why"]


def test_nothing_is_suggested_for_sheets_that_are_not_results():
    got = suggest([*WORKBOOK, *sheets(("Summary", None, None), ("Portal Frame", None, None))])
    assert "Summary" not in got and "Portal Frame" not in got


def test_pieces():
    assert element_of("pile (12)") == ("Pile(12)", True)
    assert element_of("combi wal") == ("Combi Wall", True)
    assert element_of("notes") is None
    assert combination_key("PT-B-Apron") == combination_key("ptb apron") == ("ULS", "B", "Apron")
    assert combination_key("Seismic Yard") == ("SEISMIC", "Yard")
    assert combination_key("QP") == ("QP",)
    assert combination_key("Apron") is None
