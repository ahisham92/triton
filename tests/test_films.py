"""Results films: the SAP2000 reader, the made-up jetty and the films' endpoints."""

from __future__ import annotations

import gzip
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from triton import sap, sap_demo
from triton.api import app

client = TestClient(app)
STATIC = Path(__file__).resolve().parents[1] / "src" / "triton" / "static"


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    d = tmp_path_factory.mktemp("sap")
    path = sap_demo.write(d / "jetty.s2k")
    work = d / "work"
    work.mkdir()
    return path, sap.read_all(path, work).summary()


def _tables(path: Path) -> dict[str, list[dict[str, str]]]:
    """The .s2k file's tables as records, to write them again as a SAP2000 Excel export."""
    out: dict[str, list[dict[str, str]]] = {}
    name = None
    for line in path.read_text("utf-8").splitlines():
        if line.startswith("TABLE:"):
            name = line.split('"')[1]
            out[name] = []
        elif name and line.strip() and not line.startswith("END"):
            out[name].append(sap._record(line))
    return out


def _book(path: Path, dest: Path) -> Path:
    """The tables as SAP2000 exports them to Excel: the title, the headings, the units, the rows."""
    wb = Workbook()
    wb.remove(wb.active)
    for i, (name, rows) in enumerate(_tables(path).items()):
        ws = wb.create_sheet(f"{name[:26]} {i}" if len(name) > 26 else name)
        heads = list(dict.fromkeys(k for r in rows for k in r))
        ws.append([f"TABLE:  {name}"])
        ws.append(heads)
        ws.append(["Text" for _ in heads])
        for r in rows:
            row = []
            for h in heads:
                v = r.get(h, "")
                try:
                    row.append(float(v))
                except ValueError:
                    row.append(v)
            ws.append(row)
    wb.save(dest)
    return dest


def test_demo_jetty_reads_whole(demo):
    _, s = demo
    c = sap.counts(s)
    assert c["joints"] > 800 and c["frames"] > 700 and c["areas"] == 160
    assert c["supports"] == 55 and c["combos"] == 5
    assert 0.5 < c["max_ratio"] < 1.0  # a sample that shows the colours, not one that fails
    assert {"modes", "stress"} <= set(s)


def test_demo_reactions_balance_the_vertical_load(demo):
    # Under DEAD + SDL + LIVE the piles carry what was put on the deck: a check that reactions are
    # read in their own units and signs (F3 > 0 = support in compression).
    _, s = demo
    for case in s["reactions"]["cases"]:
        assert case["sum_fz"] >= 0
    assert s["reactions"]["max"]["down"]["value"] > 1000


def test_seabed_guess(demo):
    from triton.films import _guess_seabed

    _, s = demo
    lo, hi = s["bbox"][0][2], s["bbox"][1][2]
    assert lo < _guess_seabed(s) < hi


def test_workbook_reads_like_text(demo, tmp_path):
    path, s = demo
    book = _book(path, tmp_path / "jetty.xlsx")
    work = tmp_path / "work"
    work.mkdir()
    b = sap.read_all(book, work).summary()
    assert sap.counts(b) == sap.counts(s)
    assert b["reactions"]["max"]["down"]["value"] == pytest.approx(
        s["reactions"]["max"]["down"]["value"], rel=1e-3
    )


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("TRITON_DATA_DIR", str(tmp_path))
    r = client.post(
        "/api/projects",
        json={
            "info": {
                "name": "Film",
                "client": "Port Co",
                "sites": [{"name": "Berth", "lat": 25.0, "lon": 55.0}],
            }
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_sample_film(project):
    r = client.post(f"/api/projects/{project}/films/sample")
    assert r.status_code == 201, r.text
    f = r.json()
    assert f["sample"] and f["settings"]["client"] == "Port Co"
    assert (
        f["settings"]["lat"] == 25.0 and f["settings"]["breakwaters"][0]["pts"]
    )  # placed on the map at the site
    assert f["counts"]["frames"] > 700
    s = client.get(f"/api/projects/{project}/films/{f['id']}/summary").json()
    assert len(s["frames"]["ids"]) == f["counts"]["frames"]
    assert [x["id"] for x in client.get(f"/api/projects/{project}/films").json()] == [f["id"]]


def test_add_save_delete_film(project, demo):
    _, s = demo
    body = gzip.compress(json.dumps(s).encode())
    r = client.post(
        f"/api/projects/{project}/films?file=Berth%201.xlsx",
        content=body,
        headers={"Content-Type": "application/gzip"},
    )
    assert r.status_code == 201, r.text
    f = r.json()
    assert f["settings"]["name"] == "Berth 1" and f["settings"]["seabed"] is not None
    assert not f["sample"] and f["counts"] == sap.counts(s)
    set_ = {
        **f["settings"],
        "title": "Berth 1 results",
        "mode": "brief",
        "breakwaters": [{"name": "North", "pts": [[25.001, 55.0], [25.002, 55.001]]}],
    }
    r = client.put(f"/api/projects/{project}/films/{f['id']}", json=set_)
    assert r.status_code == 200 and r.json()["settings"]["breakwaters"][0]["crest"] == 5.0
    assert client.get(f"/api/projects/{project}/films").json()[0]["settings"]["mode"] == "brief"
    assert client.delete(f"/api/projects/{project}/films/{f['id']}").status_code == 204
    assert client.get(f"/api/projects/{project}/films").json() == []


def test_bad_uploads_and_ids(project):
    r = client.post(f"/api/projects/{project}/films", content=b"not json")
    assert r.status_code == 400
    r = client.post(f"/api/projects/{project}/films", content=json.dumps({"joints": []}).encode())
    assert r.status_code == 400
    assert client.get(f"/api/projects/{project}/films/..%2F..%2Fx/summary").status_code == 404
    assert client.get(f"/api/projects/{project}/films/abc123/summary").status_code == 404
    assert client.get("/api/projects/nope/films").status_code == 404


def test_films_do_not_touch_the_project(project):
    before = client.get(f"/api/projects/{project}").json()
    client.post(f"/api/projects/{project}/films/sample")
    assert client.get(f"/api/projects/{project}").json() == before


NODE = shutil.which("node")


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_page_reader_matches_python(demo, tmp_path):
    """static/sapread.js, the reader the page runs, gives the same summary from text and Excel."""
    path, s = demo
    book = _book(path, tmp_path / "jetty.xlsx")
    script = tmp_path / "read.mjs"
    script.write_text(
        "import { readFileSync } from 'node:fs';\n"
        f"const {{ readSap }} = await import({json.dumps((STATIC / 'sapread.js').as_uri())});\n"
        "const out = {};\n"
        "for (const p of process.argv.slice(2))\n"
        "  out[p] = await readSap(new File([readFileSync(p)], p), () => {});\n"
        "console.log(JSON.stringify(out));\n",
        "utf-8",
    )
    run = subprocess.run(
        [NODE, str(script), str(path), str(book)], capture_output=True, text=True, timeout=120
    )
    assert run.returncode == 0, run.stderr
    got = json.loads(run.stdout)
    for js in got.values():
        assert sap.counts(js) == sap.counts(s)
        assert js["reactions"]["max"]["down"]["value"] == pytest.approx(
            s["reactions"]["max"]["down"]["value"], rel=1e-3
        )
        assert js["displacements"]["max"]["h"]["value"] == pytest.approx(
            s["displacements"]["max"]["h"]["value"], rel=1e-3
        )
        assert js["frames"]["ends"] == s["frames"]["ends"]


def test_demo_has_modes_quakes_and_tension(demo):
    _, s = demo
    assert [m["mode"] for m in s["modes"]] == [1, 2, 3, 4, 5, 6]
    assert s["modal"][-1]["sumuy"] == pytest.approx(0.99, abs=0.01)
    assert {c["name"] for c in s["cases"] if sap._is_quake(c["name"], c["type"])} == {"EQX", "EQY"}
    assert any(c["case"] == "EQY" for c in s["displacements"]["cases"])
    assert max(s["forces"]["tension"]) > 0 and max(s["forces"]["compression"]) > 0
    assert not any("mode" in c["name"] for c in s["cases"])


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_small_file_round_trip(demo, tmp_path):
    """The page's small file (.trsap: the summary with every case's results, gzipped) reads back as it was."""
    path, _ = demo
    script = tmp_path / "pack.mjs"
    script.write_text(
        "import { readFileSync } from 'node:fs';\n"
        "import { gzipSync } from 'node:zlib';\n"
        f"const m = await import({json.dumps((STATIC / 'sapread.js').as_uri())});\n"
        "const p = process.argv[2];\n"
        "const S = await m.readSap(new File([readFileSync(p)], 'jetty.s2k'), () => {});\n"
        "const small = new File([gzipSync(JSON.stringify(S))], 'jetty.trsap');\n"
        "const back = await m.readSap(small, () => {});\n"
        "const c = back.full.cases[0], u = m.fromB64(back.full.disp[c]);\n"
        "console.log(JSON.stringify({ same: JSON.stringify(back) === JSON.stringify(S),\n"
        "  cases: back.full.cases.length,\n"
        "  n: u.length, joints: S.joints.length, frames: Object.keys(back.full.frames).length }));\n",
        "utf-8",
    )
    run = subprocess.run([NODE, str(script), str(path)], capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr
    got = json.loads(run.stdout)
    assert got["same"] and got["cases"] >= 7 and got["n"] == 3 * got["joints"] and got["frames"] >= 5
