import math

import pytest
from conftest import pile_sheet
from pydantic import ValidationError
from test_design import pile_sheets, xlsx_bytes

from triton.design.export import FORMAT, pile_cages
from triton.design.piles import design_pile
from triton.project import DesignSettings, PileInput, PileReinforcement

# Moment fading with depth, like a laterally loaded pile: 3000 kNm at the head.
LOADS = [(i + 1, -0.5 * i, -3000.0, 3000.0 * math.exp(-0.5 * i / 4), 0.0) for i in range(41)]


def design(**piles):
    settings = DesignSettings()
    for k, v in piles.items():
        setattr(settings.piles, k, v)
    return design_pile("Pile(1)", PileInput(head_level=0.0), settings, pile_sheets(LOADS)).to_dict()


def lap(phi):
    return math.ceil(45 * phi / 50 - 1e-9) * 0.05


def test_least_steel_zones():
    d = design()
    c = d["curtailment"]
    runs = c["runs"]
    assert len(runs) >= 2
    assert runs[0]["top"] == 0.0 and runs[-1]["bottom"] == -20.0
    assert all(a["bottom"] == pytest.approx(b["top"]) for a, b in zip(runs, runs[1:], strict=False))
    outer = {r["cage"]["rings"][0]["count"] for r in runs}
    assert outer == {d["arrangement"]["rings"][0]["count"]}
    areas = [r["cage"]["area_mm2"] for r in runs]
    assert areas == sorted(areas, reverse=True) and areas[-1] < areas[0]
    for r in runs:
        assert r["utilisation"] <= 1.0
        assert r["length_m"] >= 3.0 - 1e-9
        assert max(r["bar_lengths_m"]) <= 12.0 + 1e-9
    for r in runs[:-1]:
        assert r["joint"] == "lap"
        assert r["lap_below_m"][0] == pytest.approx(lap(r["cage"]["rings"][0]["diameter"]))
    assert runs[-1]["joint"] == "toe" and set(runs[-1]["lap_below_m"]) == {0.0}
    assert c["weight_kg"] < c["unified_weight_kg"]
    assert c["steel_ratio_kg_m3"] == pytest.approx(c["weight_kg"] / (math.pi * 0.36 * 20), abs=0.2)


def test_standard_cut_lengths():
    d = design(curtailment="standard_lengths")
    runs = d["curtailment"]["runs"]
    standard = [r for r in runs if any(abs(r["bar_lengths_m"][0] - s) < 0.01 for s in (6.0, 8.0, 9.0, 12.0))]
    assert len(standard) >= len(runs) - 1


def test_couplers_have_no_laps():
    d = design(splice="coupler")
    c = d["curtailment"]
    assert all(set(r["lap_below_m"]) == {0.0} for r in c["runs"])
    assert c["couplers"] == sum(r["cage"]["bar_count"] for r in c["runs"][:-1])


def test_no_curtailment_when_switched_off():
    assert design(curtail=False)["curtailment"] is None


def test_settings_follow_ec2_limits():
    with pytest.raises(ValidationError):
        PileReinforcement(max_clear_spacing=250)
    with pytest.raises(ValidationError, match="couplers"):
        PileReinforcement(max_steel_ratio=5)
    assert PileReinforcement(max_steel_ratio=5, splice="coupler").max_steel_ratio == 5


def test_cage_export():
    d = design()
    out = pile_cages("Berth", {"run_at": "now", "piles": [d]})
    assert out["format"] == FORMAT
    (pile,) = out["piles"]
    assert pile["positions"] == [{"x": 0.0, "y": 0.0}]
    assert pile["head_level_m"] == 0.0 and pile["toe_level_m"] == -20.0
    first = pile["runs"][0]["rows"][0]
    assert first["bar_top_m"] == 0.0
    assert first["bar_top_m"] - first["bar_bottom_m"] == pytest.approx(first["bar_length_m"])


def test_cage_export_endpoint(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from triton.api import app

    monkeypatch.setenv("TRITON_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    p = client.post("/api/projects", json={"info": {"name": "Berth 1"}, "element_names": ["Pile(1)"]}).json()
    url = f"/api/projects/{p['id']}/sections/{p['sections'][0]['id']}"
    assert client.get(f"{url}/design/cages.json").status_code == 404
    data = xlsx_bytes({"Pile(1)-PT-B-Apron": pile_sheet(), "Pile(1)-QP": pile_sheet()})
    client.post(f"{url}/workbook", files={"file": ("s.xlsx", data)})
    client.post(f"{url}/design")
    r = client.get(f"{url}/design/cages.json")
    assert r.status_code == 200
    assert "Berth_1_Section_1-cages.json" in r.headers["content-disposition"]
    out = r.json()
    assert out["section"] == "Section 1"
    assert out["piles"][0]["element"] == "Pile(1)" and out["piles"][0]["count"] == 1
    x = client.get(f"{url}/design/governing.xlsx")
    assert x.status_code == 200
    assert "Berth_1_Section_1-governing-sets.xlsx" in x.headers["content-disposition"]
