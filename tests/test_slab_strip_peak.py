"""Deck strips take the peak moment across their width (as the calc report), or the average on request."""

import numpy as np
import pandas as pd

from triton.design.slabs import strip_average
from triton.project import SlabInput


def _strip(across):
    m = np.array([100.0, 300.0, -50.0, 200.0])
    n = np.array([10.0, 20.0, 30.0, 40.0])
    f = pd.DataFrame({"combination": ["C1"] * 4})
    loc = {
        "st": np.zeros(4, int),
        "kind": np.zeros(4, int),
        "inst": np.zeros(4, int),
        "s": np.array([0.1, 0.2, 0.3, 0.4]),
        "averaged": np.ones(4, bool),
    }
    return strip_average(f, m, n, loc, 1.0, None, across)


def test_peak_is_the_default():
    assert SlabInput().strip_moments == "peak"


def test_peak_keeps_the_largest_node_of_each_sign_with_its_own_n():
    env = _strip("peak")
    assert sorted(zip(env["m"], env["n"], strict=True)) == [(-50.0, 30.0), (300.0, 20.0)]


def test_average_is_the_mean_across_the_width():
    env = _strip("average")
    assert len(env) == 1 and env["m"].iloc[0] == 137.5 and env["n"].iloc[0] == 25.0
