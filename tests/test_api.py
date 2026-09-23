import io

from conftest import plate_sheet
from fastapi.testclient import TestClient
from openpyxl import Workbook

from triton.api import app

client = TestClient(app)


def xlsx_bytes(sheets):
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_index_page():
    r = client.get("/")
    assert r.status_code == 200 and "Triton" in r.text


def test_check_workbook():
    data = xlsx_bytes({"SPW-QP": plate_sheet(), "SPW-PT-B-Apron": plate_sheet(scale=2.0)})
    r = client.post("/api/workbooks/check", files={"file": ("section.xlsx", data)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["file"] == "section.xlsx"
    assert body["coverage"]["SPW"] == {"PT-B-Apron": "ok", "QP": "ok"}
    assert body["counts"]["error"] == 0


def test_rejects_other_file_types():
    r = client.post("/api/workbooks/check", files={"file": ("notes.csv", b"a,b")})
    assert r.status_code == 400


def test_corrupt_workbook():
    r = client.post("/api/workbooks/check", files={"file": ("broken.xlsx", b"not a zip")})
    assert r.status_code == 400
