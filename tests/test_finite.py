"""No infinite or astronomical utilisation reaches the results: one such number made a whole
section's results fail to load (JSON has no infinity), or read as 'no result'."""

import json
import math

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from triton import api
from triton.design.bollard import at_step
from triton.design.sheet_piles import UF_CAP
from triton.design.tube import Tube, check_tube, plastic_utilisation
from triton.project import Bollard, DesignSettings
from triton.store import finite

TUBE = Tube(1626, 18, 3, "S355")
COLUMN = dict(
    top=0.0,
    toe=-30.0,
    filled_from=-20.0,
    infill_diameter=1590,
    fck=40,
    ecm=35000,
    factor=0.7,
    curve="c",
    firm=None,
    column_ei=None,
)


def _loads(v):
    rows = [("A", 1, -5.0, -1000.0, 100.0, 1000.0, True), ("B", 2, -25.0, -1000.0, v, 10.0, False)]
    return pd.DataFrame(rows, columns=["combination", "Node", "Z", "N", "V", "M", "filled"])


@pytest.mark.parametrize("share", [1.0, 1.01])
def test_tube_shear_at_vpl_gives_a_number_not_a_billion(share):
    vpl = TUBE.resistances()["V_pl_kN"]
    out = check_tube([(math.inf, -math.inf, TUBE)], _loads(share * vpl), method="office", column=COLUMN)
    assert out["utilisation"] is not None and 1 < out["utilisation"] <= UF_CAP
    assert not out["passed"]
    assert all(p["util"] is None or p["util"] <= UF_CAP for p in out["profile"])


def test_plastic_utilisation_is_finite_at_and_over_vpl():
    vpl = TUBE.resistances()["V_pl_kN"]
    u = plastic_utilisation(
        np.array([-1000.0, -1000.0]), np.array([10.0, 10.0]), np.array([vpl, 1.2 * vpl]), TUBE
    )
    assert np.all(np.isfinite(u)) and u[1] == pytest.approx(1.2)


@pytest.mark.parametrize("ties", [[], [{"count": 2, "angle": 90.0}]])
def test_bollard_with_no_working_tie_is_unsafe_not_a_crash(ties):
    u = at_step(Bollard(ties=ties, thickening=1200.0), 1000.0, "C40/50", 435.0, 3.0, DesignSettings())[
        "utilisation"
    ]
    assert u == math.inf  # nothing carries the bottom tie force: unsafe, never 1e16 from cos 90°


def test_finite_turns_every_infinity_into_none():
    r = {"a": [1.0, math.inf, {"b": -math.inf, "c": math.nan}], "d": 2}
    assert finite(r) == {"a": [1.0, None, {"b": None, "c": None}], "d": 2}


def test_results_with_an_infinity_still_load(tmp_path, monkeypatch):
    monkeypatch.setenv("TRITON_DATA_DIR", str(tmp_path))
    client = TestClient(api.app)
    p = client.post("/api/projects", json={"element_names": ["Pile(1)"]}).json()
    pid, sid = p["id"], p["sections"][0]["id"]
    # Saved by an older Triton: json.dumps writes the non-standard token Infinity.
    path = api.store()._dir(pid, sid) / "results.json"
    path.write_text(json.dumps({"piles": [{"element": "Pile(1)", "utilisation": math.inf}], "run_at": "x"}))
    assert api.store().load_results(pid, sid)["piles"][0]["utilisation"] is None
