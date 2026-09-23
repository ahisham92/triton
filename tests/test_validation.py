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


def test_sheet_mapping_assigns_or_leaves_out_sheets(workbook, tmp_path, monkeypatch):
    import pytest
    from fastapi.testclient import TestClient
    from test_design import xlsx_bytes

    from triton.api import app
    from triton.project import SheetMapping
    from triton.validation import apply_mapping

    workbook["Middle piles row 3 Set B"] = pile_sheet(scale=2.0)
    workbook["Old run"] = plate_sheet()
    r = import_sheets(workbook)
    assert issue_codes(r).count("unknown_sheet") == 2
    m = apply_mapping(
        r,
        {
            "Middle piles row 3 Set B": {"element": "Pile(3)", "combination": "PT-B-Apron"},
            "Old run": {"ignore": True},
        },
    )
    assert "unknown_sheet" not in issue_codes(m) and "ignored_sheet" in issue_codes(m, "info")
    assert set(m.elements()["Pile(3)"]) == {"PT-B-Apron"}
    assert m.summary()["coverage"]["Pile(3)"]["QP"] == "missing"
    assert "Pile(3)" not in r.elements()  # the stored import is untouched
    with pytest.raises(ValueError):
        SheetMapping(element="Middle pile", combination="QP")

    monkeypatch.setenv("TRITON_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    p = client.post("/api/projects", json={"info": {"name": "Berth"}}).json()
    url = f"/api/projects/{p['id']}/sections/{p['sections'][0]['id']}"
    client.post(f"{url}/workbook", files={"file": ("s.xlsx", xlsx_bytes(workbook))})
    assert "Pile(3)" not in client.get(f"{url}/workbook").json()["elements"]
    p["sections"][0]["sheet_map"] = {
        "Middle piles row 3 Set B": {"element": "Pile(3)", "combination": "PT-B-Apron"}
    }
    assert client.put(f"/api/projects/{p['id']}", json=p).status_code == 200
    got = client.get(f"{url}/workbook").json()
    assert "Pile(3)" in got["elements"] and got["sheet_map"] == ["Middle piles row 3 Set B"]
