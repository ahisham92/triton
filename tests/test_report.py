"""Calculation reports built from stored design results, in the three formats."""

import io
import zipfile

import pytest
from fastapi.testclient import TestClient
from test_slabs import deck_workbook

from triton.api import app
from triton.design.runner import run_section
from triton.project import DesignSettings, PileInput, Project, Section, SlabInput
from triton.report import RENDERERS, build_report


@pytest.fixture(scope="module")
def designed():
    section = Section(
        name="Section 01",
        elements={"Deck": SlabInput(thickness=800), "Pile(1)": PileInput(head_level=2.7)},
    )
    project = Project(sections=[section])
    results = run_section(DesignSettings(), section, deck_workbook())
    results["run_at"] = "2026-09-23T22:00"
    return project, section, results


def test_report_follows_the_office_layout(designed):
    project, section, results = designed
    rep = build_report(project, section, results, "detailed")
    heads = [b.text for b in rep.blocks if b.kind == "h1"]
    assert heads[:3] == [
        "1. Introduction",
        "2. Design criteria",
        "3. Design of sections for strength and serviceability",
    ]
    assert heads[3].startswith("Appendix A")
    summary = next(
        b for b in rep.blocks if b.kind == "table" and b.headers[:2] == ["Element", "Crack width (mm)"]
    )
    assert summary.headers[-1] == "Governing load combination"
    assert any(row[0].startswith("Pile(1)") for row in summary.rows)
    captions = [b.text for b in rep.blocks if b.kind == "caption"]
    assert captions[0] == "Table 3-1: Summary of design results for Section 01"
    assert any("Punching" in c for c in captions)
    short = build_report(project, section, results, "summary")
    assert not any(b.text.startswith("Appendix") for b in short.blocks if b.kind == "h1")


def test_combi_tube_rows_and_corrosion_zones_as_the_office_tables():
    from test_combi import combi_sheets

    from triton.project import CombiWallInput

    section = Section(name="S", elements={"Combi Wall": CombiWallInput(top_level_to_ignore=0.0)})
    results = run_section(DesignSettings(), section, combi_sheets())
    results["run_at"] = "2026-09-23T22:00"
    rep = build_report(Project(sections=[section]), section, results, "summary")
    captions = [b.text for b in rep.blocks if b.kind == "caption"]
    assert captions[0].startswith("Table 2-1: Loss of thickness of the Combi Wall tube")
    zones = next(b for b in rep.blocks if b.kind == "table" and b.headers[0] == "Level (m)")
    assert zones.rows[-1] == ["-25 to -39", "1.75", "1.75", "14.5"]
    summary = next(b for b in rep.blocks if b.kind == "table" and b.headers[0] == "Element")
    bending, interaction = summary.rows[:2]
    assert bending[0].startswith("Combi Wall – steel tube") and "interaction" in interaction[0]
    num = [float(v.replace(",", "")) for v in bending[2:5]]
    assert num[0] == pytest.approx(abs(num[1]) / num[2], abs=2e-3)


@pytest.mark.parametrize("fmt", ["docx", "pdf", "xlsx"])
def test_renderers_write_files(designed, fmt):
    project, section, results = designed
    render, _ = RENDERERS[fmt]
    data = render(build_report(project, section, results, "detailed"))
    if fmt == "pdf":
        assert data.startswith(b"%PDF")
    else:
        assert zipfile.ZipFile(io.BytesIO(data)).namelist()


def test_report_route_needs_a_design():
    client = TestClient(app)
    p = client.post("/api/projects", json={}).json()
    sid = p["sections"][0]["id"]
    r = client.get(f"/api/projects/{p['id']}/sections/{sid}/design/report.pdf")
    assert r.status_code == 404
    assert client.get(f"/api/projects/{p['id']}/sections/{sid}/design/report.txt").status_code == 404
