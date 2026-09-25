"""Reinforcement drawings for AutoCAD (DXF) and Revit (drawings file + script)."""

from __future__ import annotations

import ast
import json
import math
import re
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


def flat(items: list[dict]) -> list[dict]:
    """The items as AutoCAD draws them: families and dimensions by their fallback items."""
    out = []
    for i in items:
        out += flat(i["fallback"]) if "fallback" in i else [i]
    return out


def test_views_of_every_element():
    d = sample()
    assert d["format"] == FORMAT and d["units"] == "mm"
    names = [v["name"] for v in d["views"]]
    assert names[0] == "Pile(1) - pile sheet" and "Front Beam - section" in names
    assert {
        "Deck - bottom plan M11",
        "Deck - bottom plan M22",
        "Deck - top plan M11",
        "Deck - top plan M22",
        "Deck - mesh cut across X",
        "Deck - mesh cut across Y",
        "Deck - section at Pile(1)",
        "Deck - shear links",
    } <= set(names)
    assert [v["name"] for v in sample(element="Front Beam")["views"]] == ["Front Beam - section"]
    both = {v["element"] for v in sample(element=["Front Beam", "Deck"])["views"]}
    assert both == {"Front Beam", "Deck"}


def test_pile_sheet_sections_have_every_bar_on_its_circle():
    p = SAMPLE["piles"][0]
    v = view(sample(), "Pile(1) - pile sheet")
    fams = [i for i in v["items"] if i["type"] == "family"]
    assert len(fams) == len(p["runs"])
    assert all(f["family"] == "DET_Round_Col_RFT_Dar: Round Col-RFT" for f in fams)
    # The sections sit to the right of the elevation, each lower than the one before.
    assert all(f["at"][0] > p["diameter_mm"] for f in fams)
    assert [f["at"][1] for f in fams] == sorted((f["at"][1] for f in fams), reverse=True)
    for fam, run in zip(fams, p["runs"], strict=True):
        params = {x["names"].split("|")[0]: x for x in fam["params"]}
        assert params["DAR_WIDTH"]["mm"] == p["diameter_mm"]
        r0 = run["rows"][0]
        assert params["DAR_NO OF BARS"]["n"] == r0["count"]
        assert params["DAR_MAIN RFT"]["text"] == "+".join(
            f"{r['count']}ø{r['diameter_mm']}" for r in run["rows"][:4]
        )
        bars = [i for i in fam["fallback"] if i["type"] == "bar"]
        assert len(bars) == sum(r["count"] for r in run["rows"][:4])
        cx, cy = fam["at"]
        radii = sorted({round(math.hypot(b["c"][0] - cx, b["c"][1] - cy)) for b in bars})
        assert radii == sorted({round(r["radius_mm"]) for r in run["rows"][:4]})
    first = {x["names"].split("|")[0]: x for x in fams[0]["params"]}
    assert first["DAR_STIRRUPS RFT"]["text"] == "SPIRAL ø10 PITCH 100"  # the tightest zone over the cage
    texts = [i["text"] for i in v["items"] if i["type"] == "text"]
    assert "PILE(1) - SECTION A" in texts and "PILE(1) - ELEVATION" in texts


def test_pile_sheet_elevation_bars_at_true_levels():
    p = SAMPLE["piles"][0]
    v = view(sample(), "Pile(1) - pile sheet")
    top = p["runs"][0]["rows"][0]
    k = f"bar-{top['diameter_mm']}"
    lines = [i for i in v["items"] if i["type"] == "line" and i["layer"] == k]
    assert any(
        i["a"][1] == pytest.approx(top["bar_bottom_m"] * 1000)
        and i["b"][1] == pytest.approx(top["bar_top_m"] * 1000)
        for i in lines
    )
    # Each run of bars marked as the office does: "55ø32" and "L= 9650" reading upward.
    rot = {i["text"] for i in v["items"] if i["type"] == "text" and i.get("rot") == 90}
    n = sum(r["count"] for r in p["runs"][0]["rows"])
    assert f"{n}ø32" in rot and f"L= {top['bar_length_m'] * 1000:.0f}" in rot


def test_beam_section_bars():
    b = SAMPLE["beams"][0]
    v = view(sample(), "Front Beam - section")
    bars = [i for i in v["items"] if i["type"] == "bar"]
    assert [x["c"] for x in bars] == [[x["y_mm"], x["z_mm"]] for x in b["bars"]]
    rects = [i for i in flat(v["items"]) if i["type"] == "rect"]
    assert rects[0]["a"] == [-b["width_mm"] / 2, -b["depth_mm"] / 2] and rects[1]["layer"] == "bar-16"
    link = next(i for i in v["items"] if i["type"] == "family")
    assert link["family"] == "DET_Rebar_51_Dar 1: Rebar_51" and link["align"] == "center"
    ab = {x["names"]: x["mm"] for x in link["params"]}
    assert (
        ab["DAR_A"] == b["width_mm"] - 2 * b["cover_mm"] and ab["DAR_B"] == b["depth_mm"] - 2 * b["cover_mm"]
    )
    dims = sorted(i["text"] for i in v["items"] if i["type"] == "dim")
    assert dims == sorted([f"{b['width_mm']:.0f}", f"{b['depth_mm']:.0f}"])
    # Every drawing: a frame, its caption under it and the base point to copy it from.
    assert v["base"] == [0, b["depth_mm"] / 2]
    texts = [i["text"] for i in v["items"] if i["type"] == "text"]
    assert "FRONT BEAM - SECTION" in texts and "SCALE: 1 : 20" in texts
    assert any(t.startswith("BP = base point") for t in texts)
    assert {"19ø25", "48ø25", "22ø32 (SIDES)", "ø16 @ 175 (5 LEGS)"} <= set(texts)
    frame = [i for i in v["items"] if i["type"] == "rect" and i["layer"] == "zones"][-1]
    caption = next(i for i in v["items"] if i["type"] == "text" and i["text"] == "FRONT BEAM - SECTION")
    assert caption["at"][1] < frame["a"][1]


def test_grid_positions():
    assert _grid(0, 600, 150, 75) == [75, 225, 375, 525]
    assert _grid(-100, 100, 100, 50) == [-50, 50]


def test_slab_plans_bottom_and_top_with_the_additional_bar_family():
    d = SAMPLE["slabs"][0]
    face = d["faces"][0]  # bottom, bars along X
    v = view(sample(), "Deck - bottom plan M11")
    fams = [i for i in v["items"] if i["type"] == "family"]
    assert {f["family"] for f in fams} <= {
        "RFT_ADD_MODIFIED: RFT_ADD_TOP HL",
        "RFT_ADD_MODIFIED: RFT_ADD_TOP VL",
    }
    z = face["zones"][0]
    (extra,) = [b for b in z["layers"][0]["bars"] if b["kind"] != "mesh"]
    zx = [x * 1000 for x in z["x_m"]]
    zy = [y * 1000 for y in z["y_m"]]
    f = next(i for i in fams if i["at"] == [sum(zx) / 2, sum(zy) / 2] and "HL" in i["family"])
    p = {x["names"].split("|")[0]: x for x in f["params"]}
    phi = extra["diameter_mm"]
    assert p["Diameter"]["mm"] == phi and p["Spacing"]["mm"] == extra["spacing_mm"]
    assert p["L"]["mm"] == math.ceil((zx[1] - zx[0] + 90 * phi) / 100) * 100
    assert p["Distribution"]["mm"] == pytest.approx(zy[1] - zy[0])
    assert p["Top No."]["n"] == 1 and p["TOP REINF."]["n"] == 0
    # Drawn with lines: one bar of its length through the zone and its label, as the family shows it.
    (bar,) = [i for i in f["fallback"] if i["type"] == "line" and i["layer"] == f"bar-{phi}"]
    assert bar["b"][0] - bar["a"][0] == pytest.approx(p["L"]["mm"])
    words = [i["text"] for i in f["fallback"] if i["type"] == "text"]
    assert words == [f"ø{phi} @{extra['spacing_mm']:.0f}", f"L={p['L']['mm']:.0f} (ADD.)"]
    # The mesh is drawn by hand: only in the caption, no mesh lines across the slab.
    x0, x1 = (x * 1000 for x in d["box_m"]["X"])
    assert not [
        i
        for i in flat(v["items"])
        if i["type"] == "line" and i["layer"].startswith("bar-") and i["a"][0] == x0 and i["b"][0] == x1
    ]
    texts = [i["text"] for i in v["items"] if i["type"] == "text"]
    assert any(t.startswith("Mesh along X") for t in texts)
    assert not any(t.startswith("Mesh along Y") for t in texts)  # M22 bars: their own plan
    assert all("VL" not in f["family"] for f in fams)


def test_slab_section_at_a_pile_cranks_the_bottom_bars():
    d = SAMPLE["slabs"][0]
    v = view(sample(), "Deck - section at Pile(1)")
    bottom = [f for f in d["faces"] if f["face"] == "bottom"]
    lowest = min(
        lay["above_soffit_mm"] - lay["bars"][0]["diameter_mm"] / 2
        for f in bottom
        for lay in f["mesh"]["layers"]
    )
    lift = 100 + 25 - lowest
    xb = next(f for f in bottom if f["bars_along"] == "X")["mesh"]["layers"][0]["above_soffit_mm"]
    k = f"bar-{bottom[0]['mesh']['diameter_mm']}"
    ys = {round(i["a"][1], 1) for i in v["items"] if i["type"] == "line" and i["layer"] == k}
    assert lift > 0 and round(xb + lift, 1) in ys and xb in ys
    none = sample(DrawingSettings(pile_into_slab=0))
    ys = {
        i["a"][1]
        for i in view(none, "Deck - section at Pile(1)")["items"]
        if i["type"] == "line" and i["layer"] == k
    }
    assert xb + lift not in ys


def test_every_view_has_its_place_in_one_drawing():
    views = sample()["views"]
    rows = {}
    for v in views:
        rows.setdefault(v["element"], set()).add(v["at"][1] + v["box"][3])  # the top of its row
    assert all(len(tops) >= 1 for tops in rows.values())
    tops = [min(t) for t in rows.values()]
    assert len(set(tops)) == len(tops)  # a row (or more) per element


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
    assert layers["bar-25"]["cad_layer"] == "T25-Reinforcement Section"  # not listed: the office name
    assert layers["bar-25"]["revit_section_type"] == "DET_Rebar_Dot Bar_Dar: Section Bar"
    assert layers["concrete"]["cad_layer"] == "S-CONC"
    # The office line styles for every size, and the first placeholders move over to them.
    default = DrawingSettings()
    assert [b.revit_line_style for b in default.bars][:2] == [
        "T8-Reinforcement Section",
        "T10-Reinforcement Section",
    ]
    old = DrawingSettings.model_validate(
        {"bars": [{"diameter": 32, "cad_layer": "REBAR-32", "revit_line_style": "REBAR-32"}]}
    )
    assert old.bars[0].revit_line_style == "T32-Reinforcement Section"


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
    assert {
        "T32-REINFORCEMENT_SECTION",
        "T16-REINFORCEMENT_SECTION",
        "TRITON-CONCRETE",
        "TRITON-TEXT",
    } <= names
    # R12 names: no spaces (AutoCAD refuses the whole file), upper case, at most 31 characters.
    assert all(re.fullmatch(r"[A-Z0-9$_-]{1,31}", n) for n in names - {"Defpoints"})  # ezdxf adds Defpoints
    msp = doc.modelspace()
    n_bars = sum(1 for v in data["views"] for i in flat(v["items"]) if i["type"] == "bar")
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
    r = client.get(f"{url}/design/drawings.crm")
    assert r.status_code == 200 and "Berth_1_Section_1-drawings.crm" in r.headers["content-disposition"]
    assert client.get(f"{url}/design/drawings.json").json() == r.json()
    d = r.json()
    assert d["section"] == "Section 1" and d["views"][-1]["name"] == "Pile(1) - pile sheet"
    r = client.get(f"{url}/design/drawings.dxf", params={"element": "Pile(1)"})
    assert r.status_code == 200 and "Berth_1_Section_1_Pile_1_.dxf" in r.headers["content-disposition"]
    assert "-REINFORCEMENT_SECTION" in r.text
    assert client.get(f"{url}/design/drawings.dxf", params={"element": "Deck"}).status_code == 404
    g = client.get("/api/revit/triton-drawings.dyn")
    assert g.status_code == 200 and json.loads(g.text)["Name"] == "Triton drawings"
    assert client.get("/api/revit/triton-drawings.dyn", params={"engine": "x"}).status_code == 400
    assert "NewDetailCurve" in client.get("/api/revit/triton_revit.py").text
    t = client.get("/api/revit/triton-draw-bars.txt")
    assert (
        t.status_code == 200
        and "triton.drawings/1" in t.text
        and "TritonDrawBars.txt" in t.headers["content-disposition"]
    )
    z = client.get("/api/revit/triton-addin.zip")
    assert z.status_code == 200 and z.content[:2] == b"PK"


def test_revit_addin_source_zip():
    import io
    import zipfile

    names = zipfile.ZipFile(io.BytesIO(revit.addin_zip())).namelist()
    for f in ("TritonDrawings.csproj", "TritonDrawings.addin", "DrawCommand.cs", "README.txt"):
        assert f"TritonDrawings/{f}" in names
    assert "TritonDrawings/DevKitCode.cs" in names
    cs = revit.addin_devkit_source()
    assert f'"{FORMAT}"' in cs and "public static void Run(Autodesk.Revit.DB.Document doc)" in cs


def test_devkit_code_is_statements_only():
    code = revit.devkit_code()
    lines = [x.strip() for x in code.splitlines()]
    assert not any(x.startswith(("using ", "namespace ")) for x in lines)
    assert f'"{FORMAT}"' in code and "*.crm" in code
    assert "Autodesk.Revit.DB.Document theDoc = doc;" in code


def test_pile_sheet_draws_top_bars_from_the_connection():
    data = json.loads(json.dumps(SAMPLE))
    p = data["piles"][0]
    rows = [
        {
            **r,
            "anchor_top_m": r["bar_top_m"],
            "bar_top_m": 2.952,
            "up_m": 0.25,
            "leg_m": 1.2,
            "short_m": 0.0,
            "bar_length_m": 9.4,
        }
        for r in p["runs"][0]["rows"]
    ]
    conn = {
        "host": "Deck",
        "host_kind": "slab",
        "host_top_m": 3.05,
        "host_soffit_m": 2.35,
        "shape": "L",
        "rows": rows,
    }
    for q in p["positions"]:
        q["connection"] = conn
    p["connections"] = [
        {
            "host": "Deck",
            "host_kind": "slab",
            "shape": "L",
            "count": len(p["positions"]),
            "positions": list(range(len(p["positions"]))),
            "rows": rows,
        }
    ]
    v = view(from_cages(data, DrawingSettings()), "Pile(1) - pile sheet")
    k = f"bar-{rows[0]['diameter_mm']}"
    tops = [
        i for i in v["items"] if i["type"] == "line" and i["layer"] == k and i["b"][1] == pytest.approx(2952)
    ]
    assert any(i["a"][1] == i["b"][1] and abs(i["b"][0]) > abs(i["a"][0]) for i in tops)  # the L legs
    texts = {i["text"] for i in v["items"] if i["type"] == "text"}
    assert "L= 9400 (L-BAR, LEG 1200)" in texts and "DECK (SEE ITS PLAN)" in texts
