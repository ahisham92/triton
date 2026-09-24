"""Displacements estimated from the straining actions: curvature M / EI integrated twice."""

from __future__ import annotations

import io
import math

import numpy as np
import pytest
from conftest import pile_sheet
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from test_design import xlsx_bytes

from triton import fresh
from triton.design.deflection import between_supports, integrate, pick_combination
from triton.project import DeflectionSettings, Project

EI = 2.0e5  # kN·m²


# --- Closed forms ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("n", [2, 5, 41])
def test_cantilever_tip_load(n):
    """Fixed at s = 0, load P at the tip s = L: M = P (L − s), tip deflection P L³ / 3EI."""
    p, length = 120.0, 8.0
    s = np.linspace(0, length, n)
    w = integrate(s, p * (length - s) / EI)
    assert w[-1] == pytest.approx(p * length**3 / (3 * EI), rel=1e-12)
    assert w[0] == 0.0
    # Along the member too: w(s) = P s² (3L − s) / 6EI.
    assert w == pytest.approx(p * s**2 * (3 * length - s) / (6 * EI), rel=1e-12, abs=1e-15)


def test_cantilever_uniform_moment():
    m, length = 250.0, 12.0
    s = np.linspace(0, length, 7)
    w = integrate(s, np.full(len(s), m / EI))
    assert w[-1] == pytest.approx(m * length**2 / (2 * EI), rel=1e-12)


def test_uneven_stations_and_offset_toe():
    """Stations need not be even or start at zero (levels from the toe up)."""
    p, toe, head = 80.0, -20.0, 2.5
    z = np.array([toe, -18.3, -11.0, -10.2, -4.0, 0.7, head])
    length = head - toe
    w = integrate(z, p * (head - z) / EI)
    assert w[-1] == pytest.approx(p * length**3 / (3 * EI), rel=1e-12)


def test_held_at_the_firm_soil_level():
    s = np.linspace(0, 10, 21)
    w = integrate(s, np.full(len(s), 1e-3), hold=4.0)
    assert w[0] == 0.0
    assert np.interp(4.0, s, w) == pytest.approx(0.0, abs=1e-15)
    # A uniform curvature held at 0 and 4 m: w = κ s (s − 4) / 2.
    assert w == pytest.approx(1e-3 * s * (s - 4) / 2, abs=1e-12)


def test_simply_supported_strip_uniform_load():
    """Sagging M = q x (L − x) / 2 between two supports: midspan 5 q L⁴ / 384 EI downwards."""
    q, length = 30.0, 6.0
    x = np.linspace(0, length, 241)
    s, w = between_supports(x, q * x * (length - x) / 2 / EI, [0.0, length])
    assert w[0] == pytest.approx(0.0, abs=1e-15) and w[-1] == pytest.approx(0.0, abs=1e-15)
    assert np.interp(length / 2, s, w) == pytest.approx(-5 * q * length**4 / (384 * EI), rel=1e-4)


def test_combination_is_qp_else_the_largest():
    from triton.validation import import_sheets

    wb = import_sheets({"Pile(1)-PT-B-Apron": pile_sheet(scale=3.0), "Pile(1)-QP": pile_sheet()})
    sheets = wb.elements()["Pile(1)"]
    assert pick_combination(sheets, ["M_2", "M_3"])[0] == "QP"
    assert pick_combination(sheets, ["M_2", "M_3"], "pt-b-apron") == (
        "PT-B-Apron",
        "as chosen on the Design tab",
    )
    uls = {"PT-B-Apron": sheets["PT-B-Apron"]}
    combo, why = pick_combination(uls, ["M_2", "M_3"])
    assert combo == "PT-B-Apron" and "high side" in why


# --- Through the API --------------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TRITON_DATA_DIR", str(tmp_path))
    from triton.api import app

    return TestClient(app)


def test_pile_estimate_through_the_api(client):
    p = client.post("/api/projects", json={"info": {"name": "D"}, "element_names": ["Pile(1)"]}).json()
    url = f"/api/projects/{p['id']}/sections/{p['sections'][0]['id']}"
    assert client.get(f"{url}/deflections").status_code == 409  # no workbook yet
    data = xlsx_bytes(
        {"Pile(1)-PT-B-Apron": pile_sheet(scale=3000.0), "Pile(1)-QP": pile_sheet(scale=1000.0)}
    )
    assert client.post(f"{url}/workbook", files={"file": ("s.xlsx", data)}).status_code == 200
    est = client.get(f"{url}/deflections").json()
    assert est["estimate"] is True and "not a Plaxis displacement result" in est["note"]
    (e,) = est["elements"]
    assert e["element"] == "Pile(1)" and e["combination"] == "QP" and e["kind"] == "pile"
    assert e["toe_level"] == -5 and e["head_level"] == -1
    assert "Fixed at the toe" in e["boundary"] and "Gross" in e["stiffness"]
    # Nodes 1..5 at z = −1..−5; M3 = 1000 (node + 5) = 1000 (10 − s), s from the toe. Fixed at the toe:
    # w(L) = ∫ (L − s) κ ds = 1000 (160 − 112 + 64/3) / EI, EI of a 1200 mm C40/50 circle.
    ei = 35_200 * math.pi * 1200**4 / 64 * 1e-9
    m3 = next(d for d in e["directions"] if d["key"] == "M_3")
    assert m3["head_mm"] == pytest.approx(1000 * (160 - 112 + 64 / 3) / ei * 1e3, abs=0.01)
    assert e["max_mm"] == max((d["max_mm"] for d in e["directions"]), key=abs)
    assert m3["stations"][0] == [-5.0, 0.0]


def test_settings_are_not_a_design_input(client):
    """Changed while the model is locked, the settings never make the design out of date; the report
    carries the estimate after the typed-in displacements."""
    p = client.post("/api/projects", json={"info": {"name": "D"}, "element_names": ["Pile(1)"]}).json()
    url = f"/api/projects/{p['id']}/sections/{p['sections'][0]['id']}"
    data = xlsx_bytes({"Pile(1)-PT-B-Apron": pile_sheet(), "Pile(1)-QP": pile_sheet(scale=1000.0)})
    client.post(f"{url}/workbook", files={"file": ("s.xlsx", data)})
    assert client.post(f"{url}/design").status_code == 200
    gross = client.get(f"{url}/deflections").json()["elements"][0]
    page = client.get(f"/api/projects/{p['id']}").json()
    assert page["locked"] is True
    page["sections"][0]["deflection"] = {"toe": "firm_soil", "firm_soil_level": -3.0, "stiffness": "cracked"}
    r = client.put(f"/api/projects/{p['id']}", json=page)
    assert r.status_code == 200, r.text
    assert not client.get(f"{url}/design").json().get("changed")
    held = client.get(f"{url}/deflections").json()["elements"][0]
    assert "firm soil level" in held["boundary"] and "Cracked where M > Mcr" in held["stiffness"]
    assert "the designed cage" in held["stiffness"]
    assert held["head_mm"] != gross["head_mm"]
    wb = load_workbook(io.BytesIO(client.get(f"{url}/design/report.xlsx").content))
    text = " ".join(str(c.value) for ws in wb for row in ws.iter_rows() for c in row if c.value is not None)
    assert "3.8 Estimated displacements from the straining actions" in text and "ESTIMATE" in text


# --- Fingerprint --------------------------------------------------------------------------------------

SAVED = {
    "id": "0123456789ab",
    "info": {"name": "Quay"},
    "sections": [
        {
            "id": "abcdef12",
            "name": "Section 01a",
            "x_min": -30.0,
            "elements": {
                "Pile(1)": {"kind": "pile", "diameter": 1200, "head_level": 1.9},
                "Combi Wall": {"kind": "combi_wall"},
                "SPW": {"kind": "sheet_pile_wall"},
                "Deck": {"kind": "slab", "thickness": 700},
                "Front Beam": {"kind": "front_beam", "depth": 2000},
            },
            "displacements": [{"what": "Front beam", "value": 40, "limit": 50}],
        }
    ],
}
WORKBOOK = {"version": 3, "uploaded_at": "2026-09-01T10:00:00"}


def test_fingerprint_of_projects_saved_before_is_unchanged():
    """A project saved before the estimate existed keeps its fingerprint (the hash is the one the code
    gave before), and changing the estimate's settings changes nothing either."""
    p = Project.model_validate(SAVED)
    before = fresh.fingerprint(p, p.sections[0], WORKBOOK)
    assert before["working zone and peaks"] == "9bb977fc731c"
    s = p.sections[0]
    s.deflection = DeflectionSettings(
        toe="firm_soil", firm_soil_level=-12, stiffness="cracked", long_term=True
    )
    s.deflection.combination = "PT-B-Apron"
    assert fresh.fingerprint(p, s, WORKBOOK) == before
