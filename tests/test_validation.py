import copy

from conftest import pile_sheet, plate_sheet
from openpyxl import Workbook

from triton import import_sheets, import_workbook


def issue_codes(result, severity=None):
    return [i.code for i in result.all_issues() if severity is None or i.severity.value == severity]


def test_clean_workbook_has_no_problems(workbook):
    r = import_sheets(workbook)
    assert not issue_codes(r, "error") and not issue_codes(r, "warning"), r.all_issues()
    summary = r.summary()
    assert summary["elements"] == ["SPW", "Pile(1)", "Pile(2)", "Deck"]
    assert summary["coverage"]["Pile(1)"]["QP"] == "ok"
    assert "empty_sheet" in issue_codes(r, "info")  # Portal Frame


def test_identical_combinations_are_flagged(workbook):
    workbook["Pile(2)-PT-B-Yard"] = copy.deepcopy(workbook["Pile(2)-PT-B-Apron"])
    r = import_sheets(workbook)
    [issue] = [i for i in r.all_issues() if i.code == "identical_combinations"]
    assert issue.element == "Pile(2)"
    assert "PT-B-Apron" in issue.message and "PT-B-Yard" in issue.message
    assert r.summary()["coverage"]["Pile(2)"]["PT-B-Yard"] == "warning"


def test_missing_combination_relative_to_siblings(workbook):
    del workbook["Pile(2)-PT-B-Apron"]
    r = import_sheets(workbook)
    missing = [i for i in r.all_issues() if i.code == "missing_combination"]
    assert [(i.element, i.combination) for i in missing] == [("Pile(2)", "PT-B-Apron")]
    assert r.summary()["coverage"]["Pile(2)"]["PT-B-Apron"] == "missing"


def test_missing_qp(workbook):
    del workbook["Deck-QP"]
    assert "missing_qp" in issue_codes(import_sheets(workbook), "warning")


def test_node_set_differs(workbook):
    workbook["SPW-QP"] = plate_sheet(nodes=range(1, 4), scale=3.0)
    assert "node_set_differs" in issue_codes(import_sheets(workbook), "warning")


def test_unknown_and_duplicate_sheet_names(workbook):
    workbook["Notes"] = [["some", "text"]]
    workbook["Pile(1) -QP"] = pile_sheet(scale=7.0)
    r = import_sheets(workbook)
    assert "unknown_sheet" in issue_codes(r, "warning")
    assert "duplicate_sheet_name" in issue_codes(r, "error")


def test_read_xlsx_file(tmp_path, workbook):
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in workbook.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    path = tmp_path / "section.xlsx"
    wb.save(path)
    r = import_workbook(path)
    assert not issue_codes(r, "error")
    assert len(r.elements()["Pile(1)"]) == 3
