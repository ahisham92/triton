"""Reinforcement drawings for AutoCAD (DXF) and Revit (drawings file + script)."""

from __future__ import annotations

import ast
import json
import math
from pathlib import Path

import pytest
from conftest import pile_sheet
from test_design import xlsx_bytes

from triton import revit
from triton.drawings import FORMAT, _grid, from_cages
from triton.dxf import placements, to_dxf
from triton.project import BarLayer, DrawingSettings, Project

SAMPLE = json.loads((Path(__file__).parent / "data" / "sample_cages.json").read_text("utf-8"))


def sample(settings: DrawingSettings | None = None, element: str | None = None) -> dict:
    return from_cages(SAMPLE, settings or DrawingSettings(), element)


def view(data: dict, name: str) -> dict:
    return next(v for v in data["views"] if v["name"] == name)


def test_views_of_every_element():
    d = sample()
    assert d["format"] == FORMAT and d["units"] == "mm"
    names = [v["name"] for v in d["views"]]
    runs = len(SAMPLE["piles"][0]["runs"])
    assert names[:runs] == [f"Pile(1) - cage {i} section" for i in range(1, runs + 1)]
    assert "Pile(1) - elevation" in names and "Front Beam - section" in names
    for face in ("bottom", "top"):
        for along in ("X", "Y"):
            assert f"Deck - {face} bars along {along}" in names
    assert {"Deck - mesh cut across X", "Deck - mesh cut across Y", "Deck - shear links"} <= set(names)
    assert [v["name"] for v in sample(element="Front Beam")["views"]] == ["Front Beam - section"]
    both = {v["element"] for v in sample(element=["Front Beam", "Deck"])["views"]}
    assert both == {"Front Beam", "Deck"}


def test_pile_section_has_every_bar_on_its_circle():
    p = SAMPLE["piles"][0]
    run = p["runs"][0]
    v = view(sample(), "Pile(1) - cage 1 section")
    bars = [i for i in v["items"] if i["type"] == "bar"]
    assert len(bars) == sum(r["count"] for r in run["rows"])
    radii = sorted({round(math.hypot(*b["c"])) for b in bars})
    assert radii == sorted(round(r["radius_mm"]) for r in run["rows"])
    assert {b["layer"] for b in bars} == {f"bar-{r['diameter_mm']}" for r in run["rows"]}
    circles = [i for i in v["items"] if i["type"] == "circle"]
    assert circles[0] == {"type": "circle", "layer": "concrete", "c": [0, 0], "r": p["diameter_mm"] / 2}
    link = p["diameter_mm"] / 2 - p["cover_mm"] - p["link_diameter_mm"] / 2
    assert (
        circles[1]["r"] == pytest.approx(link) and circles[1]["layer"] == f"bar-{int(p['link_diameter_mm'])}"
    )


def test_pile_elevation_bars_at_true_levels():
    p = SAMPLE["piles"][0]
    v = view(sample(), "Pile(1) - elevation")
    lines = [i for i in v["items"] if i["type"] == "line" and i["layer"].startswith("bar-")]
    assert len(lines) == 2 * sum(len(r["rows"]) for r in p["runs"])
    top = p["runs"][0]["rows"][0]
    first = lines[0]
    assert first["b"][1] == pytest.approx(top["bar_top_m"] * 1000) and first["a"][1] == pytest.approx(
        top["bar_bottom_m"] * 1000
    )


def test_beam_section_bars():
    b = SAMPLE["beams"][0]
    v = view(sample(), "Front Beam - section")
    bars = [i for i in v["items"] if i["type"] == "bar"]
    assert [x["c"] for x in bars] == [[x["y_mm"], x["z_mm"]] for x in b["bars"]]
    rects = [i for i in v["items"] if i["type"] == "rect"]
    assert rects[0]["a"] == [-b["width_mm"] / 2, -b["depth_mm"] / 2] and rects[1]["layer"] == "bar-16"


def test_grid_positions():
    assert _grid(0, 600, 150, 75) == [75, 225, 375, 525]
    assert _grid(-100, 100, 100, 50) == [-50, 50]


def test_slab_plan_mesh_and_zone_bars():
    d = SAMPLE["slabs"][0]
    face = d["faces"][0]  # bottom, bars along X
    v = view(sample(), "Deck - bottom bars along X")
    y0, y1 = (y * 1000 for y in d["box_m"]["Y"])
    x0, x1 = (x * 1000 for x in d["box_m"]["X"])
    mesh = [i for i in v["items"] if i["type"] == "line" and i["a"][0] == x0 and i["b"][0] == x1]
    s = face["mesh"]["spacing_mm"]
    assert len(mesh) == len(_grid(y0, y1, s, y0 + s / 2))
    assert all(m["a"][1] == m["b"][1] for m in mesh)  # along X
    z = face["zones"][0]
    zx = [x * 1000 for x in z["x_m"]]
    added = [i for i in v["items"] if i["type"] == "line" and [i["a"][0], i["b"][0]] == zx]
    (extra,) = [b for b in z["layers"][0]["bars"] if b["kind"] != "mesh"]
    assert added and {a["layer"] for a in added} == {f"bar-{extra['diameter_mm']}"}
    # Added bars sit between the mesh bars.
    mesh_y = {m["a"][1] for m in mesh}
    assert not mesh_y & {a["a"][1] for a in added}


def test_slab_cut_dots_for_bars_through_it():
    d = SAMPLE["slabs"][0]
    v = view(sample(), "Deck - mesh cut across X")
    dots = [i for i in v["items"] if i["type"] == "bar"]
    x_faces = [f for f in d["faces"] if f["bars_along"] == "X"]
    assert {b["c"][1] for b in dots} == {f["mesh"]["layers"][0]["above_soffit_mm"] for f in x_faces}
    assert all(0 <= b["c"][0] <= 1000 for b in dots)


def test_layer_names_from_the_project_settings():
    settings = DrawingSettings(
        bars=[
            BarLayer(
                diameter=32,
                cad_layer="S-REBAR-T32",
                revit_line_style="Rebar T32",
                revit_section_type="Rebar Dot: T32",
            )
        ],
        concrete_cad_layer="S-CONC",
    )
    layers = sample(settings)["layers"]
    assert (
        layers["bar-32"]["cad_layer"] == "S-REBAR-T32"
        and layers["bar-32"]["revit_section_type"] == "Rebar Dot: T32"
    )
    assert layers["bar-25"]["cad_layer"] == "REBAR-25"  # not listed: the placeholder
    assert layers["concrete"]["cad_layer"] == "S-CONC"
    # Placeholders until the office names come: one per bar size.
    default = DrawingSettings()
    assert [b.cad_layer for b in default.bars][:3] == ["REBAR-8", "REBAR-10", "REBAR-12"]


def test_dxf_layers_and_entities():
    ezdxf = pytest.importorskip("ezdxf")
    from ezdxf import recover

    data = sample()
    text = to_dxf(data)
    assert text.startswith("  0\r\nSECTION")
    path = Path(__import__("tempfile").mkdtemp()) / "t.dxf"
    path.write_text(text, "ascii")
    doc, auditor = recover.readfile(path)
    assert not auditor.errors
    names = {layer.dxf.name for layer in doc.layers}
    assert {"REBAR-32", "REBAR-16", "TRITON-CONCRETE", "TRITON-TEXT"} <= names
    msp = doc.modelspace()
    n_bars = sum(1 for v in data["views"] for i in v["items"] if i["type"] == "bar")
    assert len(msp.query("POLYLINE")) >= n_bars
    assert any("%%c32" in t.dxf.text for t in msp.query("TEXT"))
    assert ezdxf.__version__


def test_dxf_views_do_not_overlap():
    data = sample()
    boxes = []
    for v, (dx, dy) in zip(data["views"], placements(data["views"]), strict=True):
        x0, y0, x1, y1 = v["box"]
        boxes.append((x0 + dx, y0 + dy, x1 + dx, y1 + dy))
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]


def test_revit_script_and_dynamo_graph():
    code = revit.script()
    ast.parse(code)
    assert "triton.drawings/1" in code and "NewDetailCurve" in code
    g = json.loads(revit.dynamo_graph())
    py = next(n for n in g["Nodes"] if n["NodeType"] == "PythonScriptNode")
    assert py["Code"] == code and py["Engine"] == "CPython3"
    ports = {p["Id"] for n in g["Nodes"] for p in n["Inputs"] + n["Outputs"]}
    assert all(c["Start"] in ports and c["End"] in ports for c in g["Connectors"])
    assert json.loads(revit.dynamo_graph("IronPython2"))["Nodes"][1]["Engine"] == "IronPython2"
    with pytest.raises(ValueError):
        revit.dynamo_graph("Lua")


def test_drawing_names_stay_open_when_locked():
    from triton.api import _model

    p = Project()
    before = _model(p)
    p.drawings.bars[0].cad_layer = "S-REBAR-8"
    assert _model(p) == before


def test_drawing_endpoints(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from triton.api import app

    monkeypatch.setenv("TRITON_DATA_DIR", str(tmp_path))
    client = TestClient(app)
    p = client.post("/api/projects", json={"info": {"name": "Berth 1"}, "element_names": ["Pile(1)"]}).json()
    url = f"/api/projects/{p['id']}/sections/{p['sections'][0]['id']}"
    assert client.get(f"{url}/design/drawings.dxf").status_code == 404
    data = xlsx_bytes({"Pile(1)-PT-B-Apron": pile_sheet(), "Pile(1)-QP": pile_sheet()})
    client.post(f"{url}/workbook", files={"file": ("s.xlsx", data)})
    client.post(f"{url}/design")
    r = client.get(f"{url}/design/drawings.json")
    assert r.status_code == 200 and "Berth_1_Section_1-drawings.json" in r.headers["content-disposition"]
    d = r.json()
    assert d["section"] == "Section 1" and d["views"][-1]["name"] == "Pile(1) - elevation"
    r = client.get(f"{url}/design/drawings.dxf", params={"element": "Pile(1)"})
    assert r.status_code == 200 and "Berth_1_Section_1_Pile_1_.dxf" in r.headers["content-disposition"]
    assert "REBAR-" in r.text
    assert client.get(f"{url}/design/drawings.dxf", params={"element": "Deck"}).status_code == 404
    g = client.get("/api/revit/triton-drawings.dyn")
    assert g.status_code == 200 and json.loads(g.text)["Name"] == "Triton drawings"
    assert client.get("/api/revit/triton-drawings.dyn", params={"engine": "x"}).status_code == 400
    assert "NewDetailCurve" in client.get("/api/revit/triton_revit.py").text
    z = client.get("/api/revit/triton-addin.zip")
    assert z.status_code == 200 and z.content[:2] == b"PK"


def test_revit_addin_source_zip():
    import io
    import zipfile

    names = zipfile.ZipFile(io.BytesIO(revit.addin_zip())).namelist()
    for f in ("TritonDrawings.csproj", "TritonDrawings.addin", "DrawCommand.cs", "Drawer.cs", "README.txt"):
        assert f"TritonDrawings/{f}" in names
    cs = (revit.ADDIN / "TritonFile.cs").read_text("utf-8")
    assert f'Format = "{FORMAT}"' in cs  # the add-in reads the format Triton writes
