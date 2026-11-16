"""Load combinations defined per section: the workbook is checked against them before mapping."""

from conftest import pile_sheet

from triton.suggest import combination_key, suggest
from triton.validation import apply_section, combination_check, import_sheets

DEFINED = ["QP", "PT-B-Apron", "Seismic 1", "Seismic 2"]


def test_seismic_numbers_are_different_combinations():
    assert combination_key("Seismic 1") != combination_key("Seismic 2")
    assert combination_key("Seismic2") == combination_key("Seismic 2") == combination_key("EQ 2")


def workbook():
    return import_sheets(
        {
            "Pile(1)-QP": pile_sheet(scale=0.5),
            "Pile(1)-PT-B-apron": pile_sheet(),
            "Pile(1)-Seismic2": pile_sheet(scale=1.1),
            "Pile(1)-PT-D": pile_sheet(scale=1.2),
        }
    )


def test_the_check_reads_spellings_as_defined_and_asks_about_the_rest():
    check = combination_check(workbook(), DEFINED, {})
    status = {f["name"]: (f["status"], f["target"]) for f in check["found"]}
    assert status == {
        "QP": ("defined", "QP"),
        "PT-B-apron": ("matched", "PT-B-Apron"),
        "Seismic2": ("matched", "Seismic 2"),
        "PT-D": ("undefined", None),
    }
    assert check["unresolved"] == ["PT-D"] and check["missing"] == ["Seismic 1"]


def test_an_undefined_combination_is_not_designed_until_resolved():
    wb = apply_section(workbook(), {}, DEFINED, {})
    assert set(wb.elements()["Pile(1)"]) == {"QP", "PT-B-Apron", "Seismic 2"}
    assert any(i.code == "undefined_combination" for s in wb.sheets for i in s.issues)
    # Said to be Seismic 1: used as Seismic 1.
    wb = apply_section(workbook(), {}, DEFINED, {"PT-D": "Seismic 1"})
    assert set(wb.elements()["Pile(1)"]) == {"QP", "PT-B-Apron", "Seismic 1", "Seismic 2"}
    # Left out: ignored.
    wb = apply_section(workbook(), {}, DEFINED, {"PT-D": ""})
    assert set(wb.elements()["Pile(1)"]) == {"QP", "PT-B-Apron", "Seismic 2"}
    assert not any(i.code == "undefined_combination" for s in wb.sheets for i in s.issues)


def test_mapping_suggests_only_defined_combinations():
    sheets = [
        {"name": "Pile 1 Seismic 2", "element": None, "combination": None},
        {"name": "Pile 1 PT-D", "element": None, "combination": None},
    ]
    out = suggest(sheets, [], DEFINED)
    assert out["Pile 1 Seismic 2"]["combination"] == "Seismic 2"
    assert "Pile 1 PT-D" not in out or out["Pile 1 PT-D"]["combination"] == ""
